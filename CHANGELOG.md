# Changelog

## Unreleased (backtesting)

### Fixed
- A backtest that held cash the whole time earned exactly the risk-free rate but reported a negative Sharpe ratio (-1.3 at 2%) and small nonzero volatility. The Sharpe ratio subtracted the compound annual rate from an arithmetic mean of daily returns times 252, and the weekend-interest pattern of cash counted as volatility. Volatility and Sharpe now use each day's excess return over the risk-free asset's return for the same calendar days, annualized with the curve's observed periods per year: cash has 0 volatility and a Sharpe of 0, and riskless outperformance is reported as undefined rather than as a huge number.
- `/api/optimize/` charges a use only when it returns an answer (including a hold-cash answer): it now checks the token is live without charging, runs the optimizer, and charges last. Solver failures and server errors are free; previously a solver failure cost a use. An expired or exhausted token is refused before any optimization or backtest validation work. If a concurrent request spends the last use while the optimizer runs, the answer is withheld with a 403 and uses never go negative. The unused `check_api_key()` helper was removed.
- Uninvested cash in a backtest now earns the configured risk-free rate (default 2%), compounded over calendar days, instead of 0%. Previously the hold-cash decision and the Sharpe ratio assumed a 2% risk-free asset that the portfolio could not actually hold, so cash periods were penalized by an inconsistency rather than by the market. The result reports the total as `cash_interest`. A risk-free rate of exactly -100% is now rejected.
- "No asset beats the risk-free rate" is now treated as the answer it is (hold cash), not an error. `/api/optimize/` returns `200` with an empty portfolio, the full amount under a new `cash` field and the reason in a new `notes` field, instead of `400 "Optimization Error ... Infeasible"`; genuine solver failures now say the solver could not find a portfolio rather than blaming the constraints. Backtests sell into cash at such a rebalance (previously they logged "optimization failed" and kept the previous, stale positions), record a `note` on the rebalance and add one summary warning. The dashboard shows a cash row and the explanation; the CLI prints `cash 100%`. Every optimize response now includes `cash` and `notes`.
- Backtests no longer crash for start dates before a universe member's IPO: assets without history in the lookback window are skipped for that rebalance.
- The period after the final rebalance is now included; the equity curve is marked to market on every trading day through the end date.
- Rebalance dates use calendar steps instead of fixed day counts, so "monthly" no longer drifts.
- The optimizer trades one day after its decision by default instead of at the close it just observed.
- Share counts and cash are carried exactly between rebalances; unallocated weight stays as cash instead of disappearing.
- `max_positions` re-optimizes over the kept assets, so `max_weight` still holds and the reported expected return / volatility / Sharpe describe the returned weights (affects the API as well).
- Optimizer weights always sum to 1 after small weights are dropped.

- Input validation hardened on every path into the optimizer. JSON bodies containing `NaN`, `Infinity` or `-Infinity` are refused at parse time (Python's `json` accepts them and NaN passes every range check). Shared `require_number` / `require_integer` validators reject bools, non-finite values and out-of-range values for cash, holdings, costs, lookback, execution lag and constraints, and the `Constraints` / `BacktestConfig` dataclasses validate themselves so the CLI and library callers get the same protection. Nothing is charged for a rejected request. The dashboard validates the same rules before enabling the Run / Optimize buttons and shows the reason under the field.
- The infeasible-constraints hint now suggests a `max_weight` that actually works: it rounds up to the API's 2-decimal precision (3 stocks: 0.34, not 0.33 since 3 × 0.33 = 0.99) and also states how many stocks would suffice at the current weight.
- Constraints that can never fully invest the universe are rejected up front (e.g. `top5` with `max_weight` 0.1 reaches only 50%); previously only `max_positions * max_weight` was checked, so the request was charged and then sat in cash.
- Backtests are checked before charging: `start` before the first or `end` after the last price date is rejected, and a dry run of the schedule (`wolvwealth/backtest/preflight.py`, sharing the engine's decision iterator and skip rules) rejects ranges in which no rebalance could execute, such as a start with no lookback history or a range shorter than the execution lag. The CLI runs the same check.
- `/api/optimize/` no longer charges for requests it rejects: it authenticates first, validates (including feasibility), and only then consumes a use.
- Rate limiting (Flask-Limiter), enforced before any view runs: a site-wide and an API-wide limit per client IP (shared buckets, static files exempt), per-caller limits on optimize and backtest start/poll keyed by API key or session user, login limits (all attempts per IP; failed attempts per IP, per account and client, and per account for clients that have never logged in to it) and per-IP limits on registration. Successful logins never count as failures, and a signed device cookie set on login exempts the owner's known browsers from the per-account limit, so knowing a username is not enough to lock its owner out (OWASP device-cookie pattern; an earlier per-username limit that allowed exactly that lockout was replaced, and `WOLVWEALTH_RATELIMIT_LOGIN_PER_USERNAME` was removed in favour of the `LOGIN_FAILURES_*` settings). Limits are configurable through `WOLVWEALTH_RATELIMIT_*`; over-limit requests get 429 with `Retry-After` and are not charged. `WOLVWEALTH_TRUSTED_PROXIES` makes per-IP limits work behind a reverse proxy without letting clients spoof `X-Forwarded-For`. The dashboard keeps polling a running backtest through a 429 instead of marking it failed. Polling also survives network errors, 408 and 5xx responses with capped, jittered exponential backoff (honoring `Retry-After`), shows a \"retrying\" notice meanwhile, and gives up with an explanation after 8 consecutive failures; 404 (job gone) and 401/403 (session ended) fail at once with a specific message. Starting a job is never retried automatically, since that POST charges a use. A 2xx whose body is not JSON or not a valid job snapshot (proxy page, captive portal, truncated response) is now an error the poller retries, instead of crashing the polling loop and leaving the UI on \"queued\" forever; the start sequence and polling loop can no longer end without either scheduling the next poll or reaching a terminal state. The Optimize tab reports such responses instead of showing nothing. A test walks every `/api/` route and fails if one is not limited.
- Availability: `POST /api/backtest/` authenticates before reading the request body, so an anonymous or invalid caller costs one indexed token lookup and nothing else (previously the full pre-run check ran first: about 22 s of CPU per anonymous request for a whole-history daily schedule). The rebalance limit is now enforced from the schedule dates alone, before any price data is touched, so an over-limit request is refused in milliseconds regardless of how far over it is. Day-based schedules are generated vectorized (a whole-history daily schedule is now about 17 ms to refuse, down from 22 s). A caller whose job queue is already full is refused (429/503) before any validation runs.
- Huge numbers no longer cause HTTP 500s. A JSON integer too large for a float (e.g. `1` followed by 400 zeros) in any decimal field now returns 400 "out of range" instead of an uncaught `OverflowError`, and a `topN` alias with more digits than Python's int-parsing limit is clamped to the whole universe instead of raising `ValueError`. `top0` is rejected. The dashboard says "Out of range." for such input.

### Changed
- The optimizer's exponentially weighted covariance is computed with two matrix products instead of pypfopt's pair-by-pair loop of pandas `ewm` calls (`wolvwealth.optimizer_core.exp_cov`). Results match pypfopt's `risk_models.exp_cov` to floating-point rounding, including assets with different price histories and pairs that share no day (NaN in both); optimized weights on the real data are unchanged. A default `/api/optimize/` request over the top 500 stocks drops from about 65 s to about 3.5 s, and a 50-stock backtest rebalance from about 0.7 s to about 0.1 s; the dashboard's run-time estimate is recalibrated.
- Backtesting moved into the `wolvwealth.backtest` package (config, schedule, strategies, engine, metrics, service, cli); `backtest.py` is now a thin wrapper and the CLI is also installed as `wolvwealth-backtest`.
- Universe / constraint validation shared between the API and the backtester (`wolvwealth/optimizer_params.py`); CSV loading shared via `wolvwealth/market_data.py`.

### Added
- Optimization objectives beyond max-Sharpe, for `/api/optimize/`, `/api/backtest/`, the CLI and both dashboard tabs: `min_volatility`, `efficient_risk` (highest return within a `target_volatility`), `efficient_return` (lowest volatility for a `target_return`) and `max_utility` (return minus `risk_aversion` / 2 × variance). Requests take an optional `objective`, as a name or `{"name": ..., <parameter>: ...}`; the default remains `max_sharpe` and existing requests behave as before. Every constraint applies under every objective. Responses echo the `objective`; backtest reports carry it in `config.objective` and `result.strategy`. The dashboard's "Optimize for" picker explains each objective in plain language and takes volatility and return targets in percent. CLI: `--objective`, `--target-volatility`, `--target-return`, `--risk-aversion`.
- Each objective recommends cash only when cash beats every stock portfolio on that objective's own terms (a return target at or below the risk-free rate; nothing within a volatility limit beating the rate; no portfolio's risk-adjusted return beating it); `min_volatility` always stays invested. A target no portfolio can reach is a free 400 whose message gives a target that works, checked against the real long-only constraints rather than pypfopt's short-selling bound. A target exactly at the highest reachable return is solvable, although pypfopt's own maximum sits slightly below it. Backtest windows with an unreachable target keep their positions and say why.
- `wolvwealth.optimizer_core.compute_portfolio(prices, objective, ...)`, `estimate_inputs()` (the single place expected returns and covariance are estimated) and `HoldCashAnswer`, the base of every hold-cash answer. `compute_max_sharpe_portfolio` and `MaxSharpeStrategy` remain as aliases of the new `compute_portfolio` default and `OptimizerStrategy`.
- Rolling lookback window, execution lag and transaction costs as backtest parameters.
- Realized performance metrics (total return, CAGR, volatility, Sharpe, max drawdown, Calmar, turnover, costs).
- Equal-weight and buy-and-hold (SPY) benchmarks run through the same engine, plus a survivorship-bias warning on every report.
- `--json` report output for downstream consumers (the upcoming web UI).
- `POST /api/backtest/`: runs a backtest with benchmarks over the JSON API. Accepts an API key or a logged-in session with an `X-CSRF-Token` header, charges one use per request (malformed requests are free), caps work per request (`WOLVWEALTH_BACKTEST_MAX_REBALANCES`, `WOLVWEALTH_BACKTEST_MAX_UNIVERSE_SIZE`) and serves repeated requests from an in-memory cache.
- Backtests run as background jobs with progress: `POST /api/backtest/` returns a job id immediately (202), and `GET /api/backtest/<job_id>/` reports status, rebalance-level progress, elapsed time and an ETA, then the report. Jobs are private to their owner, expire 15 minutes after finishing, and are capped per user (429) and server-wide (503) before anything is charged.
- `wolvwealth-backtest` shows a progress bar on stderr when run in a terminal (`--quiet` to disable).
- Web dashboard on `/optimizer/` (previously an empty page): an Optimize tab and a Backtest tab with a live progress bar and ETA, growth and drawdown charts (recharts), realized-metrics table versus benchmarks, and a rebalance log. The form estimates rebalances and run time before submitting.
- `/api/optimize/` accepts a logged-in session with an `X-CSRF-Token` header in addition to an API key (shared `authenticate()` helper), so the dashboard needs no API key in the page.
- Vitest unit tests for the frontend (`npm test`, also in CI); `react-router-dom` removed (single page, no routing).
- Tests for the backtest engine, schedule, metrics, CLI, request parsing, cache, endpoint auth/billing and the optimizer-core fixes.

## Unreleased (modernize)

### Security
- Admin API endpoints now fail closed: every `/api/admin/*` and `/api/db/*` route requires a valid admin token (previously the check result was discarded, leaving them unauthenticated).
- `/api/db/dump/` no longer returns password hashes or API token values, and is POST-only.
- `SECRET_KEY` is read from the `WOLVWEALTH_SECRET_KEY` environment variable. The previously committed key must be considered compromised and rotated.
- Development seed data (`sql/data.sql`) now contains only obviously-fake accounts/tokens. The previously committed accounts, bcrypt hashes, and API tokens remain in git history and must be treated as compromised (rotate/revoke).
- CSRF protection on all session-authenticated forms; `/accounts/logout/` and `/accounts/delete/` are POST-only.
- Session cookies are `HttpOnly` and `SameSite=Lax`.
- API token uses are decremented atomically (no double-spend race).
- Production now runs gunicorn (no more `sudo flask --debug` on port 80).

### Fixed
- `/api/admin/add/` and `/api/admin/remove/` returned 500 on every request (bad view signatures).
- Universe parsing no longer skips validation of the entry after a `topN` alias; `topN` is now case-insensitive and supports any N (clamped to the universe size).
- `max_positions` truncation no longer crashes when fewer assets have nonzero weight.
- Infeasible `min_universal_weight` values are rejected up front with a clear message.
- Optimization failures are logged with the original exception instead of being swallowed.
- Deleting an account clears the session; stale sessions for deleted users are dropped instead of crashing `/account/`.
- Unknown users in `/api/admin/user-info/` return 404 instead of 500.
- Eastern time display is DST-aware (was hardcoded UTC-5).
- Database transactions roll back when a request fails (previously always committed).
- Data files load relative to the repository (or `WOLVWEALTH_DATA_DIR`) instead of the process working directory.

### Changed
- Frontend build migrated from webpack + babel to Vite; unused npm dependencies removed (including the suspicious `react-do` package and the unused `react-scripts`).
- Python packaging modernized (PEP 621 `pyproject.toml`, hatchling, `uv` + committed `uv.lock`); `requirements.txt` removed.
- Lint/format standardized on ruff (replaces pylint, pycodestyle, pydocstyle, black); eslint (flat config) for the frontend.
- Templates refactored onto a shared Jinja base template; shared page JS moved to `static/js/site.js`.
- `bin/db` now uses Python's stdlib `sqlite3` (no sqlite3 CLI required); `bin/setup` uses uv + npm.
- Backtesting utility rewritten as a CLI (`python backtest.py --help`) sharing the optimizer core with the API.
- Dead network-dependent code moved out of the app into `bin/update_prices`.

### Added
- Test suite (pytest) covering pages, auth flows, admin authorization, and optimizer logic.
- GitHub Actions CI (ruff + pytest; eslint + Vite/Tailwind build).
- MIT LICENSE, `.env.example`, `.editorconfig`, database indexes.
