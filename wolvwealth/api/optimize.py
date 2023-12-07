"""API Portfolio Optimization."""
import flask  # type: ignore
import wolvwealth

from pypfopt import expected_returns, risk_models  # type: ignore
from pypfopt.efficient_frontier import EfficientFrontier  # type: ignore
from wolvwealth.api.state import ApplicationState
from wolvwealth.api.api_exceptions import InvalidUsage


@wolvwealth.app.route("/api/optimize/", methods=["POST"])
def optimize():
    """Execute Optimization"""

    # Parse JSON
    input_json = flask.request.json

    # Initial Cash
    initial_cash = 0
    if "initial_cash" in input_json:
        if type(initial_cash) not in [int, float]:
            InvalidUsage(
                "Invalid initial_cash", payload="Error: initial_cash must be a number."
            )
        if initial_cash < 0.00:
            InvalidUsage(
                "Invalid initial_cash",
                payload="Error: Initial cash must be non-negative.",
            )
        initial_cash = input_json["initial_cash"]

    # Universe
    universe = TICKER_UNIVERSE
    if "universe" in input_json and len(input_json["universe"]) > 0:
        unclean_universe = input_json["universe"]
        if type(universe) not in [list]:
            InvalidUsage(
                "Invalid universe", payload="Error: Universe must be a list of tickers."
            )
        for ticker in unclean_universe:
            if type(ticker) not in [str] or ticker.upper() not in TICKER_UNIVERSE:
                InvalidUsage(
                    "Invalid universe",
                    payload=f'Error: Invalid ticker "{str(ticker)}".',
                )
        universe = [s.upper() for s in unclean_universe]

    # Initial Holdings
    initial_holdings = {}
    if "initial_holdings" in input_json:
        unclean_initial_holdings = input_json["initial_holdings"]
        if type(unclean_initial_holdings) not in [dict]:
            InvalidUsage(
                "Invalid initial_holdings",
                payload="Error: Initial holdings must be a dictionary.",
            )
        for ticker, shares in unclean_initial_holdings.items():
            if type(ticker) not in [str] or ticker.upper() not in TICKER_UNIVERSE:
                InvalidUsage(
                    "Invalid initial_holdings",
                    payload=f'Error: Invalid ticker "{str(ticker)}" in initial holdings.',
                )
            if type(shares) not in [int, float] or shares < 0.000:
                InvalidUsage(
                    "Invalid initial_holdings",
                    payload=f'Error: Invalid number of shares "{str(shares)}" in initial holdings.',
                )
        initial_holdings = {
            s.upper(): unclean_initial_holdings[s] for s in unclean_initial_holdings
        }

    # Execute Optimization
    if len(universe) > 0:
        filtered_data = HISTORICAL_PRICES[universe]
    else:
        filtered_data = HISTORICAL_PRICES
    mu = expected_returns.mean_historical_return(filtered_data)
    cov_matrix = risk_models.sample_cov(filtered_data)
    ef = EfficientFrontier(mu, cov_matrix)
    raw_weights = ef.max_sharpe()
    cleaned_weights = ef.clean_weights()
    nonzero_dict = {
        key: value
        for key, value in cleaned_weights.items()
        if (round(value, 3) > 0.000)
    }

    # Build Output
    total_investment = initial_cash
    for asset, shares in initial_holdings.items():
        total_investment += shares * fetch_ticker_price(asset)
    output = {}
    output["optimized_portfolio"] = {}
    output["metrics"] = {}
    for asset, shares in nonzero_dict.items():
        output["optimized_portfolio"][asset] = {
            "shares": round(shares * total_investment / fetch_ticker_price(asset), 3),
            "total_value": round(shares * total_investment, 2),
            "percent_weight": round(shares * 100, 2),
        }
    output["metrics"] = {
        "expected_annual_return": round(ef.portfolio_performance()[0], 3),
        "annual_volatility": round(ef.portfolio_performance()[1], 3),
        "sharpe_ratio": round(ef.portfolio_performance()[2], 2),
    }
    return flask.jsonify(output)
