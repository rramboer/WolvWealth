![WolvWealth Logo](/wolvwealth/static/img/logo.png)

# WolvWealth

WolvWealth builds a stock portfolio for you. You give it your cash and the shares you already hold, and it returns a maximum-Sharpe allocation: which stocks to own, how many shares of each, and the expected return, volatility and Sharpe ratio of the result. It runs as a web app and as a JSON API.

It's for individual investors who want a mean-variance allocation of their own money without a spreadsheet, and for developers who want that as an API call. It started at MHacks 16 in November 2023 and is still being worked on; see [ROADMAP.md](ROADMAP.md) for what's next.

## How it works

- **Prices:** `historical_prices.csv` holds daily adjusted closes for the 1,982 US tickers in `ticker_universe.csv` (sorted by market cap), from January 2006 to January 30, 2024. It's a snapshot; refreshing it is on the roadmap.
- **Optimization:** [PyPortfolioOpt](https://github.com/robertmartin8/PyPortfolioOpt) estimates expected returns as the mean historical return and risk as an exponentially weighted covariance, then solves for the maximum Sharpe ratio (risk-free rate 2%, the library default). The default universe is the 500 largest tickers.
- **Constraints:** a cap on any one position (`max_weight`), a floor on every position (`min_universal_weight`) and a position count (`max_positions`).
- **Accounts:** registering creates a free API key (15 optimizations, valid 7 days). Accounts and keys live in a local SQLite database under `var/`.

## Built with

- Python 3.10+, Flask 3, PyPortfolioOpt (cvxpy), pandas, yfinance, SQLite, bcrypt
- Jinja templates styled with Tailwind CSS, and a React 18 optimizer page (webpack and Babel, Chart.js and MUI charts)

## Running it

You need Python 3.10+, Node.js with npm, and the `sqlite3` command-line tool.

```bash
git clone https://github.com/rramboer/WolvWealth.git
cd WolvWealth
./bin/setup                 # creates env/ and installs the Python dependencies
source env/bin/activate
npm install                 # React, webpack and Tailwind for the frontend build
cp .env.example .env        # optional: set WOLVWEALTH_SECRET_KEY so logins survive restarts
./bin/run dev               # creates the database if needed, then serves http://localhost:8000
```

`./bin/run dev` rebuilds the CSS and JavaScript in watch mode while the Flask development server runs.

Open http://localhost:8000, register an account, and your API key is on the **Account** page. To make an account an admin (for the `/api/admin/` endpoints):

```bash
sqlite3 var/wolvwealth.sqlite3 "INSERT INTO admins(username) VALUES ('your-username');"
```

Database commands: `./bin/db create`, `./bin/db reset` (erases everything), `./bin/db destroy`, `./bin/db dump`.

## Using the API

```bash
curl -X POST http://localhost:8000/api/optimize/ \
  -H "Authorization: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"initial_cash": 10000, "initial_holdings": {"AAPL": 5}, "universe": ["top50"], "constraints": {"max_weight": 0.2}}'
```

| Field | Meaning | Default |
|---|---|---|
| `initial_cash` | Cash to invest, in dollars | `0` |
| `initial_holdings` | Ticker to share count, valued at the last price in the file | `{}` |
| `universe` | Tickers to choose from; `top20` through `top500` expand to the largest N | the top 500 |
| `constraints` | `max_weight` (0 to 1), `min_universal_weight` (0 to 1), `max_positions` | none |
| `exclude_metrics` | Leave `metrics` out of the response | `false` |

The response has `optimized_portfolio` (ticker to `shares`, `value` and `percent_weight`) and `metrics` (`portfolio_value`, `expected_annual_return`, `annual_volatility`, `sharpe_ratio`, `num_assets`). Each call uses one optimization from the key.

## Backtesting

`backtest.py` re-runs the optimizer at a fixed interval over the price history, each time using only the prices up to that date, and starts each round from the previous round's portfolio.

## License

MIT. See [LICENSE](LICENSE).

## Disclaimer

WolvWealth is not financial advice. Its numbers come from historical prices, which don't predict future returns. Check any allocation yourself before you trade on it.
