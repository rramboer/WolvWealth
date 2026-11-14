"""Backtest configuration: what to simulate, independent of how."""

import dataclasses
import datetime
import enum

import pandas as pd

from wolvwealth.optimizer_params import Constraints, InvalidOptimizationInput, check_feasibility


class Frequency(enum.StrEnum):
    """How often the portfolio is re-optimized."""

    DAILY = "daily"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMIANNUALLY = "semiannually"
    YEARLY = "yearly"

    @property
    def offset(self) -> pd.DateOffset:
        """Calendar step between rebalance decisions."""
        return _OFFSETS[self]


_OFFSETS = {
    Frequency.DAILY: pd.DateOffset(days=1),
    Frequency.WEEKLY: pd.DateOffset(weeks=1),
    Frequency.BIWEEKLY: pd.DateOffset(weeks=2),
    Frequency.MONTHLY: pd.DateOffset(months=1),
    Frequency.QUARTERLY: pd.DateOffset(months=3),
    Frequency.SEMIANNUALLY: pd.DateOffset(months=6),
    Frequency.YEARLY: pd.DateOffset(years=1),
}


@dataclasses.dataclass(frozen=True)
class BacktestConfig:
    """Everything needed to reproduce one backtest run."""

    start: datetime.date
    end: datetime.date
    universe: tuple[str, ...]
    initial_cash: float = 100_000.0
    frequency: Frequency = Frequency.MONTHLY
    lookback_days: int = 3 * 365  # Calendar days of price history fed to the optimizer.
    execution_lag: int = 1  # Trading days between the optimization decision and the trade.
    transaction_cost_bps: float = 0.0  # Fee on traded value, in basis points.
    constraints: Constraints = Constraints()
    risk_free_rate: float = 0.02  # Annual rate used for Sharpe ratios.

    def __post_init__(self) -> None:
        """Reject configurations that cannot be simulated."""
        if self.start >= self.end:
            raise InvalidOptimizationInput("start must be before end.")
        if not self.universe:
            raise InvalidOptimizationInput("universe must contain at least one ticker.")
        if self.initial_cash <= 0:
            raise InvalidOptimizationInput("initial_cash must be positive.")
        if self.lookback_days < 30:
            raise InvalidOptimizationInput("lookback_days must be at least 30.")
        if self.execution_lag < 0:
            raise InvalidOptimizationInput("execution_lag cannot be negative.")
        if self.transaction_cost_bps < 0:
            raise InvalidOptimizationInput("transaction_cost_bps cannot be negative.")
        check_feasibility(self.constraints, len(self.universe))

    @property
    def lookback(self) -> pd.Timedelta:
        """Lookback window as a Timedelta."""
        return pd.Timedelta(days=self.lookback_days)

    def to_dict(self) -> dict:
        """JSON-friendly representation."""
        data = dataclasses.asdict(self)
        data["start"] = self.start.isoformat()
        data["end"] = self.end.isoformat()
        data["universe"] = list(self.universe)
        data["frequency"] = str(self.frequency)
        return data
