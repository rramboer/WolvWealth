"""Downloading: retries shrink their batches, and a short universe is reported."""

import numpy as np
import pandas as pd
import pytest

from conftest import synthetic_market
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


def test_a_short_universe_is_reported_not_swallowed(tmp_path):
    """Silently optimizing over a fraction of the universe would be the bad outcome."""
    logged = []
    store = PriceStore(tmp_path / "historical_prices.parquet", tmp_path / "universe.csv")
    store.save_universe(TICKERS)

    def downloader(tickers, _start, **_kwargs):
        return frame_for(tickers, live=["AAA", "BBB"])  # CCC and DDD come back empty

    service = MarketDataService(store, "2024-01-01", downloader=downloader, log=logged.append)
    service.data  # pylint: disable=pointless-statement  # triggers the first-run download

    assert any("2 of 4 tickers returned no data" in line for line in logged)
    assert any("refresh-data --full" in line for line in logged)


def test_a_complete_download_says_nothing(tmp_path):
    logged = []
    store = PriceStore(tmp_path / "historical_prices.parquet", tmp_path / "universe.csv")
    store.save_universe(TICKERS)
    service = MarketDataService(
        store,
        "2024-01-01",
        downloader=lambda tickers, _s, **_k: frame_for(tickers, live=tickers),
        log=logged.append,
    )
    service.data  # pylint: disable=pointless-statement
    assert not any("returned no data" in line for line in logged)


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
