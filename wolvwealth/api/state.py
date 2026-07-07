"""Global state of application."""

import csv
import os
import pathlib

import pandas as pd

DEFAULT_DATA_DIR = pathlib.Path(__file__).resolve().parents[2]


def data_dir() -> pathlib.Path:
    """Directory containing ticker_universe.csv and historical_prices.csv."""
    return pathlib.Path(os.environ.get("WOLVWEALTH_DATA_DIR", DEFAULT_DATA_DIR))


class ApplicationState:
    """Singleton holding the ticker universe and historical price data."""

    _instance = None

    def __new__(cls):
        """Global state. Singleton."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """Initialize ONLY ON FIRST INSTANTIATION."""
        if not hasattr(self, "initialized"):
            self.initialized: bool = True
            self.HISTORICAL_PRICES: pd.DataFrame = pd.DataFrame()
            self.TICKER_UNIVERSE: list = []
            self.load_ticker_universe()
            self.load_historical_prices()

    def load_ticker_universe(self) -> None:
        """Load Ticker Universe from CSV."""
        with open(data_dir() / "ticker_universe.csv", newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                self.TICKER_UNIVERSE.append(row[0])

    def load_historical_prices(self) -> None:
        """Load historical prices of tickers in universe into DataFrame."""
        self.HISTORICAL_PRICES = pd.read_csv(data_dir() / "historical_prices.csv", parse_dates=True, index_col="Date")

    def fetch_ticker_price(self, ticker: str) -> float:
        """Return most recent known stock price of ticker."""
        return self.HISTORICAL_PRICES[ticker].iloc[-1]
