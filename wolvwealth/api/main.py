"""WolvWealth Main API."""

import json
import flask
# import pathlib
# import pypfopt
import yfinance
import wolvwealth


def retrieve_price():
    """Return current stock price of ticker"""
    sp100_tickers = [
        "AAPL", "ABBV", "ABT", "ACN", "ADBE", "AIG", "ALL", "AMGN", "AMT", "AMZN",
        "AXP", "BA", "BAC", "BIIB", "BK", "BKNG", "BLK", "BMY", "C", "CAT",
        "CHTR", "CL", "CMCSA", "COF", "COP", "COST", "CRM", "CSCO", "CVS", "CVX",
        "DD", "DHR", "DIS", "DOW", "DUK", "EMR", "EXC", "F", "META", "FDX", "GD",
        "GE", "GILD", "GM", "GOOGL", "GS", "HD", "HON", "IBM", "INTC", "INTU", "ISRG",
        "JNJ", "JPM", "KHC", "KMI", "KO", "LLY", "LMT", "LOW", "MA", "MCD", "MDLZ",
        "MDT", "MET", "MMM", "MO", "MRK", "MS", "MSFT", "NEE", "NFLX", "NKE", "NVDA",
        "ORCL", "OXY", "PEP", "PFE", "PG", "PM", "PYPL", "QCOM", "RTX", "SBUX", "SLB",
        "SO", "SPG", "T", "TGT", "TMO", "TMUS", "TSLA", "TXN", "UNH", "UNP", "UPS",
        "USB", "V", "VZ", "WBA", "WFC", "WM", "WMT", "XOM",
    ]
    price_dict = {}
    for ticker in sp100_tickers:
        price_dict[ticker] = round(yfinance.Ticker(ticker).history(period="1d")["Close"].iloc[-1], 2)
        print(ticker + " price loaded.")
    with open("prices.json", "w", encoding="utf-8") as json_file:
        json.dump(price_dict, json_file, indent=4)
    print("PRICES JSON UPDATE SUCCESSFUL.")


# with wolvwealth.app.app_context():
#     # """App Context. Runs before accepting requests."""
#     retrieve_price()


@wolvwealth.app.route("/api/")
def api_default():
    """WolvWealth API Usage Endpoint."""
    return flask.jsonify({"/api/": "API Info"})
