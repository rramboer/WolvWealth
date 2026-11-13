"""Download the price history from Yahoo Finance and write historical_prices.csv.

Run it with bin/refresh-prices. A refresh always downloads the whole history
again rather than adding new days to the end: after a split or dividend,
Yahoo re-adjusts every earlier price, so old and new downloads don't line
up (NVDA's 2024-01-30 close was $627.74 before its 2024 10-for-1 split and
is $62.59 after).
"""
import argparse
import datetime
import os
import sys
import time
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

PRICES_FILE = "historical_prices.csv"
UNIVERSE_FILE = "ticker_universe.csv"
START_DATE = "2006-01-01"
BATCH_SIZE = 200
RETRY_PAUSE_SECONDS = 5
# A ticker whose last price is more than this many days before the end of the
# data has stopped trading (delisted, acquired or renamed).
STALE_DAYS = 10
# Refuse to write the file if more of the universe than this is missing; that
# usually means Yahoo rate limited the download, not that the stocks are gone.
MAX_MISSING_FRACTION = 0.25
MARKET_TZ = ZoneInfo("America/New_York")
# Yahoo's closing prices are in shortly after the 4:00 pm close.
CLOSE_IS_FINAL = datetime.time(16, 30)


def download_end_date(now: datetime.datetime = None) -> datetime.date:
    """Return the (exclusive) end date for a download that stops at the last finished trading day.

    Before the close is final on a weekday, today's price is still moving,
    so the download stops at yesterday.
    """
    now = datetime.datetime.now(MARKET_TZ) if now is None else now.astimezone(MARKET_TZ)
    if now.weekday() < 5 and now.time() >= CLOSE_IS_FINAL:
        return now.date() + datetime.timedelta(days=1)
    return now.date()


def download_closes(tickers: list, start: str, end: datetime.date) -> pd.DataFrame:
    """Download daily closes, adjusted for splits and dividends, one column per ticker.

    Tickers Yahoo returns nothing for are left out. Each batch is retried once
    for the tickers it missed, since a busy server can drop some of them.
    """
    frames = []
    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i:i + BATCH_SIZE]
        closes = _download_batch(batch, start, end)
        missed = [t for t in batch if t not in closes.columns]
        if missed:
            time.sleep(RETRY_PAUSE_SECONDS)
            closes = pd.concat([closes, _download_batch(missed, start, end)], axis=1)
        frames.append(closes)
        print(f"  downloaded {min(i + BATCH_SIZE, len(tickers))} of {len(tickers)} tickers", file=sys.stderr)
    closes = pd.concat(frames, axis=1)
    closes = closes[closes.index.date < end]  # Never keep a row for a day that isn't over.
    closes.index.name = "Date"
    return drop_incomplete_days(closes)


def drop_incomplete_days(closes: pd.DataFrame) -> pd.DataFrame:
    """Drop days with no prices, and trailing days that have under half the prices of the day before.

    Yahoo sometimes lists today with no close yet, or with only some tickers
    filled in; kept, that row would make most tickers look like they have no
    current price.
    """
    closes = closes.dropna(how="all")
    while len(closes) > 1:
        counts = closes.iloc[-2:].notna().sum(axis=1)
        if counts.iloc[-1] >= 0.5 * counts.iloc[-2]:
            break
        closes = closes.iloc[:-1]
    return closes


def _download_batch(tickers: list, start: str, end: datetime.date) -> pd.DataFrame:
    """Download one batch and return its closes, dropping tickers that came back empty."""
    data = yf.download(tickers, start=start, end=end.isoformat(), auto_adjust=True, progress=False, threads=True)
    if data is None or data.empty:
        return pd.DataFrame()
    closes = data["Close"]
    if isinstance(closes, pd.Series):  # A single ticker can come back as a Series.
        closes = closes.to_frame(tickers[0])
    closes.index = pd.to_datetime(closes.index).tz_localize(None).normalize()
    return closes.dropna(axis=1, how="all")


def stopped_trading(closes: pd.DataFrame) -> dict:
    """Return ticker to last traded date for tickers with no price in the last STALE_DAYS days of the data."""
    cutoff = closes.index[-1] - pd.Timedelta(days=STALE_DAYS)
    last = closes.apply(lambda column: column.last_valid_index())
    return {ticker: day.date() for ticker, day in last.items() if day < cutoff}


def write_prices(closes: pd.DataFrame, path: str = PRICES_FILE) -> None:
    """Write closes to path, replacing it only once the new file is complete."""
    closes = closes.reindex(sorted(closes.columns), axis=1)
    tmp = path + ".tmp"
    # Six significant digits keep small early prices (NVDA was $0.29 in 2006)
    # accurate without writing every float to 17 digits.
    closes.to_csv(tmp, index=True, date_format="%Y-%m-%d", float_format="%.6g")
    os.replace(tmp, path)


def read_universe(path: str = UNIVERSE_FILE) -> list:
    """Return the tickers in the universe file, largest first."""
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def write_universe(universe: list, path: str = UNIVERSE_FILE) -> None:
    """Write the universe file, one ticker per line, replacing it only once complete."""
    tmp = path + ".tmp"
    # \r\n line endings, as ApplicationState.save_ticker_universe (csv.writer) writes them.
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.writelines(ticker + "\r\n" for ticker in universe)
    os.replace(tmp, path)


def refresh(universe: list, start: str = START_DATE, force: bool = False, prune: bool = False) -> dict:
    """Download the full history for universe and write it to PRICES_FILE.

    Every ticker in universe gets a column; one Yahoo returned nothing for is
    left empty, so the API reports it as having no current price. With prune,
    tickers that are missing or stopped trading are instead removed from the
    price file and from UNIVERSE_FILE, keeping the universe's order. Returns
    a report: the date range, the tickers that are missing or stopped
    trading, and the ones removed. Raises RuntimeError, leaving the old files
    alone, if too many tickers are missing (unless force).
    """
    end = download_end_date()
    closes = download_closes(universe, start, end)
    missing = [t for t in universe if t not in closes.columns]
    if not force and len(missing) > MAX_MISSING_FRACTION * len(universe):
        raise RuntimeError(
            f"Yahoo returned nothing for {len(missing)} of {len(universe)} tickers, which looks like rate "
            "limiting. The price file was not changed. Try again later, or pass --force."
        )
    report = {
        "first_date": closes.index[0].date(),
        "last_date": closes.index[-1].date(),
        "missing": missing,
        "stopped_trading": stopped_trading(closes),
        "removed": [],
    }
    if prune:
        report["removed"] = [t for t in universe if t in missing or t in report["stopped_trading"]]
        universe = [t for t in universe if t not in report["removed"]]
    write_prices(closes.reindex(columns=universe), PRICES_FILE)
    if prune:
        write_universe(universe, UNIVERSE_FILE)
    return report


def main(argv: list) -> int:
    """Command line entry point for bin/refresh-prices."""
    parser = argparse.ArgumentParser(prog="bin/refresh-prices", description=__doc__.split("\n")[0])
    parser.add_argument("--start", default=START_DATE, help=f"first date to download (default {START_DATE})")
    parser.add_argument("--force", action="store_true", help="write the file even if many tickers are missing")
    parser.add_argument(
        "--prune", action="store_true", help=f"remove tickers that are missing or stopped trading from {UNIVERSE_FILE}"
    )
    args = parser.parse_args(argv)

    universe = read_universe()
    print(f"Downloading {len(universe)} tickers from {args.start}...", file=sys.stderr)
    try:
        report = refresh(universe, args.start, args.force, args.prune)
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Wrote {PRICES_FILE}: {report['first_date']} to {report['last_date']}.")
    if report["missing"]:
        print(f"No data from Yahoo for {len(report['missing'])} tickers: {' '.join(report['missing'])}")
    if report["stopped_trading"]:
        stale = " ".join(f"{t} ({day})" for t, day in sorted(report["stopped_trading"].items()))
        print(f"Stopped trading ({len(report['stopped_trading'])}): {stale}")
    if report["removed"]:
        left = len(universe) - len(report["removed"])
        print(f"Removed {len(report['removed'])} tickers from {UNIVERSE_FILE}; {left} left.")
    return 0
