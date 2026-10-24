![WolvWealth Logo](/wolvwealth/static/img/logo.png)

# WolvWealth

**A self-hosted portfolio optimizer. Give it your cash and holdings, get back a maximum-Sharpe allocation.**

WolvWealth started as a hackathon-style proof of concept: a hosted Flask web app with user accounts and tiered API keys. We are turning it into something **each of us runs on our own machine against our own portfolios**. No hosted service, no logins, no paid tiers.

## Why local-first

Two reasons we abandoned the cloud model:

1. **Hosting user accounts, auth, and other people's financial data was not viable** for a project this size. Keeping everything on your own machine removes that whole problem class: your holdings live in a local SQLite file that never leaves your disk.
2. **AI agents work best against local tools.** With broker MCP servers now available (e.g. the Robinhood MCP, which lets an agent place real trades), the intended workflow is: your agent calls the local WolvWealth API (`POST http://localhost:8000/api/optimize/` — plain JSON, no auth), reads back the target allocation, diffs it against your current holdings, and places the resulting trades through the broker MCP. That only works cleanly if the optimizer runs on the same machine as the agent, with no accounts or API keys in the way.

This is also why the Python packages are installed into the **global interpreter** (no venv): the optimizer and its API must be runnable from anywhere on the device — by you at a shell, or by an agent spawned from any working directory — without activating an environment first.

The remaining work to fully get there is tracked in **[MIGRATION.md](MIGRATION.md)**.

## How it works now: workspaces

The accounts system is gone. In its place are **local workspaces** — one per person using the machine, no passwords. Each workspace holds any number of named portfolios, and each portfolio is a cash balance plus your share counts per ticker.

So on the family computer: you keep `long-term`, `short-term`, and `lottery-tickets` in your workspace, and your brother keeps his four portfolios in his, without the two of you tripping over each other's data. Switching is one click — it's organizational separation, not security. Everything stays in a local SQLite file under gitignored `var/`, so nobody's holdings ever end up in the repo.

The current state of the code, with diagrams of every workflow and an honest list of what is still rough, is in **[ARCHITECTURE.md](ARCHITECTURE.md)**. How to set up, run, and make changes is in **[CONTRIBUTING.md](CONTRIBUTING.md)**.

## What works right now

| Capability | Status |
|---|---|
| Workspaces: create, open, switch, delete from the browser | Working |
| Portfolios per workspace: cash, holdings, valuations at last cached price | Working |
| Optimize a saved portfolio from the browser, results page with metrics and target allocation | Working |
| `POST /api/optimize/`: optimize by stored portfolio name or explicit holdings, no auth header | Working |
| Market data: 2006-01-03 to **2024-01-30**, adjusted close, 1,982 tickers | Loaded from a committed CSV. Stale. Refresh is a manual Python-shell step |
| Walk-forward backtest (`backtest.py`) | Runs standalone from the command line |
| React optimizer page (`/optimizer/`) | Empty shell, superseded by the server-rendered flow |

## Quickstart

Everything runs from the repository root with the global Python interpreter.

```bash
git clone <repo-url> WolvWealth
cd WolvWealth
pip install -r requirements.txt
npm install                 # only needed for the website styling and JS
./bin/run dev               # http://localhost:8000
```

If you only want the API and do not have Node installed:

```bash
flask --app wolvwealth --debug run --port 8000
```

On first run the app creates its own local database (seeded with a deletable `demo` workspace) and a per-install session key under `var/`.

Then in the browser: open `http://localhost:8000/workspaces/`, create your workspace, add a portfolio with your cash and holdings, and hit **Optimize**.

## Try the API

No API key, no registration. Reference a saved portfolio by name:

```bash
curl -s -X POST http://localhost:8000/api/optimize/ \
  -H "Content-Type: application/json" \
  -d '{
        "workspace": "ryan",
        "portfolio": "long-term",
        "universe": ["top100"],
        "constraints": {"max_positions": 10, "max_weight": 0.25}
      }'
```

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
- **Prices are from the last row of the CSV**, currently 2024-01-30. Share counts and dollar values are based on those prices, not live quotes. The pages show the price date.

The full request schema is in [ARCHITECTURE.md §7](ARCHITECTURE.md#7-workflow-optimize-a-portfolio-the-core-feature).

## A note on trust

There is deliberately no authentication. This is a localhost tool for a machine whose users trust each other — workspaces keep people's portfolios tidy and separate, nothing more. Do not bind it to an interface strangers can reach.

## Roadmap

The end state: a locally runnable app where multiple people on one device each keep their own portfolios in a workspace, optimize them on that machine, and optionally have an AI agent run the optimizer and place the resulting trades through a broker MCP (e.g. Robinhood). The full plan, with what's done and what's left in order, lives in **[MIGRATION.md](MIGRATION.md)**.

## Disclaimer

The calculations and information in this project are for educational purposes only and are not investment advice. Mean-variance optimization over historical returns is a model, not a forecast. Past performance does not guarantee future results.
