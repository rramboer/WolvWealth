![WolvWealth Logo](/wolvwealth/web/static/img/logo.png)

# WolvWealth

**A self-hosted portfolio optimizer. Give it your cash and holdings, get back a maximum-Sharpe allocation.**

WolvWealth started as a hackathon-style proof of concept: a hosted Flask web app with user accounts and tiered API keys. We are turning it into something **each of us runs on our own machine against our own portfolios**. No hosted service, no logins, no paid tiers.

## Why local-first

Two reasons we abandoned the cloud model:

1. **Hosting user accounts, auth, and other people's financial data was not viable** for a project this size. Keeping everything on your own machine removes that whole problem class: your holdings live in a local SQLite file that never leaves your disk.
2. **AI agents work best against local tools.** With broker MCP servers now available (e.g. the Robinhood MCP, which lets an agent place real trades), the intended workflow is: your agent calls the local WolvWealth API (`POST http://localhost:8000/api/optimize/` — plain JSON, no auth), reads back the target allocation **and the trade list the app already computed for it**, and places those trades through the broker MCP. That only works cleanly if the optimizer runs on the same machine as the agent, with no accounts or API keys in the way.

This is also why the Python packages are installed into the **global interpreter** (no venv): the optimizer and its API must be runnable from anywhere on the device — by you at a shell, or by an agent spawned from any working directory — without activating an environment first.

## How it works now: workspaces

The accounts system is gone. In its place are **local workspaces** — one per person using the machine, no passwords. Each workspace holds any number of named portfolios, and each portfolio is a cash balance plus your share counts per ticker.

So on the family computer: you keep `long-term`, `short-term`, and `lottery-tickets` in your workspace, and your brother keeps his four portfolios in his, without the two of you tripping over each other's data. Switching is one click — it's organizational separation, not security. Everything stays in a local SQLite file under gitignored `var/`, so nobody's holdings ever end up in the repo.

The current state of the code, with diagrams of every workflow and an honest list of what is still rough, is in **[ARCHITECTURE.md](ARCHITECTURE.md)**. How to set up, run, and make changes is in **[CONTRIBUTING.md](CONTRIBUTING.md)**.

## What works right now

| Capability | Status |
|---|---|
| Workspaces: create, open, switch, delete from the browser | Working |
| Portfolios per workspace: cash, holdings, valuations at last cached price | Working |
| Bulk holdings import: paste `TICKER, shares` lines or upload a brokerage positions CSV | Working |
| Optimize a saved portfolio from the browser, results page with metrics and target allocation | Working |
| `POST /api/optimize/`: optimize by stored portfolio name or explicit holdings, no auth header; returns the target allocation, the trades to reach it, and the price date | Working |
| `GET /api/market/`: price freshness and the tradeable ticker list, so an agent can validate symbols up front | Working |
| [API.md](API.md): the whole JSON contract, written to paste into an agent's context | Working |
| `POST /api/holdings/import/`: load a holdings map or CSV text into a stored portfolio | Working |
| Market data: 2006-01-03 to today, adjusted close, 1,982 tickers | Cached in gitignored `var/` (built or migrated on first run). Auto-refreshed on startup when stale; `python -m wolvwealth refresh-data` on demand |
| Walk-forward backtest (`scripts/backtest.py`) | Runs standalone from the command line, reuses the app's optimizer |
| One-command install (`install.sh`) and a `wolvwealth serve` command; no Node or venv at runtime | Working |

## Quickstart

One command on Linux, macOS, or Windows via WSL. You need Python 3.10+ and git; nothing else (no Node, no venv, no Docker).

```bash
curl -fsSL https://raw.githubusercontent.com/rramboer/WolvWealth/main/install.sh | bash
wolvwealth serve            # http://localhost:8000, opens in your browser
```

Or from a clone: `./install.sh` then `wolvwealth serve`. The installer checks for Python, clones to `~/WolvWealth` if needed, installs the package into your user-level global interpreter so `wolvwealth` and `import wolvwealth` work from any directory, downloads the price history (several minutes; `--no-data` to defer it to first start), and adds the command to your PATH. Run it again to update.

Other commands: `wolvwealth refresh-data [--full]` pulls prices up to today, `wolvwealth reset-db` erases every workspace, `wolvwealth dump-db` prints them. `python -m wolvwealth ...` is equivalent.

The optimizer maximizes the Sharpe ratio against a risk-free rate of 4% by default, roughly the current T-bill yield. Change it for the whole install with `wolvwealth serve --risk-free-rate 0.045` or `WOLVWEALTH_RISK_FREE_RATE=0.045`, or per run from the form and the API's `risk_free_rate` field. It is an input rather than a constant because the right value moves with the market.

On first start the app creates its own local database (seeded with a deletable `demo` workspace) and a per-install session key under `var/`. If the price history is more than a week old it refreshes in the background, so the app is usable immediately and picks up the fresher prices when they land (set `WOLVWEALTH_NO_AUTO_REFRESH=1` to skip, e.g. offline).

Then in the browser: open `http://localhost:8000/workspaces/`, create your workspace, add a portfolio with your cash and holdings, and hit **Optimize**. For more than a handful of positions, open **Import holdings in bulk** on the portfolio card and paste `TICKER, shares` lines or upload the positions CSV your brokerage exports (Fidelity, Schwab, Vanguard, Robinhood exports work as-is; a `CASH` row sets your cash).

## Try the API

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

`max_turnover` caps how much of what you hold gets sold (here 20% of the portfolio's value), so a
rebalance does not churn the whole account through fees and spreads. Leave it out to let the
optimizer start from scratch.

Or spell everything out:

```bash
curl -s -X POST http://localhost:8000/api/optimize/ \
  -H "Content-Type: application/json" \
  -d '{
        "initial_cash": 5000,
        "initial_holdings": {"AAPL": 10, "MSFT": 5},
        "universe": ["top100"],
        "constraints": {"max_positions": 10}
      }'
```

Either way you get back the target allocation, the **trades to reach it from what you hold**, and `as_of` — the date of the prices behind every number:

```json
{
  "as_of": "2026-10-29",
  "optimized_portfolio": { "BND": { "shares": 149.0631, "value": 10640.13, "percent_weight": 42.7 } },
  "trades": [
    { "ticker": "AAPL", "action": "sell", "shares": 50.0,     "price": 348.91, "value": 17445.50 },
    { "ticker": "BND",  "action": "buy",  "shares": 149.0631, "price": 71.38,  "value": 10640.12 }
  ],
  "metrics": { "portfolio_value": 24917.5, "sharpe_ratio": 1.24, "num_assets": 4 }
}
```

Sells are listed first so the cash exists before the buys. An agent can hand this straight to a broker MCP.

Load a brokerage export into a workspace first (this is what an agent does before optimizing). Either a `holdings` map or the raw `csv` text, `mode` is `merge` (default) or `replace`:

```bash
curl -s -X POST http://localhost:8000/api/holdings/import/ \
  -H "Content-Type: application/json" \
  -d '{
        "workspace": "ryan",
        "portfolio": "long-term",
        "create_portfolio": true,
        "mode": "replace",
        "holdings": {"AAPL": 10, "MSFT": 5},
        "cash": 5000
      }'
```

Response shape (abbreviated, numbers illustrative):

```json
{
  "optimized_portfolio": {
    "AAPL": {"percent_weight": 13.78, "shares": 7.33, "value": 1378.49},
    "...": {}
  },
  "metrics": {
    "portfolio_value": 10000.0,
    "expected_annual_return": 0.277,
    "annual_volatility": 0.135,
    "sharpe_ratio": 1.9,
    "num_assets": 5
  }
}
```

Two things to understand before trusting the numbers:

- **Your holdings are valued and then fully reallocated.** The optimizer does not try to keep what you own. The browser flow adds your held tickers to the universe so keeping them is possible; a held ticker missing from the result means "sell it all".
- **Prices come from the local cache, not live quotes.** Share counts and dollar values use the last row of `var/historical_prices.parquet`; the pages and the API's `as_of` show which date that is. The startup refresh keeps it within about a week of today, in the background. [ARCHITECTURE.md §8](ARCHITECTURE.md#8-workflow-market-data) has the details.

The full request and response contract is **[API.md](API.md)**, written so it can be handed to an agent as-is; [ARCHITECTURE.md §7](ARCHITECTURE.md#7-workflow-optimize-a-portfolio-the-core-feature) covers how it is built.

Responses include a `trades` block — the buy/sell share deltas from what you hold to the target, sells first — so an agent can pass orders straight to a broker MCP, and `as_of`, the date of the prices behind every number.

## A note on trust

There is deliberately no authentication. This is a localhost tool for a machine whose users trust each other — workspaces keep people's portfolios tidy and separate, nothing more. Do not bind it to an interface strangers can reach.

Localhost still gets one real protection: web pages you happen to have open can send requests at `http://localhost:8000`, so the app refuses anything a browser marks as coming from another site, and refuses requests addressed to a hostname that is not this machine (DNS rebinding). Your agent, your terminal, and the app's own pages are unaffected.

## Scope

The app is where it set out to be: multiple people on one device each keep their portfolios in a local workspace, optimize them on that machine, and an agent can drive the whole loop through the local API and place the trades through a broker MCP. ARCHITECTURE.md has the design; `git log` has how it got here.

Deliberately **not** in scope, now or later:

- **Hosted deployment, accounts, authentication, billing.** If this is ever network-exposed again, that is a different project.
- **Live quotes per request.** Prices come from the local cache; `as_of` tells you how old they are and the refresh command controls that.
- **Trade execution.** WolvWealth produces target allocations and trade lists. Placing orders is the job of the agent and its broker MCP, never of this app.

## License

MIT — see [LICENSE](LICENSE). Copyright 2023-2026 Ryan Ramboer and the WolvWealth contributors.

## Disclaimer

The calculations and information in this project are for educational purposes only and are not investment advice. Mean-variance optimization over historical returns is a model, not a forecast. Past performance does not guarantee future results.

Two specific limitations worth knowing: prices are a local cache, so every allocation is priced at `as_of` rather than a live quote; and the cache holds only companies still listed when it was downloaded, so backtest results are survivorship-biased and flatter than reality.
