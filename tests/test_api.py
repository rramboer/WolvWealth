"""Contract tests for the JSON API."""


def test_api_index_lists_endpoints(client):
    body = client.get("/api/").get_json()
    assert "/api/optimize/" in body


def test_workspaces_endpoint_lists_seed(client):
    body = client.get("/api/workspaces/").get_json()
    names = [w["name"] for w in body["workspaces"]]
    assert "demo" in names


def test_optimize_explicit_body(client):
    response = client.post(
        "/api/optimize/",
        json={"initial_cash": 10000, "universe": ["top20"], "constraints": {"max_positions": 2}},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert set(body) == {"optimized_portfolio", "metrics"}
    for position in body["optimized_portfolio"].values():
        assert set(position) == {"shares", "value", "percent_weight"}
    assert body["metrics"]["portfolio_value"] == 10000


def test_optimize_rejects_invalid_json(client):
    response = client.post("/api/optimize/", data="{not json", content_type="application/json")
    assert response.status_code == 400
    assert "Parse Error" in response.get_json()["error"]["message"]


def test_optimize_validation_error_is_400(client):
    response = client.post("/api/optimize/", json={"initial_cash": -1})
    assert response.status_code == 400


def test_optimize_stored_portfolio_by_name(workspace_client, market):
    response = workspace_client.post(
        "/api/optimize/", json={"workspace": "tester", "portfolio": "main", "universe": ["top20"]}
    )
    assert response.status_code == 200
    expected_value = 5000 + 10 * market.price("AAA")
    assert response.get_json()["metrics"]["portfolio_value"] == round(expected_value, 2)


def test_optimize_stored_portfolio_session_fallback(workspace_client, market):
    # workspace omitted: falls back to the workspace open in the session cookie
    response = workspace_client.post("/api/optimize/", json={"portfolio": "main", "universe": ["top20"]})
    assert response.status_code == 200


def test_optimize_explicit_fields_override_stored(workspace_client, market):
    response = workspace_client.post(
        "/api/optimize/",
        json={"workspace": "tester", "portfolio": "main", "initial_cash": 777, "initial_holdings": {}},
    )
    assert response.status_code == 200
    assert response.get_json()["metrics"]["portfolio_value"] == 777


def test_optimize_unknown_portfolio_is_404(workspace_client, market):
    response = workspace_client.post("/api/optimize/", json={"workspace": "tester", "portfolio": "nope"})
    assert response.status_code == 404


def test_optimize_portfolio_without_workspace_is_400(client, market):
    response = client.post("/api/optimize/", json={"portfolio": "main"})
    assert response.status_code == 400
    assert "workspace" in response.get_json()["error"]["message"].lower()


def test_optimize_unknown_stored_portfolio_is_404(workspace_client):
    response = workspace_client.post("/api/optimize/", json={"workspace": "tester", "portfolio": "nope"})
    assert response.status_code == 404
    assert response.get_json()["error"]["status_code"] == 404
