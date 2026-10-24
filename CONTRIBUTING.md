# Contributing to WolvWealth

This is a small project maintained by a few friends. The point of this file is to make sure each of us can clone the repo, get it running on our own machine in a few minutes, and make changes without stepping on each other.

Read [ARCHITECTURE.md](ARCHITECTURE.md) first if you have not looked at the code in a while. It describes how the pieces fit together and lists the known gaps. In particular: **the old login/accounts/API-key system is gone** — the app now uses passwordless local workspaces, and the JSON API needs no auth header.

## Prerequisites

| Tool | Version we have tested | Notes |
|---|---|---|
| Python | 3.12 (3.10+ required) | We are using the **global interpreter** for now. No venv, no containers yet. That will change later. |
| pip | any recent | |
| Node.js + npm | Node 24 / npm 11 | Only needed for the website CSS and the React bundle. The JSON API works without Node. |
| git | any | |

The `sqlite3` CLI is **not** required: `bin/db` uses Python's built-in sqlite3 module, and the app creates its own database on first run.

## First-time setup

Run everything from the repository root. The app reads `ticker_universe.csv` and `historical_prices.csv` by relative path and will fail to start from anywhere else.

```bash
git clone <repo-url> WolvWealth
cd WolvWealth

# Python dependencies into the global interpreter (pinned versions)
pip install -r requirements.txt

# Optional: makes `import wolvwealth` work from any directory and lets pytest find the package
pip install -e .

# Node dependencies (Tailwind, webpack, Babel, React). Skip if you only need the API.
npm install
```

That's it. On first start the app creates `var/wolvwealth.sqlite3` (from `sql/schema.sql` + `sql/data.sql`, seeded with a deletable `demo` workspace) and `var/secret_key` (a per-install session-cookie key). Both live in gitignored `var/`, so your workspaces and portfolios stay on your machine and never end up in a commit.

To wipe all local workspaces and start over:

```bash
./bin/db reset
```

## Running it

**Full dev mode** (website + API, with CSS and JS rebuilt on change):

```bash
./bin/run dev
# http://localhost:8000
```

This starts a Tailwind watcher and a webpack watcher in the background and then the Flask debug server in the foreground. Ctrl-C stops Flask; the two `npx` watchers may keep running and need to be killed separately (`pkill -f tailwindcss; pkill -f webpack`).

**API only** (no Node needed):

```bash
flask --app wolvwealth --debug run --port 8000
```

The HTML pages will render without styling in this mode unless `wolvwealth/static/css/output.css` was built at some point.

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

Do not use `./bin/run prod`. It runs the Flask debug server as root on port 80, and this app has **no authentication by design** — it is meant for localhost only.

## Things that will bite you

### Market data is a 40 MB CSV ending 2024-01-30

Prices are not fetched live. Share counts in optimizer output and the valuations on the workspace page use the last row of `historical_prices.csv`. To refresh (this hits Yahoo Finance through `yfinance`, which is pinned to an old version and may need upgrading first):

```bash
python3 - <<'EOF'
from wolvwealth.api.state import ApplicationState
s = ApplicationState()
s.save_historical_prices()     # full re-download 2006 -> today, rewrites historical_prices.csv
s.load_historical_prices()
print(s.HISTORICAL_PRICES.index[-1], s.HISTORICAL_PRICES.shape)
EOF
```

Prefer `save_historical_prices()` (full refresh) over `update_historical_prices()` (append only) before committing, because the append path never corrects earlier adjusted closes after splits and dividends.

Keep data refreshes in their own commit titled `Update tickers and prices`, with no code changes mixed in. The diff is huge and nobody wants to review code next to it.

To add or remove tickers:

```python
from wolvwealth.api.state import ApplicationState
s = ApplicationState()
s.add_tickers_to_universe(["PLTR", "ARM"])   # slow: live market-cap lookups, then full price re-download
s.remove_tickers_from_universe(["SAVE"])
```

### Everything is relative to the repo root

Run Python, Flask, and the scripts from the repository root. If you see `FileNotFoundError: ticker_universe.csv`, that is why.

### Importing the package has side effects

`import wolvwealth` creates the Flask app, registers every route, creates the database if missing, and loads ~70 MB of prices into memory. Keep that in mind in tests and scripts. Routes are registered by decorators, so a new view or API module does nothing until it is imported from `wolvwealth/views/__init__.py` or `wolvwealth/api/__init__.py`.

### Holdings tickers must be in the universe

A holding can only be added if its ticker has a column in `historical_prices.csv` (equivalently, a line in `ticker_universe.csv`). If your real portfolio has something exotic, add the ticker to the universe and refresh prices first.

## Making changes

### Branches and commits

- Branch off `main` for anything beyond a one-line fix. Name it after what it does, for example `fix/metrics-truncation` or `feat/holdings-import`.
- Small, focused commits with an imperative subject line, as in the existing history: `Fix account page font size`, `Add admin commands`.
- Open a pull request against `main` and get one other person to look at it. For a three-person project, "looks good" in the PR is enough.
- Do not force-push `main`.

### Code style

Settings live in `pyproject.toml`. The tools are already in `requirements.txt`.

```bash
black --line-length 120 wolvwealth backtest.py
pycodestyle --max-line-length 120 wolvwealth
pydocstyle wolvwealth
pylint wolvwealth
```

Conventions the existing code follows and new code should too:

- Every module and function has a docstring (pydocstyle is part of the toolchain).
- API input validation raises `InvalidUsage("...")` from `wolvwealth/api/api_exceptions.py`, which becomes a JSON error response. HTML form handlers report problems with `flask.flash(...)` and redirect back to the page.
- Database access goes through `wolvwealth.model.get_db()` with parameterised queries, and workspace/portfolio reads and writes go through the helpers in `wolvwealth/portfolios.py` — every portfolio query there is scoped by workspace, keep it that way. The connection is committed and closed at the end of the request automatically.
- Front-end styling uses the Tailwind palette defined in `tailwind.config.js`. Do not add ad-hoc hex colours in templates.

### Tests

There are none yet. `pytest` and `pytest-mock` are installed and `pip install -e .` makes the package importable. When you add the first ones, put them in `tests/` and use the Flask test client:

```python
import wolvwealth

def test_workspace_required():
    client = wolvwealth.app.test_client()
    assert client.get("/workspace/").status_code == 302  # redirects to the picker
```

Tests that create or mutate workspaces touch your local `var/wolvwealth.sqlite3`. Until there is a fixture that points `DATABASE_FILENAME` at a temp file, clean up after yourself (`./bin/db reset` restores the seed state) or test against throwaway workspace names.

### Where things go

| I want to... | Touch |
|---|---|
| Add or change an API endpoint | `wolvwealth/api/<module>.py`, import it in `wolvwealth/api/__init__.py` |
| Add or change a web page | `wolvwealth/views/<page>.py` + `wolvwealth/templates/<page>.html`, import in `wolvwealth/views/__init__.py` |
| Change workspace/portfolio storage | `wolvwealth/portfolios.py` (helpers), `sql/schema.sql` + `sql/data.sql` (schema), then `./bin/db reset` |
| Change the optimizer maths | `Optimization` in `wolvwealth/api/optimize.py` — framework-free, plain dict in, plain dict out. Mirror the change in `backtest.py` until the two are unified |
| Change the browser optimize flow | `wolvwealth/views/optimizer.py` + `templates/results.html`, forms live in `templates/workspace.html` |
| Change global config | `wolvwealth/config.py` |

## Security notes

- This app has **no authentication on purpose**. Workspaces are an organizational boundary, not a security one: anyone who can reach the app can open, modify, or delete any workspace. That is the correct trade-off for a single household computer on localhost — and the reason you must never bind it to an untrusted network.
- The session secret is generated per install into `var/secret_key` and the database lives in `var/`; both are gitignored. Never commit anything from `var/`, and never commit a real holdings dump in a test or fixture if you wouldn't want it public.

## Questions

Open an issue or ask in the group chat. If you figure out something non-obvious about the codebase, add it to ARCHITECTURE.md or this file so the next person does not have to rediscover it.
