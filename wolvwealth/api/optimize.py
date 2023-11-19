"""API Portfolio Optimization."""
import flask
from pypfopt import expected_returns, risk_models
from pypfopt.efficient_frontier import EfficientFrontier
import wolvwealth
from wolvwealth.api.main import HISTORICAL_PRICES, fetch_historical_prices

@wolvwealth.app.route('/api/optimize/')
def optimize():
    """Optimization Endpoint."""
    input_json = flask.request.json
    historical_data = fetch_historical_prices()
    mu = expected_returns.mean_historical_return(historical_data)
    cov_matrix = risk_models.sample_cov(historical_data)

    ef = EfficientFrontier(mu, cov_matrix)
    raw_weights = ef.max_sharpe()

    cleaned_weights = ef.clean_weights()
    ef.portfolio_performance(verbose=True)

    if response_format == "shares":
        pass
    else:
        return flask.jsonify(cleaned_weights)
