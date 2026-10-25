# Migration: cloud proof-of-concept → local-first app

This file tracks the migration from the original hosted model (online accounts, passwords, API keys, paid tiers, admin endpoints) to the target model, and lists everything left to get there. Check items off as they land; when the whole list is done, fold anything still useful into the README and delete this file.

## Target

A **locally runnable app** where:

- **Multiple people on the same device** each keep their own portfolios in a passwordless local workspace and optimize them on that machine. Nothing leaves the device; nothing requires a server we run.
- **AI agents can drive it.** With broker MCP servers like the Robinhood MCP, an agent should be able to call `POST http://localhost:8000/api/optimize/`, turn the target allocation into a trade list, and place those trades through the MCP. The local API is the agent's tool surface, so it must be auth-free on localhost, stable, and return machine-readable errors.
- **Python dependencies are installed globally** (no venv), so the optimizer and API work from any directory on the device — for a human at a shell or an agent spawned anywhere. `pip install -e .` makes `import wolvwealth` work globally.

## Done

- [x] Remove the online auth/accounts/API-key/admin system (routes, then the leftover dead modules and templates: `api/auth.py`, `api/admin.py`, `views/account.py`, `views/accounts.py`, `views/login.py`, `views/register.py`, and their templates). Legacy URLs (`/login/`, `/register/`, `/account/`) redirect to the workspace equivalents.
- [x] Local **workspaces** (one per person on the device, no passwords) with named **portfolios** (cash + holdings), stored in gitignored `var/wolvwealth.sqlite3`.
- [x] Browser flow end to end: picker → workspace page with portfolio CRUD → optimize → results page.
- [x] Open JSON API on localhost: `POST /api/optimize/` (by stored portfolio or explicit holdings), `GET /api/workspaces/`. No auth header.
- [x] Per-install secrets and DB auto-created under gitignored `var/` on first run; global-interpreter install documented as the intended setup.
- [x] **Data refresh is scripted and automatic.** `python -m wolvwealth refresh-data [--full]`, plus a startup auto-refresh when prices are more than 7 days old (`WOLVWEALTH_NO_AUTO_REFRESH=1` disables). `yfinance` was unpinned from the dead 0.2.32 to `>=1.7` (verified working); the downloader chunks requests, retries rate-limited tickers, and refuses to persist a mostly-empty result.
- [x] **Works from any directory.** CSV/DB/sql paths all resolve from the package location, so the app, API, and CLI no longer require the repo root as the working directory.
- [x] **Price history out of git.** `historical_prices.csv` is untracked and gitignored; the cache lives in `var/historical_prices.csv`, created on first run by migrating a pre-existing in-repo copy byte-for-byte (no re-download, numbers preserved) or, on a fresh machine, by a full download.
- [x] **Tests.** `tests/` covers the optimizer core, the JSON API contract, and the workspace/portfolio browser flows (54 tests, ~1 s), with fixtures for a temp database and a deterministic 4-ticker synthetic market. Writing them fixed three logical bugs: universe validation let an invalid ticker slip through when it followed an alias like `top20` (mutate-while-iterate); metrics were computed from the untruncated solver output instead of the returned portfolio; and the real solver error was swallowed into a generic "Infeasible". **Policy from here on: every change ships with tests.**
- [x] **Hygiene.** The empty React `/optimizer/` page, the webpack build, and every unused frontend dependency (MUI, Chart.js, Google Charts, react-scripts, ...) are deleted; `package.json` only carries Tailwind. `scripts/backtest.py` now reuses the app's `Optimization` class instead of a stale duplicate, with tests. The templates share one Jinja `base.html` (header, nav, footer, flashes) instead of five copies.
- [x] **Bulk holdings import.** Each portfolio card has an "Import holdings in bulk" panel: paste `TICKER, shares` lines or upload a brokerage positions CSV (header row with Symbol/Ticker + Quantity/Shares columns is auto-detected, extra columns ignored, `$`/thousands separators tolerated, a `CASH` row sets cash). Add/update or replace-all, optional "skip unknown tickers", all-or-nothing with every problem reported at once. The same parser backs `POST /api/holdings/import/` (JSON `holdings` map or `csv` text, `mode`, `skip_unknown`, `create_portfolio`), which is how an agent loads a brokerage export into a workspace before optimizing. Also: `WOLVWEALTH_VAR` overrides the `var/` location, and the test suite seeds its own throwaway price cache so `import wolvwealth` in tests never downloads anything.
- [x] **Clean architecture.** The package is now layered, innermost first: `domain` (optimizer, holdings parser, MarketData snapshot, entities, errors; no Flask/SQL/network) -> `storage` (SQLite repository, price store, secrets) and `market` (Yahoo downloader) -> `services` (use cases + the `Services` container) -> `web` (Flask app factory, API and view blueprints) and the CLI. Each layer imports only the layers inside it, and `tests/test_architecture.py` fails the build if anyone breaks that. `import wolvwealth` has no side effects anymore (`create_app()` builds the app; `wolvwealth serve` preloads prices). The `ApplicationState` singleton, `InvalidUsage`-from-the-maths, and `flask.g`-bound DB helpers are gone. Templates, static files, SQL, and the ticker list moved inside the package so a non-editable install would work. Found and fixed along the way: a holding whose ticker drops out of the price data (delisting) crashed the workspace page. See ARCHITECTURE.md for the layer map and the "adding a bolt-on" recipe.
- [x] **One-command setup.** `install.sh` (run inside a clone, or `curl -fsSL .../install.sh | bash` on a fresh machine) finds Python 3.10+, clones if needed, does the global per-user editable install, puts `~/.local/bin` on PATH, and downloads the price history (`--no-data` to defer; `--dev` adds lint/test tools). A `wolvwealth` console script (`serve`, `refresh-data`, `reset-db`, `dump-db`) replaces `bin/setup`, `bin/run`, and `bin/db`; `wolvwealth serve` binds 127.0.0.1 and opens a browser tab. Node is no longer a runtime dependency: the built Tailwind CSS is committed and rebuilt by contributors with `npm run css`. Decided against a container for now (it would hide `import wolvwealth` from agents behind a port; see ARCHITECTURE §10). Cleanup in the same step: 32 MB of unreferenced images under `static/img`, stale `.gitignore` entries, and the stale React-era `package-lock.json`. Not done: a native Windows installer (PowerShell); Windows users go through WSL.
- [x] **One build system, minimal deps.** `requirements.txt` is deleted; `pyproject.toml` is the single source of truth with four runtime dependencies (Flask, pandas, pyportfolioopt, yfinance — solvers come in transitively) and lint/test tools behind a `dev` extra. `bin/setup` installs globally (`pip install --user --break-system-packages -e ".[dev]"`); the old venv setup, gunicorn, and the sudo-port-80 "prod" mode are gone, and the dev server binds to localhost only.

## Remaining, roughly in order

Priority call: finish the app for **humans running it manually** first; tailoring it for agents comes after that.

1. **Agent-readiness of the API** (deliberately last — on the back burner until the app is done for manual human use).
   - Consider a `trades` block in the optimize response (buy/sell share deltas vs. `initial_holdings`) so an agent can feed the broker MCP directly instead of diffing allocations itself.
   - Document the API contract well enough that it can be dropped into an agent's context (or exposed via a thin MCP wrapper of our own) without reading the code.

## Explicitly out of scope

- Hosted deployment, user accounts, authentication, billing/tiers. If the app is ever network-exposed again, that is a new project, not this migration.
- Live price quotes per request. Prices come from the local cache; freshness is handled by the refresh command.
- Trade execution. WolvWealth produces target allocations (and, later, trade lists). Placing trades is the job of the agent + broker MCP, never of this app.
