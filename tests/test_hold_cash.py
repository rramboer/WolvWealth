"""When no asset is expected to beat the risk-free rate, the answer is "hold cash", not an error.

Regression: the optimize API reported this as "Optimization Error ...
Infeasible", and backtests logged it as a failure and kept stale positions.
"""

import datetime

import cvxpy
import numpy as np
import pandas as pd
import pytest

import wolvwealth.api.optimize as optimize_api
from tests.conftest import DEV_USER, DEV_USER_TOKEN, FIXTURES_DIR
from wolvwealth.backtest import BacktestConfig, Frequency, MaxSharpeStrategy, run_backtest, run_report
from wolvwealth.optimizer_core import NoAssetBeatsRiskFreeRate, build_portfolio_output, compute_max_sharpe_portfolio


def fixture_prices() -> pd.DataFrame:
    return pd.read_csv(FIXTURES_DIR / "historical_prices.csv", parse_dates=True, index_col="Date")


def remaining_uses(client) -> int:
    account = client.post("/api/account/", json={"username": DEV_USER}, headers={"Authorization": DEV_USER_TOKEN})
    return account.get_json()["optimizations_remaining"]


class TestCore:
    def test_raises_a_specific_answer_when_nothing_beats_the_rate(self):
        prices = fixture_prices()
        with pytest.raises(NoAssetBeatsRiskFreeRate) as excinfo:
            compute_max_sharpe_portfolio(prices, risk_free_rate=10.0)  # 1000%: nothing beats it
        answer = excinfo.value
        assert answer.best_asset in prices.columns
        assert answer.risk_free_rate == 10.0
        assert answer.performance == (10.0, 0.0, 0.0)
        message = str(answer)
        assert "1000.0% risk-free rate" in message
        assert f"best: {answer.best_asset}" in message
        assert "hold cash" in message
        assert "nfeasible" not in message

    def test_is_not_a_value_error(self):
        """Generic `except ValueError` handlers must not swallow a valid answer as a failure."""
        assert not issubclass(NoAssetBeatsRiskFreeRate, ValueError)

    def test_one_asset_above_the_rate_is_enough(self):
        from pypfopt import expected_returns

        prices = fixture_prices()
        mu = expected_returns.mean_historical_return(prices).sort_values()
        between_best_two = float(mu.iloc[-2] + mu.iloc[-1]) / 2  # only the best asset beats this
        weights, _ = compute_max_sharpe_portfolio(prices, risk_free_rate=between_best_two)
        assert list(weights) == [mu.index[-1]]

    def test_output_reports_cash(self):
        hold_cash = build_portfolio_output({}, 5000, lambda _: 1.0, performance=(0.02, 0.0, 0.0), notes=["why"])
        assert hold_cash["optimized_portfolio"] == {}
        assert hold_cash["cash"] == {"value": 5000.0, "percent_weight": 100.0}
        assert hold_cash["notes"] == ["why"]
        assert hold_cash["metrics"]["num_assets"] == 0
        assert hold_cash["metrics"]["expected_annual_return"] == 0.02

        invested = build_portfolio_output({"AAPL": 0.6, "MSFT": 0.4}, 1000, lambda _: 100.0)
        assert invested["cash"] == {"value": 0.0, "percent_weight": 0.0}
        assert invested["notes"] == []


class TestOptimizeApi:
    def answer(self, *args, **kwargs):
        raise NoAssetBeatsRiskFreeRate(0.02, "AAPL", -0.05)

    def test_returns_a_hold_cash_answer_not_an_error(self, client, monkeypatch):
        monkeypatch.setattr(optimize_api, "compute_max_sharpe_portfolio", self.answer)
        response = client.post(
            "/api/optimize/",
            json={"initial_cash": 1000, "universe": ["top3"]},
            headers={"Authorization": DEV_USER_TOKEN},
        )
        assert response.status_code == 200, response.get_json()
        data = response.get_json()
        assert data["optimized_portfolio"] == {}
        assert data["cash"] == {"value": 1000.0, "percent_weight": 100.0}
        assert data["metrics"]["num_assets"] == 0
        assert data["metrics"]["annual_volatility"] == 0.0
        assert "hold cash" in data["notes"][0]
        assert "AAPL" in data["notes"][0]

    def test_a_hold_cash_answer_is_charged_like_any_answer(self, client, monkeypatch):
        monkeypatch.setattr(optimize_api, "compute_max_sharpe_portfolio", self.answer)
        before = remaining_uses(client)
        client.post("/api/optimize/", json={"initial_cash": 1000}, headers={"Authorization": DEV_USER_TOKEN})
        assert remaining_uses(client) == before - 1

    def test_normal_answers_report_zero_cash_and_no_notes(self, client):
        response = client.post(
            "/api/optimize/",
            json={"initial_cash": 1000, "universe": ["top3"]},
            headers={"Authorization": DEV_USER_TOKEN},
        )
        data = response.get_json()
        assert data["cash"]["value"] == 0.0
        assert data["notes"] == []

    def test_a_real_solver_failure_no_longer_claims_infeasible_constraints(self, client, monkeypatch):
        def solver_failure(*args, **kwargs):
            raise cvxpy.error.SolverError("solver blew up")

        monkeypatch.setattr(optimize_api, "compute_max_sharpe_portfolio", solver_failure)
        response = client.post("/api/optimize/", json={"initial_cash": 1000}, headers={"Authorization": DEV_USER_TOKEN})
        assert response.status_code == 400
        message = response.get_json()["error"]["message"]
        assert "solver could not find a portfolio" in message
        assert "nfeasible" not in message


class TestBacktest:
    def test_strategy_answers_with_cash_and_says_why(self):
        strategy = MaxSharpeStrategy(risk_free_rate=10.0)
        weights = strategy.target_weights(fixture_prices().iloc[:120])
        assert weights == {}
        assert strategy.last_performance == (10.0, 0.0, 0.0)
        assert "hold cash" in strategy.last_note

    def test_strategy_clears_the_note_on_a_normal_decision(self):
        strategy = MaxSharpeStrategy(risk_free_rate=10.0)
        strategy.target_weights(fixture_prices().iloc[:120])
        strategy.risk_free_rate = 0.0
        assert strategy.target_weights(fixture_prices().iloc[:120])
        assert strategy.last_note is None

    def test_engine_sells_into_cash_instead_of_keeping_stale_positions(self):
        """Regression: the old behaviour held the previous stocks, which is not what the model recommends."""
        index = pd.bdate_range("2020-01-01", periods=200)
        prices = pd.DataFrame({"A": 100 * 1.01 ** np.arange(200)}, index=index)

        class InvestThenCash:
            name = "invest_then_cash"
            last_note = None

            def __init__(self):
                self.calls = 0

            def target_weights(self, window):
                self.calls += 1
                if self.calls == 1:
                    return {"A": 1.0}
                self.last_note = "no asset beats cash"
                return {}

        config = BacktestConfig(
            start=datetime.date(2020, 3, 2),
            end=index[-1].date(),
            universe=("A",),
            frequency=Frequency.MONTHLY,
            lookback_days=60,
            execution_lag=0,
        )
        result = run_backtest(prices, config, InvestThenCash())
        first, second = result.rebalances[0], result.rebalances[1]
        assert first.weights == {"A": 1.0}
        assert second.weights == {}
        assert second.shares == {}
        assert second.cash == pytest.approx(second.portfolio_value)
        assert second.note == "no asset beats cash"
        assert second.to_dict()["note"] == "no asset beats cash"
        after = result.equity.loc[second.execution_date :]
        assert after.nunique() == 1  # flat in cash while A keeps rising (cash earns nothing yet)

    def test_report_explains_cash_periods_once_not_as_failures(self):
        """A falling market at a normal 2% rate, like 2022: every rebalance holds cash."""
        rng = np.random.default_rng(7)
        index = pd.bdate_range("2021-01-01", periods=400)
        daily = rng.normal(-0.0015, 0.01, size=(400, 3))  # roughly -30% a year, noisy
        prices = pd.DataFrame(100 * np.exp(np.cumsum(daily, axis=0)), index=index, columns=["A", "B", "C"])
        config = BacktestConfig(
            start=datetime.date(2021, 9, 1),
            end=index[-1].date(),
            universe=("A", "B", "C"),
            frequency=Frequency.QUARTERLY,
            lookback_days=180,
        )
        report = run_report(prices, config, benchmark_tickers=())
        events = report.result.rebalances
        assert events and all(event.weights == {} for event in events)
        assert all("2.0% risk-free rate" in event.note and "hold cash" in event.note for event in events)
        warnings = report.result.warnings
        assert not any("optimization failed" in warning for warning in warnings)
        summary = [warning for warning in warnings if "moved to cash" in warning]
        assert len(summary) == 1
        assert summary[0].startswith(f"{len(events)} of {len(events)} rebalance(s) moved to cash")
        assert report.result.equity.nunique() == 1  # never invested (cash earns nothing yet)
        # The equal-weight benchmark is unaffected: it does not consult the risk-free rate.
        assert report.benchmarks["equal_weight"].rebalances[0].weights
