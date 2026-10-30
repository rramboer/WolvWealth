"""MarketDataService: the process-wide price snapshot and how it gets refreshed."""
import threading
from datetime import timedelta

import pandas as pd

from wolvwealth.domain.market import MarketData
from wolvwealth.domain.stats import MarketStats
from wolvwealth.market import yahoo
from wolvwealth.storage.price_store import PriceStore


class MarketDataService:
    """Lazily loads the price cache, creates it on first run, and refreshes it on request."""

    #: Refuse to persist an incremental refresh if more than this share of
    #: previously live tickers came back empty (almost always rate limiting).
    MAX_WENT_DARK_FRACTION = 0.15

    def __init__(
        self,
        store: PriceStore,
        history_start: str,
        downloader=yahoo.download_prices,
        log=print,
        stats_universe_size: int = 500,
    ) -> None:
        """Wire the store and the download function (injectable for tests)."""
        self.store = store
        self.history_start = history_start
        self.downloader = downloader
        self.log = log
        self.stats_universe_size = stats_universe_size
        self._data = None
        #: Held for the duration of a background refresh so a second trigger
        #: does not start a duplicate download.
        self._refresh_lock = threading.Lock()

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
                self.store.save_prices(self._download(universe, self.history_start))
        prices = self.store.load_prices()
        return MarketData.build(universe, prices, self._load_or_compute_stats(universe, prices))

    def _download(self, tickers, start_date):
        """Download prices and say how much of the universe came back empty.

        A full download routinely loses a few hundred tickers to throttling,
        and an empty column is indistinguishable from a delisting. Silence
        here would mean quietly optimizing over a fraction of the universe
        the user believes they have, so the shortfall is always reported.
        """
        prices = self.downloader(list(tickers), start_date)
        empty = yahoo.empty_tickers(prices, tickers)
        if empty:
            self.log(
                f"wolvwealth: {len(empty)} of {len(list(tickers))} tickers returned no data "
                "(delisted, or Yahoo throttled us). They are excluded from allocations; "
                "run `wolvwealth refresh-data --full` later to pick up any that were only throttled."
            )
        return prices

    def _stats_columns(self, universe, prices) -> list:
        """Tickers the precomputed statistics cover: the largest tradeable names.

        Covering the whole ticker list would be quadratic work for names no
        default request touches -- ``exp_cov`` loops over every pair, so all
        1982 tickers take ~17 minutes against ~1 minute for 500. Every
        universe alias tops out at ``top500``, so this covers them all plus
        the default universe; anything else falls back to computing its own
        statistics in ``MarketData.stats_for``.
        """
        return MarketData.build(universe, prices).top(self.stats_universe_size)

    @staticmethod
    def _stats_meta(prices, columns) -> dict:
        """Return what the statistics cache must match to be served against ``prices``."""
        return {"as_of": str(prices.index[-1].date()), "price_rows": len(prices), "tickers": len(columns)}

    def _load_or_compute_stats(self, universe, prices) -> MarketStats:
        """Return the cached statistics if they were computed from ``prices``; rebuild them otherwise."""
        columns = self._stats_columns(universe, prices)
        try:
            cached = self.store.load_stats()
        except Exception:  # pylint: disable=broad-except  # a corrupt cache is never fatal; rebuild below
            cached = None
        if cached is not None:
            meta, mu, cov = cached
            if meta == self._stats_meta(prices, columns) and list(cov.columns) == columns:
                return MarketStats(mu, cov)
        return self._compute_and_save_stats(prices, columns)

    def _compute_and_save_stats(self, prices, columns) -> MarketStats:
        """Precompute the optimizer's statistics over ``columns`` and persist them."""
        self.log(
            f"wolvwealth: precomputing return/covariance statistics for {len(columns)} tickers "
            "(happens once per data refresh)..."
        )
        stats = MarketStats.compute(prices[columns])
        self.store.save_stats(stats.mu, stats.cov, self._stats_meta(prices, columns))
        return stats

    def age_days(self) -> int:
        """Days since the newest cached price row."""
        return self.data.age_days()

    def refresh(self, full: bool = False) -> bool:
        """Bring the cache up to today. Return True if anything changed.

        ``full`` re-downloads the entire history, which is the only way to
        pick up corrected adjusted closes after splits and dividends.
        """
        if full:
            prices = self._download(self.data.universe, self.history_start)
            self.store.save_prices(prices)
            universe = self.data.universe
            columns = self._stats_columns(universe, prices)
            self._data = MarketData.build(universe, prices, self._compute_and_save_stats(prices, columns))
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
        columns = self._stats_columns(current.universe, prices)
        self._data = MarketData.build(current.universe, prices, self._compute_and_save_stats(prices, columns))
        return True

    def _stale_by(self, max_age_days: int) -> int:
        """Return the cache's age in days if it is past ``max_age_days``, else 0."""
        age = self.age_days()
        return age if age > max_age_days else 0

    def _refresh_quietly(self) -> bool:
        """Refresh, reporting the outcome. Never raises: market data must not break startup."""
        try:
            self.refresh()
        except Exception as err:  # pylint: disable=broad-except
            self.log(f"wolvwealth: price refresh failed ({err}); continuing with cached prices")
            return False
        self.log(f"wolvwealth: prices now end {self.data.as_of}")
        return True

    def auto_refresh_if_stale(self, max_age_days: int) -> bool:
        """Incremental refresh when the cache is older than ``max_age_days``. Never raises."""
        age = self._stale_by(max_age_days)
        if not age:
            return False
        self.log(
            f"wolvwealth: cached prices are {age} days old, refreshing (set WOLVWEALTH_NO_AUTO_REFRESH=1 to skip)..."
        )
        return self._refresh_quietly()

    def start_background_refresh(self, max_age_days: int):
        """Refresh in a daemon thread if the cache is stale; return the thread, or None.

        A stale refresh downloads months of prices and then recomputes the
        covariance, which together ran into minutes of dead air before the
        server would accept its first request. Callers serve the snapshot
        already loaded and the thread swaps in a newer one when it is ready:
        rebinding ``self._data`` is a single assignment, so a request holds
        either the old snapshot or the new one, never a half-built mix.

        Returns None when the cache is fresh or a refresh is already running.
        The thread is a daemon so Ctrl-C never waits on a download.
        """
        age = self._stale_by(max_age_days)
        if not age:
            return None
        if not self._refresh_lock.acquire(blocking=False):
            return None
        self.log(
            f"wolvwealth: cached prices are {age} days old, refreshing in the background "
            "(set WOLVWEALTH_NO_AUTO_REFRESH=1 to skip); serving cached prices meanwhile..."
        )

        def run():
            try:
                self._refresh_quietly()
            finally:
                self._refresh_lock.release()

        thread = threading.Thread(target=run, name="wolvwealth-refresh", daemon=True)
        thread.start()
        return thread
