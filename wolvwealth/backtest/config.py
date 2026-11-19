"""Backtest configuration: what to simulate, independent of how."""

import dataclasses
import datetime
import enum

import pandas as pd

from wolvwealth.optimizer_params import (
    MAX_CASH,
    Constraints,
    InvalidOptimizationInput,
    check_feasibility,
    require_integer,
    require_number,
)

MIN_LOOKBACK_DAYS = 30
MAX_LOOKBACK_DAYS = 15 * 365
MAX_EXECUTION_LAG = 30  # Trading days; anything longer is not a rebalance lag.
MAX_TRANSACTION_COST_BPS = 1_000  # 10% per trade.


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
    risk_free_rate: float = 0.02  # Annual rate cash earns, used for the hold-cash decision and Sharpe ratios.

    def __post_init__(self) -> None:
        """Reject configurations that cannot be simulated, whatever built them (API, CLI, code)."""
        if not isinstance(self.start, datetime.date) or not isinstance(self.end, datetime.date):
            raise InvalidOptimizationInput("start and end must be dates.")
        if self.start >= self.end:
            raise InvalidOptimizationInput("start must be before end.")
        if not self.universe or not all(isinstance(ticker, str) and ticker for ticker in self.universe):
            raise InvalidOptimizationInput("universe must contain at least one ticker.")
        if not isinstance(self.frequency, Frequency):
            raise InvalidOptimizationInput("frequency must be a Frequency.")
        require_number(self.initial_cash, "initial_cash", minimum=0.0, maximum=MAX_CASH, exclusive_minimum=True)
        require_integer(self.lookback_days, "lookback_days", minimum=MIN_LOOKBACK_DAYS, maximum=MAX_LOOKBACK_DAYS)
        require_integer(self.execution_lag, "execution_lag", minimum=0, maximum=MAX_EXECUTION_LAG)
        require_number(self.transaction_cost_bps, "transaction_cost_bps", minimum=0.0, maximum=MAX_TRANSACTION_COST_BPS)
        require_number(self.risk_free_rate, "risk_free_rate", minimum=-1.0, maximum=1.0, exclusive_minimum=True)
        if not isinstance(self.constraints, Constraints):
            raise InvalidOptimizationInput("constraints must be a Constraints object.")
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
