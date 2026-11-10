# WolvWealth local API

A self-contained description of every endpoint, field and error. It is written to be
pasted into an agent's context whole: nothing here requires reading the source.

- **Base URL:** `http://localhost:8000` (`wolvwealth serve`, or `--port N`)
- **Auth:** none. The server binds `127.0.0.1` only and is never meant to be exposed.
- **Cross-site requests are refused (403).** Requests must be addressed to `localhost`/`127.0.0.1`/`[::1]`, and requests a browser marks as cross-site (`Sec-Fetch-Site: cross-site`, or a non-loopback `Origin` on a POST) are rejected. Plain HTTP clients send none of those headers, so agents and curl are unaffected; this only shuts out other websites driving the API from inside someone's browser.
- **Content type:** request and response bodies are JSON. `POST` bodies must be a JSON object.
- **Offline by design.** Prices come from a local cache refreshed on a schedule, *not* live
  quotes. Every optimize response carries `as_of`, the date those prices are from. Check it
  before acting on a trade list.
- **No execution.** This service produces target allocations and trade lists. Placing orders
  is the caller's job (for an agent, usually a broker MCP).
- **No server needed for one-off runs.** `wolvwealth optimize --json ...` prints the same body as
  `POST /api/optimize/`, with one flag per request field (`wolvwealth optimize --help`). Useful for
  agents that run shell commands rather than make HTTP requests.
- **MCP clients get the same operations as tools.** `wolvwealth mcp` serves `market_status`,
  `list_portfolios`, `optimize` and `import_holdings` over stdio, with input schemas that mirror the
  request bodies below and results identical to the responses. Setup is in [MCP.md](MCP.md).

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
  "universe_size": 2591,
  "tradeable_size": 1576,
  "universe_aliases": ["top20", "top50", "top100", "top200", "top300", "top400", "top500"],
  "min_history_days": 252,
  "short_history": {"SPCX": 23},
  "tickers": ["MSFT", "AAPL", "GOOGL", "..."],
  "tbill_rate": 0.0412,
  "tbill_as_of": "2026-10-29"
}
```

| Field | Meaning |
|---|---|
| `as_of` | Date of the newest price row. Everything downstream is priced at this date |
| `age_days` | Days between `as_of` and today. Refuse to trade on a stale cache if you care |
| `universe_size` | Tickers in the shipped list, including ones no longer trading |
| `tradeable_size` | Tickers with a current price — the ones you may actually name |
| `universe_aliases` | Shorthands accepted in `universe` |
| `min_history_days` | Trading days of price history a ticker needs before a universe includes it, unless the request lowers it (see `POST /api/optimize/`) |
| `short_history` | Present only when some tradeable tickers have fewer than `min_history_days` of prices: `{ticker: days}`. Aliases skip them and naming one is refused unless the request lowers `min_history_days`. They can still be held and valued |
| `stale_prices` | Present only when some tradeable tickers' newest close predates `as_of`: `{ticker: {"as_of", "trading_days_stale"}}`. Yahoo throttles individual symbols, so a few names routinely miss a day; their last close is carried forward (up to 10 trading days) rather than the ticker being treated as unpriced, and this field is the disclosure. Past 10 trading days a ticker drops out of `tickers` entirely |
| `tickers` | The full tradeable list, so symbols can be validated without a round trip. Includes the `short_history` names |
| `tbill_rate` | 13-week US Treasury bill yield as a fraction, fetched from Yahoo at the last price refresh. A reference for choosing a `risk_free_rate`; the app never applies it on its own. `null` until the first refresh |
| `tbill_as_of` | Date of that yield, or `null` |
| `price_exclusions` | Present only when some tickers are **still tradeable** but part of their price history is not used: `{ticker: {"reason", "detected", "scope": "partial", "untrusted_before"?, "masked_days"?}}`. This is the ordinary outcome of the plausibility check, not an alarm — a one-day print that spiked and came straight back is dropped on its own (`masked_days`), and a move the price *stayed* at means only the closes before it go unused (`untrusted_before`), since the history across such a jump is a poor basis for estimating return and risk either way. These names allocate and trade normally; a deep truncation may leave one under `min_history_days`, in which case it appears in `short_history` too. If a flagged move really happened, `wolvwealth accept-price TICKER DATE` restores the history permanently |
| `quarantined` | Present only when some tickers cannot be used at all: their history has too few believable closes left to estimate anything from, or the full history a split/dividend made necessary could not be re-downloaded: `{ticker: {"reason", "detected", "scope": "ticker"}}`. They cannot be bought (naming one in a `universe` is refused), and a held position in one is kept at its current size, valued at the most recent close that could be trusted (for a failed split/dividend re-download that is a fresh close on the current adjustment basis, never the superseded cached one), and reported under `unoptimized` with reason `quarantined` rather than failing the request. The exclusion lifts when a refresh (usually `wolvwealth refresh-data --full`) downloads a believable history. Check this before acting on trades, especially in unattended runs |
| `readjusted` | Present only when a refresh in the last 30 days detected that a ticker's adjustment basis changed (a split or dividend rescales the entire served history) and re-downloaded it whole: `{ticker: {"detected", "outcome"}}`. `outcome` says whether the history was replaced on the new basis (every cached number for that ticker changed, legitimately) or could not be re-downloaded (the ticker is then under `quarantined`). Dated notes about events, not current state: exclusions in force are always under `quarantined` |

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
| `initial_holdings` | object | Ticker → share count. Valued at the `as_of` close and pooled with cash. A ticker not quoted recently is still valued, at its last close, and kept rather than sold (see `stale_prices` and `unoptimized`); a quarantined one is likewise kept, valued at its last believable close; only one that has never had a price at all is an error | `{}` |
| `workspace` + `portfolio` | string | Load cash and holdings from a stored portfolio instead. `workspace` falls back to the browser session. Explicit `initial_cash`/`initial_holdings` still win | — |
| `universe` | array | Tickers and/or aliases to allocate across. Aliases skip tickers with fewer than `min_history_days` of prices; naming one is refused with the days it has and the override that would admit it | `top500` |
| `min_history_days` | integer ≥ 2 | Trading days of price history a ticker needs to be in the universe. The estimators annualize whatever history exists, so a company listed three weeks ago that rose 15% shows an expected return in the thousands of percent at almost no measured risk, and the solver puts the maximum weight there. A **held** ticker under the minimum is neither sized nor sold: it is kept at its current size, the rest is optimized around it, and the response lists it under `unoptimized`. Lower this only knowingly; `GET /api/market/` lists the excluded names under `short_history` | `252` (one trading year) |
| `constraints.max_weight` | number 0–1 | Per-asset cap | `1` |
| `constraints.max_positions` | integer ≥ 1 | Largest number of holdings. Applied by solving, keeping the N largest names, and re-solving over those under the same constraints | unlimited |
| `constraints.min_universal_weight` | number 0–1 | Floor applied to **every** asset in the universe | `0` |
| `constraints.max_turnover` | number 0–1 | Largest fraction of the portfolio's value the plan may **sell**. `0` keeps every holding and only invests cash; `1` is no limit | `1` |
| `constraints.cash_reserve` | number 0–1 (exclusive) | Fraction of the portfolio's value to leave in cash, uninvested — dry powder for later opportunities. `0.15` invests 85% and reports the other 15% in the response's `cash` block | `0` |
| `risk_free_rate` | number −1 to 1, or `"tbill"` | Annual yield the idle money earns, as a fraction (`0.04` is 4%). The Sharpe ratio is maximized and reported against it. `"tbill"` means the cached 13-week Treasury yield (see `/api/market/`) | the rate stored on the referenced `portfolio`, else `0` with a `note` in the response |
| `exclude_metrics` | boolean | Omit the `metrics` block | `false` |
| `exclude_trades` | boolean | Omit the `trades` block | `false` |

### Infeasible constraint sets

Constraints that are each individually valid can still be impossible together, and those
requests are rejected before the solver runs rather than answered with a portfolio that
quietly breaks one of them. The checks:

| Rule | Why |
|---|---|
| `max_weight` × the number of holdings allowed ≥ 1 − `cash_reserve` | Otherwise the weights cannot cover everything to be invested. The holding count is `max_positions`, or the universe size when it is unset |
| `min_universal_weight` × universe size ≤ 1 − `cash_reserve` | The floor applies to **every** universe ticker, so a large universe (or a large reserve) overfills the portfolio |
| `min_universal_weight` = 0 when `max_positions` < universe size | A positive floor requires every universe ticker to be held, so the holding count cannot also be capped below it |
| `min_universal_weight` ≤ `max_weight` | No single weight can be under the floor and over the cap |
| `max_turnover` ≥ the selling the other inputs force | A held ticker outside `universe`, a position already above `max_weight`, a `max_positions` cap that drops held names, or a `cash_reserve` above the cash already held (the shortfall can only come from sales). Money in `unoptimized` positions is not cash and cannot be sold, so it counts toward neither |
| `cash_reserve` + the `unoptimized` positions ≤ 1 | Both are fractions of the whole portfolio the optimizer may not touch, and a kept position cannot be sold to fund the reserve, so together they must leave the reserve's cash actually obtainable |

**Every** violated rule is reported in one response, so a contradictory body takes one round
trip to diagnose rather than one per conflict. The message names the fields and a remedy, and
`error.payload.conflicts` carries the same structurally:

```json
{
  "error": {
    "message": "Infeasible. max_positions 8 holdings at max_weight 0.1 each allocates at most 80% of the portfolio, leaving 20% with nowhere to go. Raise max_positions to at least 10, or max_weight to at least 0.13.",
    "status_code": 400,
    "payload": {
      "conflicts": [
        {
          "fields": ["constraints.max_positions", "constraints.max_weight"],
          "reason": "max_positions 8 holdings at max_weight 0.1 each allocates at most 80% of the portfolio, leaving 20% with nowhere to go.",
          "remedy": "Raise max_positions to at least 10, or max_weight to at least 0.13."
        }
      ]
    }
  }
}
```

An agent should apply a `remedy` and retry rather than dropping the constraint, and the
suggested values are chosen to be feasible as written.

### How `cash_reserve` interacts with everything else

Every weight in a request and a response is a fraction of the **total** portfolio value,
the reserve included: `max_weight: 0.1` with `cash_reserve: 0.3` means no position exceeds
10% of the portfolio, so the invested 70% needs at least seven names. The composition of
the invested part is the max-Sharpe portfolio either way (mixing it with cash moves along
the capital allocation line), which also means the reported `sharpe_ratio` does not depend
on the reserve — `expected_annual_return` counts the reserve as earning `risk_free_rate`,
and `annual_volatility` shrinks by the invested fraction. A reserve can make an otherwise
impossible constraint set feasible (`max_weight: 0.1` with `max_positions: 8` works at
`cash_reserve: 0.2`, since the weights only have to cover 80%), and it can force sales: a
fully invested portfolio asked for a 20% reserve must sell 20%, which counts against
`max_turnover`.

`max_turnover` is a hard constraint inside the solver, not a penalty: the result is the
best Sharpe ratio reachable while selling no more than that share of what you hold, valued
at the `as_of` close against cash plus holdings. Buying with cash is not counted, so a
portfolio that is half cash can be fully invested at `max_turnover: 0`. Two things force
sales no limit can prevent, and the request is rejected with a message naming them when the
limit is below what they require: a held ticker that is not in `universe` (it cannot be
kept), and a position already above `max_weight`. A `max_positions` cap that would have to
drop held positions is checked against the limit the same way, so the returned `turnover`
never exceeds `max_turnover`. A held ticker under `min_history_days` is the exception to the
first rule: it is kept as held whether or not `universe` names it, so it forces no sale.

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
  "risk_free_rate": { "value": 0.0, "source": "default", "note": "No risk-free rate was given, so 0% was assumed: ..." },
  "optimized_portfolio": {
    "BND":  { "shares": 149.0631, "value": 10640.13, "percent_weight": 42.7 },
    "MA":   { "shares": 8.7415,   "value": 5556.96,  "percent_weight": 22.3 },
    "CVX":  { "shares": 20.9378,  "value": 4846.26,  "percent_weight": 19.45 },
    "COST": { "shares": 3.8894,   "value": 3874.15,  "percent_weight": 15.55 }
  },
  "cash": { "value": 0, "percent_weight": 0 },
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
    "sharpe_ratio": 1.48,
    "num_assets": 4,
    "turnover": 0.7993
  }
}
```

**`as_of`** — the price date every number above was computed from.

**`risk_free_rate`** — the rate this solve assumed, always present (even with `exclude_metrics`):

| Field | Meaning |
|---|---|
| `value` | The annual rate used, as a fraction |
| `source` | `"request"` (you passed a number), `"tbill"` (you passed `"tbill"`), `"portfolio"` (read from the stored portfolio), or `"default"` (nothing was given, so `0`) |
| `note` | Present only when `source` is `"default"`: a reminder that 0% was assumed and how to set a real rate |

An agent should treat `"default"` as a prompt to ask its user where the cash sits, or to set a rate on the portfolio via `/api/holdings/import/`, rather than reporting a Sharpe ratio measured against nothing.

**`optimized_portfolio`** — the target, keyed by ticker. `shares` is rounded to 4 decimals,
`value` to cents, `percent_weight` is 0–100. A ticker you hold that is *absent* here should
be sold off entirely; the `trades` block already says so.

**`cash`** — the uninvested part of the plan, always present: `value` in USD and
`percent_weight` 0–100. Usually it is exactly the `constraints.cash_reserve`, and zero
without one; it also carries any cash that positions kept as held (`unoptimized`) leave over
but that is too small a fraction to allocate. The ticker values plus this line account for
the whole `portfolio_value`, so reconcile against both rather than treating the gap as
rounding error.

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

**`stale_prices`** — present only when some ticker in the result was priced from a close older
than `as_of`: `{ticker: {"as_of", "trading_days_stale"}}`. Yahoo throttles individual symbols,
so a snapshot routinely holds a few names that missed a day; the last close is carried forward
(up to 10 trading days) so one throttled symbol cannot fail the whole request, and this field
says which prices that applies to and how old they are. `trading_days_stale` is `null` for a
held position priced from beyond the window — see `unoptimized`.

**`unoptimized`** — present only when some held ticker cannot be sized: it has fewer than
`min_history_days` of prices, no close in the last 10 trading days, or it is quarantined. Such
a position is kept at exactly its current size (it appears in `optimized_portfolio` with the
shares held and produces no trade), the rest of the money is optimized around it, and it is
left out of `metrics` — though its value *is* included in `portfolio_value`, since the shares
are genuinely owned. `positions` maps each ticker to a `reason` (`"short_history"`,
`"no_current_price"` or `"quarantined"`), its `history_days` and `percent_weight`, plus
`priced_as_of` when the position is valued from a close other than today's (the last known
close for `no_current_price`; for `quarantined`, the most recent close that could be trusted —
the newest one before the implausible move, or, when a split/dividend re-download failed, a
freshly downloaded close on the *current* adjustment basis, since the cached history is on the
superseded one); `note` says the same in words with the remedy for each. A kept position
above `max_weight`, or a `max_positions` that the kept positions use up, is refused as an
infeasible constraint set with those remedies. To sell a `short_history` position instead,
lower `min_history_days` to its count and leave it out of `universe`; a `no_current_price` one
becomes ordinary again as soon as a refresh quotes it, so if it was delisted or renamed, remove
it from your holdings rather than waiting; a `quarantined` one becomes ordinary when a refresh
downloads a believable history (see `quarantined` in `GET /api/market/` for the reason).

**`metrics`** — figures describing the portfolio actually returned, including any
`max_positions` re-solve, and excluding any `unoptimized` positions:

| Field | Meaning |
|---|---|
| `portfolio_value` | Total being allocated: cash + holdings valued at `as_of`, USD |
| `expected_annual_return` | Annualized expected return as a fraction (`0.124` is 12.4%): the exponentially weighted mean of daily returns (500-day span, so the last two years weigh most), annualized |
| `annual_volatility` | Annualized standard deviation, same units |
| `sharpe_ratio` | `(expected_annual_return − risk_free_rate.value) / annual_volatility`, the same rate the solver maximized against. `null` when volatility is zero (degenerate history) rather than dividing by zero |
| `num_assets` | Positions in the returned portfolio, `unoptimized` ones included |
| `turnover` | Fraction of `portfolio_value` the `trades` sell (`0.3` means 30% of what you hold changes hands; buys funded by cash do not count). `0` when there are no holdings |

### Worth knowing before trading on this

- **By default your holdings are fully reallocated.** The optimizer does not try to keep what
  you own, and the first run on a real portfolio often sells most of it. Set
  `constraints.max_turnover` to cap how much is sold, and include held tickers in `universe`
  so the optimizer is allowed to keep them at all.
- **Mean-variance optimization over historical returns is a model, not a forecast.** Expected returns weight the last two years most, so a name that has run up recently looks attractive until it stops.
- **The risk-free rate shapes the answer, not just the score.** Max-Sharpe picks the frontier
  portfolio tangent to a line from the risk-free rate, so a higher rate tilts toward higher-return,
  higher-volatility, more concentrated portfolios. There is no universal right value: it is whatever
  this portfolio's idle money actually earns (a savings account, T-bills, a bond fund, a checking
  account paying nothing), which is why it is stored per portfolio and defaults to `0` rather than
  to a guess. A rate above every asset's expected return is rejected, since nothing then beats cash.
- **Repeated identical requests are served from a memo cache** keyed on the universe,
  constraints and the price snapshot (plus your held weights when `max_turnover` is set), so
  polling is cheap (sub-millisecond). The answer only changes when the cache refreshes, which
  `as_of` tells you.

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
| `risk_free_rate` | number −1 to 1, or `"tbill"` | Store the annual yield this portfolio's idle money earns, as a fraction. Every later `/api/optimize/` call that references the portfolio uses it unless the request names its own | unchanged |
| `mode` | `"merge"` \| `"replace"` | `merge` upserts each ticker (0 removes); `replace` drops existing holdings first | `"merge"` |
| `skip_unknown` | boolean | Drop unrecognized tickers instead of failing | `false` |

Pass `holdings` **or** `csv`, never both. The write is all-or-nothing: if anything is wrong,
nothing is stored and the error lists every problem at once.

```json
{ "workspace": "ryan", "portfolio": "long-term", "create_portfolio": true,
  "mode": "replace", "holdings": { "AAPL": 10, "MSFT": 5.5 }, "cash": 5000, "risk_free_rate": 0.04 }
```

Returns the portfolio's final state (`risk_free_rate` is `null` when none has been stored):

```json
{ "workspace": "ryan", "portfolio": "long-term", "mode": "replace",
  "imported": 2, "skipped_unknown": [], "ignored_lines": [],
  "cash": 5000, "risk_free_rate": 0.04, "holdings": { "AAPL": 10, "MSFT": 5.5 } }
```

## `GET /api/workspaces/`

Every workspace with its portfolios and holdings.

```json
{ "workspaces": [ { "name": "demo", "created": "2026-10-29 12:00:00",
    "portfolios": [ { "name": "main", "cash": 5000, "risk_free_rate": null, "holdings": { "AAPL": 10 } } ] } ] }
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
| `400` | Malformed JSON, a field of the wrong type, a number that is `NaN`, infinite or out of range (cash and share counts are capped at 2,000,000,000,000), an unknown or delisted ticker, an infeasible constraint set, a zero-value portfolio, or a solver failure |
| `404` | A named workspace or portfolio does not exist, or the path is not an endpoint |
| `409` | The referenced portfolio has stored values that had to be left out (`error.payload.problems` lists them). Set them again, then retry |
| `500` | Stored data cannot be used: the database, the price history or the ticker list is unreadable. The message says how to recover. Also any unexpected failure, still in this JSON shape |

Bulk-import failures add `error.payload` carrying `errors` (per-line messages) and
`unknown_tickers`.

A portfolio in `GET /api/workspaces/` carries a `problems` list only when some stored value
(edited outside the app, or written by an older version) could not be used. Those values are
left out of `cash`, `holdings` and `risk_free_rate`, and optimizing that portfolio is refused
until they are set again.

Messages name the offending field or symbol and are safe to surface verbatim. Two to expect
when working from a real brokerage export:

- `Invalid symbol in universe: XYZ.` — not in the ticker list at all.
- `SPCX has only 23 day(s) of price history; at least 252 are needed to estimate its return and risk ...` —
  a recent listing. Leave it out, or pass `min_history_days` at or below its count to include it
  anyway, knowing its statistics are noise.
- `No current price for PXD: delisted, renamed, or missed by the last price refresh.` — the
  symbol is in the ticker list but has no price at the end of the cached history. Usually it
  stopped trading (acquired, renamed), so map it to its successor symbol or drop it. It can
  also mean the last download was rate-limited for that symbol, which a
  `wolvwealth refresh-data --full` fixes.
