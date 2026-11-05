"""MarketDataService: the process-wide price snapshot and how it gets refreshed."""

import threading
from datetime import timedelta

import pandas as pd

from wolvwealth.domain.market import UNIVERSE_ALIASES, MarketData
from wolvwealth.domain.stats import ESTIMATOR, MarketStats
from wolvwealth.market import yahoo
from wolvwealth.storage.price_store import PriceStore

#: Marks the T-bill reference as not yet read from disk (None means read, and absent).
_UNLOADED = object()


def status(data: MarketData, tbill, max_age_days: int) -> dict:
    """Describe the price snapshot's freshness: the report the API and the MCP server both publish.

    ``stale`` is the same judgement the startup refresh makes, so a reader
    can act on it without knowing the threshold.
    """
    age = data.age_days()
    report = {
        "as_of": str(data.as_of),
        "age_days": age,
        "stale": age > max_age_days,
        "universe_size": len(data.universe),
        "tradeable_size": len(data.tradeable),
        "universe_aliases": sorted(UNIVERSE_ALIASES),
        # A reference for choosing a portfolio's risk_free_rate; null until
        # the first refresh has fetched it. Never applied for you.
        "tbill_rate": None if tbill is None else tbill["rate"],
        "tbill_as_of": None if tbill is None else tbill["as_of"],
    }
    if report["stale"]:
        report["refresh_hint"] = (
            f"Prices are {age} days old. Run `wolvwealth refresh-data` in a terminal for current ones; "
            "the web app also refreshes in the background when started."
        )
    return report


def classify_tickers(data: MarketData, tickers) -> dict:
    """Sort symbols into tradeable, known-but-priceless, and unknown, so a caller can validate many at once."""
    report = {"tradeable": [], "no_current_price": [], "unknown": []}
    for raw in tickers:
        ticker = str(raw).upper()
        if data.has_price(ticker):
            report["tradeable"].append(ticker)
        elif ticker in data:
            report["no_current_price"].append(ticker)
        else:
            report["unknown"].append(ticker)
    return report


class MarketDataService:  # pylint: disable=too-many-instance-attributes
    """Lazily loads the price cache, creates it on first run, and refreshes it on request."""

    #: Refuse to persist an incremental refresh if more than this share of
    #: previously live tickers came back empty (almost always rate limiting).
    MAX_WENT_DARK_FRACTION = 0.15

    #: An incremental refresh re-downloads this many already-cached trading
    #: days. Yahoo adjusts the whole history retroactively after a split or
    #: dividend, so if the overlap no longer matches the cache, that ticker's
    #: cached history is on a stale adjustment basis and must be replaced.
    OVERLAP_ROWS = 5

    #: Relative disagreement on an overlapping day beyond which a ticker is
    #: considered re-adjusted. Unchanged tickers come back byte-identical
    #: (same source, same rounding), while a split or dividend shifts the
    #: whole history by its ratio, so the margin is not delicate.
    READJUSTED_TOLERANCE = 0.005

    def __init__(
        self,
        store: PriceStore,
        history_start: str,
        downloader=yahoo.download_prices,
        log=print,
        stats_universe_size: int = 500,
        tbill_downloader=None,
    ) -> None:
        """Wire the store and the download functions (injectable for tests)."""
        self.store = store
        self.history_start = history_start
        self.downloader = downloader
        #: None means ``yahoo.download_tbill_rate``, looked up at call time
        #: so a test can replace the module function and never reach Yahoo.
        self.tbill_downloader = tbill_downloader
        self.log = log
        self._tbill = _UNLOADED
        self.stats_universe_size = stats_universe_size
        self._data = None
        #: ``PriceStore.prices_stamp()`` of the file the snapshot was loaded
        #: from (or that this process last wrote), so ``reload_if_changed``
        #: can tell another process's refresh from its own.
        self._loaded_stamp = None
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

    def loaded(self) -> bool:
        """Return true once a snapshot is in memory (reading ``data`` before then loads or builds the cache)."""
        return self._data is not None

    def cache_exists(self) -> bool:
        """Return true if reading ``data`` would load from disk rather than download the whole history."""
        return self.store.has_prices() or any(path.exists() for path in self.store.legacy_csv_paths)

    def reload_if_changed(self) -> bool:
        """Reload the snapshot if another process replaced the price cache since this one loaded it.

        A web server or MCP server can run for days while ``refresh-data``
        (or the other server) rewrites the cache underneath it. Comparing
        the file's stamp is one ``stat`` call, cheap enough for every
        request, and a mismatch means the prices in memory are no longer
        the prices on disk. Returns whether a reload happened.
        """
        if self._data is None or self.store.prices_stamp() == self._loaded_stamp:
            return False
        self.log("wolvwealth: the price cache changed on disk; reloading it")
        self._data = self._load()
        self._tbill = _UNLOADED
        return True

    @property
    def tbill(self):
        """The cached 13-week T-bill yield as ``{"rate", "as_of"}``, or None if never fetched.

        A reference for users choosing a risk-free rate, surfaced in the UI
        and the API; the optimizer never reads it on its own.
        """
        if self._tbill is _UNLOADED:
            self._tbill = self.store.load_tbill()
        return self._tbill

    def set_tbill(self, rate: float, as_of: str) -> None:
        """Replace the cached T-bill yield in memory (tests). Does not write to disk."""
        self._tbill = {"rate": rate, "as_of": as_of}

    def refresh_tbill(self) -> bool:
        """Fetch the latest T-bill yield and cache it. Never raises; returns whether it succeeded."""
        downloader = self.tbill_downloader or yahoo.download_tbill_rate
        try:
            rate, as_of = downloader()
        except Exception as err:  # pylint: disable=broad-except
            self.log(f"wolvwealth: could not fetch the T-bill rate ({err}); the stored one, if any, stands")
            return False
        self.store.save_tbill(rate, as_of)
        self.set_tbill(rate, as_of)
        return True

    def _load(self) -> MarketData:
        universe = self.store.load_universe()
        if not self.store.has_prices():
            if self.store.migrate_legacy_prices():
                self.log(f"wolvwealth: migrated price history into {self.store.prices_path}")
            else:
                self.log("wolvwealth: no price history cache, downloading 2006 -> today (this takes a while)...")
                self.store.save_prices(self._download(universe, self.history_start))
        self._loaded_stamp = self.store.prices_stamp()
        prices = self.store.load_prices()
        return MarketData.build(universe, prices, self._load_or_compute_stats(universe, prices))

    def _save_prices(self, prices) -> None:
        """Write the cache and remember the result as this process's own, so it is not re-read as foreign."""
        self.store.save_prices(prices)
        self._loaded_stamp = self.store.prices_stamp()

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
        ~2,600 tickers take tens of minutes against ~1 minute for 500. Every
        universe alias tops out at ``top500``, so this covers them all plus
        the default universe; anything else falls back to computing its own
        statistics in ``MarketData.stats_for``.
        """
        return MarketData.build(universe, prices).top(self.stats_universe_size)

    @staticmethod
    def _stats_meta(prices, columns) -> dict:
        """Return what the statistics cache must match to be served against ``prices``.

        The estimator's name is part of it: a cache built under another
        estimator holds different expected returns for the same prices, and
        serving it would silently price every request the old way.
        """
        return {
            "as_of": str(prices.index[-1].date()),
            "price_rows": len(prices),
            "tickers": len(columns),
            "estimator": ESTIMATOR,
        }

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
                return MarketStats(mu, cov, meta["estimator"])
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

        ``full`` re-downloads the entire history. An incremental refresh
        repairs re-adjusted tickers on its own (see ``_readjusted_tickers``),
        so ``full`` is for recovering tickers that were only throttled.
        """
        self.refresh_tbill()
        if full:
            prices = self._download(self.data.universe, self.history_start)
            self._save_prices(prices)
            universe = self.data.universe
            columns = self._stats_columns(universe, prices)
            self._data = MarketData.build(universe, prices, self._compute_and_save_stats(prices, columns))
            return True
        current = self.data
        last = current.prices.index[-1]
        today = pd.Timestamp.now().strftime("%Y-%m-%d")
        if (last + timedelta(days=1)).strftime("%Y-%m-%d") >= today:
            return False
        overlap_start = current.prices.index[-self.OVERLAP_ROWS :][0]
        fresh = self.downloader(list(current.universe), overlap_start.strftime("%Y-%m-%d"))
        if fresh.empty:
            return False
        previously_live = current.prices.iloc[-1].dropna().index
        went_dark = [t for t in previously_live if t in fresh and fresh[t].isna().all()]
        if len(went_dark) > self.MAX_WENT_DARK_FRACTION * len(previously_live):
            raise RuntimeError(
                f"{len(went_dark)} of {len(previously_live)} live tickers returned no data "
                "(Yahoo rate limiting?); not overwriting the price cache. Try again later."
            )
        new_rows = fresh[fresh.index > last]
        readjusted = self._readjusted_tickers(current.prices, fresh)
        if new_rows.empty and not readjusted:
            return False
        prices = pd.concat([current.prices, new_rows.reindex(columns=current.prices.columns)])
        if readjusted:
            prices = self._repair_readjusted(prices, readjusted)
        self._save_prices(prices)
        columns = self._stats_columns(current.universe, prices)
        self._data = MarketData.build(current.universe, prices, self._compute_and_save_stats(prices, columns))
        return True

    def _readjusted_tickers(self, cached: pd.DataFrame, fresh: pd.DataFrame) -> list:
        """Tickers whose freshly served prices contradict the cache on overlapping days.

        Yahoo's adjusted closes are relative to the download date: a split or
        dividend rescales the entire served history. Appending new rows onto
        a cache on the old basis fakes a one-day crash (a 10:1 split reads as
        -90%), which wrecks every statistic downstream. A disagreement on a
        day both sides have data for is exactly that basis change.
        """
        shared = cached.index.intersection(fresh.index)
        columns = cached.columns.intersection(fresh.columns)
        if shared.empty or columns.empty:
            return []
        old, new = cached.loc[shared, columns], fresh.loc[shared, columns]
        # NaN on either side (throttled day) compares as False: never a mismatch.
        mismatched = ((new - old).abs() / old.abs() > self.READJUSTED_TOLERANCE).any()
        return list(columns[mismatched])

    def _repair_readjusted(self, prices: pd.DataFrame, tickers: list) -> pd.DataFrame:
        """Replace ``tickers``' full history with a fresh download on today's adjustment basis.

        A handful of tickers split or go ex-dividend on any given day, so
        this is one small request. A ticker the repair download misses
        (throttled) is blanked rather than kept: a history that mixes two
        adjustment bases poisons the optimizer, while an empty column is
        merely excluded until the next refresh picks it up.
        """
        self.log(
            f"wolvwealth: {len(tickers)} ticker(s) changed adjustment basis (split or dividend); "
            f"re-downloading their full history: {', '.join(tickers)}"
        )
        repaired = self.downloader(list(tickers), self.history_start)
        prices = prices.copy()
        for ticker in tickers:
            if ticker in repaired.columns and not repaired[ticker].isna().all():
                prices[ticker] = repaired[ticker].reindex(prices.index)
            else:
                prices[ticker] = float("nan")
                self.log(
                    f"wolvwealth: could not re-download {ticker} after its split/dividend; "
                    "its history is cleared until a later refresh recovers it."
                )
        return prices

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
        if not age and self.tbill is not None:
            return None
        # Not a `with`: the lock is acquired here and released on the worker
        # thread when the refresh finishes, so the scopes cannot coincide.
        if not self._refresh_lock.acquire(blocking=False):  # pylint: disable=consider-using-with
            return None
        if age:
            self.log(
                f"wolvwealth: cached prices are {age} days old, refreshing in the background "
                "(set WOLVWEALTH_NO_AUTO_REFRESH=1 to skip); serving cached prices meanwhile..."
            )
        else:
            self.log("wolvwealth: fetching the current T-bill rate in the background...")

        def run():
            try:
                if age:
                    self._refresh_quietly()
                else:
                    # Prices are fresh; only the T-bill reference is missing
                    # (an install from before it existed). One tiny download.
                    self.refresh_tbill()
            finally:
                self._refresh_lock.release()

        thread = threading.Thread(target=run, name="wolvwealth-refresh", daemon=True)
        thread.start()
        return thread
