"""API Portfolio Optimization."""
import datetime
import math
import flask
import wolvwealth

from pypfopt import expected_returns, risk_models
from pypfopt.efficient_frontier import EfficientFrontier
from wolvwealth.api.state import ApplicationState
from wolvwealth.api.api_exceptions import InvalidUsage
from wolvwealth.api.auth import check_api_key
from wolvwealth.api.tax_lots import Lot


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
        # Top 500 stocks by market cap, leaving out any with no current price.
        self.universe = [t for t in self.state.TICKER_UNIVERSE[:500] if self.state.has_current_price(t)]
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
        self.parse_prices()
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

    def parse_prices(self) -> None:
        """Parse optional current prices that replace the last price in the data."""
        self.price_overrides = {}
        if "prices" not in self.input_json:
            return
        if not isinstance(self.input_json["prices"], dict):
            raise InvalidUsage("Invalid prices. prices must be a dictionary of tickers to prices.")
        for ticker, price in self.input_json["prices"].items():
            if ticker.upper() not in self.state.TICKER_UNIVERSE:
                raise InvalidUsage(f"Invalid symbol in prices: {ticker}.")
            if not is_finite_number(price) or price <= 0:
                raise InvalidUsage(f"Invalid price for {ticker}. Prices must be numbers greater than 0.")
            self.price_overrides[ticker.upper()] = float(price)

    def price(self, ticker: str) -> float:
        """Return the price used for ticker: the one from the request if given, else the last in the data."""
        if ticker in self.price_overrides:
            return self.price_overrides[ticker]
        return self.state.fetch_ticker_price(ticker)

    def parse_universe(self) -> None:
        if "universe" not in self.input_json:
            return
        if not isinstance(self.input_json["universe"], list):
            raise InvalidUsage("Invalid universe. universe must be a list.")
        unclean_universe = self.input_json["universe"]
        if len(unclean_universe) == 0:  # If empty universe, use default
            return
        # Tickers named directly, as opposed to ones added by a topN alias.
        named = {t.upper() for t in unclean_universe if isinstance(t, str) and t.upper() in self.state.TICKER_UNIVERSE}
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
        # A named ticker with no current price is an error; one added by an alias is dropped.
        for ticker in self.universe:
            if ticker in named and not self.state.has_current_price(ticker):
                raise InvalidUsage(self.no_price_message(ticker, "universe"))
        self.universe = [t for t in self.universe if self.state.has_current_price(t)]

    def no_price_message(self, ticker: str, field: str) -> str:
        """Error message for a ticker in field that has no price on the last day of the data."""
        return (
            f"No current price for {ticker} in {field}. It last traded on {self.state.last_traded_date(ticker)}, "
            f"before the price data ends on {self.state.price_date()}."
        )

    def parse_initial_holdings(self) -> None:
        """Parse holdings. Each value is a share count or a list of tax lots."""
        self.initial_holdings = {}
        self.lots = {}  # Ticker to list of Lot, only for holdings given as lots.
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
            if isinstance(shares, list):
                lots = self.parse_lots(ticker.upper(), shares)
                self.lots[ticker.upper()] = lots
                shares = round(sum(lot.shares for lot in lots), 8)
            else:
                if not isinstance(shares, (int, float)):
                    raise InvalidUsage(
                        "Invalid initial_holdings. initial_holdings must be a dictionary of strings to numbers."
                    )
                if shares < 0.00:
                    raise InvalidUsage("Invalid initial_holdings. Shares must be positive.")
                self.lots.pop(ticker.upper(), None)  # A later share count replaces earlier lots.
            if not self.state.has_current_price(ticker.upper()):
                raise InvalidUsage(self.no_price_message(ticker.upper(), "initial_holdings"))
            self.initial_holdings[ticker.upper()] = shares

    @staticmethod
    def parse_lots(ticker: str, unclean_lots: list) -> list:
        """Parse a list of lots: {"shares": n, "cost_per_share": n, "acquired": "YYYY-MM-DD"}."""
        if len(unclean_lots) == 0:
            raise InvalidUsage(f"Invalid initial_holdings for {ticker}. A list of lots must have at least one lot.")
        lots = []
        for i, lot in enumerate(unclean_lots):
            where = f"Invalid lot {i + 1} for {ticker}."
            if not isinstance(lot, dict):
                raise InvalidUsage(f"{where} Each lot must be an object with shares, cost_per_share and acquired.")
            for field in ("shares", "cost_per_share", "acquired"):
                if field not in lot:
                    raise InvalidUsage(f"{where} Missing {field}.")
            if not is_finite_number(lot["shares"]) or lot["shares"] <= 0:
                raise InvalidUsage(f"{where} shares must be a number greater than 0.")
            if not is_finite_number(lot["cost_per_share"]) or lot["cost_per_share"] < 0:
                raise InvalidUsage(f"{where} cost_per_share must be a number, 0 or more.")
            acquired = parse_date(lot["acquired"])
            if acquired is None:
                raise InvalidUsage(f"{where} acquired must be a date in YYYY-MM-DD format.")
            lots.append(Lot(float(lot["shares"]), float(lot["cost_per_share"]), acquired, i))
        return lots

    def execute_optimization(self) -> None:
        """Run optimization."""
        total_investment = self.initial_cash
        for asset, shares in self.initial_holdings.items():
            total_investment += shares * self.price(asset)
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
        target_shares = {}
        for asset, weight in cleaned_weights.items():
            target_shares[asset] = round(weight * total_investment / self.price(asset), 4)
            output["optimized_portfolio"][asset] = {
                "shares": target_shares[asset],
                "value": round(weight * total_investment, 2),
                "percent_weight": round(weight * 100, 2),
            }
        output["trades"] = compute_trades(self.initial_holdings, target_shares, self.price)
        if "prices" in self.input_json:
            used = dict.fromkeys([*self.initial_holdings, *target_shares])
            output["price_sources"] = {
                "price_data_date": self.state.price_date(),
                "from_request": sorted(t for t in used if t in self.price_overrides),
                "from_price_data": sorted(t for t in used if t not in self.price_overrides),
            }
        if self.exclude_metrics == False:
            metrics = ef.portfolio_performance()
            output["metrics"] = {
                "portfolio_value": round(total_investment, 2),
                "expected_annual_return": round(metrics[0], 3),
                "annual_volatility": round(metrics[1], 3),
                "sharpe_ratio": round(metrics[2], 2),
                "num_assets": len(cleaned_weights),
            }
        self.output = flask.jsonify(output)


def is_finite_number(value) -> bool:
    """Return True if value is an int or float (not a bool) and not NaN or infinite."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def parse_date(value):
    """Parse a YYYY-MM-DD string into a date, or return None if it isn't one."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def compute_trades(initial_holdings: dict, target_shares: dict, fetch_price) -> list:
    """Return the trades that turn initial_holdings into target_shares.

    Sells come first so their proceeds are available for the buys. Within
    each side, trades are ordered by dollar value, largest first. A holding
    with no target is sold in full, using the exact share count held.
    """
    sells = []
    buys = []
    for ticker in dict.fromkeys([*initial_holdings, *target_shares]):
        held = initial_holdings.get(ticker, 0)
        if ticker in target_shares:
            delta = round(target_shares[ticker] - held, 4)
        else:
            delta = -held  # Close the position completely.
        if delta == 0:
            continue
        price = fetch_price(ticker)
        trade = {
            "action": "sell" if delta < 0 else "buy",
            "ticker": ticker,
            "shares": abs(delta),
            "price": round(float(price), 2),
            "value": round(float(abs(delta) * price), 2),
        }
        (sells if delta < 0 else buys).append(trade)
    sells.sort(key=lambda t: t["value"], reverse=True)
    buys.sort(key=lambda t: t["value"], reverse=True)
    return sells + buys


@wolvwealth.app.route("/api/optimize/", methods=["POST"])
def optimize():
    """Execute Optimization"""
    return Optimization().output
