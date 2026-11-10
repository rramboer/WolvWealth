"""MarketData: an immutable snapshot of the price history the optimizer reads.

The universe is ordered by descending market cap, so ``top(n)`` is the n
largest names. Prices are a DataFrame indexed by date with one column per
ticker. Where the data came from (CSV, Parquet, a test fixture) is not this
class's concern.
"""

import dataclasses
import functools

import numpy as np
import pandas as pd

from wolvwealth.domain.cache import LRUCache
from wolvwealth.domain.stats import ESTIMATOR, ESTIMATORS, TRADING_DAYS, MarketStats

#: "top20" expands to the 20 largest tickers by market cap, etc.
UNIVERSE_ALIASES = {f"top{n}": n for n in (20, 50, 100, 200, 300, 400, 500)}


#: Highest believable share price. The most expensive US share trades in the
#: hundreds of thousands; anything above this is corruption or tampering.
MAX_PRICE = 1e7
#: Largest believable one-day move in split- and dividend-adjusted closes,
#: as a factor either way (5x up, or down to a fifth). Adjusted large-cap data
#: essentially never does this; a poisoned or unadjusted download does, and
#: one such spike is enough to drag the optimizer toward or away from a name.
MAX_DAILY_MOVE = 5.0
#: Closes a ticker must still have after an implausible move truncates its
#: history for the remainder to be worth serving. Below this there is too
#: little left to estimate anything from, so the ticker is excluded instead.
#: Deliberately far under MIN_HISTORY_DAYS: a name with 30 trusted closes is
#: kept out of universes by the seasoning rule anyway, but can still be held,
#: valued and reported, which total exclusion would prevent.
MIN_TRUSTED_DAYS = 30
#: Trading days a close may be behind the newest row and still count as a
#: current price. Yahoo throttles individual tickers constantly, and the
#: cache is a daily snapshot, so "has a close stamped with the newest date
#: in the file" is not a property a correct portfolio can depend on: one
#: throttled ticker would otherwise make a held position unvaluable and
#: refuse the whole request, on the main path, in an unattended run. A
#: carried-forward close is what every broker statement shows for a day a
#: stock did not print, and the error it introduces is small and bounded
#: (measured on this app's own data: ~0.7% median drift after one trading
#: day, ~2.3% after ten), where refusing to answer is total. Ten trading
#: days is two calendar weeks: long enough to ride out throttling across
#: several refreshes of a weekly schedule, short enough that a genuinely
#: delisted name stops being quoted promptly. Every price older than the
#: snapshot is reported, per ticker, wherever it is used.
MAX_STALE_DAYS = 10

#: Days of prices a ticker needs before the optimizer may allocate to it,
#: unless a request says otherwise: one trading year. The estimators
#: annualize whatever history exists, so a company listed three weeks ago
#: that rose 15% shows an expected return in the thousands of percent, and
#: a covariance estimated from the same fifteen returns is mostly noise, so
#: the solver sees the best return in the universe at almost no risk and
#: puts the maximum allowed weight there. The standard remedy, and the one
#: institutional risk models use before they trust an asset's own history,
#: is to require a seasoning period; a year is the usual one, and is what
#: the walk-forward backtest already needed to get through 2012.
MIN_HISTORY_DAYS = TRADING_DAYS


def clean_prices(prices, minimum_rows: int = 2, latest=None) -> pd.DataFrame:
    """Return ``prices`` in the shape every reader assumes, or raise ValueError saying what is wrong.

    The cache is written by this app, but also survives crashes, older
    versions and hand edits, and every download is whatever Yahoo sent. So,
    one definition of usable: rows are dates, ascending and unique (a repeated
    date keeps its last row); tickers are unique upper-case strings; a close
    is a positive finite number no higher than MAX_PRICE, anything else
    (text, zero, negative, infinite, absurd) is missing; a day with no price
    at all is dropped, as is any day after ``latest`` (a download cannot
    contain tomorrow). Fewer than ``minimum_rows`` usable days is an error:
    nothing can be estimated from it.
    """
    if prices.empty and minimum_rows == 0:
        return prices
    frame = prices
    if not isinstance(frame.index, pd.DatetimeIndex):
        if not (pd.api.types.is_object_dtype(frame.index) or pd.api.types.is_string_dtype(frame.index)):
            raise ValueError("its rows are not dated.")
        try:
            frame = frame.set_axis(pd.to_datetime(frame.index), axis=0)
        except (ValueError, TypeError) as err:
            raise ValueError("its rows are not dated.") from err
    frame = frame.set_axis([str(column).strip().upper() for column in frame.columns], axis=1)
    frame = frame.loc[:, ~frame.columns.duplicated()]
    if not all(pd.api.types.is_float_dtype(dtype) for dtype in frame.dtypes):
        frame = frame.apply(pd.to_numeric, errors="coerce").astype(float)
    frame = frame.where(np.isfinite(frame) & (frame > 0) & (frame <= MAX_PRICE))
    if not frame.index.is_monotonic_increasing:
        frame = frame.sort_index(kind="stable")
    if frame.index.has_duplicates:
        frame = frame[~frame.index.duplicated(keep="last")]
    if latest is not None:
        frame = frame[frame.index <= pd.Timestamp(latest)]
    frame = frame.dropna(how="all")
    if len(frame) < minimum_rows:
        raise ValueError(f"it has {len(frame)} usable day(s) of prices; at least {minimum_rows} are needed.")
    frame.index.name = "Date"
    return frame


def _beyond_band(ratio) -> bool:
    """Return true if a one-day ratio is outside the believable band (NaN is never beyond it)."""
    return bool(ratio > MAX_DAILY_MOVE or ratio < 1 / MAX_DAILY_MOVE)


def _describe_move(closes: pd.Series, position: int) -> str:
    """Say what happened at ``position``, against the close before it."""
    previous, close = closes.iloc[position - 1], closes.iloc[position]
    return f"moved {close / previous:.2f}x in one day on {closes.index[position].date()} ({previous:g} -> {close:g})"


def classify_implausible(closes: pd.Series, accepted=(), since=None):
    """Judge one ticker's closes, returning what to stop using, or None if nothing is suspect.

    A single price series cannot prove whether a 6x day was a real
    repricing or a corrupt print, so the question asked here is the one it
    *can* answer: does removing the day restore continuity?

    - **It reverts.** The next close returns to where the move started, so
      the day is an isolated bad print. Only that close is dropped
      (``masked_days``); the ticker keeps trading on the rest.
    - **It persists.** The price stayed at the new level, so either the
      market repriced the company or the feed changed basis. Either way the
      history across the jump is a poor basis for estimating return and
      risk, so only prices from that day on are used (``untrusted_before``).
      That holds whichever of the two it was, which is why the verdict does
      not depend on resolving the unanswerable question.
    - **Nothing usable is left.** Too few closes follow the jump to serve,
      so the ticker is excluded outright (``scope`` ``"ticker"``).

    ``accepted`` holds dates a human adjudicated as real; those are never
    flagged again, and each becomes the level later days are measured from.
    A one-day round trip is two moves on two dates, so accepting the spike
    and not its reversal leaves the reversal to be judged on its own --
    deliberately, since "that price was real" says nothing about the next
    one. ``since`` ignores days before it, which were judged when they
    arrived.
    """
    closes = closes.dropna()
    if len(closes) < 2:
        return None
    values = closes.to_numpy()
    start = None if since is None else pd.Timestamp(since)
    masked, cut = [], None
    # The close each day is judged against: the newest one still believed.
    # A print marked for masking never becomes the baseline, or the day it
    # reverts would be measured from the bad number and a single corrupt
    # close would read as two moves -- a spike, then a crash back.
    baseline = 0
    for position in range(1, len(values)):
        day = closes.index[position]
        if not _beyond_band(values[position] / values[baseline]):
            baseline = position
            continue
        if str(day.date()) in accepted or (start is not None and day < start):
            # Settled, by a person or by an earlier refresh. It stands as
            # the level everything after it is measured from.
            baseline = position
            continue
        # Does the next close come back to where the move began? With no
        # close after it yet there is nothing to judge, and dropping one
        # print is the smaller mistake than distrusting a whole history --
        # if the move turns out to persist, the next refresh sees the jump
        # again (now with a successor) and truncates then.
        reverts = position + 1 < len(values) and not _beyond_band(values[position + 1] / values[baseline])
        if reverts or position + 1 >= len(values):
            masked.append(position)
            continue
        cut, baseline = position, position
    if cut is None and not masked:
        return None
    if cut is not None:
        return _persistent_finding(closes, cut, [p for p in masked if p > cut])
    first = masked[0]
    if first == len(values) - 1:
        tail = ", with no later close yet to tell a bad print from a repricing, so it is not used for now"
    else:
        tail = " and back the next close, so that one print is not used"
    others = f" ({len(masked) - 1} other such print(s) too)" if len(masked) > 1 else ""
    return {
        "reason": _describe_move(closes, first) + tail + others,
        "scope": "partial",
        "masked_days": [str(closes.index[p].date()) for p in masked],
    }


def _persistent_finding(closes: pd.Series, cut: int, masked) -> dict:
    """Describe a move the price stayed at: truncate the history, or give up on the ticker."""
    day = closes.index[cut]
    moved = _describe_move(closes, cut)
    remaining = len(closes) - cut
    if remaining < MIN_TRUSTED_DAYS:
        return {
            "reason": f"{moved} and stayed there, with only {remaining} close(s) after it -- too few to estimate "
            "anything from, so the ticker is excluded",
            "scope": "ticker",
        }
    entry = {
        "reason": f"{moved} and stayed there, so closes before {day.date()} are not used ({remaining} remain)",
        "scope": "partial",
        "untrusted_before": str(day.date()),
    }
    if masked:
        entry["masked_days"] = [str(closes.index[p].date()) for p in masked]
    return entry


def implausible_moves(prices: pd.DataFrame, accepted=None, since=None) -> dict:
    """Return ``{ticker: finding}`` for every ticker whose closes move implausibly (see ``classify_implausible``).

    Each close is compared with the previous close that exists, so a gap
    (a throttled day, a trading halt) does not hide a jump across it. The
    frame-wide comparison is only a filter: the few tickers it turns up are
    then judged one at a time, so the usual case of nothing suspect costs
    one vectorized pass.
    """
    if prices.empty:
        return {}
    accepted = accepted or {}
    ratio = prices / prices.ffill().shift(1)
    flagged = (ratio > MAX_DAILY_MOVE) | (ratio < 1 / MAX_DAILY_MOVE)
    window = flagged if since is None else flagged.loc[pd.Timestamp(since) :]
    if window.empty:
        return {}
    found = {}
    for ticker in prices.columns[window.any().to_numpy()]:
        finding = classify_implausible(prices[ticker], accepted.get(str(ticker), ()), since)
        if finding is not None:
            found[str(ticker)] = finding
    return found


def last_believable_close(series: pd.Series):
    """Return ``(close, date)`` of the newest close that precedes any implausible move, or None if there is none.

    For valuing a held position in a quarantined ticker: the first flagged
    move and everything from it on may be corrupt, so the newest close before
    it is the last number worth putting on the position. A history with no
    implausible move (quarantined because a split/dividend re-download
    failed, say) yields its newest close.
    """
    closes = series.dropna()
    if closes.empty:
        return None
    ratio = closes / closes.shift(1)
    flagged = (ratio > MAX_DAILY_MOVE) | (ratio < 1 / MAX_DAILY_MOVE)
    if flagged.any():
        closes = closes[closes.index < flagged.idxmax()]
        if closes.empty:
            return None
    return float(closes.iloc[-1]), closes.index[-1].date()


@dataclasses.dataclass(frozen=True)
class MarketData:  # pylint: disable=too-many-public-methods  # the domain's one read model; see below
    """Ticker universe plus daily adjusted closes, with optional precomputed statistics.

    Every question the optimizer, the API, the CLI and the MCP tools ask of
    the market is answered here, so the method count is the breadth of that
    vocabulary rather than a sign of more than one responsibility. Splitting
    it would mean handing callers two objects that must describe the same
    snapshot and could disagree about it.
    """

    universe: tuple
    prices: pd.DataFrame
    stats: MarketStats = None
    #: Which expected-return estimator this snapshot's statistics use, for
    #: the ones computed on demand as much as the precomputed ones. The app
    #: always uses the default; backtests compare alternatives.
    estimator: str = ESTIMATOR
    #: Quarantined tickers' best trustworthy close: ``{ticker: (close, date)}``,
    #: built by the market-data service (which knows *why* each was excluded,
    #: and so which close is safe -- see its ``_frozen_valuations``).
    #: The snapshot blanks a quarantined ticker's prices entirely, so without
    #: this a held position in one could not be totalled and the whole
    #: request would fail. Consulted only by ``can_value``,
    #: ``valuation_price`` and ``valuation_date`` -- never by
    #: ``latest_prices`` or ``tradeable`` -- so a quarantined name stays
    #: unbuyable however recent its last believable close is. Membership here
    #: is also how the snapshot knows a ticker is quarantined at all, which
    #: the refusal messages use to say so instead of guessing "delisted".
    frozen_valuations: dict = dataclasses.field(default_factory=dict)
    #: Memoized optimizer solutions for exactly these prices. Not part of the
    #: snapshot's identity, so it is excluded from equality and repr; a
    #: refreshed snapshot starts empty and cannot serve stale solutions.
    solutions: LRUCache = dataclasses.field(default_factory=LRUCache, compare=False, repr=False)

    @classmethod
    def build(  # pylint: disable=too-many-arguments  # mirrors the fields; every caller names what it passes
        cls,
        universe,
        prices: pd.DataFrame,
        stats: MarketStats = None,
        estimator: str = None,
        frozen_valuations: dict = None,
    ) -> "MarketData":
        """Normalize the universe to a tuple of upper-case tickers.

        The estimator defaults to the one ``stats`` were computed with, and
        may not contradict it: a snapshot that sliced one estimator's cache
        for some universes and computed another for the rest would price
        two requests on different assumptions.
        """
        estimator = estimator or (stats.estimator if stats is not None else ESTIMATOR)
        if estimator not in ESTIMATORS:
            raise ValueError(f"Unknown expected-return estimator {estimator!r}; choose from {', '.join(ESTIMATORS)}.")
        if stats is not None and stats.estimator != estimator:
            raise ValueError(f"stats were computed with {stats.estimator!r}, not {estimator!r}")
        return cls(tuple(str(t).upper() for t in universe), prices, stats, estimator, frozen_valuations or {})

    def with_estimator(self, estimator: str) -> "MarketData":
        """Return a snapshot of the same prices under another estimator, with no statistics and no memo carried over.

        Both must start empty: the cached statistics and the memoized
        solutions were produced under the old estimator, and a fresh
        instance is the only way to be sure nothing of them is reused.
        """
        return MarketData.build(self.universe, self.prices, estimator=estimator)

    @functools.cached_property
    def tickers(self) -> frozenset:
        """The universe as a set, for membership tests that would otherwise scan the tuple."""
        return frozenset(self.universe)

    @functools.cached_property
    def _pricing_window(self) -> pd.DataFrame:
        """The newest rows in which a close still counts as a current price (see MAX_STALE_DAYS)."""
        return self.prices.iloc[-(MAX_STALE_DAYS + 1) :]

    @functools.cached_property
    def _priced_at(self) -> dict:
        """Ticker -> row of ``_pricing_window`` holding the close ``latest_prices`` serves."""
        window = self._pricing_window
        valid = window.notna().to_numpy()
        # Last True per column, found from the end so the newest close wins.
        newest = len(window) - 1 - valid[::-1].argmax(axis=0)
        return {str(t): int(newest[i]) for i, t in enumerate(window.columns) if valid[:, i].any()}

    @functools.cached_property
    def latest_prices(self) -> dict:
        """Ticker -> newest close within MAX_STALE_DAYS, carried forward over days the ticker did not print.

        Excludes names quoted nowhere in that window: delisted, renamed, or
        throttled for long enough that calling their last close "current"
        would be a fiction. Which prices were carried, and from when, is in
        ``stale_prices`` -- a reader is never left to assume everything here
        is stamped ``as_of``.

        Valuing a portfolio touches this once per holding, so it is a plain
        dict: the equivalent DataFrame lookups cost ~50 microseconds each and
        dominated requests once the solve itself was cached.
        """
        window = self._pricing_window
        return {
            ticker: float(window.iat[row, window.columns.get_loc(ticker)]) for ticker, row in self._priced_at.items()
        }

    @functools.cached_property
    def _price_dates(self) -> dict:
        """Ticker -> date of the close ``latest_prices`` serves for it."""
        window = self._pricing_window
        return {ticker: window.index[row].date() for ticker, row in self._priced_at.items()}

    @functools.cached_property
    def stale_prices(self) -> dict:
        """Tickers priced from a close older than the snapshot: ``{ticker: {as_of, trading_days_stale}}``.

        The disclosure that makes carrying a price forward honest. Empty
        whenever every tradeable ticker printed on the newest day, which is
        the ordinary case.
        """
        newest = len(self._pricing_window) - 1
        return {
            ticker: {"as_of": str(self._price_dates[ticker]), "trading_days_stale": newest - row}
            for ticker, row in sorted(self._priced_at.items())
            if row < newest
        }

    @functools.cached_property
    def _last_known(self) -> dict:
        """Ticker -> ``(close, date)`` for its newest close at any age, however old.

        Only for valuing a position someone already holds: a share count is
        a fact whatever the price feed is doing, and a portfolio that cannot
        be totalled is a failed request. Never a basis for buying, and never
        reached while ``latest_prices`` has the ticker, so the full-history
        scan it costs happens only when something is genuinely unquoted.
        """
        values = self.prices.to_numpy()
        valid = ~np.isnan(values)
        newest = len(values) - 1 - valid[::-1].argmax(axis=0)
        return {
            str(ticker): (float(values[newest[i], i]), self.prices.index[newest[i]].date())
            for i, ticker in enumerate(self.prices.columns)
            if valid[:, i].any()
        }

    def __contains__(self, ticker) -> bool:
        """Return true if the ticker is in the universe."""
        return ticker in self.tickers

    @functools.cached_property
    def tradeable(self) -> tuple:
        """The universe in market-cap order, minus names with no current price.

        Tickers that were acquired, renamed or delisted keep their place in
        the ticker list but their prices stop, which makes their
        exponentially weighted covariance with everything else NaN and makes
        the solver reject the whole problem. They are also, by definition,
        not buyable, so no allocation should ever name one.
        """
        return tuple(ticker for ticker in self.universe if ticker in self.latest_prices)

    @functools.cached_property
    def history_days(self) -> dict:
        """Ticker -> number of days with a price, which is how much history its statistics rest on."""
        return {str(ticker): int(days) for ticker, days in self.prices.count().items()}

    @functools.cached_property
    def _seasoned_by_default(self) -> tuple:
        return self._seasoned(MIN_HISTORY_DAYS)

    def _seasoned(self, min_history_days: int) -> tuple:
        return tuple(ticker for ticker in self.tradeable if self.history_days[ticker] >= min_history_days)

    def seasoned(self, min_history_days: int = MIN_HISTORY_DAYS) -> tuple:
        """Return the tradeable universe in market-cap order, minus names with under ``min_history_days`` of prices.

        These are the tickers a universe alias expands to and the only ones
        an entry may name without lowering the minimum. The default is
        memoized because every request without an explicit universe builds
        its default from it.
        """
        if min_history_days == MIN_HISTORY_DAYS:
            return self._seasoned_by_default
        return self._seasoned(min_history_days)

    def short_history(self, min_history_days: int = MIN_HISTORY_DAYS) -> dict:
        """Tradeable tickers with fewer than ``min_history_days`` of prices, each with its count.

        For the market report: a symbol that is in the tradeable list and
        still refused in a universe needs to be explained where the
        decision is made, not in a log.
        """
        return {
            ticker: self.history_days[ticker]
            for ticker in self.tradeable
            if self.history_days[ticker] < min_history_days
        }

    def top(self, count: int, min_history_days: int = MIN_HISTORY_DAYS) -> list:
        """Return the ``count`` largest tradeable tickers by market cap with at least ``min_history_days`` of prices."""
        return list(self.seasoned(min_history_days)[:count])

    @property
    def as_of(self):
        """Date of the newest price row."""
        return self.prices.index[-1].date()

    def age_days(self, now=None) -> int:
        """Days between the newest price row and ``now`` (default: today)."""
        now = now or pd.Timestamp.now()
        return (pd.Timestamp(now) - self.prices.index[-1]).days

    def has_price(self, ticker: str) -> bool:
        """Return true if the ticker has a usable latest price (delisted names end in NaN)."""
        return ticker in self.latest_prices

    def price(self, ticker: str) -> float:
        """Latest cached price of one ticker. KeyError if there is none."""
        if ticker not in self.latest_prices:
            raise KeyError(ticker)
        return self.latest_prices[ticker]

    def can_value(self, ticker: str) -> bool:
        """Return true if the ticker has ever had a close, so a position in it can be totalled.

        A quarantined ticker's closes are blanked from the snapshot, but its
        last believable one survives in ``frozen_valuations``, so holding one
        never makes a portfolio untotallable.
        """
        return ticker in self.latest_prices or ticker in self._last_known or ticker in self.frozen_valuations

    def valuation_price(self, ticker: str) -> float:
        """Price for valuing a held position: the current one, else the last known close at any age.

        Distinct from ``price`` on purpose. ``price`` answers "what may the
        optimizer trade this at", and goes missing when nothing recent is
        quoted. This answers "what is the position someone already owns
        worth", where the only alternative to an old close is refusing to
        total their portfolio. Callers must disclose the date, which
        ``valuation_date`` gives them. KeyError if the ticker never traded.
        """
        if ticker in self.latest_prices:
            return self.latest_prices[ticker]
        if ticker in self._last_known:
            return self._last_known[ticker][0]
        return self.frozen_valuations[ticker][0]

    def valuation_date(self, ticker: str):
        """Date of the close ``valuation_price`` uses for the ticker. KeyError if it never traded."""
        if ticker in self._price_dates:
            return self._price_dates[ticker]
        if ticker in self._last_known:
            return self._last_known[ticker][1]
        return self.frozen_valuations[ticker][1]

    def value(self, holdings: dict) -> float:
        """Market value of a holdings map, valuing each position at the best price available for it."""
        return sum(shares * self.valuation_price(ticker) for ticker, shares in holdings.items())

    def truncated(self, end_date) -> "MarketData":
        """Return a snapshot that knows nothing after ``end_date`` (for backtests).

        Precomputed stats are dropped: they describe the full history, and a
        truncated snapshot reading them would be lookahead.
        """
        return MarketData(self.universe, self.prices[:end_date], estimator=self.estimator)

    def stats_for(self, tickers) -> tuple:
        """Return the expected returns and covariance for ``tickers`` as ``(mu, cov)``.

        Sliced from the precomputed full-universe stats when they cover the
        request; computed from prices otherwise (truncated backtest
        snapshots, tests).
        """
        tickers = list(tickers)
        if self.stats is not None and self.stats.covers(tickers):
            return self.stats.slice(tickers)
        return MarketStats.compute(self.prices[tickers], self.estimator).slice(tickers)

    def expand_universe(self, entries, min_history_days: int = MIN_HISTORY_DAYS, held=()) -> list:
        """Expand aliases and tickers into a deduplicated, tradeable, seasoned ticker list.

        Raises ValueError naming the first entry that is neither an alias nor
        a ticker in the universe, that names a ticker with no current price,
        or that names one with fewer than ``min_history_days`` of prices.
        Aliases skip untradeable and short-history names rather than failing,
        so ``top500`` is the 500 largest names that can actually be bought
        and have statistics worth optimizing on. A named short-history ticker
        is refused rather than skipped: the entry was deliberate, and the
        reader must learn why it is missing and how to include it anyway.
        The exception is a short-history ticker in ``held``: it is skipped,
        because the optimizer keeps such a position as it is rather than
        either sizing it on noise or selling it, and the browser, CLI and
        MCP flows name every held ticker so keeping it is possible.
        """
        held = set(held)
        expanded = []
        for entry in entries:
            if not isinstance(entry, str):
                raise ValueError("universe must be list of strings.")
            if entry in UNIVERSE_ALIASES:
                expanded.extend(self.top(UNIVERSE_ALIASES[entry], min_history_days))
            elif entry.upper() in self.latest_prices:
                ticker = entry.upper()
                if self.history_days[ticker] < min_history_days:
                    if ticker in held:
                        continue
                    raise ValueError(self.describe_short_history(ticker, min_history_days))
                expanded.append(ticker)
            elif entry.upper() in self.tickers:
                # A held one is skipped, not refused, for the same reason a
                # short-history holding is: the optimizer keeps it as it is
                # rather than sizing or selling it, and every surface names
                # held tickers in the universe, so naming it must not fail.
                if entry.upper() in held:
                    continue
                if entry.upper() in self.frozen_valuations:
                    raise ValueError(
                        f"{entry.upper()} is quarantined: its downloaded prices failed a plausibility check, so "
                        "it cannot be bought. The market status report gives the reason under `quarantined`; a "
                        "refresh that downloads a believable history lifts the exclusion."
                    )
                raise ValueError(
                    f"No current price for {entry.upper()}: delisted, renamed, or not quoted in the last "
                    f"{MAX_STALE_DAYS} trading days (Yahoo may be throttling it)."
                )
            else:
                raise ValueError(f"Invalid symbol in universe: {entry}.")
        return list(dict.fromkeys(expanded))

    def describe_short_history(self, ticker: str, min_history_days: int = MIN_HISTORY_DAYS) -> str:
        """Say why ``ticker`` is refused in a universe and what would admit it."""
        days = self.history_days[ticker]
        return (
            f"{ticker} has only {days} day(s) of price history; at least {min_history_days} are needed to estimate "
            f"its return and risk (a few weeks of moves extrapolated to a year is noise the optimizer would act "
            f"on). Leave it out, or pass min_history_days of {days} or less to include it anyway."
        )
