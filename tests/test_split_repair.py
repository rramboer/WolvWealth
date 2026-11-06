"""A refresh trusts nothing it downloads: re-adjusted histories are repaired, poisoned ones excluded.

Yahoo adjusts the whole served history retroactively after a split or
dividend. Appending new rows onto a cache on the old basis fakes a crash, so
the refresh re-downloads a few cached days, compares them, and replaces the
full history of any ticker that disagrees.

A download can also be wrong in ways no split explains (corrupt, or
tampered with while the app runs unattended on a schedule). Those values
must never reach the optimizer, and the exclusion must be visible in the
reports an agent reads, not only in a log nobody watches.
"""

import numpy as np
import pandas as pd
import pytest

from conftest import synthetic_market
from wolvwealth.services.market_data import MarketDataService
from wolvwealth.storage.price_store import PriceStore


def build_service(tmp_path, downloader=None):
    market = synthetic_market()
    store = PriceStore(tmp_path / "historical_prices.parquet", tmp_path / "universe.csv")
    store.save_universe(list(market.universe))
    store.save_prices(market.prices)
    return MarketDataService(store, "2020-01-01", downloader=downloader, log=lambda *_: None), store


def splitting_downloader(service, split_ticker="BBB", ratio=0.5, fail_repair=False):
    """Serve prices as Yahoo would after ``split_ticker`` split: its whole history scaled by ``ratio``.

    The refresh request (many tickers, recent start) gets the overlap plus 3
    new rows; the repair request (just the split ticker, history start) gets
    the full rescaled history.
    """
    cached = service.data.prices
    adjusted = cached.copy()
    adjusted[split_ticker] = (adjusted[split_ticker] * ratio).round(2)
    new_dates = pd.bdate_range(cached.index[-1] + pd.Timedelta(days=1), periods=3)
    last = adjusted.iloc[-1]
    new_rows = pd.DataFrame(
        {t: [round(last[t] * f, 2) for f in (1.01, 1.02, 1.03)] for t in cached.columns}, index=new_dates
    )
    full = pd.concat([adjusted, new_rows])
    calls = []

    def download(tickers, start, **_kwargs):
        calls.append((list(tickers), start))
        if len(calls) > 1 and fail_repair:  # the repair request comes back empty (throttled)
            return full.loc[:, list(tickers)] * float("nan")
        return full.loc[full.index >= start, list(tickers)]

    return download, full, calls


@pytest.mark.parametrize("ratio", [0.5, 0.1, 1 / 15, 1 / 20, 10.0], ids=["2:1", "10:1", "15:1", "20:1", "1:10 reverse"])
def test_a_split_ticker_gets_its_whole_history_re_downloaded_on_disk_and_in_memory(tmp_path, ratio):
    service, store = build_service(tmp_path)
    download, full, calls = splitting_downloader(service, ratio=ratio)
    service.downloader = download
    before = service.data.prices

    assert service.refresh() is True

    prices = store.load_prices()
    # The split ticker's history is on the new basis, end to end, on disk and in the served snapshot.
    pd.testing.assert_series_equal(prices["BBB"], full["BBB"].reindex(prices.index), check_names=False)
    pd.testing.assert_series_equal(service.data.prices["BBB"], prices["BBB"], check_names=False, check_freq=False)
    # Untouched tickers keep their cached history and gain the new rows.
    pd.testing.assert_series_equal(prices["AAA"].loc[before.index], before["AAA"], check_names=False)
    assert len(prices) == len(before) + 3
    # No fake crash or spike: the repaired history has no split-sized one-day move.
    assert prices["BBB"].pct_change().abs().max() < 0.3
    # The repair was a single-ticker request from the start of history.
    assert calls[1] == (["BBB"], "2020-01-01")


@pytest.mark.parametrize(
    "ratio", [1.0, 1 - MarketDataService.READJUSTED_TOLERANCE / 2], ids=["identical", "within tolerance"]
)
def test_an_overlap_that_agrees_appends_without_any_repair_download(tmp_path, ratio):
    service, store = build_service(tmp_path)
    download, _full, calls = splitting_downloader(service, ratio=ratio)
    service.downloader = download
    assert service.refresh() is True
    assert len(calls) == 1, "an agreeing overlap must not trigger a repair download"
    assert len(store.load_prices()) == len(synthetic_market().prices) + 3


def test_a_failed_repair_blanks_the_ticker_instead_of_mixing_bases(tmp_path):
    service, store = build_service(tmp_path)
    download, _full, _calls = splitting_downloader(service, fail_repair=True)
    service.downloader = download
    assert service.refresh() is True
    prices = store.load_prices()
    assert prices["BBB"].isna().all(), "a mixed-basis history must never be persisted"
    assert prices["AAA"].notna().any()


@pytest.mark.parametrize("served", ["only the cached overlap", "nothing at all"])
def test_a_refresh_that_brings_nothing_new_changes_nothing(tmp_path, served):
    """Re-serving the cached overlap, or an empty frame (Yahoo down), persists nothing and reports no change."""
    service, store = build_service(tmp_path)
    cached = service.data.prices

    def download(tickers, start, **_kwargs):
        if served == "nothing at all":
            return pd.DataFrame()
        return cached.loc[cached.index >= start, list(tickers)]

    service.downloader = download
    before = store.load_prices()
    assert service.refresh() is False
    pd.testing.assert_frame_equal(store.load_prices(), before)


@pytest.mark.parametrize(
    "poison, check",
    [
        # One absurd close drags the estimates; the name is excluded, not averaged in.
        (
            lambda rows: rows.iloc.__setitem__((-2, 2), rows.iloc[-3, 2] * 50),
            lambda prices, service: (prices["CCC"].isna().all() and "CCC" in service.quarantine),
        ),
        (
            lambda rows: rows.iloc.__setitem__((-2, 2), 1e12),
            lambda prices, service: (np.isnan(prices["CCC"].iloc[-2]) and "CCC" not in service.quarantine),
        ),
        # Rows from the future and tickers nobody asked for are dropped on arrival.
        (
            lambda rows: rows.loc.__setitem__(pd.Timestamp.now().normalize() + pd.Timedelta(days=30), 1.0),
            lambda prices, service: prices.index[-1] < pd.Timestamp.now(),
        ),
        (lambda rows: rows.__setitem__("EVIL", 1.0), lambda prices, service: "EVIL" not in prices.columns),
    ],
    ids=["one-day spike", "absurd price", "future rows", "unrequested ticker"],
)
def test_a_poisoned_download_never_reaches_the_cache(tmp_path, poison, check):
    service, store = build_service(tmp_path)
    cached = service.data.prices

    def download(tickers, start, **_kwargs):
        rows = synthetic_market(seed=3).prices.iloc[:3].copy()
        rows.index = pd.bdate_range(cached.index[-1] + pd.Timedelta(days=1), periods=3)
        rows = rows * (cached.iloc[-1] / rows.iloc[0])  # continue smoothly from the cache
        rows = pd.concat([cached.loc[cached.index >= start], rows])
        poison(rows)
        return rows

    service.downloader = download
    assert service.refresh() is True
    assert check(store.load_prices(), service)
    assert check(service.data.prices, service), "the served snapshot matches what was saved"


def test_a_quarantine_is_reported_survives_refreshes_and_only_a_clean_full_refresh_lifts_it(tmp_path, monkeypatch):
    service, store = build_service(tmp_path)
    clean = synthetic_market().prices
    spiked = clean.copy()
    spiked.iloc[200, 1] *= 20
    service.downloader = lambda tickers, _start, **_kwargs: spiked.loc[:, list(tickers)]
    assert service.refresh(full=True) is True
    assert list(service.quarantine) == ["BBB"] and "x in one day on 2020-" in service.quarantine["BBB"]["reason"]
    assert "BBB" not in service.data.tradeable

    # The next incremental refresh brings believable new rows for BBB; it stays excluded anyway.
    def more_rows(tickers, start, **_kwargs):
        rows = pd.DataFrame(
            {t: [clean[t].iloc[-1]] * 2 for t in tickers},
            index=pd.bdate_range(clean.index[-1] + pd.Timedelta(days=1), periods=2),
        )
        return pd.concat([store.load_prices().loc[start:, list(tickers)], rows])

    service.downloader = more_rows
    assert service.refresh() is True and store.load_prices()["BBB"].isna().all()

    # Too many implausible names at once is a bad download, not a bad market: nothing is written.
    before = store.load_prices()
    wrecked = clean * np.where(np.arange(len(clean))[:, None] == 300, 30.0, 1.0)
    service.downloader = lambda tickers, _start, **_kwargs: wrecked.loc[:, list(tickers)]
    with pytest.raises(RuntimeError, match="moved implausibly"):
        service.refresh(full=True)
    assert store.load_prices().equals(before) and list(service.quarantine) == ["BBB"]

    service.downloader = lambda tickers, _start, **_kwargs: clean.loc[:, list(tickers)]
    assert service.refresh(full=True) is True
    assert service.quarantine == {} and not store.quarantine_path.exists() and "BBB" in service.data.tradeable
