"""Download adjusted closes from Yahoo Finance, politely.

Yahoo rate-limits bulk downloads of the ~2,000-ticker universe, and a
rate-limited ticker comes back as an all-NaN column rather than an
exception. So: download in chunks with pauses, then re-request the all-NaN
stragglers with backoff. Whatever is still empty afterwards is treated as
genuinely delisted.
"""
import time
from datetime import datetime

import pandas as pd
import yfinance as yf

CHUNK_SIZE = 100
CHUNK_PAUSE_SECONDS = 2
RETRY_PASSES = 4
RETRY_BACKOFF_SECONDS = 30


def _download_chunk(tickers: list, start_date: str, end_date: str) -> pd.DataFrame:
    """Download adjusted closes for one batch of tickers."""
    # auto_adjust=True makes "Close" the split/dividend-adjusted close
    # (newer yfinance dropped the separate "Adj Close" column here).
    data = yf.download(tickers, start=start_date, end=end_date, auto_adjust=True, progress=False)["Close"]
    if isinstance(data, pd.Series):  # single-ticker shape
        data = data.to_frame(name=tickers[0])
    return data.round(2)


def download_prices(tickers: list, start_date: str, end_date: str = None, sleep=time.sleep) -> pd.DataFrame:
    """Download adjusted closes for all ``tickers`` from start_date to end_date (default today)."""
    end_date = end_date or datetime.now().strftime("%Y-%m-%d")
    frames = []
    for i in range(0, len(tickers), CHUNK_SIZE):
        if i:
            sleep(CHUNK_PAUSE_SECONDS)
        frames.append(_download_chunk(tickers[i:i + CHUNK_SIZE], start_date, end_date))
    data = pd.concat(frames, axis=1).reindex(columns=list(tickers))

    for attempt in range(RETRY_PASSES):
        missing = [t for t in tickers if data[t].isna().all()]
        if not missing:
            break
        sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
        for i in range(0, len(missing), CHUNK_SIZE):
            chunk = missing[i:i + CHUNK_SIZE]
            retried = _download_chunk(chunk, start_date, end_date)
            for ticker in retried.columns.intersection(chunk):
                data[ticker] = retried[ticker]
            sleep(CHUNK_PAUSE_SECONDS)
    return data


def market_cap(ticker: str) -> float:
    """Market cap of a stock, or total assets of an ETF. One live request per call."""
    info = yf.Ticker(ticker).info
    try:
        return info["marketCap"]
    except KeyError:
        return info["totalAssets"]
