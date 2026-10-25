"""MarketDataService: the process-wide price snapshot and how it gets refreshed."""
from datetime import timedelta

import pandas as pd

from wolvwealth.domain.market import MarketData
from wolvwealth.market import yahoo
from wolvwealth.storage.price_store import PriceStore


class MarketDataService:
    """Lazily loads the price cache, creates it on first run, and refreshes it on request."""

    #: Refuse to persist an incremental refresh if more than this share of
    #: previously live tickers came back empty (almost always rate limiting).
    MAX_WENT_DARK_FRACTION = 0.15

    def __init__(self, store: PriceStore, history_start: str, downloader=yahoo.download_prices, log=print) -> None:
        """Wire the store and the download function (injectable for tests)."""
        self.store = store
        self.history_start = history_start
        self.downloader = downloader
        self.log = log
        self._data = None

    @property
    def data(self) -> MarketData:
        """The current snapshot, loading (or building) the cache on first access."""
        if self._data is None:
            self._data = self._load()
        return self._data

    def set_data(self, data: MarketData) -> None:
        """Replace the snapshot in memory (tests, backtests). Does not write to disk."""
        self._data = data

    def _load(self) -> MarketData:
        universe = self.store.load_universe()
        if not self.store.has_prices():
            if self.store.migrate_legacy_prices():
                self.log(f"wolvwealth: migrated price history into {self.store.prices_path}")
            else:
                self.log("wolvwealth: no price history cache, downloading 2006 -> today (this takes a while)...")
                self.store.save_prices(self.downloader(universe, self.history_start))
        return MarketData.build(universe, self.store.load_prices())

    def age_days(self) -> int:
        """Days since the newest cached price row."""
        return self.data.age_days()

    def refresh(self, full: bool = False) -> bool:
        """Bring the cache up to today. Return True if anything changed.

        ``full`` re-downloads the entire history, which is the only way to
        pick up corrected adjusted closes after splits and dividends.
        """
        if full:
            prices = self.downloader(list(self.data.universe), self.history_start)
            self.store.save_prices(prices)
            self._data = MarketData.build(self.data.universe, prices)
            return True
        current = self.data
        start = (current.prices.index[-1] + timedelta(days=1)).strftime("%Y-%m-%d")
        today = pd.Timestamp.now().strftime("%Y-%m-%d")
        if start >= today:
            return False
        fresh = self.downloader(list(current.universe), start)
        if fresh.empty:
            return False
        previously_live = current.prices.iloc[-1].dropna().index
        went_dark = [t for t in previously_live if t in fresh and fresh[t].isna().all()]
        if len(went_dark) > self.MAX_WENT_DARK_FRACTION * len(previously_live):
            raise RuntimeError(
                f"{len(went_dark)} of {len(previously_live)} live tickers returned no data "
                "(Yahoo rate limiting?); not overwriting the price cache. Try again later."
            )
        prices = pd.concat([current.prices, fresh])
        self.store.save_prices(prices)
        self._data = MarketData.build(current.universe, prices)
        return True

    def auto_refresh_if_stale(self, max_age_days: int) -> bool:
        """Incremental refresh when the cache is older than ``max_age_days``. Never raises."""
        age = self.age_days()
        if age <= max_age_days:
            return False
        self.log(
            f"wolvwealth: cached prices are {age} days old, refreshing (set WOLVWEALTH_NO_AUTO_REFRESH=1 to skip)..."
        )
        try:
            self.refresh()
        except Exception as err:  # pylint: disable=broad-except  # never make startup fail over market data
            self.log(f"wolvwealth: price refresh failed ({err}); continuing with cached prices")
            return False
        self.log(f"wolvwealth: prices now end {self.data.as_of}")
        return True
