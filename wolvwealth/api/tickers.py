"""API Tickers Endpoints."""
import json
import flask
# import yfinance
import wolvwealth

@wolvwealth.app.route('/api/tickers/')
def get_tickers():
    """Returns JSON List of all supported tickers."""
    tickers = {}
    with open("prices.json", "r", encoding="utf-8") as json_file:
        tickers = json.load(json_file)
    data = []
    for tick in tickers:
        data.append(f"{tick}")
    tickers["tickers"] = data
    return flask.jsonify(**tickers)

@wolvwealth.app.route('/api/ticker/<ticker>')
def get_ticker(ticker):
    """Returns data of a specific ticker."""
    context = {"ticker": str(ticker),
               "price": -1,
               "PTE": 0}    
    return flask.jsonify(**context)
