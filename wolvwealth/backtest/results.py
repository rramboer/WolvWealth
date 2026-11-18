"""Backtest output types."""

import dataclasses

import pandas as pd


@dataclasses.dataclass(frozen=True)
class RebalanceEvent:
    """One executed rebalance."""

    decision_date: pd.Timestamp  # Last price the strategy was allowed to see.
    execution_date: pd.Timestamp  # Trading day the new weights were bought.
    portfolio_value: float  # Value right before trading, at execution prices.
    weights: dict[str, float]  # Target weights.
    shares: dict[str, float]  # Holdings after trading.
    cash: float  # Uninvested cash after trading and costs.
    turnover: float  # Traded value / portfolio value (0..2).
    transaction_cost: float
    eligible_assets: int  # Universe members with enough history on the decision date.
    expected_performance: tuple[float, float, float] | None = None  # (return, vol, sharpe) the optimizer predicted.
    note: str | None = None  # Why the strategy chose this allocation, when notable (e.g. holding cash).

    def to_dict(self) -> dict:
        """JSON-friendly representation."""
        return {
            "decision_date": self.decision_date.date().isoformat(),
            "execution_date": self.execution_date.date().isoformat(),
            "portfolio_value": round(self.portfolio_value, 2),
            "weights": {asset: round(weight, 6) for asset, weight in self.weights.items()},
            "shares": {asset: round(shares, 4) for asset, shares in self.shares.items()},
            "cash": round(self.cash, 2),
            "turnover": round(self.turnover, 4),
            "transaction_cost": round(self.transaction_cost, 2),
            "eligible_assets": self.eligible_assets,
            "note": self.note,
            "expected_performance": None
            if self.expected_performance is None
            else {
                "expected_annual_return": round(self.expected_performance[0], 4),
                "annual_volatility": round(self.expected_performance[1], 4),
                "sharpe_ratio": round(self.expected_performance[2], 3),
            },
        }


@dataclasses.dataclass(frozen=True)
class BacktestResult:
    """Simulation output for one strategy."""

    strategy: str
    equity: pd.Series  # Daily portfolio value, indexed by trading day, covering the whole period.
    rebalances: list[RebalanceEvent]
    warnings: list[str]

    @property
    def start_value(self) -> float:
        """Portfolio value on the first day."""
        return float(self.equity.iloc[0])

    @property
    def end_value(self) -> float:
        """Portfolio value on the last day."""
        return float(self.equity.iloc[-1])

    def to_dict(self, include_equity: bool = True) -> dict:
        """JSON-friendly representation."""
        data = {
            "strategy": self.strategy,
            "start_value": round(self.start_value, 2),
            "end_value": round(self.end_value, 2),
            "rebalances": [event.to_dict() for event in self.rebalances],
            "warnings": list(self.warnings),
        }
        if include_equity:
            data["equity"] = {date.date().isoformat(): round(float(value), 2) for date, value in self.equity.items()}
        return data
