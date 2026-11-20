"""Unit tests for pure optimizer logic and helpers (no HTTP, no network)."""

import warnings

import numpy as np
import pandas as pd
import pytest
from pypfopt import risk_models

from tests.conftest import FIXTURES_DIR
from wolvwealth.api.api_exceptions import InvalidUsage
from wolvwealth.api.optimize import Optimization
from wolvwealth.api.state import ApplicationState
from wolvwealth.optimizer_core import build_portfolio_output, compute_max_sharpe_portfolio, exp_cov
from wolvwealth.timeutil import eastern_to_utc_storage, utc_to_eastern_display


def make_optimization(input_json: dict) -> Optimization:
    """Build an Optimization for parse testing without hitting request/auth code."""
    optimization = Optimization.__new__(Optimization)
    optimization.state = ApplicationState()
    optimization.set_defaults()
    optimization.input_json = input_json
    return optimization


def fixture_prices() -> pd.DataFrame:
    return pd.read_csv(FIXTURES_DIR / "historical_prices.csv", parse_dates=True, index_col="Date")


class TestParseUniverse:
    def test_top_n_alias_expands(self):
        optimization = make_optimization({"universe": ["top2"]})
        optimization.parse_universe()
        assert optimization.universe == ApplicationState().TICKER_UNIVERSE[:2]

    def test_top_n_alias_is_case_insensitive(self):
        optimization = make_optimization({"universe": ["TOP2"]})
        optimization.parse_universe()
        assert len(optimization.universe) == 2

    def test_bogus_symbol_after_alias_is_still_validated(self):
        """Regression test: mutating-while-iterating used to skip the symbol after topN."""
        optimization = make_optimization({"universe": ["top2", "BOGUS"]})
        with pytest.raises(InvalidUsage):
            optimization.parse_universe()

    def test_lowercase_ticker_accepted(self):
        optimization = make_optimization({"universe": ["aapl"]})
        optimization.parse_universe()
        assert optimization.universe == ["AAPL"]

    def test_duplicates_removed(self):
        optimization = make_optimization({"universe": ["AAPL", "aapl", "top2"]})
        optimization.parse_universe()
        assert sorted(optimization.universe) == sorted(set(optimization.universe))

    def test_top_n_clamped_to_universe_size(self):
        optimization = make_optimization({"universe": ["top999"]})
        optimization.parse_universe()
        assert optimization.universe == ApplicationState().TICKER_UNIVERSE

    def test_empty_universe_uses_default(self):
        optimization = make_optimization({"universe": []})
        optimization.parse_universe()
        assert optimization.universe == ApplicationState().TICKER_UNIVERSE[:500]


class TestParseConstraints:
    def test_min_universal_weight_infeasible_for_universe(self):
        optimization = make_optimization({"constraints": {"min_universal_weight": 0.5}})
        with pytest.raises(InvalidUsage, match="min_universal_weight"):
            optimization.parse_constraints()

    def test_max_positions_times_max_weight_must_cover_portfolio(self):
        optimization = make_optimization({"constraints": {"max_weight": 0.2, "max_positions": 2}})
        with pytest.raises(InvalidUsage, match="max_positions"):
            optimization.parse_constraints()


class TestOptimizerCore:
    def test_weights_sum_to_one(self):
        weights, performance = compute_max_sharpe_portfolio(fixture_prices())
        assert sum(weights.values()) == pytest.approx(1.0, abs=0.01)
        assert len(performance) == 3

    def test_max_positions_limits_and_renormalizes(self):
        weights, _ = compute_max_sharpe_portfolio(fixture_prices(), max_positions=2)
        assert len(weights) <= 2
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-6)

    def test_build_portfolio_output(self):
        weights = {"AAPL": 0.6, "MSFT": 0.4}
        output = build_portfolio_output(weights, 1000, lambda _: 100.0, performance=(0.1, 0.2, 0.5))
        assert output["optimized_portfolio"]["AAPL"] == {"shares": 6.0, "value": 600.0, "percent_weight": 60.0}
        assert output["metrics"]["portfolio_value"] == 1000
        assert output["metrics"]["num_assets"] == 2

    def test_build_portfolio_output_without_metrics(self):
        output = build_portfolio_output({"AAPL": 1.0}, 500, lambda _: 50.0)
        assert "metrics" not in output


def random_prices(columns: int, rows: int = 400, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0005, 0.015, (rows, columns)) + rng.normal(0, 0.01, (rows, 1))  # Shared market factor.
    index = pd.bdate_range("2020-01-01", periods=rows)
    return pd.DataFrame(100 * np.cumprod(1 + returns, axis=0), index=index, columns=[f"S{i}" for i in range(columns)])


class TestExpCov:
    """The vectorized covariance must reproduce pypfopt's pair-by-pair risk_models.exp_cov."""

    def assert_matches_pypfopt(self, prices: pd.DataFrame) -> None:
        with warnings.catch_warnings():  # Both warn when they repair a non-positive-semidefinite matrix.
            warnings.simplefilter("ignore", UserWarning)
            expected = risk_models.exp_cov(prices)
            actual = exp_cov(prices)
        assert actual.index.equals(expected.index)
        assert actual.columns.equals(expected.columns)
        np.testing.assert_allclose(actual.to_numpy(), expected.to_numpy(), rtol=1e-10, atol=1e-14, equal_nan=True)

    def test_fixture(self):
        self.assert_matches_pypfopt(fixture_prices())

    def test_many_assets(self):
        self.assert_matches_pypfopt(random_prices(40))

    def test_staggered_histories(self):
        """Assets that start trading later (IPOs) or stop early use only the days they have."""
        prices = random_prices(6)
        prices.iloc[:150, 1] = np.nan
        prices.iloc[300:, 2] = np.nan
        prices.iloc[100:120, 3] = np.nan  # A gap in the middle.
        self.assert_matches_pypfopt(prices)

    def test_pairs_with_no_shared_day_are_nan(self):
        prices = random_prices(3)
        prices.iloc[200:, 0] = np.nan
        prices.iloc[:201, 1] = np.nan
        actual = exp_cov(prices)
        assert np.isnan(actual.loc["S0", "S1"])
        assert not np.isnan(actual.loc["S0", "S2"])
        self.assert_matches_pypfopt(prices)

    def test_matrix_repair(self):
        """Pairs estimated over different days can make the matrix non-PSD; both repair it the same way."""
        prices = random_prices(30, rows=80)
        rng = np.random.default_rng(0)
        for column in range(30):  # A gap of 10-40 days somewhere in every asset's history.
            start = rng.integers(0, 60)
            prices.iloc[start : start + rng.integers(10, 40), column] = np.nan
        with pytest.warns(UserWarning, match="non positive semidefinite"):
            repaired = exp_cov(prices)
        assert np.linalg.eigvalsh(repaired.to_numpy()).min() > -1e-10
        self.assert_matches_pypfopt(prices)

    def test_span_and_frequency(self):
        prices = random_prices(5)
        expected = risk_models.exp_cov(prices, span=60, frequency=52)
        np.testing.assert_allclose(exp_cov(prices, span=60, frequency=52).to_numpy(), expected.to_numpy(), rtol=1e-10)


class TestTimeutil:
    def test_winter_display_is_est(self):
        assert utc_to_eastern_display("2024-01-15 12:00:00") == "2024-01-15 07:00 AM ET"

    def test_summer_display_is_edt(self):
        assert utc_to_eastern_display("2024-07-15 12:00:00") == "2024-07-15 08:00 AM ET"

    def test_eastern_to_utc_storage_roundtrip(self):
        assert eastern_to_utc_storage("2024-07-15 08:00:00") == "2024-07-15 12:00:00"
        assert eastern_to_utc_storage("2024-01-15 07:00:00") == "2024-01-15 12:00:00"
