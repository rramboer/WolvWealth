"""The precomputed-statistics lifecycle: built once per data refresh, sliced per request."""
import json

import numpy as np
import pandas as pd
import pytest

from conftest import synthetic_market
from wolvwealth.domain import optimizer
from wolvwealth.domain.market import MarketData
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
    assert meta == {"as_of": str(data.as_of), "price_rows": len(data.prices), "tickers": len(data.universe)}
    expected = MarketStats.compute(data.prices)
    assert np.allclose(mu, expected.mu) and np.allclose(cov, expected.cov)


def test_second_load_reuses_persisted_stats_without_recomputing(tmp_path, monkeypatch):
    first, _ = build_service(tmp_path)
    expected = first.data.stats
    monkeypatch.setattr(MarketStats, "compute", lambda *_: pytest.fail("must slice the cache, not recompute"))
    second, _ = build_service(tmp_path)
    assert np.allclose(second.data.stats.mu, expected.mu)
    assert np.allclose(second.data.stats.cov, expected.cov)


def test_stats_not_matching_prices_are_rebuilt(tmp_path):
    first, store = build_service(tmp_path)
    first.data  # pylint: disable=pointless-statement  # populate the stats cache
    meta = json.loads(store.stats_meta_path.read_text(encoding="utf-8"))
    store.stats_meta_path.write_text(json.dumps({**meta, "as_of": "1999-12-31"}), encoding="utf-8")
    second, _ = build_service(tmp_path)
    assert second.data.stats is not None
    assert json.loads(store.stats_meta_path.read_text(encoding="utf-8")) == meta


def test_refresh_recomputes_stats_for_the_new_rows(tmp_path):
    service, store = build_service(tmp_path)
    last = service.data.prices.index[-1]

    def downloader(tickers, _start, **_kwargs):
        dates = pd.bdate_range(last + pd.Timedelta(days=1), periods=3)
        return pd.DataFrame({t: [100.0, 101.0, 102.0] for t in tickers}, index=dates)

    service.downloader = downloader
    assert service.refresh()
    meta, _mu, cov = store.load_stats()
    data = service.data
    assert meta == {"as_of": str(data.as_of), "price_rows": len(data.prices), "tickers": len(data.universe)}
    assert list(cov.columns) == list(data.prices.columns)


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
