"""API Portfolio Optimization."""
import flask
from pypfopt import expected_returns, risk_models
from pypfopt.efficient_frontier import EfficientFrontier
import wolvwealth
from wolvwealth.api.main import TICKER_UNIVERSE, HISTORICAL_PRICES, TICKER_UNIVERSE_PRICES


@wolvwealth.app.route('/api/optimize/', methods=["POST"])
def optimize():
    """Optimization Endpoint."""
    print("hit optimize")
    # Parse JSON
    input_json = flask.request.json
    print("INPUT_JSON=", input_json)
    # Initial Cash
    initial_cash = 0
    if "initial_cash" in input_json:
        # convert to int or float (check if decimal in string)
        if "." in input_json["initial_cash"]:
            initial_cash = float(input_json["initial_cash"])
        else:
            initial_cash = int(input_json["initial_cash"])
        if type(initial_cash) not in [int, float] or initial_cash < 0.00:
            flask.abort(
                400, description="Error: Initial cash must be non-negative.")

    # Response Type
    response_type = "shares"
    if "response_type" in input_json:
        response_type = input_json["response_type"]
        if type(response_type) not in [str] or (response_type != "shares" and response_type != "weights"):
            flask.abort(
                400, description="Error: Response type must be either 'shares' or 'weights'.")

    # Universe
    universe = TICKER_UNIVERSE
    if "universe" in input_json:
        unclean_universe = input_json["universe"]
        if type(universe) not in [list]:
            flask.abort(
                400, description="Error: Universe must be a list of tickers.")
        for ticker in unclean_universe:
            if type(ticker) not in [str] or ticker.upper() not in TICKER_UNIVERSE:
                flask.abort(
                    400, description=f'Error: Invalid ticker "{str(ticker)}".')
        universe = [s.upper() for s in unclean_universe]

    # Initial Holdings
    initial_holdings = {}
    print(input_json["initial_holdings"])
    if "initial_holdings" in input_json:
        unclean_initial_holdings = input_json["initial_holdings"]
        if type(unclean_initial_holdings) not in [dict]:
            flask.abort(
                400, description="Error: Initial holdings must be a dictionary.")
        for ticker, shares in unclean_initial_holdings.items():
            if type(ticker) not in [str] or ticker.upper() not in TICKER_UNIVERSE:
                flask.abort(
                    400, description=f'Error: Invalid ticker "{str(ticker)}" in initial holdings.')
            # convert shares to int or float (check if decimal in string)
            if "." in input_json["initial_holdings"]:
                shares = float(shares)
            else:
                shares = int(shares)
            if type(shares) not in [int, float] or shares < 0.000:
                flask.abort(
                    400, description=f'Error: Invalid number of shares "{str(shares)}" in initial holdings.')
        initial_holdings = {
            s.upper(): unclean_initial_holdings[s] for s in unclean_initial_holdings}

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
    nonzero_dict = {key: value for key,
                    value in cleaned_weights.items() if (round(value, 3) > 0.000)}
    ef.portfolio_performance(verbose=True)

    # Build Output
    if response_type == "shares":
        # Calculate total investment value
        total_investment = initial_cash
        for asset, shares in initial_holdings.items():
            # convert shares to int or float (check if decimal in string)
            if "." in input_json["initial_holdings"]:
                shares = float(shares)
            else:
                shares = int(shares)
            total_investment += shares * TICKER_UNIVERSE_PRICES[asset]
        output = {}
        for asset, shares in nonzero_dict.items():
            output[asset] = {
                "shares": round(shares * total_investment / TICKER_UNIVERSE_PRICES[asset], 3),
                "total_value": round(shares * total_investment, 2),
                "percent_weight": str(round(shares * 100, 2)) + "%"
            }
        return flask.jsonify(output)
    else:  # response_type == "weights"
        return flask.jsonify(cleaned_weights)
