"""MarketDataService: the process-wide price snapshot and how it gets refreshed.

Two frames, deliberately distinct. The *archive* is the Parquet file: every
close ever downloaded, and prices are only ever added to it or corrected in
place -- a refresh never shortens a history, and never deletes one. The
*snapshot* is what the optimizer reads: the archive with excluded tickers
blanked (see ``_exclude``).

Keeping them apart is what makes a bad refresh recoverable. Exclusions are
judgements about data that may be wrong, or that a later download will cure
-- a throttled split repair, a corrupt close. Writing those judgements into
the file would destroy the evidence they were made from: the next refresh
would have nothing to compare a download against, nothing to fall back to,
and a ticker listed for decades whose repair was throttled would be gone for
good. So exclusions live in ``quarantine.json`` and are re-applied to the
archive on every load.
"""

import threading
from datetime import timedelta

import pandas as pd

from wolvwealth.domain.errors import DataError
from wolvwealth.domain.market import MIN_HISTORY_DAYS, UNIVERSE_ALIASES, MarketData, clean_prices, implausible_moves
from wolvwealth.domain.numbers import TBILL_RATE_RANGE, is_plausible_tbill_rate
from wolvwealth.domain.stats import ESTIMATOR, MarketStats
from wolvwealth.market import yahoo
from wolvwealth.storage.price_store import PriceStore

#: A T-bill quote older than this is not the "current" yield it would be presented as.
TBILL_MAX_AGE_DAYS = 30

#: Opening words of the quarantine reason recorded when a split/dividend
#: re-download failed or came back truncated. Such a ticker is excluded while
#: its cached history sits on a superseded adjustment basis, so a later
#: refresh that does replace the history lifts exactly these entries --
#: never one recorded for implausible prices, which a new basis does not cure.
BASIS_FAILURE_REASON = "changed adjustment basis"

#: Marks the T-bill reference as not yet read from disk (None means read, and absent).
_UNLOADED = object()


def status(data: MarketData, tbill, max_age_days: int, quarantine: dict = None, readjustments: dict = None) -> dict:
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
        # Days of prices a ticker needs before a universe may include it,
        # unless the request lowers it. Stated so a reader can tell why a
        # name in the tradeable list is still absent from every allocation.
        "min_history_days": MIN_HISTORY_DAYS,
        # A reference for choosing a portfolio's risk_free_rate; null until
        # the first refresh has fetched it. Never applied for you.
        "tbill_rate": None if tbill is None else tbill["rate"],
        "tbill_as_of": None if tbill is None else tbill["as_of"],
    }
    if quarantine:
        # Unattended runs (agents, schedules) read this report, not the logs:
        # an excluded name must be visible where the decision is made.
        report["quarantined"] = quarantine
    if readjustments:
        # Same audience: a split or dividend makes a refresh re-download that
        # ticker's entire history, and a scheduled run must be able to see
        # that its numbers changed basis (or that the re-download failed and
        # the name is quarantined) without reading logs. Dated notes, not
        # state: current exclusions are always under "quarantined".
        report["readjusted"] = readjustments
    short = data.short_history()
    if short:
        report["short_history"] = short
    if report["stale"]:
        report["refresh_hint"] = (
            f"Prices are {age} days old. Run `wolvwealth refresh-data` in a terminal for current ones; "
            "the web app also refreshes in the background when started."
        )
    return report


def service_status(service: "MarketDataService", data: MarketData, max_age_days: int) -> dict:
    """``status()`` over the service's own stored records: the one composition every surface publishes."""
    return status(data, service.tbill, max_age_days, service.quarantine, service.readjustments)


def classify_tickers(data: MarketData, tickers) -> dict:
    """Sort symbols into tradeable, short-history, known-but-priceless, and unknown, to validate many at once.

    A short-history name can be held and valued, but a universe refuses it
    unless the request lowers ``min_history_days``; it goes in its own
    bucket so the caller learns that before building the request.
    """
    report = {"tradeable": [], "short_history": [], "no_current_price": [], "unknown": []}
    for raw in tickers:
        ticker = str(raw).upper()
        if data.has_price(ticker) and data.history_days[ticker] < MIN_HISTORY_DAYS:
            report["short_history"].append(ticker)
        elif data.has_price(ticker):
            report["tradeable"].append(ticker)
        elif ticker in data:
            report["no_current_price"].append(ticker)
        else:
            report["unknown"].append(ticker)
    return report


def _check_tbill(rate, as_of) -> None:
    """Raise ValueError unless a downloaded T-bill quote is believable.

    Portfolios set to "tbill" use this number in every optimization, often
    unattended, so a tampered or garbled quote must be refused rather than
    stored: a plausible yield, dated, not in the future, and not stale.
    """
    if not is_plausible_tbill_rate(rate):
        low, high = TBILL_RATE_RANGE
        raise ValueError(f"implausible yield {rate!r} (expected a fraction between {low} and {high})")
    try:
        day = pd.Timestamp(as_of)
    except (ValueError, TypeError) as err:
        raise ValueError(f"undated quote ({as_of!r})") from err
    today = pd.Timestamp.now().normalize()
    if not today - timedelta(days=TBILL_MAX_AGE_DAYS) <= day <= today:
        raise ValueError(f"quote dated {as_of}, not within the last {TBILL_MAX_AGE_DAYS} days")


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

    #: A re-downloaded full history must cover at least this share of the
    #: trading days the cache already holds for that ticker, or it is judged
    #: truncated (Yahoo serves only the recent tail of a history while
    #: throttling) and the ticker is blanked instead of shortened. A company
    #: listed for decades otherwise comes back looking like a recent IPO, and
    #: every statistic estimated from the stub is wrong. Splits and dividends
    #: change prices, never the number of trading days, so the guard cannot
    #: reject a genuine re-adjustment.
    MIN_HISTORY_COVERAGE = 0.9

    #: How long a note about a re-downloaded (split/dividend) history stays in
    #: the market report. Long enough that a weekly scheduled run cannot miss
    #: it, short enough that the report stays about recent events.
    READJUSTMENT_NOTE_DAYS = 30

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
        #: Every close on disk, including tickers the snapshot excludes. The
        #: refresh merges into this, never into the blanked snapshot, so an
        #: exclusion can always be undone by a later download.
        self._archive = None
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
        # Keep the archive coherent with the snapshot: a caller that injects
        # prices and then refreshes must merge into those, not into the file.
        self._archive = data.prices

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
            _check_tbill(rate, as_of)
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
                downloaded = self._download(universe, self.history_start)
                if len(downloaded) >= 2:
                    self._record_quarantine(downloaded, clear=True)
                if len(downloaded) < 2:
                    raise DataError(
                        "The first price download returned no usable history (offline, or Yahoo is throttling). "
                        "Nothing was saved; run `wolvwealth refresh-data` again later."
                    )
                self.store.save_prices(downloaded)
        self._loaded_stamp = self.store.prices_stamp()
        self._archive = self.store.load_prices()
        served = self._exclude(self._archive, self.store.load_quarantine())
        return MarketData.build(universe, served, self._load_or_compute_stats(universe, served))

    def _exclude(self, prices: pd.DataFrame, record: dict) -> pd.DataFrame:
        """Return the snapshot view of the archive: quarantined tickers blanked, the archive itself untouched.

        Blanking happens here, on the way to the optimizer, and never on the
        way to disk. An excluded ticker therefore keeps every close it ever
        had, so lifting the exclusion is a matter of re-reading the file
        rather than re-downloading a history Yahoo may not serve again.
        """
        blanked = [ticker for ticker in record if ticker in prices.columns]
        if not blanked:
            return prices
        served = prices.copy()
        served[blanked] = float("nan")
        return served

    def _save_prices(self, prices) -> None:
        """Write the archive and remember the result as this process's own, so it is not re-read as foreign."""
        self.store.save_prices(prices)
        self._archive = prices
        self._loaded_stamp = self.store.prices_stamp()

    def _publish(self, universe, prices: pd.DataFrame, record: dict) -> None:
        """Persist the archive and swap in the snapshot built from it, with ``record``'s tickers excluded."""
        self._save_prices(prices)
        served = self._exclude(prices, record)
        columns = self._stats_columns(universe, served)
        self._data = MarketData.build(universe, served, self._compute_and_save_stats(served, columns))

    def _fetch(self, tickers, start_date) -> pd.DataFrame:
        """Download and clean: the one gate every price from the network passes through.

        Only the requested tickers are kept, nothing dated after today
        survives, and every close must be believable (see ``clean_prices``).
        Plausibility across days is checked once merged, by ``_quarantine``.
        """
        tickers = list(tickers)
        raw = self.downloader(tickers, start_date)
        if not isinstance(raw, pd.DataFrame):
            raise RuntimeError(f"the price source returned {type(raw).__name__}, not a table of prices")
        fresh = clean_prices(raw, minimum_rows=0, latest=pd.Timestamp.now().normalize())
        return fresh.reindex(columns=tickers) if not fresh.empty else fresh

    def _record_quarantine(self, prices, since=None, whole=(), clear=False, broken=None, recovered=()) -> dict:
        """Record every ticker that must be excluded from allocations and return the record. Writes no prices.

        ``since`` limits the implausibility check to rows from that date on
        (the cached history before it was checked when it arrived); ``whole``
        names tickers whose entire history was just replaced; ``broken`` maps
        tickers whose split/dividend re-download failed or came back
        truncated to their reasons; ``recovered`` names tickers whose history
        was successfully replaced on the new basis, lifting an earlier
        ``broken`` entry (and only such an entry: a new adjustment basis is
        no answer to implausible prices). Tickers already in quarantine stay
        excluded until a full refresh (``clear``) re-checks them, so a
        refresh cannot quietly bring a poisoned name back. If more than
        MAX_WENT_DARK_FRACTION of the live tickers look implausible at once,
        the download as a whole is not trusted and nothing is written.
        """
        window = prices if since is None else prices.loc[since:]
        found = implausible_moves(window)
        if whole:
            found.update(implausible_moves(prices[[t for t in whole if t in prices.columns]]))
        record = {} if clear else self.store.load_quarantine()
        newly = {ticker: reason for ticker, reason in found.items() if ticker not in record}
        live = prices.iloc[-1].dropna().index if not prices.empty else pd.Index([])
        if len(newly) > max(3, self.MAX_WENT_DARK_FRACTION * len(live)):
            raise RuntimeError(
                f"{len(newly)} tickers moved implausibly in the downloaded prices (corrupt or tampered data?); "
                "not overwriting the price cache. Try again later."
            )
        self._lift_cured_basis_failures(record, recovered)
        detected = pd.Timestamp.now().strftime("%Y-%m-%d")
        record.update({ticker: {"reason": reason, "detected": detected} for ticker, reason in (broken or {}).items()})
        record.update({ticker: {"reason": reason, "detected": detected} for ticker, reason in newly.items()})
        for ticker, reason in newly.items():
            self.log(
                f"wolvwealth: quarantined {ticker}: its downloaded prices {reason}. It is excluded from "
                "allocations until `wolvwealth refresh-data --full` downloads a believable history."
            )
        self.store.save_quarantine(record)
        return record

    def _lift_cured_basis_failures(self, record: dict, recovered) -> None:
        """Drop ``recovered`` tickers from ``record`` where they were excluded only for a failed re-basing.

        The cure is specific: their history now exists whole on the current
        adjustment basis, which is exactly what the exclusion was waiting
        for. An entry recorded for implausible prices is left alone -- a
        split says nothing about whether those prices were believable.
        """
        for ticker in recovered:
            entry = record.get(ticker)
            if entry and entry["reason"].startswith(BASIS_FAILURE_REASON):
                del record[ticker]
                self.log(
                    f"wolvwealth: {ticker}'s full history was re-downloaded on the new adjustment basis; "
                    "it is back in allocations."
                )

    @property
    def quarantine(self) -> dict:
        """Tickers excluded because their downloaded prices were implausible: ``{ticker: {reason, detected}}``."""
        return self.store.load_quarantine()

    def _download(self, tickers, start_date):
        """Download prices and say how much of the universe came back empty.

        A full download routinely loses a few hundred tickers to throttling,
        and an empty column is indistinguishable from a delisting. Silence
        here would mean quietly optimizing over a fraction of the universe
        the user believes they have, so the shortfall is always reported.
        """
        prices = self._fetch(tickers, start_date)
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

        Two lists are joined: the largest names with the default minimum
        history (the default universe and every alias) and the largest names
        regardless of history, so a request that lowers ``min_history_days``
        to admit a recent listing among the top 500 is served from the cache
        too. The second list is rarely more than a handful of extra columns.
        """
        data = MarketData.build(universe, prices)
        size = self.stats_universe_size
        return list(dict.fromkeys(list(data.tradeable[:size]) + data.top(size)))

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

        ``full`` re-downloads the entire history and merges it over the
        cache, so days are only ever added, never lost to throttling. An
        incremental refresh repairs re-adjusted tickers on its own (see
        ``_readjusted_tickers``), so ``full`` is for recovering tickers or
        days that were only throttled.
        """
        self.refresh_tbill()
        if full:
            return self._refresh_full()
        current = self.data
        last = current.prices.index[-1]
        today = pd.Timestamp.now().strftime("%Y-%m-%d")
        if (last + timedelta(days=1)).strftime("%Y-%m-%d") >= today:
            return False
        overlap_start = current.prices.index[-self.OVERLAP_ROWS :][0]
        fresh = self._fetch(current.universe, overlap_start.strftime("%Y-%m-%d"))
        if fresh.empty:
            return False
        previously_live = current.prices.iloc[-1].dropna().index
        self._refuse_mass_blackout(fresh, previously_live)
        new_rows = fresh[fresh.index > last]
        # Against the archive, so a ticker the snapshot excludes is still
        # checked for a split and can still be recovered by this refresh.
        archive = self._archive
        readjusted = self._readjusted_tickers(archive, fresh)
        if new_rows.empty and not readjusted:
            return False
        prices = pd.concat([archive, new_rows.reindex(columns=archive.columns)])
        broken = {}
        if readjusted:
            prices, broken = self._repair_readjusted(prices, readjusted, archive)
            self._refuse_mass_repair_failure(broken, len(previously_live))
            self._record_readjustments(readjusted, broken)
        repaired = [ticker for ticker in readjusted if ticker not in broken]
        record = self._record_quarantine(prices, since=last, whole=repaired, broken=broken, recovered=repaired)
        self._publish(current.universe, prices, record)
        return True

    def _refresh_full(self) -> bool:
        """Re-download the whole history and merge it over the cache: a full refresh may add days, never lose them.

        This program runs unattended on schedules, and Yahoo under throttling
        serves anything from nothing to only the recent tail of a history.
        Replacing the cache with whatever arrived would shorten histories
        silently (see ``_merge_full_download``), so the download is laid over
        the cache additively, and a download that lost too many live tickers
        outright is refused before anything is written.
        """
        universe = self.data.universe  # loads the cache, and with it the archive, if this is the first access
        cached = self._archive
        fresh = self._download(universe, self.history_start)
        previously_live = self.data.prices.iloc[-1].dropna().index
        came_back = fresh.iloc[-1].dropna().index if not fresh.empty else pd.Index([])
        lost = previously_live.difference(came_back)
        if len(lost) > self.MAX_WENT_DARK_FRACTION * len(previously_live):
            raise RuntimeError(
                f"{len(lost)} of {len(previously_live)} live tickers came back without a current price "
                "(Yahoo rate limiting?); not overwriting the price cache. Try again later."
            )
        prices, readjusted, broken = self._merge_full_download(cached, fresh)
        self._refuse_mass_repair_failure(broken, len(previously_live))
        if readjusted:
            self._record_readjustments(readjusted, broken)
        # After the throttling check, which must count what came back, not what was then judged implausible.
        record = self._record_quarantine(prices, clear=True, broken=broken)
        self._publish(universe, prices, record)
        return True

    def _merge_full_download(self, cached: pd.DataFrame, fresh: pd.DataFrame) -> tuple:
        """Lay a full re-download over the cache: fresh wins where both have a close, the cache fills the rest.

        A ticker the download misses entirely keeps its cached history and
        merely has no current price until a later refresh recovers it; one
        served only in part has the missing days filled from the cache. The
        exception is a ticker whose adjustment basis changed (split or
        dividend): its cached rows are on the old basis and must not survive,
        so it takes only the fresh history -- and if that came back truncated,
        its cached history is kept intact and the ticker is excluded instead,
        the same choice ``_repair_readjusted`` makes on the incremental path.
        Returns ``(merged, readjusted tickers, {excluded ticker: reason})``.
        """
        readjusted = self._readjusted_tickers(cached, fresh)
        if readjusted:
            self.log(
                f"wolvwealth: {len(readjusted)} ticker(s) changed adjustment basis (split or dividend); "
                f"their cached history is replaced by the download: {', '.join(readjusted)}"
            )
        merged = fresh.combine_first(cached.drop(columns=readjusted, errors="ignore"))
        # Universe (market-cap) column order; a ticker no longer in the universe is dropped, as before.
        merged = merged.reindex(columns=fresh.columns)
        broken = {}
        for ticker in readjusted:
            fresh_days, cached_days = int(fresh[ticker].count()), int(cached[ticker].count())
            if fresh_days < self.MIN_HISTORY_COVERAGE * cached_days:
                # Keep the old basis whole rather than persist a stub: the column stays
                # internally consistent, and the next refresh can still detect the split.
                merged[ticker] = cached[ticker].reindex(merged.index)
                broken[ticker] = self._basis_failure(ticker, fresh_days, cached_days)
        return merged.dropna(how="all"), readjusted, broken

    def _basis_failure(self, ticker: str, fresh_days: int, cached_days: int) -> str:
        """Log why a re-adjusted ticker is excluded rather than re-based, and return the reason to record."""
        reason = (
            f"{BASIS_FAILURE_REASON} (split or dividend) but only {fresh_days} of its {cached_days} cached "
            "trading days could be re-downloaded"
        )
        self.log(
            f"wolvwealth: {ticker} {reason} (throttled?); a history that short would pass it off as a recent "
            "listing, so its cached history is kept as it was and the ticker is excluded from allocations "
            "until a refresh re-downloads it whole."
        )
        return reason

    def _refuse_mass_blackout(self, fresh: pd.DataFrame, previously_live) -> None:
        """Raise if an implausible share of tickers that had a price yesterday returned nothing today."""
        went_dark = [t for t in previously_live if t in fresh and fresh[t].isna().all()]
        if len(went_dark) > self.MAX_WENT_DARK_FRACTION * len(previously_live):
            raise RuntimeError(
                f"{len(went_dark)} of {len(previously_live)} live tickers returned no data "
                "(Yahoo rate limiting?); not overwriting the price cache. Try again later."
            )

    def _refuse_mass_repair_failure(self, broken: dict, live: int) -> None:
        """Raise rather than exclude an implausible share of live tickers over failed split/dividend re-downloads.

        One or two names coming back empty or truncated is Yahoo throttling
        those names; dozens at once means the refresh download itself cannot
        be trusted, and persisting it would quarantine a swath of the
        tradeable universe in one unattended run.
        """
        if len(broken) > max(3, self.MAX_WENT_DARK_FRACTION * live):
            raise RuntimeError(
                f"{len(broken)} re-adjusted tickers' histories came back empty or truncated "
                "(Yahoo rate limiting?); not overwriting the price cache. Try again later."
            )

    def _record_readjustments(self, readjusted: list, broken: dict) -> None:
        """Note which tickers' whole histories were re-downloaded after a split/dividend, and how that went.

        The market report carries these notes for READJUSTMENT_NOTE_DAYS, so
        a user (or an agent on a schedule) who did not watch the refresh
        learns that every number in a ticker's history legitimately changed,
        or that the ticker vanished from the tradeable list because the
        re-download failed -- instead of discovering either by surprise.
        """
        today = pd.Timestamp.now().normalize()
        cutoff = today - timedelta(days=self.READJUSTMENT_NOTE_DAYS)

        def recent(note) -> bool:
            try:
                return pd.Timestamp(note["detected"]) >= cutoff
            except (ValueError, TypeError):
                return False  # hand-edited or from a buggy build: an undatable note cannot expire, so drop it

        record = {ticker: note for ticker, note in self.store.load_readjustments().items() if recent(note)}
        for ticker in readjusted:
            outcome = (
                "history could not be re-downloaded; cached history kept, ticker quarantined"
                if ticker in broken
                else "history replaced on the new adjustment basis"
            )
            record[ticker] = {"detected": today.strftime("%Y-%m-%d"), "outcome": outcome}
        self.store.save_readjustments(record)

    @property
    def readjustments(self) -> dict:
        """Recent split/dividend re-downloads: ``{ticker: {detected, outcome}}`` (see ``_record_readjustments``)."""
        return self.store.load_readjustments()

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

    def _repair_readjusted(self, prices: pd.DataFrame, tickers: list, cached: pd.DataFrame) -> tuple:
        """Replace ``tickers``' full history with a fresh download on today's adjustment basis.

        A handful of tickers split or go ex-dividend on any given day, so
        this is one small request. When the repair download misses a ticker
        or comes back truncated (throttled), its cached history is restored
        exactly as it was -- including dropping the new-basis rows this
        refresh appended, which would otherwise leave the column mixing two
        bases -- and the ticker is excluded instead. Nothing is deleted: the
        old basis is still every close that ticker ever had, and the next
        refresh sees the same mismatch and tries the repair again. Returns
        ``(prices, {excluded ticker: reason})`` so the caller can quarantine
        the excluded names where reports will show them.
        """
        self.log(
            f"wolvwealth: {len(tickers)} ticker(s) changed adjustment basis (split or dividend); "
            f"re-downloading their full history: {', '.join(tickers)}"
        )
        repaired = self._fetch(tickers, self.history_start)
        prices = prices.copy()
        broken = {}
        for ticker in tickers:
            cached_days = int(cached[ticker].count())
            fresh_days = int(repaired[ticker].count()) if ticker in repaired.columns else 0
            if fresh_days and fresh_days >= self.MIN_HISTORY_COVERAGE * cached_days:
                prices[ticker] = repaired[ticker].reindex(prices.index)
            else:
                prices[ticker] = cached[ticker].reindex(prices.index)
                broken[ticker] = self._basis_failure(ticker, fresh_days, cached_days)
        return prices, broken

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
