![WolvWealth Logo](/wolvwealth/static/img/logo.png)

# WolvWealth

WolvWealth builds a stock portfolio for you. You give it your cash and the shares you already hold, and it returns a maximum-Sharpe allocation: which stocks to own, how many shares of each, and the expected return, volatility and Sharpe ratio of the result. It runs as a web app and as a JSON API.

It's for individual investors who want a mean-variance allocation of their own money without a spreadsheet, and for developers who want that as an API call. It started at MHacks 16 in November 2023 and is still being worked on; see [ROADMAP.md](ROADMAP.md) for what's next.

## How it works

- **Prices:** `historical_prices.csv` holds daily closes, adjusted for splits and dividends, for the 1,753 US tickers in `ticker_universe.csv` (sorted by market cap as of early 2024), from January 2006 to the last refresh, currently November 11, 2026. `bin/refresh-prices` re-downloads it; see [Refreshing prices](#refreshing-prices).
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
| `initial_holdings` | Ticker to share count, or to a list of tax lots (see below) | `{}` |
| `universe` | Tickers to choose from; `top20` through `top500` expand to the largest N | the top 500 |
| `constraints` | `max_weight` (0 to 1), `min_universal_weight` (0 to 1), `max_positions` | none |
| `exclude_metrics` | Leave `metrics` out of the response | `false` |
| `prices` | Ticker to current price, used instead of the last price in the file | none |
| `lot_method` | How sells pick lots: `min_tax`, `fifo`, `hifo` or `lifo` | `min_tax` |
| `tax_rates` | `short_term` and `long_term` rates (0 to 1) that `min_tax` ranks lots by | `0.24` and `0.15` |
| `trade_date` | The sale date (`YYYY-MM-DD`) for holding periods and wash sales | today |

A ticker that has no price on the last day of the file (it stopped trading before the file ends) is rejected in `initial_holdings` and `universe`, and left out of the `top20`-style aliases.

The response has `optimized_portfolio` (ticker to `shares`, `value` and `percent_weight`), `trades` and `metrics` (`portfolio_value`, `expected_annual_return`, `annual_volatility`, `sharpe_ratio`, `num_assets`). Each call uses one optimization from the key.

`trades` is the list of orders that turns `initial_holdings` into `optimized_portfolio`, in the order to place them: every sell first, so its proceeds can fund the buys, then every buy, each side largest dollar value first. Each trade has `action` (`sell` or `buy`), `ticker`, `shares`, `price` and `value`. A holding the optimized portfolio doesn't include is sold in full.

### Current prices

Holdings, share counts and trades use the last price in the file, from the day before the last refresh, unless the request sends `prices` (for example, to price a trade with today's quotes). Expected returns and risk always come from the full price history. When `prices` is sent, the response adds `price_sources`: `price_data_date` (the file's last date), and which tickers used a price from the request (`from_request`) or from the file (`from_price_data`).

### Data warnings

When a ticker in `initial_holdings` or `optimized_portfolio` has price history that looks wrong, the response adds `data_warnings`, one entry per ticker, with `ticker`, `message` and, where they apply, `non_positive_prices` (`days`, `from`, `to`) and `large_moves` (`date`, `change_percent`). Two things are flagged:

- Prices at or below $0. These are always a data error; Yahoo's dividend adjustment produces them when a dividend is recorded as larger than the price (NFE, after its 2026 reverse split).
- One-day moves of more than 90% up or down. These can be real (GME in January 2021) or a bad split or dividend adjustment (CHRD's +25,733% when its post-bankruptcy shares were joined to the old ones).

The data is left as is. If it's wrong, that ticker's expected return and risk are too, so check its history elsewhere before trading on it.

### Tax lots

A holding can be a list of the lots it was bought in instead of a share count:

```json
"initial_holdings": {
  "AAPL": [{"shares": 3, "cost_per_share": 120.50, "acquired": "2021-03-01"},
           {"shares": 2, "cost_per_share": 190.00, "acquired": "2026-10-20"}],
  "MSFT": 10
}
```

List each ticker once, with all of its lots in one list; a ticker that appears more than once (in any capitalization) is an error if any of its entries is lots, and so is a ticker listed twice in `prices`. Lots don't change the optimization; a holding counts as the sum of its lots. They add which lots each sell should come from. When any holding is given as lots, each sell of such a holding gets `lots` (`acquired`, `shares`, `cost_per_share`, `term` of `short` or `long`, and `gain`) and `realized_gain`, and the response gets `tax_summary`:

| Field | Meaning |
|---|---|
| `short_term_gain`, `long_term_gain`, `total_gain` | Realized gains across all sells with lots; negative is a loss |
| `trade_date`, `lot_method`, `tax_rates` | What the lots were chosen with |
| `unknown_basis` | Sells of holdings given as a share count, which have no gain figure |
| `priced_from_price_data`, `price_data_date` | Sells whose gain uses the file's old price because `prices` didn't include them |
| `wash_sale_warnings` | Sells at a loss when another lot of the ticker was bought in the 30 days before `trade_date` |

`min_tax` sells the lots with the least tax per share first, at the assumed `tax_rates`, so losses go first. A lot is long term when sold after its one-year anniversary. The lots only apply if you choose them as specific lots when you place the order; otherwise most brokers sell the oldest lots first. These are estimates: they don't know about your other accounts, purchases after the sale, loss carryovers or state taxes.

## Refreshing prices

```bash
bin/refresh-prices           # re-download the whole history, about 3 minutes
bin/refresh-prices --prune   # also drop tickers Yahoo no longer has from ticker_universe.csv
```

Then restart the server; prices are loaded at startup.

- It always downloads the full history from 2006 again instead of adding new days. Splits and dividends re-adjust every earlier price, so new days wouldn't line up with old ones.
- It stops at the last finished trading day: run before 4:30 pm New York time, today is left out. A trailing day Yahoo has only partly filled in is dropped.
- It reports tickers Yahoo returned nothing for and tickers with no price in the last 10 days of the data. Without `--prune` they stay in the universe with no current price, and the API rejects them. With `--prune` they're removed. Check the list: a symbol Yahoo has given to a different company won't be caught.
- It lists tickers whose history looks like bad data (see [Data warnings](#data-warnings)) without changing them.
- If more than a quarter of the tickers come back empty, which usually means Yahoo is rate limiting, it leaves the files alone. `--force` writes them anyway.

## Tests

```bash
python3 -m pytest
```

Run from the repo root. The tests use a throwaway database, not `var/`, and don't download anything. They take about a minute, almost all of it one request over the full default universe.

## Backtesting

`backtest.py` re-runs the optimizer at a fixed interval over the price history, each time using only the prices up to that date, and starts each round from the previous round's portfolio.

## License

MIT. See [LICENSE](LICENSE).

## Disclaimer

WolvWealth is not financial advice. Its numbers come from historical prices, which don't predict future returns. Check any allocation yourself before you trade on it.
