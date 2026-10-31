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
