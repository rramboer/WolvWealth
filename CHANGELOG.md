# Changelog

## Unreleased (backtesting)

### Fixed
- Backtests no longer crash for start dates before a universe member's IPO: assets without history in the lookback window are skipped for that rebalance.
- The period after the final rebalance is now included; the equity curve is marked to market on every trading day through the end date.
- Rebalance dates use calendar steps instead of fixed day counts, so "monthly" no longer drifts.
- The optimizer trades one day after its decision by default instead of at the close it just observed.
- Share counts and cash are carried exactly between rebalances; unallocated weight stays as cash instead of disappearing.
- `max_positions` re-optimizes over the kept assets, so `max_weight` still holds and the reported expected return / volatility / Sharpe describe the returned weights (affects the API as well).
- Optimizer weights always sum to 1 after small weights are dropped.

### Changed
- Backtesting moved into the `wolvwealth.backtest` package (config, schedule, strategies, engine, metrics, service, cli); `backtest.py` is now a thin wrapper and the CLI is also installed as `wolvwealth-backtest`.
- Universe / constraint validation shared between the API and the backtester (`wolvwealth/optimizer_params.py`); CSV loading shared via `wolvwealth/market_data.py`.

### Added
- Rolling lookback window, execution lag and transaction costs as backtest parameters.
- Realized performance metrics (total return, CAGR, volatility, Sharpe, max drawdown, Calmar, turnover, costs).
- Equal-weight and buy-and-hold (SPY) benchmarks run through the same engine, plus a survivorship-bias warning on every report.
- `--json` report output for downstream consumers (the upcoming web UI).
- `POST /api/backtest/`: runs a backtest with benchmarks over the JSON API. Accepts an API key or a logged-in session with an `X-CSRF-Token` header, charges one use per request (malformed requests are free), caps work per request (`WOLVWEALTH_BACKTEST_MAX_REBALANCES`, `WOLVWEALTH_BACKTEST_MAX_UNIVERSE_SIZE`) and serves repeated requests from an in-memory cache.
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
