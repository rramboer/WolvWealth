"""API Portfolio Optimization."""
import flask
import wolvwealth

from pypfopt import expected_returns, risk_models
from pypfopt.efficient_frontier import EfficientFrontier
from wolvwealth.api.state import ApplicationState
from wolvwealth.api.api_exceptions import InvalidUsage
from wolvwealth.api.auth import check_api_key


class Optimization:
    def __init__(self) -> None:
        """Initialize optimization."""
        check_api_key()
        self.state = ApplicationState()
        self.set_defaults()
        self.parse_input()
        self.execute_optimization()

    def set_defaults(self) -> None:
        self.initial_cash = 0  # $0.00
        self.universe = self.state.TICKER_UNIVERSE[:500]  # Top 500 stocks by market cap.
        self.exclude_metrics = False  # Include metrics in output.
        self.max_positions = -1  # Maximum number of stocks in portfolio. May overpwoer max_weight.
        self.max_weight = 1  # No weights higher than this. Can be ignored if max_positions is set.
        self.min_universal_weight = 0.00  # Every stock must have at least this weight.
        self.weight_threshold = 0.0005  # Ignore stocks with weights below this. May cause allocation < 100%.

    def parse_input(self) -> None:
        """Parse request as JSON."""
        try:
            self.input_json = flask.request.json
        except Exception:
            raise InvalidUsage("Parse Error. Unable to parse request as JSON.")
        self.parse_initial_cash()
        self.parse_universe()
        self.parse_initial_holdings()
        self.parse_exclude_metrics()
        self.parse_constraints()

    def parse_constraints(self) -> None:
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
            if self.input_json["constraints"]["max_positions"] < 0.00:
                raise InvalidUsage("Invalid max_positions. max_positions must be greater than 0.")
            self.max_positions = self.input_json["constraints"]["max_positions"]
            if self.input_json["constraints"]["max_positions"] > len(self.universe):
                self.max_positions = len(self.universe)
            if self.max_positions * self.max_weight < 1:
                raise InvalidUsage(
                    f"Infeasible. max_positions * max_weight must be greater than or equal to 1 for full allocation."
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
        if "exclude_metrics" not in self.input_json:
            return
        if not isinstance(self.input_json["exclude_metrics"], bool):
            raise InvalidUsage("Invalid exclude_metrics. exclude_metrics must be a boolean.")
        self.exclude_metrics = self.input_json["exclude_metrics"]

    def parse_initial_cash(self) -> None:
        if "initial_cash" not in self.input_json:
            return
        if not isinstance(self.input_json["initial_cash"], (int, float)):
            raise InvalidUsage("Invalid initial_cash. initial_cash must be a number.")
        if self.input_json["initial_cash"] < 0.00:
            raise InvalidUsage("Invalid initial_cash. initial_cash must be greater than 0.")
        self.initial_cash = round(self.input_json["initial_cash"], 2)

    def parse_universe(self) -> None:
        if "universe" not in self.input_json:
            return
        if not isinstance(self.input_json["universe"], list):
            raise InvalidUsage("Invalid universe. universe must be a list.")
        unclean_universe = self.input_json["universe"]
        if len(unclean_universe) == 0:  # If empty universe, use default
            return
        for ticker in unclean_universe:
            if not isinstance(ticker, str):
                raise InvalidUsage("Invalid universe. universe must be list of strings.")
            if ticker.upper() not in self.state.TICKER_UNIVERSE:
                if ticker == "top20":
                    unclean_universe.remove(ticker)
                    unclean_universe.extend(self.state.TICKER_UNIVERSE[:20])
                elif ticker == "top50":
                    unclean_universe.remove(ticker)
                    unclean_universe.extend(self.state.TICKER_UNIVERSE[:50])
                elif ticker == "top100":
                    unclean_universe.remove(ticker)
                    unclean_universe.extend(self.state.TICKER_UNIVERSE[:100])
                elif ticker == "top200":
                    unclean_universe.remove(ticker)
                    unclean_universe.extend(self.state.TICKER_UNIVERSE[:200])
                elif ticker == "top300":
                    unclean_universe.remove(ticker)
                    unclean_universe.extend(self.state.TICKER_UNIVERSE[:300])
                elif ticker == "top400":
                    unclean_universe.remove(ticker)
                    unclean_universe.extend(self.state.TICKER_UNIVERSE[:400])
                elif ticker == "top500":
                    unclean_universe.remove(ticker)
                    unclean_universe.extend(self.state.TICKER_UNIVERSE[:500])
                else:
                    raise InvalidUsage(f"Invalid symbol in universe: {ticker}.")
        self.universe = [s.upper() for s in unclean_universe]
        self.universe = list(dict.fromkeys(self.universe))

    def parse_initial_holdings(self) -> None:
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
        try:
            mu = expected_returns.mean_historical_return(filtered_data)
            cov_matrix = risk_models.exp_cov(filtered_data)
            ef = EfficientFrontier(mu, cov_matrix, verbose=False, weight_bounds=(self.min_universal_weight, 1))
            if self.max_weight != 1:
                ef.add_constraint(lambda weights: weights <= self.max_weight)
            ef.max_sharpe()
            cleaned_weights = ef.clean_weights(cutoff=self.weight_threshold)
            if self.max_positions != -1:
                sorted_weights = sorted(cleaned_weights.items(), key=lambda x: x[1], reverse=True)
                cleaned_weights = {}
                for i in range(self.max_positions):
                    cleaned_weights[sorted_weights[i][0]] = sorted_weights[i][1]
                total_weight = sum(cleaned_weights.values())
                for k, v in cleaned_weights.items():
                    cleaned_weights[k] = v / total_weight
            for k, v in cleaned_weights.copy().items():
                if v == 0:
                    del cleaned_weights[k]

        except Exception as e:
            raise InvalidUsage(f"Optimization Error. Check your inputs and constraints. Infeasible.")

        # Construct output
        output = {}
        output["optimized_portfolio"] = {}
        for asset, weight in cleaned_weights.items():
            output["optimized_portfolio"][asset] = {
                "shares": round(weight * total_investment / self.state.fetch_ticker_price(asset), 4),
                "value": round(weight * total_investment, 2),
                "percent_weight": round(weight * 100, 2),
            }
        if self.exclude_metrics == False:
            metrics = ef.portfolio_performance()
            output["metrics"] = {
                "portfolio_value": round(total_investment, 2),
                "expected_annual_return": round(metrics[0], 3),
                "annual_volatility": round(metrics[1], 3),
                "sharpe_ratio": round(metrics[2], 2),
            }
        self.output = flask.jsonify(output)


@wolvwealth.app.route("/api/optimize/", methods=["POST"])
def optimize():
    """Execute Optimization"""
    return Optimization().output
