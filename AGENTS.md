# Working on WolvWealth

Instructions for AI agents (and anyone) making changes to this codebase. For *using* the running app as an agent, read [API.md](API.md) instead — it is the complete JSON contract.

**This code allocates real money.** A wrong number is worse than a crash and worse than a missing feature. When correctness and convenience conflict, correctness wins; when data is incomplete, report it rather than papering over it; when the app assumes something (a price date, a risk-free rate), the output must say so. Prefer small, verified steps over large changes.

## Commands

```bash
pytest tests/ -q                                       # whole suite, ~10 s, no network, never touches real data
black --check --line-length 120 wolvwealth scripts tests
pycodestyle wolvwealth scripts tests
pydocstyle wolvwealth
pylint wolvwealth scripts
```

CI runs exactly these five on every push (`.github/workflows/ci.yml`). All five must pass; run them before claiming work is done. Format with `black --line-length 120` (not the default 88).

## Layering (enforced, not aspirational)

`tests/test_architecture.py` parses every module's imports and fails the build on a violation. Direction: `web/` and `__main__.py` → `services/` → `storage/`, `market/` → `domain/`. Also `services/`, `web/`, `__main__.py` → `config.py`.

| Layer | Contains | Must never import |
|---|---|---|
| `domain/` | The maths and entities: optimizer, trades, MarketData, parsing, errors | Flask, sqlite3, yfinance, requests, file paths |
| `storage/`, `market/` | SQLite + Parquet persistence; the Yahoo downloader | Flask, each other |
| `services/` | Use cases: wire a repo + MarketData into one operation | Flask (`request`, `session`, `g`) |
| `web/`, `__main__.py` | Thin adapters: parse input, call a service, render | SQL text, solver calls |

A new feature is: logic in `domain/` (if any), a use case in `services/`, a ~dozen-line adapter (API route, view + template, or CLI subcommand), and tests. If you need a new top-level package, add it to the layer map in `tests/test_architecture.py`.

## Invariants that have already bitten people

- **Anything new that reaches the solver must join both memo keys** in `domain/optimizer.py` (`max_sharpe_weights` and `_return_and_volatility`). Solved weights are memoized per price snapshot; a solver input missing from the key silently serves one request's portfolio to a different request. The tests in `tests/test_optimize_cache.py` encode which fields share a solve and which do not — extend them when you extend the key.
- **API.md is contract-tested.** `tests/test_api_contract.py` fails if a documented endpoint, request field, or response key does not match the code. Change the API and API.md together, always.
- **Schema changes need a migration.** `storage/sql/schema.sql` only runs for brand-new databases. Adding a column means also adding it to `MIGRATIONS` in `storage/database.py`, so existing installs get it on their next connection. Test against an old-schema file (see `test_database_from_before_the_rate_column_is_migrated_on_open`).
- **All SQL lives in `storage/repository.py`**, parameterized, and every portfolio query is scoped by workspace. Keep it that way.
- **Errors are domain errors.** Raise `ValidationError` / `NotFoundError` from `domain/errors.py` with a message naming the field and, where possible, the fix. Never HTTP status codes outside `web/`. The API blueprint maps them to 400/404 JSON; views flash the message.
- **Never prune `data/ticker_universe.csv` because tickers came back empty.** Yahoo returns identical nothing for a delisted company and a throttled request; pruning deletes live companies. `MarketData.tradeable` already handles dead tickers dynamically.
- **Tests must not touch the network or real `var/`.** `conftest.py` builds everything on a temp dir with a deterministic 4-ticker market, and an autouse fixture makes the T-bill fetch behave offline. Downloaders are injectable — stub them, never call Yahoo.
- **Mutations are POSTs.** `web/security.py` rejects cross-site requests; a state-changing GET breaks that model.
- **GET-like reads of the price cache are fine; writes must be atomic.** `PriceStore` writes through a temp file + `os.replace` because a background refresh runs while requests are served. Follow that pattern for any new cache file.
- **Every change ships with tests.** A bug fix includes a regression test that fails without it. The suite runs in seconds; there is no excuse.

## Where things go

| Change | Touch |
|---|---|
| API endpoint | Route in `web/api/__init__.py` calling a `services/` function; document in API.md |
| Web page | `web/views/<page>.py` + `templates/<page>.html` (extend `base.html`); register in `web/views/__init__.py` |
| Optimizer maths | `domain/optimizer.py` — plain dict in, plain dict out; update the memo keys and `tests/test_optimize*.py` |
| Storage | `storage/repository.py` + `domain/models.py` + `storage/sql/schema.sql` + `MIGRATIONS` |
| CLI subcommand | `__main__.py` (argparse + `cmd_*`), tests in `tests/test_cli.py` |
| Tunables / paths | `config.py` (`Settings`) |
| Styling | Tailwind classes in templates, then `npm run css` and commit the rebuilt `output.css` (palette in `tailwind.config.js`; no ad-hoc hex colors) |

## Docs map

- `README.md` — what the project is, how it works, known limitations. Keep it current when behavior changes.
- `API.md` — the agent-facing JSON contract. Contract-tested; written to be pasted into an agent's context whole.
- `CONTRIBUTING.md` — human setup, environment, conventions.
- Docstrings explain *why* (constraints, trade-offs), not *what the next line does*.
