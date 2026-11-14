"""Tests for POST /api/backtest/ and the request parsing / caching behind it."""

import pandas as pd
import pytest

from tests.conftest import DEV_USER, DEV_USER_TOKEN, FIXTURES_DIR, install_csrf_token, login
from wolvwealth.api.backtest import report_cache
from wolvwealth.backtest import BacktestConfig, Frequency
from wolvwealth.backtest.cache import ReportCache
from wolvwealth.backtest.request import BacktestLimits, count_rebalances, parse_backtest_request
from wolvwealth.optimizer_params import InvalidOptimizationInput

PAYLOAD = {"start": "2023-07-01", "universe": ["top3"], "lookback_days": 120, "frequency": "monthly"}


@pytest.fixture(autouse=True)
def clear_cache():
    report_cache.clear()
    yield
    report_cache.clear()


def fixture_prices() -> pd.DataFrame:
    return pd.read_csv(FIXTURES_DIR / "historical_prices.csv", parse_dates=True, index_col="Date")


def fixture_universe() -> list[str]:
    return [line.split(",")[0] for line in (FIXTURES_DIR / "ticker_universe.csv").read_text().split()]


def parse(payload, **limits):
    return parse_backtest_request(
        payload, ticker_universe=fixture_universe(), prices=fixture_prices(), limits=BacktestLimits(**limits)
    )


class TestParseBacktestRequest:
    def test_defaults(self):
        request = parse({"start": "2023-07-01", "universe": ["AAPL", "MSFT"]})
        assert request.config.frequency == Frequency.MONTHLY
        assert request.config.end == fixture_prices().index[-1].date()
        assert request.config.lookback_days == 3 * 365
        assert request.config.execution_lag == 1
        assert request.benchmark_tickers == ()  # fixture has no SPY
        assert request.include_equity is True

    def test_requires_start(self):
        with pytest.raises(InvalidOptimizationInput, match="start"):
            parse({})

    def test_rejects_unknown_fields(self):
        with pytest.raises(InvalidOptimizationInput, match="Unknown field"):
            parse({"start": "2023-07-01", "cash": 5})

    def test_rejects_bad_frequency(self):
        with pytest.raises(InvalidOptimizationInput, match="frequency"):
            parse({"start": "2023-07-01", "frequency": "hourly"})

    def test_rejects_bool_as_number(self):
        with pytest.raises(InvalidOptimizationInput, match="initial_cash"):
            parse({"start": "2023-07-01", "initial_cash": True})

    def test_rejects_start_after_data(self):
        with pytest.raises(InvalidOptimizationInput, match="after the last"):
            parse({"start": "2030-01-01"})

    def test_rebalance_limit(self):
        with pytest.raises(InvalidOptimizationInput, match="Too many rebalances"):
            parse({"start": "2023-01-02", "frequency": "daily"}, max_rebalances=50)

    def test_universe_limit(self):
        with pytest.raises(InvalidOptimizationInput, match="Universe too large"):
            parse({"start": "2023-07-01", "universe": ["top5"]}, max_universe_size=3)

    def test_unknown_benchmark_rejected(self):
        with pytest.raises(InvalidOptimizationInput, match="SPY"):
            parse({"start": "2023-07-01", "benchmarks": ["SPY"]})

    def test_benchmark_from_universe(self):
        request = parse({"start": "2023-07-01", "benchmarks": ["nvda"]})
        assert request.benchmark_tickers == ("NVDA",)

    def test_constraints_are_validated(self):
        with pytest.raises(InvalidOptimizationInput, match="max_positions"):
            parse({"start": "2023-07-01", "constraints": {"max_positions": 2, "max_weight": 0.2}})

    def test_count_rebalances_matches_schedule(self):
        prices = fixture_prices()
        config = BacktestConfig(
            start=pd.Timestamp("2023-01-02").date(),
            end=pd.Timestamp("2023-12-31").date(),
            universe=("AAPL",),
            frequency=Frequency.MONTHLY,
        )
        assert count_rebalances(prices, config) == 12


class TestReportCache:
    def test_hit_and_eviction(self):
        cache = ReportCache(maxsize=1)
        prices = fixture_prices()
        config_a = BacktestConfig(start=pd.Timestamp("2023-07-01").date(), end=prices.index[-1].date(), universe=("A",))
        config_b = BacktestConfig(start=pd.Timestamp("2023-07-01").date(), end=prices.index[-1].date(), universe=("B",))
        calls = []
        report, cached = cache.get_or_compute(prices, config_a, (), lambda: calls.append("a") or "report-a")
        assert (report, cached) == ("report-a", False)
        report, cached = cache.get_or_compute(prices, config_a, (), lambda: calls.append("a") or "report-a")
        assert (report, cached) == ("report-a", True)
        cache.get_or_compute(prices, config_b, (), lambda: "report-b")
        _, cached = cache.get_or_compute(prices, config_a, (), lambda: calls.append("a") or "report-a")
        assert cached is False  # evicted by config_b
        assert calls == ["a", "a"]


def api_key_post(client, payload):
    return client.post("/api/backtest/", json=payload, headers={"Authorization": DEV_USER_TOKEN})


def remaining_uses(client) -> int:
    response = client.post("/api/account/", json={"username": DEV_USER}, headers={"Authorization": DEV_USER_TOKEN})
    return response.get_json()["optimizations_remaining"]


class TestBacktestEndpoint:
    def test_listed_in_api_index(self, client):
        assert "/api/backtest/" in client.get("/api/").get_json()

    def test_requires_auth(self, client):
        assert client.post("/api/backtest/", json=PAYLOAD).status_code == 401

    def test_rejects_invalid_api_key(self, client):
        response = client.post("/api/backtest/", json=PAYLOAD, headers={"Authorization": "bogus"})
        assert response.status_code == 403

    def test_happy_path_with_api_key(self, client):
        response = api_key_post(client, PAYLOAD)
        assert response.status_code == 200, response.get_json()
        data = response.get_json()
        assert data["cached"] is False
        assert data["config"]["universe"] == fixture_universe()[:3]
        assert data["metrics"]["rebalances"] == len(data["result"]["rebalances"])
        assert "equal_weight" in data["benchmarks"]
        assert len(data["result"]["equity"]) == data["metrics"]["trading_days"]
        assert any("Survivorship" in warning for warning in data["warnings"])

    def test_charges_one_use_per_request_including_cache_hits(self, client):
        before = remaining_uses(client)
        assert api_key_post(client, PAYLOAD).status_code == 200
        second = api_key_post(client, PAYLOAD)
        assert second.get_json()["cached"] is True
        assert remaining_uses(client) == before - 2

    def test_invalid_payload_is_free(self, client):
        before = remaining_uses(client)
        response = api_key_post(client, {"start": "2023-07-01", "frequency": "hourly"})
        assert response.status_code == 400
        assert "frequency" in response.get_json()["error"]["message"]
        assert remaining_uses(client) == before

    def test_non_json_body_rejected(self, client):
        response = client.post("/api/backtest/", data="nope", content_type="text/plain")
        assert response.status_code == 400

    def test_include_equity_false(self, client):
        response = api_key_post(client, {**PAYLOAD, "include_equity": False})
        assert response.status_code == 200
        assert "equity" not in response.get_json()["result"]

    def test_rebalance_limit_enforced(self, app, client):
        app.config["BACKTEST_MAX_REBALANCES"] = 2
        try:
            response = api_key_post(client, PAYLOAD)
        finally:
            app.config["BACKTEST_MAX_REBALANCES"] = 120
        assert response.status_code == 400
        assert "Too many rebalances" in response.get_json()["error"]["message"]

    def test_session_login_with_csrf_header(self, client):
        login(client)
        token = install_csrf_token(client)
        before = remaining_uses(client)
        response = client.post("/api/backtest/", json=PAYLOAD, headers={"X-CSRF-Token": token})
        assert response.status_code == 200, response.get_json()
        assert remaining_uses(client) == before - 1

    def test_session_login_without_csrf_header_rejected(self, client):
        login(client)
        install_csrf_token(client)
        response = client.post("/api/backtest/", json=PAYLOAD)
        assert response.status_code == 403
        assert "CSRF" in response.get_json()["error"]["message"]

    def test_session_login_with_wrong_csrf_rejected(self, client):
        login(client)
        install_csrf_token(client)
        response = client.post("/api/backtest/", json=PAYLOAD, headers={"X-CSRF-Token": "wrong"})
        assert response.status_code == 403

    def test_exhausted_session_token_rejected(self, app, client):
        import sqlite3

        login(client)
        token = install_csrf_token(client)
        connection = sqlite3.connect(app.config["DATABASE_FILENAME"])
        connection.execute("UPDATE tokens SET uses = 0 WHERE token = ?", (DEV_USER_TOKEN,))
        connection.commit()
        connection.close()
        response = client.post("/api/backtest/", json=PAYLOAD, headers={"X-CSRF-Token": token})
        assert response.status_code == 403
        assert "run out of uses" in response.get_json()["error"]["message"]
