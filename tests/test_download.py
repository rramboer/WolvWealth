"""Downloading: the Yahoo adapter's shapes, retries that shrink their batches, and a short universe reported."""

import numpy as np
import pandas as pd
import pytest

from conftest import REAL_DOWNLOAD_TBILL_RATE, synthetic_market
from wolvwealth.domain.errors import DataError
from wolvwealth.market import yahoo
from wolvwealth.services.market_data import MarketDataService
from wolvwealth.storage.price_store import PriceStore

TICKERS = ["AAA", "BBB", "CCC", "DDD"]
DATES = pd.bdate_range("2024-01-02", periods=5)


def frame_for(tickers, live=()):
    """A price frame where only ``live`` tickers carry numbers."""
    return pd.DataFrame(
        {t: ([1.0] * len(DATES) if t in live else [np.nan] * len(DATES)) for t in tickers},
        index=DATES,
    )


# --- the yfinance adapter ---------------------------------------------------


def test_download_prices_frames_a_single_ticker_series_and_rounds(monkeypatch):
    """yfinance hands back a Series for one ticker and a frame for several; callers always get a frame."""

    def fake_download(tickers, **_kwargs):
        if len(tickers) == 1:
            return pd.DataFrame({"Close": [1.23456, 2.34567]}, index=DATES[:2])
        return pd.DataFrame({("Close", t): [1.0, 2.0] for t in tickers}, index=DATES[:2])

    monkeypatch.setattr(yahoo.yf, "download", fake_download)
    one = yahoo.download_prices(["AAA"], "2024-01-01", "2024-01-03", sleep=lambda _s: None)
    assert list(one.columns) == ["AAA"] and one["AAA"].tolist() == [1.23, 2.35]
    two = yahoo.download_prices(["AAA", "BBB"], "2024-01-01", sleep=lambda _s: None)
    assert list(two.columns) == ["AAA", "BBB"]


def test_download_tbill_rate_turns_the_percent_quote_into_a_fraction_and_raises_on_nothing(monkeypatch):
    served = {}
    monkeypatch.setattr(yahoo.yf, "download", lambda symbol, **_kwargs: served[symbol])
    dates = pd.to_datetime(["2026-10-28", "2026-10-29", "2026-10-30"])
    # Newer yfinance: a (field, symbol) column index; today's row may not have printed yet.
    served[yahoo.TBILL_SYMBOL] = pd.DataFrame({("Close", yahoo.TBILL_SYMBOL): [4.10, 4.12, np.nan]}, index=dates)
    assert REAL_DOWNLOAD_TBILL_RATE() == (0.0412, "2026-10-29")
    # Older yfinance: flat columns, so "Close" is a Series.
    served[yahoo.TBILL_SYMBOL] = pd.DataFrame({"Close": [4.10, 4.15]}, index=dates[:2])
    assert REAL_DOWNLOAD_TBILL_RATE() == (0.0415, "2026-10-29")
    served[yahoo.TBILL_SYMBOL] = pd.DataFrame({"Close": [np.nan]}, index=dates[:1])
    with pytest.raises(RuntimeError, match="no quotes"):
        REAL_DOWNLOAD_TBILL_RATE()


def test_empty_tickers_lists_only_the_all_nan_columns():
    prices = frame_for(TICKERS, live=["AAA", "CCC"])
    assert yahoo.empty_tickers(prices) == ["BBB", "DDD"]
    assert yahoo.empty_tickers(prices, ["AAA", "BBB"]) == ["BBB"]


def test_retries_ask_in_smaller_batches_each_pass(monkeypatch):
    """A straggler that only answers to a small request must still be recovered."""
    sizes = []

    def fake_chunk(tickers, _start, _end):
        sizes.append(len(tickers))
        # Only ever answers when asked in a batch of 5 or fewer.
        live = tickers if len(tickers) <= 5 else []
        return frame_for(tickers, live=live)

    monkeypatch.setattr(yahoo, "_download_chunk", fake_chunk)
    many = [f"T{i:03d}" for i in range(120)]
    prices = yahoo.download_prices(many, "2024-01-01", "2024-01-09", sleep=lambda _s: None)

    assert yahoo.empty_tickers(prices) == []
    assert sizes[0] == yahoo.CHUNK_SIZE  # first pass uses the full chunk
    assert min(sizes) <= 5  # later passes shrink until the request succeeds


def test_retries_stop_early_when_nothing_is_missing(monkeypatch):
    calls = []

    def fake_chunk(tickers, _start, _end):
        calls.append(len(tickers))
        return frame_for(tickers, live=tickers)

    monkeypatch.setattr(yahoo, "_download_chunk", fake_chunk)
    yahoo.download_prices(TICKERS, "2024-01-01", "2024-01-09", sleep=lambda _s: None)
    assert len(calls) == 1  # one pass, no retries


# --- the service's first download -------------------------------------------


def test_a_short_universe_is_reported_and_a_complete_one_is_not(tmp_path):
    """Silently optimizing over a fraction of the universe would be the bad outcome."""

    def first_download_log(live):
        logged = []
        root = tmp_path / f"{len(live)}-live"
        store = PriceStore(root / "historical_prices.parquet", root / "universe.csv")
        store.save_universe(TICKERS)
        service = MarketDataService(
            store, "2024-01-01", downloader=lambda tickers, _s, **_k: frame_for(tickers, live=live), log=logged.append
        )
        service.data  # pylint: disable=pointless-statement  # triggers the first-run download
        return logged

    short = first_download_log(["AAA", "BBB"])  # CCC and DDD come back empty
    assert any("2 of 4 tickers returned no data" in line for line in short)
    assert any("refresh-data --full" in line for line in short)
    assert not any("returned no data" in line for line in first_download_log(TICKERS))
    with pytest.raises(DataError, match="Nothing was saved"):
        first_download_log([])  # offline: everything empty
    assert not (tmp_path / "0-live" / "historical_prices.parquet").exists()


def test_incremental_refresh_still_guards_against_mass_failure(tmp_path):
    """The went-dark guard protects the append path; it must not have been lost."""
    market = synthetic_market()
    store = PriceStore(tmp_path / "historical_prices.parquet", tmp_path / "universe.csv")
    store.save_universe(list(market.universe))
    store.save_prices(market.prices)
    service = MarketDataService(store, "2020-01-01", downloader=None, log=lambda *_: None)
    last = service.data.prices.index[-1]

    def mostly_empty(tickers, _start, **_kwargs):
        dates = pd.bdate_range(last + pd.Timedelta(days=1), periods=2)
        return pd.DataFrame({t: ([1.0, 2.0] if t == tickers[0] else [np.nan, np.nan]) for t in tickers}, index=dates)

    service.downloader = mostly_empty
    with pytest.raises(RuntimeError, match="rate limiting"):
        service.refresh()
