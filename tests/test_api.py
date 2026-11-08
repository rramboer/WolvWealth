"""Contract tests for the JSON API."""

import pytest

from conftest import dated_to_today


def test_api_index_lists_every_endpoint_and_the_contract(client):
    body = client.get("/api/").get_json()
    assert {"/api/optimize/", "/api/market/", "/api/holdings/import/"} <= set(body)
    assert "API.md" in body["contract"]


def test_workspaces_endpoint_lists_seed(client):
    body = client.get("/api/workspaces/").get_json()
    names = [w["name"] for w in body["workspaces"]]
    assert "demo" in names


def test_market_endpoint_reports_freshness_tickers_and_the_tbill(client, services, market):
    body = client.get("/api/market/").get_json()
    assert body["as_of"] == str(market.as_of)
    assert body["tradeable_size"] == len(market.tradeable) and body["tickers"] == list(market.tradeable)
    assert "top20" in body["universe_aliases"]
    assert body["tbill_rate"] is None and body["tbill_as_of"] is None, "unknown until a refresh fetches it"
    assert body["stale"] is True and "refresh-data" in body["refresh_hint"], "the synthetic market is years old"
    assert "quarantined" not in body
    services.price_store.save_quarantine({"DDD": {"reason": "moved 40.00x in one day", "detected": "2026-11-06"}})
    assert client.get("/api/market/").get_json()["quarantined"]["DDD"]["reason"] == "moved 40.00x in one day"
    services.price_store.save_quarantine({})
    assert "readjusted" not in body
    note = {"detected": "2026-11-06", "outcome": "history replaced on the new adjustment basis"}
    services.price_store.save_readjustments({"BBB": note})
    assert client.get("/api/market/").get_json()["readjusted"]["BBB"] == note
    services.price_store.save_readjustments({})
    services.market.set_data(dated_to_today(market))
    services.market.set_tbill(0.0412, "2026-10-30")
    body = client.get("/api/market/").get_json()
    assert body["stale"] is False and "refresh_hint" not in body
    assert body["tbill_rate"] == 0.0412 and body["tbill_as_of"] == "2026-10-30"


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


@pytest.mark.parametrize(
    "raw, fragment",
    [
        ("{not json", "Parse Error"),
        ('{"initial_cash": NaN}', "initial_cash must be a number"),  # Python's json accepts these three
        ('{"initial_cash": Infinity}', "initial_cash must be a number"),
        ('{"initial_cash": 1e400}', "initial_cash must be a number"),
    ],
)
def test_optimize_rejects_unparseable_or_non_finite_json(client, raw, fragment):
    response = client.post("/api/optimize/", data=raw, content_type="application/json")
    assert response.status_code == 400
    assert fragment in response.get_json()["error"]["message"]


def test_api_failures_keep_the_json_error_shape_even_for_a_bug(app, monkeypatch):
    """API.md promises no HTML error page: wrong methods, unknown routes and crashes all answer JSON."""
    app.config["PROPAGATE_EXCEPTIONS"] = False
    client = app.test_client()
    assert client.get("/api/optimize/").get_json()["error"]["status_code"] == 405
    assert client.get("/api/nope/").get_json()["error"]["status_code"] == 404

    def bug(*_args, **_kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("wolvwealth.services.market_data.status", bug)
    response = client.get("/api/market/")
    assert response.status_code == 500
    assert response.get_json()["error"] == {"message": "Internal error: RuntimeError: kaboom", "status_code": 500}


def test_optimize_stored_portfolio_by_name_or_from_the_session(workspace_client, market):
    expected_value = round(5000 + 10 * market.price("AAA"), 2)
    explicit = workspace_client.post(
        "/api/optimize/", json={"workspace": "tester", "portfolio": "main", "universe": ["top20"]}
    )
    assert explicit.status_code == 200
    assert explicit.get_json()["metrics"]["portfolio_value"] == expected_value
    # workspace omitted: falls back to the workspace open in the session cookie
    fallback = workspace_client.post("/api/optimize/", json={"portfolio": "main", "universe": ["top20"]})
    assert fallback.status_code == 200 and fallback.get_json() == explicit.get_json()


def test_optimize_explicit_fields_override_stored(workspace_client, market):
    response = workspace_client.post(
        "/api/optimize/",
        json={"workspace": "tester", "portfolio": "main", "initial_cash": 777, "initial_holdings": {}},
    )
    assert response.status_code == 200
    assert response.get_json()["metrics"]["portfolio_value"] == 777


def test_bad_portfolio_references_are_refused_by_name(app, workspace_client):
    no_session = app.test_client()
    cases = [
        (no_session, {"portfolio": "main"}, 400, "workspace"),
        (workspace_client, {"portfolio": 5}, 400, "portfolio must be a string"),
        (workspace_client, {"workspace": 5, "portfolio": "main"}, 400, "workspace must be a string"),
        (workspace_client, {"workspace": "tester", "portfolio": "nope"}, 404, "Unknown portfolio"),
    ]
    for who, body, status, fragment in cases:
        response = who.post("/api/optimize/", json=body)
        assert response.status_code == status, body
        error = response.get_json()["error"]
        assert error["status_code"] == status and fragment in error["message"], (body, error)
