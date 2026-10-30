"""MarketData: an immutable snapshot of the price history the optimizer reads.

The universe is ordered by descending market cap, so ``top(n)`` is the n
largest names. Prices are a DataFrame indexed by date with one column per
ticker. Where the data came from (CSV, Parquet, a test fixture) is not this
class's concern.
"""
import dataclasses

import pandas as pd

from wolvwealth.domain.stats import MarketStats

#: "top20" expands to the 20 largest tickers by market cap, etc.
UNIVERSE_ALIASES = {f"top{n}": n for n in (20, 50, 100, 200, 300, 400, 500)}


@dataclasses.dataclass(frozen=True)
class MarketData:
    """Ticker universe plus daily adjusted closes, with optional precomputed statistics."""

    universe: tuple
    prices: pd.DataFrame
    stats: MarketStats = None

    @classmethod
    def build(cls, universe, prices: pd.DataFrame, stats: MarketStats = None) -> "MarketData":
        """Normalize the universe to a tuple of upper-case tickers."""
        return cls(tuple(str(t).upper() for t in universe), prices, stats)

    def __contains__(self, ticker) -> bool:
        """Return true if the ticker is in the universe."""
        return ticker in self.universe

    def top(self, count: int) -> list:
        """Return the ``count`` largest tickers by market cap."""
        return list(self.universe[:count])

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
        return ticker in self.prices.columns and pd.notna(self.prices[ticker].iloc[-1])

    def price(self, ticker: str) -> float:
        """Latest cached price of one ticker. KeyError if there is none."""
        if not self.has_price(ticker):
            raise KeyError(ticker)
        return float(self.prices[ticker].iloc[-1])

    def value(self, holdings: dict) -> float:
        """Market value of a holdings map at the latest prices."""
        return sum(shares * self.price(ticker) for ticker, shares in holdings.items())

    def truncated(self, end_date) -> "MarketData":
        """Return a snapshot that knows nothing after ``end_date`` (for backtests).

        Precomputed stats are dropped: they describe the full history, and a
        truncated snapshot reading them would be lookahead.
        """
        return MarketData(self.universe, self.prices[:end_date])

    def stats_for(self, tickers) -> tuple:
        """Return the expected returns and covariance for ``tickers`` as ``(mu, cov)``.

        Sliced from the precomputed full-universe stats when they cover the
        request; computed from prices otherwise (truncated backtest
        snapshots, tests).
        """
        tickers = list(tickers)
        if self.stats is not None and self.stats.covers(tickers):
            return self.stats.slice(tickers)
        return MarketStats.compute(self.prices[tickers]).slice(tickers)

    def expand_universe(self, entries) -> list:
        """Expand aliases and tickers into a deduplicated ticker list.

        Raises ValueError naming the first entry that is neither an alias nor
        a ticker in the universe.
        """
        expanded = []
        for entry in entries:
            if not isinstance(entry, str):
                raise ValueError("universe must be list of strings.")
            if entry in UNIVERSE_ALIASES:
                expanded.extend(self.top(UNIVERSE_ALIASES[entry]))
            elif entry.upper() in self.universe:
                expanded.append(entry.upper())
            else:
                raise ValueError(f"Invalid symbol in universe: {entry}.")
        return list(dict.fromkeys(expanded))
