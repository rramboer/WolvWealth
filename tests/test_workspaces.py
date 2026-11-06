"""Browser-flow tests for workspaces and portfolios (Flask test client, temp DB)."""

import sqlite3

import pytest

import wolvwealth.__main__ as cli
from conftest import synthetic_market
from wolvwealth.config import Settings
from wolvwealth.mcp.server import McpServer
from wolvwealth.services.container import Services
from wolvwealth.storage.database import Database
from wolvwealth.storage.repository import PortfolioRepository
from wolvwealth.web import context, create_app


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
    assert client.get("/workspace/").status_code == 302, "nothing open yet"
    assert client.post("/workspaces/enter/", data={"name": "demo"}).status_code == 302
    assert client.get("/workspace/").status_code == 200
    client.post("/workspaces/leave/")
    assert client.get("/workspace/").status_code == 302


def test_enter_missing_workspace_flashes(client):
    response = client.post("/workspaces/enter/", data={"name": "ghost"})
    assert "/workspaces/" in response.headers["Location"]
    assert client.get("/workspace/").status_code == 302  # nothing was opened


def test_delete_workspace_cascades_and_clears_only_its_own_session(workspace_client, repo):
    workspace_client.post("/workspaces/delete/", data={"name": "ghost"})  # nothing to delete
    workspace_client.post("/workspaces/delete/", data={"name": "demo"})  # another workspace: session untouched
    assert workspace_client.get("/workspace/").status_code == 200
    assert not repo.workspace_exists("demo") and repo.workspace_exists("tester")
    workspace_client.post("/workspaces/delete/", data={"name": "tester"})
    assert repo.get_portfolio("tester", name="main") is None
    assert workspace_client.get("/workspace/").status_code == 302  # session cleared


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


def test_portfolio_writes_are_scoped_to_the_open_workspace(workspace_client, repo):
    pid = repo.get_portfolio("tester", name="main").id
    workspace_client.post("/workspaces/leave/")
    workspace_client.post("/workspaces/create/", data={"name": "intruder"})
    workspace_client.post("/portfolios/holdings/", data={"portfolio_id": pid, "ticker": "BBB", "shares": "1"})
    workspace_client.post("/portfolios/cash/", data={"portfolio_id": pid, "cash": "1"})
    workspace_client.post("/portfolios/delete/", data={"portfolio_id": pid})
    untouched = repo.get_portfolio("tester", name="main")
    assert untouched is not None and untouched.holdings == {"AAA": 10} and untouched.cash == 5000
    assert repo.write_holdings("tester", 10**6, {"AAA": 1}) == 0, "no such portfolio: nothing written"
    workspace_client.post("/workspaces/enter/", data={"name": "tester"})
    workspace_client.post("/portfolios/delete/", data={"portfolio_id": pid})
    assert repo.get_portfolio("tester", name="main") is None


def test_browser_optimize_flow_renders_the_results_and_the_trades_to_place(workspace_client, repo):
    """The browser gets the same trade list the API hands an agent."""
    pid = repo.get_portfolio("tester", name="main").id
    response = workspace_client.post(
        "/optimizer/run/", data={"portfolio_id": pid, "universe": "top20", "max_positions": "2"}
    )
    assert response.status_code == 200
    assert b"%" in response.data and b"Trades to get there" in response.data  # results table rendered
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


def test_browser_unknown_universe_alias_falls_back_and_a_bad_number_is_flagged_in_place(workspace_client, repo):
    pid = repo.get_portfolio("tester", name="main").id
    response = workspace_client.post("/optimizer/run/", data={"portfolio_id": pid, "universe": "bogus"})
    assert response.status_code == 200 and b"top100" in response.data
    response = workspace_client.post("/optimizer/run/", data={"portfolio_id": pid, "max_weight": "nan"})
    assert response.status_code == 200 and b"max_weight must be a number" in response.data
    response = workspace_client.post("/optimizer/run/", data={"portfolio_id": pid, "max_positions": "x"})
    assert response.status_code == 200 and b"Max positions must be a whole number" in response.data
    response = workspace_client.post("/optimizer/run/", data={"portfolio_id": pid, "max_turnover": "x"})
    assert response.status_code == 200 and b"Max turnover must be a number" in response.data


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


# --- bad form input: flashed, and nothing changes ---------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/portfolios/create/",
        "/portfolios/delete/",
        "/portfolios/cash/",
        "/portfolios/risk-free-rate/",
        "/portfolios/holdings/",
        "/portfolios/import/",
        "/optimizer/run/",
    ],
)
def test_form_posts_without_an_open_workspace_go_to_the_picker(client, path):
    response = client.post(path, data={"portfolio_id": "1", "name": "x", "text": "AAA 1"})
    assert response.status_code == 302 and "/workspaces/" in response.headers["Location"]


@pytest.mark.parametrize(
    "path, data, flashed",
    [
        ("/portfolios/create/", {"name": "bad/name"}, "Portfolio names are"),
        ("/portfolios/create/", {"name": "main"}, "already exists"),
        ("/portfolios/create/", {"name": "fresh", "cash": "abc"}, "Cash must be a number."),
        ("/portfolios/create/", {"name": "fresh", "cash": "-1"}, "Cash must be zero or more."),
        ("/portfolios/cash/", {"cash": "abc"}, "Cash must be a number."),
        ("/portfolios/cash/", {"cash": "nan"}, "Cash must be a real amount"),  # float() accepts these three
        ("/portfolios/cash/", {"cash": "inf"}, "Cash must be a real amount"),
        ("/portfolios/create/", {"name": "fresh", "cash": "1e400"}, "Cash must be a real amount"),
        ("/portfolios/holdings/", {"ticker": "AAA", "shares": "nan"}, "Shares must be a real count"),
        ("/portfolios/holdings/", {"ticker": "AAA", "shares": "1e13"}, "Shares must be a real count"),
        ("/portfolios/import/", {"text": "AAA inf"}, "unusable amount"),
        ("/portfolios/holdings/", {"ticker": "AAA", "shares": "x"}, "Shares must be a number."),
        ("/portfolios/holdings/", {"ticker": "AAA", "shares": "-1"}, "Shares must be zero or more"),
        ("/portfolios/import/", {"portfolio_id": "999999", "text": "AAA 1"}, "That portfolio no longer exists."),
        ("/portfolios/import/", {"text": "AAA 1", "mode": "append"}, "Nothing imported. Invalid mode"),
        ("/optimizer/run/", {"portfolio_id": "999999"}, "That portfolio no longer exists."),
    ],
)
def test_bad_form_input_is_flashed_and_changes_nothing(workspace_client, repo, path, data, flashed):
    pid = repo.get_portfolio("tester", name="main").id
    response = workspace_client.post(path, data={"portfolio_id": pid, **data})
    assert response.status_code == 302
    assert flashed in workspace_client.get("/workspace/").data.decode()
    portfolio = repo.get_portfolio("tester", name="main")
    assert portfolio.cash == 5000 and portfolio.holdings == {"AAA": 10}
    assert repo.get_portfolio("tester", name="fresh") is None


def test_form_posts_with_an_unparseable_portfolio_id_do_nothing(workspace_client, repo):
    everything = {"portfolio_id": "abc", "cash": "1", "ticker": "AAA", "shares": "1", "risk_free_rate": "0.01"}
    for path in ("/portfolios/delete/", "/portfolios/cash/", "/portfolios/risk-free-rate/", "/portfolios/holdings/"):
        response = workspace_client.post(path, data=everything)
        assert response.status_code == 302 and response.headers["Location"].endswith("/workspace/"), path
    response = workspace_client.post("/optimizer/run/", data={"portfolio_id": "abc"})
    assert response.status_code == 302 and response.headers["Location"].endswith("/workspace/")
    portfolio = repo.get_portfolio("tester", name="main")
    assert portfolio.cash == 5000 and portfolio.holdings == {"AAA": 10} and portfolio.risk_free_rate is None


# --- pages and plumbing -----------------------------------------------------


def test_legacy_redirects_and_removed_pages(client):
    assert "/workspaces/" in client.get("/login/").headers["Location"]
    assert "/workspaces/" in client.get("/register/").headers["Location"]
    assert "/workspace/" in client.get("/account/").headers["Location"]
    assert client.get("/optimizer/").status_code == 404, "the React shell page is gone"


def test_landing_and_picker_render(client):
    assert client.get("/").status_code == 200
    assert client.get("/workspaces/").status_code == 200
    assert client.get("/static/css/output.css").status_code == 200


def test_flashed_message_renders_on_picker(client):
    client.post("/workspaces/create/", data={"name": "bad/name"})
    assert b"Workspace names are" in client.get("/workspaces/").data


def test_database_from_before_the_rate_column_is_migrated_on_open(tmp_path):
    """An install created before portfolios.risk_free_rate existed must keep working after upgrading."""
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


def test_unusable_stored_values_are_flagged_left_out_and_block_optimizing_until_fixed(workspace_client, services, repo):
    """A row edited outside the app must neither crash the page nor reach the optimizer as a guess."""
    pid = repo.get_portfolio("tester", name="main").id
    with sqlite3.connect(str(services.settings.database_path)) as raw:
        raw.execute("UPDATE portfolios SET cash = 'lots', risk_free_rate = 4.0 WHERE id = ?", (pid,))
        raw.execute(
            "INSERT INTO holdings(portfolio_id, ticker, shares) VALUES (?, 'BBB', -2), (?, 'CCC', 3)", (pid, pid)
        )
    portfolio = repo.get_portfolio("tester", name="main")
    assert portfolio.cash == 0.0 and portfolio.risk_free_rate is None
    assert portfolio.holdings == {"AAA": 10, "CCC": 3}
    assert [problem.split(" ")[0] for problem in portfolio.problems] == ["cash", "risk-free", "BBB"]

    page = workspace_client.get("/workspace/")
    assert page.status_code == 200 and b"could not be used" in page.data and b"BBB has -2.0 shares" in page.data
    listed = workspace_client.get("/api/workspaces/").get_json()["workspaces"]
    main = next(p for w in listed if w["name"] == "tester" for p in w["portfolios"] if p["name"] == "main")
    assert main["problems"] == portfolio.problems
    refused = workspace_client.post("/api/optimize/", json={"portfolio": "main"})
    assert refused.status_code == 409 and refused.get_json()["error"]["payload"]["problems"] == portfolio.problems
    assert b"no trades were planned" in workspace_client.post("/optimizer/run/", data={"portfolio_id": pid}).data

    # Every problem is fixable from the page itself.
    workspace_client.post("/portfolios/cash/", data={"portfolio_id": pid, "cash": "100"})
    workspace_client.post("/portfolios/risk-free-rate/", data={"portfolio_id": pid, "risk_free_rate": ""})
    workspace_client.post("/portfolios/holdings/", data={"portfolio_id": pid, "ticker": "BBB", "shares": "0"})
    assert repo.get_portfolio("tester", name="main").problems == []
    assert workspace_client.post("/api/optimize/", json={"portfolio": "main"}).status_code == 200


@pytest.mark.parametrize(
    "corrupt, fragment",
    [
        (lambda settings: settings.database_path.write_bytes(b"not sqlite" * 100), "database"),
        (lambda settings: settings.prices_path.write_bytes(b"not parquet"), "price history"),
        (lambda settings: settings.ticker_universe_path.write_text("", encoding="utf-8"), "ticker list"),
    ],
    ids=["database", "price cache", "ticker list"],
)
def test_unreadable_stored_data_is_reported_with_a_remedy_on_every_surface(tmp_path, capsys, corrupt, fragment):
    """No traceback anywhere: the browser gets a page, the API its JSON shape, MCP a tool error, the CLI exit 1."""
    settings = Settings(var_dir=tmp_path / "var", ticker_universe_path=tmp_path / "universe.csv", auto_refresh=False)
    services = Services.build(settings, log=lambda *_: None)
    settings.ticker_universe_path.write_text("AAA\nBBB\nCCC\nDDD\n", encoding="utf-8")
    services.price_store.save_prices(synthetic_market().prices)
    services.database.open().close()
    corrupt(settings)

    app = create_app(services)
    app.config["PROPAGATE_EXCEPTIONS"] = False
    client = app.test_client()
    page = client.post("/workspaces/enter/", data={"name": "demo"}, follow_redirects=True)
    assert page.status_code == 500
    assert "can't read its data" in page.get_data(as_text=True) and fragment in page.get_data(as_text=True)
    body = client.post("/api/optimize/", json={"workspace": "demo", "portfolio": "main"}).get_json()
    assert body["error"]["status_code"] == 500 and fragment in body["error"]["message"]
    tool = McpServer(services, log=lambda *_: None).call_tool(
        {"name": "optimize", "arguments": {"workspace": "demo", "portfolio": "main"}}
    )
    assert tool["isError"] is True and fragment in tool["content"][0]["text"]
    assert cli.main(["optimize", "--workspace", "demo", "--portfolio", "main"], services) == 1
    assert fragment in capsys.readouterr().err


def test_database_reset_works_before_the_file_exists(tmp_path):
    database = Database(tmp_path / "fresh.sqlite3")
    database.reset()
    with database.connect() as connection:
        assert [row["name"] for row in connection.execute("SELECT name FROM workspaces")] == ["demo"]


def test_repository_transaction_rolls_back_on_error(services):
    with pytest.raises(RuntimeError):
        with services.database.connect() as connection:
            services.repository(connection).create_workspace("doomed")
            raise RuntimeError("abort")
    with services.database.connect() as connection:
        assert not services.repository(connection).workspace_exists("doomed")


def test_a_crashing_request_rolls_back_its_writes(app, repo):
    """The per-request connection commits at teardown only if the view succeeded."""

    @app.route("/boom/")
    def boom():
        context.repo().create_workspace("doomed")
        raise RuntimeError("abort")

    try:
        assert app.test_client().get("/boom/").status_code == 500
    except RuntimeError:
        pass  # the test client re-raises when Flask propagates exceptions
    assert not repo.workspace_exists("doomed")


def test_workspace_page_survives_holding_without_price_data(client):
    # The demo seed holds AAPL/MSFT, which the synthetic market does not price.
    client.post("/workspaces/enter/", data={"name": "demo"})
    response = client.get("/workspace/")
    assert response.status_code == 200
    assert b"no price data" in response.data
