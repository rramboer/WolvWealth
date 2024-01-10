import wolvwealth
import csv
import pandas as pd
import datetime
import sys
import requests


URL = "http://localhost:8000/api/optimize"
KEY = "qD7jUXoAwEUJzMwRIGJBLdfZ5b_0-2-K-MPPBQfAW8w"


class Frequency:
    """How many days between portfolio reoptimizations."""

    Yearly = datetime.timedelta(days=365)
    Semiannually = datetime.timedelta(days=182)
    Quarterly = datetime.timedelta(days=91)
    Monthly = datetime.timedelta(days=30)
    Biweekly = datetime.timedelta(days=14)
    Weekly = datetime.timedelta(days=7)
    Daily = datetime.timedelta(days=1)


FREQ = Frequency.Quarterly

CASH = 100000

START_DATE = datetime.date(2006, 1, 1)
END_DATE = datetime.date.today()

UNIVERSE = ["SPY"]

MAX_WEIGHT = 1
MAX_POSITIONS = 10

PRICES = pd.read_csv("historical_prices.csv", parse_dates=True, index_col="Date")


def ticker_price(ticker: str, date: str) -> float:
    """Return stock price of ticker at given date."""
    print("Getting price for " + ticker)
    return PRICES[ticker][date]


def optimize(value: float, date: str):
    """Send POST request to API to optimize portfolio."""
    # Cut off bottom of csv file at date
    with open("historical_prices_backup.csv", "r") as f:
        reader = csv.reader(f)
        data = list(reader)
    for i in range(len(data)):
        if data[i][0] == date:
            data = data[:i]
            break
    with open("historical_prices.csv", "w") as f:
        writer = csv.writer(f)
        writer.writerows(data)

    input_json = {
        "initial_cash": value,
        "universe": UNIVERSE,
        "constraints": {
            "max_positions": MAX_POSITIONS,
        },
    }
    response = requests.post(URL, json=input_json, headers={"Authorization": KEY})
    if response.status_code != 200:
        print("Error: " + response.text)
        sys.exit(1)
    return response.json()["optimized_portfolio"]


def backtest():
    """Backtest the optimizer."""
    cur_date = START_DATE
    curr_portfolio = {}
    new_portfolio = {}
    total_value = CASH
    total_value_history = {}
    while cur_date <= END_DATE:
        if cur_date.weekday() == 5 or cur_date.weekday() == 6 or cur_date.strftime("%Y-%m-%d") not in PRICES.index:
            cur_date += datetime.timedelta(days=1)
            continue
        # At this point we know we have data for the current date
        print("Optimizing portfolio for " + cur_date.strftime("%Y-%m-%d"))
        # Calculate total value of portfolio determined by selling all current holdings
        for ticker in curr_portfolio:
            total_value += ticker_price(ticker, cur_date.strftime("%Y-%m-%d")) * curr_portfolio[ticker]["shares"]
        # Optimize portfolio
        new_portfolio = optimize(total_value)
        # Calculate total value of portfolio determined by buying new holdings
        for ticker in new_portfolio:
            total_value -= ticker_price(ticker, cur_date.strftime("%Y-%m-%d")) * new_portfolio[ticker]["shares"]
        # Update portfolio
        curr_portfolio = new_portfolio
        # Update total value history
        total_value_history[cur_date.strftime("%Y-%m-%d")] = total_value
        # Increment date
        cur_date += FREQ
    # Print results
    print("Total value: " + str(total_value))
    print("Total value history: " + str(total_value_history))


if __name__ == "__main__":
    backtest()
