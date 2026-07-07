"""Tests for the JSON API: auth, admin authorization, and /api/optimize/."""

import pytest

from tests.conftest import DEV_ADMIN_TOKEN, DEV_USER, DEV_USER_TOKEN

ADMIN_ENDPOINTS = [
    "/api/admin/add/",
    "/api/admin/remove/",
    "/api/admin/user-info/",
    "/api/admin/update-user/",
    "/api/admin/delete-user/",
    "/api/db/dump/",
    "/api/db/status/",
]


def test_api_index_anonymous(client):
    response = client.get("/api/")
    assert response.status_code == 200
    data = response.get_json()
    assert "/api/optimize/" in data
    assert "/api/db/dump/" not in data


def test_api_index_lists_admin_routes_for_admin(client):
    response = client.get("/api/", headers={"Authorization": DEV_ADMIN_TOKEN})
    data = response.get_json()
    assert "/api/db/dump/" in data
    # Every advertised route must actually be routable (no 404s)
    for route in data:
        assert any(rule.rule == route for rule in client.application.url_map.iter_rules()), route


@pytest.mark.parametrize("endpoint", ADMIN_ENDPOINTS)
def test_admin_endpoints_reject_anonymous(client, endpoint):
    response = client.post(endpoint, json={"username": DEV_USER})
    assert response.status_code == 403


@pytest.mark.parametrize("endpoint", ADMIN_ENDPOINTS)
def test_admin_endpoints_reject_non_admin_token(client, endpoint):
    response = client.post(endpoint, json={"username": DEV_USER}, headers={"Authorization": DEV_USER_TOKEN})
    assert response.status_code == 403


def test_db_dump_get_not_allowed(client):
    assert client.get("/api/db/dump/", headers={"Authorization": DEV_ADMIN_TOKEN}).status_code == 405


def test_db_dump_excludes_secrets(client):
    response = client.post("/api/db/dump/", headers={"Authorization": DEV_ADMIN_TOKEN})
    assert response.status_code == 200
    data = response.get_json()
    assert data["admins"] == ["devadmin"]
    for user in data["users"]:
        assert "password" not in user
    for token in data["tokens"]:
        assert "token" not in token


def test_admin_user_info(client):
    response = client.post(
        "/api/admin/user-info/", json={"username": DEV_USER}, headers={"Authorization": DEV_ADMIN_TOKEN}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["username"] == DEV_USER
    assert data["admin"] is False
    assert data["api_key"] == DEV_USER_TOKEN


def test_admin_user_info_unknown_user_404(client):
    response = client.post(
        "/api/admin/user-info/", json={"username": "ghost"}, headers={"Authorization": DEV_ADMIN_TOKEN}
    )
    assert response.status_code == 404


def test_admin_add_and_remove(client):
    headers = {"Authorization": DEV_ADMIN_TOKEN}
    assert client.post("/api/admin/add/", json={"username": DEV_USER}, headers=headers).status_code == 200
    info = client.post("/api/admin/user-info/", json={"username": DEV_USER}, headers=headers).get_json()
    assert info["admin"] is True
    assert client.post("/api/admin/remove/", json={"username": DEV_USER}, headers=headers).status_code == 200


def test_account_info(client):
    response = client.post("/api/account/", json={"username": DEV_USER}, headers={"Authorization": DEV_USER_TOKEN})
    assert response.status_code == 200
    data = response.get_json()
    assert data["username"] == DEV_USER
    assert data["api_key"] == DEV_USER_TOKEN


def test_account_info_wrong_key(client):
    response = client.post("/api/account/", json={"username": DEV_USER}, headers={"Authorization": "bogus"})
    assert response.status_code == 403


def test_account_info_non_ascii_key_rejected_not_500(client):
    """A non-ASCII Authorization header must 403, not crash compare_digest."""
    response = client.post("/api/account/", json={"username": DEV_USER}, headers={"Authorization": "café"})
    assert response.status_code == 403


def test_oversized_request_body_rejected(client):
    """Bodies over MAX_CONTENT_LENGTH (16 MiB) are rejected, not parsed."""
    assert client.application.config["MAX_CONTENT_LENGTH"] == 16 * 1024 * 1024
    huge = b'{"initial_cash": 1000, "pad": "' + b"x" * (17 * 1024 * 1024) + b'"}'
    response = client.post(
        "/api/optimize/", data=huge, content_type="application/json", headers={"Authorization": DEV_USER_TOKEN}
    )
    # The optimize route wraps body-parse failures as 400; Werkzeug's own
    # RequestEntityTooLarge is 413. Either way the body must be refused.
    assert response.status_code in (400, 413)


def test_optimize_requires_api_key(client):
    assert client.post("/api/optimize/", json={}).status_code == 401


def test_optimize_rejects_invalid_api_key(client):
    response = client.post("/api/optimize/", json={}, headers={"Authorization": "bogus"})
    assert response.status_code == 403


def optimize(client, payload):
    return client.post("/api/optimize/", json=payload, headers={"Authorization": DEV_USER_TOKEN})


def test_optimize_happy_path(client):
    response = optimize(client, {"initial_cash": 10000, "universe": ["top5"]})
    assert response.status_code == 200
    data = response.get_json()
    assert data["metrics"]["portfolio_value"] == 10000
    weights = [position["percent_weight"] for position in data["optimized_portfolio"].values()]
    assert weights
    assert sum(weights) == pytest.approx(100, abs=1)


def test_optimize_with_holdings_and_exclude_metrics(client):
    response = optimize(client, {"initial_holdings": {"aapl": 10}, "exclude_metrics": True})
    assert response.status_code == 200
    data = response.get_json()
    assert "metrics" not in data
    assert data["optimized_portfolio"]


def test_optimize_decrements_uses(client):
    before = client.post(
        "/api/account/", json={"username": DEV_USER}, headers={"Authorization": DEV_USER_TOKEN}
    ).get_json()["optimizations_remaining"]
    optimize(client, {"initial_cash": 1000})
    after = client.post(
        "/api/account/", json={"username": DEV_USER}, headers={"Authorization": DEV_USER_TOKEN}
    ).get_json()["optimizations_remaining"]
    assert after == before - 1


def test_optimize_zero_investment_rejected(client):
    response = optimize(client, {})
    assert response.status_code == 400
    assert "Total investment" in response.get_json()["error"]["message"]


def test_optimize_bogus_symbol_rejected(client):
    response = optimize(client, {"initial_cash": 1000, "universe": ["top2", "BOGUS"]})
    assert response.status_code == 400
    assert "BOGUS" in response.get_json()["error"]["message"]


def test_optimize_universe_must_be_list(client):
    response = optimize(client, {"initial_cash": 1000, "universe": "AAPL"})
    assert response.status_code == 400


def test_optimize_infeasible_min_weight_rejected(client):
    payload = {"initial_cash": 1000, "constraints": {"min_universal_weight": 0.5}}
    response = optimize(client, payload)
    assert response.status_code == 400
    assert "min_universal_weight" in response.get_json()["error"]["message"]


def test_optimize_expired_token_rejected(app, client):
    import sqlite3

    connection = sqlite3.connect(app.config["DATABASE_FILENAME"])
    connection.execute("UPDATE tokens SET expires = '2000-01-01 00:00:00' WHERE token = ?", (DEV_USER_TOKEN,))
    connection.commit()
    connection.close()
    response = optimize(client, {"initial_cash": 1000})
    assert response.status_code == 403
