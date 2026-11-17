"""Tests for POST /api/backtest/ and the request parsing / caching behind it."""

import threading

import pandas as pd
import pytest

import wolvwealth.api.backtest as backtest_api
from tests.conftest import DEV_ADMIN_TOKEN, DEV_USER, DEV_USER_TOKEN, FIXTURES_DIR, install_csrf_token, login
from wolvwealth.backtest import BacktestConfig, Frequency
from wolvwealth.backtest.cache import ReportCache
from wolvwealth.backtest.jobs import JobManager
from wolvwealth.backtest.request import BacktestLimits, count_rebalances, parse_backtest_request
from wolvwealth.optimizer_params import InvalidOptimizationInput

PAYLOAD = {"start": "2023-07-01", "universe": ["top3"], "lookback_days": 120, "frequency": "monthly"}


@pytest.fixture(autouse=True)
def fresh_backtest_state(monkeypatch):
    """Isolate tests: empty cache and a fresh job manager each time."""
    backtest_api.report_cache.clear()
    manager = JobManager(max_workers=2)
    monkeypatch.setattr(backtest_api, "job_manager", manager)
    yield
    manager.shutdown(wait=True)
    backtest_api.report_cache.clear()


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


def api_key_get(client, url):
    return client.get(url, headers={"Authorization": DEV_USER_TOKEN})


def finish(client, start_response, get=api_key_get):
    """Wait for the job behind a POST /api/backtest/ response and return the final poll."""
    data = start_response.get_json()
    assert start_response.status_code in (200, 202), data
    assert backtest_api.job_manager.wait(data["job_id"], timeout=120)
    final = get(client, data["status_url"])
    assert final.status_code == 200, final.get_json()
    return final.get_json()


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

    def test_start_returns_job_then_poll_returns_report(self, client):
        response = api_key_post(client, PAYLOAD)
        assert response.status_code == 202
        started = response.get_json()
        assert started["status"] in ("queued", "running")
        assert started["status_url"] == f"/api/backtest/{started['job_id']}/"
        assert response.headers["Location"] == started["status_url"]
        assert "report" not in started

        data = finish(client, response)
        assert data["status"] == "succeeded"
        assert data["cached"] is False
        assert data["progress"]["fraction"] == 1.0
        assert data["estimated_seconds_remaining"] is None
        report = data["report"]
        assert report["config"]["universe"] == fixture_universe()[:3]
        assert report["metrics"]["rebalances"] == len(report["result"]["rebalances"])
        assert "equal_weight" in report["benchmarks"]
        assert len(report["result"]["equity"]) == report["metrics"]["trading_days"]
        assert any("Survivorship" in warning for warning in report["warnings"])

    def test_cache_hit_finishes_immediately(self, client):
        finish(client, api_key_post(client, PAYLOAD))
        second = api_key_post(client, PAYLOAD)
        assert second.status_code == 200
        data = second.get_json()
        assert data["status"] == "succeeded"
        assert data["cached"] is True
        assert "report" in data

    def test_charges_one_use_per_request_including_cache_hits(self, client):
        before = remaining_uses(client)
        finish(client, api_key_post(client, PAYLOAD))
        api_key_post(client, PAYLOAD)
        assert remaining_uses(client) == before - 2

    def test_polling_is_free(self, client):
        response = api_key_post(client, PAYLOAD)
        finish(client, response)
        before = remaining_uses(client)
        for _ in range(3):
            api_key_get(client, response.get_json()["status_url"])
        assert remaining_uses(client) == before

    def test_invalid_payload_is_free(self, client):
        before = remaining_uses(client)
        response = api_key_post(client, {"start": "2023-07-01", "frequency": "hourly"})
        assert response.status_code == 400
        assert "frequency" in response.get_json()["error"]["message"]
        assert remaining_uses(client) == before

    def test_non_json_body_rejected(self, client):
        response = client.post(
            "/api/backtest/", data="nope", content_type="text/plain", headers={"Authorization": DEV_USER_TOKEN}
        )
        assert response.status_code == 400

    @pytest.mark.parametrize("headers", [{}, {"Authorization": "bogus"}])
    def test_unauthenticated_requests_do_no_validation_work(self, client, monkeypatch, headers):
        """Regression: an anonymous POST used to run the full (seconds-long) pre-run check before auth."""

        def must_not_run(*args, **kwargs):
            raise AssertionError("parse_backtest_request ran for an unauthenticated request")

        monkeypatch.setattr(backtest_api, "parse_backtest_request", must_not_run)
        heavy = {"start": "2023-01-02", "frequency": "daily", "universe": ["top5"]}
        response = client.post("/api/backtest/", json=heavy, headers=headers)
        assert response.status_code == (401 if not headers else 403)

    def test_include_equity_false(self, client):
        data = finish(client, api_key_post(client, {**PAYLOAD, "include_equity": False}))
        assert "equity" not in data["report"]["result"]

    def test_rebalance_limit_enforced(self, app, client):
        app.config["BACKTEST_MAX_REBALANCES"] = 2
        try:
            response = api_key_post(client, PAYLOAD)
        finally:
            app.config["BACKTEST_MAX_REBALANCES"] = 120
        assert response.status_code == 400
        assert "Too many rebalances" in response.get_json()["error"]["message"]

    def test_unknown_job_is_404(self, client):
        assert api_key_get(client, "/api/backtest/nope/").status_code == 404

    def test_other_users_cannot_see_a_job(self, client):
        response = api_key_post(client, PAYLOAD)
        finish(client, response)
        other = client.get(response.get_json()["status_url"], headers={"Authorization": DEV_ADMIN_TOKEN})
        assert other.status_code == 404

    def test_per_user_job_limit_is_429_and_free(self, client, monkeypatch):
        blocker = threading.Event()

        def slow_report(*args, **kwargs):
            blocker.wait(30)
            raise ValueError("stopped by test")

        monkeypatch.setattr(backtest_api, "run_report", slow_report)
        backtest_api.job_manager.max_active_per_owner = 1
        try:
            first = api_key_post(client, PAYLOAD)
            assert first.status_code == 202
            before = remaining_uses(client)
            second = api_key_post(client, {**PAYLOAD, "lookback_days": 121})
            assert second.status_code == 429
            assert remaining_uses(client) == before
        finally:
            blocker.set()
            backtest_api.job_manager.wait(first.get_json()["job_id"], timeout=30)

    def test_full_queue_is_refused_before_validation(self, client, monkeypatch):
        blocker = threading.Event()

        def slow_report(*args, **kwargs):
            blocker.wait(30)
            raise ValueError("stopped by test")

        monkeypatch.setattr(backtest_api, "run_report", slow_report)
        backtest_api.job_manager.max_active_per_owner = 1
        try:
            first = api_key_post(client, PAYLOAD)
            assert first.status_code == 202

            def must_not_run(*args, **kwargs):
                raise AssertionError("validation ran although the owner's queue was full")

            monkeypatch.setattr(backtest_api, "parse_backtest_request", must_not_run)
            assert api_key_post(client, {**PAYLOAD, "lookback_days": 121}).status_code == 429
        finally:
            blocker.set()
            backtest_api.job_manager.wait(first.get_json()["job_id"], timeout=30)

    def test_failed_job_reports_error(self, client, monkeypatch):
        def explode(*args, **kwargs):
            raise ValueError("Fewer than two trading days")

        monkeypatch.setattr(backtest_api, "run_report", explode)
        data = finish(client, api_key_post(client, PAYLOAD))
        assert data["status"] == "failed"
        assert "Fewer than two trading days" in data["error"]
        assert "report" not in data

    def test_session_login_with_csrf_header(self, client):
        login(client)
        token = install_csrf_token(client)
        before = remaining_uses(client)
        response = client.post("/api/backtest/", json=PAYLOAD, headers={"X-CSRF-Token": token})
        data = finish(client, response, get=lambda c, url: c.get(url))  # polling needs no CSRF header
        assert data["status"] == "succeeded"
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
