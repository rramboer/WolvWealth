import datetime
from pypfopt import expected_returns, risk_models
from pypfopt.efficient_frontier import EfficientFrontier
from wolvwealth.api.state import ApplicationState
import os


class Frequency:
    """How many days between portfolio reoptimizations."""

    Yearly = datetime.timedelta(days=365)
    Semiannually = datetime.timedelta(days=182)
    Quarterly = datetime.timedelta(days=91)
    Monthly = datetime.timedelta(days=30)
    Biweekly = datetime.timedelta(days=14)
    Weekly = datetime.timedelta(days=7)
    Daily = datetime.timedelta(days=1)


class Optimization:
    def __init__(self, input_: dict, initial_cash_: float = 0.0) -> None:
        """Initialize optimization."""
        self.state = ApplicationState()
        self.set_inputs(input_, initial_cash_)
        self.execute_optimization()

    def set_inputs(self, input_: dict, initial_cash_: float) -> None:
        self.initial_cash = initial_cash_  # $0.00
        self.universe = self.state.TICKER_UNIVERSE[:50]  # Top 500 stocks by market cap.
        self.exclude_metrics = False  # Include metrics in output.
        self.max_positions = 10  # Maximum number of stocks in portfolio. May overpwoer max_weight. Default is -1.
        self.max_weight = 1  # No weights higher than this. Can be ignored if max_positions is set.
        self.min_universal_weight = 0.00  # Every stock must have at least this weight.
        self.weight_threshold = 0.0005  # Ignore stocks with weights below this. May cause allocation < 100%.
        self.initial_holdings = input_

    def execute_optimization(self) -> None:
        """Run optimization."""
        total_investment = self.initial_cash
        for asset, shares in self.initial_holdings.items():
            total_investment += shares * self.state.fetch_ticker_price(asset)
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
            print(f"Optimization Error. Check your inputs and constraints. Infeasible.")
            print(e)
            exit(1)

        # Construct output
        output = {}
        output["optimized_portfolio"] = {}
        for asset, weight in cleaned_weights.items():
            output["optimized_portfolio"][asset] = {
                "shares": round(weight * total_investment / self.state.fetch_ticker_price(asset), 4),
                "value": round(weight * total_investment, 2),
                "percent_weight": round(weight * 100, 2),
            }
        if self.exclude_metrics == False:
            metrics = ef.portfolio_performance()
            output["metrics"] = {
                "portfolio_value": round(total_investment, 2),
                "expected_annual_return": round(metrics[0], 3),
                "annual_volatility": round(metrics[1], 3),
                "sharpe_ratio": round(metrics[2], 2),
            }
        self.output = output


HIST_PRICES_BACKUP = ApplicationState().HISTORICAL_PRICES


def run_optimizer(date: str, opt_previous: dict):
    # print("Running optimization for: " + date)

    # Reformat optimization results from output of old response to input of new request
    new_initial_holdings = {}
    opt_previous = opt_previous["optimized_portfolio"]
    for ticker in opt_previous:
        new_initial_holdings[ticker] = opt_previous[ticker]["shares"]

    # Change historical prices to use current prices
    ApplicationState().HISTORICAL_PRICES = HIST_PRICES_BACKUP[:date]

    # Run optimization
    opt = Optimization(new_initial_holdings).output
    return opt


def get_next_date(date: str):
    next_date = (datetime.datetime.strptime(date, "%Y-%m-%d") + FREQ).strftime("%Y-%m-%d")
    if next_date not in ApplicationState().HISTORICAL_PRICES.index:
        next_date = (datetime.timedelta(days=1) + datetime.datetime.strptime(next_date, "%Y-%m-%d")).strftime(
            "%Y-%m-%d"
        )
    return next_date


def percent_change(old, new):
    return ((new - old) / old * 100).round(2)


def output_results(returns: dict):
    # print("=====  BACKTEST RESULTS  =====")
    start_value = returns[START_DATE][0]
    for date in returns:
        print(date + ": " + str(returns[date][0]) + " (" + str(returns[date][1]) + "%)")
        end_value = returns[date][0]
    print(f"=====  TOTAL RETURN: {percent_change(start_value, end_value)}% =====")


def main():
    # print("Running optimization for: " + START_DATE)
    ApplicationState().HISTORICAL_PRICES = HIST_PRICES_BACKUP[:START_DATE]
    opt = Optimization({}, INITIAL_CASH).output
    curr_date = get_next_date(START_DATE)
    total_returns = {
        START_DATE: [opt["metrics"]["portfolio_value"], 0],
    }
    prev_total = opt["metrics"]["portfolio_value"]
    while curr_date < END_DATE:
        opt = run_optimizer(curr_date, opt)
        total_returns[curr_date] = [
            opt["metrics"]["portfolio_value"],
            percent_change(prev_total, opt["metrics"]["portfolio_value"]),
        ]
        prev_total = opt["metrics"]["portfolio_value"]
        curr_date = get_next_date(curr_date)
    output_results(total_returns)


if __name__ == "__main__":
    START_DATE = "2019-01-01"
    while START_DATE not in ApplicationState().HISTORICAL_PRICES.index:
        START_DATE = (datetime.timedelta(days=1) + datetime.datetime.strptime(START_DATE, "%Y-%m-%d")).strftime(
            "%Y-%m-%d"
        )
    END_DATE = datetime.date.today().strftime("%Y-%m-%d")
    INITIAL_CASH = 100000
    FREQ_ARRAY = [
        Frequency.Yearly,
        Frequency.Semiannually,
        Frequency.Quarterly,
        Frequency.Monthly,
        Frequency.Weekly,
        Frequency.Daily,
    ]
    for FREQ in FREQ_ARRAY[:-1]:
        print("=====  FREQUENCY: " + str(FREQ) + "  =====")
        main()
        print()
