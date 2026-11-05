"""MarketData: an immutable snapshot of the price history the optimizer reads.

The universe is ordered by descending market cap, so ``top(n)`` is the n
largest names. Prices are a DataFrame indexed by date with one column per
ticker. Where the data came from (CSV, Parquet, a test fixture) is not this
class's concern.
"""

import dataclasses
import functools

import pandas as pd

from wolvwealth.domain.cache import LRUCache
from wolvwealth.domain.stats import ESTIMATOR, ESTIMATORS, MarketStats

#: "top20" expands to the 20 largest tickers by market cap, etc.
UNIVERSE_ALIASES = {f"top{n}": n for n in (20, 50, 100, 200, 300, 400, 500)}


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

    def top(self, count: int) -> list:
        """Return the ``count`` largest tradeable tickers by market cap."""
        return list(self.tradeable[:count])

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

    def expand_universe(self, entries) -> list:
        """Expand aliases and tickers into a deduplicated, tradeable ticker list.

        Raises ValueError naming the first entry that is neither an alias nor
        a ticker in the universe, or that names a ticker with no current
        price. Aliases skip untradeable names rather than failing, so
        ``top500`` is the 500 largest names that can actually be bought.
        """
        expanded = []
        for entry in entries:
            if not isinstance(entry, str):
                raise ValueError("universe must be list of strings.")
            if entry in UNIVERSE_ALIASES:
                expanded.extend(self.top(UNIVERSE_ALIASES[entry]))
            elif entry.upper() in self.latest_prices:
                expanded.append(entry.upper())
            elif entry.upper() in self.tickers:
                raise ValueError(
                    f"No current price for {entry.upper()}: delisted, renamed, " "or missed by the last price refresh."
                )
            else:
                raise ValueError(f"Invalid symbol in universe: {entry}.")
        return list(dict.fromkeys(expanded))
