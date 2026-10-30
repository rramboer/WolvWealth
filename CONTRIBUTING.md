# Contributing to WolvWealth

This is a small project maintained by a few friends. The point of this file is to make sure each of us can clone the repo, get it running on our own machine in a few minutes, and make changes without stepping on each other.

Read [ARCHITECTURE.md](ARCHITECTURE.md) first if you have not looked at the code in a while. It describes how the pieces fit together and lists the known gaps. In particular: **the old login/accounts/API-key system is gone** — the app now uses passwordless local workspaces, and the JSON API needs no auth header. If you are picking up migration work (the move to the fully local, agent-friendly setup), the ordered task list is in [MIGRATION.md](MIGRATION.md).

## Prerequisites

| Tool | Version we have tested | Notes |
|---|---|---|
| Python | 3.12 (3.10+ required) | We use the **global interpreter on purpose** — no venv. The optimizer and API must work from anywhere on the device, including for AI agents spawned in arbitrary directories (see README "Why local-first"). |
| pip | any recent | |
| Node.js + npm | Node 24 / npm 11 | **Only if you change the styling.** The built Tailwind CSS (`wolvwealth/web/static/css/output.css`) is committed, so running the app never needs Node. |
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

On first start the app creates `var/wolvwealth.sqlite3` (from `wolvwealth/storage/sql/`, seeded with a deletable `demo` workspace) and `var/secret_key` (a per-install session-cookie key). Both live in gitignored `var/`, so your workspaces and portfolios stay on your machine and never end up in a commit. `WOLVWEALTH_VAR=/some/dir` relocates `var/` for a run.

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

**Changing the styling** needs Node. The templates link the committed `wolvwealth/web/static/css/output.css`; after editing Tailwind classes, rebuild it and commit the result:

```bash
npm install                 # once
npm run css                 # one-off minified build
npm run css:watch           # rebuild on every template change while you work
```

**Smoke test without a server**, using the Flask test client:

```bash
python3 - <<'PY'
from wolvwealth import create_app
c = create_app().test_client()
r = c.post("/api/optimize/",
           json={"initial_cash": 10000, "universe": ["top20"], "constraints": {"max_positions": 5}})
print(r.status_code, r.get_json())
PY
```

There is no "prod" mode: `wolvwealth serve` binds to 127.0.0.1 and that is the only mode. This app has **no authentication by design**.

## Things that will bite you

### Market data is a Parquet cache in var/

Prices are not fetched live. Share counts in optimizer output and the valuations on the workspace page use the last row of `var/historical_prices.parquet`, read through `storage/price_store.py` and held in memory as a `MarketData` snapshot by `services/market_data.py`. The cache is created on first access: converted from a CSV cache left by an older install if one is still on disk (`var/historical_prices.csv`, or the in-repo `historical_prices.csv` from when the file was committed), otherwise downloaded in full. Alongside it live `var/market_stats.parquet` + `.json`: the optimizer's expected-return and covariance statistics, precomputed once per data refresh and sliced per optimize request (see ARCHITECTURE.md §8); they are rebuilt automatically whenever they do not match the loaded prices. Refreshing is scripted and automatic:

- **Automatic:** `wolvwealth serve` refreshes before binding if the newest cached row is more than `auto_refresh_max_age_days` (7) days old. Failures are non-fatal (it keeps serving cached prices). `WOLVWEALTH_NO_AUTO_REFRESH=1` disables it (offline use, scripts that manage data themselves). `create_app()` itself never refreshes.
- **On demand:** `wolvwealth refresh-data` (incremental append) or `wolvwealth refresh-data --full` (complete 2006-to-today re-download).

Run `--full` occasionally, because the append path never corrects earlier adjusted closes after splits and dividends. Yahoo rate-limits bulk downloads, so `market/yahoo.py` works in chunks with pauses and retries; the incremental path refuses to write the cache if a suspicious fraction of live tickers comes back empty. Expect a full-universe refresh to take several minutes, longer if Yahoo is throttling. To change the universe, edit `wolvwealth/data/ticker_universe.csv` (keep it sorted by market cap, largest first) and run `--full`.

### Importing the package has no side effects; building Services does

`import wolvwealth` only defines things. `Services.build()` wires settings, the database, the price store, and the market-data service without touching disk; the first access to `services.market.data` loads (or downloads) the price cache, and the first `database.open()` creates the SQLite file. Routes live on two blueprints (`web/api`, `web/views`) registered by `create_app()`, so a new module does nothing until it is registered there.

### Holdings tickers must be in the universe

A holding can only be added if its ticker has a column in `var/historical_prices.parquet` (equivalently, a line in `wolvwealth/data/ticker_universe.csv`). If your real portfolio has something exotic, add the ticker to the universe and refresh prices first.

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
- Rule violations raise `ValidationError` / `NotFoundError` from `wolvwealth/domain/errors.py`; they carry a message and optional `details`, never an HTTP status. The API blueprint maps them to 400/404 JSON; views catch them and `flask.flash(...)` the message, then redirect back to the page.
- All SQL lives in `wolvwealth/storage/repository.py` (parameterised, every portfolio query scoped by workspace, keep it that way). Views and the API get a repository from `web/context.py`, on a per-request connection committed at teardown; the CLI and tests use `with services.database.connect() as conn:`.
- **The dependency rule is enforced by `tests/test_architecture.py`:** `domain` imports nothing from the rest of the package; `storage`/`market` import only `domain`; `services` imports those three; `web` and the CLI import anything. If you need to add a new top-level package, add it to the layer map in that test.
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
| Add or change an API endpoint | `wolvwealth/web/api/__init__.py` (route) calling a function in `wolvwealth/services/` |
| Add or change a web page | `wolvwealth/web/views/<page>.py` + `wolvwealth/web/templates/<page>.html` (extend `base.html`); import the module in `web/views/__init__.py` |
| Change workspace/portfolio storage | `wolvwealth/storage/repository.py` (SQL), `wolvwealth/domain/models.py` (entities), `wolvwealth/storage/sql/` (schema), then `wolvwealth reset-db` |
| Change the optimizer maths | `wolvwealth/domain/optimizer.py` — plain dict in, plain dict out, only reads a `MarketData`. `scripts/backtest.py` reuses it |
| Change the browser optimize flow | `wolvwealth/web/views/optimizer.py` + `templates/results.html`, forms live in `templates/workspace.html` |
| Change where files live or a tunable | `wolvwealth/config.py` (`Settings`) |
| Add a bolt-on feature | A module in `wolvwealth/services/` for the use case, a blueprint or CLI command to expose it, tests. See ARCHITECTURE.md "Adding a bolt-on" |
| Add a CLI command | `wolvwealth/__main__.py` (argparse subcommand + `cmd_*` function), tests in `tests/test_cli.py` |
| Change the installer | `install.sh`; test it with a sandbox `HOME` so your real user site and shell rc are untouched |

## Security notes

- This app has **no authentication on purpose**. Workspaces are an organizational boundary, not a security one: anyone who can reach the app can open, modify, or delete any workspace. That is the correct trade-off for a single household computer on localhost — and the reason you must never bind it to an untrusted network.
- The session secret is generated per install into `var/secret_key` and the database lives in `var/`; both are gitignored. Never commit anything from `var/`, and never commit a real holdings dump in a test or fixture if you wouldn't want it public.

## Questions

Open an issue or ask in the group chat. If you figure out something non-obvious about the codebase, add it to ARCHITECTURE.md or this file so the next person does not have to rediscover it.
