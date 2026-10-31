"""Maximum-Sharpe portfolio optimization: validate a request, solve, describe the result.

Plain dict in (the JSON API body, or an equivalent built by a view or script),
plain dict out. The only inputs are the request and a MarketData snapshot, so
the same code runs in the web app, the CLI, tests, and the backtest.
"""

import dataclasses

import cvxpy as cp
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
#: Slack allowed when checking a turnover limit against what the other
#: constraints already force; covers the solver's own tolerance.
TURNOVER_TOLERANCE = 1e-6
#: A held position the solver leaves within this much weight below its
#: current size is kept exactly as is. ``clean_weights`` rounds to five
#: decimals, so a position the solver did not touch otherwise comes back a
#: hair short and appears in the trade list as a sell of 0.0001 shares:
#: noise, but noise an agent would still place as an order.
DUST_WEIGHT = 1e-4
#: Convex solver for the max-Sharpe program. cvxpy would otherwise pick OSQP,
#: a first-order method that hits its iteration limit and fails outright on
#: real price data once the universe passes ~400 tickers -- including the
#: default universe of 500. CLARABEL is an interior-point method that solves
#: the same problems in a fraction of the time and agrees with OSQP to ~1e-5
#: (far below WEIGHT_THRESHOLD) wherever OSQP does converge. It ships as a
#: required dependency of cvxpy, so it is always available.
SOLVER = "CLARABEL"


@dataclasses.dataclass
class OptimizationRequest:  # pylint: disable=too-many-instance-attributes
    """A validated optimization request."""

    cash: float = 0.0
    holdings: dict = dataclasses.field(default_factory=dict)  # ticker -> shares
    universe: list = dataclasses.field(default_factory=list)
    max_weight: float = 1.0
    max_positions: int = None
    min_universal_weight: float = 0.0
    max_turnover: float = None  # fraction of portfolio value that may be sold; None = no limit
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
                f"No current price for {ticker.upper()} in initial_holdings: delisted, renamed, "
                "or missed by the last price refresh. Remove it, or run `wolvwealth refresh-data --full`."
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
    _parse_max_turnover(constraints, request)


def _parse_max_turnover(constraints, request) -> None:
    if "max_turnover" not in constraints:
        return
    value = constraints["max_turnover"]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError("Invalid max_turnover. max_turnover must be a number.")
    if not 0 <= value <= 1:
        raise ValidationError("Invalid max_turnover. max_turnover must be between 0 and 1.")
    # Nothing can sell more than the whole portfolio, so 1 is "no limit";
    # normalizing it to None lets such requests share the unconstrained solve.
    request.max_turnover = None if value >= 1 else round(value, 4)


def current_weights(request: OptimizationRequest, market: MarketData) -> dict:
    """Held ticker -> its share of the total investment (cash + holdings).

    Cash is part of the denominator, so with cash on hand these sum to less
    than one; the gap is what the optimizer can deploy without selling.
    """
    total = request.cash + market.value(request.holdings)
    return {ticker: shares * market.price(ticker) / total for ticker, shares in request.holdings.items() if shares > 0}


def turnover(current: dict, target: dict) -> float:
    """Fraction of portfolio value sold moving from ``current`` to ``target`` weights.

    Sells only (the SEC's "lesser of purchases and sales" convention, since
    purchases are sells plus deployed cash): buying with fresh cash is not
    churn, while selling a position to buy another is counted once.
    """
    return sum(max(weight - target.get(ticker, 0.0), 0.0) for ticker, weight in current.items())


def _keep_untouched_holdings(weights: dict, current: dict) -> dict:
    """Snap held positions left within ``DUST_WEIGHT`` below their current weight back onto it.

    The weight this adds is taken from the largest buy, so the total stays at
    one without shaving every other position, which would only mint new dust.
    """
    snapped = dict(weights)
    excess = 0.0
    for ticker, held in current.items():
        if ticker in snapped and 0 < held - snapped[ticker] <= DUST_WEIGHT:
            excess += held - snapped[ticker]
            snapped[ticker] = held
    if excess:
        buys = {ticker: weight - current.get(ticker, 0.0) for ticker, weight in snapped.items()}
        biggest = max(buys, key=buys.get)
        if buys[biggest] >= excess:
            snapped[biggest] -= excess
    return snapped


def _turnover_floor(request: OptimizationRequest, current: dict) -> tuple:
    """Turnover the other constraints force regardless of the solve, and why.

    Returns ``(fraction, reasons)``. Two things make a sale unavoidable:
    holding a ticker the universe does not contain (it cannot be kept), and
    holding more of a ticker than ``max_weight`` allows (the excess must go).
    Catching these here gives a message naming the cause; the solver alone
    would only say "infeasible".
    """
    in_universe = set(request.universe)
    outside = {ticker: weight for ticker, weight in current.items() if ticker not in in_universe}
    excess = {
        ticker: weight - request.max_weight
        for ticker, weight in current.items()
        if ticker in in_universe and weight > request.max_weight
    }
    reasons = []
    if outside:
        names = ", ".join(sorted(outside))
        reasons.append(f"{names} not being in the universe forces {sum(outside.values()):.1%} to be sold")
    if excess:
        names = ", ".join(sorted(excess))
        reasons.append(f"max_weight {request.max_weight} forces {sum(excess.values()):.1%} of {names} to be sold")
    return sum(outside.values()) + sum(excess.values()), reasons


def _check_turnover_feasible(request: OptimizationRequest, current: dict) -> None:
    if request.max_turnover is None:
        return
    floor, reasons = _turnover_floor(request, current)
    if floor > request.max_turnover + TURNOVER_TOLERANCE:
        raise ValidationError(
            f"Infeasible. max_turnover {request.max_turnover} is below the {floor:.1%} the other inputs already "
            f"force: {'; '.join(reasons)}. Raise max_turnover, or add the held tickers to the universe."
        )


def _run_solver(mu, cov_matrix, max_weight: float, min_universal_weight: float, turnover_limit: tuple = None) -> dict:
    """Maximize the Sharpe ratio under the weight bounds; return the cleaned weights.

    ``turnover_limit`` is ``(limit, current)`` with ``current`` the held
    weights (ticker -> fraction of total investment), or None for no limit.
    """
    try:
        frontier = EfficientFrontier(
            mu, cov_matrix, verbose=False, weight_bounds=(min_universal_weight, 1), solver=SOLVER
        )
        if max_weight != 1:
            frontier.add_constraint(lambda weights: weights <= max_weight)
        if turnover_limit is not None:
            frontier.add_constraint(_turnover_constraint(mu.index, *turnover_limit))
        frontier.max_sharpe()
        return frontier.clean_weights(cutoff=WEIGHT_THRESHOLD)
    except Exception as err:  # pylint: disable=broad-except
        # Surface the real solver error; "Infeasible" alone is undiagnosable.
        raise ValidationError(f"Optimization Error. Check your inputs and constraints. {err}") from err


def _turnover_constraint(tickers, limit: float, current: dict):
    """Build the cvxpy constraint capping the fraction of the portfolio sold at ``limit``.

    Sold value is ``sum(pos(current - w))`` over the universe, plus every
    held weight the universe does not contain (those positions have no
    variable and are sold in full).

    Written in a form that survives PyPortfolioOpt's max-Sharpe
    transformation. ``max_sharpe`` substitutes ``w = k * w_true`` and
    rewrites each constraint ``f(w) <= c`` as ``f(w) <= c * k`` -- scaling the
    constant side only. A plain ``pos(current - w)`` would then compare
    scaled weights against unscaled ones. Multiplying every constant by
    ``sum(w)`` instead (which the transformed problem pins to ``k``) makes the
    whole constraint homogeneous in ``k``, so dividing through recovers
    exactly ``sum(pos(current - w_true)) + outside <= limit``.
    """
    held = np.array([current.get(ticker, 0.0) for ticker in tickers])
    in_universe = set(tickers)
    outside = sum(weight for ticker, weight in current.items() if ticker not in in_universe)

    def constraint(weights):
        invested = cp.sum(weights)
        sold = cp.sum(cp.pos(held * invested - weights)) + outside * invested
        return sold <= limit

    return constraint


def _solver_inputs(request: OptimizationRequest, current: dict) -> tuple:
    """Return the turnover inputs that reach the solver, hashable, or None when unlimited."""
    if request.max_turnover is None:
        return None
    return request.max_turnover, tuple(sorted(current.items()))


def max_sharpe_weights(request: OptimizationRequest, market: MarketData, current: dict) -> dict:
    """Solved weights for this request, memoized on the market snapshot.

    The solve is ~90% of a request, and it depends on nothing but the prices
    and the fields in the key below, so agents repeating a request get the
    stored answer. Cash, holdings and ``max_positions`` are deliberately
    absent from the key unless ``max_turnover`` is set: by default they only
    scale or truncate the weights afterwards, so varying them still hits the
    same solve. A turnover limit makes the held weights a solver input, so
    they join the key only then. Anything new that reaches the solver must
    be added to the key.

    Nothing is stored when the solver fails, so an infeasible request keeps
    reporting its own error rather than a remembered one.
    """
    turnover_inputs = _solver_inputs(request, current)
    key = ("weights", tuple(request.universe), request.max_weight, request.min_universal_weight, turnover_inputs)

    def compute():
        mu, cov_matrix = market.stats_for(request.universe)
        limit = None if turnover_inputs is None else (request.max_turnover, current)
        return _run_solver(mu, cov_matrix, request.max_weight, request.min_universal_weight, limit)

    # Copied on the way out: callers rebuild the dict as they filter it, and a
    # cached value must survive that unchanged for the next request.
    return dict(market.solutions.get_or_compute(key, compute))


def _return_and_volatility(request: OptimizationRequest, market: MarketData, weights: dict, current: dict) -> tuple:
    """Annual expected return and volatility of ``weights``, memoized on the snapshot.

    Both are functions of the weight vector alone, and the key determines
    that vector in full: everything that drives the solve, plus
    ``max_positions``, which truncates it. Cash and holdings scale the
    portfolio without changing its shape, so they are absent here too,
    except through the turnover inputs when ``max_turnover`` is set.

    Worth memoizing separately because slicing the covariance matrix for a
    large universe costs milliseconds, which dominates a request whose solve
    was already served from the cache. ``_keep_untouched_holdings`` can move
    the vector by up to ``DUST_WEIGHT`` per holding without changing the key;
    that is far below the three decimals the metrics are reported to.
    """
    key = (
        "metrics",
        tuple(request.universe),
        request.max_weight,
        request.min_universal_weight,
        _solver_inputs(request, current),
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
    current = current_weights(request, market)
    _check_turnover_feasible(request, current)

    weights = max_sharpe_weights(request, market, current)
    if request.max_positions is not None:
        top = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)[: request.max_positions]
        total_weight = sum(weight for _, weight in top)
        weights = {asset: weight / total_weight for asset, weight in top}
    weights = {asset: weight for asset, weight in weights.items() if weight != 0}
    weights = _keep_untouched_holdings(weights, current)

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
        expected_return, volatility = _return_and_volatility(request, market, weights, current)
        # Degenerate history (too few rows, flat prices) gives zero volatility; report
        # the Sharpe ratio as null rather than dividing by zero.
        sharpe = round((expected_return - RISK_FREE_RATE) / volatility, 2) if volatility > 0 else None
        result["metrics"] = {
            "portfolio_value": round(total_investment, 2),
            "expected_annual_return": round(expected_return, 3),
            "annual_volatility": round(volatility, 3),
            "sharpe_ratio": sharpe,
            "num_assets": len(weights),
            # Measured on the returned portfolio, so it includes anything the
            # max_positions truncation sold beyond what the solver was allowed.
            "turnover": round(turnover(current, weights), 4),
        }
    return result


def optimize(payload, market: MarketData) -> dict:
    """Validate and solve in one call."""
    return solve(parse_request(payload, market), market)
