"""Browser-flow tests for workspaces and portfolios (Flask test client, temp DB)."""


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
    client.post("/workspaces/leave/")
    assert client.get("/workspace/").status_code == 302


def test_enter_missing_workspace_flashes(client):
    response = client.post("/workspaces/enter/", data={"name": "ghost"})
    assert "/workspaces/" in response.headers["Location"]
    assert client.get("/workspace/").status_code == 302  # nothing was opened


def test_delete_workspace_cascades_and_clears_session(workspace_client, repo):
    workspace_client.post("/workspaces/delete/", data={"name": "tester"})
    assert repo.get_portfolio("tester", name="main") is None
    assert workspace_client.get("/workspace/").status_code == 302  # session cleared


def test_portfolio_create_and_holdings(workspace_client, repo):
    portfolio = repo.get_portfolio("tester", name="main")
    assert portfolio.cash == 5000
    assert portfolio.holdings == {"AAA": 10}


def test_holdings_upsert_and_zero_removes(workspace_client, repo):
    pid = repo.get_portfolio("tester", name="main").id
    workspace_client.post("/portfolios/holdings/", data={"portfolio_id": pid, "ticker": "AAA", "shares": "4"})
    assert repo.get_portfolio("tester", name="main").holdings == {"AAA": 4}
    workspace_client.post("/portfolios/holdings/", data={"portfolio_id": pid, "ticker": "AAA", "shares": "0"})
    assert repo.get_portfolio("tester", name="main").holdings == {}


def test_holdings_reject_unknown_ticker(workspace_client, repo):
    pid = repo.get_portfolio("tester", name="main").id
    workspace_client.post("/portfolios/holdings/", data={"portfolio_id": pid, "ticker": "FAKE", "shares": "1"})
    assert "FAKE" not in repo.get_portfolio("tester", name="main").holdings


def test_set_cash(workspace_client, repo):
    pid = repo.get_portfolio("tester", name="main").id
    workspace_client.post("/portfolios/cash/", data={"portfolio_id": pid, "cash": "123.45"})
    assert repo.get_portfolio("tester", name="main").cash == 123.45


def test_portfolio_delete_scoped_to_workspace(workspace_client, repo):
    pid = repo.get_portfolio("tester", name="main").id
    workspace_client.post("/workspaces/leave/")
    workspace_client.post("/workspaces/create/", data={"name": "intruder"})
    workspace_client.post("/portfolios/delete/", data={"portfolio_id": pid})
    assert repo.get_portfolio("tester", name="main") is not None
    workspace_client.post("/workspaces/enter/", data={"name": "tester"})
    workspace_client.post("/portfolios/delete/", data={"portfolio_id": pid})
    assert repo.get_portfolio("tester", name="main") is None


def test_browser_optimize_flow(workspace_client, repo):
    pid = repo.get_portfolio("tester", name="main").id
    response = workspace_client.post(
        "/optimizer/run/", data={"portfolio_id": pid, "universe": "top20", "max_positions": "2"}
    )
    assert response.status_code == 200
    assert b"%" in response.data  # results table rendered


def test_browser_results_show_the_trades_to_place(workspace_client, repo):
    """The browser gets the same trade list the API hands an agent."""
    pid = repo.get_portfolio("tester", name="main").id
    response = workspace_client.post(
        "/optimizer/run/", data={"portfolio_id": pid, "universe": "top20", "max_positions": "2"}
    )
    assert b"Trades to get there" in response.data
    assert b"BUY" in response.data or b"SELL" in response.data


def test_browser_max_turnover_keeps_the_holding(workspace_client, repo):
    """The form's max turnover reaches the solver: at 0 the held AAA is kept and only cash is invested."""
    pid = repo.get_portfolio("tester", name="main").id
    response = workspace_client.post(
        "/optimizer/run/", data={"portfolio_id": pid, "universe": "top20", "max_turnover": "0"}
    )
    assert response.status_code == 200
    assert b"Sold to get there" in response.data
    assert b"SELL" not in response.data

    response = workspace_client.post("/optimizer/run/", data={"portfolio_id": pid, "max_turnover": "x"})
    assert response.status_code == 200
    assert b"Max turnover must be a number" in response.data


def test_browser_unset_rate_is_flagged_on_the_card_and_in_the_results(workspace_client, repo):
    pid = repo.get_portfolio("tester", name="main").id
    assert b"not set, 0% assumed" in workspace_client.get("/workspace/").data
    response = workspace_client.post("/optimizer/run/", data={"portfolio_id": pid, "universe": "top20"})
    assert b"vs 0.00% risk-free" in response.data
    assert b"0% was assumed" in response.data


def test_browser_set_rate_on_the_portfolio(workspace_client, repo):
    pid = repo.get_portfolio("tester", name="main").id
    response = workspace_client.post(
        "/portfolios/risk-free-rate/", data={"portfolio_id": pid, "risk_free_rate": "0.035"}
    )
    assert response.status_code == 302
    assert repo.get_portfolio("tester", name="main").risk_free_rate == 0.035
    page = workspace_client.get("/workspace/").data
    assert b"Risk-free rate set to 3.50%" in page and b"not set, 0% assumed" not in page
    response = workspace_client.post("/optimizer/run/", data={"portfolio_id": pid, "universe": "top20"})
    assert b"vs 3.50% risk-free" in response.data
    assert b"0% was assumed" not in response.data
    # Blank clears it.
    workspace_client.post("/portfolios/risk-free-rate/", data={"portfolio_id": pid, "risk_free_rate": ""})
    assert repo.get_portfolio("tester", name="main").risk_free_rate is None
    workspace_client.post("/portfolios/risk-free-rate/", data={"portfolio_id": pid, "risk_free_rate": "x"})
    assert b"Risk-free rate must be a decimal fraction" in workspace_client.get("/workspace/").data
    workspace_client.post("/portfolios/risk-free-rate/", data={"portfolio_id": pid, "risk_free_rate": "4"})
    assert b"0.04 means 4%" in workspace_client.get("/workspace/").data


def test_browser_tbill_button(workspace_client, repo, services):
    pid = repo.get_portfolio("tester", name="main").id
    assert b"Use\n                        T-bill" not in workspace_client.get("/workspace/").data
    assert b'value="tbill"' not in workspace_client.get("/workspace/").data
    services.market.set_tbill(0.0412, "2026-10-30")
    page = workspace_client.get("/workspace/").data
    assert b'value="tbill"' in page and b"(4.12%)" in page
    workspace_client.post("/portfolios/risk-free-rate/", data={"portfolio_id": pid, "source": "tbill"})
    assert repo.get_portfolio("tester", name="main").risk_free_rate == 0.0412


def test_browser_optimize_error_shows_on_the_page_it_came_from(workspace_client, repo):
    pid = repo.get_portfolio("tester", name="main").id
    response = workspace_client.post("/optimizer/run/", data={"portfolio_id": pid, "max_positions": "x"})
    assert response.status_code == 200
    assert b"Max positions must be a whole number" in response.data


def test_legacy_redirects(client):
    assert "/workspaces/" in client.get("/login/").headers["Location"]
    assert "/workspaces/" in client.get("/register/").headers["Location"]
    assert "/workspace/" in client.get("/account/").headers["Location"]


def test_landing_and_picker_render(client):
    assert client.get("/").status_code == 200
    assert client.get("/workspaces/").status_code == 200
    assert client.get("/static/css/output.css").status_code == 200


def test_flashed_message_renders_on_picker(client):
    client.post("/workspaces/create/", data={"name": "bad/name"})
    assert b"Workspace names are" in client.get("/workspaces/").data


def test_react_shell_page_is_gone(client):
    assert client.get("/optimizer/").status_code == 404


def test_database_from_before_the_rate_column_is_migrated_on_open(tmp_path):
    """An install created before portfolios.risk_free_rate existed must keep working after upgrading."""
    import sqlite3

    from wolvwealth.storage.database import Database
    from wolvwealth.storage.repository import PortfolioRepository

    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(str(path))
    old.executescript(
        "CREATE TABLE workspaces(name VARCHAR(64) NOT NULL, created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "PRIMARY KEY(name));"
        "CREATE TABLE portfolios(id INTEGER PRIMARY KEY AUTOINCREMENT, workspace VARCHAR(64) NOT NULL, "
        "name VARCHAR(64) NOT NULL, cash REAL NOT NULL DEFAULT 0, created DATETIME NOT NULL DEFAULT "
        "CURRENT_TIMESTAMP, UNIQUE(workspace, name), FOREIGN KEY(workspace) REFERENCES workspaces(name) "
        "ON DELETE CASCADE);"
        "CREATE TABLE holdings(portfolio_id INTEGER NOT NULL, ticker VARCHAR(16) NOT NULL, shares REAL NOT NULL, "
        "PRIMARY KEY(portfolio_id, ticker), FOREIGN KEY(portfolio_id) REFERENCES portfolios(id) ON DELETE CASCADE);"
        "INSERT INTO workspaces(name) VALUES ('old'); INSERT INTO portfolios(workspace, name, cash) "
        "VALUES ('old', 'p', 5);"
    )
    old.commit()
    old.close()
    with Database(path).connect() as connection:
        repo = PortfolioRepository(connection)
        portfolio = repo.get_portfolio("old", name="p")
        assert portfolio.cash == 5 and portfolio.risk_free_rate is None
        repo.set_risk_free_rate("old", portfolio.id, 0.02)
        assert repo.get_portfolio("old", name="p").risk_free_rate == 0.02
    with Database(path).connect() as connection:  # a second open finds nothing left to migrate
        assert Database.migrate(connection) == []


def test_repository_transaction_rolls_back_on_error(services):
    import pytest

    with pytest.raises(RuntimeError):
        with services.database.connect() as connection:
            services.repository(connection).create_workspace("doomed")
            raise RuntimeError("abort")
    with services.database.connect() as connection:
        assert not services.repository(connection).workspace_exists("doomed")


def test_workspace_page_survives_holding_without_price_data(client):
    # The demo seed holds AAPL/MSFT, which the synthetic market does not price.
    client.post("/workspaces/enter/", data={"name": "demo"})
    response = client.get("/workspace/")
    assert response.status_code == 200
    assert b"no price data" in response.data
