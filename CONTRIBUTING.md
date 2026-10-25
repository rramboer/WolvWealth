# Contributing to WolvWealth

This is a small project maintained by a few friends. The point of this file is to make sure each of us can clone the repo, get it running on our own machine in a few minutes, and make changes without stepping on each other.

Read [ARCHITECTURE.md](ARCHITECTURE.md) first if you have not looked at the code in a while. It describes how the pieces fit together and lists the known gaps. In particular: **the old login/accounts/API-key system is gone** — the app now uses passwordless local workspaces, and the JSON API needs no auth header. If you are picking up migration work (the move to the fully local, agent-friendly setup), the ordered task list is in [MIGRATION.md](MIGRATION.md).

## Prerequisites

| Tool | Version we have tested | Notes |
|---|---|---|
| Python | 3.12 (3.10+ required) | We use the **global interpreter on purpose** — no venv. The optimizer and API must work from anywhere on the device, including for AI agents spawned in arbitrary directories (see README "Why local-first"). |
| pip | any recent | |
| Node.js + npm | Node 24 / npm 11 | **Only if you change the styling.** The built Tailwind CSS (`wolvwealth/static/css/output.css`) is committed, so running the app never needs Node. |
| git | any | |

The `sqlite3` CLI is **not** required: the app creates its own database on first run, and `wolvwealth reset-db` / `dump-db` use Python's built-in sqlite3 module.

## First-time setup

```bash
git clone git@github.com:rramboer/WolvWealth.git
cd WolvWealth
./install.sh --dev          # --dev adds the lint/test tools; --no-data skips the price download
```

`install.sh` is the same installer a non-developer runs (`curl ... | bash` in the README). It runs `pip install --user --break-system-packages -e ".[dev]"` (an editable install into the global interpreter), makes sure `~/.local/bin` is on your PATH so the `wolvwealth` command resolves, and downloads the price history. Dependencies are declared in `pyproject.toml` and deliberately minimal — just Flask, pandas, pyportfolioopt, and yfinance at runtime — because this gets installed globally on other people's machines.

Because the install is editable and data-file paths are resolved from the install location, `import wolvwealth`, the Flask app, and `wolvwealth <command>` all work from **any directory**. The clone must stay where it is (it *is* the install).

On first start the app creates `var/wolvwealth.sqlite3` (from `sql/schema.sql` + `sql/data.sql`, seeded with a deletable `demo` workspace) and `var/secret_key` (a per-install session-cookie key). Both live in gitignored `var/`, so your workspaces and portfolios stay on your machine and never end up in a commit. `WOLVWEALTH_VAR=/some/dir` relocates `var/` for a run.

To wipe all local workspaces and start over:

```bash
wolvwealth reset-db
```

## Running it

```bash
wolvwealth serve --dev      # Flask debug server with auto-reload, http://localhost:8000, opens a browser tab
wolvwealth serve            # what a household member runs; --no-browser and --port N also exist
```

`flask --app wolvwealth --debug run --port 8000` still works if you prefer the Flask CLI.

**Changing the styling** needs Node. The templates link the committed `wolvwealth/static/css/output.css`; after editing Tailwind classes, rebuild it and commit the result:

```bash
npm install                 # once
npm run css                 # one-off minified build
npm run css:watch           # rebuild on every template change while you work
```

**Smoke test without a server**, using the Flask test client:

```bash
python3 - <<'EOF'
import wolvwealth
c = wolvwealth.app.test_client()
r = c.post("/api/optimize/",
           json={"initial_cash": 10000, "universe": ["top20"], "constraints": {"max_positions": 5}})
print(r.status_code, r.get_json())
EOF
```

There is no "prod" mode: `wolvwealth serve` binds to 127.0.0.1 and that is the only mode. This app has **no authentication by design**.

## Things that will bite you

### Market data is a 40 MB CSV in var/

Prices are not fetched live. Share counts in optimizer output and the valuations on the workspace page use the last row of `var/historical_prices.csv`. The cache is created on first run: migrated from a pre-existing in-repo `historical_prices.csv` if one is still on disk (the file used to be committed), otherwise downloaded in full. Refreshing is automatic and scripted:

- **Automatic:** on startup, if the newest cached row is more than `AUTO_REFRESH_MAX_AGE_DAYS` (7) days old, the app appends the missing days. Failures are non-fatal (it keeps serving cached prices). Set `WOLVWEALTH_NO_AUTO_REFRESH=1` to disable (offline use, tests, scripts that manage data themselves).
- **On demand:** `python -m wolvwealth refresh-data` (incremental append) or `python -m wolvwealth refresh-data --full` (complete 2006-to-today re-download).

Run `--full` occasionally, because the append path never corrects earlier adjusted closes after splits and dividends. Yahoo rate-limits bulk downloads, so the downloader works in chunks with pauses and retries; the incremental path refuses to write the CSV if a suspicious fraction of live tickers comes back empty. Expect a full-universe refresh to take several minutes, longer if Yahoo is throttling — if it keeps failing, wait an hour and try again.

Price data is never committed anymore: the cache lives in gitignored `var/`, so each machine maintains its own. (The old `Update tickers and prices` data commits are history.)

To add or remove tickers:

```python
from wolvwealth.api.state import ApplicationState
s = ApplicationState()
s.add_tickers_to_universe(["PLTR", "ARM"])   # slow: live market-cap lookups, then full price re-download
s.remove_tickers_from_universe(["SAVE"])
```

### Importing the package has side effects

`import wolvwealth` creates the Flask app, registers every route, creates the database if missing, loads ~70 MB of prices into memory, and **may hit the network** (the startup auto-refresh when prices are stale — set `WOLVWEALTH_NO_AUTO_REFRESH=1` in tests and scripts). Keep that in mind. Routes are registered by decorators, so a new view or API module does nothing until it is imported from `wolvwealth/views/__init__.py` or `wolvwealth/api/__init__.py`.

### Holdings tickers must be in the universe

A holding can only be added if its ticker has a column in `historical_prices.csv` (equivalently, a line in `ticker_universe.csv`). If your real portfolio has something exotic, add the ticker to the universe and refresh prices first.

## Making changes

### Branches and commits

- Branch off `main` for anything beyond a one-line fix. Name it after what it does, for example `fix/metrics-truncation` or `feat/holdings-import`.
- Small, focused commits with an imperative subject line, as in the existing history: `Fix account page font size`, `Add admin commands`.
- Open a pull request against `main` and get one other person to look at it. For a three-person project, "looks good" in the PR is enough.
- Do not force-push `main`.

### Code style

Settings live in `pyproject.toml`. The tools come with the `dev` extra (`./install.sh --dev`, or `pip install --user --break-system-packages -e ".[dev]"`).

```bash
black --line-length 120 wolvwealth scripts/backtest.py
pycodestyle --max-line-length 120 wolvwealth scripts tests
pydocstyle wolvwealth
pylint wolvwealth
```

Conventions the existing code follows and new code should too:

- Every module and function has a docstring (pydocstyle is part of the toolchain).
- API input validation raises `InvalidUsage("...")` from `wolvwealth/api/api_exceptions.py`, which becomes a JSON error response. HTML form handlers report problems with `flask.flash(...)` and redirect back to the page.
- Database access goes through `wolvwealth.model.get_db()` with parameterised queries, and workspace/portfolio reads and writes go through the helpers in `wolvwealth/portfolios.py` — every portfolio query there is scoped by workspace, keep it that way. The connection is committed and closed at the end of the request automatically.
- Front-end styling uses the Tailwind palette defined in `tailwind.config.js`. Do not add ad-hoc hex colours in templates.

### Tests

```bash
pytest tests/ -q
```

The suite covers the optimizer core, the JSON API contract, and the workspace/portfolio browser flows. The fixtures in `tests/conftest.py` give you:

- `client` — a Flask test client backed by a **temp database** (your real `var/wolvwealth.sqlite3` is never touched).
- `market` — swaps the global price data for 4 synthetic tickers (`AAA`–`DDD`) with a fixed random seed, so optimizer assertions are deterministic and fast.
- `workspace_client` — `client` with a `tester` workspace open and one seeded portfolio.

`conftest.py` sets `WOLVWEALTH_NO_AUTO_REFRESH=1` and points `WOLVWEALTH_VAR` at a throwaway folder seeded with a two-row price CSV before importing the app, so tests never hit the network even on a machine with no real price cache yet. (`WOLVWEALTH_VAR` is also handy for scratch runs of the app that should not touch your real `var/`.)

**Every change ships with tests.** Bug fix → a regression test that fails without the fix. New feature → tests for its happy path and its validation errors. The suite runs in about a second; there is no excuse to skip it.

### Where things go

| I want to... | Touch |
|---|---|
| Add or change an API endpoint | `wolvwealth/api/<module>.py`, import it in `wolvwealth/api/__init__.py` |
| Add or change a web page | `wolvwealth/views/<page>.py` + `wolvwealth/templates/<page>.html` (extend `base.html`), import in `wolvwealth/views/__init__.py` |
| Change workspace/portfolio storage | `wolvwealth/portfolios.py` (helpers), `sql/schema.sql` + `sql/data.sql` (schema), then `wolvwealth reset-db` |
| Change the optimizer maths | `Optimization` in `wolvwealth/api/optimize.py` — framework-free, plain dict in, plain dict out. `scripts/backtest.py` reuses it |
| Change the browser optimize flow | `wolvwealth/views/optimizer.py` + `templates/results.html`, forms live in `templates/workspace.html` |
| Change global config | `wolvwealth/config.py` |
| Add a CLI command | `wolvwealth/__main__.py` (argparse subcommand + `cmd_*` function), tests in `tests/test_cli.py` |
| Change the installer | `install.sh`; test it with a sandbox `HOME` so your real user site and shell rc are untouched |

## Security notes

- This app has **no authentication on purpose**. Workspaces are an organizational boundary, not a security one: anyone who can reach the app can open, modify, or delete any workspace. That is the correct trade-off for a single household computer on localhost — and the reason you must never bind it to an untrusted network.
- The session secret is generated per install into `var/secret_key` and the database lives in `var/`; both are gitignored. Never commit anything from `var/`, and never commit a real holdings dump in a test or fixture if you wouldn't want it public.

## Questions

Open an issue or ask in the group chat. If you figure out something non-obvious about the codebase, add it to ARCHITECTURE.md or this file so the next person does not have to rediscover it.
