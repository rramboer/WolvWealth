"""Uninvested cash earns the configured risk-free rate, consistently with the Sharpe ratio and the hold-cash rule."""

import datetime
import json

import numpy as np
import pandas as pd
import pytest

from wolvwealth.backtest import BacktestConfig, Frequency, run_backtest
from wolvwealth.backtest.engine import cash_growth_factors

PERIODS = 300
INDEX = pd.bdate_range("2020-01-01", periods=PERIODS)
PRICES = pd.DataFrame({"A": 100 * 1.01 ** np.arange(PERIODS), "B": 50.0}, index=INDEX)


class Fixed:
    name = "fixed"
    last_note = None

    def __init__(self, weights):
        self.weights = weights

    def target_weights(self, window):
        return dict(self.weights)


class CashThenInvest:
    name = "cash_then_invest"
    last_note = None

    def __init__(self):
        self.calls = 0

    def target_weights(self, window):
        self.calls += 1
        return {} if self.calls == 1 else {"A": 1.0}


def config(**overrides):
    defaults = {
        "start": datetime.date(2020, 3, 2),
        "end": INDEX[-1].date(),
        "universe": ("A", "B"),
        "frequency": Frequency.MONTHLY,
        "lookback_days": 60,
        "execution_lag": 0,
        "risk_free_rate": 0.02,
    }
    defaults.update(overrides)
    return BacktestConfig(**defaults)


def years_between(index):
    return (index[-1] - index[0]).days / 365.25


class TestGrowthFactors:
    def test_compounds_over_calendar_days(self):
        days = pd.DatetimeIndex(["2020-01-03", "2020-01-06", "2021-01-03"])  # Friday, Monday, one year later
        growth = cash_growth_factors(days, 0.02)
        assert growth[0] == 1.0
        assert growth[1] == pytest.approx(1.02 ** (3 / 365.25))  # the weekend earns interest too
        assert growth[2] == pytest.approx(1.02 ** (366 / 365.25))

    def test_zero_rate_is_flat(self):
        assert np.all(cash_growth_factors(INDEX, 0.0) == 1.0)


class TestEngine:
    def test_all_cash_grows_at_the_rate(self):
        result = run_backtest(PRICES, config(), Fixed({}))
        equity = result.equity
        expected_end = 100_000 * 1.02 ** years_between(equity.index)
        assert equity.iloc[0] == 100_000
        assert equity.is_monotonic_increasing
        assert equity.iloc[-1] == pytest.approx(expected_end)
        assert result.cash_interest == pytest.approx(expected_end - 100_000)
        assert isinstance(result.cash_interest, float)
        json.dumps(result.to_dict())  # NumPy scalars would fail here

    def test_zero_rate_leaves_cash_flat(self):
        result = run_backtest(PRICES, config(risk_free_rate=0.0), Fixed({}))
        assert result.equity.nunique() == 1
        assert result.cash_interest == 0.0

    def test_negative_rate_erodes_cash(self):
        result = run_backtest(PRICES, config(risk_free_rate=-0.01), Fixed({}))
        assert result.equity.is_monotonic_decreasing
        assert result.cash_interest < 0

    def test_partially_invested_portfolio_accrues_on_the_cash_half_only(self):
        result = run_backtest(PRICES, config(frequency=Frequency.YEARLY), Fixed({"A": 0.5}))
        equity = result.equity
        a = PRICES.loc[equity.index, "A"]
        growth = cash_growth_factors(equity.index, 0.02)
        expected = 50_000 * a / a.iloc[0] + 50_000 * growth
        pd.testing.assert_series_equal(equity, pd.Series(expected, index=equity.index), check_names=False)

    def test_interest_accrued_before_a_trade_is_invested(self):
        """Cash held between rebalances is worth more when it is finally invested."""
        result = run_backtest(PRICES, config(), CashThenInvest())
        first, second = result.rebalances[0], result.rebalances[1]
        assert first.weights == {}
        days = (second.execution_date - first.execution_date).days
        assert second.portfolio_value == pytest.approx(100_000 * 1.02 ** (days / 365.25))
        assert second.weights == {"A": 1.0}
        assert second.cash == pytest.approx(0, abs=1e-6)
        # Fully invested from here on: no further interest.
        assert result.cash_interest == pytest.approx(second.portfolio_value - 100_000)

    def test_fully_invested_portfolio_earns_no_interest(self):
        result = run_backtest(PRICES, config(), Fixed({"A": 1.0}))
        assert result.cash_interest == pytest.approx(0, abs=1e-6)
