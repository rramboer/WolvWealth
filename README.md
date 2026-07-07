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
- **Backtesting utility** — replay the optimizer over historical data at any rebalance frequency

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

## Backtesting

`backtest.py` replays the optimizer over historical data, re-optimizing at a fixed frequency and reporting portfolio value at each rebalance:

```bash
uv run python backtest.py --start 2019-01-01 --cash 100000 --frequency monthly
```

## Project Structure

```
├── wolvwealth/            Flask application package
│   ├── api/               JSON API (optimize, auth, admin) + in-memory market data
│   ├── views/             Server-rendered pages (login, account, optimizer, ...)
│   ├── templates/         Jinja templates (base.html + pages)
│   ├── js/                React dashboard source (built by Vite)
│   ├── static/            CSS/images + generated js/bundle.js, css/output.css
│   ├── optimizer_core.py  Shared max-Sharpe optimization core
│   └── config.py          Environment-driven configuration
├── bin/                   setup / run / db / update_prices scripts
├── sql/                   schema.sql + development seed data
├── tests/                 pytest suite (fixture market data in tests/fixtures)
├── var/                   generated SQLite database (gitignored)
├── ticker_universe.csv    ticker universe, sorted by market cap
├── historical_prices.csv  bundled price history snapshot
└── backtest.py            backtesting CLI
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
