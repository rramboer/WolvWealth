# Roadmap

## Next: tax-aware trades

The optimizer returns the target share counts and the trades that get there from current holdings, but nothing about what those trades cost in taxes. The plan, roughly in order:

1. **Tax lots.** Accept a holding as lots (shares, cost per share, date bought) as well as a plain share count. For each sell, pick which lots to sell (lowest tax by default, FIFO as an option) and report the realized gain or loss, split into short term and long term.
2. **Wash sales.** Flag a sale at a loss when the plan buys the same ticker back, or when it was bought in the 30 days before.
3. **A cap on realized gains** as an optimizer constraint, so a rebalance can stay under a tax budget.
4. **Saved holdings.** Store holdings and lots with an account so they don't have to be re-entered (needs a schema change).
5. **Tax-loss harvesting.** Suggest losing positions to sell and similar, but not identical, replacements to buy.

## Bugs

- With both `max_positions` and `max_weight` set, positions can exceed the cap: the N largest weights are kept and scaled back up to 100% after the solve. With `max_positions: 5` and `max_weight: 0.2` over the top 50, two positions come back at 29.65%. The reported metrics also describe the weights from before that cut.
- A `top20`-style alias in `universe` is removed from the list while the list is being looped over, so the entry right after it is never checked. `["top20", "NOTATICKER"]` returns a 500 instead of "Invalid symbol".
- `/api/admin/add/` and `/api/admin/remove/` declare a `username` argument their routes never pass, so every call fails with a 500.
- Every solver failure is reported as "Infeasible", including ones caused by bad input.
- `./bin/run prod` runs the Flask debug server as root on port 80. It should use gunicorn (`gunicorn_config.py` is already there).

## Data

- A command to refresh prices. The file ends January 30, 2024. A refresh has to handle splits and dividends re-adjusting old prices, and tickers Yahoo doesn't return on a given day.
- Holdings are valued at the last close in the file; every response should say what date that is.

## Quality

- A test suite (there isn't one yet) and CI running pytest, pycodestyle, pydocstyle and pylint, which `pyproject.toml` already lists.
- Make the risk-free rate an input instead of PyPortfolioOpt's fixed 2%.
- Use `backtest.py` to compare other expected-return estimators against the plain historical mean.
