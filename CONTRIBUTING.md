# Contributing to WolvWealth

This is a small project maintained by a few friends. The point of this file is to make sure each of us can clone the repo, get it running on our own machine in a few minutes, and make changes without stepping on each other.

Read the "How it is built" section of [README.md](README.md) first if you have not looked at the code in a while, and [AGENTS.md](AGENTS.md) for the rules and invariants that changes must respect (it is written for AI agents, but every rule in it applies to humans too).

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
wolvwealth optimize --cash 5000 --universe top50      # one optimization in the terminal, no server; --json for the API body
wolvwealth mcp --config                               # the MCP configuration for an AI app; `wolvwealth mcp` itself is what the app runs
```

To poke at the MCP server by hand: run `wolvwealth mcp`, paste `{"jsonrpc":"2.0","id":1,"method":"tools/list"}` and press Enter; every tool and its schema comes back on one line. Ctrl-D ends the session. `claude mcp add wolvwealth -- $(which python3) -m wolvwealth mcp` wires it into Claude Code for an end-to-end try.

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

Prices are not fetched live. Share counts and the valuations on the workspace page use the
last row of `var/historical_prices.parquet`, held in memory as a `MarketData` snapshot
(README "How it is built" covers the caches and the precomputed statistics). What matters
when you are changing code:

- `wolvwealth serve` refreshes in the background when the cache is more than
  `auto_refresh_max_age_days` (7) days old, so startup never blocks on it, and failures are
  non-fatal. `WOLVWEALTH_NO_AUTO_REFRESH=1` disables it. `create_app()` itself never refreshes.
- `wolvwealth refresh-data` appends, detecting and repairing tickers whose adjusted history
  changed (splits, dividends) along the way; `--full` re-downloads everything, which is
  mainly for recovering tickers an earlier download lost to throttling. Expect several
  minutes, longer when Yahoo throttles.
- To refresh the universe against today's listings, run `python scripts/build_universe.py`
  (use `--dry-run` first) and then `wolvwealth refresh-data --full` so the new tickers get
  price history; until then they are simply untradeable. The script adds and re-ranks but
  never removes. To hand-edit instead, keep `wolvwealth/data/ticker_universe.csv` sorted by
  market cap, largest first — the file order is the `top500` ranking. Tickers that stop
  trading are handled automatically. Never prune the file because tickers came back empty:
  Yahoo returns the same nothing for a delisted company as for a throttled request, so
  pruning deletes live companies.

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
black --line-length 120 wolvwealth scripts tests
pycodestyle wolvwealth scripts tests
pydocstyle wolvwealth
pylint wolvwealth scripts
```

GitHub Actions runs exactly these four commands plus the test suite on every push and pull request (`.github/workflows/ci.yml`), across Python 3.10-3.13 on Linux and 3.12 on macOS. On Linux the tests run with networking switched off. Another job runs `./install.sh` on every supported Python, since the installer runs on whatever interpreter the user has. A last job builds the wheel, installs it into a clean virtualenv and smoke-tests it, which is how a packaging mistake gets caught before someone else hits it.

Every number that arrives from outside (JSON, a form field, a CLI flag, a CSV cell, a database row) goes through `wolvwealth/domain/numbers.py`: `is_number` for anything numeric, `is_amount` for cash and share counts. A plain `isinstance(value, float)` lets `NaN` and infinity through, and both JSON and `float()` produce them. Data read from disk that turns out unusable raises `DataError` with a message that says how to recover.

Everything downloaded is untrusted, because the app often runs unattended. Prices enter only through `MarketDataService._fetch` (cleaned, unrequested tickers and future rows dropped) and are merged through `_quarantine` (implausible moves excluded and reported in `market_status`). A downloaded T-bill quote must pass `_check_tbill`. `scripts/build_universe.py` caps response sizes and accepts only ticker-shaped symbols and believable sizes. Add a new download source the same way: validate before anything is stored, and make anything rejected visible in a report an agent reads.

New tests go into the file and table that already own the behavior: add a row to an existing `parametrize` list before writing a new test, and merge tests that share their setup.

Tests must never touch the network. `tests/conftest.py` fails any test that calls yfinance or opens a non-loopback connection. If you need prices, use the `services`/`market` fixtures (synthetic data) or inject a fake `downloader`.

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

`conftest.py` builds `Services` against a `tmp_path` with `auto_refresh=False` and preloads a deterministic four-ticker synthetic market, so tests never touch your real `var/` and never hit the network — even on a machine with no price cache yet. (`WOLVWEALTH_VAR` points the app itself at a throwaway folder, which is handy for scratch runs.)

**Every change ships with tests.** Bug fix → a regression test that fails without the fix. New feature → tests for its happy path and its validation errors. The suite runs in about a second; there is no excuse to skip it.

### Where things go

| I want to... | Touch |
|---|---|
| Add or change an API endpoint | `wolvwealth/web/api/__init__.py` (route) calling a function in `wolvwealth/services/` |
| Add or change a web page | `wolvwealth/web/views/<page>.py` + `wolvwealth/web/templates/<page>.html` (extend `base.html`); import the module in `web/views/__init__.py` |
| Change workspace/portfolio storage | `wolvwealth/storage/repository.py` (SQL), `wolvwealth/domain/models.py` (entities), `wolvwealth/storage/sql/` (schema), then `wolvwealth reset-db` |
| Change the optimizer maths | `wolvwealth/domain/optimizer.py` — plain dict in, plain dict out, only reads a `MarketData`. `scripts/backtest.py` reuses it |
| Change the expected-return estimator | `wolvwealth/domain/stats.py`: add it to `ESTIMATORS` (it must be per-ticker separable; `tests/test_stats.py` checks), run `scripts/backtest.py --estimator NAME` against the current one, and only then point `ESTIMATOR` at it. Update the table below |
| Change the browser optimize flow | `wolvwealth/web/views/optimizer.py` + `templates/results.html`, forms live in `templates/workspace.html` |
| Change where files live or a tunable | `wolvwealth/config.py` (`Settings`) |
| Add a bolt-on feature | A module in `wolvwealth/services/` for the use case, a blueprint or CLI command to expose it, tests. See AGENTS.md "Where things go" |
| Add a CLI command | `wolvwealth/__main__.py` (argparse subcommand + `cmd_*` function), tests in `tests/test_cli.py` |
| Add or change an MCP tool | `wolvwealth/mcp/tools.py` (a `Tool`: name, description the model reads, JSON schema, handler calling a service), tests in `tests/test_mcp.py`, the tool table in `MCP.md` |
| Change the installer | `install.sh`; test it with a sandbox `HOME` so your real user site and shell rc are untouched |

## The backtest and the expected-return estimator

`python scripts/backtest.py [--estimator NAME] [--start YYYY-MM-DD] [--frequencies quarterly,monthly] [--verbose]` walks the optimizer forward over the cached prices: at each rebalance it sees only the data up to that date, holds the result until the next one, and values the holdings daily. It prints realized Sharpe, CAGR, volatility, max drawdown and mean turnover per rebalance for the max-Sharpe strategy and for an equal-weight (1/N) portfolio over the same names. The universe at each date is the 50 largest names with at least a year of history (a company listed last week has no statistics worth optimizing on), capped at 10 positions. The output is bracketed by a survivorship warning for good reason: the cache holds only companies still listed today, ranked by today's market cap, and the walk charges no commissions, spreads or taxes. Compare strategies against each other, never against reality.

That is how the estimator in `domain/stats.py` was chosen (November 2026, prices 2006 to 2026-10-29, four start dates: 2012, 2016, 2019, 2022; quarterly and monthly rebalancing, eight windows in all):

| Expected-return estimator | Mean realized Sharpe | Windows beating 1/N | Turnover per quarterly rebalance |
|---|---|---|---|
| `mean_historical` (CAGR over the whole history; the original) | 1.02 | 0 of 8 | 23 to 25% |
| `mean_5y` (CAGR over the last five years) | 1.22 | 1 of 8 | not measured |
| `ema_1000` (exponentially weighted, 1000-day span) | 1.22 | 0 of 8 | not measured |
| **`ema_500`** (500-day span; **chosen**) | **1.39** | **5 of 8** | 40 to 45% |
| `ema_250` (250-day span) | 1.46 | 5 of 8 | 49 to 54% |
| equal weight, for reference | 1.35 | | 6% |

The whole-history mean came last in every one of the eight windows, and realized Sharpe rose monotonically with recency weighting. `ema_500` beat it everywhere, by 0.26 to 0.69, and cleared the 1/N hurdle more often than not. `ema_250` scored a little higher but sold about a quarter more of the portfolio at each rebalance, in a walk that charges nothing for trading, and the two are within noise of each other on windows that all end on the same day. 500 days is also PyPortfolioOpt's own default span, so it is a conventional choice rather than a fitted one. The max-Sharpe portfolios under every estimator turn over far more than 1/N does; `constraints.max_turnover` exists for that.

What this does not show: how any of it would have done including the companies that failed along the way, or in a decade where recent winners stopped winning. Treat it as evidence that the whole-history mean was the wrong default, not as a forecast.

## Security notes

- This app has **no authentication on purpose**. Workspaces are an organizational boundary, not a security one: anyone who can reach the app can open, modify, or delete any workspace. That is the correct trade-off for a single household computer on localhost — and the reason you must never bind it to an untrusted network.
- **Localhost is reachable from the browser, so cross-site requests are refused** (`web/security.py`). Any web page can *send* requests to `http://localhost:8000` even though it cannot read the answers, and a DNS-rebound domain could read them too. Every request is checked: the Host header must name this machine, and requests a browser marks cross-site (`Sec-Fetch-Site`, `Origin`) get a 403 — except plain link-click navigations, which are harmless GETs. Agents, curl and the app's own pages send nothing that trips this. If you add a route, it is covered automatically; if you add a GET that mutates state, you have broken the model — mutations must be POSTs.
- The session secret is generated per install into `var/secret_key` and the database lives in `var/`; both are gitignored (a historical copy of each was tracked until late 2026 — the key only signs the workspace-name cookie on localhost, so it was removed rather than rotated out of history). Never commit anything from `var/`, and never commit a real holdings dump in a test or fixture if you wouldn't want it public.

## Questions

Open an issue or ask in the group chat. If you figure out something non-obvious about the codebase, add it to AGENTS.md or this file so the next person does not have to rediscover it.
