"""Loading of the two market-data CSVs shared by the web app and the backtester.

Kept free of Flask so that the backtesting package can read prices without
importing the web application.
"""

import csv
import functools
import os
import pathlib

import pandas as pd

DEFAULT_DATA_DIR = pathlib.Path(__file__).resolve().parents[1]


def data_dir() -> pathlib.Path:
    """Directory containing ticker_universe.csv and historical_prices.csv."""
    return pathlib.Path(os.environ.get("WOLVWEALTH_DATA_DIR", DEFAULT_DATA_DIR))


def load_ticker_universe(directory: pathlib.Path | None = None) -> list[str]:
    """Tickers from ticker_universe.csv, in file order (largest market cap first)."""
    return list(_read_ticker_universe((directory or data_dir()).resolve()))


def load_historical_prices(directory: pathlib.Path | None = None) -> pd.DataFrame:
    """Adjusted close prices from historical_prices.csv, indexed by date and sorted.

    The file is large and immutable while the process runs, so it is read once
    per directory and shared; callers must treat the frame as read-only.
    """
    return _read_historical_prices((directory or data_dir()).resolve())


@functools.lru_cache(maxsize=4)
def _read_ticker_universe(directory: pathlib.Path) -> tuple[str, ...]:
    with open(directory / "ticker_universe.csv", newline="") as f:
        return tuple(row[0] for row in csv.reader(f) if row)


@functools.lru_cache(maxsize=4)
def _read_historical_prices(directory: pathlib.Path) -> pd.DataFrame:
    prices = pd.read_csv(directory / "historical_prices.csv", parse_dates=True, index_col="Date")
    return prices.sort_index()
