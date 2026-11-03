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
    assert set(body) == {"as_of", "risk_free_rate", "optimized_portfolio", "cash", "trades", "metrics"}
    for position in body["optimized_portfolio"].values():
        assert set(position) == {"shares", "value", "percent_weight"}
    for trade in body["trades"]:
        assert set(trade) == {"ticker", "action", "shares", "price", "value"}
    assert body["metrics"]["portfolio_value"] == 10000
    # No reserve asked for: the cash line says so explicitly rather than vanishing.
    assert body["cash"] == {"value": 0, "percent_weight": 0}


def test_optimize_without_a_rate_assumes_zero_and_says_so(client):
    body = client.post("/api/optimize/", json={"initial_cash": 10000, "universe": ["top20"]}).get_json()
    assert body["risk_free_rate"]["value"] == 0.0
    assert body["risk_free_rate"]["source"] == "default"
    assert "0% was assumed" in body["risk_free_rate"]["note"]
    # Still present when metrics are excluded: it is an echoed input, not a metric.
    body = client.post(
        "/api/optimize/", json={"initial_cash": 10000, "universe": ["top20"], "exclude_metrics": True}
    ).get_json()
    assert body["risk_free_rate"]["source"] == "default"


def test_optimize_request_rate_is_used_and_echoed_without_a_note(client):
    body = client.post(
        "/api/optimize/", json={"initial_cash": 10000, "universe": ["top20"], "risk_free_rate": 0.01}
    ).get_json()
    assert body["risk_free_rate"] == {"value": 0.01, "source": "request"}
    response = client.post("/api/optimize/", json={"initial_cash": 10000, "risk_free_rate": 4})
    assert response.status_code == 400
    assert "0.04 means 4%" in response.get_json()["error"]["message"]


def test_optimize_tbill_shortcut(client, services):
    response = client.post("/api/optimize/", json={"initial_cash": 10000, "risk_free_rate": "tbill"})
    assert response.status_code == 400
    assert "not been fetched" in response.get_json()["error"]["message"]
    services.market.set_tbill(0.0412, "2026-10-30")
    body = client.post(
        "/api/optimize/", json={"initial_cash": 10000, "universe": ["top20"], "risk_free_rate": "tbill"}
    ).get_json()
    assert body["risk_free_rate"] == {"value": 0.0412, "source": "tbill"}
    market = client.get("/api/market/").get_json()
    assert market["tbill_rate"] == 0.0412 and market["tbill_as_of"] == "2026-10-30"


def test_market_endpoint_reports_unknown_tbill_as_null(client):
    market = client.get("/api/market/").get_json()
    assert market["tbill_rate"] is None and market["tbill_as_of"] is None


def test_optimize_stored_portfolio_uses_its_own_rate(workspace_client, repo):
    pid = repo.get_portfolio("tester", name="main").id
    repo.set_risk_free_rate("tester", pid, 0.035)
    body = workspace_client.post("/api/optimize/", json={"portfolio": "main", "universe": ["top20"]}).get_json()
    assert body["risk_free_rate"] == {"value": 0.035, "source": "portfolio"}
    # An explicit rate in the request still wins over the stored one.
    body = workspace_client.post(
        "/api/optimize/", json={"portfolio": "main", "universe": ["top20"], "risk_free_rate": 0.0}
    ).get_json()
    assert body["risk_free_rate"] == {"value": 0.0, "source": "request"}
    listed = workspace_client.get("/api/workspaces/").get_json()["workspaces"]
    main = next(p for w in listed if w["name"] == "tester" for p in w["portfolios"] if p["name"] == "main")
    assert main["risk_free_rate"] == 0.035


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
