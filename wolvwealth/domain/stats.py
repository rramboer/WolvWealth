"""MarketStats: expected returns and covariance over the full price history.

Both statistics are separable: the expected return is per column and the
exponentially weighted covariance is per pair of columns, so the stats of
any sub-universe are a slice of the full-universe stats. That is what lets
them be computed once per data refresh and sliced per optimize request
instead of recomputed from prices every time. Every estimator in
``ESTIMATORS`` must keep that property (``tests/test_stats.py`` checks it).
"""

import dataclasses

import pandas as pd
from pypfopt import expected_returns, risk_models

#: Trading days in a year, the annualization PyPortfolioOpt assumes too.
TRADING_DAYS = 252


def _mean_historical(prices: pd.DataFrame) -> pd.Series:
    """Geometric mean return over the whole history (CAGR), every day weighted equally."""
    return expected_returns.mean_historical_return(prices)


def _trailing_mean(years: int):
    """CAGR over only the last ``years`` of history: recency by a hard window."""

    def estimate(prices: pd.DataFrame) -> pd.Series:
        return expected_returns.mean_historical_return(prices.iloc[-years * TRADING_DAYS :])

    return estimate


def _ema(span: int):
    """Exponentially weighted mean daily return, annualized: recency by a smooth decay of ``span`` days."""

    def estimate(prices: pd.DataFrame) -> pd.Series:
        return expected_returns.ema_historical_return(prices, span=span)

    return estimate


#: Expected-return estimators by name. Each maps a price DataFrame to an
#: annualized return per column, computed from that column alone (the
#: separability the precomputed cache depends on; it rules out estimators
#: that look across tickers, such as CAPM against a universe-derived
#: benchmark or James-Stein shrinkage toward the cross-sectional mean).
#: All of them stay available so ``scripts/backtest.py --estimator`` can
#: compare them; ``ESTIMATOR`` names the one the app uses.
ESTIMATORS = {
    "mean_historical": _mean_historical,
    "mean_5y": _trailing_mean(5),
    "ema_250": _ema(250),
    "ema_500": _ema(500),
    "ema_1000": _ema(1000),
}

#: The estimator behind every optimization. Recorded in the statistics
#: cache's metadata, so changing this name rebuilds the cache on the next
#: load instead of serving returns computed the old way.
#:
#: ``ema_500`` was chosen by the walk-forward backtest (``scripts/backtest.py``;
#: the table is in CONTRIBUTING.md): over four start dates and two rebalance
#: frequencies it beat the whole-history mean on realized Sharpe in every
#: window (1.39 against 1.02 on average) and beat an equal-weight portfolio
#: in five of eight, which the whole-history mean never did. The 250-day
#: span scored a little higher still but traded a quarter more per
#: rebalance, in a backtest that charges nothing for trading; 500 days is
#: also the span PyPortfolioOpt itself defaults to, so it is a conventional
#: choice rather than a fitted one. The usual caveats hold: survivorship-
#: biased data, overlapping windows, and a decade in which recent winners
#: kept winning.
ESTIMATOR = "ema_500"


def expected_return(prices: pd.DataFrame, estimator: str = None) -> pd.Series:
    """Annualized expected return per column under ``estimator`` (default ``ESTIMATOR``)."""
    name = estimator or ESTIMATOR
    if name not in ESTIMATORS:
        raise ValueError(f"Unknown expected-return estimator {name!r}; choose from {', '.join(ESTIMATORS)}.")
    return ESTIMATORS[name](prices)


@dataclasses.dataclass(frozen=True)
class MarketStats:
    """Annualized expected return per ticker and covariance between tickers, and how mu was estimated."""

    mu: pd.Series
    cov: pd.DataFrame
    estimator: str = ESTIMATOR

    @classmethod
    def compute(cls, prices: pd.DataFrame, estimator: str = None) -> "MarketStats":
        """Compute expected returns under ``estimator`` and the exponentially weighted covariance."""
        name = estimator or ESTIMATOR
        return cls(expected_return(prices, name), risk_models.exp_cov(prices), name)

    def covers(self, tickers) -> bool:
        """Return true if every ticker has both statistics."""
        wanted = set(tickers)
        return wanted.issubset(self.mu.index) and wanted.issubset(self.cov.columns)

    def slice(self, tickers) -> tuple:
        """Return (mu, cov) restricted to ``tickers``, in that order."""
        tickers = list(tickers)
        return self.mu.loc[tickers], self.cov.loc[tickers, tickers]
