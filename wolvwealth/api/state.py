import pandas as pd  # type: ignore
import csv
import yfinance as yf  # type: ignore
from datetime import datetime


class ApplicationState:
    """GLOBAL VARIABLES."""

    _instance = None

    def __new__(cls):
        """Singleton Pattern."""
        if cls._instance is None:
            cls._instance = super(ApplicationState, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize ONLY ON FIRST INSTANTIATION."""
        if not hasattr(self, "initialized"):
            self.initialized = True
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

    def load_historical_prices(self) -> None:
        """Load historical prices of tickers in universe into DataFrame."""
        self.HISTORICAL_PRICES = pd.read_csv(
            "historical_prices.csv", parse_dates=True, index_col="Date"
        )

    def add_ticker_to_universe(self, ticker: str) -> None:
        """Add ticker to universe."""
        self.TICKER_UNIVERSE.append(ticker)
        with open("ticker_universe.csv", "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([ticker])
        self.save_historical_prices()
        self.load_historical_prices()

    def fetch_ticker_price(self, ticker: str) -> float:
        """Return current stock price of ticker."""
        return self.HISTORICAL_PRICES[ticker].iloc[-1]

    def save_historical_prices(self) -> None:
        """
        Saves historical stock prices of tickers in universe starting Jan 1, 2010.
        THIS SHOULD ONLY BE RUN ONCE AFTER UNIVERSE CHANGE!
        """
        start_date = "2010-01-01"
        end_date = datetime.now()
        historical_data = yf.download(
            self.TICKER_UNIVERSE, start=start_date, end=end_date
        )["Adj Close"].round(2)
        historical_data.to_csv("historical_prices.csv", index=True)

    def update_historical_prices(self) -> None:
        """Update historical stock prices of tickers in universe."""
        start_date = (
            datetime.strptime(self.HISTORICAL_PRICES.index[-1], "%Y-%m-%d")
            + datetime.timedelta(days=1)
        ).strftime("%Y-%m-%d")
        end_date = datetime.now().strftime("%Y-%m-%d")
        historical_data = yf.download(
            self.TICKER_UNIVERSE, start=start_date, end=end_date
        )["Adj Close"].round(2)
        self.HISTORICAL_PRICES.append(historical_data, inplace=True)
        self.HISTORICAL_PRICES.to_csv("historical_prices.csv", index=True)
