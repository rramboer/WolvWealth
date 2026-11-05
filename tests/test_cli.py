"""Tests for the `wolvwealth` command line (serve, reset-db, dump-db, refresh-data parsing)."""

import pytest

import wolvwealth.__main__ as cli


def test_parser_commands_and_defaults():
    parser = cli.build_parser()
    serve = parser.parse_args(["serve"])
    assert serve.command == "serve" and serve.port == 8000 and not serve.dev and not serve.no_browser
    serve = parser.parse_args(["serve", "--port", "9000", "--dev", "--no-browser"])
    assert serve.port == 9000 and serve.dev and serve.no_browser
    assert parser.parse_args(["refresh-data", "--full"]).full
    assert parser.parse_args(["reset-db"]).command == "reset-db"
    with pytest.raises(SystemExit):
        parser.parse_args([])
    with pytest.raises(SystemExit):
        parser.parse_args(["bogus"])


def test_serve_binds_localhost_and_opens_browser(services, monkeypatch):
    calls = {}
    monkeypatch.setattr("flask.Flask.run", lambda self, **kwargs: calls.update(kwargs))
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)

    class ImmediateTimer:
        def __init__(self, _delay, func, args=()):
            self.func, self.args = func, args

        def start(self):
            self.func(*self.args)

    monkeypatch.setattr(cli.threading, "Timer", ImmediateTimer)
    assert cli.main(["serve", "--port", "8123"], services) == 0
    assert calls == {"host": "127.0.0.1", "port": 8123, "debug": False}
    assert opened == ["http://localhost:8123/"]

    opened.clear()
    cli.main(["serve", "--no-browser", "--dev"], services)
    assert calls["debug"] is True and opened == []


def test_reset_db_erases_workspaces_and_restores_demo(workspace_client, services, capsys):
    assert cli.main(["reset-db"], services) == 0
    names = [w["name"] for w in workspace_client.get("/api/workspaces/").get_json()["workspaces"]]
    assert names == ["demo"]
    assert "All workspaces erased" in capsys.readouterr().out


def test_dump_db_prints_tables(workspace_client, services, capsys):
    assert cli.main(["dump-db"], services) == 0
    out = capsys.readouterr().out
    assert "== workspaces ==" in out and "tester" in out and "'AAA'" in out


def test_refresh_data_reports_up_to_date(services, capsys):
    services.market.downloader = lambda *a, **k: pytest.fail("must not download when nothing is missing")
    import pandas as pd

    # Pretend the newest cached row is today so the incremental path has nothing to fetch.
    frame = services.market.data.prices.copy()
    frame.index = pd.date_range(end=pd.Timestamp.now().normalize(), periods=len(frame))
    services.market.set_data(services.market.data.__class__(services.market.data.universe, frame))
    services.market.tbill_downloader = lambda: (0.0412, "2026-10-30")
    assert cli.main(["refresh-data"], services) == 0
    out = capsys.readouterr().out
    assert "Prices already up to date" in out
    assert "T-bill yield: 4.12% as of 2026-10-30" in out
    assert services.market.tbill == {"rate": 0.0412, "as_of": "2026-10-30"}
    assert services.price_store.load_tbill() == {"rate": 0.0412, "as_of": "2026-10-30"}


# --- optimize ---------------------------------------------------------------


def test_optimize_parser_maps_every_api_field():
    args = cli.build_parser().parse_args(
        [
            "optimize",
            "--workspace", "ryan", "--portfolio", "long-term",
            "--cash", "100", "--holdings", "AAA=10",
            "--universe", "top20", "BBB",
            "--max-positions", "3", "--max-weight", "0.5", "--min-universal-weight", "0.01",
            "--max-turnover", "0.2", "--cash-reserve", "0.1",
            "--risk-free-rate", "tbill", "--json",
        ]
    )  # fmt: skip
    assert args.command == "optimize" and args.json and args.risk_free_rate == "tbill"
    assert args.universe == ["top20", "BBB"] and args.max_positions == 3 and args.cash_reserve == 0.1
    assert cli.build_parser().parse_args(["optimize", "--risk-free-rate", "0.04"]).risk_free_rate == 0.04
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["optimize", "--risk-free-rate", "four"])


def test_optimize_prints_a_readable_report(services, capsys):
    code = cli.main(
        [
            "optimize",
            "--cash",
            "1000",
            "--holdings",
            "AAA=10, BBB=5",
            "--universe",
            "top20",
            "--risk-free-rate",
            "0.02",
        ],
        services,
    )
    out, err = capsys.readouterr()
    assert code == 0
    assert "Max-Sharpe allocation for the given cash and holdings, priced at the 2021-" in out
    assert "Universe: top20, AAA, BBB" in out
    assert "Risk-free rate: 2.00% (from --risk-free-rate)" in out
    assert "Target allocation" in out and "TICKER" in out and "Portfolio value $" in out
    assert "Trades to get there, sells first" in out or "No trades" in out
    assert "cached prices are" in err and "refresh-data" in err, "the synthetic market is years old; say so"


def test_optimize_without_a_rate_says_zero_was_assumed(services, capsys):
    assert cli.main(["optimize", "--cash", "1000"], services) == 0
    out = capsys.readouterr().out
    assert "Risk-free rate: 0.00% (none given, so 0% assumed)" in out
    assert "0% was assumed" in out


def test_optimize_json_is_the_api_response_body(services, client, capsys):
    import json

    code = cli.main(["optimize", "--cash", "1000", "--holdings", "AAA=10", "--universe", "top20", "--json"], services)
    assert code == 0
    body = json.loads(capsys.readouterr().out)
    via_api = client.post(
        "/api/optimize/", json={"initial_cash": 1000, "initial_holdings": {"AAA": 10}, "universe": ["top20", "AAA"]}
    ).get_json()
    assert body == via_api


def test_optimize_stored_portfolio_uses_its_rate_and_keeps_held_tickers_allowed(services, repo, capsys):
    repo.create_workspace("ryan")
    pid = repo.create_portfolio("ryan", "long-term", cash=2000).id
    repo.set_holding("ryan", pid, "DDD", 7)
    repo.set_risk_free_rate("ryan", pid, 0.035)
    code = cli.main(
        ["optimize", "--workspace", "ryan", "--portfolio", "long-term", "--universe", "AAA", "BBB"], services
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "for ryan/long-term" in out
    assert "Universe: AAA, BBB, DDD" in out, "held tickers join the universe so keeping them is possible"
    assert "Risk-free rate: 3.50% (stored on the portfolio)" in out


def test_optimize_portfolio_flags_need_each_other(services, capsys):
    assert cli.main(["optimize", "--portfolio", "x"], services) == 1
    assert "--portfolio needs --workspace" in capsys.readouterr().err
    assert cli.main(["optimize", "--workspace", "x"], services) == 1
    assert "--workspace does nothing without --portfolio" in capsys.readouterr().err


def test_optimize_errors_go_to_stderr_or_json(services, capsys):
    assert cli.main(["optimize", "--cash", "1000", "--universe", "FAKE"], services) == 1
    out, err = capsys.readouterr()
    assert out == "" and "Invalid symbol in universe: FAKE" in err

    import json

    args = ["optimize", "--cash", "1000", "--max-positions", "2", "--max-weight", "0.1", "--json"]
    assert cli.main(args, services) == 1
    out, err = capsys.readouterr()
    body = json.loads(out)
    assert "Infeasible" in body["error"]["message"] and body["error"]["payload"]["conflicts"]
    assert err == ""


def test_optimize_holdings_file_accepts_a_brokerage_export_with_cash(services, tmp_path, capsys):
    export = tmp_path / "positions.csv"
    export.write_text("Symbol,Description,Quantity,Last Price\nAAA,Alpha,12,$10\nCASH,Sweep,750,$1\n", encoding="utf-8")
    assert cli.main(["optimize", "--holdings-file", str(export), "--universe", "top20", "--json"], services) == 0
    import json

    body = json.loads(capsys.readouterr().out)
    expected = 750 + 12 * services.market.data.price("AAA")
    assert body["metrics"]["portfolio_value"] == round(expected, 2)

    assert cli.main(["optimize", "--holdings-file", str(tmp_path / "missing.csv")], services) == 1
    assert "Cannot read --holdings-file" in capsys.readouterr().err
    assert cli.main(["optimize", "--holdings", "AAA ten"], services) == 1
    assert "Could not read the holdings" in capsys.readouterr().err


def test_optimize_tbill_rate_and_cash_reserve_show_in_the_report(services, capsys):
    services.market.set_tbill(0.0412, "2026-10-30")
    code = cli.main(
        ["optimize", "--cash", "1000", "--universe", "top20", "--risk-free-rate", "tbill", "--cash-reserve", "0.2"],
        services,
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Risk-free rate: 4.12% (the current 13-week T-bill yield)" in out
    assert "CASH      20.00%" in out and "$200.00" in out
