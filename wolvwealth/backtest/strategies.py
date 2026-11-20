"""Allocation strategies: given a price window, decide target weights.

The engine only knows the Strategy protocol, so the optimizer and the
benchmarks (equal weight, buy and hold) are all simulated by the same code.
"""

import dataclasses
from typing import Protocol

import cvxpy
import pandas as pd
from pypfopt import exceptions as pypfopt_exceptions

from wolvwealth.optimizer_core import HoldCashAnswer, TargetUnreachable, compute_portfolio
from wolvwealth.optimizer_params import Constraints, Objective

Weights = dict[str, float]


class StrategyError(Exception):
    """The strategy could not produce weights for this window; the engine holds."""


class Strategy(Protocol):
    """Decides target weights at each rebalance."""

    name: str

    def target_weights(self, window: pd.DataFrame) -> Weights | None:
        """Return target weights (summing to 1), {} to move everything to cash, or None to keep current holdings.

        `window` holds the lookback prices of the eligible assets only. May
        raise StrategyError when no sensible allocation exists.
        """


class OptimizerStrategy:
    """The WolvWealth optimizer: the configured objective (default max-Sharpe) over the lookback window."""

    def __init__(
        self,
        constraints: Constraints | None = None,
        risk_free_rate: float = 0.02,
        *,
        objective: Objective | None = None,
    ) -> None:
        """Store the objective and constraints applied at every rebalance."""
        self.constraints = constraints or Constraints()
        self.objective = objective or Objective()
        self.risk_free_rate = risk_free_rate
        self.last_performance: tuple[float, float, float] | None = None
        self.last_note: str | None = None  # Why the last decision was what it was, if notable.

    def target_weights(self, window: pd.DataFrame) -> Weights:
        """Optimize over the window, enforcing max_positions against the eligible count."""
        constraints = self.constraints
        if constraints.max_positions != -1:
            constraints = dataclasses.replace(
                constraints, max_positions=min(constraints.max_positions, window.shape[1])
            )
        self.last_note = None
        try:
            weights, self.last_performance = compute_portfolio(
                window, self.objective, risk_free_rate=self.risk_free_rate, **constraints.as_kwargs()
            )
        except HoldCashAnswer as answer:
            # The model's answer, not a failure: sell into cash rather than keep stale positions.
            self.last_performance, self.last_note = answer.performance, str(answer)
            return {}
        except TargetUnreachable as err:
            # This window cannot meet the target (e.g. a volatility limit in a turbulent market): hold, and say why.
            raise StrategyError(str(err)) from err
        except (pypfopt_exceptions.OptimizationError, cvxpy.error.SolverError, ValueError) as err:
            raise StrategyError(f"optimization failed: {err}") from err
        return weights

    @property
    def name(self) -> str:
        """The objective's name, e.g. "max_sharpe", so results say what was optimized."""
        return str(self.objective.name)


MaxSharpeStrategy = OptimizerStrategy  # Former name, from when max-Sharpe was the only objective.


class EqualWeightStrategy:
    """1/N across every eligible asset. The fair benchmark for the same universe."""

    name = "equal_weight"

    def target_weights(self, window: pd.DataFrame) -> Weights:
        """Spread capital evenly."""
        assets = list(window.columns)
        if not assets:
            raise StrategyError("no eligible assets")
        return {asset: 1.0 / len(assets) for asset in assets}


class BuyAndHoldStrategy:
    """Buy fixed weights at the first rebalance and never trade again."""

    name = "buy_and_hold"

    def __init__(self, weights: Weights) -> None:
        """Store the weights to buy once."""
        self.weights = weights
        self._bought = False

    def target_weights(self, window: pd.DataFrame) -> Weights | None:
        """Return the weights the first time, then hold."""
        if self._bought:
            return None
        missing = [asset for asset in self.weights if asset not in window.columns]
        if missing:
            raise StrategyError(f"not yet tradable: {', '.join(missing)}")
        self._bought = True
        return dict(self.weights)
