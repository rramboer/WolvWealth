# Roadmap

## Next: tax-aware trades

The optimizer returns the trades from current holdings to the target and, for holdings given as tax lots, which lots each sell comes from, the realized gains and wash-sale warnings. It doesn't yet let taxes change the target itself. The plan, roughly in order:

1. **A cap on realized gains** as an optimizer constraint, so a rebalance can stay under a tax budget.
2. **Saved holdings.** Store holdings and lots with an account so they don't have to be re-entered (needs a schema change).
3. **Tax-loss harvesting.** Suggest losing positions to sell and similar, but not identical, replacements to buy.

## Bugs

- With both `max_positions` and `max_weight` set, positions can exceed the cap: the N largest weights are kept and scaled back up to 100% after the solve. With `max_positions: 5` and `max_weight: 0.2` over the top 50, two positions come back at 29.65%. The reported metrics also describe the weights from before that cut.
- A `top20`-style alias in `universe` is removed from the list while the list is being looped over, so the entry right after it is never checked. `["top20", "NOTATICKER"]` returns a 500 instead of "Invalid symbol".
- `/api/admin/add/` and `/api/admin/remove/` declare a `username` argument their routes never pass, so every call fails with a 500.
- Every solver failure is reported as "Infeasible", including ones caused by bad input.
- The trade list can spend a few cents more cash than there is: target share counts are rounded to 4 decimals and the buys match them exactly (up to $0.03 seen). Rounding buys down would fix it but the trades would then miss the target slightly.
- `./bin/run prod` runs the Flask debug server as root on port 80. It should use gunicorn (`gunicorn_config.py` is already there).

## Data

- Re-rank `ticker_universe.csv` by current market cap. The order, and so what `top20` through `top500` include, is from early 2024. `ApplicationState.sort_ticker_universe` does it but makes one Yahoo request per ticker.
- `historical_prices.csv` is 55 MB and tracked in git, so each refresh adds about that much to the repo history, and GitHub warns about files over 50 MB. Options: build it locally with `bin/refresh-prices` instead of committing it, or shrink the universe.
- Refresh on a schedule, and catch a ticker symbol that Yahoo has reassigned to a different company.
- Responses only say what date the prices are from when the request sends `prices` or tax lots; every response should.

## Quality

- Extend the test suite (it covers trades, prices and tax lots so far) and add CI running pytest, pycodestyle, pydocstyle and pylint, which `pyproject.toml` already lists.
- A request over the default top 500 takes about 50 seconds, nearly all in PyPortfolioOpt's `exp_cov`, which works through every pair of tickers. The covariance only changes when prices are refreshed, so it could be cached per universe.
- Make the risk-free rate an input instead of PyPortfolioOpt's fixed 2%.
- Use `backtest.py` to compare other expected-return estimators against the plain historical mean.
