"""Realized performance statistics computed from an equity curve."""

import dataclasses
import math

import numpy as np
import pandas as pd

from wolvwealth.backtest.results import RebalanceEvent

TRADING_DAYS_PER_YEAR = 252  # Fallback when the curve is too short to measure its own frequency.
RISKLESS_TOLERANCE = 1e-12  # Daily excess-return spread below this is floating-point noise: no risk.


@dataclasses.dataclass(frozen=True)
class PerformanceMetrics:
    """What actually happened, as opposed to what the optimizer expected."""

    start_value: float
    end_value: float
    total_return: float  # Fraction, e.g. 0.23 for +23%.
    cagr: float
    annual_volatility: float  # Of returns in excess of the risk-free rate, so cash contributes none.
    sharpe_ratio: float  # Mean / spread of daily excess returns, annualized; 0 for a portfolio that only holds cash.
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


def _volatility_and_sharpe(excess: np.ndarray, periods_per_year: float) -> tuple[float, float]:
    """Annualized spread and Sharpe ratio of per-period excess returns.

    A spread at floating-point-noise level means the portfolio took no risk
    (it only held cash): volatility is 0, and so is the Sharpe ratio when the
    mean excess return is also nil. Riskless outperformance has no finite
    Sharpe ratio and is reported as undefined.
    """
    if len(excess) < 2:
        return float("nan"), float("nan")
    mean = float(np.mean(excess))
    spread = float(np.std(excess, ddof=1))
    if spread < RISKLESS_TOLERANCE:
        return 0.0, (0.0 if abs(mean) < RISKLESS_TOLERANCE else float("nan"))
    root = math.sqrt(periods_per_year)
    return spread * root, mean / spread * root


def compute_metrics(
    equity: pd.Series,
    rebalances: list[RebalanceEvent] | None = None,
    risk_free_rate: float = 0.0,
) -> PerformanceMetrics:
    """Summarize an equity curve indexed by trading day.

    Volatility and Sharpe are computed from each period's excess return: the
    portfolio's return minus what the risk-free asset earned over the same
    calendar days, (1 + rate) ** (days / 365.25) - 1, matching how the engine
    accrues interest on cash. Comparing like with like matters: subtracting
    the compound annual rate from an arithmetic mean of daily returns, or
    counting the weekend-interest pattern of cash as volatility, made a
    portfolio that only held cash show a negative Sharpe ratio. Both are
    annualized with the curve's observed number of periods per year.
    """
    equity = equity.dropna()
    if len(equity) < 2:
        raise ValueError("Need at least two equity observations to compute metrics.")
    rebalances = rebalances or []
    start_value, end_value = float(equity.iloc[0]), float(equity.iloc[-1])
    total_return = end_value / start_value - 1
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (end_value / start_value) ** (1 / years) - 1 if years > 0 else float("nan")

    daily = equity.pct_change().dropna()
    gap_days = np.diff(equity.index.to_numpy()).astype("timedelta64[D]").astype(float)
    risk_free = (1.0 + risk_free_rate) ** (gap_days / 365.25) - 1.0
    excess = daily.to_numpy() - risk_free
    periods_per_year = len(daily) / years if years > 0 else TRADING_DAYS_PER_YEAR
    annual_volatility, sharpe = _volatility_and_sharpe(excess, periods_per_year)

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
