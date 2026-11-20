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

- max_sharpe: no asset's expected return beats the risk-free rate, or a
  binding max_weight caps every allowed portfolio's expected return at it.
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

Rebalancing. Given the current holdings (`current_weights`), the optimizer
rebalances from them instead of starting over, when there is something to
trade off: a turnover limit (`max_turnover`, the value sold as a fraction of
the portfolio, one-way as fund turnover is reported, so investing cash is
not turnover) or a trading cost (`transaction_cost_bps` of the value bought
and sold). Costs are charged against one year of expected return: a trade
must be expected to earn its cost back within a year. Every objective that
weighs return weighs costs (max_sharpe maximizes the Sharpe ratio net of
them; efficient_return's target is net of them); min_volatility ignores
return and so costs, while max_turnover limits its trading like any other
objective's. Holdings outside the assets being optimized must be sold. With
no holdings, or neither a limit nor a cost, the result is the same as
optimizing from scratch.

With a turnover limit, a hold-cash answer must also fit within it (moving to
cash sells everything); otherwise CashBlockedByTurnover says so. Constraints
that force more selling than the limit allows raise TurnoverInfeasible.
Both are InvalidOptimizationInput, with a limit that would work.
"""

import dataclasses
import math
from collections.abc import Callable, Mapping

import cvxpy as cp
import numpy as np
import pandas as pd
from pypfopt import exceptions as pypfopt_exceptions
from pypfopt import expected_returns, objective_functions, risk_models
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
TURNOVER_TOLERANCE = 1e-9
# Rebalancing problems are solved with Clarabel (interior point). cvxpy's default for quadratic programs, OSQP
# (first order), stops at its iteration limit on a 500-asset problem with a turnover limit; Clarabel takes ~20
# iterations. Problems without rebalancing keep pypfopt's default, so their answers are unchanged.
REBALANCING_SOLVER = cp.CLARABEL
TURNOVER_HINT_DECIMALS = 2  # max_turnover has the API's 2-decimal constraint precision.


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


class NoPortfolioBeatsRiskFreeRate(HoldCashAnswer):
    """Some asset beats the risk-free rate, but no portfolio that can actually be held does.

    What is reachable is capped by the weight bounds (a binding max_weight),
    and when rebalancing also by the turnover limit and trading costs.
    """

    def __init__(
        self,
        risk_free_rate: float,
        best_return: float,
        objective_label: str,
        *,
        from_holdings: bool = False,
        limited: bool = False,
        costs: bool = False,
    ) -> None:
        """Record the best reachable return (net of trading costs when rebalancing), for the explanation."""
        self.best_return = best_return
        reachable = "reachable from the current holdings" if from_holdings else "allowed by these constraints"
        within = " within max_turnover" if limited else ""
        after = " after trading costs" if costs else ""
        super().__init__(
            risk_free_rate,
            f"No portfolio {reachable}{within} is expected to beat the "
            f"{risk_free_rate:.1%} risk-free rate{after} (best: {best_return:.1%}), so the {objective_label} "
            "recommendation is to hold cash.",
        )


class CashBeatsRiskBudget(HoldCashAnswer):
    """Within the volatility limit, no portfolio is expected to beat the risk-free rate."""

    def __init__(
        self, risk_free_rate: float, target_volatility: float, best_return: float, trading_cost: float = 0.0
    ) -> None:
        """Record the limit and the best return within it (and its trading cost), for the explanation."""
        self.target_volatility = target_volatility
        self.best_return = best_return
        self.trading_cost = trading_cost
        after = f", after {trading_cost:.2%} of trading costs," if trading_cost else ""
        super().__init__(
            risk_free_rate,
            f"Within the {target_volatility:.1%} volatility limit, the highest expected annual return{after} is "
            f"{best_return - trading_cost:.1%}, which does not beat the {risk_free_rate:.1%} risk-free rate, so the "
            "target-volatility recommendation is to hold cash.",
        )


class CashBeatsUtility(HoldCashAnswer):
    """No portfolio's return after the risk penalty beats the risk-free rate."""

    def __init__(
        self,
        risk_free_rate: float,
        risk_aversion: float,
        best_return: float,
        penalty: float,
        trading_cost: float = 0.0,
    ) -> None:
        """Record the best portfolio's return, risk penalty and trading cost, for the explanation."""
        self.risk_aversion = risk_aversion
        self.best_return = best_return
        self.penalty = penalty
        self.trading_cost = trading_cost
        costs = f" and {trading_cost:.2%} of trading costs" if trading_cost else ""
        super().__init__(
            risk_free_rate,
            f"At risk aversion {risk_aversion:g}, the best portfolio's risk-adjusted return ({best_return:.1%} "
            f"expected, minus a {penalty:.1%} risk penalty{costs}, is {best_return - penalty - trading_cost:.1%}) "
            f"does not beat the {risk_free_rate:.1%} risk-free rate, so the max-utility recommendation is to "
            "hold cash.",
        )


class TargetUnreachable(InvalidOptimizationInput):
    """No portfolio of this universe under these constraints can meet the objective's target."""


class TurnoverInfeasible(InvalidOptimizationInput):
    """The constraints force selling more of the current holdings than max_turnover allows."""


class CashBlockedByTurnover(InvalidOptimizationInput):
    """The answer is to hold cash, but selling everything exceeds max_turnover."""

    def __init__(self, answer: HoldCashAnswer, sold: float, max_turnover: float) -> None:
        """Explain both the cash answer and the limit it breaks."""
        self.answer = answer
        hint = _ceil(sold, TURNOVER_HINT_DECIMALS)
        super().__init__(
            f"{answer} Moving to cash would sell {sold:.1%} of the portfolio, above max_turnover "
            f"({max_turnover:g}). Raise max_turnover to at least {hint:.{TURNOVER_HINT_DECIMALS}f} to move to cash."
        )


def _ceil(value: float, decimals: int) -> float:
    """Round up at `decimals`, so a hint of that precision still satisfies the bound (1/3 -> 0.34)."""
    return math.ceil(value * 10**decimals - 1e-9) / 10**decimals


def _floor(value: float, decimals: int) -> float:
    """Round down at `decimals`, so a hint of that precision is still reachable."""
    return math.floor(value * 10**decimals + 1e-9) / 10**decimals


@dataclasses.dataclass(frozen=True)
class _Rebalance:
    """Where a rebalance starts from, and what trading away from it costs or is limited to."""

    current: pd.Series  # Weight of each current holding in the portfolio's value; cash is the rest.
    cost_rate: float  # Cost per unit of value traded (bought or sold): transaction_cost_bps / 10,000.
    max_turnover: float | None  # Most of the portfolio's value that may be sold; None for no limit.

    @property
    def active(self) -> bool:
        """Whether the current holdings change the answer: something is held, and trading is limited or costs."""
        return bool((self.current > 0).any()) and (self.max_turnover is not None or self.cost_rate > 0)

    def split(self, assets: pd.Index) -> tuple[np.ndarray, float]:
        """Current weights of `assets`, and the total weight held outside them (which must be sold)."""
        inside = self.current.reindex(assets, fill_value=0.0).to_numpy(dtype=float)
        return inside, max(0.0, float(self.current.sum()) - float(inside.sum()))

    def _deltas(self, weights: Mapping[str, float]) -> pd.Series:
        target = pd.Series(weights, dtype=float)
        union = target.index.union(self.current.index)
        return target.reindex(union, fill_value=0.0) - self.current.reindex(union, fill_value=0.0)

    def traded(self, weights: Mapping[str, float]) -> float:
        """Value bought plus value sold, as a fraction of the portfolio, to reach `weights`."""
        return float(self._deltas(weights).abs().sum())

    def sold(self, weights: Mapping[str, float]) -> float:
        """Value sold, as a fraction of the portfolio (one-way turnover), to reach `weights`."""
        return float((-self._deltas(weights)).clip(lower=0.0).sum())


@dataclasses.dataclass(frozen=True)
class _Scope:
    """Which assets a solve covered, worded for a TargetUnreachable message."""

    description: str  # e.g. "any portfolio of this universe under these constraints"
    remedy: str = ""  # A further way out, appended as its own sentence.
    assets: str = "the universe"  # What holdings outside the solve are outside of.


_UNIVERSE_SCOPE = _Scope("any portfolio of this universe under these constraints")


def _kept_positions_scope(kept: list[str]) -> _Scope:
    """max_positions keeps the largest positions of the first solve, not the best set for every target."""
    return _Scope(
        f"a portfolio of the {len(kept)} largest positions max_positions kept ({', '.join(kept)})",
        " Or raise max_positions: it keeps the largest positions of the unrestricted portfolio, "
        "which are not always the assets that can reach the target.",
        f"the {len(kept)} position{'s' if len(kept) != 1 else ''} max_positions kept",
    )


def estimate_inputs(prices: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    """Annual expected returns and covariance from price history.

    The one place the estimators are chosen: mean historical returns and an
    exponentially weighted covariance (recent co-movement counts more).
    """
    return expected_returns.mean_historical_return(prices), exp_cov(prices)


EXP_COV_SPAN = 180  # pypfopt's default: about 9 months of trading days.
TRADING_DAYS_PER_YEAR = 252


def exp_cov(prices: pd.DataFrame, span: int = EXP_COV_SPAN, frequency: int = TRADING_DAYS_PER_YEAR) -> pd.DataFrame:
    """Annualized exponentially weighted covariance; pypfopt's risk_models.exp_cov, vectorized.

    pypfopt fills the matrix one pair at a time with a pandas ewm call, about
    125,000 calls for 500 assets (~50 s on the full price history). Each
    entry is the last value of `((X - X.mean()) * (Y - Y.mean())).ewm(span).mean()`,
    which with pandas' defaults (adjust=True, ignore_na=False) is the
    weighted average of the pair's co-movements over the days both assets
    have returns, with weight (1 - alpha) ** (days before the last row).
    That is two matrix products: the weighted co-movements, and per pair
    the total weight of the days they share, so assets with different
    histories (IPOs) are handled as pypfopt does. A pair that shares no day
    is NaN, as there.
    """
    returns = expected_returns.returns_from_prices(prices)
    values = returns.to_numpy(dtype=float)
    present = ~np.isnan(values)
    demeaned = np.where(present, values - np.nanmean(values, axis=0), 0.0)
    alpha = 2.0 / (span + 1.0)
    weights = (1.0 - alpha) ** np.arange(len(values) - 1, -1, -1, dtype=float)  # 1 on the last row
    shared_weight = present.T.astype(float) @ (present * weights[:, None])
    with np.errstate(invalid="ignore", divide="ignore"):
        covariance = (demeaned.T @ (demeaned * weights[:, None])) / shared_weight
    covariance[shared_weight == 0] = np.nan
    matrix = pd.DataFrame(covariance * frequency, index=prices.columns, columns=prices.columns)
    return risk_models.fix_nonpositive_semidefinite(matrix, "spectral")


def compute_portfolio(
    prices: pd.DataFrame,
    objective: Objective | None = None,
    *,
    max_weight: float = 1.0,
    min_universal_weight: float = 0.0,
    max_positions: int = -1,
    weight_threshold: float = 0.0005,
    max_turnover: float | None = None,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    current_weights: Mapping[str, float] | None = None,
    transaction_cost_bps: float = 0.0,
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
        max_turnover: most of the portfolio's value that may be sold to get
            from `current_weights` to the answer (None: no limit).
        risk_free_rate: what cash earns; used for the Sharpe ratio and the hold-cash decision.
        current_weights: the current holdings as asset -> fraction of the
            portfolio's value (cash is the rest), possibly including assets
            outside `prices`, which must then be sold. See the module docstring.
        transaction_cost_bps: cost of trading, in basis points of the value bought and sold.

    Returns:
        (weights, performance) where weights maps asset -> nonzero weight,
        summing to 1, and performance is (expected_return, annual_volatility,
        sharpe_ratio) for exactly those weights, before trading costs.

    Raises:
        HoldCashAnswer when cash beats every portfolio under the objective
        (see the module docstring); TargetUnreachable, TurnoverInfeasible or
        CashBlockedByTurnover when the request cannot be met;
        pypfopt/cvxpy optimization errors when the solver fails; ValueError
        when the price data cannot be optimized (e.g. NaN columns).
    """
    objective = objective or Objective()
    rebalance = _Rebalance(
        pd.Series(dict(current_weights or {}), dtype=float), transaction_cost_bps / 10_000, max_turnover
    )
    try:
        return _compute(
            prices,
            objective,
            min_universal_weight,
            max_weight,
            max_positions,
            weight_threshold,
            risk_free_rate,
            rebalance if rebalance.active else None,
        )
    except HoldCashAnswer as answer:
        sold = rebalance.sold({})
        if max_turnover is not None and sold > max_turnover + TURNOVER_TOLERANCE:
            raise CashBlockedByTurnover(answer, sold, max_turnover) from answer
        raise


def _compute(
    prices: pd.DataFrame,
    objective: Objective,
    min_weight: float,
    max_weight: float,
    max_positions: int,
    weight_threshold: float,
    risk_free_rate: float,
    rebalance: _Rebalance | None,
) -> tuple[dict[str, float], Performance]:
    mu, cov_matrix = estimate_inputs(prices)
    if rebalance is not None:
        _require_feasible_turnover(rebalance, mu.index, min_weight, max_weight, _UNIVERSE_SCOPE)
    _check_cash_beats_every_portfolio(objective, mu, risk_free_rate, min_weight, max_weight, rebalance)

    def solve(assets: list[str], scope: _Scope) -> dict[str, float]:
        return _solve(
            objective,
            mu[assets],
            cov_matrix.loc[assets, assets],
            min_weight,
            max_weight,
            weight_threshold,
            risk_free_rate,
            scope,
            rebalance,
        )

    weights = solve(list(mu.index), _UNIVERSE_SCOPE)
    if max_positions != -1 and len(weights) > max_positions:
        largest = [asset for asset, _ in sorted(weights.items(), key=lambda item: item[1], reverse=True)]
        kept = largest[:max_positions]
        scope = _kept_positions_scope(kept)
        if rebalance is not None:
            _require_feasible_turnover(rebalance, pd.Index(kept), min_weight, max_weight, scope)
        weights = solve(kept, scope)
    weights = _normalize(weights)
    performance = portfolio_performance(weights, mu, cov_matrix, risk_free_rate)
    trading_cost = rebalance.cost_rate * rebalance.traded(weights) if rebalance is not None else 0.0
    _check_cash_beats_portfolio(objective, performance, risk_free_rate, trading_cost)
    return weights, performance


def compute_max_sharpe_portfolio(prices: pd.DataFrame, **kwargs: float) -> tuple[dict[str, float], Performance]:
    """compute_portfolio with the max_sharpe objective. Kept for existing callers."""
    return compute_portfolio(prices, Objective(ObjectiveName.MAX_SHARPE), **kwargs)


def _check_cash_beats_every_portfolio(
    objective: Objective,
    mu: pd.Series,
    risk_free_rate: float,
    min_weight: float,
    max_weight: float,
    rebalance: _Rebalance | None,
) -> None:
    """Raise a HoldCashAnswer when cash wins before any solve is needed.

    Moving to cash is not charged its own trading costs here: the
    comparison is between what the reachable portfolios are expected to earn
    after the costs of reaching them, and the risk-free rate.
    """
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
    if rebalance is not None:
        best_net = highest_net_return(mu, min_weight, max_weight, rebalance)
        if best_net <= risk_free_rate:
            raise NoPortfolioBeatsRiskFreeRate(
                risk_free_rate,
                best_net,
                OBJECTIVE_LABELS[objective.name],
                from_holdings=True,
                limited=rebalance.max_turnover is not None,
                costs=rebalance.cost_rate > 0,
            )
        return
    # Some asset beats the rate, but a binding max_weight can cap every allowed portfolio below it; pypfopt's
    # max_sharpe then fails as "infeasible" although the answer is simply to hold cash.
    best = highest_reachable_return(mu, min_weight, max_weight)
    if best <= risk_free_rate:
        raise NoPortfolioBeatsRiskFreeRate(risk_free_rate, best, OBJECTIVE_LABELS[objective.name])


def _check_cash_beats_portfolio(
    objective: Objective, performance: Performance, risk_free_rate: float, trading_cost: float = 0.0
) -> None:
    """Raise a HoldCashAnswer when the optimized portfolio itself does not beat cash on the objective's terms."""
    expected_return, volatility, _ = performance
    if objective.name == ObjectiveName.EFFICIENT_RISK and expected_return - trading_cost <= risk_free_rate:
        raise CashBeatsRiskBudget(risk_free_rate, objective.target_volatility, expected_return, trading_cost)
    if objective.name == ObjectiveName.MAX_UTILITY:
        penalty = 0.5 * objective.risk_aversion * volatility**2  # pypfopt's utility: return - risk_aversion/2 * var
        if expected_return - penalty - trading_cost <= risk_free_rate:
            raise CashBeatsUtility(risk_free_rate, objective.risk_aversion, expected_return, penalty, trading_cost)


def _frontier(
    mu: pd.Series,
    cov_matrix: pd.DataFrame,
    min_weight: float,
    max_weight: float,
    rebalance: _Rebalance | None = None,
) -> EfficientFrontier:
    """A fresh long-only, fully-invested frontier with the per-asset bounds and any turnover limit applied."""
    solver = REBALANCING_SOLVER if rebalance is not None else None
    ef = EfficientFrontier(mu, cov_matrix, verbose=False, weight_bounds=(min_weight, 1), solver=solver)
    if max_weight != 1:
        ef.add_constraint(lambda weights: weights <= max_weight)
    if rebalance is not None and rebalance.max_turnover is not None:
        current, outside = rebalance.split(mu.index)
        limit = rebalance.max_turnover
        ef.add_constraint(lambda weights: cp.sum(cp.pos(current - weights)) + outside <= limit)
    return ef


def _add_trading_cost(ef: EfficientFrontier, mu: pd.Series, rebalance: _Rebalance | None) -> None:
    """Subtract the cost of trading from the current holdings from the objective (in units of annual return)."""
    if rebalance is not None and rebalance.cost_rate > 0:
        current, _ = rebalance.split(mu.index)  # Selling outside holdings costs the same whatever the answer.
        ef.add_objective(objective_functions.transaction_cost, w_prev=current, k=rebalance.cost_rate)


def _solve(
    objective: Objective,
    mu: pd.Series,
    cov_matrix: pd.DataFrame,
    min_weight: float,
    max_weight: float,
    weight_threshold: float,
    risk_free_rate: float,
    scope: _Scope = _UNIVERSE_SCOPE,
    rebalance: _Rebalance | None = None,
) -> dict[str, float]:
    """Run one optimization for `objective` over the given assets and return the cleaned nonzero weights."""
    if rebalance is not None and objective.name == ObjectiveName.MAX_SHARPE:
        raw = _max_sharpe_rebalancing(mu, cov_matrix, min_weight, max_weight, risk_free_rate, rebalance)
        return _clean(raw, mu.index, weight_threshold)
    ef = _frontier(mu, cov_matrix, min_weight, max_weight, rebalance)
    match objective.name:
        case ObjectiveName.MAX_SHARPE:
            ef.max_sharpe(risk_free_rate=risk_free_rate)
        case ObjectiveName.MIN_VOLATILITY:
            ef.min_volatility()  # Ignores return, so ignores trading costs; max_turnover still applies.
        case ObjectiveName.MAX_UTILITY:
            _add_trading_cost(ef, mu, rebalance)
            ef.max_quadratic_utility(risk_aversion=objective.risk_aversion)
        case ObjectiveName.EFFICIENT_RETURN:
            target = _reachable_return_target(objective.target_return, mu, min_weight, max_weight, scope, rebalance)
            _require_net_return(ef, mu, target, rebalance)
            ef.efficient_return(target)
        case ObjectiveName.EFFICIENT_RISK:
            _add_trading_cost(ef, mu, rebalance)
            ef = _efficient_risk(
                objective.target_volatility, mu, cov_matrix, min_weight, max_weight, ef, scope, rebalance
            )
        case _:  # pragma: no cover - Objective validates the name.
            raise ValueError(f"Unsupported objective: {objective.name}")
    cleaned = ef.clean_weights(cutoff=weight_threshold)
    return {asset: weight for asset, weight in cleaned.items() if weight > 0}


def _clean(raw: np.ndarray, assets: pd.Index, cutoff: float) -> dict[str, float]:
    """pypfopt's clean_weights for a solve done outside it: drop weights below `cutoff`, round to 5 places."""
    weights = np.where(np.abs(raw) < cutoff, 0.0, raw).round(5)
    return {asset: float(weight) for asset, weight in zip(assets, weights, strict=True) if weight > 0}


def _max_sharpe_rebalancing(
    mu: pd.Series,
    cov_matrix: pd.DataFrame,
    min_weight: float,
    max_weight: float,
    risk_free_rate: float,
    rebalance: _Rebalance,
) -> np.ndarray:
    """Maximize the Sharpe ratio net of trading costs, within any turnover limit.

    pypfopt's max_sharpe substitutes y = kappa * w to make the problem
    convex, but rescales constraints as `expr <= constant * kappa` and does
    not rescale extra objectives, which would leave the current weights
    unscaled in |w - current|. Here every term is scaled: with
    traded = |y - kappa * current| + kappa * outside,

        minimize   y' S y
        subject to (mu - rf)' y - cost_rate * traded >= 1,  sum(y) = kappa,
                   min_weight * kappa <= y <= max_weight * kappa,
                   sum(pos(kappa * current - y)) + kappa * outside <= max_turnover * kappa,

    and w = y / kappa maximizes (mu'w - rf - cost_rate * traded(w)) / sigma(w).
    The caller has checked that some reachable portfolio's net return beats rf.
    """
    current, outside = rebalance.split(mu.index)
    y = cp.Variable(len(mu))
    kappa = cp.Variable(nonneg=True)
    traded = cp.norm1(y - kappa * current) + kappa * outside
    constraints = [
        cp.sum(y) == kappa,
        y >= min_weight * kappa,
        y <= max_weight * kappa,
        (mu.to_numpy(dtype=float) - risk_free_rate) @ y - rebalance.cost_rate * traded >= 1,
    ]
    if rebalance.max_turnover is not None:
        constraints.append(cp.sum(cp.pos(kappa * current - y)) + kappa * outside <= rebalance.max_turnover * kappa)
    problem = cp.Problem(cp.Minimize(cp.quad_form(y, cov_matrix.to_numpy(dtype=float), assume_PSD=True)), constraints)
    problem.solve(solver=REBALANCING_SOLVER)
    if problem.status not in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE) or not kappa.value or kappa.value <= 0:
        raise pypfopt_exceptions.OptimizationError(f"Max-Sharpe rebalancing solve failed: {problem.status}.")
    return np.asarray(y.value, dtype=float) / float(kappa.value)


def _require_net_return(ef: EfficientFrontier, mu: pd.Series, target: float, rebalance: _Rebalance | None) -> None:
    """With trading costs, efficient_return's target is net of them."""
    if rebalance is None or rebalance.cost_rate == 0:
        return
    current, outside = rebalance.split(mu.index)
    returns, cost_rate = mu.to_numpy(dtype=float), rebalance.cost_rate
    ef.add_constraint(lambda w: returns @ w - cost_rate * (cp.norm1(w - current) + outside) >= target)


def _require_feasible_turnover(
    rebalance: _Rebalance, assets: pd.Index, min_weight: float, max_weight: float, scope: _Scope
) -> None:
    """Raise TurnoverInfeasible if the constraints force selling more than max_turnover.

    The least selling is exact without a solver: holdings outside `assets`
    are sold entirely, holdings above max_weight are trimmed to it, and if
    lifting every asset to min_universal_weight would overfill the budget,
    the excess is sold too. Investing cash is not selling.
    """
    if rebalance.max_turnover is None:
        return
    current, outside = rebalance.split(assets)
    trimmed = np.maximum(current - max_weight, 0.0)
    overfill = max(0.0, float(np.clip(current, min_weight, max_weight).sum()) - 1.0)
    needed = outside + float(trimmed.sum()) + overfill
    if needed <= rebalance.max_turnover + TURNOVER_TOLERANCE:
        return
    outside_names = [str(a) for a in rebalance.current.index if a not in assets and rebalance.current[a] > 0]
    reasons = []
    if outside_names:
        reasons.append(f"selling holdings outside {scope.assets}: {_names(outside_names)}")
    if trimmed.sum() > 0:
        reasons.append(f"trimming holdings above max_weight: {_names([str(a) for a in assets[trimmed > 0]])}")
    if overfill > 0:
        reasons.append("making room for min_universal_weight in every asset")
    whole_universe = scope.assets == _UNIVERSE_SCOPE.assets
    remedy = f", or add {_names(outside_names)} to the universe" if outside_names and whole_universe else ""
    if not whole_universe:
        remedy = ", or raise max_positions so fewer current holdings have to be sold"
    hint = min(1.0, _ceil(needed, TURNOVER_HINT_DECIMALS))
    raise TurnoverInfeasible(
        f"Infeasible max_turnover. These constraints require selling at least {needed:.1%} of the portfolio "
        f"({'; '.join(reasons)}), above max_turnover ({rebalance.max_turnover:g}). Raise max_turnover to at "
        f"least {hint:.{TURNOVER_HINT_DECIMALS}f}{remedy}."
    )


def _names(names: list[str], limit: int = 5) -> str:
    """'A, B, C' or 'A, B, C, D, E and 3 more'."""
    shown = ", ".join(names[:limit])
    return f"{shown} and {len(names) - limit} more" if len(names) > limit else shown


def _efficient_risk(
    target_volatility: float,
    mu: pd.Series,
    cov_matrix: pd.DataFrame,
    min_weight: float,
    max_weight: float,
    ef: EfficientFrontier,
    scope: _Scope,
    rebalance: _Rebalance | None = None,
) -> EfficientFrontier:
    """Maximize return subject to volatility <= target, after checking the target is reachable.

    pypfopt's own check compares the target with the unconstrained (short-
    selling) minimum volatility, which is lower than what a long-only
    portfolio can reach, so targets in between failed in the solver with no
    useful message. The calmest portfolio under the real constraints
    (including any turnover limit) is computed first instead.
    """
    calmest = _frontier(mu, cov_matrix, min_weight, max_weight, rebalance)
    calmest.min_volatility()
    w = calmest.weights
    lowest = float(np.sqrt(w @ cov_matrix.to_numpy() @ w))
    if target_volatility < lowest - VOLATILITY_TOLERANCE:
        hint = _ceil(lowest, HINT_DECIMALS)
        limited = rebalance is not None and rebalance.max_turnover is not None
        raise TargetUnreachable(
            f"Unreachable target_volatility. The lowest expected annual volatility {scope.description} "
            f"can reach{' within max_turnover' if limited else ''} is {lowest:.2%}, above the "
            f"{target_volatility:.2%} target. Raise target_volatility to at least {hint:.{HINT_DECIMALS}f}"
            f"{', raise max_turnover' if limited else ''}, or use min_volatility.{scope.remedy}"
        )
    if target_volatility <= lowest + VOLATILITY_TOLERANCE:
        return calmest  # The limit admits only the calmest portfolio.
    ef.efficient_risk(target_volatility)
    return ef


def _reachable_return_target(
    target_return: float,
    mu: pd.Series,
    min_weight: float,
    max_weight: float,
    scope: _Scope,
    rebalance: _Rebalance | None = None,
) -> float:
    """The target to hand the solver, or TargetUnreachable if no portfolio within the bounds can return it."""
    limited = rebalance is not None and rebalance.max_turnover is not None
    costs = rebalance is not None and rebalance.cost_rate > 0
    if rebalance is None:
        highest = highest_reachable_return(mu, min_weight, max_weight)
    else:
        highest = highest_net_return(mu, min_weight, max_weight, rebalance)
    if target_return > highest + RETURN_TOLERANCE:
        hint = _floor(highest, HINT_DECIMALS)
        raise TargetUnreachable(
            f"Unreachable target_return. The highest expected annual return{' after trading costs' if costs else ''} "
            f"{scope.description} can reach{' within max_turnover' if limited else ''} is {highest:.2%}, below the "
            f"{target_return:.2%} target. Lower target_return to at most {hint:.{HINT_DECIMALS}f}"
            f"{', raise max_turnover' if limited else ''}, or use efficient_risk or max_sharpe.{scope.remedy}"
        )
    return min(target_return, highest - RETURN_BOUNDARY_MARGIN)


def highest_net_return(mu: pd.Series, min_weight: float, max_weight: float, rebalance: _Rebalance) -> float:
    """Highest expected return, net of the cost of trading there, of a portfolio reachable within max_turnover.

    A small linear program (the greedy fill of highest_reachable_return no
    longer applies once trading costs and a turnover limit depend on the
    current holdings). NaN returns make the answer unknowable: infinity.
    """
    if mu.isna().any():
        return math.inf
    current, outside = rebalance.split(mu.index)
    w = cp.Variable(len(mu))
    constraints = [cp.sum(w) == 1, w >= min_weight, w <= max_weight]
    if rebalance.max_turnover is not None:
        constraints.append(cp.sum(cp.pos(current - w)) + outside <= rebalance.max_turnover)
    traded = cp.norm1(w - current) + outside
    problem = cp.Problem(cp.Maximize(mu.to_numpy(dtype=float) @ w - rebalance.cost_rate * traded), constraints)
    problem.solve(solver=REBALANCING_SOLVER)
    if problem.status not in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE):
        raise pypfopt_exceptions.OptimizationError(f"Highest-return solve failed: {problem.status}.")
    return float(problem.value)


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


DUST_TRADE_FRACTION = 1e-4  # Changes smaller than 0.01% of the portfolio (or a cent) are not worth a trade.


def plan_trades(
    weights: Mapping[str, float],
    total_value: float,
    held_values: Mapping[str, float],
    fee_rate: float = 0.0,
) -> tuple[dict[str, float], float, float]:
    """Dollar values to hold after trading from `held_values` toward `weights`, the cash left, and the fees.

    Sells are made exactly as decided, so the value sold (the turnover the
    optimizer limited) is what it chose. Fees are paid from the purchases:
    every buy is scaled by the same factor so that buys plus all fees fit in
    the cash plus the proceeds of the sales. A change smaller than
    DUST_TRADE_FRACTION of the portfolio (solver noise, or a holding the
    optimizer kept) is not traded; the holding stays as it is.
    """
    dust = max(0.01, DUST_TRADE_FRACTION * total_value)
    assets = list(dict.fromkeys([*weights, *held_values]))
    held = {asset: float(held_values.get(asset, 0.0)) for asset in assets}
    changes = {asset: weights.get(asset, 0.0) * total_value - held[asset] for asset in assets}
    changes = {asset: change for asset, change in changes.items() if abs(change) >= dust}
    sold = sum(-change for change in changes.values() if change < 0)
    buys = {asset: change for asset, change in changes.items() if change > 0}
    budget = total_value - sum(held.values()) + sold * (1 - fee_rate)  # Cash after the sales and their fees.
    wanted = sum(buys.values()) * (1 + fee_rate)
    scale = min(1.0, max(0.0, budget / wanted)) if wanted > 0 else 0.0
    values = dict(held)
    for asset, change in changes.items():
        values[asset] += change * scale if change > 0 else change
    bought = sum(buys.values()) * scale
    fees = fee_rate * (sold + bought)
    cash = max(0.0, budget - bought * (1 + fee_rate))
    return values, cash, fees


def build_portfolio_output(
    weights: dict[str, float],
    total_investment: float,
    price_of: Callable[[str], float],
    performance: Performance | None = None,
    notes: list[str] | None = None,
    objective: Objective | None = None,
    current_shares: Mapping[str, float] | None = None,
    transaction_cost_bps: float = 0.0,
) -> dict:
    """Convert optimized weights into the API output structure.

    `total_investment` is the portfolio's whole value: cash plus
    `current_shares` at `price_of`. `optimized_portfolio` is what to hold
    after trading (see plan_trades: sells exactly as decided, fees of
    `transaction_cost_bps` paid from the purchases, changes too small to be
    worth a trade skipped), so its weights can sit a hair below the
    optimizer's when there are fees. `cash` is what is left uninvested: zero
    (to the cent) for a normal portfolio, everything after fees for a
    hold-cash answer (no weights). `trades` lists what to buy and sell, and
    `rebalance` totals it; its `turnover` is the value sold as a fraction of
    the portfolio, as max_turnover measures it. `objective`, when given, is
    echoed so the answer says what it optimized.
    """
    held = {asset: float(shares) for asset, shares in (current_shares or {}).items() if shares > 0}
    held_values = {asset: shares * price_of(asset) for asset, shares in held.items()}
    values, cash_value, fees = plan_trades(weights, total_investment, held_values, transaction_cost_bps / 10_000)
    output = {
        "optimized_portfolio": {},
        "cash": {
            "value": round(cash_value, 2),
            "percent_weight": round(cash_value / total_investment * 100, 2) if total_investment > 0 else 0.0,
        },
        "notes": list(notes or []),
        "trades": {},
    }
    if objective is not None:
        output["objective"] = objective.to_dict()
    for asset in weights:
        output["optimized_portfolio"][asset] = {
            "shares": round(values[asset] / price_of(asset), 4),
            "value": round(values[asset], 2),
            "percent_weight": round(values[asset] / total_investment * 100, 2),
        }
    bought = sold = 0.0
    for asset, value in values.items():
        change = value - held_values.get(asset, 0.0)
        if change == 0:
            continue
        bought, sold = (bought + change, sold) if change > 0 else (bought, sold - change)
        # Selling out is exactly the shares held; otherwise the change in value at today's price.
        shares = held[asset] if value == 0 and asset in held else abs(change) / price_of(asset)
        output["trades"][asset] = {
            "action": "buy" if change > 0 else "sell",
            "shares": round(shares, 4),
            "value": round(abs(change), 2),
        }
    output["rebalance"] = {
        "bought": round(bought, 2),
        "sold": round(sold, 2),
        "turnover": round(sold / total_investment, 4) if total_investment > 0 else 0.0,
        "transaction_cost": round(fees, 2),
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
