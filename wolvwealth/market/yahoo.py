"""Download adjusted closes from Yahoo Finance, politely.

Yahoo rate-limits bulk downloads of the ~2,000-ticker universe, and a
rate-limited ticker comes back as an all-NaN column rather than an
exception. So: download in chunks with pauses, then re-request the all-NaN
stragglers with backoff, in smaller batches each pass.

Whatever is still empty is *either* delisted or still throttled, and there
is no way to tell from here: Yahoo returns the same nothing for a symbol
that was acquired as for one it is refusing to serve. Callers should report
the count rather than quietly treat a short universe as the whole market.
"""

import time
from datetime import datetime

import pandas as pd
import yfinance as yf

CHUNK_SIZE = 100
CHUNK_PAUSE_SECONDS = 2
RETRY_BACKOFF_SECONDS = 30
#: One retry pass per entry, asking in smaller batches each time. A full
#: 1982-ticker download leaves a few hundred empty columns; re-requesting 60
#: of them recovered 11, so a meaningful share are throttling rather than
#: delistings and are worth chasing. Shrinking the batch is a heuristic: a
#: shorter request is cheaper for Yahoo to answer and costs us less when it
#: is refused.
RETRY_CHUNK_SIZES = (100, 25, 5)
#: Yahoo's symbol for the 13-week US Treasury bill discount yield, quoted in
#: percent (4.12 means 4.12%). The textbook risk-free rate, offered to users
#: as a reference they can copy onto a portfolio, never applied for them.
TBILL_SYMBOL = "^IRX"


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
        frames.append(_download_chunk(tickers[i : i + CHUNK_SIZE], start_date, end_date))
    data = pd.concat(frames, axis=1).reindex(columns=list(tickers))

    for attempt, chunk_size in enumerate(RETRY_CHUNK_SIZES):
        missing = [t for t in tickers if data[t].isna().all()]
        if not missing:
            break
        sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
        for i in range(0, len(missing), chunk_size):
            chunk = missing[i : i + chunk_size]
            retried = _download_chunk(chunk, start_date, end_date)
            for ticker in retried.columns.intersection(chunk):
                data[ticker] = retried[ticker]
            sleep(CHUNK_PAUSE_SECONDS)
    return data


def download_tbill_rate() -> tuple:
    """Return ``(rate, as_of)``: the latest 13-week T-bill yield as a fraction, and its date.

    Raises if Yahoo returns nothing; the caller decides whether that is
    fatal (it never is: the rate is a reference, not an input the app needs).
    """
    data = yf.download(TBILL_SYMBOL, period="10d", auto_adjust=False, progress=False)["Close"]
    if isinstance(data, pd.DataFrame):
        data = data.iloc[:, 0]
    data = data.dropna()
    if data.empty:
        raise RuntimeError(f"Yahoo returned no quotes for {TBILL_SYMBOL}")
    return round(float(data.iloc[-1]) / 100, 6), str(data.index[-1].date())


def empty_tickers(prices: pd.DataFrame, tickers=None) -> list:
    """Tickers that came back with no data at all.

    Either they stopped trading (Yahoo serves nothing for a symbol once it is
    gone, not even its history) or the request was throttled. The caller
    cannot tell which, so it should say how many rather than pretend the
    universe is complete.
    """
    wanted = list(prices.columns if tickers is None else tickers)
    return [t for t in wanted if t in prices.columns and prices[t].isna().all()]
