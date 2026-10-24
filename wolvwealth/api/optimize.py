"""API Portfolio Optimization."""
import flask
import numpy as np
import pandas as pd
import wolvwealth
import wolvwealth.portfolios

from pypfopt import expected_returns, risk_models
from pypfopt.efficient_frontier import EfficientFrontier
from wolvwealth.api.state import ApplicationState
from wolvwealth.api.api_exceptions import InvalidUsage


class Optimization:
    """Run one optimization from a plain input dictionary.

    Framework-free on purpose: callers (JSON API, HTML views, scripts) build
    the input dict themselves and read the plain-dict result from .result.
    """

    def __init__(self, input_json: dict) -> None:
        """Initialize optimization."""
        if not isinstance(input_json, dict):
            raise InvalidUsage("Parse Error. Request body must be a JSON object.")
        self.input_json = input_json
        self.state = ApplicationState()
        self.set_defaults()
        self.parse_input()
        self.execute_optimization()

    #: Universe aliases: "top20" expands to the 20 largest tickers by market cap, etc.
    UNIVERSE_ALIASES = {f"top{n}": n for n in (20, 50, 100, 200, 300, 400, 500)}

    #: Risk-free rate used for the Sharpe ratio, matching PyPortfolioOpt's default.
    RISK_FREE_RATE = 0.02

    def set_defaults(self) -> None:
        """Set the defaults every request starts from."""
        self.initial_cash = 0  # $0.00
        self.universe = self.state.TICKER_UNIVERSE[:500]  # Top 500 stocks by market cap.
        self.exclude_metrics = False  # Include metrics in output.
        self.max_positions = -1  # Maximum number of stocks in portfolio. May overpower max_weight.
        self.max_weight = 1  # No weights higher than this. Can be ignored if max_positions is set.
        self.min_universal_weight = 0.00  # Every stock must have at least this weight.
        self.weight_threshold = 0.0005  # Ignore stocks with weights below this. May cause allocation < 100%.

    def parse_input(self) -> None:
        """Parse the input dictionary."""
        self.parse_initial_cash()
        self.parse_universe()
        self.parse_initial_holdings()
        self.parse_exclude_metrics()
        self.parse_constraints()

    def parse_constraints(self) -> None:
        """Validate and apply the optional constraints block."""
        if "constraints" not in self.input_json:
            return
        if not isinstance(self.input_json["constraints"], dict):
            raise InvalidUsage("Invalid constraints. constraints must be a dictionary.")
        if "max_weight" in self.input_json["constraints"]:
            if not isinstance(self.input_json["constraints"]["max_weight"], (int, float)):
                raise InvalidUsage("Invalid max_weight. max_weight must be a number.")
            if (
                self.input_json["constraints"]["max_weight"] < 0.00
                or self.input_json["constraints"]["max_weight"] > 1.00
            ):
                raise InvalidUsage("Invalid max_weight. max_weight must be greater than 0 and less than 1.")
            self.max_weight = round(self.input_json["constraints"]["max_weight"], 2)
        if "max_positions" in self.input_json["constraints"]:
            if not isinstance(self.input_json["constraints"]["max_positions"], int):
                raise InvalidUsage("Invalid max_positions. max_positions must be an integer.")
            if self.input_json["constraints"]["max_positions"] < 1:
                raise InvalidUsage("Invalid max_positions. max_positions must be greater than 0.")
            self.max_positions = self.input_json["constraints"]["max_positions"]
            if self.input_json["constraints"]["max_positions"] > len(self.universe):
                self.max_positions = len(self.universe)
            if self.max_positions * self.max_weight < 1:
                raise InvalidUsage(
                    "Infeasible. max_positions * max_weight must be greater than or equal to 1 for full allocation."
                )
        if "min_universal_weight" in self.input_json["constraints"]:
            if not isinstance(self.input_json["constraints"]["min_universal_weight"], (int, float)):
                raise InvalidUsage("Invalid min_universal_weight. min_universal_weight must be a number.")
            if (
                self.input_json["constraints"]["min_universal_weight"] < 0.00
                or self.input_json["constraints"]["min_universal_weight"] > 1.00
            ):
                raise InvalidUsage(
                    "Invalid min_universal_weight. min_universal_weight must be greater than 0 and less than 1."
                )
            self.min_universal_weight = round(self.input_json["constraints"]["min_universal_weight"], 2)
        if self.min_universal_weight > self.max_weight:
            raise InvalidUsage("Invalid constraints. min_weight must be less than max_weight.")

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
        """Validate the universe, expanding topN aliases and upper-casing tickers."""
        if "universe" not in self.input_json:
            return
        if not isinstance(self.input_json["universe"], list):
            raise InvalidUsage("Invalid universe. universe must be a list.")
        if len(self.input_json["universe"]) == 0:  # If empty universe, use default
            return
        expanded = []
        for entry in self.input_json["universe"]:
            if not isinstance(entry, str):
                raise InvalidUsage("Invalid universe. universe must be list of strings.")
            if entry.upper() in self.state.TICKER_UNIVERSE:
                expanded.append(entry.upper())
            elif entry in self.UNIVERSE_ALIASES:
                expanded.extend(self.state.TICKER_UNIVERSE[: self.UNIVERSE_ALIASES[entry]])
            else:
                raise InvalidUsage(f"Invalid symbol in universe: {entry}.")
        self.universe = list(dict.fromkeys(expanded))

    def parse_initial_holdings(self) -> None:
        """Validate the optional initial_holdings map of ticker to shares."""
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
        filtered_data = self.state.HISTORICAL_PRICES[self.universe]
        mu = expected_returns.mean_historical_return(filtered_data)
        cov_matrix = risk_models.exp_cov(filtered_data)
        try:
            ef = EfficientFrontier(mu, cov_matrix, verbose=False, weight_bounds=(self.min_universal_weight, 1))
            if self.max_weight != 1:
                ef.add_constraint(lambda weights: weights <= self.max_weight)
            ef.max_sharpe()
            cleaned_weights = ef.clean_weights(cutoff=self.weight_threshold)
        except Exception as err:
            # Surface the real solver error; "Infeasible" alone is undiagnosable.
            raise InvalidUsage(f"Optimization Error. Check your inputs and constraints. {err}") from err

        if self.max_positions != -1:
            top = sorted(cleaned_weights.items(), key=lambda x: x[1], reverse=True)[: self.max_positions]
            total_weight = sum(weight for _, weight in top)
            cleaned_weights = {asset: weight / total_weight for asset, weight in top}
        cleaned_weights = {asset: weight for asset, weight in cleaned_weights.items() if weight != 0}

        # Construct output
        output = {}
        output["optimized_portfolio"] = {}
        for asset, weight in cleaned_weights.items():
            output["optimized_portfolio"][asset] = {
                "shares": round(weight * total_investment / self.state.fetch_ticker_price(asset), 4),
                "value": round(weight * total_investment, 2),
                "percent_weight": round(weight * 100, 2),
            }
        if not self.exclude_metrics:
            # Describe the portfolio actually returned, including the
            # max_positions truncation (ef.portfolio_performance() would
            # describe the untruncated solver output instead).
            weights = pd.Series(cleaned_weights, dtype=float).reindex(mu.index, fill_value=0.0)
            expected_return = float(weights @ mu)
            volatility = float(np.sqrt(weights @ cov_matrix @ weights))
            output["metrics"] = {
                "portfolio_value": round(total_investment, 2),
                "expected_annual_return": round(expected_return, 3),
                "annual_volatility": round(volatility, 3),
                "sharpe_ratio": round((expected_return - self.RISK_FREE_RATE) / volatility, 2),
                "num_assets": len(cleaned_weights),
            }
        self.result = output


def resolve_portfolio_input(input_json: dict) -> dict:
    """Expand a saved-portfolio reference into cash and holdings.

    A request may say {"workspace": "ryan", "portfolio": "long-term"} instead
    of spelling out initial_cash/initial_holdings. The workspace falls back to
    the browser session's active workspace. Explicit fields win over stored
    values so a request can still override either one.
    """
    if not isinstance(input_json, dict) or "portfolio" not in input_json:
        return input_json
    if not isinstance(input_json["portfolio"], str):
        raise InvalidUsage("Invalid portfolio. portfolio must be a string (a portfolio name).")
    workspace = input_json.pop("workspace", None)
    if workspace is None:
        workspace = flask.session.get("workspace")
    if workspace is None:
        raise InvalidUsage("No workspace given. Pass \"workspace\" or open one in the browser first.")
    if not isinstance(workspace, str):
        raise InvalidUsage("Invalid workspace. workspace must be a string.")
    portfolio = wolvwealth.portfolios.fetch_portfolio(workspace, name=input_json.pop("portfolio"))
    if portfolio is None:
        raise InvalidUsage("Unknown portfolio for this workspace.", status_code=404)
    input_json.setdefault("initial_cash", portfolio["cash"])
    input_json.setdefault("initial_holdings", portfolio["holdings"])
    return input_json


@wolvwealth.app.route("/api/optimize/", methods=["POST"])
def optimize():
    """Execute an optimization from a JSON request body."""
    try:
        input_json = flask.request.get_json(force=True)
    except Exception:
        raise InvalidUsage("Parse Error. Unable to parse request as JSON.")
    input_json = resolve_portfolio_input(input_json)
    return flask.jsonify(Optimization(input_json).result)
