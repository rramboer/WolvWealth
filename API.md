# WolvWealth local API

A self-contained description of every endpoint, field and error. It is written to be
pasted into an agent's context whole: nothing here requires reading the source.

- **Base URL:** `http://localhost:8000` (`wolvwealth serve`, or `--port N`)
- **Auth:** none. The server binds `127.0.0.1` only and is never meant to be exposed.
- **Content type:** request and response bodies are JSON. `POST` bodies must be a JSON object.
- **Offline by design.** Prices come from a local cache refreshed on a schedule, *not* live
  quotes. Every optimize response carries `as_of`, the date those prices are from. Check it
  before acting on a trade list.
- **No execution.** This service produces target allocations and trade lists. Placing orders
  is the caller's job (for an agent, usually a broker MCP).

## The usual agent loop

1. `GET /api/market/` — how fresh is the data, and which tickers may I name?
2. *(optional)* `POST /api/holdings/import/` — load a brokerage export into a stored portfolio.
3. `POST /api/optimize/` — get the target allocation **and the trades to reach it**.
4. Place `trades` through a broker, sells first.

Steps 2 and 3 are independent: you can pass holdings inline to `/api/optimize/` and never
store anything.

---

## `GET /api/`

Index of endpoints. Useful as a liveness check.

## `GET /api/market/`

Price-cache freshness and the symbols a request may use.

```json
{
  "as_of": "2026-10-29",
  "age_days": 1,
  "universe_size": 1982,
  "tradeable_size": 1576,
  "universe_aliases": ["top20", "top50", "top100", "top200", "top300", "top400", "top500"],
  "tickers": ["MSFT", "AAPL", "GOOGL", "..."]
}
```

| Field | Meaning |
|---|---|
| `as_of` | Date of the newest price row. Everything downstream is priced at this date |
| `age_days` | Days between `as_of` and today. Refuse to trade on a stale cache if you care |
| `universe_size` | Tickers in the shipped list, including ones no longer trading |
| `tradeable_size` | Tickers with a current price — the ones you may actually name |
| `universe_aliases` | Shorthands accepted in `universe` |
| `tickers` | The full tradeable list, so symbols can be validated without a round trip |

`universe_size` exceeds `tradeable_size` because the ticker list keeps names that were
acquired, renamed or delisted. Those are excluded from aliases and rejected if named.

---

## `POST /api/optimize/`

Maximum-Sharpe allocation over a universe, plus the trades to get there from what you hold.

### Request

Every field is optional, but the portfolio must be worth more than zero — supply
`initial_cash`, `initial_holdings`, or a stored `portfolio`.

| Field | Type | Meaning | Default |
|---|---|---|---|
| `initial_cash` | number ≥ 0 | Cash to invest, USD | `0` |
| `initial_holdings` | object | Ticker → share count. Valued at the `as_of` close and pooled with cash | `{}` |
| `workspace` + `portfolio` | string | Load cash and holdings from a stored portfolio instead. `workspace` falls back to the browser session. Explicit `initial_cash`/`initial_holdings` still win | — |
| `universe` | array | Tickers and/or aliases to allocate across | `top500` |
| `constraints.max_weight` | number 0–1 | Per-asset cap | `1` |
| `constraints.max_positions` | integer ≥ 1 | Keep the N largest weights, then renormalize | unlimited |
| `constraints.min_universal_weight` | number 0–1 | Floor applied to **every** asset in the universe | `0` |
| `exclude_metrics` | boolean | Omit the `metrics` block | `false` |
| `exclude_trades` | boolean | Omit the `trades` block | `false` |

`max_positions × max_weight` must be at least 1, or the request is infeasible by
construction and is rejected.

```json
{
  "initial_cash": 5000,
  "initial_holdings": { "AAPL": 50, "F": 200 },
  "universe": ["top50"],
  "constraints": { "max_positions": 4 }
}
```

### Response

```json
{
  "as_of": "2026-10-29",
  "optimized_portfolio": {
    "BND":  { "shares": 149.0631, "value": 10640.13, "percent_weight": 42.7 },
    "MA":   { "shares": 8.7415,   "value": 5556.96,  "percent_weight": 22.3 },
    "CVX":  { "shares": 20.9378,  "value": 4846.26,  "percent_weight": 19.45 },
    "COST": { "shares": 3.8894,   "value": 3874.15,  "percent_weight": 15.55 }
  },
  "trades": [
    { "ticker": "AAPL", "action": "sell", "shares": 50.0,     "price": 348.91, "value": 17445.50 },
    { "ticker": "F",    "action": "sell", "shares": 200.0,    "price": 12.36,  "value": 2472.00 },
    { "ticker": "BND",  "action": "buy",  "shares": 149.0631, "price": 71.38,  "value": 10640.12 },
    { "ticker": "MA",   "action": "buy",  "shares": 8.7415,   "price": 635.70, "value": 5556.97 },
    { "ticker": "CVX",  "action": "buy",  "shares": 20.9378,  "price": 231.46, "value": 4846.26 },
    { "ticker": "COST", "action": "buy",  "shares": 3.8894,   "price": 996.08, "value": 3874.15 }
  ],
  "metrics": {
    "portfolio_value": 24917.5,
    "expected_annual_return": 0.124,
    "annual_volatility": 0.084,
    "sharpe_ratio": 1.24,
    "num_assets": 4
  }
}
```

**`as_of`** — the price date every number above was computed from.

**`optimized_portfolio`** — the target, keyed by ticker. `shares` is rounded to 4 decimals,
`value` to cents, `percent_weight` is 0–100. A ticker you hold that is *absent* here should
be sold off entirely; the `trades` block already says so.

**`trades`** — what to do, which is usually the only part an agent needs:

| Field | Meaning |
|---|---|
| `ticker` | Symbol to trade |
| `action` | `"buy"` or `"sell"` |
| `shares` | Always positive; `action` carries the direction |
| `price` | The `as_of` close this plan assumed. Compare against a live quote before trusting the value |
| `value` | `shares × price`, USD |

**Sells are listed first**, each group ordered by descending value, so executing the list in
order frees cash before spending it. A position that does not change produces no entry, as
does a difference too small to show at 4 decimals. Share counts are fractional; round them if
your broker requires whole shares, accepting the small drift that introduces.

**`metrics`** — figures describing the portfolio actually returned, including the
`max_positions` truncation:

| Field | Meaning |
|---|---|
| `portfolio_value` | Total being allocated: cash + holdings valued at `as_of`, USD |
| `expected_annual_return` | Annualized expected return as a fraction (`0.124` is 12.4%) |
| `annual_volatility` | Annualized standard deviation, same units |
| `sharpe_ratio` | `(expected_annual_return − 0.02) / annual_volatility`, using a 2% risk-free rate. `null` when volatility is zero (degenerate history) rather than dividing by zero |
| `num_assets` | Positions in the returned portfolio |

### Worth knowing before trading on this

- **Your holdings are fully reallocated.** The optimizer does not try to keep what you own. To
  let it keep a position, include that ticker in `universe`.
- **Mean-variance optimization over historical returns is a model, not a forecast.**
- **Repeated identical requests are served from a memo cache** keyed on the universe,
  constraints and the price snapshot, so polling is cheap (sub-millisecond). The answer only
  changes when the cache refreshes, which `as_of` tells you.

---

## `POST /api/holdings/import/`

Bulk-load holdings into a stored portfolio — how a brokerage export becomes something
`/api/optimize/` can reference by name.

| Field | Type | Meaning | Default |
|---|---|---|---|
| `workspace` | string | Target workspace; falls back to the browser session | session |
| `portfolio` | string | Target portfolio name | **required** |
| `create_portfolio` | boolean | Create it if missing | `false` |
| `holdings` | object | Ticker → shares | one of these |
| `csv` | string | Raw brokerage CSV or pasted `TICKER, shares` lines, same parser the browser uses | two is required |
| `cash` | number | Set the portfolio's cash. A `CASH` row in `csv` does the same | unchanged |
| `mode` | `"merge"` \| `"replace"` | `merge` upserts each ticker (0 removes); `replace` drops existing holdings first | `"merge"` |
| `skip_unknown` | boolean | Drop unrecognized tickers instead of failing | `false` |

Pass `holdings` **or** `csv`, never both. The write is all-or-nothing: if anything is wrong,
nothing is stored and the error lists every problem at once.

```json
{ "workspace": "ryan", "portfolio": "long-term", "create_portfolio": true,
  "mode": "replace", "holdings": { "AAPL": 10, "MSFT": 5.5 }, "cash": 5000 }
```

Returns the portfolio's final state:

```json
{ "workspace": "ryan", "portfolio": "long-term", "mode": "replace",
  "imported": 2, "skipped_unknown": [], "ignored_lines": [],
  "cash": 5000, "holdings": { "AAPL": 10, "MSFT": 5.5 } }
```

## `GET /api/workspaces/`

Every workspace with its portfolios and holdings.

```json
{ "workspaces": [ { "name": "demo", "created": "2026-10-29 12:00:00",
    "portfolios": [ { "name": "main", "cash": 5000, "holdings": { "AAPL": 10 } } ] } ] }
```

---

## Errors

Every failure is JSON with the same shape and a non-2xx status. There is no HTML error page
and no bare stack trace.

```json
{ "error": { "message": "Invalid symbol in universe: FAKETICKER.", "status_code": 400 } }
```

| Status | When |
|---|---|
| `400` | Malformed JSON, a field of the wrong type, an unknown or delisted ticker, an infeasible constraint set, a zero-value portfolio, or a solver failure |
| `404` | A named workspace or portfolio does not exist |

Bulk-import failures add `error.payload` carrying `errors` (per-line messages) and
`unknown_tickers`.

Messages name the offending field or symbol and are safe to surface verbatim. Two to expect
when working from a real brokerage export:

- `Invalid symbol in universe: XYZ.` — not in the ticker list at all.
- `No current price for PXD: delisted, renamed, or missed by the last price refresh.` — the
  symbol is in the ticker list but has no price at the end of the cached history. Usually it
  stopped trading (acquired, renamed), so map it to its successor symbol or drop it. It can
  also mean the last download was rate-limited for that symbol, which a
  `wolvwealth refresh-data --full` fixes.
