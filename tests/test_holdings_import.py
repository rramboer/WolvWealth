"""Tests for bulk holdings import: the parser, the browser form, and the JSON API."""

import io

from wolvwealth.domain.holdings_parse import parse_holdings, parse_number

UNIVERSE = ["AAA", "BBB", "CCC", "DDD"]


# --- parser -----------------------------------------------------------------


def test_parse_number_handles_brokerage_formatting():
    assert parse_number("1,234.5") == 1234.5
    assert parse_number("$12") == 12
    assert parse_number('"3"') == 3
    assert parse_number("(4)") == -4
    assert parse_number("--") is None
    assert parse_number("n/a") is None


def test_parse_simple_lines_any_separator():
    parsed = parse_holdings("AAA, 10\nBBB\t5.5\nccc 2\nDDD=1\n", universe=UNIVERSE)
    assert parsed.ok
    assert parsed.holdings == {"AAA": 10, "BBB": 5.5, "CCC": 2, "DDD": 1}


def test_parse_csv_with_header_and_extra_columns():
    text = (
        "Account Number,Symbol,Description,Quantity,Last Price,Current Value\n"
        'X123,AAA,Alpha Corp,"1,000.5",$10.00,"$10,005.00"\n'
        "X123,BBB,Beta Inc,3,$20.00,$60.00\n"
        "X123,SPAXX**,Money Market,500,$1.00,$500.00\n"
        ",,,,,\n"
        '"Account Total",,,,"$10,565.00"\n'
    )
    parsed = parse_holdings(text, universe=UNIVERSE)
    assert parsed.holdings == {"AAA": 1000.5, "BBB": 3}
    assert parsed.unknown == ["SPAXX"]
    assert not parsed.ok
    assert any("Account Total" in line or "line 6" in line for line in parsed.ignored)


def test_parse_header_with_shares_column_name():
    parsed = parse_holdings("Ticker;Shares;Cost\nAAA;4;100\nBBB;6;200", universe=UNIVERSE)
    assert parsed.ok and parsed.holdings == {"AAA": 4, "BBB": 6}


def test_parse_cash_row_and_duplicate_lots_summed():
    parsed = parse_holdings("AAA,1\nAAA,2\ncash,2500\n# comment\n\n", universe=UNIVERSE)
    assert parsed.ok
    assert parsed.holdings == {"AAA": 3}
    assert parsed.cash == 2500


def test_parse_rejects_negative_and_garbage_tickers():
    parsed = parse_holdings("AAA,-5\n@@@,1\nBBB,2", universe=UNIVERSE)
    assert parsed.holdings == {"BBB": 2}
    assert len(parsed.errors) == 2
    assert "line 1" in parsed.errors[0] and "negative" in parsed.errors[0]
    assert "line 2" in parsed.errors[1]


def test_parse_empty_and_header_only_inputs():
    assert parse_holdings("", universe=UNIVERSE).errors == ["No holdings given."]
    assert parse_holdings("   \n\n", universe=UNIVERSE).errors == ["No holdings given."]
    assert "No holdings found" in parse_holdings("Symbol,Quantity\n", universe=UNIVERSE).errors[0]
    half = parse_holdings("Symbol,Price\nAAA,10", universe=UNIVERSE)
    assert "header" in half.errors[0]


def test_parse_without_universe_accepts_any_ticker():
    parsed = parse_holdings("ZZZ 1", universe=None)
    assert parsed.ok and parsed.holdings == {"ZZZ": 1}


# --- browser form ------------------------------------------------------------


def test_import_paste_merges_by_default(workspace_client, repo):
    pid = repo.get_portfolio("tester", name="main").id
    response = workspace_client.post(
        "/portfolios/import/", data={"portfolio_id": pid, "text": "BBB, 5\nCCC, 2.5\nCASH, 999"}
    )
    assert response.status_code == 302
    portfolio = repo.get_portfolio("tester", name="main")
    assert portfolio.holdings == {"AAA": 10, "BBB": 5, "CCC": 2.5}  # AAA kept
    assert portfolio.cash == 999
    page = workspace_client.get("/workspace/").data
    assert b"Imported 2 holdings" in page and b"Cash set to $999.00" in page


def test_import_replace_mode_drops_existing(workspace_client, repo):
    pid = repo.get_portfolio("tester", name="main").id
    workspace_client.post("/portfolios/import/", data={"portfolio_id": pid, "text": "BBB 1", "mode": "replace"})
    assert repo.get_portfolio("tester", name="main").holdings == {"BBB": 1}


def test_import_file_upload(workspace_client, repo):
    pid = repo.get_portfolio("tester", name="main").id
    csv_bytes = "﻿Symbol,Quantity\r\nBBB,7\r\nDDD,1\r\n".encode("utf-8")
    workspace_client.post(
        "/portfolios/import/",
        data={"portfolio_id": pid, "file": (io.BytesIO(csv_bytes), "positions.csv")},
        content_type="multipart/form-data",
    )
    assert repo.get_portfolio("tester", name="main").holdings == {"AAA": 10, "BBB": 7, "DDD": 1}


def test_import_is_all_or_nothing_on_unknown_ticker(workspace_client, repo):
    pid = repo.get_portfolio("tester", name="main").id
    workspace_client.post("/portfolios/import/", data={"portfolio_id": pid, "text": "BBB 1\nFAKE 2"})
    assert repo.get_portfolio("tester", name="main").holdings == {"AAA": 10}
    page = workspace_client.get("/workspace/").data
    assert b"Nothing imported" in page and b"FAKE" in page


def test_import_skip_unknown_checkbox(workspace_client, repo):
    pid = repo.get_portfolio("tester", name="main").id
    workspace_client.post(
        "/portfolios/import/", data={"portfolio_id": pid, "text": "BBB 1\nFAKE 2", "skip_unknown": "on"}
    )
    assert repo.get_portfolio("tester", name="main").holdings == {"AAA": 10, "BBB": 1}
    assert b"Skipped unknown tickers: FAKE" in workspace_client.get("/workspace/").data


def test_import_scoped_to_workspace(workspace_client, repo):
    pid = repo.get_portfolio("tester", name="main").id
    workspace_client.post("/workspaces/leave/")
    workspace_client.post("/workspaces/create/", data={"name": "intruder"})
    workspace_client.post("/portfolios/import/", data={"portfolio_id": pid, "text": "BBB 1", "mode": "replace"})
    assert repo.get_portfolio("tester", name="main").holdings == {"AAA": 10}


def test_import_requires_open_workspace(client):
    response = client.post("/portfolios/import/", data={"portfolio_id": 1, "text": "AAA 1"})
    assert "/workspaces/" in response.headers["Location"]


# --- JSON API ----------------------------------------------------------------


def test_api_index_lists_import_endpoint(client):
    assert "/api/holdings/import/" in client.get("/api/").get_json()


def test_api_import_holdings_map(workspace_client, repo):
    response = workspace_client.post(
        "/api/holdings/import/",
        json={"workspace": "tester", "portfolio": "main", "holdings": {"bbb": 3, "CCC": 1.5}, "cash": 42},
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["imported"] == 2
    assert body["holdings"] == {"AAA": 10, "BBB": 3, "CCC": 1.5}
    assert body["cash"] == 42
    assert body["mode"] == "merge"


def test_api_import_csv_replace_and_create_portfolio(workspace_client, repo):
    response = workspace_client.post(
        "/api/holdings/import/",
        json={
            "workspace": "tester",
            "portfolio": "brokerage",
            "create_portfolio": True,
            "mode": "replace",
            "csv": "Symbol,Quantity\nAAA,1\nBBB,2\nCASH,100\n",
        },
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["holdings"] == {"AAA": 1, "BBB": 2} and body["cash"] == 100
    assert repo.get_portfolio("tester", name="brokerage").holdings == {"AAA": 1, "BBB": 2}


def test_api_import_then_optimize_round_trip(workspace_client, repo):
    workspace_client.post(
        "/api/holdings/import/",
        json={"workspace": "tester", "portfolio": "main", "mode": "replace", "holdings": {"BBB": 20}},
    )
    response = workspace_client.post(
        "/api/optimize/", json={"workspace": "tester", "portfolio": "main", "universe": ["top20"]}
    )
    assert response.status_code == 200
    assert response.get_json()["metrics"]["portfolio_value"] > 5000  # cash + 20 BBB


def test_api_import_unknown_ticker_is_400_with_payload(workspace_client, repo):
    response = workspace_client.post(
        "/api/holdings/import/", json={"workspace": "tester", "portfolio": "main", "holdings": {"FAKE": 1}}
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["payload"]["unknown_tickers"] == ["FAKE"]
    assert repo.get_portfolio("tester", name="main").holdings == {"AAA": 10}
    ok = workspace_client.post(
        "/api/holdings/import/",
        json={"workspace": "tester", "portfolio": "main", "holdings": {"FAKE": 1, "BBB": 1}, "skip_unknown": True},
    )
    assert ok.status_code == 200 and ok.get_json()["skipped_unknown"] == ["FAKE"]


def test_api_import_validation_errors(workspace_client, repo):
    base = {"workspace": "tester", "portfolio": "main"}
    cases = [
        ({**base}, "Missing input"),
        ({**base, "holdings": {"AAA": 1}, "csv": "AAA 1"}, "not both"),
        ({**base, "holdings": []}, "must be an object"),
        ({**base, "holdings": {"AAA": "ten"}}, "must be a number"),
        ({**base, "holdings": {"AAA": -1}}, "zero or more"),
        ({**base, "holdings": {"AAA": 1}, "mode": "append"}, "Invalid mode"),
        ({**base, "holdings": {"AAA": 1}, "cash": -5}, "Invalid cash"),
        ({**base, "portfolio": "nope", "holdings": {"AAA": 1}}, "Unknown portfolio"),
        ({"workspace": "ghost", "portfolio": "main", "holdings": {"AAA": 1}}, "Unknown workspace"),
    ]
    for body, needle in cases:
        response = workspace_client.post("/api/holdings/import/", json=body)
        assert response.status_code in (400, 404), body
        message = response.get_json()["error"]["message"] + " ".join(
            response.get_json()["error"].get("payload", {}).get("errors", [])
        )
        assert needle in message, (body, message)
    assert repo.get_portfolio("tester", name="main").holdings == {"AAA": 10}


def test_api_import_session_workspace_fallback(workspace_client, repo):
    response = workspace_client.post("/api/holdings/import/", json={"portfolio": "main", "holdings": {"BBB": 1}})
    assert response.status_code == 200 and response.get_json()["workspace"] == "tester"


def test_api_import_rejects_bad_json(client):
    response = client.post("/api/holdings/import/", data="{nope", content_type="application/json")
    assert response.status_code == 400
    response = client.post("/api/holdings/import/", json=[1, 2])
    assert response.status_code == 400
