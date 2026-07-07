# Changelog

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
