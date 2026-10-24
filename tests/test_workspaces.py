"""Browser-flow tests for workspaces and portfolios (Flask test client, temp DB)."""
import wolvwealth


def fetch_portfolio(workspace, name):
    """Read a portfolio through the DB helpers inside a request context."""
    with wolvwealth.app.test_request_context():
        return wolvwealth.portfolios.fetch_portfolio(workspace, name=name)


def test_workspace_page_requires_open_workspace(client):
    response = client.get("/workspace/")
    assert response.status_code == 302
    assert "/workspaces/" in response.headers["Location"]


def test_create_workspace_opens_it(client):
    response = client.post("/workspaces/create/", data={"name": "ryan"})
    assert response.status_code == 302
    assert "/workspace/" in response.headers["Location"]
    assert client.get("/workspace/").status_code == 200


def test_create_workspace_rejects_bad_names(client):
    for bad in ("", "a/b", "x" * 65):
        response = client.post("/workspaces/create/", data={"name": bad})
        assert "/workspaces/" in response.headers["Location"]
    body = client.get("/api/workspaces/").get_json()
    assert all(w["name"] == "demo" for w in body["workspaces"])


def test_duplicate_workspace_rejected(client):
    client.post("/workspaces/create/", data={"name": "ryan"})
    client.post("/workspaces/create/", data={"name": "ryan"})
    names = [w["name"] for w in client.get("/api/workspaces/").get_json()["workspaces"]]
    assert names.count("ryan") == 1


def test_enter_and_leave_workspace(client):
    assert client.post("/workspaces/enter/", data={"name": "demo"}).status_code == 302
    assert client.get("/workspace/").status_code == 200
    client.get("/workspaces/leave/")
    assert client.get("/workspace/").status_code == 302


def test_enter_missing_workspace_flashes(client):
    response = client.post("/workspaces/enter/", data={"name": "ghost"})
    assert "/workspaces/" in response.headers["Location"]
    assert client.get("/workspace/").status_code == 302  # nothing was opened


def test_delete_workspace_cascades_and_clears_session(workspace_client):
    workspace_client.post("/workspaces/delete/", data={"name": "tester"})
    assert fetch_portfolio("tester", "main") is None
    assert workspace_client.get("/workspace/").status_code == 302  # session cleared


def test_portfolio_create_and_holdings(workspace_client):
    portfolio = fetch_portfolio("tester", "main")
    assert portfolio["cash"] == 5000
    assert portfolio["holdings"] == {"AAA": 10}


def test_holdings_upsert_and_zero_removes(workspace_client):
    pid = fetch_portfolio("tester", "main")["id"]
    workspace_client.post("/portfolios/holdings/", data={"portfolio_id": pid, "ticker": "AAA", "shares": "4"})
    assert fetch_portfolio("tester", "main")["holdings"] == {"AAA": 4}
    workspace_client.post("/portfolios/holdings/", data={"portfolio_id": pid, "ticker": "AAA", "shares": "0"})
    assert fetch_portfolio("tester", "main")["holdings"] == {}


def test_holdings_reject_unknown_ticker(workspace_client):
    pid = fetch_portfolio("tester", "main")["id"]
    workspace_client.post("/portfolios/holdings/", data={"portfolio_id": pid, "ticker": "FAKE", "shares": "1"})
    assert "FAKE" not in fetch_portfolio("tester", "main")["holdings"]


def test_set_cash(workspace_client):
    pid = fetch_portfolio("tester", "main")["id"]
    workspace_client.post("/portfolios/cash/", data={"portfolio_id": pid, "cash": "123.45"})
    assert fetch_portfolio("tester", "main")["cash"] == 123.45


def test_portfolio_delete_scoped_to_workspace(workspace_client):
    pid = fetch_portfolio("tester", "main")["id"]
    # Another workspace cannot delete tester's portfolio with a guessed id
    workspace_client.get("/workspaces/leave/")
    workspace_client.post("/workspaces/create/", data={"name": "intruder"})
    workspace_client.post("/portfolios/delete/", data={"portfolio_id": pid})
    assert fetch_portfolio("tester", "main") is not None
    # The owner can
    workspace_client.post("/workspaces/enter/", data={"name": "tester"})
    workspace_client.post("/portfolios/delete/", data={"portfolio_id": pid})
    assert fetch_portfolio("tester", "main") is None


def test_browser_optimize_flow(workspace_client):
    pid = fetch_portfolio("tester", "main")["id"]
    response = workspace_client.post(
        "/optimizer/run/", data={"portfolio_id": pid, "universe": "top20", "max_positions": "2"}
    )
    assert response.status_code == 200
    assert b"%" in response.data  # results table rendered


def test_legacy_redirects(client):
    assert "/workspaces/" in client.get("/login/").headers["Location"]
    assert "/workspaces/" in client.get("/register/").headers["Location"]
    assert "/workspace/" in client.get("/account/").headers["Location"]


def test_landing_page_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Choose a workspace" in response.data


def test_old_react_optimizer_page_is_gone(client):
    client.post("/workspaces/enter/", data={"name": "demo"})
    assert client.get("/optimizer/").status_code == 404


def test_holding_without_cached_price_does_not_break_page(client):
    # The demo seed holds AAPL/MSFT, which the synthetic test market does not price.
    client.post("/workspaces/enter/", data={"name": "demo"})
    response = client.get("/workspace/")
    assert response.status_code == 200
    assert b"no cached price" in response.data
