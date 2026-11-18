![WolvWealth Logo](wolvwealth/static/img/logo.png)

# Welcome to WolvWealth!

[![CI](https://github.com/rramboer/WolvWealth/actions/workflows/ci.yml/badge.svg)](https://github.com/rramboer/WolvWealth/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](pyproject.toml)
[![Node 22](https://img.shields.io/badge/node-%E2%89%A522-green.svg)](package.json)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

**Elevate your investment strategy with precision and confidence.**

## About

We're disrupting finance by empowering your investment decisions with our modern portfolio optimizer, building diversified portfolios tailored to your risk preferences and financial goals.

WolvWealth is a web application that democratizes high quality investment portfolio management by making it easy for anyone to create an optimized stock portfolio. Under the hood it applies Modern Portfolio Theory — max-Sharpe optimization over mean historical returns and an exponentially-weighted covariance matrix, powered by [PyPortfolioOpt](https://pyportfolioopt.readthedocs.io/) and cvxpy.

### Features

- **Web dashboard** — register, log in, manage your account and API key, and use the optimizer and backtester from the browser with live progress
- **JSON API** — portfolio optimization and backtesting, authenticated by API token (or the browser session), with tiered usage limits, strict input validation and rate limiting
- **Constraint options** — maximum positions, maximum weight per asset, minimum universal weight, custom ticker universes (`"top50"`, `"AAPL"`, ...)
- **Backtester** — replay the optimizer over historical data with lookback windows, execution lag, transaction costs, realized metrics and benchmarks

## Inspiration

Our inspiration for WolvWealth emerged from our shared passion for democratized financial technology and the desire to empower investors with a sophisticated yet user-friendly tool to optimize their investment portfolios. We recognized the need for a platform for the everyday investor that integrates cutting-edge technologies, allowing users to make informed decisions, maximize returns, and manage risk effectively.

## Getting Started

Prerequisites: [Python 3.12](https://www.python.org/), [uv](https://docs.astral.sh/uv/), [Node.js 22+](https://nodejs.org/).

```bash
git clone https://github.com/rramboer/WolvWealth.git
cd WolvWealth

./bin/setup       # uv sync + npm ci + create the dev database
./bin/run dev     # tailwind & vite watchers + Flask dev server
```

Then open http://localhost:8000. The development database seeds two fake accounts you can log in with (password `wolvwealth-dev` for both):

| Username   | Role  | API token                  |
| ---------- | ----- | -------------------------- |
| `devadmin` | admin | `insecure-dev-admin-token` |
| `devuser`  | user  | `insecure-dev-user-token`  |

## Scripts

| Command                  | What it does                                                        |
| ------------------------ | ------------------------------------------------------------------- |
| `./bin/setup`            | Install Python (uv) + frontend (npm) dependencies, create the dev DB |
| `./bin/run dev`          | Start asset watchers and the Flask dev server on :8000              |
| `./bin/run prod`         | Build assets and serve with gunicorn (requires `WOLVWEALTH_SECRET_KEY`) |
| `./bin/db create`        | Create `var/wolvwealth.sqlite3` from `sql/schema.sql` + dev seeds   |
| `./bin/db reset`         | Destroy and recreate the database                                   |
| `./bin/db destroy`       | Delete the database                                                 |
| `./bin/db dump`          | Print users and tokens                                              |
| `./bin/update_prices`    | Refresh `historical_prices.csv` from Yahoo Finance (network)        |
| `npm run build`          | Production build: Tailwind CSS + Vite JS bundle                     |
| `npm run lint`           | ESLint over the React sources                                       |
| `npm test`               | Vitest unit tests for the React sources                             |
| `uv run pytest`          | Run the Python test suite                                           |
| `uv run ruff check .`    | Lint Python code                                                    |
| `uv run ruff format .`   | Format Python code                                                  |

## Configuration

All configuration is environment-driven (see [.env.example](.env.example)):

| Variable                       | Default                     | Purpose                                              |
| ------------------------------ | --------------------------- | ---------------------------------------------------- |
| `WOLVWEALTH_SECRET_KEY`        | insecure dev fallback       | Signs session cookies. **Required in production.**   |
| `WOLVWEALTH_DATABASE`          | `var/wolvwealth.sqlite3`    | SQLite database path                                 |
| `WOLVWEALTH_DATA_DIR`          | repository root             | Directory with the two market-data CSVs              |
| `WOLVWEALTH_SETTINGS`          | (unset)                     | Optional extra Flask settings file                   |
| `GUNICORN_PROCESSES`           | `1`                         | Gunicorn worker count                                |
| `GUNICORN_THREADS`             | `4`                         | Gunicorn threads per worker                          |
| `GUNICORN_BIND`                | `0.0.0.0:8000`              | Gunicorn bind address                                |
| `GUNICORN_FORWARDED_ALLOW_IPS` | `127.0.0.1`                 | Trusted reverse-proxy IPs for `X-Forwarded-*`        |
| `WOLVWEALTH_BACKTEST_MAX_REBALANCES` | `120`                 | Most rebalances one `/api/backtest/` request may run |
| `WOLVWEALTH_BACKTEST_MAX_UNIVERSE_SIZE` | `100`              | Largest universe one `/api/backtest/` request may use |
| `WOLVWEALTH_BACKTEST_CACHE_SIZE` | `64`                      | Backtest reports kept in the in-memory cache         |
| `WOLVWEALTH_BACKTEST_MAX_WORKERS` | `2`                      | Backtest jobs that run at the same time              |
| `WOLVWEALTH_BACKTEST_MAX_PENDING_JOBS` | `20`                | Queued + running jobs before new ones get 503        |
| `WOLVWEALTH_BACKTEST_MAX_JOBS_PER_USER` | `2`                | Jobs one account may have in flight before 429       |
| `WOLVWEALTH_BACKTEST_JOB_TTL_SECONDS` | `900`                | How long a finished job stays pollable               |
| `WOLVWEALTH_TRUSTED_PROXIES` | `0`                        | Reverse proxies in front of the app; see [Rate limiting](#rate-limiting) |
| `WOLVWEALTH_RATELIMIT_ENABLED` | `true`                    | Turn rate limiting off entirely (not for production) |
| `WOLVWEALTH_RATELIMIT_STORAGE_URI` | `memory://`           | Counter storage; use `redis://...` with several workers |
| `WOLVWEALTH_RATELIMIT_*`     | see below                    | Individual limits, e.g. `WOLVWEALTH_RATELIMIT_LOGIN_PER_IP="5 per minute"` |

Generate a production secret key with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

> **Security note:** a hardcoded `SECRET_KEY`, real user accounts, and live API tokens were committed to this repository's history prior to the modernization. Any deployment that ever used them must rotate the secret key and revoke/reissue those tokens.

## Database

The SQLite database lives in `var/` (generated, not tracked in git) and is built from `sql/schema.sql` plus the development seeds in `sql/data.sql` by `./bin/db create`.

`sql/data.sql` is **development-only** fixture data. For production, create the schema without seeds and add your first admin manually:

```bash
python3 - <<'EOF'
import sqlite3
conn = sqlite3.connect("var/wolvwealth.sqlite3")
conn.executescript(open("sql/schema.sql").read())
conn.commit()
EOF
# Then register a user through the web UI and grant admin:
# INSERT INTO admins (username) VALUES ('<your-user>');
```

## Market Data

Two CSVs at the repository root drive the optimizer:

- `ticker_universe.csv` — ~2,000 tickers sorted by market cap (descending)
- `historical_prices.csv` — daily adjusted-close prices since 2006 (~40 MB), loaded into memory at startup

The bundled price data is a snapshot; the last row is treated as the "current" price, so refresh it periodically with `./bin/update_prices` (requires network access to Yahoo Finance). The app logs the data's last date at startup.

## API

Endpoints take JSON and authenticate with an API token in the `Authorization` header. Tokens are tied to an account, carry a use count and an expiration, and are shown on the account page. `/api/optimize/` and `/api/backtest/` also accept the logged-in browser session (that is how the dashboard calls them); state-changing session requests must send the session's CSRF token in an `X-CSRF-Token` header. `GET /api/` lists the available routes.

| Endpoint | Description |
| --- | --- |
| `GET`/`POST /api/` | Usage info (no auth) |
| `POST /api/optimize/` | Optimize a portfolio (one use) |
| `POST /api/backtest/` | Start a backtest job (one use) |
| `GET /api/backtest/<job_id>/` | Poll a backtest job (free) |
| `POST /api/account/` | Account details for the token's owner |
| `POST /api/admin/*`, `/api/db/*` | Administration (admin token required) |

Optimize and backtest requests are authenticated first, then validated, and only a valid, feasible request is charged a use: malformed input, infeasible constraints, rate-limited requests and refused jobs are free. Request bodies are strict JSON: the non-standard `NaN` / `Infinity` literals are rejected.

### `POST /api/optimize/`

Request fields (all optional except that total investment must be > 0). Numbers must be finite; booleans are not numbers.

- `initial_cash` (number, 0 to 1e12) — cash to allocate
- `initial_holdings` (object) — current holdings as `{"TICKER": shares}`, shares 0 to 1e12
- `universe` (list of strings) — tickers to consider; `"topN"` (e.g. `"top50"`) expands to the N largest by market cap, clamped to the universe size (`top0` is rejected). Default: top 500
- `constraints.max_weight` (greater than 0, at most 1) — maximum weight per asset
- `constraints.max_positions` (integer ≥ 0; 0 means no limit) — keep only the N largest positions
- `constraints.min_universal_weight` (0 to 1) — minimum weight for every universe asset
- `exclude_metrics` (bool) — omit the `metrics` block from the response

Unknown constraint keys are rejected. Constraints must be able to invest 100%: the portfolio holds at most the universe size (or `max_positions`, if smaller) assets, each at most `max_weight`, so that product must reach 1. Otherwise the request fails with a hint that works at the API's 2-decimal precision, e.g. for `top3` with `max_weight` 0.1: *Raise max_weight to at least 0.34, or hold at least 10 stocks.*

```bash
curl -s -X POST http://localhost:8000/api/optimize/ \
  -H "Authorization: insecure-dev-user-token" \
  -H "Content-Type: application/json" \
  -d '{
        "initial_cash": 10000,
        "universe": ["top50"],
        "constraints": {"max_positions": 5, "max_weight": 0.5}
      }'
```

```json
{
  "metrics": {
    "annual_volatility": 0.106,
    "expected_annual_return": 0.239,
    "num_assets": 5,
    "portfolio_value": 10000,
    "sharpe_ratio": 2.25
  },
  "optimized_portfolio": {
    "AAPL": { "percent_weight": 9.35, "shares": 4.9719, "value": 934.92 },
    "ABBV": { "percent_weight": 30.62, "shares": 18.5665, "value": 3061.98 },
    "BND": { "percent_weight": 10.74, "shares": 14.6842, "value": 1073.71 },
    "MA": { "percent_weight": 41.82, "shares": 9.3929, "value": 4181.61 },
    "NFLX": { "percent_weight": 7.48, "shares": 1.3286, "value": 747.78 }
  },
  "cash": { "percent_weight": 0.0, "value": 0.0 },
  "notes": []
}
```

If no asset in the universe has an expected return above the 2% risk-free rate, that is an answer, not an error: the max-Sharpe recommendation is to hold cash. The response is `200` with an empty `optimized_portfolio`, `cash` holding the full amount, `metrics` of the cash position (the risk-free return, zero volatility, `num_assets` 0), and the reason in `notes`, e.g. *No asset's expected annual return beats the 2.0% risk-free rate (best: AAPL at -5.0%), so the max-Sharpe recommendation is to hold cash.* It costs a use like any other answer.

Errors return `{"error": {"message": "...", "status_code": ...}}`:

| Status | Meaning |
| --- | --- |
| 400 | Invalid input (unparseable or non-finite JSON, bad or out-of-range values, infeasible constraints, unrunnable backtest) |
| 401 | No API key and no logged-in session |
| 403 | Invalid, expired or exhausted token, or missing/invalid CSRF token on a session request |
| 404 | Backtest job not found (expired, belongs to another account, or the server restarted) |
| 429 | Rate limited (see [Rate limiting](#rate-limiting); honor `Retry-After`) or too many of your backtest jobs running |
| 503 | Backtest queue full; retry shortly |

### `POST /api/backtest/` and `GET /api/backtest/<job_id>/`

Runs the simulation described under [Backtesting](#backtesting) as a background job and returns the optimizer's result next to its benchmarks. A 50-stock monthly backtest takes roughly 0.6–1 s per rebalance, so the POST returns immediately with a job id and the client polls for progress.

1. `POST /api/backtest/` authenticates, checks that your job queue has room, validates the request, charges one use, and returns `202 Accepted` with a job snapshot and a `Location` / `status_url` to poll. If an identical request is already cached, it returns `200` with the finished job (`"cached": true`) right away.
2. `GET /api/backtest/<job_id>/` (about once a second) returns the job's `status` (`queued`, `running`, `succeeded`, `failed`), `progress` (`completed`, `total`, `fraction`, `message`), `elapsed_seconds` and `estimated_seconds_remaining`. When `status` is `succeeded` the snapshot includes the full `report`; when `failed`, an `error` message. Polling is free.

Authenticate with an API key as above, or from the browser with the logged-in session cookie; starting a job from a session also requires an `X-CSRF-Token` header (the token the templates expose). Jobs are visible only to the account that started them and expire 15 minutes after finishing. Nothing is charged for a rejected request or for a job refused for capacity: `429` when you already have `WOLVWEALTH_BACKTEST_MAX_JOBS_PER_USER` jobs running, `503` when the server queue is full.

Request fields (`start` required, everything else optional):

- `start`, `end` (YYYY-MM-DD, within the price data) — `end` defaults to the last price date
- `initial_cash` (number, greater than 0 up to 1e12, default 100000)
- `frequency` — `daily`, `weekly`, `biweekly`, `monthly` (default), `quarterly`, `semiannually`, `yearly`
- `universe` (list) — tickers and/or `"topN"` aliases, default `["top50"]`
- `lookback_days` (integer 30–5475, default 1095), `execution_lag` (trading days, 0–30, default 1), `transaction_cost_bps` (0–1000, default 0)
- `constraints` — same object as `/api/optimize/`
- `benchmarks` (list of tickers, default `["SPY"]`) — buy-and-hold comparisons; an equal-weight portfolio of the universe is always included
- `include_equity` (bool, default true) — set false to omit the daily equity curves

Unknown fields are rejected. Before anything is charged the request is also checked against the data and the limits, all with 400:

- dates outside the price data;
- more than `WOLVWEALTH_BACKTEST_MAX_REBALANCES` rebalances (counted from the schedule dates alone, so an oversized request is refused in milliseconds) or more than `WOLVWEALTH_BACKTEST_MAX_UNIVERSE_SIZE` tickers;
- ranges in which no rebalance could execute, found by a dry run of the schedule without the optimizer: no lookback history yet, too few stocks with history for the constraints, or a range shorter than the execution lag. The message says which.

```bash
curl -s -X POST http://localhost:8000/api/backtest/ \
  -H "Authorization: insecure-dev-user-token" \
  -H "Content-Type: application/json" \
  -d '{"start": "2021-01-01", "frequency": "monthly", "universe": ["top50"], "transaction_cost_bps": 5}'
# -> 202 {"job_id": "5gh1DgAaDsY7LgR7", "status": "queued", "status_url": "/api/backtest/5gh1DgAaDsY7LgR7/", ...}

curl -s http://localhost:8000/api/backtest/5gh1DgAaDsY7LgR7/ -H "Authorization: insecure-dev-user-token"
```

While running:

```json
{
  "job_id": "5gh1DgAaDsY7LgR7",
  "status": "running",
  "progress": { "phase": "optimizer", "completed": 16, "total": 39, "fraction": 0.4103, "message": "Optimizing rebalance 17 of 37" },
  "elapsed_seconds": 8.1,
  "estimated_seconds_remaining": 11.6,
  "cached": false,
  "status_url": "/api/backtest/5gh1DgAaDsY7LgR7/"
}
```

When finished, `status` is `succeeded` and `report` holds the result (equity curves abbreviated):

```json
"report": {
  "config": { "start": "2021-01-01", "end": "2024-01-30", "frequency": "monthly", "...": "..." },
  "result": {
    "strategy": "max_sharpe",
    "start_value": 100000.0,
    "end_value": 124366.52,
    "equity": { "2021-01-04": 100000.0, "...": "..." },
    "rebalances": [
      {
        "decision_date": "2021-01-04",
        "execution_date": "2021-01-05",
        "portfolio_value": 100000.0,
        "weights": { "TSLA": 0.31, "...": "..." },
        "shares": { "TSLA": 115.2, "...": "..." },
        "cash": 0.0,
        "turnover": 0.9995,
        "transaction_cost": 49.98,
        "eligible_assets": 49,
        "expected_performance": { "expected_annual_return": 0.42, "annual_volatility": 0.25, "sharpe_ratio": 1.6 }
      }
    ],
    "warnings": []
  },
  "metrics": { "total_return": 0.2437, "cagr": 0.0735, "annual_volatility": 0.19, "sharpe_ratio": 0.33, "max_drawdown": -0.27, "...": "..." },
  "benchmarks": {
    "equal_weight": { "result": { "...": "..." }, "metrics": { "...": "..." } },
    "SPY": { "result": { "...": "..." }, "metrics": { "...": "..." } }
  },
  "warnings": ["Survivorship bias: ..."]
}
```

Jobs are held in the memory of the process that accepted them, so polls must reach that process: keep `GUNICORN_PROCESSES=1` (the default) or put sticky routing in front of multiple workers.

## Web dashboard

`/optimizer/` (login required) is a React app with two tabs that call the JSON API with the browser session:

- **Optimize** — amount, universe and constraints in; allocation table (with a cash row when anything is held in cash) and the optimizer's expected return / volatility / Sharpe out. A hold-cash answer is shown with its explanation.
- **Backtest** — date range, frequency, universe, lookback, execution lag, costs and constraints in. The form estimates the rebalance count and run time before you start; while the job runs it shows a progress bar with the current rebalance and time remaining (polling `GET /api/backtest/<job_id>/`). Results: growth-of-cash and drawdown charts against the equal-weight and SPY benchmarks, a realized-metrics table, and an expandable rebalance log.

Both forms validate the same rules as the server (finite numbers, ranges, dates within the data, universe size, constraint feasibility) and disable the button with the reason under the field until the input is valid. Each run costs one use, shown in the page header.

While a backtest runs, polling survives transient trouble: network errors, 408, 5xx and malformed responses are retried with capped, jittered exponential backoff (a "retrying" notice is shown), 429s wait for `Retry-After`, and after 8 consecutive failures the job is reported as lost with an explanation. A vanished job (404) or ended session (401/403) is reported immediately. Starting a job is never retried automatically, since that request charges a use.

The source is in `wolvwealth/js/` (`lib/` pure helpers: API client, validation, retry policy, chart data; `hooks/useBacktestJob.js` for the job lifecycle; `components/` presentational pieces; `features/` the two tabs) and is built by Vite into `static/js/bundle.js`.

## Rate limiting

Every request is rate limited before its view runs, so before authentication, request parsing or any database work beyond what identifying the caller needs. Over-limit requests get `429 Too Many Requests` with a `Retry-After` header (JSON under `/api/`, an HTML page elsewhere) and are never charged. Responses carry `X-RateLimit-Limit`, `-Remaining` and `-Reset` headers.

| Limit (env suffix after `WOLVWEALTH_RATELIMIT_`) | Default | Keyed by | Applies to |
| --- | --- | --- | --- |
| `SITE_PER_IP` | 600 per minute | client IP | every route except static files, one shared bucket |
| `API_PER_IP` | 300 per minute | client IP | every `/api/` route, one shared bucket (stops anonymous floods and API-key guessing) |
| `OPTIMIZE_PER_CALLER` | 20 per minute; 200 per hour | API key or session user | `POST /api/optimize/` |
| `BACKTEST_START_PER_CALLER` | 10 per minute; 60 per hour | API key or session user | `POST /api/backtest/` |
| `BACKTEST_STATUS_PER_CALLER` | 180 per minute | API key or session user | `GET /api/backtest/<id>/` (the dashboard polls once a second) |
| `ACCOUNT_PER_IP` | 30 per minute | client IP | `POST /api/account/` |
| `LOGIN_PER_IP` | 30 per minute | client IP | `POST /accounts/login/`, all attempts (bounds password-hashing CPU) |
| `LOGIN_FAILURES_PER_IP` | 10 per minute; 50 per hour | client IP | failed logins |
| `LOGIN_FAILURES_PER_ACCOUNT_CLIENT` | 5 per minute; 20 per hour | account + known device, else account + IP | failed logins at one account from one place |
| `LOGIN_FAILURES_PER_ACCOUNT` | 20 per hour | account | failed logins at one account from clients that have never logged in to it (distributed brute force) |
| `REGISTER_PER_IP` | 5 per hour | client IP | `POST /accounts/register/` |

**Login throttling cannot be used to lock someone out.** Only failed logins count against the failure limits, so normal logins from a shared office IP never add up. The per-account limit, which stops a botnet spreading guesses across many IPs, only applies to clients that have never logged in to that account: a successful login stores a signed device cookie (`wolvwealth_device`, HttpOnly, scoped to `/accounts/`), and from that browser the owner is throttled per device instead. So an attacker who knows a username can exhaust the guesses available to strangers, but not the owner's access from their own devices (OWASP's "device cookie" pattern). Logging in from a brand-new device while an account is under attack can still be delayed until the hour rolls over.

**Behind a reverse proxy, set `WOLVWEALTH_TRUSTED_PROXIES`.** Gunicorn reports the socket peer as the client address, so behind nginx every visitor looks like `127.0.0.1` and would share one set of per-IP limits; one abusive client would lock everyone out. Set it to the number of proxies in front of the app (usually `1`) and make sure the proxy appends to `X-Forwarded-For` (nginx: `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`). Only that many right-most entries are believed, so clients cannot spoof their address. Leave it at `0` when gunicorn is exposed directly; the header is then ignored.

Counters live in process memory by default, like backtest jobs, which is right for the default single gunicorn worker. With `GUNICORN_PROCESSES > 1`, point `WOLVWEALTH_RATELIMIT_STORAGE_URI` at a shared store such as `redis://localhost:6379` (install the `redis` package) or each worker will enforce its own copy of every limit.

Application-level limits complement, not replace, connection limits at the proxy or load balancer (e.g. nginx `limit_req` / `limit_conn`), which can refuse floods before they reach Python at all.

## Backtesting

The `wolvwealth.backtest` package replays the optimizer over historical data and compares it against two benchmarks: an equal-weight portfolio of the same universe (the fair comparison) and buy-and-hold SPY. It is installed as the `wolvwealth-backtest` command; `python backtest.py` is an equivalent wrapper.

```bash
wolvwealth-backtest --start 2019-01-01 --frequency monthly --universe top50 --cost-bps 5
wolvwealth-backtest --start 2015-01-01 --universe AAPL MSFT GOOGL --max-positions -1 --json > report.json
wolvwealth-backtest --help
```

The CLI applies the same pre-run checks as the API (dates within the data, a feasible universe, at least one rebalance that can execute) and exits with status 2 and a message otherwise. Run in a terminal, it shows a progress bar on stderr (`--quiet` to hide it).

How the simulation works:

- **Rebalance schedule** uses calendar steps (`monthly` = same day next month), snapped forward to the next trading day.
- **Lookback window** (`--lookback-days`, default 3 years) limits the history the optimizer sees. Only assets that already traded at the start of the window are eligible, so stocks that were not yet public are skipped instead of crashing the run.
- **Execution lag** (`--execution-lag`, default 1 trading day): the optimizer decides on day *t*'s close and trades at day *t+1*'s close.
- **Transaction costs** (`--cost-bps`) are charged on traded value and paid out of the portfolio.
- **Accounting** carries exact share counts and cash; the equity curve is marked to market every trading day through the end date.
- **Holding cash**: when no eligible asset's expected return over the lookback window beats the risk-free rate (`--risk-free-rate`, default 2%), the optimizer's answer is cash, so that rebalance sells into cash rather than keeping the previous positions. Each such rebalance carries a `note`, and the report adds one summary warning (e.g. *3 of 7 rebalance(s) moved to cash, first on 2022-06-01: ...*).
- **Metrics** (total return, CAGR, volatility, Sharpe, max drawdown, Calmar, turnover, costs) are computed from the realized equity curve, not from the optimizer's forecasts.

The ticker universe is a snapshot of today's listings ranked by today's market cap, so every backtest carries survivorship bias. The report says so; read the optimizer's results relative to the equal-weight benchmark rather than in absolute terms.

## Project Structure

```
├── wolvwealth/            Flask application package
│   ├── api/               JSON API (optimize, backtest, auth, admin) + in-memory market data
│   ├── views/             Server-rendered pages (login, account, optimizer, ...)
│   ├── templates/         Jinja templates (base.html + pages, 429.html)
│   ├── js/                React dashboard source: lib/, hooks/, components/, features/, __tests__/ (built by Vite)
│   ├── static/            CSS/images + generated js/bundle.js, css/output.css
│   ├── backtest/          Backtesting package: config, schedule, strategies, engine, preflight (pre-run checks),
│   │                      progress, metrics, service, request, cache, jobs, cli
│   ├── optimizer_core.py  Shared max-Sharpe optimization core
│   ├── optimizer_params.py Shared universe/constraint validation and feasibility (API + backtester)
│   ├── ratelimit.py       Rate limits, client-IP handling behind trusted proxies, 429 responses
│   ├── device_cookies.py  Signed "known device" cookies for login throttling
│   ├── market_data.py     Loads the two market-data CSVs
│   └── config.py          Environment-driven configuration
├── bin/                   setup / run / db / update_prices scripts
├── sql/                   schema.sql + development seed data
├── tests/                 pytest suite (fixture market data in tests/fixtures); JS tests live in wolvwealth/js/__tests__/
├── var/                   generated SQLite database (gitignored)
├── ticker_universe.csv    ticker universe, sorted by market cap
├── historical_prices.csv  bundled price history snapshot
└── backtest.py            thin wrapper around wolvwealth.backtest.cli
```

Build pipeline: Tailwind → `wolvwealth/static/css/output.css`; Vite → `wolvwealth/static/js/bundle.js`. Both are generated (gitignored) — run `npm run build` (or `./bin/run dev`, which watches) before serving.

## Development

```bash
uv run ruff check .          # lint
uv run ruff format .         # format
uv run pytest                # tests (fast: uses small fixture data)
npm run lint                 # eslint (React sources)
npm test                     # vitest (React sources)
npm run build                # production asset build
```

CI runs all of the above on every push and pull request. Lint with the ruff version pinned in `uv.lock` (what `uv run ruff` and the pre-commit hook use; `pre-commit install` runs it on every commit). A newer globally installed ruff can disagree with CI. Without a project environment, `uvx ruff@<locked version> check .` does the same.

## Deployment

```bash
export WOLVWEALTH_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
./bin/run prod    # npm run build + gunicorn -c gunicorn_config.py wolvwealth:app
```

Gunicorn binds `0.0.0.0:8000` by default; run it behind a reverse proxy (nginx, Caddy) and:

- set `WOLVWEALTH_TRUSTED_PROXIES` to the number of proxies in front of the app (usually `1`), or every visitor shares one set of per-IP rate limits (see [Rate limiting](#rate-limiting));
- set `GUNICORN_FORWARDED_ALLOW_IPS` to your proxy's address;
- serve over HTTPS and set `SESSION_COOKIE_SECURE = True` in a `WOLVWEALTH_SETTINGS` file (the device cookie follows it).

Backtest jobs and rate-limit counters live in process memory, so keep `GUNICORN_PROCESSES=1` (the default), or add sticky routing for job polling and a shared `WOLVWEALTH_RATELIMIT_STORAGE_URI` such as Redis. `gunicorn_config.py` warns when more workers are configured.

## License

MIT — see [LICENSE](LICENSE).
