"""Maximum-Sharpe portfolio optimization: validate a request, solve, describe the result.

Plain dict in (the JSON API body, or an equivalent built by a view or script),
plain dict out. The only inputs are the request and a MarketData snapshot, so
the same code runs in the web app, the CLI, tests, and the backtest.
"""
import dataclasses

import numpy as np
import pandas as pd
from pypfopt.efficient_frontier import EfficientFrontier

from wolvwealth.domain import trades
from wolvwealth.domain.errors import ValidationError
from wolvwealth.domain.market import MarketData

#: Risk-free rate used for the Sharpe ratio, matching PyPortfolioOpt's default.
RISK_FREE_RATE = 0.02
#: Default universe when the request names none: the 500 largest tickers.
DEFAULT_UNIVERSE_SIZE = 500
#: Weights below this are treated as zero after solving.
WEIGHT_THRESHOLD = 0.0005
#: Convex solver for the max-Sharpe program. cvxpy would otherwise pick OSQP,
#: a first-order method that hits its iteration limit and fails outright on
#: real price data once the universe passes ~400 tickers -- including the
#: default universe of 500. CLARABEL is an interior-point method that solves
#: the same problems in a fraction of the time and agrees with OSQP to ~1e-5
#: (far below WEIGHT_THRESHOLD) wherever OSQP does converge. It ships as a
#: required dependency of cvxpy, so it is always available.
SOLVER = "CLARABEL"


@dataclasses.dataclass
class OptimizationRequest:
    """A validated optimization request."""

    cash: float = 0.0
    holdings: dict = dataclasses.field(default_factory=dict)  # ticker -> shares
    universe: list = dataclasses.field(default_factory=list)
    max_weight: float = 1.0
    max_positions: int = None
    min_universal_weight: float = 0.0
    exclude_metrics: bool = False
    exclude_trades: bool = False


def parse_request(payload, market: MarketData) -> OptimizationRequest:
    """Validate a raw request dict against the market data.

    Every problem raises ValidationError with a message naming the field.
    """
    if not isinstance(payload, dict):
        raise ValidationError("Parse Error. Request body must be a JSON object.")
    request = OptimizationRequest(universe=market.top(DEFAULT_UNIVERSE_SIZE))
    _parse_cash(payload, request)
    _parse_universe(payload, request, market)
    _parse_holdings(payload, request, market)
    _parse_output_flags(payload, request)
    _parse_constraints(payload, request)
    return request


def _parse_cash(payload, request) -> None:
    if "initial_cash" not in payload:
        return
    cash = payload["initial_cash"]
    if not isinstance(cash, (int, float)) or isinstance(cash, bool):
        raise ValidationError("Invalid initial_cash. initial_cash must be a number.")
    if cash < 0:
        raise ValidationError("Invalid initial_cash. initial_cash must be greater than 0.")
    request.cash = round(cash, 2)


def _parse_universe(payload, request, market) -> None:
    if "universe" not in payload:
        return
    entries = payload["universe"]
    if not isinstance(entries, list):
        raise ValidationError("Invalid universe. universe must be a list.")
    if not entries:  # empty means "use the default"
        return
    try:
        request.universe = market.expand_universe(entries)
    except ValueError as err:
        raise ValidationError(f"Invalid universe. {err}" if "list of strings" in str(err) else str(err)) from err


def _parse_holdings(payload, request, market) -> None:
    if "initial_holdings" not in payload:
        return
    holdings = payload["initial_holdings"]
    if not isinstance(holdings, dict):
        raise ValidationError("Invalid initial_holdings. initial_holdings must be a dictionary.")
    cleaned = {}
    for ticker, shares in holdings.items():
        if not isinstance(ticker, str):
            raise ValidationError("Invalid initial_holdings. initial_holdings must be list of strings.")
        if ticker.upper() not in market:
            raise ValidationError(f"Invalid symbol in initial_holdings: {ticker}.")
        if not market.has_price(ticker.upper()):
            # Without a current price the position cannot be valued, and
            # valuing it anyway (last known price, or zero) would quietly put
            # a wrong number on someone's money. Say so instead.
            raise ValidationError(
                f"No current price for {ticker.upper()} in initial_holdings; "
                "it looks delisted or renamed. Remove it and try again."
            )
        if not isinstance(shares, (int, float)) or isinstance(shares, bool):
            raise ValidationError(
                "Invalid initial_holdings. initial_holdings must be a dictionary of strings to numbers."
            )
        if shares < 0:
            raise ValidationError("Invalid initial_holdings. Shares must be positive.")
        cleaned[ticker.upper()] = shares
    request.holdings = cleaned


def _parse_output_flags(payload, request) -> None:
    for field in ("exclude_metrics", "exclude_trades"):
        if field not in payload:
            continue
        if not isinstance(payload[field], bool):
            raise ValidationError(f"Invalid {field}. {field} must be a boolean.")
        setattr(request, field, payload[field])


def _parse_constraints(payload, request) -> None:
    if "constraints" not in payload:
        return
    constraints = payload["constraints"]
    if not isinstance(constraints, dict):
        raise ValidationError("Invalid constraints. constraints must be a dictionary.")
    if "max_weight" in constraints:
        value = constraints["max_weight"]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValidationError("Invalid max_weight. max_weight must be a number.")
        if not 0 <= value <= 1:
            raise ValidationError("Invalid max_weight. max_weight must be greater than 0 and less than 1.")
        request.max_weight = round(value, 2)
    if "max_positions" in constraints:
        value = constraints["max_positions"]
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValidationError("Invalid max_positions. max_positions must be an integer.")
        if value < 1:
            raise ValidationError("Invalid max_positions. max_positions must be greater than 0.")
        request.max_positions = min(value, len(request.universe))
        if request.max_positions * request.max_weight < 1:
            raise ValidationError(
                "Infeasible. max_positions * max_weight must be greater than or equal to 1 for full allocation."
            )
    if "min_universal_weight" in constraints:
        value = constraints["min_universal_weight"]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValidationError("Invalid min_universal_weight. min_universal_weight must be a number.")
        if not 0 <= value <= 1:
            raise ValidationError(
                "Invalid min_universal_weight. min_universal_weight must be greater than 0 and less than 1."
            )
        request.min_universal_weight = round(value, 2)
    if request.min_universal_weight > request.max_weight:
        raise ValidationError("Invalid constraints. min_weight must be less than max_weight.")


def _run_solver(mu, cov_matrix, max_weight: float, min_universal_weight: float) -> dict:
    """Maximize the Sharpe ratio under the weight bounds; return the cleaned weights."""
    try:
        frontier = EfficientFrontier(
            mu, cov_matrix, verbose=False, weight_bounds=(min_universal_weight, 1), solver=SOLVER
        )
        if max_weight != 1:
            frontier.add_constraint(lambda weights: weights <= max_weight)
        frontier.max_sharpe()
        return frontier.clean_weights(cutoff=WEIGHT_THRESHOLD)
    except Exception as err:  # pylint: disable=broad-except
        # Surface the real solver error; "Infeasible" alone is undiagnosable.
        raise ValidationError(f"Optimization Error. Check your inputs and constraints. {err}") from err


def max_sharpe_weights(request: OptimizationRequest, market: MarketData) -> dict:
    """Solved weights for this request, memoized on the market snapshot.

    The solve is ~90% of a request, and it depends on nothing but the prices
    and the three fields in the key below, so agents repeating a request get
    the stored answer. Cash, holdings and ``max_positions`` are deliberately
    absent from the key: they only scale or truncate the weights afterwards,
    so varying them still hits the same solve. Anything new that reaches the
    solver must be added to the key.

    Nothing is stored when the solver fails, so an infeasible request keeps
    reporting its own error rather than a remembered one.
    """
    key = ("weights", tuple(request.universe), request.max_weight, request.min_universal_weight)

    def compute():
        mu, cov_matrix = market.stats_for(request.universe)
        return _run_solver(mu, cov_matrix, request.max_weight, request.min_universal_weight)

    # Copied on the way out: callers rebuild the dict as they filter it, and a
    # cached value must survive that unchanged for the next request.
    return dict(market.solutions.get_or_compute(key, compute))


def _return_and_volatility(request: OptimizationRequest, market: MarketData, weights: dict) -> tuple:
    """Annual expected return and volatility of ``weights``, memoized on the snapshot.

    Both are functions of the weight vector alone, and the key determines
    that vector in full: the three fields that drive the solve, plus
    ``max_positions``, which truncates it. Cash and holdings scale the
    portfolio without changing its shape, so they are absent here too.

    Worth memoizing separately because slicing the covariance matrix for a
    large universe costs milliseconds, which dominates a request whose solve
    was already served from the cache.
    """
    key = (
        "metrics",
        tuple(request.universe),
        request.max_weight,
        request.min_universal_weight,
        request.max_positions,
    )

    def compute():
        mu, cov_matrix = market.stats_for(request.universe)
        vector = pd.Series(weights, dtype=float).reindex(mu.index, fill_value=0.0)
        return float(vector @ mu), float(np.sqrt(vector @ cov_matrix @ vector))

    return market.solutions.get_or_compute(key, compute)


def solve(request: OptimizationRequest, market: MarketData) -> dict:
    """Run the optimization and return the result dict (the API response shape)."""
    total_investment = request.cash + market.value(request.holdings)
    if total_investment == 0:
        raise ValidationError("Invalid input. Total investment value cannot be 0.")

    weights = max_sharpe_weights(request, market)
    if request.max_positions is not None:
        top = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)[: request.max_positions]
        total_weight = sum(weight for _, weight in top)
        weights = {asset: weight / total_weight for asset, weight in top}
    weights = {asset: weight for asset, weight in weights.items() if weight != 0}

    portfolio = {
        asset: {
            "shares": round(weight * total_investment / market.price(asset), 4),
            "value": round(weight * total_investment, 2),
            "percent_weight": round(weight * 100, 2),
        }
        for asset, weight in weights.items()
    }
    # as_of is the date of the prices every number here was computed from.
    # An agent about to place real orders needs it to decide whether the plan
    # is fresh enough to act on; without it the response looks like a quote.
    result = {"as_of": str(market.as_of), "optimized_portfolio": portfolio}
    if not request.exclude_trades:
        result["trades"] = trades.plan(
            request.holdings,
            {asset: row["shares"] for asset, row in portfolio.items()},
            market.latest_prices,
        )
    if not request.exclude_metrics:
        # Describe the portfolio actually returned, including the
        # max_positions truncation (ef.portfolio_performance() would
        # describe the untruncated solver output instead). Computed here
        # rather than above so `exclude_metrics` requests skip it entirely.
        expected_return, volatility = _return_and_volatility(request, market, weights)
        # Degenerate history (too few rows, flat prices) gives zero volatility; report
        # the Sharpe ratio as null rather than dividing by zero.
        sharpe = round((expected_return - RISK_FREE_RATE) / volatility, 2) if volatility > 0 else None
        result["metrics"] = {
            "portfolio_value": round(total_investment, 2),
            "expected_annual_return": round(expected_return, 3),
            "annual_volatility": round(volatility, 3),
            "sharpe_ratio": sharpe,
            "num_assets": len(weights),
        }
    return result


def optimize(payload, market: MarketData) -> dict:
    """Validate and solve in one call."""
    return solve(parse_request(payload, market), market)
