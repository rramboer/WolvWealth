"""Shared portfolio-optimization core.

Pure functions used by both the JSON API (wolvwealth.api.optimize) and the
backtester (wolvwealth.backtest) so the two cannot silently diverge.

An optimization has three separable parts, kept separate here so each can
grow on its own: estimating the inputs (expected returns and covariance,
`estimate_inputs`), the objective (what to maximize or minimize, an
`Objective`), and the constraints (applied identically under every
objective).

Cash. Portfolios are fully invested in stocks, and the only alternative is
holding everything in cash at the risk-free rate. The optimizer recommends
cash only when cash beats every stock portfolio on the objective's own
terms; then it raises a HoldCashAnswer, a valid result rather than an error:

- max_sharpe: no asset's expected return beats the risk-free rate.
- efficient_risk: no portfolio within the volatility limit is expected to
  beat the risk-free rate, which cash earns with no volatility at all.
- efficient_return: the return target is at or below the risk-free rate, so
  cash meets it with no volatility.
- max_utility: no portfolio's return after the risk penalty beats the
  risk-free rate (cash pays no penalty).
- min_volatility: never. Cash would trivially be the calmest holding; this
  objective asks for the calmest *invested* portfolio.

A target that no portfolio can reach (a volatility limit below the calmest
portfolio, a return target above the highest reachable return) raises
TargetUnreachable, an InvalidOptimizationInput whose message says which
target would work.
"""

import dataclasses
import math
from collections.abc import Callable

import numpy as np
import pandas as pd
from pypfopt import expected_returns, risk_models
from pypfopt.efficient_frontier import EfficientFrontier

from wolvwealth.optimizer_params import InvalidOptimizationInput, Objective, ObjectiveName

DEFAULT_RISK_FREE_RATE = 0.02  # Matches pypfopt's max_sharpe default.

Performance = tuple[float, float, float]  # (expected_return, annual_volatility, sharpe_ratio)

# How each objective's recommendation is named in explanations.
OBJECTIVE_LABELS = {
    ObjectiveName.MAX_SHARPE: "max-Sharpe",
    ObjectiveName.MIN_VOLATILITY: "minimum-volatility",
    ObjectiveName.EFFICIENT_RISK: "target-volatility",
    ObjectiveName.EFFICIENT_RETURN: "target-return",
    ObjectiveName.MAX_UTILITY: "max-utility",
}

# A volatility target this close to the calmest portfolio's volatility is that portfolio; asking the
# solver for "return subject to vol <= target" right at the boundary only invites numerical failure.
VOLATILITY_TOLERANCE = 1e-5
RETURN_TOLERANCE = 1e-9
# The solver's maximum return sits up to ~1e-9 below the exact one, so pypfopt refuses a target at the exact
# maximum. A target this close to it is solved this far below it: 0.0001% a year, nothing economically.
RETURN_BOUNDARY_MARGIN = 1e-6
HINT_DECIMALS = 3  # Targets in hints are annual decimals to 0.1%, e.g. 0.143.


class HoldCashAnswer(Exception):
    """Under the model, holding cash beats every stock portfolio on the objective's own terms.

    This is a valid result, not an error in the request. Callers turn it
    into a hold-cash answer. Deliberately not a ValueError, so generic error
    handling cannot swallow it as a failure.
    """

    def __init__(self, risk_free_rate: float, message: str) -> None:
        """Record the rate cash earns and the explanation."""
        self.risk_free_rate = risk_free_rate
        super().__init__(message)

    @property
    def performance(self) -> Performance:
        """Expected performance of the cash answer: the risk-free return with no volatility."""
        return self.risk_free_rate, 0.0, 0.0


class NoAssetBeatsRiskFreeRate(HoldCashAnswer):
    """No asset's expected return exceeds the risk-free rate, so no stock portfolio can either."""

    def __init__(
        self, risk_free_rate: float, best_asset: str, best_return: float, objective_label: str = "max-Sharpe"
    ) -> None:
        """Record the rate and the closest asset, for the explanation."""
        self.best_asset = best_asset
        self.best_return = best_return
        super().__init__(
            risk_free_rate,
            f"No asset's expected annual return beats the {risk_free_rate:.1%} risk-free rate "
            f"(best: {best_asset} at {best_return:.1%}), so the {objective_label} recommendation is to hold cash.",
        )


class CashMeetsReturnTarget(HoldCashAnswer):
    """The return target is at or below the risk-free rate, so cash meets it with no volatility."""

    def __init__(self, risk_free_rate: float, target_return: float) -> None:
        """Record the rate and the target, for the explanation."""
        self.target_return = target_return
        super().__init__(
            risk_free_rate,
            f"Cash earning the {risk_free_rate:.1%} risk-free rate already meets the {target_return:.1%} "
            "return target with no volatility, so the target-return recommendation is to hold cash.",
        )


class CashBeatsRiskBudget(HoldCashAnswer):
    """Within the volatility limit, no portfolio is expected to beat the risk-free rate."""

    def __init__(self, risk_free_rate: float, target_volatility: float, best_return: float) -> None:
        """Record the limit and the best return within it, for the explanation."""
        self.target_volatility = target_volatility
        self.best_return = best_return
        super().__init__(
            risk_free_rate,
            f"Within the {target_volatility:.1%} volatility limit, the highest expected annual return is "
            f"{best_return:.1%}, which does not beat the {risk_free_rate:.1%} risk-free rate, so the "
            "target-volatility recommendation is to hold cash.",
        )


class CashBeatsUtility(HoldCashAnswer):
    """No portfolio's return after the risk penalty beats the risk-free rate."""

    def __init__(self, risk_free_rate: float, risk_aversion: float, best_return: float, penalty: float) -> None:
        """Record the best portfolio's return and risk penalty, for the explanation."""
        self.risk_aversion = risk_aversion
        self.best_return = best_return
        self.penalty = penalty
        super().__init__(
            risk_free_rate,
            f"At risk aversion {risk_aversion:g}, the best portfolio's risk-adjusted return ({best_return:.1%} "
            f"expected, minus a {penalty:.1%} risk penalty, is {best_return - penalty:.1%}) does not beat the "
            f"{risk_free_rate:.1%} risk-free rate, so the max-utility recommendation is to hold cash.",
        )


class TargetUnreachable(InvalidOptimizationInput):
    """No portfolio of this universe under these constraints can meet the objective's target."""


@dataclasses.dataclass(frozen=True)
class _Scope:
    """Which assets a solve covered, worded for a TargetUnreachable message."""

    description: str  # e.g. "any portfolio of this universe under these constraints"
    remedy: str = ""  # A further way out, appended as its own sentence.


_UNIVERSE_SCOPE = _Scope("any portfolio of this universe under these constraints")


def _kept_positions_scope(kept: list[str]) -> _Scope:
    """max_positions keeps the largest positions of the first solve, not the best set for every target."""
    return _Scope(
        f"a portfolio of the {len(kept)} largest positions max_positions kept ({', '.join(kept)})",
        " Or raise max_positions: it keeps the largest positions of the unrestricted portfolio, "
        "which are not always the assets that can reach the target.",
    )


def estimate_inputs(prices: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    """Annual expected returns and covariance from price history.

    The one place the estimators are chosen: mean historical returns and an
    exponentially weighted covariance (recent co-movement counts more).
    """
    return expected_returns.mean_historical_return(prices), risk_models.exp_cov(prices)


def compute_portfolio(
    prices: pd.DataFrame,
    objective: Objective | None = None,
    *,
    max_weight: float = 1.0,
    min_universal_weight: float = 0.0,
    max_positions: int = -1,
    weight_threshold: float = 0.0005,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> tuple[dict[str, float], Performance]:
    """Compute portfolio weights for `objective` (default max_sharpe) over the given price history.

    Args:
        prices: historical prices, one column per asset.
        objective: what to optimize; see Objective.
        max_weight: maximum weight per asset (1.0 disables the constraint).
        min_universal_weight: minimum weight required for every asset.
        max_positions: keep only the N largest positions (-1 disables). The
            portfolio is re-optimized over those N assets so that every
            constraint and target still holds and the reported performance
            describes the returned weights, not the pre-truncation portfolio.
        weight_threshold: drop weights below this cutoff.
        risk_free_rate: what cash earns; used for the Sharpe ratio and the hold-cash decision.

    Returns:
        (weights, performance) where weights maps asset -> nonzero weight,
        summing to 1, and performance is (expected_return, annual_volatility,
        sharpe_ratio) for exactly those weights.

    Raises:
        HoldCashAnswer when cash beats every portfolio under the objective
        (see the module docstring); TargetUnreachable when the objective's
        target cannot be met; pypfopt/cvxpy optimization errors when the
        solver fails; ValueError when the price data cannot be optimized
        (e.g. NaN columns).
    """
    objective = objective or Objective()
    mu, cov_matrix = estimate_inputs(prices)
    _check_cash_beats_every_asset(objective, mu, risk_free_rate)

    def solve(assets: list[str], scope: _Scope) -> dict[str, float]:
        return _solve(
            objective,
            mu[assets],
            cov_matrix.loc[assets, assets],
            min_universal_weight,
            max_weight,
            weight_threshold,
            risk_free_rate,
            scope,
        )

    weights = solve(list(mu.index), _UNIVERSE_SCOPE)
    if max_positions != -1 and len(weights) > max_positions:
        largest = [asset for asset, _ in sorted(weights.items(), key=lambda item: item[1], reverse=True)]
        kept = largest[:max_positions]
        weights = solve(kept, _kept_positions_scope(kept))
    weights = _normalize(weights)
    performance = portfolio_performance(weights, mu, cov_matrix, risk_free_rate)
    _check_cash_beats_portfolio(objective, performance, risk_free_rate)
    return weights, performance


def compute_max_sharpe_portfolio(prices: pd.DataFrame, **kwargs: float) -> tuple[dict[str, float], Performance]:
    """compute_portfolio with the max_sharpe objective. Kept for existing callers."""
    return compute_portfolio(prices, Objective(ObjectiveName.MAX_SHARPE), **kwargs)


def _check_cash_beats_every_asset(objective: Objective, mu: pd.Series, risk_free_rate: float) -> None:
    """Raise a HoldCashAnswer when cash wins before any solve is needed."""
    if objective.name == ObjectiveName.EFFICIENT_RETURN:
        if objective.target_return <= risk_free_rate:
            raise CashMeetsReturnTarget(risk_free_rate, objective.target_return)
        return
    if objective.name == ObjectiveName.MIN_VOLATILITY:
        return
    # A portfolio's expected return is a weighted average of its assets', so if none beats cash, no mix can.
    finite_mu = mu.dropna()
    if not finite_mu.empty and not (finite_mu > risk_free_rate).any():
        best = finite_mu.idxmax()
        raise NoAssetBeatsRiskFreeRate(
            risk_free_rate, str(best), float(finite_mu[best]), OBJECTIVE_LABELS[objective.name]
        )


def _check_cash_beats_portfolio(objective: Objective, performance: Performance, risk_free_rate: float) -> None:
    """Raise a HoldCashAnswer when the optimized portfolio itself does not beat cash on the objective's terms."""
    expected_return, volatility, _ = performance
    if objective.name == ObjectiveName.EFFICIENT_RISK and expected_return <= risk_free_rate:
        raise CashBeatsRiskBudget(risk_free_rate, objective.target_volatility, expected_return)
    if objective.name == ObjectiveName.MAX_UTILITY:
        penalty = 0.5 * objective.risk_aversion * volatility**2  # pypfopt's utility: return - risk_aversion/2 * var
        if expected_return - penalty <= risk_free_rate:
            raise CashBeatsUtility(risk_free_rate, objective.risk_aversion, expected_return, penalty)


def _frontier(mu: pd.Series, cov_matrix: pd.DataFrame, min_weight: float, max_weight: float) -> EfficientFrontier:
    """A fresh long-only, fully-invested frontier with the per-asset bounds applied."""
    ef = EfficientFrontier(mu, cov_matrix, verbose=False, weight_bounds=(min_weight, 1))
    if max_weight != 1:
        ef.add_constraint(lambda weights: weights <= max_weight)
    return ef


def _solve(
    objective: Objective,
    mu: pd.Series,
    cov_matrix: pd.DataFrame,
    min_weight: float,
    max_weight: float,
    weight_threshold: float,
    risk_free_rate: float,
    scope: _Scope = _UNIVERSE_SCOPE,
) -> dict[str, float]:
    """Run one optimization for `objective` over the given assets and return the cleaned nonzero weights."""
    ef = _frontier(mu, cov_matrix, min_weight, max_weight)
    match objective.name:
        case ObjectiveName.MAX_SHARPE:
            ef.max_sharpe(risk_free_rate=risk_free_rate)
        case ObjectiveName.MIN_VOLATILITY:
            ef.min_volatility()
        case ObjectiveName.MAX_UTILITY:
            ef.max_quadratic_utility(risk_aversion=objective.risk_aversion)
        case ObjectiveName.EFFICIENT_RETURN:
            ef.efficient_return(_reachable_return_target(objective.target_return, mu, min_weight, max_weight, scope))
        case ObjectiveName.EFFICIENT_RISK:
            ef = _efficient_risk(objective.target_volatility, mu, cov_matrix, min_weight, max_weight, ef, scope)
        case _:  # pragma: no cover - Objective validates the name.
            raise ValueError(f"Unsupported objective: {objective.name}")
    cleaned = ef.clean_weights(cutoff=weight_threshold)
    return {asset: weight for asset, weight in cleaned.items() if weight > 0}


def _efficient_risk(
    target_volatility: float,
    mu: pd.Series,
    cov_matrix: pd.DataFrame,
    min_weight: float,
    max_weight: float,
    ef: EfficientFrontier,
    scope: _Scope,
) -> EfficientFrontier:
    """Maximize return subject to volatility <= target, after checking the target is reachable.

    pypfopt's own check compares the target with the unconstrained (short-
    selling) minimum volatility, which is lower than what a long-only
    portfolio can reach, so targets in between failed in the solver with no
    useful message. The calmest portfolio under the real constraints is
    computed first instead.
    """
    calmest = _frontier(mu, cov_matrix, min_weight, max_weight)
    calmest.min_volatility()
    w = calmest.weights
    lowest = float(np.sqrt(w @ cov_matrix.to_numpy() @ w))
    if target_volatility < lowest - VOLATILITY_TOLERANCE:
        hint = math.ceil(lowest * 10**HINT_DECIMALS - 1e-9) / 10**HINT_DECIMALS
        raise TargetUnreachable(
            f"Unreachable target_volatility. The lowest expected annual volatility {scope.description} "
            f"can reach is {lowest:.2%}, above the {target_volatility:.2%} target. Raise target_volatility "
            f"to at least {hint:.{HINT_DECIMALS}f}, or use min_volatility.{scope.remedy}"
        )
    if target_volatility <= lowest + VOLATILITY_TOLERANCE:
        return calmest  # The limit admits only the calmest portfolio.
    ef.efficient_risk(target_volatility)
    return ef


def _reachable_return_target(
    target_return: float, mu: pd.Series, min_weight: float, max_weight: float, scope: _Scope
) -> float:
    """The target to hand the solver, or TargetUnreachable if no portfolio within the bounds can return it."""
    highest = highest_reachable_return(mu, min_weight, max_weight)
    if target_return > highest + RETURN_TOLERANCE:
        hint = math.floor(highest * 10**HINT_DECIMALS + 1e-9) / 10**HINT_DECIMALS
        raise TargetUnreachable(
            f"Unreachable target_return. The highest expected annual return {scope.description} "
            f"can reach is {highest:.2%}, below the {target_return:.2%} target. Lower target_return "
            f"to at most {hint:.{HINT_DECIMALS}f}, or use efficient_risk or max_sharpe.{scope.remedy}"
        )
    return min(target_return, highest - RETURN_BOUNDARY_MARGIN)


def highest_reachable_return(mu: pd.Series, min_weight: float, max_weight: float) -> float:
    """Highest expected return of a fully-invested portfolio with every weight in [min_weight, max_weight].

    Exact without a solver: give every asset the minimum, then fill the rest
    of the budget into the highest-returning assets up to the cap. NaN
    returns make the answer unknowable, so this reports infinity and leaves
    the failure to the solver's error handling.
    """
    if mu.isna().any():
        return math.inf
    values = mu.to_numpy(dtype=float)
    weights = np.full(len(values), float(min_weight))  # float: an int 0 would truncate every fill to 0
    remaining = 1.0 - min_weight * len(values)
    room = min(max_weight, 1.0) - min_weight
    for index in np.argsort(-values):
        if remaining <= 0:
            break
        add = min(room, remaining)
        weights[index] += add
        remaining -= add
    return float(weights @ values)


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    """Scale weights to sum to exactly 1 so no capital is silently left unallocated."""
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("No asset received a nonzero weight.")
    return {asset: weight / total for asset, weight in weights.items()}


def portfolio_performance(
    weights: dict[str, float],
    mu: pd.Series,
    cov_matrix: pd.DataFrame,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> Performance:
    """Expected annual return, annual volatility and Sharpe ratio of the given weights."""
    assets = list(weights)
    w = np.array([weights[asset] for asset in assets])
    expected_return = float(w @ mu[assets].to_numpy())
    volatility = float(np.sqrt(w @ cov_matrix.loc[assets, assets].to_numpy() @ w))
    sharpe = (expected_return - risk_free_rate) / volatility if volatility > 0 else float("nan")
    return expected_return, volatility, sharpe


def build_portfolio_output(
    weights: dict[str, float],
    total_investment: float,
    price_of: Callable[[str], float],
    performance: Performance | None = None,
    notes: list[str] | None = None,
    objective: Objective | None = None,
) -> dict:
    """Convert optimized weights into the API output structure.

    `cash` is whatever the weights leave unallocated: zero for a normal
    portfolio (weights sum to 1), everything for a hold-cash answer (no weights).
    `objective`, when given, is echoed so the answer says what it optimized.
    """
    cash_weight = max(0.0, 1.0 - sum(weights.values()))
    output = {
        "optimized_portfolio": {},
        "cash": {"value": round(cash_weight * total_investment, 2), "percent_weight": round(cash_weight * 100, 2)},
        "notes": list(notes or []),
    }
    if objective is not None:
        output["objective"] = objective.to_dict()
    for asset, weight in weights.items():
        output["optimized_portfolio"][asset] = {
            "shares": round(weight * total_investment / price_of(asset), 4),
            "value": round(weight * total_investment, 2),
            "percent_weight": round(weight * 100, 2),
        }
    if performance is not None:
        expected_return, volatility, sharpe = performance
        output["metrics"] = {
            "portfolio_value": round(total_investment, 2),
            "expected_annual_return": round(expected_return, 3),
            "annual_volatility": round(volatility, 3),
            "sharpe_ratio": round(sharpe, 2),
            "num_assets": len(weights),
        }
    return output
