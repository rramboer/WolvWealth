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

`tests/test_architecture.py` parses every module's imports and fails the build on a violation. Direction: `web/`, `mcp/` and `__main__.py` → `services/` → `storage/`, `market/` → `domain/`. Also `services/`, `web/`, `mcp/`, `__main__.py` → `config.py`.

| Layer | Contains | Must never import |
|---|---|---|
| `domain/` | The maths and entities: optimizer, trades, MarketData, parsing, errors | Flask, sqlite3, yfinance, requests, file paths |
| `storage/`, `market/` | SQLite + Parquet persistence; the Yahoo downloader | Flask, each other |
| `services/` | Use cases: wire a repo + MarketData into one operation | Flask (`request`, `session`, `g`) |
| `web/`, `mcp/`, `__main__.py` | Thin adapters: parse input, call a service, render | SQL text, solver calls |

A new feature is: logic in `domain/` (if any), a use case in `services/`, a ~dozen-line adapter per surface (API route, MCP tool, view + template, or CLI subcommand), and tests. The API, the MCP tools and the CLI must call the *same* service function for the same operation; `tests/test_mcp.py` and `tests/test_cli.py` compare their outputs to the API's. If you need a new top-level package, add it to the layer map in `tests/test_architecture.py`.

## Invariants that have already bitten people

- **Anything new that reaches the solver must join both memo keys** in `domain/optimizer.py` (`max_sharpe_weights` and `_return_and_volatility`). Solved weights are memoized per price snapshot; a solver input missing from the key silently serves one request's portfolio to a different request. The tests in `tests/test_optimize_cache.py` encode which fields share a solve and which do not — extend them when you extend the key.
- **A new constraint is three things, not one.** Parse and range-check the field in `domain/optimizer.py`; add how it interacts with the existing constraints to `RULES` in `domain/constraints.py` (individually valid numbers are routinely impossible in combination, and the solver's only word for that is "infeasible"); then enforce it on the portfolio *actually returned*, not on the solver's intermediate answer. Extend `assert_honors` and the feasible grid in `tests/test_constraints.py` — a cap that the step after the solve quietly breaks is the exact bug that file exists for.
- **The expected-return estimator is a named, versioned choice.** `domain/stats.py` holds the candidates in `ESTIMATORS` and the one in use in `ESTIMATOR`, whose name is part of the statistics cache's metadata; a cache built under another name is rebuilt, never served. A candidate must be computable per ticker from that ticker's prices alone (the cache covers 500 names and is sliced per request; `tests/test_stats.py` checks separability), which rules out cross-sectional estimators such as CAPM against a universe-derived benchmark. Do not change `ESTIMATOR` on taste: run `scripts/backtest.py --estimator` against the current one and update the table in CONTRIBUTING.md.
- **API.md is contract-tested.** `tests/test_api_contract.py` fails if a documented endpoint, request field, or response key does not match the code. Change the API and API.md together, always.
- **Schema changes need a migration.** `storage/sql/schema.sql` only runs for brand-new databases. Adding a column means also adding it to `MIGRATIONS` in `storage/database.py`, so existing installs get it on their next connection. Test against an old-schema file (see `test_database_from_before_the_rate_column_is_migrated_on_open`).
- **All SQL lives in `storage/repository.py`**, parameterized, and every portfolio query is scoped by workspace. Keep it that way.
- **Errors are domain errors.** Raise `ValidationError` / `NotFoundError` from `domain/errors.py` with a message naming the field and, where possible, the fix. Never HTTP status codes outside `web/`. The API blueprint maps them to 400/404 JSON; views flash the message.
- **The price Parquet is an append-only archive; exclusions are a view over it.** A refresh may add days or correct them in place, never delete a ticker's history — not for a quarantine, not for a throttled split repair. Excluded tickers are blanked by `_exclude` on the way to the snapshot the optimizer reads, and the exclusion lives in `quarantine.json`. Writing a judgement into the file destroys the evidence it was made from: the next refresh would have nothing to compare a download against and nothing to fall back to, and a history Yahoo truncated once may never be served whole again. `tests/test_split_repair.py` pins "snapshot excludes, disk retains".
- **Never prune `data/ticker_universe.csv` because tickers came back empty.** Yahoo returns identical nothing for a delisted company and a throttled request; pruning deletes live companies. `MarketData.tradeable` already handles dead tickers dynamically.
- **Tests must not touch the network or real `var/`.** `conftest.py` builds everything on a temp dir with a deterministic 4-ticker market, and an autouse fixture makes the T-bill fetch behave offline. Downloaders are injectable — stub them, never call Yahoo.
- **Mutations are POSTs.** `web/security.py` rejects cross-site requests; a state-changing GET breaks that model.
- **Nothing may print to stdout while `wolvwealth mcp` runs.** stdout is the MCP protocol stream; one stray line ends the session. The server redirects Python-level stdout to stderr as a backstop and the CLI builds its services with a stderr logger, but new code should log through the injected `log` callable, never bare `print`. `tests/test_mcp.py` has a stray-print test; extend it if you add output anywhere a tool can reach.
- **GET-like reads of the price cache are fine; writes must be atomic.** `PriceStore` writes through a temp file + `os.replace` because a background refresh runs while requests are served. Follow that pattern for any new cache file.
- **Every change ships with tests.** A bug fix includes a regression test that fails without it. The suite runs in seconds; there is no excuse.

## Where things go

| Change | Touch |
|---|---|
| API endpoint | Route in `web/api/__init__.py` calling a `services/` function; document in API.md |
| Web page | `web/views/<page>.py` + `templates/<page>.html` (extend `base.html`); register in `web/views/__init__.py` |
| Optimizer maths | `domain/optimizer.py` — plain dict in, plain dict out; update the memo keys and `tests/test_optimize*.py` |
| Expected-return estimator | `domain/stats.py` (`ESTIMATORS`, `ESTIMATOR`); `tests/test_stats.py`; the backtest table in CONTRIBUTING.md |
| Storage | `storage/repository.py` + `domain/models.py` + `storage/sql/schema.sql` + `MIGRATIONS` |
| CLI subcommand | `__main__.py` (argparse + `cmd_*`), tests in `tests/test_cli.py` |
| MCP tool | A `Tool` in `mcp/tools.py` (schema + description + handler calling a `services/` function); tests in `tests/test_mcp.py`; keep MCP.md's tool table current |
| Tunables / paths | `config.py` (`Settings`) |
| Styling | Tailwind classes in templates, then `npm run css` and commit the rebuilt `output.css` (palette in `tailwind.config.js`; no ad-hoc hex colors) |

## Docs map

- `README.md` — what the project is, how it works, known limitations. Keep it current when behavior changes.
- `API.md` — the agent-facing JSON contract. Contract-tested; written to be pasted into an agent's context whole.
- `MCP.md` — how to connect an AI app to `wolvwealth mcp`, written to be pasted into the assistant so it does the setup. Keep the tool table and troubleshooting list current.
- `CONTRIBUTING.md` — human setup, environment, conventions.
- Docstrings explain *why* (constraints, trade-offs), not *what the next line does*.
