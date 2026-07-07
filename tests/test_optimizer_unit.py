"""Unit tests for pure optimizer logic and helpers (no HTTP, no network)."""

import pandas as pd
import pytest

from tests.conftest import FIXTURES_DIR
from wolvwealth.api.api_exceptions import InvalidUsage
from wolvwealth.api.optimize import Optimization
from wolvwealth.api.state import ApplicationState
from wolvwealth.optimizer_core import build_portfolio_output, compute_max_sharpe_portfolio
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


class TestTimeutil:
    def test_winter_display_is_est(self):
        assert utc_to_eastern_display("2024-01-15 12:00:00") == "2024-01-15 07:00 AM ET"

    def test_summer_display_is_edt(self):
        assert utc_to_eastern_display("2024-07-15 12:00:00") == "2024-07-15 08:00 AM ET"

    def test_eastern_to_utc_storage_roundtrip(self):
        assert eastern_to_utc_storage("2024-07-15 08:00:00") == "2024-07-15 12:00:00"
        assert eastern_to_utc_storage("2024-01-15 07:00:00") == "2024-01-15 12:00:00"
