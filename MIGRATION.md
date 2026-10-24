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

## Remaining, roughly in order

1. **Data refresh command.** One command (e.g. `python -m wolvwealth refresh-data` or `bin/refresh`) that pulls prices up to today. Currently a manual Python-shell ritual, prices end 2024-01-30, and `yfinance` is pinned to 0.2.32 and likely needs a bump first. Move the 40 MB `historical_prices.csv` out of git: download/build on first run, cache under `var/`.
2. **Remove the working-directory trap.** CSV paths in `api/state.py` are relative, so everything must run from the repo root. Resolve paths from the package/`var/` location so the app and API work from **any** directory — a hard requirement for agent use.
3. **Bulk holdings import** (CSV upload or paste box) so a 30-position portfolio isn't 30 form submissions. This is also the step where an agent could load a brokerage export into a workspace.
4. **Agent-readiness of the API.**
   - Surface real solver errors instead of the generic "Infeasible" (an agent can't recover from that).
   - Compute metrics **after** `max_positions` truncation so the returned numbers describe the returned portfolio.
   - Consider a `trades` block in the optimize response (buy/sell share deltas vs. `initial_holdings`) so an agent can feed the broker MCP directly instead of diffing allocations itself.
   - Document the API contract well enough that it can be dropped into an agent's context (or exposed via a thin MCP wrapper of our own) without reading the code.
5. **Tests** for the optimizer core, workspace/portfolio flows, and the API contract, with a fixture pointing the DB at a temp file.
6. **Hygiene.**
   - Unify `backtest.py` with `Optimization` (it duplicates the maths and can drift).
   - Reconcile `pyproject.toml` dependencies with `requirements.txt`.
   - Delete or build out the empty React `/optimizer/` page; drop the unused frontend deps (MUI, Chart.js, Google Charts, react-scripts) if deleting.
   - A Jinja base template instead of per-page header/footer copies.
7. **One-command setup** on any machine (installer script or container) so a non-developer household member can get it running. Note the tension with the global-install requirement: if we containerize, the API port is still what agents talk to, but "packages work anywhere on the device" argues for a plain installer first.

## Explicitly out of scope

- Hosted deployment, user accounts, authentication, billing/tiers. If the app is ever network-exposed again, that is a new project, not this migration.
- Live price quotes per request. Prices come from the local cache; freshness is handled by the refresh command.
- Trade execution. WolvWealth produces target allocations (and, later, trade lists). Placing trades is the job of the agent + broker MCP, never of this app.
