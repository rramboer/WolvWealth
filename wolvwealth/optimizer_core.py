"""Shared portfolio-optimization core.

Pure functions used by both the JSON API (wolvwealth.api.optimize) and the
backtesting utility (backtest.py) so the two cannot silently diverge.
"""

from collections.abc import Callable

import pandas as pd
from pypfopt import expected_returns, risk_models
from pypfopt.efficient_frontier import EfficientFrontier


def compute_max_sharpe_portfolio(
    prices: pd.DataFrame,
    *,
    max_weight: float = 1.0,
    min_universal_weight: float = 0.0,
    max_positions: int = -1,
    weight_threshold: float = 0.0005,
) -> tuple[dict[str, float], tuple[float, float, float]]:
    """Compute max-Sharpe portfolio weights for the given price history.

    Args:
        prices: historical prices, one column per asset.
        max_weight: maximum weight per asset (1.0 disables the constraint).
        min_universal_weight: minimum weight required for every asset.
        max_positions: keep only the N largest positions (-1 disables).
        weight_threshold: drop weights below this cutoff.

    Returns:
        (weights, performance) where weights maps asset -> nonzero weight and
        performance is (expected_return, annual_volatility, sharpe_ratio).

    Raises:
        pypfopt/cvxpy optimization errors when the problem is infeasible.
    """
    mu = expected_returns.mean_historical_return(prices)
    cov_matrix = risk_models.exp_cov(prices)
    ef = EfficientFrontier(mu, cov_matrix, verbose=False, weight_bounds=(min_universal_weight, 1))
    if max_weight != 1:
        ef.add_constraint(lambda weights: weights <= max_weight)
    ef.max_sharpe()
    cleaned_weights = dict(ef.clean_weights(cutoff=weight_threshold))
    if max_positions != -1:
        largest = sorted(cleaned_weights.items(), key=lambda item: item[1], reverse=True)[:max_positions]
        total_weight = sum(weight for _, weight in largest)
        if total_weight == 0:
            raise ValueError("No asset received a nonzero weight.")
        cleaned_weights = {asset: weight / total_weight for asset, weight in largest}
    cleaned_weights = {asset: weight for asset, weight in cleaned_weights.items() if weight != 0}
    return cleaned_weights, ef.portfolio_performance()


def build_portfolio_output(
    weights: dict[str, float],
    total_investment: float,
    price_of: Callable[[str], float],
    performance: tuple[float, float, float] | None = None,
) -> dict:
    """Convert optimized weights into the API/backtest output structure."""
    output = {"optimized_portfolio": {}}
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
