"""WolvWealth Main API."""

import json
import csv
import flask
import pathlib
import pypfopt
import yfinance as yf
import wolvwealth
import pandas as pd

TICKER_UNIVERSE =  [
    "AAPL", "ABBV", "ABT", "ACN", "ADBE", "AIG", "ALL", "AMGN", "AMT", "AMZN",
    "AXP", "BA", "BAC", "BIIB", "BK", "BKNG", "BLK", "BMY", "C", "CAT",
    "CHTR", "CL", "CMCSA", "COF", "COP", "COST", "CRM", "CSCO", "CVS", "CVX",
    "DD", "DHR", "DIS", "DOW", "DUK", "EMR", "EXC", "F", "META", "FDX", "GD",
    "GE", "GILD", "GM", "GOOGL", "GS", "HD", "HON", "IBM", "INTC", "INTU", "ISRG",
    "JNJ", "JPM", "KHC", "KMI", "KO", "LLY", "LMT", "LOW", "MA", "MCD", "MDLZ",
    "MDT", "MET", "MMM", "MO", "MRK", "MS", "MSFT", "NEE", "NFLX", "NKE", "NVDA",
    "ORCL", "OXY", "PEP", "PFE", "PG", "PM", "PYPL", "QCOM", "RTX", "SBUX", "SLB",
    "SO", "SPG", "T", "TGT", "TMO", "TMUS", "TSLA", "TXN", "UNH", "UNP", "UPS",
    "USB", "V", "VZ", "WBA", "WFC", "WM", "WMT", "XOM"
]

def fetch_historical_prices():
    return pd.read_csv("historical_prices.csv", parse_dates=True, index_col="Date")

def fetch_ticker_prices():
    price_dict = {}
    for ticker in TICKER_UNIVERSE:
        price_dict[ticker] = HISTORICAL_PRICES[ticker].iloc[-1]
    return price_dict

def save_historical_prices():
    """Return current stock price of tickers in S&P 100"""
    start_date = "2008-01-01"
    end_date = "2023-11-17"

    historical_data = yf.download(TICKER_UNIVERSE, start=start_date, end=end_date)["Adj Close"].round(2)
    historical_data.to_csv("historical_prices.csv", index=True)

# with wolvwealth.app.app_context():
#     # """App Context. Runs before accepting requests."""
#     fetch_historical_prices()

# GLOBAL VARIABLES
HISTORICAL_PRICES = fetch_historical_prices()
TICKER_UNIVERSE_PRICES = fetch_ticker_prices()

@wolvwealth.app.route("/api/")
def api_default():
    """WolvWealth API Usage Endpoint."""
    return flask.jsonify({"/api/": "API Info"})
