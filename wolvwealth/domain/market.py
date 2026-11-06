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


def implausible_moves(prices: pd.DataFrame) -> dict:
    """Return ``{ticker: description}`` for every ticker with a day-over-day move beyond MAX_DAILY_MOVE.

    Each close is compared with the previous close that exists, so a gap
    (a throttled day, a trading halt) does not hide a jump across it.
    """
    previous = prices.ffill().shift(1)
    ratio = prices / previous
    flagged = (ratio > MAX_DAILY_MOVE) | (ratio < 1 / MAX_DAILY_MOVE)
    found = {}
    for ticker in prices.columns[flagged.any()]:
        day = flagged.index[flagged[ticker].to_numpy()][0]
        found[str(ticker)] = (
            f"moved {ratio.at[day, ticker]:.2f}x in one day on {day.date()} "
            f"({previous.at[day, ticker]:g} -> {prices.at[day, ticker]:g})"
        )
    return found


@dataclasses.dataclass(frozen=True)
class MarketData:
    """Ticker universe plus daily adjusted closes, with optional precomputed statistics."""

    universe: tuple
    prices: pd.DataFrame
    stats: MarketStats = None
    #: Which expected-return estimator this snapshot's statistics use, for
    #: the ones computed on demand as much as the precomputed ones. The app
    #: always uses the default; backtests compare alternatives.
    estimator: str = ESTIMATOR
    #: Memoized optimizer solutions for exactly these prices. Not part of the
    #: snapshot's identity, so it is excluded from equality and repr; a
    #: refreshed snapshot starts empty and cannot serve stale solutions.
    solutions: LRUCache = dataclasses.field(default_factory=LRUCache, compare=False, repr=False)

    @classmethod
    def build(cls, universe, prices: pd.DataFrame, stats: MarketStats = None, estimator: str = None) -> "MarketData":
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
        return cls(tuple(str(t).upper() for t in universe), prices, stats, estimator)

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
    def latest_prices(self) -> dict:
        """Ticker -> newest close, excluding delisted names whose history ends in NaN.

        Valuing a portfolio touches this once per holding, so it is a plain
        dict: the equivalent DataFrame lookups cost ~50 microseconds each and
        dominated requests once the solve itself was cached.
        """
        return {ticker: float(price) for ticker, price in self.prices.iloc[-1].dropna().items()}

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

    def value(self, holdings: dict) -> float:
        """Market value of a holdings map at the latest prices."""
        return sum(shares * self.price(ticker) for ticker, shares in holdings.items())

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
                raise ValueError(
                    f"No current price for {entry.upper()}: delisted, renamed, " "or missed by the last price refresh."
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
