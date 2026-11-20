"""Optimize charges a use only for an answer; anything that ends without one is free.

Policy: a well-formed request that produces a result (including a hold-cash
result) costs one use. Requests that are rejected, fail in the solver or crash
do not. Exhausted or expired tokens are refused before any work is done.
"""

import sqlite3

import cvxpy
import pytest

import wolvwealth.api.backtest as backtest_api
import wolvwealth.api.optimize as optimize_api
from tests.conftest import DEV_USER_TOKEN, install_csrf_token, login
from wolvwealth.optimizer_core import NoAssetBeatsRiskFreeRate

BODY = {"initial_cash": 1000, "universe": ["top3"]}
HEADERS = {"Authorization": DEV_USER_TOKEN}


def uses(app) -> int:
    connection = sqlite3.connect(app.config["DATABASE_FILENAME"])
    try:
        return connection.execute("SELECT uses FROM tokens WHERE token = ?", (DEV_USER_TOKEN,)).fetchone()[0]
    finally:
        connection.close()


def set_token(app, **columns):
    connection = sqlite3.connect(app.config["DATABASE_FILENAME"])
    assignments = ", ".join(f"{name} = ?" for name in columns)
    connection.execute(f"UPDATE tokens SET {assignments} WHERE token = ?", (*columns.values(), DEV_USER_TOKEN))
    connection.commit()
    connection.close()


def must_not_run(*args, **kwargs):
    raise AssertionError("the optimizer ran for a request that should have been refused first")


class TestOnlyAnswersAreCharged:
    def test_an_answer_costs_one_use(self, app, client):
        before = uses(app)
        assert client.post("/api/optimize/", json=BODY, headers=HEADERS).status_code == 200
        assert uses(app) == before - 1

    def test_a_hold_cash_answer_costs_one_use(self, app, client, monkeypatch):
        def hold_cash(*args, **kwargs):
            raise NoAssetBeatsRiskFreeRate(0.02, "AAPL", -0.1)

        monkeypatch.setattr(optimize_api, "compute_portfolio", hold_cash)
        before = uses(app)
        assert client.post("/api/optimize/", json=BODY, headers=HEADERS).status_code == 200
        assert uses(app) == before - 1

    def test_a_solver_failure_is_free(self, app, client, monkeypatch):
        def solver_failure(*args, **kwargs):
            raise cvxpy.error.SolverError("did not converge")

        monkeypatch.setattr(optimize_api, "compute_portfolio", solver_failure)
        before = uses(app)
        response = client.post("/api/optimize/", json=BODY, headers=HEADERS)
        assert response.status_code == 400
        assert uses(app) == before

    def test_a_crash_is_free(self, app, client, monkeypatch):
        def crash(*args, **kwargs):
            raise RuntimeError("bug")

        monkeypatch.setattr(optimize_api, "compute_portfolio", crash)
        before = uses(app)
        with pytest.raises(RuntimeError):  # TESTING propagates exceptions; in production this is a 500
            client.post("/api/optimize/", json=BODY, headers=HEADERS)
        assert uses(app) == before

    def test_session_callers_follow_the_same_policy(self, app, client, monkeypatch):
        def solver_failure(*args, **kwargs):
            raise cvxpy.error.SolverError("did not converge")

        login(client)
        token = install_csrf_token(client)
        before = uses(app)
        assert client.post("/api/optimize/", json=BODY, headers={"X-CSRF-Token": token}).status_code == 200
        assert uses(app) == before - 1
        monkeypatch.setattr(optimize_api, "compute_portfolio", solver_failure)
        assert client.post("/api/optimize/", json=BODY, headers={"X-CSRF-Token": token}).status_code == 400
        assert uses(app) == before - 1


class TestDeadTokensAreRefusedBeforeAnyWork:
    @pytest.mark.parametrize(
        ("columns", "message"),
        [({"uses": 0}, "run out of uses"), ({"expires": "2000-01-01 00:00:00"}, "expired")],
    )
    def test_optimize(self, app, client, monkeypatch, columns, message):
        monkeypatch.setattr(optimize_api, "compute_portfolio", must_not_run)
        set_token(app, **columns)
        response = client.post("/api/optimize/", json=BODY, headers=HEADERS)
        assert response.status_code == 403
        assert message in response.get_json()["error"]["message"]

    def test_optimize_from_a_session(self, app, client, monkeypatch):
        monkeypatch.setattr(optimize_api, "compute_portfolio", must_not_run)
        login(client)
        token = install_csrf_token(client)
        set_token(app, uses=0)
        response = client.post("/api/optimize/", json=BODY, headers={"X-CSRF-Token": token})
        assert response.status_code == 403

    def test_backtest_validation_is_skipped_for_an_exhausted_token(self, app, client, monkeypatch):
        monkeypatch.setattr(backtest_api, "parse_backtest_request", must_not_run)
        set_token(app, uses=0)
        response = client.post("/api/backtest/", json={"start": "2023-07-01"}, headers=HEADERS)
        assert response.status_code == 403
        assert "run out of uses" in response.get_json()["error"]["message"]


class TestLastUseRace:
    def test_if_another_request_spends_the_last_use_meanwhile_no_answer_is_given(self, app, client, monkeypatch):
        """Two requests both pass the up-front check with one use left; only the first to finish is answered."""
        set_token(app, uses=1)
        real = optimize_api.compute_portfolio

        def concurrent_request_spends_it(*args, **kwargs):
            set_token(app, uses=0)  # the other request finished first
            return real(*args, **kwargs)

        monkeypatch.setattr(optimize_api, "compute_portfolio", concurrent_request_spends_it)
        response = client.post("/api/optimize/", json=BODY, headers=HEADERS)
        assert response.status_code == 403
        assert "optimized_portfolio" not in response.get_json()
        assert uses(app) == 0  # never negative
