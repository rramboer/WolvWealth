"""The MCP server: protocol framing, the handshake, the four tools, and the stdio hygiene that keeps it alive.

In-process tests drive ``McpServer`` through ``protocol.dispatch``; one test
runs the real ``wolvwealth mcp`` subprocess over pipes, since the failure
modes that matter (a stray print on stdout, a handshake that waits on the
price cache, a tool that starts a download) only show up end to end.
"""

import dataclasses
import io
import json
import os
import subprocess
import sys
import time

import numpy as np
import pytest

import wolvwealth.__main__ as cli
from conftest import synthetic_market
from test_api_contract import DOCUMENTED
from test_background_refresh import appending_downloader, build_service
from wolvwealth.config import REPO_ROOT, Settings
from wolvwealth.domain.market import MAX_STALE_DAYS, MarketData
from wolvwealth.mcp import protocol, server, tools
from wolvwealth.mcp.server import McpServer
from wolvwealth.services.container import Services
from wolvwealth.storage.database import Database

TOOL_NAMES = ["market_status", "list_portfolios", "optimize", "import_holdings"]


def call(mcp: McpServer, method: str, params=None, request_id=1):
    """Dispatch one request and return the raw JSON-RPC response."""
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return protocol.dispatch(message, mcp.methods(), log=lambda *_: None)


def tool_result(mcp: McpServer, name: str, arguments=None) -> dict:
    """Call a tool and return the ``tools/call`` result block."""
    response = call(mcp, "tools/call", {"name": name, "arguments": arguments or {}})
    assert "result" in response, response
    return response["result"]


def text_of(result: dict) -> str:
    return "\n".join(block["text"] for block in result["content"] if block["type"] == "text")


@pytest.fixture
def mcp(services):
    return McpServer(services, log=lambda *_: None)


# --- handshake and listing --------------------------------------------------


def test_initialize_echoes_a_known_protocol_version_or_offers_the_newest_and_lists_tools_only(mcp):
    result = call(mcp, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "wolvwealth" and result["serverInfo"]["version"]
    assert "never places orders" in result["instructions"] and "as_of" in result["instructions"]
    unknown = call(mcp, "initialize", {"protocolVersion": "1999-01-01"})["result"]
    assert unknown["protocolVersion"] == server.PROTOCOL_VERSIONS[0]
    assert call(mcp, "initialize", {})["result"]["protocolVersion"] == server.PROTOCOL_VERSIONS[0]


def test_tools_list_describes_the_four_tools_with_schemas_and_annotations(mcp):
    listed = call(mcp, "tools/list")["result"]["tools"]
    assert [tool["name"] for tool in listed] == TOOL_NAMES
    for tool in listed:
        assert tool["description"] and tool["inputSchema"]["type"] == "object"
        assert set(tool["annotations"]) == {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
    by_name = {tool["name"]: tool for tool in listed}
    assert all(
        by_name[name]["annotations"]["readOnlyHint"] for name in ("market_status", "list_portfolios", "optimize")
    )
    assert not by_name["import_holdings"]["annotations"]["readOnlyHint"]
    assert by_name["import_holdings"]["inputSchema"]["required"] == ["workspace", "portfolio"]


def test_tool_schemas_only_name_fields_api_md_documents():
    """The tools are API.md's bodies in schema form; a field here that API.md lacks is drift."""
    optimize_schema = tools.OPTIMIZE_SCHEMA["properties"]
    assert set(optimize_schema) - DOCUMENTED == {"constraints"}, "API.md names the nested fields as constraints.<x>"
    assert set(optimize_schema["constraints"]["properties"]) == set(cli.CONSTRAINT_FLAGS)
    assert set(tools.IMPORT_SCHEMA["properties"]) - DOCUMENTED == set()


def test_ping_and_notifications_and_unknown_methods(mcp):
    assert call(mcp, "ping")["result"] == {}
    initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert protocol.dispatch(initialized, mcp.methods()) is None
    unknown_notification = {"jsonrpc": "2.0", "method": "notifications/whatever"}
    assert protocol.dispatch(unknown_notification, mcp.methods()) is None
    response = call(mcp, "resources/list")
    assert response["error"]["code"] == protocol.METHOD_NOT_FOUND


def test_malformed_messages_get_an_invalid_request_or_silence(mcp):
    methods = mcp.methods()
    assert protocol.dispatch("not an object", methods)["error"]["code"] == protocol.INVALID_REQUEST
    no_method = protocol.dispatch({"jsonrpc": "2.0", "id": 9}, methods)
    assert no_method["id"] == 9 and no_method["error"]["code"] == protocol.INVALID_REQUEST
    assert protocol.dispatch({"jsonrpc": "2.0", "id": 9, "result": {}}, methods) is None, "a reply to us: ignored"
    assert protocol.dispatch({"jsonrpc": "2.0"}, methods) is None
    for method in (5, ["ping"]):
        wrong = protocol.dispatch({"jsonrpc": "2.0", "id": 3, "method": method}, methods)
        assert (
            wrong["error"]["code"] == protocol.INVALID_REQUEST
            and "method must be a string" in wrong["error"]["message"]
        )
    with_data = protocol.error_response(1, protocol.INVALID_PARAMS, "bad", {"field": "x"})
    assert with_data["error"]["data"] == {"field": "x"}

    def boom(_params):
        raise RuntimeError("kaboom")

    quiet = protocol.dispatch({"jsonrpc": "2.0", "id": 1, "method": "boom"}, {"boom": boom})  # no log given
    assert quiet["error"]["code"] == protocol.INTERNAL_ERROR and "kaboom" in quiet["error"]["message"]


def test_unknown_tool_and_bad_arguments_are_protocol_errors(mcp):
    response = call(mcp, "tools/call", {"name": "launch_missiles", "arguments": {}})
    assert response["error"]["code"] == protocol.INVALID_PARAMS and "tools/list" in response["error"]["message"]
    response = call(mcp, "tools/call", {"name": "optimize", "arguments": "not an object"})
    assert response["error"]["code"] == protocol.INVALID_PARAMS


# --- the tools --------------------------------------------------------------


def test_market_status_reports_freshness_and_classifies_tickers(mcp, services):
    services.market.set_tbill(0.0412, "2026-10-30")
    prices = services.market.data.prices.copy()
    # BBB quoted nowhere in the staleness window: known, but no current price.
    prices.iloc[-(MAX_STALE_DAYS + 1) :, prices.columns.get_loc("BBB")] = np.nan
    prices.iloc[-1, prices.columns.get_loc("CCC")] = np.nan  # merely throttled today: still tradeable
    services.market.set_data(MarketData.build(services.market.data.universe, prices))
    report = tool_result(mcp, "market_status", {"tickers": ["aaa", "bbb", "ccc", "ZZZ"]})["structuredContent"]
    assert report["as_of"].startswith("2021-") and report["age_days"] > 365 and report["stale"] is True
    assert "refresh-data" in report["refresh_hint"]
    assert report["tbill_rate"] == 0.0412 and report["universe_aliases"][0] == "top100"
    assert report["tickers"] == {
        "tradeable": ["AAA", "CCC"],
        "short_history": [],
        "quarantined": [],
        "no_current_price": ["BBB"],
        "unknown": ["ZZZ"],
    }
    assert report["stale_prices"]["CCC"]["trading_days_stale"] == 1, "carried forward, and said so"
    assert "BBB" not in report["stale_prices"], "no current price at all is not a stale price"
    assert "tickers" not in tool_result(mcp, "market_status")["structuredContent"]
    wrong = tool_result(mcp, "market_status", {"tickers": "AAA"})
    assert wrong["isError"] is True and "list of strings" in text_of(wrong)


def test_list_portfolios_matches_the_api(mcp, workspace_client):
    via_tool = tool_result(mcp, "list_portfolios")
    via_api = workspace_client.get("/api/workspaces/").get_json()
    assert via_tool["structuredContent"] == via_api
    assert json.loads(text_of(via_tool)) == via_api
    assert any(w["name"] == "tester" for w in via_api["workspaces"])


def test_optimize_tool_returns_the_api_body_with_held_tickers_kept_allowed(mcp, client):
    arguments = {"initial_cash": 1000, "initial_holdings": {"AAA": 10}, "universe": ["top20"]}
    result = tool_result(mcp, "optimize", arguments)
    assert result["isError"] is False
    via_api = client.post("/api/optimize/", json=dict(arguments, universe=["top20", "AAA"])).get_json()
    assert result["structuredContent"] == via_api
    assert set(result["structuredContent"]) == {
        "as_of",
        "risk_free_rate",
        "optimized_portfolio",
        "cash",
        "trades",
        "metrics",
    }


def test_optimize_tool_uses_a_stored_portfolio_and_its_rate(mcp, repo):
    repo.create_workspace("ryan")
    pid = repo.create_portfolio("ryan", "long-term", cash=2000).id
    repo.set_holding("ryan", pid, "DDD", 7)
    repo.set_risk_free_rate("ryan", pid, 0.035)
    body = tool_result(mcp, "optimize", {"workspace": "ryan", "portfolio": "long-term", "universe": ["AAA"]})
    assert body["structuredContent"]["risk_free_rate"] == {"value": 0.035, "source": "portfolio"}
    assert set(body["structuredContent"]["optimized_portfolio"]) <= {"AAA", "DDD"}


def test_domain_errors_are_tool_errors_the_model_can_act_on(mcp):
    result = tool_result(mcp, "optimize", {"initial_cash": 1000, "universe": ["FAKE"]})
    assert result["isError"] is True and "Invalid symbol in universe: FAKE" in text_of(result)
    infeasible = {"initial_cash": 1000, "constraints": {"max_positions": 2, "max_weight": 0.1}}
    result = tool_result(mcp, "optimize", infeasible)
    assert result["isError"] is True
    assert "Infeasible" in text_of(result) and '"conflicts"' in text_of(result), "the remedy payload rides along"
    result = tool_result(mcp, "optimize", {"workspace": "nobody", "portfolio": "x"})
    assert result["isError"] is True and "Unknown portfolio" in text_of(result)
    # Malformed shapes are named, not iterated: the held-tickers step used to crash on these.
    for arguments, field in [({"initial_holdings": 5}, "initial_holdings"), ({"universe": 5}, "universe")]:
        result = tool_result(mcp, "optimize", {"initial_cash": 1000, **arguments})
        assert result["isError"] is True and f"Invalid {field}" in text_of(result)
    result = tool_result(mcp, "optimize", {"initial_cash": float("nan")})
    assert result["isError"] is True and "initial_cash must be a number" in text_of(result)


def test_import_holdings_tool_writes_and_matches_the_api_shape(mcp, repo, client, services):
    services.market.set_tbill(0.0412, "2026-10-30")
    repo.create_workspace("ryan")
    arguments = {
        "workspace": "ryan",
        "portfolio": "ira",
        "create_portfolio": True,
        "csv": "Symbol,Quantity\nAAA,12\nCASH,750\n",
        "risk_free_rate": "tbill",
    }
    result = tool_result(mcp, "import_holdings", arguments)
    assert result["isError"] is False
    body = result["structuredContent"]
    assert body["holdings"] == {"AAA": 12.0} and body["cash"] == 750 and body["risk_free_rate"] == 0.0412
    stored = repo.get_portfolio("ryan", name="ira")
    assert stored.holdings == {"AAA": 12.0} and stored.cash == 750
    via_api = client.post(
        "/api/holdings/import/",
        json={"workspace": "ryan", "portfolio": "ira2", "create_portfolio": True, "holdings": {"BBB": 1}},
    ).get_json()
    assert set(body) == set(via_api)
    failed = tool_result(mcp, "import_holdings", {"workspace": "ryan", "portfolio": "ira", "holdings": {"NOPE": 1}})
    assert failed["isError"] is True and "NOPE" in text_of(failed)
    assert repo.get_portfolio("ryan", name="ira").holdings == {"AAA": 12.0}, "nothing written on failure"


# --- data lifecycle ---------------------------------------------------------


def test_tools_refuse_to_download_when_there_is_no_price_cache(tmp_path):
    """A model's tool call must never start the multi-minute first download; it says what to run instead."""
    services = Services.build(Settings(var_dir=tmp_path / "var", auto_refresh=False), log=lambda *_: None)
    services.market.downloader = lambda *a, **k: pytest.fail("must not download")
    mcp = McpServer(services, log=lambda *_: None)
    for name in ("market_status", "optimize", "import_holdings"):
        result = tool_result(mcp, name, {"workspace": "demo", "portfolio": "x", "holdings": {}})
        assert result["isError"] is True and "wolvwealth refresh-data" in text_of(result), name
    assert not services.market.loaded()
    assert tool_result(mcp, "list_portfolios")["isError"] is False, "no prices needed to list what is stored"


def services_over_a_real_store(tmp_path, auto_refresh: bool) -> Services:
    market, store = build_service(tmp_path, downloader=None)
    settings = Settings(var_dir=tmp_path / "var", auto_refresh=auto_refresh)
    return Services(settings=settings, database=Database(settings.database_path), price_store=store, market=market)


def test_first_tool_call_loads_lazily_and_starts_the_same_background_refresh_as_serve(tmp_path, monkeypatch):
    services = services_over_a_real_store(tmp_path, auto_refresh=True)
    started = []
    monkeypatch.setattr(services.market, "start_background_refresh", lambda days: started.append(days))
    mcp = McpServer(services, log=lambda *_: None)
    call(mcp, "initialize", {"protocolVersion": "2025-06-18"})
    assert not services.market.loaded(), "the handshake must not wait on the price cache"
    assert tool_result(mcp, "market_status")["isError"] is False
    assert services.market.loaded() and started == [services.settings.auto_refresh_max_age_days]
    tool_result(mcp, "market_status")
    assert started == [services.settings.auto_refresh_max_age_days], "started once per process, as serve does"


def test_reload_if_changed_notices_another_process_rewriting_the_cache(tmp_path):
    service, store = build_service(tmp_path, downloader=None)
    first = service.data
    assert service.reload_if_changed() is False
    time.sleep(0.02)  # a distinct mtime even on coarse filesystems; the size changes too
    store.save_prices(first.prices.iloc[:-1])
    assert service.reload_if_changed() is True
    assert len(service.data.prices) == len(first.prices) - 1
    # This process's own refresh writes the file too, and must not count as foreign.
    service.downloader = appending_downloader(service)
    assert service.refresh() is True
    assert service.reload_if_changed() is False


def test_a_long_lived_server_serves_the_refreshed_prices(tmp_path):
    services = services_over_a_real_store(tmp_path, auto_refresh=False)
    mcp = McpServer(services, log=lambda *_: None)
    before = tool_result(mcp, "market_status")["structuredContent"]["as_of"]
    other_process = build_service(tmp_path, downloader=None)[0]  # same files, separate service object
    other_process.downloader = appending_downloader(other_process)
    time.sleep(0.02)
    assert other_process.refresh() is True
    after = tool_result(mcp, "market_status")["structuredContent"]["as_of"]
    assert after > before


# --- stdio transport --------------------------------------------------------


def run_session(services, lines, monkeypatch=None):
    """Feed newline-delimited messages through ``server.run`` and return the parsed stdout lines."""
    stdin = io.BytesIO(("\n".join(json.dumps(m) if not isinstance(m, str) else m for m in lines) + "\n").encode())
    stdout = io.BytesIO()
    assert server.run(services, stdin=stdin, stdout=stdout, log=lambda *_: None) == 0
    raw = stdout.getvalue().decode()
    return raw, [json.loads(line) for line in raw.splitlines()]


def test_serve_frames_one_json_message_per_line_and_handles_garbage_and_batches(services):
    raw, out = run_session(
        services,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            "this is not json",
            "[" * 100_000,  # deeper than the JSON parser recurses: a parse error, not the end of the session
            "",
            [{"jsonrpc": "2.0", "id": 2, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/cancelled"}],
            [],
            [{"jsonrpc": "2.0", "method": "notifications/initialized"}],  # only notifications: nothing owed
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        ],
    )
    assert raw.count("\n") == len(out) == 6, raw
    assert out[0]["id"] == 1 and "protocolVersion" in out[0]["result"]
    assert out[1]["error"]["code"] == out[2]["error"]["code"] == protocol.PARSE_ERROR and out[1]["id"] is None
    assert out[3] == [{"jsonrpc": "2.0", "id": 2, "result": {}}], "a batch answers with a batch, notifications omitted"
    assert out[4]["error"]["code"] == protocol.INVALID_REQUEST
    assert [t["name"] for t in out[5]["result"]["tools"]] == TOOL_NAMES


def test_stray_prints_during_a_tool_call_go_to_stderr_not_the_protocol_stream(services, monkeypatch, capsys):
    """One line of chatter on stdout ends an MCP session; everything that prints must land on stderr."""
    noisy = dataclasses.replace(
        tools.BY_NAME["list_portfolios"], handler=lambda *_: print("downloading...") or {"ok": 1}
    )
    monkeypatch.setitem(tools.BY_NAME, "list_portfolios", noisy)
    raw, out = run_session(
        services, [{"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "list_portfolios"}}]
    )
    assert "downloading" not in raw and out == [
        {
            "jsonrpc": "2.0",
            "id": 7,
            "result": {
                "content": [{"type": "text", "text": '{\n  "ok": 1\n}'}],
                "structuredContent": {"ok": 1},
                "isError": False,
            },
        }
    ]
    assert "downloading..." in capsys.readouterr().err


def test_a_crashing_handler_answers_an_internal_error_and_the_loop_survives(services, monkeypatch):
    def boom(*_):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(
        tools.BY_NAME, "list_portfolios", dataclasses.replace(tools.BY_NAME["list_portfolios"], handler=boom)
    )
    _, out = run_session(
        services,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_portfolios"}},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        ],
    )
    assert out[0]["error"]["code"] == protocol.INTERNAL_ERROR and "kaboom" in out[0]["error"]["message"]
    assert out[1] == {"jsonrpc": "2.0", "id": 2, "result": {}}


def test_real_subprocess_session_answers_the_handshake_before_touching_any_price_data(tmp_path):
    """The whole thing, as an AI app runs it: a child process, pipes, an install with no price cache yet."""
    env = dict(os.environ, WOLVWEALTH_VAR=str(tmp_path / "var"), WOLVWEALTH_NO_AUTO_REFRESH="1")
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_portfolios", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "market_status", "arguments": {}}},
    ]
    completed = subprocess.run(
        [sys.executable, "-m", "wolvwealth", "mcp"],
        input="\n".join(json.dumps(m) for m in messages) + "\n",
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    out = [json.loads(line) for line in completed.stdout.splitlines()]  # every stdout line is protocol
    assert [m["id"] for m in out] == [1, 2, 3, 4]
    assert out[0]["result"]["protocolVersion"] == "2025-06-18"
    assert [t["name"] for t in out[1]["result"]["tools"]] == TOOL_NAMES
    assert out[2]["result"]["structuredContent"]["workspaces"][0]["name"] == "demo"
    assert out[3]["result"]["isError"] is True and "wolvwealth refresh-data" in out[3]["result"]["content"][0]["text"]
    assert not (tmp_path / "var" / "historical_prices.parquet").exists(), "no download was attempted"


# --- CLI entry --------------------------------------------------------------


def test_mcp_config_prints_json_an_app_can_use_without_path(services, capsys):
    assert cli.build_parser().parse_args(["mcp", "--config"]).config
    assert not cli.build_parser().parse_args(["mcp"]).config
    assert cli.main(["mcp", "--config"], services) == 0
    out, err = capsys.readouterr()
    config = json.loads(out)
    assert config == {"mcpServers": {"wolvwealth": {"command": sys.executable, "args": ["-m", "wolvwealth", "mcp"]}}}
    assert os.path.isabs(config["mcpServers"]["wolvwealth"]["command"])
    assert "claude_desktop_config.json" in err and "claude mcp add wolvwealth" in err and "MCP.md" in err


def test_mcp_mode_builds_stderr_logging_services_and_hands_them_to_the_server(monkeypatch, services, capsys):
    seen = {}

    def fake_build(log=print, **_kwargs):
        seen["log"] = log
        return services

    monkeypatch.setattr(cli.Services, "build", fake_build)
    monkeypatch.setattr(server, "run", lambda given: seen.update(served=given) or 0)
    assert cli.main(["mcp"]) == 0
    assert seen["served"] is services
    seen["log"]("hello from a refresh")
    out, err = capsys.readouterr()
    assert "hello from a refresh" in err and "hello from a refresh" not in out


def test_claude_desktop_config_path_follows_the_platform_and_the_version_survives_an_uninstalled_tree(monkeypatch):
    monkeypatch.setattr(server.sys, "platform", "darwin")
    mac = str(server.claude_desktop_config_path())
    assert mac.endswith("Library/Application Support/Claude/claude_desktop_config.json")
    monkeypatch.setattr(server.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", "C:/Users/me/AppData/Roaming")
    assert str(server.claude_desktop_config_path()).startswith("C:/Users/me/AppData/Roaming")
    monkeypatch.setattr(server.sys, "platform", "linux")
    assert str(server.claude_desktop_config_path()).endswith(".config/Claude/claude_desktop_config.json")

    def not_installed(_name):
        raise server.importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(server.importlib.metadata, "version", not_installed)
    assert server.version_string() == "0"
