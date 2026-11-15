"""Tests for the wolvwealth.backtest package.

Engine arithmetic is checked on tiny synthetic price tables where the right
answer can be worked out by hand; the optimizer strategy is checked on the
fixture CSV. Each regression test names the bug it guards against.
"""

import datetime
import json

import numpy as np
import pandas as pd
import pytest

from tests.conftest import FIXTURES_DIR
from wolvwealth.backtest import (
    BacktestConfig,
    BuyAndHoldStrategy,
    EqualWeightStrategy,
    Frequency,
    MaxSharpeStrategy,
    compute_metrics,
    run_backtest,
    run_report,
)
from wolvwealth.backtest.cli import main
from wolvwealth.backtest.engine import eligible_assets
from wolvwealth.backtest.schedule import rebalance_dates
from wolvwealth.optimizer_core import compute_max_sharpe_portfolio, portfolio_performance
from wolvwealth.optimizer_params import Constraints, InvalidOptimizationInput

START = datetime.date(2020, 1, 1)


class FixedWeights:
    """Test strategy: always the same target weights."""

    name = "fixed"

    def __init__(self, weights: dict[str, float]) -> None:
        self.weights = weights
        self.calls = 0

    def target_weights(self, window: pd.DataFrame) -> dict[str, float]:
        self.calls += 1
        return dict(self.weights)


def business_days(start: str, periods: int) -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=periods)


def synthetic_prices(periods: int = 300) -> pd.DataFrame:
    """A: +1% per day, B: flat at 50, C: lists halfway through, D: flat at 20 with a gap."""
    index = business_days("2019-01-01", periods)
    a = 100 * 1.01 ** np.arange(periods)
    c = np.full(periods, np.nan)
    c[periods // 2 :] = 10.0
    d = np.full(periods, 20.0)
    d[periods // 2 : periods // 2 + 5] = np.nan
    return pd.DataFrame({"A": a, "B": 50.0, "C": c, "D": d}, index=index)


def config(**overrides) -> BacktestConfig:
    defaults = {
        "start": START,
        "end": datetime.date(2020, 3, 1),
        "universe": ("A", "B"),
        "initial_cash": 100_000.0,
        "frequency": Frequency.MONTHLY,
        "lookback_days": 60,
        "execution_lag": 0,
    }
    defaults.update(overrides)
    return BacktestConfig(**defaults)


def fixture_prices() -> pd.DataFrame:
    return pd.read_csv(FIXTURES_DIR / "historical_prices.csv", parse_dates=True, index_col="Date")


class TestSchedule:
    def test_monthly_steps_do_not_drift(self):
        """Regression: timedelta(days=30) made 'monthly' slide a day or two every month."""
        calendar = pd.date_range("2023-01-31", "2023-07-31", freq="D")
        dates = [d.date().isoformat() for d in rebalance_dates(calendar, Frequency.MONTHLY)]
        assert dates == [
            "2023-01-31",
            "2023-02-28",
            "2023-03-31",
            "2023-04-30",
            "2023-05-31",
            "2023-06-30",
            "2023-07-31",
        ]

    def test_snaps_forward_to_trading_day(self):
        days = business_days("2023-01-02", 60)  # 2023-01-02 is a Monday
        dates = rebalance_dates(days, Frequency.MONTHLY)
        assert dates[0] == pd.Timestamp("2023-01-02")
        assert dates[1] == pd.Timestamp("2023-02-02")  # Feb 2 is a Thursday, already a trading day
        assert dates[2] == pd.Timestamp("2023-03-02")

    def test_daily_covers_every_trading_day(self):
        days = business_days("2023-01-02", 10)
        assert rebalance_dates(days, Frequency.DAILY) == list(days)

    def test_empty_calendar(self):
        assert rebalance_dates(pd.DatetimeIndex([]), Frequency.WEEKLY) == []


class TestEligibility:
    def test_excludes_assets_without_history_at_window_start(self):
        """Regression: pre-IPO tickers (all-NaN columns) crashed the optimizer with 'NaN or Inf'."""
        prices = synthetic_prices()
        window = prices.iloc[100:160]
        assert eligible_assets(window) == ["A", "B", "D"]

    def test_tolerates_small_gaps(self):
        prices = synthetic_prices()
        window = prices.iloc[140:200]  # D has 5 NaNs in 60 rows (8%)
        assert "D" in eligible_assets(window)

    def test_excludes_assets_with_large_gaps(self):
        prices = synthetic_prices()
        window = prices.iloc[148:160]  # 5 NaNs in 12 rows
        assert "D" not in eligible_assets(window)


class TestEngine:
    def test_fully_invested_tracks_the_asset(self):
        prices = synthetic_prices()
        result = run_backtest(prices, config(), FixedWeights({"A": 1.0}))
        a = prices.loc[result.equity.index, "A"]
        expected = 100_000 * a / a.iloc[0]
        pd.testing.assert_series_equal(result.equity, expected, check_names=False)

    def test_equity_curve_covers_every_trading_day_through_end(self):
        """Regression: the old script dropped the period after the last rebalance."""
        prices = synthetic_prices()
        cfg = config(end=datetime.date(2020, 2, 20))
        result = run_backtest(prices, cfg, FixedWeights({"A": 1.0}))
        in_range = prices.index[(prices.index >= "2020-01-01") & (prices.index <= "2020-02-20")]
        assert list(result.equity.index) == list(in_range)
        assert result.equity.notna().all()
        last_rebalance = result.rebalances[-1].execution_date
        assert result.equity.index[-1] > last_rebalance
        assert result.end_value > result.rebalances[-1].portfolio_value  # A kept rising after the trade

    def test_execution_lag_trades_a_day_later(self):
        """Regression: the optimizer used to buy at the very close it had just observed."""
        prices = synthetic_prices()
        result = run_backtest(prices, config(execution_lag=1), FixedWeights({"A": 1.0}))
        first = result.rebalances[0]
        assert first.execution_date == result.equity.index[1]
        assert first.decision_date == result.equity.index[0]
        assert result.equity.iloc[0] == 100_000  # still cash on day one
        assert result.equity.iloc[1] == pytest.approx(100_000)  # bought at day-two close, no gain yet
        assert result.equity.iloc[2] == pytest.approx(101_000)

    def test_cash_is_tracked_when_weights_do_not_sum_to_one(self):
        """Regression: unallocated weight used to vanish from the portfolio."""
        prices = synthetic_prices()
        result = run_backtest(prices, config(), FixedWeights({"A": 0.5}))
        first = result.rebalances[0]
        assert first.cash == pytest.approx(50_000)
        assert result.equity.iloc[0] == pytest.approx(100_000)

    def test_transaction_costs_are_paid_from_the_portfolio(self):
        prices = synthetic_prices()
        cfg = config(transaction_cost_bps=10, frequency=Frequency.YEARLY)
        result = run_backtest(prices, cfg, FixedWeights({"A": 1.0}))
        first = result.rebalances[0]
        # Invest I such that I + 0.001 * I = 100000  ->  I = 100000 / 1.001
        assert first.transaction_cost == pytest.approx(100_000 / 1.001 * 0.001, rel=1e-6)
        assert first.cash == pytest.approx(0, abs=1e-6)
        assert first.cash >= 0
        assert first.turnover == pytest.approx(1 / 1.001, rel=1e-6)
        assert result.equity.iloc[0] == pytest.approx(100_000 - first.transaction_cost)

    def test_rebalancing_only_charges_on_traded_value(self):
        prices = synthetic_prices()
        cfg = config(transaction_cost_bps=10, universe=("A", "B"))
        result = run_backtest(prices, cfg, FixedWeights({"A": 0.5, "B": 0.5}))
        second = result.rebalances[1]
        assert 0 < second.turnover < 0.5  # only the drift between A and B is traded
        assert second.transaction_cost < result.rebalances[0].transaction_cost

    def test_pre_ipo_asset_is_skipped_until_it_has_history(self):
        prices = synthetic_prices()
        # C first trades around 2019-07-31; a 60-day window ending 2019-08-01 starts before that.
        cfg = config(universe=("A", "B", "C"), start=datetime.date(2019, 8, 1), end=datetime.date(2019, 9, 1))
        result = run_backtest(prices, cfg, EqualWeightStrategy())
        assert "C" not in result.rebalances[0].weights
        assert result.rebalances[0].eligible_assets == 2

    def test_buy_and_hold_trades_once(self):
        prices = synthetic_prices()
        cfg = config(frequency=Frequency.WEEKLY, universe=("B",))
        result = run_backtest(prices, cfg, BuyAndHoldStrategy({"B": 1.0}))
        assert len(result.rebalances) == 1
        assert result.equity.iloc[-1] == pytest.approx(100_000)

    def test_strategy_failure_holds_previous_positions(self):
        class Flaky(FixedWeights):
            def target_weights(self, window):
                self.calls += 1
                if self.calls > 1:
                    from wolvwealth.backtest.strategies import StrategyError

                    raise StrategyError("boom")
                return dict(self.weights)

        prices = synthetic_prices()
        result = run_backtest(prices, config(), Flaky({"A": 1.0}))
        assert len(result.rebalances) == 1
        assert any("boom" in warning for warning in result.warnings)
        assert result.equity.notna().all()

    def test_unknown_ticker_is_rejected(self):
        with pytest.raises(ValueError, match="ZZZ"):
            run_backtest(synthetic_prices(), config(universe=("A", "ZZZ")), FixedWeights({"A": 1.0}))

    def test_max_sharpe_strategy_on_fixture_data(self):
        prices = fixture_prices()
        cfg = BacktestConfig(
            start=datetime.date(2023, 6, 1),
            end=prices.index[-1].date(),
            universe=tuple(prices.columns),
            frequency=Frequency.QUARTERLY,
            lookback_days=120,
            constraints=Constraints(max_positions=3, max_weight=0.5),
        )
        result = run_backtest(prices, cfg, MaxSharpeStrategy(cfg.constraints))
        assert result.rebalances
        for event in result.rebalances:
            assert len(event.weights) <= 3
            assert max(event.weights.values()) <= 0.5 + 1e-6
            assert sum(event.weights.values()) == pytest.approx(1.0)
            assert event.expected_performance is not None
        assert result.equity.notna().all()


class TestConfig:
    def test_rejects_inverted_dates(self):
        with pytest.raises(InvalidOptimizationInput):
            config(start=datetime.date(2021, 1, 1), end=datetime.date(2020, 1, 1))

    def test_rejects_infeasible_constraints(self):
        with pytest.raises(InvalidOptimizationInput):
            config(constraints=Constraints(max_positions=2, max_weight=0.2))

    def test_to_dict_is_json_serializable(self):
        json.dumps(config().to_dict())


class TestMetrics:
    def test_constant_growth(self):
        index = business_days("2020-01-01", 253)
        equity = pd.Series(100.0 * 1.001 ** np.arange(253), index=index)
        metrics = compute_metrics(equity)
        assert metrics.total_return == pytest.approx(1.001**252 - 1)
        assert metrics.max_drawdown == 0
        assert metrics.annual_volatility == pytest.approx(0, abs=1e-9)
        assert metrics.trading_days == 253

    def test_max_drawdown(self):
        equity = pd.Series([100.0, 120.0, 90.0, 110.0], index=business_days("2020-01-01", 4))
        assert compute_metrics(equity).max_drawdown == pytest.approx(-0.25)

    def test_cagr_matches_total_return_over_one_year(self):
        index = pd.DatetimeIndex([pd.Timestamp("2020-01-01"), pd.Timestamp("2020-12-31")])
        equity = pd.Series([100.0, 110.0], index=index)
        metrics = compute_metrics(equity)
        assert metrics.cagr == pytest.approx(0.10, abs=0.001)

    def test_to_dict_replaces_nan(self):
        equity = pd.Series([100.0, 100.0], index=business_days("2020-01-01", 2))
        data = compute_metrics(equity).to_dict()
        assert data["sharpe_ratio"] is None
        json.dumps(data)


class TestOptimizerCoreFixes:
    def test_max_positions_respects_max_weight(self):
        """Regression: truncate-then-renormalize could push a weight past max_weight."""
        weights, _ = compute_max_sharpe_portfolio(fixture_prices(), max_positions=2, max_weight=0.6)
        assert len(weights) <= 2
        assert max(weights.values()) <= 0.6 + 1e-6
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_reported_performance_describes_returned_weights(self):
        """Regression: metrics used to describe the pre-truncation portfolio."""
        from pypfopt import expected_returns, risk_models

        prices = fixture_prices()
        weights, performance = compute_max_sharpe_portfolio(prices, max_positions=2)
        mu = expected_returns.mean_historical_return(prices)
        cov = risk_models.exp_cov(prices)
        assert performance == pytest.approx(portfolio_performance(weights, mu, cov))

    def test_weights_always_sum_to_one(self):
        weights, _ = compute_max_sharpe_portfolio(fixture_prices(), weight_threshold=0.2)
        assert sum(weights.values()) == pytest.approx(1.0)


class TestServiceAndCli:
    def test_report_includes_benchmarks_and_warnings(self):
        prices = fixture_prices()
        cfg = BacktestConfig(
            start=datetime.date(2023, 7, 1),
            end=prices.index[-1].date(),
            universe=tuple(prices.columns),
            lookback_days=120,
        )
        report = run_report(prices, cfg, benchmark_tickers=("SPY",))
        assert "equal_weight" in report.benchmarks
        assert "SPY" not in report.benchmarks  # fixture has no SPY column
        assert any("SPY" in warning for warning in report.warnings)
        assert any("Survivorship" in warning for warning in report.warnings)
        data = report.to_dict()
        json.dumps(data)
        assert data["metrics"]["rebalances"] == len(report.result.rebalances)
        assert len(data["result"]["equity"]) == len(report.result.equity)

    def test_cli_text_output(self, capsys):
        code = main(["--start", "2023-07-01", "--universe", "top3", "--lookback-days", "120", "--benchmark"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Total return" in out
        assert "equal_weight" in out

    def test_cli_json_output(self, capsys):
        code = main(["--start", "2023-07-01", "--universe", "AAPL", "MSFT", "--lookback-days", "120", "--json"])
        assert code == 0
        data = json.loads(capsys.readouterr().out)
        assert data["config"]["universe"] == ["AAPL", "MSFT"]
        assert "equity" in data["result"]

    def test_terminal_progress_bar(self):
        import io

        from wolvwealth.backtest.cli import TerminalProgressBar
        from wolvwealth.backtest.progress import Progress

        stream = io.StringIO()
        bar = TerminalProgressBar(stream)
        bar(Progress("optimizer", 3, 6, "Optimizing rebalance 4 of 5"))
        assert "#" * 15 + "-" * 15 in stream.getvalue()
        assert "50%" in stream.getvalue()
        bar.close()
        assert stream.getvalue().endswith("\r")

    def test_cli_rejects_bad_ticker(self, capsys):
        code = main(["--universe", "NOPE"])
        assert code == 2
        assert "NOPE" in capsys.readouterr().err
