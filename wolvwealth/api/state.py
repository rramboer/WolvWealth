"""Global state of application."""

import pandas as pd

from wolvwealth.market_data import data_dir, load_historical_prices, load_ticker_universe

__all__ = ["ApplicationState", "data_dir"]


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
            self.TICKER_UNIVERSE: list[str] = load_ticker_universe()
            self.HISTORICAL_PRICES: pd.DataFrame = load_historical_prices()

    def fetch_ticker_price(self, ticker: str) -> float:
        """Return most recent known stock price of ticker."""
        return self.HISTORICAL_PRICES[ticker].iloc[-1]
