"""Realized performance statistics computed from an equity curve."""

import dataclasses
import math

import numpy as np
import pandas as pd

from wolvwealth.backtest.results import RebalanceEvent

TRADING_DAYS_PER_YEAR = 252


@dataclasses.dataclass(frozen=True)
class PerformanceMetrics:
    """What actually happened, as opposed to what the optimizer expected."""

    start_value: float
    end_value: float
    total_return: float  # Fraction, e.g. 0.23 for +23%.
    cagr: float
    annual_volatility: float
    sharpe_ratio: float
    max_drawdown: float  # Negative fraction, e.g. -0.18.
    calmar_ratio: float
    best_day: float
    worst_day: float
    trading_days: int
    years: float
    rebalances: int
    average_turnover: float
    total_transaction_costs: float

    def to_dict(self) -> dict:
        """JSON-friendly representation (NaN -> None)."""
        return {key: _clean(value) for key, value in dataclasses.asdict(self).items()}


def _clean(value):
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 6)
    return value


def compute_metrics(
    equity: pd.Series,
    rebalances: list[RebalanceEvent] | None = None,
    risk_free_rate: float = 0.0,
) -> PerformanceMetrics:
    """Summarize an equity curve indexed by trading day."""
    equity = equity.dropna()
    if len(equity) < 2:
        raise ValueError("Need at least two equity observations to compute metrics.")
    rebalances = rebalances or []
    start_value, end_value = float(equity.iloc[0]), float(equity.iloc[-1])
    total_return = end_value / start_value - 1
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (end_value / start_value) ** (1 / years) - 1 if years > 0 else float("nan")

    daily = equity.pct_change().dropna()
    annual_volatility = float(daily.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)) if len(daily) > 1 else float("nan")
    excess = float(daily.mean()) * TRADING_DAYS_PER_YEAR - risk_free_rate
    sharpe = excess / annual_volatility if annual_volatility and annual_volatility > 0 else float("nan")

    drawdown = equity / equity.cummax() - 1
    max_drawdown = float(drawdown.min())
    calmar = cagr / abs(max_drawdown) if max_drawdown < 0 and not math.isnan(cagr) else float("nan")

    return PerformanceMetrics(
        start_value=start_value,
        end_value=end_value,
        total_return=total_return,
        cagr=cagr,
        annual_volatility=annual_volatility,
        sharpe_ratio=sharpe,
        max_drawdown=max_drawdown,
        calmar_ratio=calmar,
        best_day=float(daily.max()) if len(daily) else float("nan"),
        worst_day=float(daily.min()) if len(daily) else float("nan"),
        trading_days=len(equity),
        years=years,
        rebalances=len(rebalances),
        average_turnover=float(np.mean([event.turnover for event in rebalances])) if rebalances else 0.0,
        total_transaction_costs=float(sum(event.transaction_cost for event in rebalances)),
    )
