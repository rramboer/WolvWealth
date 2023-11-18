"""WolvWealth Main API."""

import json
import csv
import flask
import pathlib
import pypfopt
import yfinance as yf
import wolvwealth
import pandas as pd


def fetch_historical_prices():
    """Return current stock price of tickers in S&P 100"""
    tickers = [
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

    start_date = "2008-01-01"
    end_date = "2023-11-17"

    historical_data = yf.download(tickers, start=start_date, end=end_date)["Adj Close"].round(2)
    historical_data.to_csv("historical_prices.csv", index=True)


with wolvwealth.app.app_context():
    # """App Context. Runs before accepting requests."""
    fetch_historical_prices()


@wolvwealth.app.route("/api/")
def api_default():
    """WolvWealth API Usage Endpoint."""
    return flask.jsonify({"/api/": "API Info"})

@wolvwealth.app.route("/api/price/")
def fetch_ticker_price():
    tickers = ["AAPL", "META", "GOOGL", "AMZN", "MSFT"]
    price_dict = {}
    for ticker in tickers:
        price_dict[ticker] = round(yf.Ticker(ticker).history(period="1d")["Close"].iloc[-1], 2)
    return flask.jsonify(price_dict)
