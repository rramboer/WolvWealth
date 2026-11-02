"""An incremental refresh detects re-adjusted histories (splits/dividends) and repairs them.

Yahoo adjusts the whole served history retroactively after a split or
dividend. Appending new rows onto a cache on the old basis fakes a crash, so
the refresh re-downloads a few cached days, compares them, and replaces the
full history of any ticker that disagrees.
"""

import pandas as pd

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


def test_a_split_ticker_gets_its_whole_history_re_downloaded(tmp_path):
    service, store = build_service(tmp_path)
    download, full, calls = splitting_downloader(service)
    service.downloader = download
    before = service.data.prices

    assert service.refresh() is True

    prices = store.load_prices()
    # The split ticker's history is on the new basis, end to end.
    pd.testing.assert_series_equal(prices["BBB"], full["BBB"].reindex(prices.index), check_names=False)
    # Untouched tickers keep their cached history and gain the new rows.
    pd.testing.assert_series_equal(prices["AAA"].loc[before.index], before["AAA"], check_names=False)
    assert len(prices) == len(before) + 3
    # No fake crash: the repaired history has no split-sized one-day move.
    assert prices["BBB"].pct_change().abs().max() < 0.3
    # The repair was a single-ticker request from the start of history.
    assert calls[1] == (["BBB"], "2020-01-01")


def test_unchanged_overlap_appends_without_any_repair_download(tmp_path):
    service, store = build_service(tmp_path)
    download, _full, calls = splitting_downloader(service, ratio=1.0)
    service.downloader = download
    assert service.refresh() is True
    assert len(calls) == 1, "identical overlap must not trigger a repair download"
    assert len(store.load_prices()) == len(synthetic_market().prices) + 3


def test_a_failed_repair_blanks_the_ticker_instead_of_mixing_bases(tmp_path):
    service, store = build_service(tmp_path)
    download, _full, _calls = splitting_downloader(service, fail_repair=True)
    service.downloader = download
    assert service.refresh() is True
    prices = store.load_prices()
    assert prices["BBB"].isna().all(), "a mixed-basis history must never be persisted"
    assert prices["AAA"].notna().any()


def test_in_memory_snapshot_is_repaired_too(tmp_path):
    service, _ = build_service(tmp_path)
    download, full, _calls = splitting_downloader(service)
    service.downloader = download
    service.refresh()
    returns = service.data.prices["BBB"].pct_change().abs()
    assert returns.max() < 0.3


def test_refresh_with_nothing_new_and_matching_overlap_is_a_noop(tmp_path):
    """Re-serving only the already-cached overlap changes nothing and persists nothing."""
    service, store = build_service(tmp_path)
    cached = service.data.prices

    def overlap_only(tickers, start, **_kwargs):
        return cached.loc[cached.index >= start, list(tickers)]

    service.downloader = overlap_only
    before = store.load_prices()
    assert service.refresh() is False
    pd.testing.assert_frame_equal(store.load_prices(), before)
