"""Volatility and Sharpe use excess returns over the risk-free rate for the same calendar days.

Regression: a backtest that held cash the whole time earned exactly the 2%
risk-free rate but reported a Sharpe ratio of -1.3 and 0.07% volatility.
"""

import datetime
import math

import numpy as np
import pandas as pd
import pytest

from wolvwealth.backtest import BacktestConfig, Frequency, compute_metrics, run_backtest, run_report
from wolvwealth.backtest.engine import cash_growth_factors


class AllCash:
    name = "cash"
    last_note = None

    def target_weights(self, window):
        return {}


def all_cash_run(rate=0.02, periods=520):
    index = pd.bdate_range("2021-01-04", periods=periods)
    prices = pd.DataFrame({"A": 100.0}, index=index)
    config = BacktestConfig(
        start=datetime.date(2021, 3, 1),
        end=index[-1].date(),
        universe=("A",),
        frequency=Frequency.MONTHLY,
        lookback_days=60,
        risk_free_rate=rate,
    )
    result = run_backtest(prices, config, AllCash())
    return result, compute_metrics(result.equity, result.rebalances, rate)


class TestHoldingCash:
    @pytest.mark.parametrize("rate", [0.02, 0.05, 0.0, -0.01])
    def test_cash_earns_the_rate_with_zero_volatility_and_zero_sharpe(self, rate):
        result, metrics = all_cash_run(rate)
        assert metrics.cagr == pytest.approx(rate, abs=1e-12)
        assert metrics.annual_volatility == 0.0
        assert metrics.sharpe_ratio == 0.0  # was -1.335 at 2%
        assert metrics.max_drawdown == 0.0 if rate >= 0 else metrics.max_drawdown < 0

    def test_a_falling_market_report_that_holds_cash_shows_sharpe_zero(self):
        rng = np.random.default_rng(7)
        index = pd.bdate_range("2021-01-01", periods=400)
        daily = rng.normal(-0.0015, 0.01, size=(400, 3))
        prices = pd.DataFrame(100 * np.exp(np.cumsum(daily, axis=0)), index=index, columns=["A", "B", "C"])
        config = BacktestConfig(
            start=datetime.date(2021, 9, 1),
            end=index[-1].date(),
            universe=("A", "B", "C"),
            frequency=Frequency.QUARTERLY,
            lookback_days=180,
        )
        report = run_report(prices, config, benchmark_tickers=())
        assert all(event.weights == {} for event in report.result.rebalances)
        assert report.metrics.cagr == pytest.approx(0.02, abs=1e-12)
        assert report.metrics.annual_volatility == 0.0
        assert report.metrics.sharpe_ratio == 0.0
        assert report.to_dict()["metrics"]["sharpe_ratio"] == 0.0


class TestRiskyPortfolios:
    def test_sharpe_matches_a_hand_computed_excess_return_sharpe(self):
        rng = np.random.default_rng(3)
        index = pd.bdate_range("2020-01-01", periods=600)
        equity = pd.Series(100_000 * np.cumprod(1 + rng.normal(0.0006, 0.01, 600)), index=index)
        rate = 0.03
        metrics = compute_metrics(equity, risk_free_rate=rate)

        returns = equity.pct_change().dropna().to_numpy()
        days = np.array([(b - a).days for a, b in zip(index[:-1], index[1:], strict=True)])
        excess = returns - ((1 + rate) ** (days / 365.25) - 1)
        per_year = len(returns) / ((index[-1] - index[0]).days / 365.25)
        expected_sharpe = excess.mean() / excess.std(ddof=1) * math.sqrt(per_year)
        expected_vol = excess.std(ddof=1) * math.sqrt(per_year)

        assert metrics.sharpe_ratio == pytest.approx(expected_sharpe)
        assert metrics.annual_volatility == pytest.approx(expected_vol)

    def test_higher_risk_free_rate_lowers_sharpe(self):
        rng = np.random.default_rng(5)
        index = pd.bdate_range("2020-01-01", periods=400)
        equity = pd.Series(100 * np.cumprod(1 + rng.normal(0.0008, 0.01, 400)), index=index)
        low = compute_metrics(equity, risk_free_rate=0.0).sharpe_ratio
        high = compute_metrics(equity, risk_free_rate=0.05).sharpe_ratio
        assert high < low

    def test_risk_free_component_adds_no_volatility(self):
        """Half in a stock, half in cash: volatility comes from the stock half only."""
        rng = np.random.default_rng(11)
        index = pd.bdate_range("2020-01-01", periods=400)
        stock = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, 400))
        cash = 50_000 * cash_growth_factors(index, 0.02)
        with_cash = compute_metrics(pd.Series(500 * stock + cash, index=index), risk_free_rate=0.02)
        assert 0 < with_cash.annual_volatility < compute_metrics(pd.Series(stock, index=index)).annual_volatility

    def test_riskless_outperformance_has_no_finite_sharpe(self):
        """Every period beats the risk-free return by the same amount: no risk, so no finite Sharpe."""
        index = pd.bdate_range("2020-01-01", periods=300)
        days = np.diff(index.to_numpy()).astype("timedelta64[D]").astype(float)
        returns = (1.02 ** (days / 365.25) - 1) + 0.0001  # constant excess every period
        equity = pd.Series(100 * np.concatenate([[1.0], np.cumprod(1 + returns)]), index=index)
        metrics = compute_metrics(equity, risk_free_rate=0.02)
        assert metrics.annual_volatility == 0.0
        assert math.isnan(metrics.sharpe_ratio)
        assert metrics.to_dict()["sharpe_ratio"] is None
