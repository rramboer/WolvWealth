"""API Portfolio Optimization."""

import re

import cvxpy
import flask
from pypfopt import exceptions as pypfopt_exceptions

import wolvwealth
from wolvwealth.api.api_exceptions import InvalidUsage
from wolvwealth.api.auth import check_api_key
from wolvwealth.api.state import ApplicationState
from wolvwealth.optimizer_core import build_portfolio_output, compute_max_sharpe_portfolio

TOP_N_PATTERN = re.compile(r"top(\d+)", re.IGNORECASE)


class Optimization:
    """Parse an /api/optimize/ request and run the optimization."""

    def __init__(self) -> None:
        """Initialize optimization."""
        check_api_key()
        self.state = ApplicationState()
        self.set_defaults()
        self.parse_input()
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
        constraints = self.input_json["constraints"]
        if not isinstance(constraints, dict):
            raise InvalidUsage("Invalid constraints. constraints must be a dictionary.")
        if "max_weight" in constraints:
            if not isinstance(constraints["max_weight"], (int, float)):
                raise InvalidUsage("Invalid max_weight. max_weight must be a number.")
            if constraints["max_weight"] < 0.00 or constraints["max_weight"] > 1.00:
                raise InvalidUsage("Invalid max_weight. max_weight must be greater than 0 and less than 1.")
            self.max_weight = round(constraints["max_weight"], 2)
        if "max_positions" in constraints:
            if not isinstance(constraints["max_positions"], int):
                raise InvalidUsage("Invalid max_positions. max_positions must be an integer.")
            if constraints["max_positions"] < 0.00:
                raise InvalidUsage("Invalid max_positions. max_positions must be greater than 0.")
            self.max_positions = min(constraints["max_positions"], len(self.universe))
            if self.max_positions * self.max_weight < 1:
                raise InvalidUsage(
                    "Infeasible. max_positions * max_weight must be greater than or equal to 1 for full allocation."
                )
        if "min_universal_weight" in constraints:
            if not isinstance(constraints["min_universal_weight"], (int, float)):
                raise InvalidUsage("Invalid min_universal_weight. min_universal_weight must be a number.")
            if constraints["min_universal_weight"] < 0.00 or constraints["min_universal_weight"] > 1.00:
                raise InvalidUsage(
                    "Invalid min_universal_weight. min_universal_weight must be greater than 0 and less than 1."
                )
            self.min_universal_weight = round(constraints["min_universal_weight"], 2)
        if self.min_universal_weight > self.max_weight:
            raise InvalidUsage("Invalid constraints. min_weight must be less than max_weight.")
        if self.min_universal_weight * len(self.universe) > 1:
            raise InvalidUsage(
                "Infeasible. min_universal_weight * universe size cannot exceed 1. "
                "Lower min_universal_weight or shrink the universe."
            )

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
        if not isinstance(self.input_json["initial_cash"], (int, float)):
            raise InvalidUsage("Invalid initial_cash. initial_cash must be a number.")
        if self.input_json["initial_cash"] < 0.00:
            raise InvalidUsage("Invalid initial_cash. initial_cash must be greater than 0.")
        self.initial_cash = round(self.input_json["initial_cash"], 2)

    def parse_universe(self) -> None:
        """Validate the optional universe list, expanding topN aliases."""
        if "universe" not in self.input_json:
            return
        if not isinstance(self.input_json["universe"], list):
            raise InvalidUsage("Invalid universe. universe must be a list.")
        requested = self.input_json["universe"]
        if len(requested) == 0:  # If empty universe, use default
            return
        universe = []
        for entry in requested:
            if not isinstance(entry, str):
                raise InvalidUsage("Invalid universe. universe must be list of strings.")
            ticker = entry.upper()
            if ticker in self.state.TICKER_UNIVERSE:
                universe.append(ticker)
                continue
            top_n = TOP_N_PATTERN.fullmatch(entry)
            if top_n:  # e.g. "top50" expands to the 50 largest stocks by market cap
                universe.extend(self.state.TICKER_UNIVERSE[: int(top_n.group(1))])
                continue
            raise InvalidUsage(f"Invalid symbol in universe: {entry}.")
        self.universe = list(dict.fromkeys(universe))

    def parse_initial_holdings(self) -> None:
        """Validate the optional initial_holdings mapping."""
        self.initial_holdings = {}
        if "initial_holdings" not in self.input_json:
            return
        if not isinstance(self.input_json["initial_holdings"], dict):
            raise InvalidUsage("Invalid initial_holdings. initial_holdings must be a dictionary.")
        unclean_initial_holdings = self.input_json["initial_holdings"]
        for ticker, shares in unclean_initial_holdings.items():
            if not isinstance(ticker, str):
                raise InvalidUsage("Invalid initial_holdings. initial_holdings must be list of strings.")
            if ticker.upper() not in self.state.TICKER_UNIVERSE:
                raise InvalidUsage(f"Invalid symbol in initial_holdings: {ticker}.")
            if not isinstance(shares, (int, float)):
                raise InvalidUsage(
                    "Invalid initial_holdings. initial_holdings must be a dictionary of strings to numbers."
                )
            if shares < 0.00:
                raise InvalidUsage("Invalid initial_holdings. Shares must be positive.")
        self.initial_holdings = {s.upper(): unclean_initial_holdings[s] for s in unclean_initial_holdings}

    def execute_optimization(self) -> None:
        """Run optimization."""
        total_investment = self.initial_cash
        for asset, shares in self.initial_holdings.items():
            total_investment += shares * self.state.fetch_ticker_price(asset)
        if total_investment == 0:
            raise InvalidUsage("Invalid input. Total investment value cannot be 0.")
        missing = [t for t in self.universe if t not in self.state.HISTORICAL_PRICES.columns]
        if missing:
            raise InvalidUsage(f"No price data available for: {', '.join(sorted(missing))}.")
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
            total_investment,
            self.state.fetch_ticker_price,
            performance=None if self.exclude_metrics else performance,
        )
        self.output = flask.jsonify(output)


@wolvwealth.app.route("/api/optimize/", methods=["POST"])
def optimize():
    """Execute Optimization."""
    return Optimization().output
