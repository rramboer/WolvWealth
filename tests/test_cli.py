"""Tests for the `wolvwealth` command line (serve, optimize, refresh-data, reset-db, dump-db)."""

import dataclasses
import json
import re

import pytest

import wolvwealth.__main__ as cli
from conftest import RECENT_DAY, dated_to_today, synthetic_market


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
    assert parser.parse_args(["serve", "--port", "65535"]).port == 65535
    for port in ("0", "-1", "65536", "http"):
        with pytest.raises(SystemExit):
            parser.parse_args(["serve", "--port", port])


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

    # Under --dev, Werkzeug's reloader runs the command a second time in a
    # child process: that one must not load the cache, refresh, or open a tab.
    monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")
    services.settings = dataclasses.replace(services.settings, auto_refresh=True)
    monkeypatch.setattr(services.market, "start_background_refresh", lambda _days: pytest.fail("second refresh"))
    assert cli.main(["serve", "--dev"], services) == 0 and opened == []


def test_reset_db_erases_workspaces_and_restores_demo(workspace_client, services, capsys):
    assert cli.main(["reset-db"], services) == 0
    names = [w["name"] for w in workspace_client.get("/api/workspaces/").get_json()["workspaces"]]
    assert names == ["demo"]
    assert "All workspaces erased" in capsys.readouterr().out
    assert workspace_client.get("/workspace/").status_code == 302, "the session's workspace is gone: dropped"


def test_dump_db_prints_tables(workspace_client, services, capsys):
    assert cli.main(["dump-db"], services) == 0
    out = capsys.readouterr().out
    assert "== workspaces ==" in out and "tester" in out and "'AAA'" in out


def test_refresh_data_reports_up_to_date(services, capsys):
    services.market.downloader = lambda *a, **k: pytest.fail("must not download when nothing is missing")
    # The newest cached row is today, so the incremental path has nothing to fetch.
    services.market.set_data(dated_to_today(services.market.data))
    services.market.tbill_downloader = lambda: (0.0412, RECENT_DAY)
    assert cli.main(["refresh-data"], services) == 0
    out = capsys.readouterr().out
    assert "Prices already up to date" in out
    assert f"T-bill yield: 4.12% as of {RECENT_DAY}" in out
    assert services.market.tbill == {"rate": 0.0412, "as_of": RECENT_DAY} == services.price_store.load_tbill()


def test_refresh_data_full_re_downloads_everything_and_a_failed_download_is_a_message(services, capsys):
    spiked = synthetic_market().prices
    spiked.iloc[100, 3] *= 40
    services.market.downloader = lambda tickers, _start, **_k: spiked.loc[:, list(tickers)]
    assert cli.main(["refresh-data", "--full"], services) == 0
    out = capsys.readouterr().out
    assert "Full re-download, 2006 -> today" in out and "Done. Prices now end 2021-" in out
    assert "T-bill" not in out, "no T-bill line until one has been fetched"
    assert "Quarantined DDD (excluded from allocations since " in out and "x in one day" in out
    saved = services.price_store.load_prices()

    def offline(*_args, **_kwargs):
        raise ConnectionError("Name or service not known")

    for broken, said in [(offline, "Name or service not known"), (lambda *_a, **_k: None, "returned NoneType")]:
        services.market.downloader = broken
        assert cli.main(["refresh-data", "--full"], services) == 1
        err = capsys.readouterr().err
        assert "price refresh failed (" in err and said in err
    assert services.price_store.load_prices().equals(saved), "the cache is untouched"


# --- optimize ---------------------------------------------------------------


def test_optimize_parser_maps_every_api_field():
    args = cli.build_parser().parse_args(
        [
            "optimize",
            "--workspace", "ryan", "--portfolio", "long-term",
            "--cash", "100", "--holdings", "AAA=10",
            "--universe", "top20", "BBB", "--min-history-days", "60",
            "--max-positions", "3", "--max-weight", "0.5", "--min-universal-weight", "0.01",
            "--max-turnover", "0.2", "--cash-reserve", "0.1",
            "--risk-free-rate", "tbill", "--json",
        ]
    )  # fmt: skip
    assert args.command == "optimize" and args.json and args.risk_free_rate == "tbill"
    assert args.universe == ["top20", "BBB"] and args.max_positions == 3 and args.cash_reserve == 0.1
    assert args.min_history_days == 60 and cli._optimize_payload(args)["min_history_days"] == 60
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
    services.market.set_data(dated_to_today(services.market.data))
    assert cli.main(["optimize", "--cash", "1000"], services) == 0
    assert capsys.readouterr().err == "", "fresh prices: no staleness warning"


def test_optimize_json_is_the_api_response_body(services, client, capsys):
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
    assert cli.main(["optimize", "--cash", "inf"], services) == 1  # argparse's float() accepts it
    assert "initial_cash must be a number" in capsys.readouterr().err

    args = ["optimize", "--cash", "1000", "--max-positions", "2", "--max-weight", "0.1", "--json"]
    assert cli.main(args, services) == 1
    out, err = capsys.readouterr()
    body = json.loads(out)
    assert "Infeasible" in body["error"]["message"] and body["error"]["payload"]["conflicts"]
    assert err == ""
    assert cli.main(["optimize", "--cash", "1000", "--universe", "FAKE", "--json"], services) == 1
    body = json.loads(capsys.readouterr().out)
    assert set(body["error"]) == {"message"}, "no payload unless the error carries one"


def test_optimize_holdings_file_accepts_a_brokerage_export_with_cash(services, tmp_path, capsys):
    export = tmp_path / "positions.csv"
    export.write_text("Symbol,Description,Quantity,Last Price\nAAA,Alpha,12,$10\nCASH,Sweep,750,$1\n", encoding="utf-8")
    assert cli.main(["optimize", "--holdings-file", str(export), "--universe", "top20", "--json"], services) == 0
    body = json.loads(capsys.readouterr().out)
    expected = 750 + 12 * services.market.data.price("AAA")
    assert body["metrics"]["portfolio_value"] == round(expected, 2)

    assert cli.main(["optimize", "--holdings-file", str(tmp_path / "missing.csv")], services) == 1
    assert "Cannot read --holdings-file" in capsys.readouterr().err
    assert cli.main(["optimize", "--holdings", "AAA=1", "--holdings-file", str(export)], services) == 1
    assert "not both" in capsys.readouterr().err
    binary = tmp_path / "positions.xlsx"
    binary.write_bytes(b"PK\x03\x04\xff\xfe")
    assert cli.main(["optimize", "--holdings-file", str(binary)], services) == 1
    assert "not UTF-8 text" in capsys.readouterr().err
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
    cash_line = next(line for line in out.splitlines() if line.strip().startswith("CASH"))
    # Solver dust can leave the cash line a hair off the exact reserve, either way.
    assert re.search(r"CASH\s+(19\.9|20\.0)\d%.*\$(199|200)\.\d\d", cash_line)
