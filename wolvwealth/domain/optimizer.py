"""Maximum-Sharpe portfolio optimization: validate a request, solve, describe the result.

Plain dict in (the JSON API body, or an equivalent built by a view or script),
plain dict out. The only inputs are the request and a MarketData snapshot, so
the same code runs in the web app, the CLI, tests, and the backtest.
"""

import dataclasses
import math

import cvxpy as cp
import numpy as np
import pandas as pd
from pypfopt.efficient_frontier import EfficientFrontier

from wolvwealth.domain import constraints as constraint_rules
from wolvwealth.domain import trades
from wolvwealth.domain.errors import DataError, ValidationError
from wolvwealth.domain.market import MIN_HISTORY_DAYS, MarketData
from wolvwealth.domain.numbers import MAX_AMOUNT, is_number

#: Annual risk-free rate assumed when the request names none: zero, because
#: the app cannot know where someone's uninvested money sits. A savings
#: account, T-bills, a bond ETF and a checking account all pay differently,
#: so the rate belongs to the portfolio and its owner, and a guess applied
#: silently would misstate every Sharpe ratio. The result says when this
#: fallback was used (``risk_free_rate.note``). The same rate is used both
#: to solve for the max-Sharpe portfolio and to report its ratio, and it is
#: always passed to the solver explicitly: PyPortfolioOpt's own default was
#: 0.02 through 1.5.x and 0.0 from 1.6.0, both allowed by pyproject's pin.
DEFAULT_RISK_FREE_RATE = 0.0
#: Where the rate in a request came from, for the response. The service
#: layer sets "portfolio" or "tbill" when it filled the rate in itself.
RATE_SOURCES = ("request", "portfolio", "tbill", "default")
#: Shown whenever the fallback rate was used.
DEFAULT_RATE_NOTE = (
    "No risk-free rate was given, so 0% was assumed: the Sharpe ratio treats uninvested money as earning "
    "nothing. Set the rate your cash actually earns (savings, T-bills, a bond fund) on the portfolio, or "
    "pass risk_free_rate with the request."
)
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
    cash_reserve: float = 0.0  # fraction of portfolio value left in cash, uninvested
    #: Days of prices a ticker needs to be in the universe. Shapes the universe
    #: and the kept positions, both of which are in the memo keys, so it is
    #: deliberately absent from them itself: two requests that resolve to the
    #: same ticker list and kept positions share a solve.
    min_history_days: int = MIN_HISTORY_DAYS
    #: Held tickers with fewer than ``min_history_days`` of prices, each at
    #: its current weight (fraction of the total portfolio). The optimizer
    #: has no statistics it can trust for them, so it neither sizes nor
    #: sells them: they are kept exactly as held and the rest of the money
    #: is optimized around them, as a cash reserve is. A solver input, since
    #: they shrink what the solver may deploy, so they are in the memo keys.
    unoptimized: dict = dataclasses.field(default_factory=dict)
    #: Ticker -> days of history, for the ones in ``unoptimized`` (for the messages).
    unoptimized_days: dict = dataclasses.field(default_factory=dict)

    def invested_fraction(self) -> float:
        """Return the share of the portfolio the optimizer is allowed to deploy.

        Every weight in a request or a response is a fraction of the *total*
        portfolio, cash reserve included -- "no stock above 10%" should not
        quietly mean 10% of the invested part. The solver works on weights
        that sum to one, so its bounds are divided by this and its answer is
        multiplied back. Positions kept as they are take their share off the
        top in the same way as the reserve.
        """
        return 1.0 - self.cash_reserve - sum(self.unoptimized.values())

    def solver_positions(self):
        """Return how many positions the solver may choose: ``max_positions`` less the ones kept as they are."""
        if self.max_positions is None:
            return None
        return self.max_positions - len(self.unoptimized)

    risk_free_rate: float = DEFAULT_RISK_FREE_RATE
    risk_free_rate_source: str = "default"
    exclude_metrics: bool = False
    exclude_trades: bool = False


def parse_request(payload, market: MarketData) -> OptimizationRequest:
    """Validate a raw request dict against the market data.

    Every problem raises ValidationError with a message naming the field.
    """
    if not isinstance(payload, dict):
        raise ValidationError("Parse Error. Request body must be a JSON object.")
    request = OptimizationRequest()
    # Before the universe: the default universe and every alias are filtered by it.
    _parse_min_history_days(payload, request)
    request.universe = market.top(DEFAULT_UNIVERSE_SIZE, request.min_history_days)
    _parse_cash(payload, request)
    _parse_risk_free_rate(payload, request)
    # Holdings before the universe: a held ticker under the minimum history
    # is kept as it is, so naming it in the universe must not be refused.
    _parse_holdings(payload, request, market)
    _parse_universe(payload, request, market)
    if not request.universe:
        _raise_empty_universe(request, market, explicit="min_history_days" in payload)
    _set_unoptimized(request, market)
    _parse_output_flags(payload, request)
    _parse_constraints(payload, request)
    # Field-by-field validation above; how the fields interact is one concern
    # in one module, checked once everything is known.
    constraint_rules.check(request)
    return request


def _raise_empty_universe(request, market, explicit: bool) -> None:
    """Say why nothing is left to optimize over: no prices at all, or none with enough history.

    A minimum the request set is the request's problem (ValidationError);
    the default minimum finding nothing means the cache itself is too short
    (DataError), which no change to the request should be asked to fix.
    """
    if not market.tradeable:
        raise DataError(
            "No ticker in the list has a current price, so there is nothing to optimize over. "
            "Run `wolvwealth refresh-data --full` to download prices for the whole list."
        )
    longest = max(market.history_days[ticker] for ticker in market.tradeable)
    message = (
        f"No tradeable ticker has {request.min_history_days} days of price history (the longest in the cache "
        f"is {longest} days), so there is nothing to optimize over."
    )
    if explicit:
        raise ValidationError(f"Invalid min_history_days. {message} Pass a lower min_history_days.")
    raise DataError(f"{message} Run `wolvwealth refresh-data --full` to download the whole history.")


def _parse_min_history_days(payload, request) -> None:
    if "min_history_days" not in payload:
        return
    value = payload["min_history_days"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError("Invalid min_history_days. min_history_days must be an integer (trading days).")
    if value < 2:
        raise ValidationError(
            "Invalid min_history_days. min_history_days must be at least 2: a return needs two prices."
        )
    request.min_history_days = value


def _parse_cash(payload, request) -> None:
    if "initial_cash" not in payload:
        return
    cash = payload["initial_cash"]
    if not is_number(cash):
        raise ValidationError("Invalid initial_cash. initial_cash must be a number.")
    if cash < 0:
        raise ValidationError("Invalid initial_cash. initial_cash must be greater than 0.")
    if cash > MAX_AMOUNT:
        raise ValidationError(f"Invalid initial_cash. initial_cash must be at most {MAX_AMOUNT:,.0f}.")
    request.cash = round(cash, 2)


def _parse_risk_free_rate(payload, request) -> None:
    if "risk_free_rate" not in payload:
        return
    value = payload["risk_free_rate"]
    if not is_number(value):
        raise ValidationError("Invalid risk_free_rate. risk_free_rate must be a number.")
    if not -1 < value < 1:
        # The common slip is writing 4 for 4%; say so rather than reporting a range.
        raise ValidationError("Invalid risk_free_rate. risk_free_rate is a decimal fraction: 0.04 means 4%.")
    request.risk_free_rate = round(value, 6)
    request.risk_free_rate_source = "request"


def _parse_universe(payload, request, market) -> None:
    if "universe" not in payload:
        return
    entries = payload["universe"]
    if not isinstance(entries, list):
        raise ValidationError("Invalid universe. universe must be a list.")
    if not entries:  # empty means "use the default"
        return
    try:
        request.universe = market.expand_universe(entries, request.min_history_days, held=request.holdings)
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
        if not is_number(shares):
            raise ValidationError(
                "Invalid initial_holdings. initial_holdings must be a dictionary of strings to numbers."
            )
        if shares < 0:
            raise ValidationError("Invalid initial_holdings. Shares must be positive.")
        if shares > MAX_AMOUNT:
            raise ValidationError(f"Invalid initial_holdings. {ticker.upper()} has more than {MAX_AMOUNT:,.0f} shares.")
        cleaned[ticker.upper()] = shares
    request.holdings = cleaned


def _set_unoptimized(request, market) -> None:
    """Record the held tickers the minimum history excludes, at their current weights.

    Selling them would act on the same absence of statistics that keeps
    them out of the universe; the one thing the optimizer knows about such
    a position is its size today, so that is what it keeps. Lowering
    ``min_history_days`` to their count turns them back into ordinary
    tickers: optimized if the universe names them, sold if it does not.
    """
    total = request.cash + market.value(request.holdings)
    if total == 0:
        return
    for ticker, shares in request.holdings.items():
        days = market.history_days[ticker]
        if shares > 0 and days < request.min_history_days:
            request.unoptimized[ticker] = shares * market.price(ticker) / total
            request.unoptimized_days[ticker] = days
    for ticker in request.unoptimized:
        if ticker in request.universe:
            request.universe.remove(ticker)


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
        if not is_number(value):
            raise ValidationError("Invalid max_weight. max_weight must be a number.")
        # Stored to two decimals, so anything below 0.005 would become a cap of zero.
        if not 0.005 <= value <= 1:
            raise ValidationError("Invalid max_weight. max_weight must be at least 0.01 and at most 1.")
        request.max_weight = round(value, 2)
    if "max_positions" in constraints:
        value = constraints["max_positions"]
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValidationError("Invalid max_positions. max_positions must be an integer.")
        if value < 1:
            raise ValidationError("Invalid max_positions. max_positions must be greater than 0.")
        request.max_positions = min(value, len(request.universe) + len(request.unoptimized))
    if "min_universal_weight" in constraints:
        value = constraints["min_universal_weight"]
        if not is_number(value):
            raise ValidationError("Invalid min_universal_weight. min_universal_weight must be a number.")
        if not 0 <= value <= 1:
            raise ValidationError(
                "Invalid min_universal_weight. min_universal_weight must be greater than 0 and less than 1."
            )
        request.min_universal_weight = round(value, 2)
    if "cash_reserve" in constraints:
        value = constraints["cash_reserve"]
        if not is_number(value):
            raise ValidationError("Invalid cash_reserve. cash_reserve must be a number.")
        if not 0 <= value < 1:
            raise ValidationError(
                "Invalid cash_reserve. cash_reserve is the fraction of the portfolio to keep in cash: "
                "at least 0 and below 1 (keeping everything in cash leaves nothing to optimize)."
            )
        request.cash_reserve = round(value, 2)
    _parse_max_turnover(constraints, request)


def _parse_max_turnover(constraints, request) -> None:
    if "max_turnover" not in constraints:
        return
    value = constraints["max_turnover"]
    if not is_number(value):
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

    Returns ``(fraction, reasons)``. Three things make selling unavoidable:
    holding a ticker the universe does not contain (it cannot be kept),
    holding more of a ticker than ``max_weight`` allows (the excess must go),
    and a ``cash_reserve`` larger than the cash already held (the shortfall
    can only come from sales). Catching these here gives a message naming
    the cause; the solver alone would only say "infeasible".

    The forced sales and the cash shortfall do not add: proceeds from a
    position that had to go anyway can be kept as the reserve. The floor is
    whichever is larger.
    """
    in_universe = set(request.universe)
    outside = {ticker: weight for ticker, weight in current.items() if ticker not in in_universe}
    excess = {
        ticker: weight - request.max_weight
        for ticker, weight in current.items()
        if ticker in in_universe and weight > request.max_weight
    }
    held_cash = max(0.0, 1.0 - sum(current.values()))
    cash_shortfall = max(0.0, request.cash_reserve - held_cash)
    reasons = []
    if outside:
        names = ", ".join(sorted(outside))
        reasons.append(f"{names} not being in the universe forces {sum(outside.values()):.1%} to be sold")
    if excess:
        names = ", ".join(sorted(excess))
        reasons.append(f"max_weight {request.max_weight} forces {sum(excess.values()):.1%} of {names} to be sold")
    if cash_shortfall > 0:
        reasons.append(
            f"cash_reserve {request.cash_reserve} requires {cash_shortfall:.1%} more cash than is held, "
            "which only selling can raise"
        )
    return max(sum(outside.values()) + sum(excess.values()), cash_shortfall), reasons


def _check_turnover_feasible(request: OptimizationRequest, current: dict) -> None:
    if request.max_turnover is None:
        return
    floor, reasons = _turnover_floor(request, current)
    if floor > request.max_turnover + TURNOVER_TOLERANCE:
        ways_out = "Raise max_turnover, or add the held tickers to the universe."
        if request.cash_reserve > 0:
            ways_out = "Raise max_turnover, lower cash_reserve, or add the held tickers to the universe."
        raise ValidationError(
            f"Infeasible. max_turnover {request.max_turnover} is below the {floor:.1%} the other inputs already "
            f"force: {'; '.join(reasons)}. {ways_out}"
        )


def _run_solver(mu, cov_matrix, request: OptimizationRequest, turnover_limit: tuple = None) -> dict:
    """Maximize the Sharpe ratio under the request's bounds; return the cleaned weights.

    ``turnover_limit`` is ``(limit, current)`` with ``current`` the held
    weights (ticker -> fraction of total investment), or None for no limit.

    The solver's weights sum to one over the *invested* part of the
    portfolio, while the request's weight bounds are fractions of the whole,
    cash reserve included. So the bounds are divided by the invested
    fraction on the way in and the answer is multiplied by it on the way
    out: with a 30% reserve, "max_weight 0.1" allows each solver weight up
    to 0.1 / 0.7 of the invested sleeve, which is 10% of the portfolio. The
    composition itself is the tangency portfolio either way; only binding
    bounds and a turnover limit make the reserve a true solver input.
    """
    invested = request.invested_fraction()
    scaled_max = request.max_weight / invested
    scaled_min = request.min_universal_weight / invested
    if float(mu.max()) <= request.risk_free_rate:
        # PyPortfolioOpt raises the same complaint in its own words; this one
        # names the number and the field so the fix is obvious.
        raise ValidationError(
            f"Optimization Error. No asset in the universe has an expected return above the risk_free_rate of "
            f"{request.risk_free_rate:.2%} (best is {float(mu.max()):.2%}), so no portfolio beats holding cash. "
            "Lower risk_free_rate or widen the universe."
        )
    try:
        frontier = EfficientFrontier(mu, cov_matrix, verbose=False, weight_bounds=(scaled_min, 1), solver=SOLVER)
        if scaled_max < 1:
            frontier.add_constraint(lambda weights: weights <= scaled_max)
        if turnover_limit is not None:
            frontier.add_constraint(_turnover_constraint(mu.index, *turnover_limit, invested))
        frontier.max_sharpe(risk_free_rate=request.risk_free_rate)
        cleaned = frontier.clean_weights(cutoff=WEIGHT_THRESHOLD)
        return {ticker: weight * invested for ticker, weight in cleaned.items()}
    except Exception as err:  # pylint: disable=broad-except
        # Surface the real solver error; "Infeasible" alone is undiagnosable.
        raise ValidationError(f"Optimization Error. Check your inputs and constraints. {err}") from err


def _turnover_constraint(tickers, limit: float, current: dict, invested_fraction: float = 1.0):
    """Build the cvxpy constraint capping the fraction of the portfolio sold at ``limit``.

    Sold value is ``sum(pos(current - w))`` over the universe, plus every
    held weight the universe does not contain (those positions have no
    variable and are sold in full). A cash reserve scales every final weight
    by ``invested_fraction``, so the comparison against the held weights --
    both sides fractions of the total portfolio -- carries that factor.

    Written in a form that survives PyPortfolioOpt's max-Sharpe
    transformation. ``max_sharpe`` substitutes ``w = k * w_true`` and
    rewrites each constraint ``f(w) <= c`` as ``f(w) <= c * k`` -- scaling the
    constant side only. A plain ``pos(current - w)`` would then compare
    scaled weights against unscaled ones. Multiplying every constant by
    ``sum(w)`` instead (which the transformed problem pins to ``k``) makes the
    whole constraint homogeneous in ``k``, so dividing through recovers
    exactly ``sum(pos(current - invested_fraction * w_true)) + outside <= limit``.
    """
    held = np.array([current.get(ticker, 0.0) for ticker in tickers])
    in_universe = set(tickers)
    outside = sum(weight for ticker, weight in current.items() if ticker not in in_universe)

    def constraint(weights):
        scale = cp.sum(weights)
        sold = cp.sum(cp.pos(held * scale - invested_fraction * weights)) + outside * scale
        return sold <= limit

    return constraint


def _solver_inputs(request: OptimizationRequest, current: dict) -> tuple:
    """Return the turnover inputs that reach the solver, hashable, or None when unlimited."""
    if request.max_turnover is None:
        return None
    return request.max_turnover, tuple(sorted(current.items()))


def _check_positions_turnover_feasible(request: OptimizationRequest, current: dict, kept: list) -> None:
    """Reject a position cap that forces more selling than ``max_turnover`` allows.

    Narrowing to ``kept`` sells every held ticker left out of it, which is a
    cost of ``max_positions`` rather than of anything the solver chose. The
    limit used to be applied to the full-universe solve and the truncation
    then sold past it silently, so the plan broke a limit it reported.
    """
    if request.max_turnover is None:
        return
    floor, _reasons = _turnover_floor(dataclasses.replace(request, universe=list(kept)), current)
    if floor <= request.max_turnover + TURNOVER_TOLERANCE:
        return
    dropped = sorted(ticker for ticker in current if ticker not in set(kept))
    reason = (
        f"max_positions {request.max_positions} narrows the portfolio to {len(kept)} holdings, which forces "
        f"{floor:.1%} of it to be sold, above max_turnover {request.max_turnover}."
    )
    if dropped:
        reason += f" Dropping {', '.join(dropped)} alone accounts for most of it."
    # Rounded up, so following the suggestion clears the limit rather than
    # landing back on this same error one tolerance away.
    remedy = f"Raise max_positions, or raise max_turnover to at least {math.ceil(floor * 100) / 100}."
    raise ValidationError(
        f"Infeasible. {reason} {remedy}",
        details={
            "conflicts": [
                {
                    "fields": ["constraints.max_positions", "constraints.max_turnover"],
                    "reason": reason,
                    "remedy": remedy,
                }
            ]
        },
    )


def _apply_position_cap(request: OptimizationRequest, market: MarketData, current: dict, weights: dict) -> dict:
    """Re-solve over the ``max_positions`` largest holdings, under the same constraints.

    A cardinality cap cannot be expressed to the solver directly: it is a
    binary condition, and PyPortfolioOpt's max-Sharpe substitution (``w = k *
    w_true``) makes the product of ``k`` with a binary indicator non-convex,
    so ``max_sharpe`` rejects integer constraints outright. The standard
    stand-in is to let the unconstrained solve rank the names, keep the
    largest ``max_positions`` of them, and solve again over just those.

    The previous approach kept the same names but merely rescaled their
    weights to sum to one, which is not a solve and silently broke the other
    constraints: dividing by a total below one inflates every weight, so a
    10% cap came back as a 14% position, and positions sold by the
    truncation pushed realized turnover past its limit. Re-solving enforces
    ``max_weight``, the weight floor and the turnover limit on the portfolio
    that is actually returned, and it can only improve the objective, since
    the rescaled weights remain available to the restricted problem.
    """
    kept = [ticker for ticker, _ in sorted(weights.items(), key=lambda kv: kv[1], reverse=True)]
    kept = kept[: request.solver_positions()]
    _check_positions_turnover_feasible(request, current, kept)
    mu, cov_matrix = market.stats_for(kept)
    limit = None if request.max_turnover is None else (request.max_turnover, current)
    return _run_solver(mu, cov_matrix, request, limit)


def max_sharpe_weights(request: OptimizationRequest, market: MarketData, current: dict) -> dict:
    """Solved weights for this request, memoized on the market snapshot.

    The solve is ~90% of a request, and it depends on nothing but the prices
    and the fields in the key below, so agents repeating a request get the
    stored answer. Cash and holdings are deliberately absent from the key
    unless ``max_turnover`` is set: by default they only scale the weights
    afterwards, so varying them still hits the same solve. A turnover limit
    makes the held weights a solver input, so they join the key only then.
    ``max_positions`` selects and then re-solves over a subset, and
    ``cash_reserve`` rescales the solver's weight bounds, so both are part
    of the key too. Anything new that reaches the solver must be added here.
    ``min_history_days`` is not here on purpose: it only decides which
    tickers make up ``request.universe``, which is.

    Nothing is stored when the solver fails, so an infeasible request keeps
    reporting its own error rather than a remembered one.
    """
    turnover_inputs = _solver_inputs(request, current)
    key = (
        "weights",
        tuple(request.universe),
        request.max_weight,
        request.min_universal_weight,
        request.risk_free_rate,
        turnover_inputs,
        request.max_positions,
        request.cash_reserve,
        tuple(sorted(request.unoptimized.items())),
    )

    def compute():
        mu, cov_matrix = market.stats_for(request.universe)
        limit = None if turnover_inputs is None else (request.max_turnover, current)
        weights = _run_solver(mu, cov_matrix, request, limit)
        held = [ticker for ticker, weight in weights.items() if weight != 0]
        if request.max_positions is None or len(held) <= request.solver_positions():
            return weights
        return _apply_position_cap(request, market, current, weights)

    # Copied on the way out: callers rebuild the dict as they filter it, and a
    # cached value must survive that unchanged for the next request.
    return dict(market.solutions.get_or_compute(key, compute))


def _return_and_volatility(request: OptimizationRequest, market: MarketData, weights: dict, current: dict) -> tuple:
    """Annual expected return and volatility of ``weights``, memoized on the snapshot.

    Both are functions of the weight vector alone, and the key determines
    that vector in full: everything that drives the solve, ``max_positions``
    included. Cash and holdings scale the portfolio without changing its
    shape, so they are absent here too, except through the turnover inputs
    when ``max_turnover`` is set.

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
        request.risk_free_rate,
        _solver_inputs(request, current),
        request.max_positions,
        request.cash_reserve,
        tuple(sorted(request.unoptimized.items())),
    )

    def compute():
        mu, cov_matrix = market.stats_for(request.universe)
        # Reindexing to the universe drops the positions kept as they are:
        # the metrics describe what was optimized, and the response says so.
        vector = pd.Series(weights, dtype=float).reindex(mu.index, fill_value=0.0)
        # The cash reserve earns the risk-free rate (that is what the rate
        # means here) and has no variance, so it adds to the return and
        # dilutes nothing but the volatility -- which is why the Sharpe
        # ratio is unchanged by the reserve: both the excess return and the
        # volatility shrink by the invested fraction.
        reserve_return = request.cash_reserve * request.risk_free_rate
        return float(vector @ mu) + reserve_return, float(np.sqrt(vector @ cov_matrix @ vector))

    return market.solutions.get_or_compute(key, compute)


def solve(request: OptimizationRequest, market: MarketData) -> dict:
    """Run the optimization and return the result dict (the API response shape)."""
    total_investment = request.cash + market.value(request.holdings)
    if total_investment == 0:
        raise ValidationError("Invalid input. Total investment value cannot be 0.")
    current = current_weights(request, market)
    # The kept positions are not the solver's to sell, so they are not in
    # the held weights it and the turnover rules see; they rejoin below.
    optimizable = {ticker: weight for ticker, weight in current.items() if ticker not in request.unoptimized}
    _check_turnover_feasible(request, optimizable)

    if request.invested_fraction() > WEIGHT_THRESHOLD:
        weights = max_sharpe_weights(request, market, optimizable)
        weights = {asset: weight for asset, weight in weights.items() if weight != 0}
        weights = _keep_untouched_holdings(weights, optimizable)
    else:
        weights = {}  # everything is kept or reserved; nothing is left to solve for
    weights.update(request.unoptimized)

    portfolio = {
        asset: {
            "shares": round(weight * total_investment / market.price(asset), 4),
            "value": round(weight * total_investment, 2),
            "percent_weight": round(weight * 100, 2),
        }
        for asset, weight in weights.items()
    }
    for asset in request.unoptimized:
        # Exactly the shares held, not a weight converted back, so the trade
        # list cannot carry a rounding sliver for a position nobody touched.
        portfolio[asset]["shares"] = request.holdings[asset]
    # as_of is the date of the prices every number here was computed from.
    # An agent about to place real orders needs it to decide whether the plan
    # is fresh enough to act on; without it the response looks like a quote.
    result = {
        "as_of": str(market.as_of),
        "risk_free_rate": _describe_rate(request),
        "optimized_portfolio": portfolio,
        # Always present, usually zero: uninvested money is part of the plan,
        # not a leftover, and an agent reconciling value against the holdings
        # should find the difference stated rather than have to infer it.
        "cash": {
            "value": round(request.cash_reserve * total_investment, 2),
            "percent_weight": round(request.cash_reserve * 100, 2),
        },
    }
    if request.unoptimized:
        result["unoptimized"] = _describe_unoptimized(request)
    if not request.exclude_trades:
        result["trades"] = trades.plan(
            request.holdings,
            {asset: row["shares"] for asset, row in portfolio.items()},
            market.latest_prices,
        )
    if not request.exclude_metrics:
        # Describe the portfolio actually returned, which under a position
        # cap is the restricted re-solve rather than the first pass
        # (ef.portfolio_performance() would describe the latter). Computed
        # here so `exclude_metrics` requests skip it entirely.
        expected_return, volatility = _return_and_volatility(request, market, weights, optimizable)
        # Degenerate history (too few rows, flat prices) gives zero volatility; report
        # the Sharpe ratio as null rather than dividing by zero.
        sharpe = round((expected_return - request.risk_free_rate) / volatility, 2) if volatility > 0 else None
        result["metrics"] = {
            "portfolio_value": round(total_investment, 2),
            "expected_annual_return": round(expected_return, 3),
            "annual_volatility": round(volatility, 3),
            "sharpe_ratio": sharpe,
            "num_assets": len(weights),
            # Measured on the returned portfolio, and now bounded by
            # max_turnover: a position cap that would sell past the limit is
            # refused rather than quietly reported above it.
            "turnover": round(turnover(current, weights), 4),
        }
    return result


def _describe_unoptimized(request: OptimizationRequest) -> dict:
    """Describe the positions kept as they are, and how to have them optimized instead, for the response."""
    kept = sum(request.unoptimized.values())
    fewest = min(request.unoptimized_days.values())
    return {
        "positions": {
            ticker: {"history_days": request.unoptimized_days[ticker], "percent_weight": round(weight * 100, 2)}
            for ticker, weight in request.unoptimized.items()
        },
        "note": (
            f"Kept at their current size, {kept:.1%} of the portfolio, and left out of the metrics: fewer than "
            f"{request.min_history_days} days of price history is too little to estimate their return and risk, "
            f"so the optimizer neither sized nor sold them. Pass min_history_days of {fewest} or less to have them "
            "optimized like any other ticker (which may sell them down or out)."
        ),
    }


def _describe_rate(request: OptimizationRequest) -> dict:
    """Describe the rate the solve assumed and where it came from, for the response.

    Present even when metrics are excluded: it is an input being echoed, not
    a measurement, and a reader must be able to tell a deliberate 0% from
    the fallback.
    """
    described = {"value": request.risk_free_rate, "source": request.risk_free_rate_source}
    if request.risk_free_rate_source == "default":
        described["note"] = DEFAULT_RATE_NOTE
    return described


def optimize(payload, market: MarketData) -> dict:
    """Validate and solve in one call."""
    return solve(parse_request(payload, market), market)
