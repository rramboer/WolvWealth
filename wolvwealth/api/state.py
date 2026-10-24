"""Global state of application."""
import csv
import time
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf

from wolvwealth import config


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
        with open(config.TICKER_UNIVERSE_FILE, newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                self.TICKER_UNIVERSE.append(row[0])

    def save_ticker_universe(self) -> None:
        """Save Ticker Universe to CSV."""
        with open(config.TICKER_UNIVERSE_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            for ticker in self.TICKER_UNIVERSE:
                writer.writerow([ticker])

    def load_historical_prices(self) -> None:
        """Load historical prices of tickers in universe into DataFrame."""
        self.HISTORICAL_PRICES = pd.read_csv(config.HISTORICAL_PRICES_FILE, parse_dates=True, index_col="Date")

    def prices_age_days(self) -> int:
        """Return the age in days of the newest cached price row."""
        return (datetime.now() - self.HISTORICAL_PRICES.index[-1].to_pydatetime()).days

    # Yahoo rate-limits bulk downloads of the ~2,000-ticker universe, and a
    # rate-limited ticker comes back as an all-NaN column rather than an
    # exception. Download in chunks with pauses, then re-request the all-NaN
    # stragglers with backoff so delisted tickers are the only gaps left.
    _CHUNK_SIZE = 100
    _CHUNK_PAUSE_SECONDS = 2
    _RETRY_PASSES = 4
    _RETRY_BACKOFF_SECONDS = 30

    @staticmethod
    def _download_chunk(tickers: list, start_date: str, end_date: str) -> pd.DataFrame:
        """Download adjusted closes for one batch of tickers."""
        # auto_adjust=True makes "Close" the split/dividend-adjusted close
        # (newer yfinance dropped the separate "Adj Close" column here).
        data = yf.download(tickers, start=start_date, end=end_date, auto_adjust=True, progress=False)["Close"]
        if isinstance(data, pd.Series):  # single-ticker shape
            data = data.to_frame(name=tickers[0])
        return data.round(2)

    def _download_prices(self, start_date: str) -> pd.DataFrame:
        """Download adjusted closes for the whole universe from start_date to today."""
        end_date = datetime.now().strftime("%Y-%m-%d")
        frames = []
        for i in range(0, len(self.TICKER_UNIVERSE), self._CHUNK_SIZE):
            if i:
                time.sleep(self._CHUNK_PAUSE_SECONDS)
            frames.append(self._download_chunk(self.TICKER_UNIVERSE[i:i + self._CHUNK_SIZE], start_date, end_date))
        data = pd.concat(frames, axis=1).reindex(columns=self.TICKER_UNIVERSE)

        # Retry tickers that came back entirely empty (usually rate limiting,
        # occasionally a delisting). Whatever is still all-NaN after the
        # retries is treated as genuinely gone.
        for attempt in range(self._RETRY_PASSES):
            missing = [t for t in self.TICKER_UNIVERSE if data[t].isna().all()]
            if not missing:
                break
            time.sleep(self._RETRY_BACKOFF_SECONDS * (attempt + 1))
            for i in range(0, len(missing), self._CHUNK_SIZE):
                chunk = missing[i:i + self._CHUNK_SIZE]
                retried = self._download_chunk(chunk, start_date, end_date)
                for ticker in retried.columns.intersection(chunk):
                    data[ticker] = retried[ticker]
                time.sleep(self._CHUNK_PAUSE_SECONDS)
        return data

    def save_historical_prices(self) -> None:
        """Saves historical stock prices of tickers in universe to CSV."""
        historical_data = self._download_prices("2006-01-01")
        historical_data.to_csv(config.HISTORICAL_PRICES_FILE, index=True)

    def update_historical_prices(self) -> bool:
        """Append missing recent prices to the CSV. Return True if anything changed."""
        start_date = (self.HISTORICAL_PRICES.index[-1].to_pydatetime() + timedelta(days=1)).strftime("%Y-%m-%d")
        end_date = datetime.now().strftime("%Y-%m-%d")
        if start_date >= end_date:
            return False
        historical_data = self._download_prices(start_date)
        if historical_data.empty:
            return False

        # Refuse to persist a clearly rate-limited result: tickers that had a
        # price in the last cached row should not suddenly all disappear.
        previously_live = self.HISTORICAL_PRICES.iloc[-1].dropna().index
        went_dark = [t for t in previously_live if t in historical_data and historical_data[t].isna().all()]
        if len(went_dark) > 0.15 * len(previously_live):
            raise RuntimeError(
                f"{len(went_dark)} of {len(previously_live)} live tickers returned no data "
                "(Yahoo rate limiting?); not overwriting the price cache. Try again later."
            )

        self.HISTORICAL_PRICES = pd.concat([self.HISTORICAL_PRICES, historical_data])
        self.HISTORICAL_PRICES.to_csv(config.HISTORICAL_PRICES_FILE, index=True)
        return True

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
