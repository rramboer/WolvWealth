"""Shared portfolio-optimization core.

Pure functions used by both the JSON API (wolvwealth.api.optimize) and the
backtester (wolvwealth.backtest) so the two cannot silently diverge.
"""

from collections.abc import Callable

import numpy as np
import pandas as pd
from pypfopt import expected_returns, risk_models
from pypfopt.efficient_frontier import EfficientFrontier

DEFAULT_RISK_FREE_RATE = 0.02  # Matches pypfopt's max_sharpe default.

Performance = tuple[float, float, float]  # (expected_return, annual_volatility, sharpe_ratio)


class NoAssetBeatsRiskFreeRate(Exception):
    """No asset's expected return exceeds the risk-free rate, so the max-Sharpe answer is cash.

    This is a valid result, not an error in the request: under the model,
    every risky portfolio has a Sharpe ratio at or below zero, and the best
    allocation is to hold the risk-free asset. Callers turn it into a
    hold-cash answer. Deliberately not a ValueError, so generic error handling
    cannot swallow it as a failure.
    """

    def __init__(self, risk_free_rate: float, best_asset: str, best_return: float) -> None:
        """Record the rate and the closest asset, for the explanation."""
        self.risk_free_rate = risk_free_rate
        self.best_asset = best_asset
        self.best_return = best_return
        super().__init__(
            f"No asset's expected annual return beats the {risk_free_rate:.1%} risk-free rate "
            f"(best: {best_asset} at {best_return:.1%}), so the max-Sharpe recommendation is to hold cash."
        )

    @property
    def performance(self) -> Performance:
        """Expected performance of the cash answer: the risk-free return with no volatility."""
        return self.risk_free_rate, 0.0, 0.0


def compute_max_sharpe_portfolio(
    prices: pd.DataFrame,
    *,
    max_weight: float = 1.0,
    min_universal_weight: float = 0.0,
    max_positions: int = -1,
    weight_threshold: float = 0.0005,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> tuple[dict[str, float], Performance]:
    """Compute max-Sharpe portfolio weights for the given price history.

    Args:
        prices: historical prices, one column per asset.
        max_weight: maximum weight per asset (1.0 disables the constraint).
        min_universal_weight: minimum weight required for every asset.
        max_positions: keep only the N largest positions (-1 disables). The
            portfolio is re-optimized over those N assets so that max_weight
            still holds and the reported performance describes the returned
            weights, not the pre-truncation portfolio.
        weight_threshold: drop weights below this cutoff.
        risk_free_rate: used for the Sharpe ratio.

    Returns:
        (weights, performance) where weights maps asset -> nonzero weight,
        summing to 1, and performance is (expected_return, annual_volatility,
        sharpe_ratio) for exactly those weights.

    Raises:
        NoAssetBeatsRiskFreeRate when no asset's expected return exceeds
        risk_free_rate (the answer is then to hold cash); pypfopt/cvxpy
        optimization errors when the solver fails; ValueError when the price
        data cannot be optimized (e.g. NaN columns).
    """
    mu = expected_returns.mean_historical_return(prices)
    finite_mu = mu.dropna()
    if not finite_mu.empty and not (finite_mu > risk_free_rate).any():
        best = finite_mu.idxmax()
        raise NoAssetBeatsRiskFreeRate(risk_free_rate, str(best), float(finite_mu[best]))
    cov_matrix = risk_models.exp_cov(prices)
    weights = _solve(mu, cov_matrix, max_weight, min_universal_weight, weight_threshold, risk_free_rate)
    if max_positions != -1 and len(weights) > max_positions:
        largest = [asset for asset, _ in sorted(weights.items(), key=lambda item: item[1], reverse=True)]
        keep = largest[:max_positions]
        weights = _solve(
            mu[keep], cov_matrix.loc[keep, keep], max_weight, min_universal_weight, weight_threshold, risk_free_rate
        )
    weights = _normalize(weights)
    return weights, portfolio_performance(weights, mu, cov_matrix, risk_free_rate)


def _solve(
    mu: pd.Series,
    cov_matrix: pd.DataFrame,
    max_weight: float,
    min_universal_weight: float,
    weight_threshold: float,
    risk_free_rate: float,
) -> dict[str, float]:
    """Run one max-Sharpe optimization and return the cleaned nonzero weights."""
    ef = EfficientFrontier(mu, cov_matrix, verbose=False, weight_bounds=(min_universal_weight, 1))
    if max_weight != 1:
        ef.add_constraint(lambda weights: weights <= max_weight)
    ef.max_sharpe(risk_free_rate=risk_free_rate)
    cleaned = ef.clean_weights(cutoff=weight_threshold)
    return {asset: weight for asset, weight in cleaned.items() if weight > 0}


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
) -> dict:
    """Convert optimized weights into the API output structure.

    `cash` is whatever the weights leave unallocated: zero for a normal
    portfolio (weights sum to 1), everything for a hold-cash answer (no weights).
    """
    cash_weight = max(0.0, 1.0 - sum(weights.values()))
    output = {
        "optimized_portfolio": {},
        "cash": {"value": round(cash_weight * total_investment, 2), "percent_weight": round(cash_weight * 100, 2)},
        "notes": list(notes or []),
    }
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
