"""The precomputed-statistics lifecycle: built once per data refresh, sliced per request."""

import json

import numpy as np
import pandas as pd
import pytest

from conftest import RECENT_DAY, dated_to_today, synthetic_market
from wolvwealth.domain import optimizer
from wolvwealth.domain.market import MarketData
from wolvwealth.domain import stats as stats_module
from wolvwealth.domain.stats import MarketStats
from wolvwealth.services.market_data import MarketDataService
from wolvwealth.storage.price_store import PriceStore


def fail_downloader(*_args, **_kwargs):
    pytest.fail("must not download")


def build_service(tmp_path, downloader=fail_downloader):
    """A MarketDataService over a real on-disk store seeded with the synthetic market."""
    market = synthetic_market()
    store = PriceStore(tmp_path / "historical_prices.parquet", tmp_path / "universe.csv")
    store.save_universe(list(market.universe))
    store.save_prices(market.prices)
    return MarketDataService(store, "2020-01-01", downloader=downloader, log=lambda *_: None), store


def test_first_load_computes_and_persists_stats(tmp_path):
    service, store = build_service(tmp_path)
    data = service.data
    assert data.stats is not None and data.stats.covers(data.universe)
    meta, mu, cov = store.load_stats()
    assert meta == {
        "as_of": str(data.as_of),
        "price_rows": len(data.prices),
        "tickers": len(data.universe),
        "estimator": stats_module.ESTIMATOR,
    }
    expected = MarketStats.compute(data.prices)
    assert np.allclose(mu, expected.mu) and np.allclose(cov, expected.cov)


def test_second_load_reuses_persisted_stats_without_recomputing(tmp_path, monkeypatch):
    first, _ = build_service(tmp_path)
    expected = first.data.stats
    monkeypatch.setattr(MarketStats, "compute", lambda *_: pytest.fail("must slice the cache, not recompute"))
    second, _ = build_service(tmp_path)
    assert np.allclose(second.data.stats.mu, expected.mu)
    assert np.allclose(second.data.stats.cov, expected.cov)


@pytest.mark.parametrize(
    "spoil",
    [
        lambda meta: json.dumps({**meta, "as_of": "1999-12-31"}),
        lambda meta: json.dumps({**meta, "estimator": "ema_250"}),
        lambda meta: "not json {",
    ],
    ids=["other prices", "other estimator", "corrupt sidecar"],
)
def test_a_stale_or_corrupt_stats_cache_is_rebuilt_not_served(tmp_path, spoil):
    """Other prices, or a changed ESTIMATOR, must never serve expected returns computed the old way."""
    first, store = build_service(tmp_path)
    first.data  # pylint: disable=pointless-statement  # populate the stats cache
    meta = json.loads(store.stats_meta_path.read_text(encoding="utf-8"))
    store.stats_meta_path.write_text(spoil(meta), encoding="utf-8")
    second, _ = build_service(tmp_path)
    assert second.data.stats is not None and second.data.stats.estimator == stats_module.ESTIMATOR
    assert json.loads(store.stats_meta_path.read_text(encoding="utf-8")) == meta


def test_a_legacy_csv_cache_is_migrated_on_first_load_instead_of_downloaded(tmp_path):
    logged = []
    market = synthetic_market()
    legacy = tmp_path / "historical_prices.csv"
    market.prices.to_csv(legacy, index=True)
    store = PriceStore(tmp_path / "historical_prices.parquet", tmp_path / "universe.csv", legacy_csv_paths=(legacy,))
    store.save_universe(list(market.universe))
    service = MarketDataService(store, "2020-01-01", downloader=fail_downloader, log=logged.append)
    assert service.data.as_of == market.as_of and store.has_prices()
    assert any("migrated" in line for line in logged)


def test_a_full_refresh_re_downloads_the_whole_history_and_recovers_blank_tickers(tmp_path):
    """`refresh-data --full`: the one way to pick up a ticker the first download lost to throttling."""
    market = synthetic_market()
    store = PriceStore(tmp_path / "historical_prices.parquet", tmp_path / "universe.csv")
    store.save_universe(list(market.universe))
    throttled = market.prices.copy()
    throttled["CCC"] = np.nan
    store.save_prices(throttled)
    calls = []

    def downloader(tickers, start, **_kwargs):
        calls.append((list(tickers), start))
        return market.prices.loc[:, list(tickers)]

    service = MarketDataService(store, "2020-01-01", downloader=downloader, log=lambda *_: None)
    assert "CCC" not in service.data.tradeable
    assert service.refresh(full=True) is True
    assert calls == [(list(market.universe), "2020-01-01")]
    assert "CCC" in service.data.tradeable and store.load_prices()["CCC"].notna().all()
    meta, _mu, cov = store.load_stats()
    assert "CCC" in cov.columns and meta["price_rows"] == len(market.prices)

    # Throttled: most names come back empty. The cache must not be replaced by the wreckage.
    service.downloader = lambda tickers, _start, **_kwargs: market.prices.loc[:, list(tickers)[:1]]
    with pytest.raises(RuntimeError, match="not overwriting"):
        service.refresh(full=True)
    assert store.load_prices()["CCC"].notna().all() and "CCC" in service.data.tradeable


def wide_market_prices(tickers, periods=400):
    """Deterministic random-walk prices for ``tickers``: the went-dark guard needs more than four names."""
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2020-01-02", periods=periods)
    prices = pd.DataFrame(
        {t: np.round(100 * np.exp(np.cumsum(0.0003 + 0.01 * rng.standard_normal(periods))), 2) for t in tickers},
        index=dates,
    )
    prices.index.name = "Date"
    return prices


def test_a_full_refresh_merges_over_the_cache_so_throttling_cannot_shorten_a_history(tmp_path):
    """Regression: Yahoo, throttling, served only the recent tail of a long-listed ticker's history
    (and nothing at all for another), and a full refresh overwrote the cache with it, so a company
    listed for decades looked like a recent IPO to the optimizer."""
    tickers = [f"TK{i}" for i in range(8)]
    cached = wide_market_prices(tickers)
    store = PriceStore(tmp_path / "historical_prices.parquet", tmp_path / "universe.csv")
    store.save_universe(tickers)
    store.save_prices(cached)
    new_dates = pd.bdate_range(cached.index[-1] + pd.Timedelta(days=1), periods=2)
    full = pd.concat([cached, pd.DataFrame({t: [cached[t].iloc[-1]] * 2 for t in tickers}, index=new_dates)])

    def downloader(requested, _start, **_kwargs):
        served = full.loc[:, list(requested)].copy()
        served["TK2"] = served["TK2"].where(served.index >= served.index[-60])  # only the recent tail came back
        served["TK3"] = np.nan  # nothing at all: throttled or delisted, indistinguishable
        return served

    service = MarketDataService(store, "2020-01-01", downloader=downloader, log=lambda *_: None)
    assert service.refresh(full=True) is True
    merged = store.load_prices()
    # The tail-only ticker keeps every cached day and gains the new ones; it still trades.
    assert merged["TK2"].count() == len(full)
    pd.testing.assert_series_equal(merged["TK2"], full["TK2"], check_names=False, check_freq=False)
    assert service.data.has_price("TK2") and service.data.history_days["TK2"] == len(full)
    # The empty ticker keeps its history but has no current price until something recovers it.
    assert merged["TK3"].count() == len(cached) and not service.data.has_price("TK3")
    pd.testing.assert_series_equal(
        merged["TK3"].iloc[: len(cached)], cached["TK3"], check_names=False, check_freq=False
    )


def test_a_full_refresh_takes_only_the_fresh_history_for_a_re_adjusted_ticker(tmp_path):
    """A split ticker's cached rows are on the old basis: the cache must not fill them back in --
    unless the new-basis history came back truncated, in which case the ticker is blanked."""
    tickers = [f"TK{i}" for i in range(8)]
    cached = wide_market_prices(tickers)

    def run(truncated):
        root = tmp_path / ("truncated" if truncated else "complete")
        store = PriceStore(root / "historical_prices.parquet", root / "universe.csv")
        store.save_universe(tickers)
        store.save_prices(cached)
        new_dates = pd.bdate_range(cached.index[-1] + pd.Timedelta(days=1), periods=2)
        full = pd.concat([cached, pd.DataFrame({t: [cached[t].iloc[-1]] * 2 for t in tickers}, index=new_dates)])
        full["TK1"] = (full["TK1"] * 0.5).round(2)  # 2:1 split: the served history is rescaled end to end
        if truncated:
            full["TK1"] = full["TK1"].where(full.index >= full.index[-60])

        def downloader(requested, _start, **_kwargs):
            return full.loc[:, list(requested)]

        service = MarketDataService(store, "2020-01-01", downloader=downloader, log=lambda *_: None)
        assert service.refresh(full=True) is True
        return store.load_prices(), full

    merged, full = run(truncated=False)
    pd.testing.assert_series_equal(merged["TK1"], full["TK1"], check_names=False, check_freq=False)
    assert merged["TK1"].pct_change().abs().max() < 0.3, "no old-basis row survives to fake a crash"

    merged, _full = run(truncated=True)
    assert merged["TK1"].isna().all(), "a truncated new-basis history is excluded, not adopted"
    assert merged["TK0"].notna().all()


def test_refresh_recomputes_stats_for_the_new_rows(tmp_path):
    service, store = build_service(tmp_path)
    last = service.data.prices.index[-1]

    def downloader(tickers, _start, **_kwargs):
        dates = pd.bdate_range(last + pd.Timedelta(days=1), periods=3)
        fresh = pd.DataFrame({t: [100.0, 101.0, 102.0] for t in tickers}, index=dates)
        fresh.iloc[0, 0], fresh.iloc[1, 1] = 0.0, np.inf  # what Yahoo sends on a bad day
        return fresh

    service.downloader = downloader
    assert service.refresh()
    assert store.load_prices().iloc[-3:, :2].isna().sum().sum() == 2, "a bad close is merged as missing"
    meta, _mu, cov = store.load_stats()
    data = service.data
    assert meta == {
        "as_of": str(data.as_of),
        "price_rows": len(data.prices),
        "tickers": len(data.universe),
        "estimator": stats_module.ESTIMATOR,
    }
    assert list(cov.columns) == list(data.prices.columns)


def test_refresh_fetches_the_tbill_rate_and_survives_its_failure(tmp_path):
    service, store = build_service(tmp_path)
    service.set_data(synthetic_market())
    assert service.tbill is None
    service.tbill_downloader = lambda: (0.0412, RECENT_DAY)
    assert service.refresh_tbill() is True
    assert service.tbill == {"rate": 0.0412, "as_of": RECENT_DAY} == store.load_tbill()

    def broken():
        raise RuntimeError("no quotes")

    tomorrow = (pd.Timestamp.now() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    # Every portfolio set to "tbill" uses this number, often unattended: a tampered quote must not be stored.
    for bad in (
        broken,
        lambda: (0.9, RECENT_DAY),
        lambda: (-0.5, RECENT_DAY),
        lambda: (float("nan"), RECENT_DAY),
        lambda: ("0.04", RECENT_DAY),
        lambda: (0.04, "someday"),
        lambda: (0.04, tomorrow),
        lambda: (0.04, "2020-01-02"),
    ):
        service.tbill_downloader = bad
        assert service.refresh_tbill() is False
        assert (
            service.tbill == {"rate": 0.0412, "as_of": RECENT_DAY} == store.load_tbill()
        ), "the last good value stands"


def test_fresh_prices_but_no_tbill_triggers_only_a_tbill_fetch(tmp_path):
    """An install from before the T-bill reference existed picks it up at startup without a price refresh."""
    service, _store = build_service(tmp_path)
    service.set_data(dated_to_today(synthetic_market()))
    service.tbill_downloader = lambda: (0.0412, RECENT_DAY)
    thread = service.start_background_refresh(max_age_days=7)
    assert thread is not None
    thread.join(timeout=10)
    assert service.tbill["rate"] == 0.0412
    assert service.start_background_refresh(max_age_days=7) is None, "nothing left to do"


def test_stats_for_slices_equal_direct_computation(market):
    subset = ["AAA", "CCC"]
    full = MarketData.build(market.universe, market.prices, MarketStats.compute(market.prices))
    sliced_mu, sliced_cov = full.stats_for(subset)
    direct = MarketStats.compute(market.prices[subset])
    assert np.allclose(sliced_mu, direct.mu) and np.allclose(sliced_cov, direct.cov)
    assert list(sliced_mu.index) == subset and list(sliced_cov.columns) == subset


def test_truncated_snapshot_drops_stats_to_avoid_lookahead(market):
    full = MarketData.build(market.universe, market.prices, MarketStats.compute(market.prices))
    truncated = full.truncated(market.prices.index[100])
    assert truncated.stats is None


def test_optimize_result_identical_with_and_without_precomputed_stats(market):
    body = {"initial_cash": 10_000, "universe": ["AAA", "BBB", "CCC"]}
    plain = optimizer.optimize(body, market)
    precomputed = MarketData.build(market.universe, market.prices, MarketStats.compute(market.prices))
    assert optimizer.optimize(body, precomputed) == plain
