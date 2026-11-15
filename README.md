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

- **Web dashboard** — register, log in, and manage your account and API key
- **JSON API** — token-authenticated portfolio optimization with tiered usage limits
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

All endpoints accept JSON over POST and authenticate with an API token in the `Authorization` header. Tokens are tied to an account, carry a use count and an expiration, and are shown on the account page. `GET /api/` lists the available routes.

| Endpoint         | Description                                            |
| ---------------- | ------------------------------------------------------ |
| `/api/`          | Usage info (GET or POST, no auth)                      |
| `/api/optimize/` | Optimize a portfolio (consumes one use per call)       |
| `/api/backtest/` | Start a backtest job (one use); poll `/api/backtest/<id>/` |
| `/api/account/`  | Account details for the token's owner                  |
| `/api/admin/*`, `/api/db/*` | Administration (admin token required)       |

### `POST /api/optimize/`

Request fields (all optional except that total investment must be > 0):

- `initial_cash` (number) — cash to allocate
- `initial_holdings` (object) — current holdings as `{"TICKER": shares}`
- `universe` (list of strings) — tickers to consider; `"topN"` (e.g. `"top50"`) expands to the N largest by market cap. Default: top 500
- `constraints.max_weight` (0-1) — maximum weight per asset
- `constraints.max_positions` (int) — keep only the N largest positions
- `constraints.min_universal_weight` (0-1) — minimum weight for every universe asset
- `exclude_metrics` (bool) — omit the `metrics` block from the response

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
  }
}
```

Errors return `{"error": {"message": "...", "status_code": ...}}` with 400 (invalid input), 401 (missing token), or 403 (invalid/expired/exhausted token).

### `POST /api/backtest/` and `GET /api/backtest/<job_id>/`

Runs the simulation described under [Backtesting](#backtesting) as a background job and returns the optimizer's result next to its benchmarks. A 50-stock monthly backtest takes roughly 0.6–1 s per rebalance, so the POST returns immediately with a job id and the client polls for progress.

1. `POST /api/backtest/` validates the request, charges one use, and returns `202 Accepted` with a job snapshot and a `Location` / `status_url` to poll. If an identical request is already cached, it returns `200` with the finished job (`"cached": true`) right away.
2. `GET /api/backtest/<job_id>/` (about once a second) returns the job's `status` (`queued`, `running`, `succeeded`, `failed`), `progress` (`completed`, `total`, `fraction`, `message`), `elapsed_seconds` and `estimated_seconds_remaining`. When `status` is `succeeded` the snapshot includes the full `report`; when `failed`, an `error` message. Polling is free.

Authenticate with an API key as above, or from the browser with the logged-in session cookie; starting a job from a session also requires an `X-CSRF-Token` header (the token the templates expose). Jobs are visible only to the account that started them and expire 15 minutes after finishing. Malformed requests cost nothing, and neither do jobs refused for capacity: `429` when you already have `WOLVWEALTH_BACKTEST_MAX_JOBS_PER_USER` jobs running, `503` when the server queue is full.

Request fields (`start` required, everything else optional):

- `start`, `end` (YYYY-MM-DD) — `end` defaults to the last price date
- `initial_cash` (number, default 100000)
- `frequency` — `daily`, `weekly`, `biweekly`, `monthly` (default), `quarterly`, `semiannually`, `yearly`
- `universe` (list) — tickers and/or `"topN"` aliases, default `["top50"]`
- `lookback_days` (int, default 1095), `execution_lag` (trading days, default 1), `transaction_cost_bps` (default 0)
- `constraints` — same object as `/api/optimize/`
- `benchmarks` (list of tickers, default `["SPY"]`) — buy-and-hold comparisons; an equal-weight portfolio of the universe is always included
- `include_equity` (bool, default true) — set false to omit the daily equity curves

Requests that would need more than `WOLVWEALTH_BACKTEST_MAX_REBALANCES` rebalances or more than `WOLVWEALTH_BACKTEST_MAX_UNIVERSE_SIZE` tickers are rejected with 400.

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

## Backtesting

The `wolvwealth.backtest` package replays the optimizer over historical data and compares it against two benchmarks: an equal-weight portfolio of the same universe (the fair comparison) and buy-and-hold SPY. It is installed as the `wolvwealth-backtest` command; `python backtest.py` is an equivalent wrapper.

```bash
wolvwealth-backtest --start 2019-01-01 --frequency monthly --universe top50 --cost-bps 5
wolvwealth-backtest --start 2015-01-01 --universe AAPL MSFT GOOGL --max-positions -1 --json > report.json
wolvwealth-backtest --help
```

How the simulation works:

- **Rebalance schedule** uses calendar steps (`monthly` = same day next month), snapped forward to the next trading day.
- **Lookback window** (`--lookback-days`, default 3 years) limits the history the optimizer sees. Only assets that already traded at the start of the window are eligible, so stocks that were not yet public are skipped instead of crashing the run.
- **Execution lag** (`--execution-lag`, default 1 trading day): the optimizer decides on day *t*'s close and trades at day *t+1*'s close.
- **Transaction costs** (`--cost-bps`) are charged on traded value and paid out of the portfolio.
- **Accounting** carries exact share counts and cash; the equity curve is marked to market every trading day through the end date.
- **Metrics** (total return, CAGR, volatility, Sharpe, max drawdown, Calmar, turnover, costs) are computed from the realized equity curve, not from the optimizer's forecasts.

The ticker universe is a snapshot of today's listings ranked by today's market cap, so every backtest carries survivorship bias. The report says so; read the optimizer's results relative to the equal-weight benchmark rather than in absolute terms.

## Project Structure

```
├── wolvwealth/            Flask application package
│   ├── api/               JSON API (optimize, backtest, auth, admin) + in-memory market data
│   ├── views/             Server-rendered pages (login, account, optimizer, ...)
│   ├── templates/         Jinja templates (base.html + pages)
│   ├── js/                React dashboard source (built by Vite)
│   ├── static/            CSS/images + generated js/bundle.js, css/output.css
│   ├── backtest/          Backtesting package (config, schedule, strategies, engine, metrics, service, request, cache, jobs, cli)
│   ├── optimizer_core.py  Shared max-Sharpe optimization core
│   ├── optimizer_params.py Shared universe/constraint validation (API + backtester)
│   ├── market_data.py     Loads the two market-data CSVs
│   └── config.py          Environment-driven configuration
├── bin/                   setup / run / db / update_prices scripts
├── sql/                   schema.sql + development seed data
├── tests/                 pytest suite (fixture market data in tests/fixtures)
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
npm run build                # production asset build
```

CI runs all of the above on every push and pull request.

## Deployment

```bash
export WOLVWEALTH_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
./bin/run prod    # npm run build + gunicorn -c gunicorn_config.py wolvwealth:app
```

Gunicorn binds `0.0.0.0:8000` by default; run it behind a reverse proxy (nginx, Caddy) and set `GUNICORN_FORWARDED_ALLOW_IPS` to your proxy's address.

## License

MIT — see [LICENSE](LICENSE).
