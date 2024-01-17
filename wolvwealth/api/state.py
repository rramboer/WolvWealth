"""Global state of application."""
import csv
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf


class ApplicationState:
    """GLOBAL VARIABLES."""

    _instance = None

    def __new__(cls):
        """Global state. Singleton."""
        if cls._instance is None:
            cls._instance = super(ApplicationState, cls).__new__(cls)
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
        with open("ticker_universe.csv", newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                self.TICKER_UNIVERSE.append(row[0])

    def save_ticker_universe(self) -> None:
        """Save Ticker Universe to CSV."""
        with open("ticker_universe.csv", "w", newline="") as f:
            writer = csv.writer(f)
            for ticker in self.TICKER_UNIVERSE:
                writer.writerow([ticker])

    def load_historical_prices(self) -> None:
        """Load historical prices of tickers in universe into DataFrame."""
        self.HISTORICAL_PRICES = pd.read_csv("historical_prices.csv", parse_dates=True, index_col="Date")

    def save_historical_prices(self) -> None:
        """Saves historical stock prices of tickers in universe to CSV."""
        start_date = "2006-01-01"
        end_date = datetime.now().strftime("%Y-%m-%d")
        historical_data = yf.download(self.TICKER_UNIVERSE, start=start_date, end=end_date)["Adj Close"].round(2)
        historical_data.to_csv("historical_prices.csv", index=True)

    def add_tickers_to_universe(self, tickers: list):
        """Add tickers to universe and update historical prices."""
        for ticker in tickers:
            if ticker in self.TICKER_UNIVERSE:
                continue
            # Binary search through ticker universe, list is sorted by market cap descending
            low = 0
            high = len(self.TICKER_UNIVERSE) - 1
            mid = 0
            while low < high:
                mid = (low + high) // 2
                market_cap = self.get_market_cap(ticker)
                market_cap_mid = self.get_market_cap(self.TICKER_UNIVERSE[mid])
                if market_cap < market_cap_mid:
                    low = mid + 1
                elif market_cap > market_cap_mid:
                    high = mid - 1
                else:
                    break
            self.TICKER_UNIVERSE.insert(mid, ticker)
        self.save_ticker_universe()
        self.save_historical_prices()
        self.load_historical_prices()

    def remove_tickers_from_universe(self, tickers: list) -> None:
        """Remove tickers from universe and update historical prices."""
        for ticker in tickers:
            if ticker not in self.TICKER_UNIVERSE:
                continue
            self.TICKER_UNIVERSE.remove(ticker)
        self.save_ticker_universe()
        self.save_historical_prices()
        self.load_historical_prices()

    def fetch_ticker_price(self, ticker: str) -> float:
        """Return current stock price of ticker."""
        return self.HISTORICAL_PRICES[ticker].iloc[-1]

    def update_historical_prices(self) -> None:
        """Update historical stock prices of tickers in universe."""
        start_date = (
            datetime.strptime(str(self.HISTORICAL_PRICES.index[-1]), "%Y-%m-%d %H:%M:%S") + timedelta(days=1)
        ).strftime("%Y-%m-%d")
        end_date = datetime.now().strftime("%Y-%m-%d")
        if start_date >= end_date:
            return
        historical_data = yf.download(self.TICKER_UNIVERSE, start=start_date, end=end_date)["Adj Close"].round(2)
        self.HISTORICAL_PRICES = pd.concat([self.HISTORICAL_PRICES, historical_data])
        self.HISTORICAL_PRICES.to_csv("historical_prices.csv", index=True)

    def get_market_cap(self, ticker: str) -> float:
        """Return market cap of ticker."""
        try:
            return yf.Ticker(ticker).info["marketCap"]  # Key for stocks
        except KeyError:
            return yf.Ticker(ticker).info["totalAssets"]  # Key for ETFs

    def sort_ticker_universe(self) -> None:
        """Sort ticker universe by market cap. Descending."""
        self.TICKER_UNIVERSE = [
            x[0]
            for x in sorted(
                [(ticker, self.get_market_cap(ticker)) for ticker in self.TICKER_UNIVERSE],
                key=lambda x: x[1],
                reverse=True,
            )
        ]
        self.save_ticker_universe()
