"""API Portfolio Optimization."""
import flask
import wolvwealth

from pypfopt import expected_returns, risk_models
from pypfopt.efficient_frontier import EfficientFrontier
from wolvwealth.api.state import ApplicationState
from wolvwealth.api.api_exceptions import InvalidUsage


class Optimization:
    def __init__(self) -> None:
        self.state = ApplicationState()
        self.parse_input()
        self.execute_optimization()

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

    def parse_exclude_metrics(self) -> None:
        self.exclude_metrics = False
        if "exclude_metrics" not in self.input_json:
            return
        if not isinstance(self.input_json["exclude_metrics"], bool):
            raise InvalidUsage(
                "Invalid exclude_metrics. exclude_metrics must be a boolean."
            )
        self.exclude_metrics = self.input_json["exclude_metrics"]

    def parse_initial_cash(self) -> None:
        self.initial_cash = 0.0
        if "initial_cash" not in self.input_json:
            return
        if not isinstance(self.input_json["initial_cash"], (int, float)):
            raise InvalidUsage("Invalid initial_cash. initial_cash must be a number.")
        if self.input_json["initial_cash"] < 0.00:
            raise InvalidUsage(
                "Invalid initial_cash. initial_cash must be greater than 0."
            )
        self.initial_cash = self.input_json["initial_cash"]

    def parse_universe(self) -> None:
        self.universe = self.state.TICKER_UNIVERSE[:500]  # Default to "top500"
        if "universe" not in self.input_json:
            return
        if not isinstance(self.input_json["universe"], list):
            raise InvalidUsage("Invalid universe. universe must be a list.")
        unclean_universe = self.input_json["universe"]
        if len(unclean_universe) == 0:  # If empty universe, use default
            return
        for ticker in unclean_universe:
            if not isinstance(ticker, str):
                raise InvalidUsage(
                    "Invalid universe. universe must be list of strings."
                )
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
            raise InvalidUsage(
                "Invalid initial_holdings. initial_holdings must be a dictionary."
            )
        unclean_initial_holdings = self.input_json["initial_holdings"]
        for ticker, shares in unclean_initial_holdings.items():
            if not isinstance(ticker, str):
                raise InvalidUsage(
                    "Invalid initial_holdings. initial_holdings must be list of strings."
                )
            if ticker.upper() not in self.state.TICKER_UNIVERSE:
                raise InvalidUsage(f"Invalid symbol in initial_holdings: {ticker}.")
            if not isinstance(shares, (int, float)):
                raise InvalidUsage(
                    "Invalid initial_holdings. initial_holdings must be a dictionary of strings to numbers."
                )
            if shares < 0.00:
                raise InvalidUsage("Invalid initial_holdings. Shares must be positive.")
        self.initial_holdings = {
            s.upper(): unclean_initial_holdings[s] for s in unclean_initial_holdings
        }

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
        ef = EfficientFrontier(mu, cov_matrix, verbose=False)
        raw_weights = ef.max_sharpe()
        cleaned_weights = ef.clean_weights()
        non_zero_weights = {
            key: value
            for key, value in cleaned_weights.items()
            if (round(value, 3) > 0.000)
        }

        # Construct output
        output = {}
        output["optimized_portfolio"] = {}
        for asset, weight in non_zero_weights.items():
            output["optimized_portfolio"][asset] = {
                "shares": round(
                    weight * total_investment / self.state.fetch_ticker_price(asset), 3
                ),
                "total_value": round(weight * total_investment, 2),
                "percent_weight": round(weight * 100, 2),
            }
        if self.exclude_metrics == False:
            output["metrics"] = {
                "total_investment": round(total_investment, 2),
                "expected_annual_return": round(ef.portfolio_performance()[0], 3),
                "annual_volatility": round(ef.portfolio_performance()[1], 3),
                "sharpe_ratio": round(ef.portfolio_performance()[2], 2),
            }
        self.output = flask.jsonify(output)


@wolvwealth.app.route("/api/optimize/", methods=["POST"])
def optimize():
    """Execute Optimization"""
    return Optimization().output
