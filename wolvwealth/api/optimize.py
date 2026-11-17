"""API Portfolio Optimization."""

import cvxpy
import flask
from pypfopt import exceptions as pypfopt_exceptions

import wolvwealth
from wolvwealth.api.api_exceptions import InvalidUsage
from wolvwealth.api.auth import authenticate
from wolvwealth.api.state import ApplicationState
from wolvwealth.optimizer_core import build_portfolio_output, compute_max_sharpe_portfolio
from wolvwealth.optimizer_params import (
    MAX_CASH,
    MAX_SHARES,
    Constraints,
    InvalidOptimizationInput,
    feasibility_problem,
    parse_constraints,
    require_number,
    resolve_universe,
)
from wolvwealth.ratelimit import api_per_ip, caller_key, configured, limiter


class Optimization:
    """Parse an /api/optimize/ request and run the optimization."""

    def __init__(self) -> None:
        """Initialize optimization."""
        caller = authenticate(require_csrf=True)  # 401/403 first, but nothing charged yet.
        self.state = ApplicationState()
        self.set_defaults()
        self.parse_input()
        self.validate_investment()
        caller.charge()  # Only well-formed, feasible requests cost a use.
        self.execute_optimization()

    def set_defaults(self) -> None:
        """Set default values for all optimization parameters."""
        self.initial_cash = 0  # $0.00
        self.universe = self.state.TICKER_UNIVERSE[:500]  # Top 500 stocks by market cap.
        self.exclude_metrics = False  # Include metrics in output.
        self.max_positions = -1  # Maximum number of stocks in portfolio. May overpower max_weight.
        self.max_weight = 1  # No weights higher than this. Can be ignored if max_positions is set.
        self.min_universal_weight = 0.00  # Every stock must have at least this weight.
        self.weight_threshold = 0.0005  # Ignore stocks with weights below this. May cause allocation < 100%.

    def parse_input(self) -> None:
        """Parse request as JSON."""
        try:
            self.input_json = flask.request.json
        except Exception as err:
            raise InvalidUsage("Parse Error. Unable to parse request as JSON.") from err
        if self.input_json is None:
            raise InvalidUsage("Parse Error. Unable to parse request as JSON.")
        self.parse_initial_cash()
        self.parse_universe()
        self.parse_initial_holdings()
        self.parse_exclude_metrics()
        self.parse_constraints()

    def parse_constraints(self) -> None:
        """Validate and apply the optional constraints object."""
        if "constraints" not in self.input_json:
            return
        try:
            constraints = parse_constraints(self.input_json["constraints"], len(self.universe))
        except InvalidOptimizationInput as err:
            raise InvalidUsage(str(err)) from err
        self.max_weight = constraints.max_weight
        self.max_positions = constraints.max_positions
        self.min_universal_weight = constraints.min_universal_weight

    def parse_exclude_metrics(self) -> None:
        """Validate the optional exclude_metrics flag."""
        if "exclude_metrics" not in self.input_json:
            return
        if not isinstance(self.input_json["exclude_metrics"], bool):
            raise InvalidUsage("Invalid exclude_metrics. exclude_metrics must be a boolean.")
        self.exclude_metrics = self.input_json["exclude_metrics"]

    def parse_initial_cash(self) -> None:
        """Validate the optional initial_cash amount."""
        if "initial_cash" not in self.input_json:
            return
        try:
            cash = require_number(self.input_json["initial_cash"], "initial_cash", minimum=0.0, maximum=MAX_CASH)
        except InvalidOptimizationInput as err:
            raise InvalidUsage(str(err)) from err
        self.initial_cash = round(cash, 2)

    def parse_universe(self) -> None:
        """Validate the optional universe list, expanding topN aliases."""
        if "universe" not in self.input_json:
            return
        requested = self.input_json["universe"]
        if isinstance(requested, list) and len(requested) == 0:  # If empty universe, use default
            return
        try:
            self.universe = resolve_universe(requested, self.state.TICKER_UNIVERSE)
        except InvalidOptimizationInput as err:
            raise InvalidUsage(str(err)) from err

    def parse_initial_holdings(self) -> None:
        """Validate the optional initial_holdings mapping."""
        self.initial_holdings = {}
        if "initial_holdings" not in self.input_json:
            return
        if not isinstance(self.input_json["initial_holdings"], dict):
            raise InvalidUsage("Invalid initial_holdings. initial_holdings must be a dictionary.")
        holdings = {}
        for ticker, shares in self.input_json["initial_holdings"].items():
            if not isinstance(ticker, str) or ticker.upper() not in self.state.TICKER_UNIVERSE:
                raise InvalidUsage(f"Invalid symbol in initial_holdings: {ticker}.")
            try:
                holdings[ticker.upper()] = require_number(shares, "shares", minimum=0.0, maximum=MAX_SHARES)
            except InvalidOptimizationInput as err:
                raise InvalidUsage(f"Invalid initial_holdings for {ticker}. {err}") from err
        self.initial_holdings = holdings

    def validate_investment(self) -> None:
        """Check the request can be optimized at all: money to invest, price data, feasible constraints."""
        total_investment = self.initial_cash
        for asset, shares in self.initial_holdings.items():
            total_investment += shares * self.state.fetch_ticker_price(asset)
        if total_investment == 0:
            raise InvalidUsage("Invalid input. Total investment value cannot be 0.")
        self.total_investment = total_investment
        missing = [t for t in self.universe if t not in self.state.HISTORICAL_PRICES.columns]
        if missing:
            raise InvalidUsage(f"No price data available for: {', '.join(sorted(missing))}.")
        constraints = Constraints(
            max_weight=self.max_weight,
            min_universal_weight=self.min_universal_weight,
            max_positions=self.max_positions,
            weight_threshold=self.weight_threshold,
        )
        problem = feasibility_problem(constraints, len(self.universe))
        if problem:
            raise InvalidUsage(problem)

    def execute_optimization(self) -> None:
        """Run optimization."""
        filtered_data = self.state.HISTORICAL_PRICES[self.universe]
        try:
            weights, performance = compute_max_sharpe_portfolio(
                filtered_data,
                max_weight=self.max_weight,
                min_universal_weight=self.min_universal_weight,
                max_positions=self.max_positions,
                weight_threshold=self.weight_threshold,
            )
        except (pypfopt_exceptions.OptimizationError, cvxpy.error.SolverError, ValueError) as err:
            wolvwealth.app.logger.exception("Portfolio optimization failed")
            raise InvalidUsage("Optimization Error. Check your inputs and constraints. Infeasible.") from err
        output = build_portfolio_output(
            weights,
            self.total_investment,
            self.state.fetch_ticker_price,
            performance=None if self.exclude_metrics else performance,
        )
        self.output = flask.jsonify(output)


@wolvwealth.app.route("/api/optimize/", methods=["POST"])
@api_per_ip
@limiter.limit(configured("RATELIMIT_OPTIMIZE_PER_CALLER"), key_func=caller_key)
def optimize():
    """Execute Optimization."""
    return Optimization().output
