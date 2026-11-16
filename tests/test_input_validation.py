"""Malformed numeric input must be refused before any optimizer runs.

Python's json module accepts NaN/Infinity literals, and NaN compares false
to everything, so naive range checks let it through. These tests cover each
entry point: the shared validators, the domain objects (which the CLI builds
directly), the backtest request parser, and both JSON endpoints.
"""

import datetime
import math

import pandas as pd
import pytest

from tests.conftest import DEV_USER_TOKEN, FIXTURES_DIR
from wolvwealth.backtest import BacktestConfig, Frequency
from wolvwealth.backtest.cli import main
from wolvwealth.backtest.request import parse_backtest_request
from wolvwealth.optimizer_params import (
    Constraints,
    InvalidOptimizationInput,
    parse_constraints,
    require_integer,
    require_number,
)

NAN, INF = float("nan"), float("inf")
NOT_NUMBERS = [NAN, INF, -INF, True, False, "100", None, [1], {"v": 1}]
NOT_INTEGERS = [NAN, INF, True, 3.0, "3", None]


def fixture_prices() -> pd.DataFrame:
    return pd.read_csv(FIXTURES_DIR / "historical_prices.csv", parse_dates=True, index_col="Date")


def fixture_universe() -> list[str]:
    return [line.split(",")[0] for line in (FIXTURES_DIR / "ticker_universe.csv").read_text().split()]


class TestSharedValidators:
    @pytest.mark.parametrize("value", NOT_NUMBERS)
    def test_require_number_rejects_non_finite_and_non_numbers(self, value):
        with pytest.raises(InvalidOptimizationInput, match="amount"):
            require_number(value, "amount")

    def test_require_number_ranges(self):
        assert require_number(5, "x", minimum=0, maximum=10) == 5.0
        with pytest.raises(InvalidOptimizationInput, match="at least 0"):
            require_number(-1, "x", minimum=0)
        with pytest.raises(InvalidOptimizationInput, match="greater than 0"):
            require_number(0, "x", minimum=0, exclusive_minimum=True)
        with pytest.raises(InvalidOptimizationInput, match="at most 10"):
            require_number(11, "x", maximum=10)

    @pytest.mark.parametrize("value", NOT_INTEGERS)
    def test_require_integer_rejects_non_integers(self, value):
        with pytest.raises(InvalidOptimizationInput, match="count"):
            require_integer(value, "count")

    def test_require_integer_ranges(self):
        assert require_integer(3, "n", minimum=1, maximum=5) == 3
        with pytest.raises(InvalidOptimizationInput):
            require_integer(0, "n", minimum=1)
        with pytest.raises(InvalidOptimizationInput):
            require_integer(6, "n", maximum=5)


class TestConstraints:
    @pytest.mark.parametrize("field", ["max_weight", "min_universal_weight"])
    @pytest.mark.parametrize("value", [NAN, INF, -INF, True, "0.5", -0.1, 1.5])
    def test_parse_rejects_bad_weights(self, field, value):
        with pytest.raises(InvalidOptimizationInput, match=field):
            parse_constraints({field: value}, universe_size=10)

    @pytest.mark.parametrize("value", [NAN, INF, True, 2.0, "2", -1])
    def test_parse_rejects_bad_max_positions(self, value):
        with pytest.raises(InvalidOptimizationInput, match="max_positions"):
            parse_constraints({"max_positions": value}, universe_size=10)

    def test_parse_rejects_unknown_keys(self):
        with pytest.raises(InvalidOptimizationInput, match="Unknown constraint"):
            parse_constraints({"max_wieght": 0.5}, universe_size=10)

    def test_zero_max_positions_means_no_limit(self):
        assert parse_constraints({"max_positions": 0}, universe_size=10).max_positions == -1

    def test_max_weight_zero_rejected(self):
        with pytest.raises(InvalidOptimizationInput, match="max_weight"):
            parse_constraints({"max_weight": 0}, universe_size=10)

    def test_dataclass_itself_validates(self):
        """Direct construction (CLI, library code) cannot bypass the checks."""
        with pytest.raises(InvalidOptimizationInput):
            Constraints(max_weight=NAN)
        with pytest.raises(InvalidOptimizationInput):
            Constraints(max_positions=0)
        with pytest.raises(InvalidOptimizationInput):
            Constraints(weight_threshold=-1)


class TestBacktestConfig:
    def make(self, **overrides):
        base = {
            "start": datetime.date(2020, 1, 1),
            "end": datetime.date(2021, 1, 1),
            "universe": ("AAPL",),
        }
        base.update(overrides)
        return BacktestConfig(**base)

    @pytest.mark.parametrize("value", [NAN, INF, -INF, 0, -5, 1e13, True, "1000"])
    def test_initial_cash(self, value):
        with pytest.raises(InvalidOptimizationInput, match="initial_cash"):
            self.make(initial_cash=value)

    @pytest.mark.parametrize("value", [NAN, INF, -1, 1001, True])
    def test_transaction_cost(self, value):
        with pytest.raises(InvalidOptimizationInput, match="transaction_cost_bps"):
            self.make(transaction_cost_bps=value)

    @pytest.mark.parametrize("value", [29, 15 * 365 + 1, 3.0, True, NAN])
    def test_lookback_days(self, value):
        with pytest.raises(InvalidOptimizationInput, match="lookback_days"):
            self.make(lookback_days=value)

    @pytest.mark.parametrize("value", [-1, 31, 1.0, NAN])
    def test_execution_lag(self, value):
        with pytest.raises(InvalidOptimizationInput, match="execution_lag"):
            self.make(execution_lag=value)

    @pytest.mark.parametrize("value", [NAN, INF, 2.0, -2.0])
    def test_risk_free_rate(self, value):
        with pytest.raises(InvalidOptimizationInput, match="risk_free_rate"):
            self.make(risk_free_rate=value)

    def test_universe_entries_must_be_tickers(self):
        with pytest.raises(InvalidOptimizationInput, match="universe"):
            self.make(universe=("AAPL", ""))
        with pytest.raises(InvalidOptimizationInput, match="universe"):
            self.make(universe=())

    def test_frequency_must_be_enum(self):
        with pytest.raises(InvalidOptimizationInput, match="frequency"):
            self.make(frequency="monthly")

    def test_valid_config_still_builds(self):
        config = self.make(frequency=Frequency.WEEKLY, transaction_cost_bps=5, initial_cash=1)
        assert config.initial_cash == 1


class TestBacktestRequestParser:
    def parse(self, **fields):
        payload = {"start": "2023-07-01", "universe": ["AAPL", "MSFT"], **fields}
        return parse_backtest_request(payload, ticker_universe=fixture_universe(), prices=fixture_prices())

    @pytest.mark.parametrize("field", ["initial_cash", "transaction_cost_bps"])
    @pytest.mark.parametrize("value", NOT_NUMBERS)
    def test_numeric_fields_reject_garbage(self, field, value):
        with pytest.raises(InvalidOptimizationInput, match=field):
            self.parse(**{field: value})

    @pytest.mark.parametrize("field", ["lookback_days", "execution_lag"])
    @pytest.mark.parametrize("value", NOT_INTEGERS)
    def test_integer_fields_reject_garbage(self, field, value):
        with pytest.raises(InvalidOptimizationInput, match=field):
            self.parse(**{field: value})

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("initial_cash", 0),
            ("initial_cash", -100),
            ("initial_cash", 1e13),
            ("transaction_cost_bps", -1),
            ("transaction_cost_bps", 5000),
            ("lookback_days", 10),
            ("lookback_days", 10**9),
            ("execution_lag", -1),
            ("execution_lag", 500),
        ],
    )
    def test_out_of_range_values(self, field, value):
        with pytest.raises(InvalidOptimizationInput, match=field):
            self.parse(**{field: value})

    def test_nan_in_constraints(self):
        with pytest.raises(InvalidOptimizationInput, match="max_weight"):
            self.parse(constraints={"max_weight": NAN})

    def test_valid_request_still_parses(self):
        request = self.parse(initial_cash=5000, transaction_cost_bps=2.5, lookback_days=120, execution_lag=0)
        assert request.config.initial_cash == 5000


def raw_json_post(client, url, body: str):
    """Send a hand-written JSON body so NaN/Infinity literals reach the server verbatim."""
    return client.post(url, data=body, content_type="application/json", headers={"Authorization": DEV_USER_TOKEN})


class TestEndpointsRefuseNonFiniteJson:
    @pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
    def test_optimize_rejects_literal_cash(self, client, literal):
        response = raw_json_post(client, "/api/optimize/", f'{{"initial_cash": {literal}, "universe": ["top3"]}}')
        assert response.status_code == 400
        assert "Parse Error" in response.get_json()["error"]["message"]

    @pytest.mark.parametrize("literal", ["NaN", "Infinity"])
    def test_backtest_rejects_literal_cash(self, client, literal):
        body = f'{{"start": "2023-07-01", "universe": ["top3"], "initial_cash": {literal}}}'
        response = raw_json_post(client, "/api/backtest/", body)
        assert response.status_code == 400
        assert "Parse Error" in response.get_json()["error"]["message"]

    def test_optimize_rejects_nan_holdings(self, client):
        response = raw_json_post(client, "/api/optimize/", '{"initial_holdings": {"AAPL": NaN}}')
        assert response.status_code == 400

    def test_optimize_rejects_huge_cash(self, client):
        response = raw_json_post(client, "/api/optimize/", '{"initial_cash": 1e30, "universe": ["top3"]}')
        assert response.status_code == 400
        assert "initial_cash" in response.get_json()["error"]["message"]

    def test_optimize_rejects_bool_cash(self, client):
        response = raw_json_post(client, "/api/optimize/", '{"initial_cash": true, "universe": ["top3"]}')
        assert response.status_code == 400
        assert "initial_cash" in response.get_json()["error"]["message"]

    def test_optimize_rejects_negative_shares(self, client):
        response = raw_json_post(client, "/api/optimize/", '{"initial_holdings": {"AAPL": -3}}')
        assert response.status_code == 400
        assert "AAPL" in response.get_json()["error"]["message"]

    def test_backtest_rejects_out_of_range_without_charging(self, client):
        def uses():
            account = client.post(
                "/api/account/", json={"username": "devuser"}, headers={"Authorization": DEV_USER_TOKEN}
            )
            return account.get_json()["optimizations_remaining"]

        before = uses()
        body = '{"start": "2023-07-01", "universe": ["top3"], "transaction_cost_bps": 99999}'
        response = raw_json_post(client, "/api/backtest/", body)
        assert response.status_code == 400
        assert "transaction_cost_bps" in response.get_json()["error"]["message"]
        assert uses() == before


class TestCli:
    @pytest.mark.parametrize(
        "argv",
        [
            ["--cash", "nan"],
            ["--cash", "inf"],
            ["--cash", "0"],
            ["--cost-bps", "-5"],
            ["--lookback-days", "5"],
            ["--execution-lag", "99"],
            ["--max-weight", "nan"],
        ],
    )
    def test_bad_numbers_exit_2(self, argv, capsys):
        code = main(["--start", "2023-07-01", "--universe", "AAPL", "MSFT", *argv])
        assert code == 2
        assert "error:" in capsys.readouterr().err

    def test_nan_is_not_a_valid_float_for_our_purposes(self):
        assert math.isnan(float("nan"))  # argparse's float() happily accepts it; our config must not.


def remaining_uses(client) -> int:
    account = client.post("/api/account/", json={"username": "devuser"}, headers={"Authorization": DEV_USER_TOKEN})
    return account.get_json()["optimizations_remaining"]


class TestInfeasibleWeightsAreRejectedUpFront:
    """Regression: top5 with max_weight 0.1 can only ever invest 50%, but was accepted."""

    @pytest.mark.parametrize(
        ("universe_size", "raw"),
        [
            (5, {"max_weight": 0.1}),  # 5 x 10% = 50%
            (5, {"max_weight": 0.19}),  # 5 x 19% = 95%
            (10, {"max_positions": 20, "max_weight": 0.05}),  # capped to 10 positions x 5%
        ],
    )
    def test_parse_constraints(self, universe_size, raw):
        with pytest.raises(InvalidOptimizationInput, match="Infeasible"):
            parse_constraints(raw, universe_size=universe_size)

    def test_boundary_is_feasible(self):
        assert parse_constraints({"max_weight": 0.2}, universe_size=5).max_weight == 0.2

    def test_backtest_request(self):
        payload = {"start": "2023-07-01", "universe": ["top5"], "constraints": {"max_weight": 0.1}}
        with pytest.raises(InvalidOptimizationInput, match="Infeasible"):
            parse_backtest_request(payload, ticker_universe=fixture_universe(), prices=fixture_prices())

    def test_backtest_endpoint_rejects_without_charging(self, client):
        before = remaining_uses(client)
        response = client.post(
            "/api/backtest/",
            json={"start": "2023-07-01", "universe": ["top5"], "constraints": {"max_weight": 0.1}},
            headers={"Authorization": DEV_USER_TOKEN},
        )
        assert response.status_code == 400
        assert "Infeasible" in response.get_json()["error"]["message"]
        assert remaining_uses(client) == before

    def test_optimize_endpoint_rejects_without_charging(self, client):
        before = remaining_uses(client)
        response = client.post(
            "/api/optimize/",
            json={"initial_cash": 1000, "universe": ["top5"], "constraints": {"max_weight": 0.1}},
            headers={"Authorization": DEV_USER_TOKEN},
        )
        assert response.status_code == 400
        assert "Infeasible" in response.get_json()["error"]["message"]
        assert remaining_uses(client) == before


class TestRejectedOptimizeRequestsAreFree:
    """Regression: /api/optimize/ charged a use before validating anything."""

    @pytest.mark.parametrize(
        "payload",
        [
            {},  # zero investment
            {"initial_cash": -5},
            {"initial_cash": 1000, "universe": ["BOGUS"]},
            {"initial_cash": 1000, "constraints": {"min_universal_weight": 0.5}},
        ],
    )
    def test_invalid_payload_costs_nothing(self, client, payload):
        before = remaining_uses(client)
        response = client.post("/api/optimize/", json=payload, headers={"Authorization": DEV_USER_TOKEN})
        assert response.status_code == 400
        assert remaining_uses(client) == before

    def test_valid_payload_still_costs_one(self, client):
        before = remaining_uses(client)
        response = client.post(
            "/api/optimize/",
            json={"initial_cash": 1000, "universe": ["top3"]},
            headers={"Authorization": DEV_USER_TOKEN},
        )
        assert response.status_code == 200
        assert remaining_uses(client) == before - 1

    def test_auth_is_still_checked_first(self, client):
        assert client.post("/api/optimize/", json={}).status_code == 401
        assert client.post("/api/optimize/", json={}, headers={"Authorization": "bogus"}).status_code == 403


class TestDatesOutsideThePriceData:
    """Regression: a range before the first price date passed validation, was charged, then failed in the worker."""

    def parse(self, **fields):
        payload = {"universe": ["AAPL", "MSFT"], "lookback_days": 30, **fields}
        return parse_backtest_request(payload, ticker_universe=fixture_universe(), prices=fixture_prices())

    def test_start_before_first_price_date(self):
        first = fixture_prices().index[0].date()
        with pytest.raises(InvalidOptimizationInput, match=f"before the first available price date \\({first}\\)"):
            self.parse(start="2010-01-01", end="2023-06-01")

    def test_range_entirely_before_the_data(self):
        with pytest.raises(InvalidOptimizationInput, match="before the first available price date"):
            self.parse(start="1990-01-01", end="2000-01-01")

    def test_end_after_last_price_date(self):
        last = fixture_prices().index[-1].date()
        with pytest.raises(InvalidOptimizationInput, match=f"after the last available price date \\({last}\\)"):
            self.parse(start="2023-03-01", end="2030-01-01")

    def test_start_on_first_date_without_enough_history_is_rejected(self):
        """Starting on the first price date leaves no lookback history, so no rebalance could ever execute."""
        first = fixture_prices().index[0].date().isoformat()
        with pytest.raises(InvalidOptimizationInput, match="No rebalance could execute"):
            self.parse(start=first, end="2023-01-20")

    def test_valid_range_with_history_still_parses(self):
        request = self.parse(start="2023-03-01", end="2023-12-01")
        assert request.config.start.isoformat() == "2023-03-01"

    @pytest.mark.parametrize(
        "fields",
        [
            {"start": "1990-01-01", "end": "2000-01-01"},
            {"start": "2010-01-01"},
            {"start": "2023-03-01", "end": "2030-01-01"},
        ],
    )
    def test_endpoint_rejects_without_charging(self, client, fields):
        before = remaining_uses(client)
        response = client.post(
            "/api/backtest/",
            json={"universe": ["top3"], "lookback_days": 30, **fields},
            headers={"Authorization": DEV_USER_TOKEN},
        )
        assert response.status_code == 400, response.get_json()
        assert remaining_uses(client) == before
