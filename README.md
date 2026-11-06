![WolvWealth Logo](/wolvwealth/web/static/img/logo.png)

# WolvWealth

**A self-hosted portfolio optimizer. Give it your cash and holdings, get back a maximum-Sharpe allocation and the exact trades to reach it.**

Everything runs on your own machine: a single-process Flask app bound to localhost, with your portfolios in a local SQLite file that never leaves your disk. No hosted service, no accounts, no API keys.

## Why local-first

1. **Your holdings are financial data.** Keeping them in a local file removes the entire problem class of hosting, authenticating, and securing other people's money records.
2. **AI agents work best against local tools.** With broker MCP servers available (e.g. the Robinhood MCP, which lets an agent place real trades), the intended loop is: your agent calls WolvWealth (as MCP tools from a desktop app, from the shell with `wolvwealth optimize`, or over the local API at `POST http://localhost:8000/api/optimize/` — plain JSON, no auth), reads back the target allocation **and the trade list the app already computed**, and places those trades through the broker MCP. That only works cleanly when the optimizer runs on the same machine as the agent, with no credentials in the way.

This is also why the package installs into the **global interpreter** (no venv): the optimizer and its API must be runnable from anywhere on the device — by you at a shell, or by an agent spawned from any working directory — without activating an environment first.

## Workspaces

A **workspace** is one person using the machine (`ryan`, `my-brother`), with no password. Each workspace holds named **portfolios**, and each portfolio is a cash balance, share counts per ticker, and a **risk-free rate** — the annual yield that portfolio's uninvested money actually earns (a savings account, T-bills, a bond fund), which the Sharpe ratio is maximized and reported against. Separation between workspaces is organizational, not security: anyone at the keyboard can open any of them.

## What it does

| Capability | Where |
|---|---|
| Workspaces and portfolios: create, edit, value at the last cached price | browser, `/workspace/` |
| Bulk holdings import: paste `TICKER, shares` lines or upload a brokerage positions CSV (Fidelity, Schwab, Vanguard, Robinhood exports work as-is; a `CASH` row sets cash) | browser + `POST /api/holdings/import/` |
| Max-Sharpe optimization with constraints: per-asset cap, position count, universal floor, **max turnover** (cap on how much of what you hold may be sold) | browser + `POST /api/optimize/` + `wolvwealth optimize` |
| Trade list: the buy/sell share deltas from what you hold to the target, sells first so cash is freed before it is spent | in every optimize response |
| Market freshness and symbol validation for agents, including the current 13-week T-bill yield as a risk-free-rate reference | `GET /api/market/` |
| Market data: daily adjusted closes for 2,591 US tickers, 2006 to today, refreshed in the background when stale | `var/` cache, `wolvwealth refresh-data` |
| MCP server: the four use cases above as self-describing tools for Claude Desktop, Claude Code, Cursor and any MCP client; stdio, no port, no extra packages | `wolvwealth mcp`, setup in [MCP.md](MCP.md) |
| Walk-forward backtest (survivorship-biased, and its output says so) | `scripts/backtest.py`, standalone |

The full JSON contract is **[API.md](API.md)**, written to be handed to an agent as-is and contract-tested so it cannot drift from the code (`tests/test_api_contract.py`).

## Quickstart

One command on Linux, macOS, or Windows via WSL. You need Python 3.10+ and git; nothing else (no Node, no venv, no Docker).

```bash
curl -fsSL https://raw.githubusercontent.com/rramboer/WolvWealth/main/install.sh | bash
wolvwealth serve            # http://localhost:8000, opens in your browser
```

Or from a clone: `./install.sh` then `wolvwealth serve`. The installer checks for Python, clones to `~/WolvWealth` if needed, installs editably into your user-level global interpreter so `wolvwealth` and `import wolvwealth` work from any directory, downloads the price history (several minutes; `--no-data` defers it to first start), and adds the command to your PATH. Run it again to update.

Other commands: `wolvwealth optimize` runs an optimization from the shell with no server (see below), `wolvwealth mcp --config` prints what an AI app needs to use WolvWealth as tools (see below), `wolvwealth refresh-data [--full]` pulls prices up to today, `wolvwealth reset-db` erases every workspace, `wolvwealth dump-db` prints them. `python -m wolvwealth ...` is equivalent.

On first start the app creates its local database (seeded with a deletable `demo` workspace) and a per-install session key under gitignored `var/`. If the price history is more than a week old it refreshes in the background, so the app is usable immediately and picks up fresher prices when they land (`WOLVWEALTH_NO_AUTO_REFRESH=1` skips this, e.g. offline).

Then in the browser: open `http://localhost:8000/workspaces/`, create your workspace, add a portfolio with your cash and holdings (use **Import holdings in bulk** for more than a handful), set its **risk-free rate** (a *Use T-bill* button fills in the current Treasury yield; until you set one, 0% is assumed and every result says so), and hit **Optimize**.

## From the shell

The same optimization without a server, for people at a terminal and for agents that run commands:

```bash
wolvwealth optimize --workspace ryan --portfolio long-term --universe top100 --max-positions 10 --max-turnover 0.2
wolvwealth optimize --cash 5000 --holdings "AAPL=10,MSFT=5" --risk-free-rate tbill
wolvwealth optimize --holdings-file ~/Downloads/positions.csv --json      # brokerage export in, API body out
```

Every flag is a field of the API request below, one for one. The report shows the price date, the rate assumed and where it came from, the target allocation, the trades (sells first), and the metrics; `--json` prints exactly the `POST /api/optimize/` response body instead. Held tickers always join the universe, as in the browser, so keeping a position is possible. Errors exit 1 with the message on stderr (or as the API's error object with `--json`). Prices are never refreshed by this command; it says so on stderr when the cache is more than a week old.

## For AI apps (MCP)

Claude Desktop, Claude Code, Cursor, and other MCP clients can use WolvWealth as tools: `market_status`, `list_portfolios`, `optimize`, and `import_holdings`, each described well enough that the model needs no documentation pasted in. It is a **stdio** server: the app starts `wolvwealth mcp` itself as a child process and talks to it over pipes, so there is no port, no URL, nothing to keep running, and nothing another program on the network could reach. It needs no extra packages and works whether or not `wolvwealth serve` is up.

```bash
wolvwealth mcp --config     # prints the JSON for your app's MCP settings, and where to put it
```

The fastest setup is to paste **[MCP.md](MCP.md)** into your assistant and ask it to do it for you; the file is written for exactly that, covers each app's config location, and has the troubleshooting list. The server answers the handshake before loading any data, picks up a refreshed price cache without a restart, and never starts the first price download on a model's behalf (a tool call says to run `wolvwealth refresh-data` instead). Everything it prints goes to stderr, because on stdio a stray line on stdout would end the session.

## The API

No API key, no registration. Reference a saved portfolio by name:

```bash
curl -s -X POST http://localhost:8000/api/optimize/ \
  -H "Content-Type: application/json" \
  -d '{
        "workspace": "ryan",
        "portfolio": "long-term",
        "universe": ["top100"],
        "constraints": {"max_positions": 10, "max_weight": 0.25, "max_turnover": 0.2}
      }'
```

`max_turnover` caps how much of what you hold gets sold (here 20% of the portfolio's value), so a rebalance does not churn the whole account through fees and spreads. Leave it out to let the optimizer start from scratch. Or spell everything out instead of naming a portfolio:

```bash
curl -s -X POST http://localhost:8000/api/optimize/ \
  -H "Content-Type: application/json" \
  -d '{
        "initial_cash": 5000,
        "initial_holdings": {"AAPL": 10, "MSFT": 5},
        "universe": ["top100"],
        "risk_free_rate": 0.04
      }'
```

Either way you get back the target allocation, the **trades to reach it from what you hold**, the metrics, which risk-free rate was assumed and where it came from, and `as_of` — the date of the prices behind every number:

```json
{
  "as_of": "2026-10-29",
  "risk_free_rate": { "value": 0.04, "source": "request" },
  "optimized_portfolio": { "BND": { "shares": 149.0631, "value": 10640.13, "percent_weight": 42.7 } },
  "trades": [
    { "ticker": "AAPL", "action": "sell", "shares": 50.0,     "price": 348.91, "value": 17445.50 },
    { "ticker": "BND",  "action": "buy",  "shares": 149.0631, "price": 71.38,  "value": 10640.12 }
  ],
  "metrics": { "portfolio_value": 24917.5, "sharpe_ratio": 1.24, "num_assets": 4, "turnover": 0.3 }
}
```

Sells are listed first so the cash exists before the buys. An agent can hand this straight to a broker MCP. To load a brokerage export into a workspace first, `POST /api/holdings/import/` takes a `holdings` map or raw `csv` text and can set cash and the risk-free rate in the same call. [API.md](API.md) documents every field, default, and error.

Two things to understand before trusting the numbers:

- **By default your holdings are fully reallocated.** Set `constraints.max_turnover` to cap selling, and include held tickers in `universe` so keeping them is possible (the browser flow adds them automatically). A held ticker missing from the result means "sell it all".
- **Recent listings are excluded until they have a year of prices.** The estimators annualize whatever history exists, so a company listed three weeks ago that rose 15% shows an expected return in the thousands of percent at almost no measured risk, and the optimizer puts the maximum weight there. Aliases skip tickers with fewer than `min_history_days` of prices (default 252, a trading year) and naming one is refused with the count it has; pass a lower `min_history_days` to include it knowingly. `GET /api/market/` lists the excluded names under `short_history`. A held ticker that is too young cannot be kept by the browser, CLI or MCP flows (which add held names to the universe) without lowering the minimum; the API will sell it as a name outside the universe.
- **Prices come from the local cache, not live quotes.** Every number is priced at `as_of`; check it before acting on a trade list.

## How it is built

The package is concentric layers, and the dependency direction is enforced by a test (`tests/test_architecture.py`) that parses every module's imports:

```
web/ (Flask app, API + views),  mcp/ (stdio tools),  __main__.py (CLI)     ← thin adapters
  └── services/     use cases: optimize, holdings import, market data lifecycle
        └── storage/ (SQLite, Parquet caches)  and  market/ (Yahoo downloader)
              └── domain/   the maths: optimizer, trades, MarketData, parsing, errors
                            (no Flask, no SQL, no files, no network)
```

A new feature is a function in `services/` plus a dozen-line adapter per surface (API route, MCP tool, CLI subcommand); the three surfaces call the same service functions, so they cannot drift apart, and everything in `domain/` and `services/` is testable without a server. `import wolvwealth` has no side effects; the database and price cache are created on first use. Paths resolve from the install location, so everything works from any directory.

Points of the design worth knowing:

- **Optimization** is PyPortfolioOpt max-Sharpe (CLARABEL solver) over exponentially weighted expected returns and covariance (500-day span, so the last two years weigh most). The estimator was chosen by walk-forward backtest against the whole-history mean and three other candidates, with an equal-weight portfolio as the hurdle; the table and the caveats are in [CONTRIBUTING.md](CONTRIBUTING.md#the-backtest-and-the-expected-return-estimator). Its name is recorded in the statistics cache, so changing it rebuilds the cache rather than serving returns computed the old way. `max_weight`, `min_universal_weight`, `max_turnover`, and `cash_reserve` (keep a fraction of the portfolio in cash as dry powder; weights stay fractions of the *total*, so the solver's bounds are rescaled by the invested share) are solver constraints; `max_positions` is a cardinality cap, which max-Sharpe cannot take directly, so it is applied by solving once, keeping the N largest names and re-solving over just those under the same constraints — the returned portfolio therefore satisfies every constraint, and the reported metrics describe it. Combinations that cannot be satisfied at all (10% per holding across 8 holdings only invests 80%) are rejected before the solver by `domain/constraints.py`, which reports every conflict at once with the fields involved and a concrete remedy. The risk-free rate resolves as: the request's own value (a number or `"tbill"`), else the stored portfolio's rate, else 0% — flagged in the response so a deliberate zero is distinguishable from the fallback.
- **Repeated requests are cheap.** Solved weights and metrics are memoized per price snapshot, keyed on exactly what reaches the solver (~190 ms cold, under 1 ms repeated). A price refresh builds a new snapshot and drops the memo with it, so stale prices can never serve a cached portfolio. The return/covariance statistics are precomputed once per data refresh for the 500 largest tradeable tickers and sliced per request; larger universes compute their own.
- **Market data** is a Parquet cache under `var/`, refreshed incrementally (`refresh-data`) or fully (`--full`). An incremental refresh re-downloads the last five cached trading days and compares them with the cache; a ticker that disagrees has been re-adjusted for a split or dividend, so its whole history is re-downloaded on the new basis (`--full` is for recovering tickers that were only throttled). Yahoo throttles bulk downloads and returns the same empty column for a throttled ticker as for a delisted one, so the ticker list is never pruned automatically; `MarketData.tradeable` recomputes which tickers are usable on every snapshot, and the refresh reports how many came back empty rather than pretending a short universe is the whole market.
- **Storage** is three SQLite tables — workspaces, portfolios (cash, nullable risk-free rate), holdings — with cascading deletes, every query scoped by workspace, and all SQL in one repository module. Columns added after first release are applied to existing databases on their next connection.
- **Startup never blocks on a refresh.** A stale cache refreshes on a daemon thread while the cached prices serve; the new snapshot swaps in atomically. All cache writes go through a temp file and an atomic rename, so a refresh can run while requests are served. A long-lived process (the web app, an MCP server an AI app keeps open for days) also notices when *another* process replaced the cache and reloads it, so no surface ever serves prices older than the file on disk.
- **No Node at runtime.** The Tailwind CSS build is committed; contributors who change styling rebuild it with `npm run css`.

How to set up a development environment and make changes is in [CONTRIBUTING.md](CONTRIBUTING.md); the rules and invariants for working on the code (human or AI) are in [AGENTS.md](AGENTS.md).

## A note on trust

There is deliberately no authentication. This is a localhost tool for a machine whose users trust each other — workspaces keep portfolios tidy and separate, nothing more. Do not bind it to an interface strangers can reach.

Localhost still gets one real protection: web pages you happen to have open can send requests at `http://localhost:8000`, so the app refuses anything a browser marks as coming from another site, and refuses requests addressed to a hostname that is not this machine (DNS rebinding). Your agent, your terminal, and the app's own pages are unaffected.

## Scope

Deliberately **not** in scope, now or later:

- **Hosted deployment, accounts, authentication, billing.** If this is ever network-exposed, that is a different project.
- **Live quotes per request.** Prices come from the local cache; `as_of` tells you how old they are and the refresh command controls that.
- **Trade execution.** WolvWealth produces target allocations and trade lists. Placing orders is the job of the agent and its broker MCP, never of this app.

## Still to address

Known gaps:

- **A young ticker is excluded, not shrunk.** A ticker under the minimum history is left out of the universe entirely rather than given a tempered estimate. Institutional risk models instead proxy a new listing's risk through its factor exposures (industry, size) until it has its own history, and Bayesian estimators shrink a short sample's mean toward a prior in proportion to how little data it has. Both are cross-sectional, which the per-ticker statistics cache cannot express (see `domain/stats.py`), so the rule here is a seasoning period with an explicit override. A held ticker under the minimum cannot currently be kept at its present size while the rest is optimized; it is either admitted with its noisy statistics or sold.

## License

MIT — see [LICENSE](LICENSE). Copyright 2023-2026 Ryan Ramboer and the WolvWealth contributors.

## Disclaimer

The calculations and information in this project are for educational purposes only and are not investment advice. Mean-variance optimization over historical returns is a model, not a forecast. Past performance does not guarantee future results.

Two specific limitations worth knowing: prices are a local cache, so every allocation is priced at `as_of` rather than a live quote; and the cache holds only companies still listed when it was downloaded, so backtest results are survivorship-biased and flatter than reality.
