"""MarketStats: expected returns and covariance over the full price history.

Both statistics are separable: the mean historical return is per column and
the exponentially weighted covariance is per pair of columns, so the stats of
any sub-universe are a slice of the full-universe stats. That is what lets
them be computed once per data refresh and sliced per optimize request
instead of recomputed from prices every time.
"""
import dataclasses

import pandas as pd
from pypfopt import expected_returns, risk_models


@dataclasses.dataclass(frozen=True)
class MarketStats:
    """Annualized expected return per ticker and covariance between tickers."""

    mu: pd.Series
    cov: pd.DataFrame

    @classmethod
    def compute(cls, prices: pd.DataFrame) -> "MarketStats":
        """Mean historical return and exponentially weighted covariance (the optimizer's inputs)."""
        return cls(expected_returns.mean_historical_return(prices), risk_models.exp_cov(prices))

    def covers(self, tickers) -> bool:
        """Return true if every ticker has both statistics."""
        wanted = set(tickers)
        return wanted.issubset(self.mu.index) and wanted.issubset(self.cov.columns)

    def slice(self, tickers) -> tuple:
        """Return (mu, cov) restricted to ``tickers``, in that order."""
        tickers = list(tickers)
        return self.mu.loc[tickers], self.cov.loc[tickers, tickers]
