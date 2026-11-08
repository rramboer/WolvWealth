"""The four MCP tools: what they are called, what they take, and what they do.

The descriptions are the documentation a model reads instead of API.md, so
they carry the warnings that matter before acting on a result: prices are
a cache with a date, holdings get reallocated unless turnover is capped,
sells come first, and WolvWealth never places orders. Input schemas mirror
the API's request bodies field for field (``tests/test_mcp.py`` checks them
against API.md), and every handler calls the same ``services/`` function
the API route does.
"""

import dataclasses

from wolvwealth.domain.errors import ValidationError
from wolvwealth.domain.market import MarketData
from wolvwealth.services import holdings_import, market_data, optimize, workspaces
from wolvwealth.services.container import Services

#: Shown when the price cache does not exist yet. The server never starts the
#: multi-minute first download on its own: a tool call that silently hangs
#: for minutes reads as broken, and a model cannot tell the user why.
NO_CACHE_MESSAGE = (
    "No price history on this machine yet. Ask the user to run `wolvwealth refresh-data` once in a terminal "
    "(downloads 2006-to-today prices, about 40 MB, several minutes), then call this tool again."
)

_NUMBER_OR_TBILL = {
    "type": ["number", "string"],
    "description": 'Annual yield the idle cash earns, as a decimal fraction (0.04 is 4%), or the string "tbill" for '
    "the current 13-week US Treasury bill yield.",
}

OPTIMIZE_SCHEMA = {
    "type": "object",
    "properties": {
        "workspace": {"type": "string", "description": "Workspace that holds `portfolio`. Required with it."},
        "portfolio": {
            "type": "string",
            "description": "Name of a stored portfolio to load cash, holdings, and risk-free rate from. "
            "Explicit initial_cash / initial_holdings still win over the stored values.",
        },
        "initial_cash": {"type": "number", "minimum": 0, "description": "Cash to invest, USD."},
        "initial_holdings": {
            "type": "object",
            "additionalProperties": {"type": "number", "minimum": 0},
            "description": "Ticker to share count. Valued at the cached close and pooled with the cash.",
        },
        "universe": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tickers and/or aliases (top20, top50, top100, top200, top300, top400, top500: the N "
            "largest US names by market cap) to allocate across. Default top500. Held tickers are added "
            "automatically so keeping them is possible. Names with fewer than min_history_days of prices are "
            "skipped by aliases and refused by name.",
        },
        "min_history_days": {
            "type": "integer",
            "minimum": 2,
            "description": "Trading days of price history a ticker needs to be in the universe. Default 252 (one "
            "year). Lower it only knowingly: a newly listed name's few weeks of returns, extrapolated to a year, "
            "look like an enormous expected return at almost no risk, and the optimizer puts the maximum weight "
            "there. A held ticker under the minimum is kept at its current size and reported under "
            "`unoptimized` in the result. market_status lists the excluded names under short_history.",
        },
        "constraints": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "max_weight": {"type": "number", "minimum": 0, "maximum": 1, "description": "Per-asset cap."},
                "max_positions": {"type": "integer", "minimum": 1, "description": "Largest number of holdings."},
                "min_universal_weight": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "Floor applied to EVERY ticker in the universe (so it forces holding them all).",
                },
                "max_turnover": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "Largest fraction of the portfolio's value the plan may SELL. 0 keeps every "
                    "holding and only invests cash; 1 (the default) is no limit.",
                },
                "cash_reserve": {
                    "type": "number",
                    "minimum": 0,
                    "exclusiveMaximum": 1,
                    "description": "Fraction of the portfolio's value to leave in cash, uninvested.",
                },
            },
        },
        "risk_free_rate": _NUMBER_OR_TBILL,
        "exclude_metrics": {"type": "boolean"},
        "exclude_trades": {"type": "boolean"},
    },
    "additionalProperties": False,
}

IMPORT_SCHEMA = {
    "type": "object",
    "required": ["workspace", "portfolio"],
    "properties": {
        "workspace": {"type": "string", "description": "An existing workspace."},
        "portfolio": {"type": "string", "description": "Portfolio name within it."},
        "create_portfolio": {"type": "boolean", "description": "Create the portfolio if it does not exist."},
        "holdings": {
            "type": "object",
            "additionalProperties": {"type": "number", "minimum": 0},
            "description": "Ticker to share count. Pass this OR `csv`, not both.",
        },
        "csv": {
            "type": "string",
            "description": "Raw text instead of `holdings`: `TICKER, shares` lines, or a brokerage positions export "
            "(Fidelity, Schwab, Vanguard, Robinhood work as-is). A CASH row sets the cash balance.",
        },
        "cash": {"type": "number", "minimum": 0, "description": "Set the cash balance, USD."},
        "risk_free_rate": _NUMBER_OR_TBILL,
        "mode": {
            "type": "string",
            "enum": ["merge", "replace"],
            "description": "merge (default) upserts each ticker; replace makes the portfolio exactly equal the "
            "import, dropping holdings not listed.",
        },
        "skip_unknown": {"type": "boolean", "description": "Ignore tickers the app does not know instead of failing."},
    },
    "additionalProperties": False,
}

MARKET_STATUS_SCHEMA = {
    "type": "object",
    "properties": {
        "tickers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional symbols to check. Each comes back as tradeable, no_current_price (known but "
            "delisted or renamed), or unknown.",
        }
    },
    "additionalProperties": False,
}


@dataclasses.dataclass(frozen=True)
class Tool:
    """One MCP tool: its listing and the function behind it."""

    name: str
    description: str
    input_schema: dict
    handler: callable
    read_only: bool = True
    destructive: bool = False

    def describe(self) -> dict:
        """Return the ``tools/list`` entry."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {
                "readOnlyHint": self.read_only,
                "destructiveHint": self.destructive,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        }


def market(services: Services) -> MarketData:
    """Return the price snapshot, loaded on first use and reloaded when another process refreshes it.

    Mirrors what ``wolvwealth serve`` does at startup, but lazily, so the MCP
    handshake answers at once instead of after seconds of Parquet loading.
    When there is no cache at all the tool refuses with instructions rather
    than starting the multi-minute first download on a model's behalf.
    """
    service = services.market
    if service.loaded():
        service.reload_if_changed()
        return service.data
    if not service.cache_exists():
        raise ValidationError(NO_CACHE_MESSAGE)
    data = service.data
    if services.settings.auto_refresh:
        # Same policy as `serve`: stale prices refresh on a daemon thread while
        # the cached ones serve, and the new snapshot swaps in when it lands.
        service.start_background_refresh(services.settings.auto_refresh_max_age_days)
    return data


def market_status(services: Services, arguments: dict) -> dict:
    """Freshness of the price cache, the T-bill reference, and an optional symbol check."""
    data = market(services)
    report = market_data.service_status(services.market, data, services.settings.auto_refresh_max_age_days)
    if "tickers" in arguments:
        tickers = arguments["tickers"]
        if not isinstance(tickers, list) or not all(isinstance(t, str) for t in tickers):
            raise ValidationError("Invalid tickers. tickers must be a list of strings.")
        report["tickers"] = market_data.classify_tickers(data, tickers)
    return report


def list_portfolios(services: Services, _arguments: dict) -> dict:
    """Every workspace with its portfolios, cash, risk-free rate, and holdings."""
    with services.database.connect() as connection:
        return workspaces.list_all(services.repository(connection))


def run_optimize(services: Services, arguments: dict) -> dict:
    """Max-Sharpe allocation plus the trades to reach it."""
    data = market(services)
    with services.database.connect() as connection:
        result, _, _ = optimize.optimize_keeping_held(
            arguments, data, services.repository(connection), services.market.tbill
        )
    return result


def run_import(services: Services, arguments: dict) -> dict:
    """Bulk-load holdings into a stored portfolio."""
    data = market(services)
    with services.database.connect() as connection:
        return holdings_import.run(services.repository(connection), data, arguments, tbill=services.market.tbill)


TOOLS = (
    Tool(
        name="market_status",
        description=(
            "Check the local price data before trusting any result. Returns as_of (the date every price and trade "
            "is computed from; prices are a local cache, never live quotes), age_days, stale (true when older than "
            "a week, with a refresh_hint to pass to the user), the current 13-week T-bill yield (tbill_rate) as a "
            "reference risk-free rate, the universe aliases, and under short_history any tradeable name with "
            "too little price history to be optimized on by default, and under stale_prices any whose newest "
            "close predates as_of (throttled; their last close is carried forward). In unattended runs also "
            "check quarantined "
            "(names excluded because their downloaded prices were implausible or irrecoverable) and readjusted "
            "(names whose entire history was legitimately re-downloaded after a split or dividend in the last 30 "
            "days -- their numbers changed basis, not meaning). Pass `tickers` to validate symbols in one call."
        ),
        input_schema=MARKET_STATUS_SCHEMA,
        handler=market_status,
    ),
    Tool(
        name="list_portfolios",
        description=(
            "List every workspace (a person using this machine) and its stored portfolios: cash, holdings as "
            "ticker to share count, and the risk-free rate set on each (null means unset; optimize then assumes 0% "
            "and says so). Use the names with `optimize` and `import_holdings`."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=list_portfolios,
    ),
    Tool(
        name="optimize",
        description=(
            "Compute the maximum-Sharpe-ratio allocation for a portfolio and the exact trades to reach it from what "
            "is held. Give workspace + portfolio (a stored one), or initial_cash and initial_holdings inline. "
            "Returns optimized_portfolio (target shares, value, percent_weight per ticker), cash (the uninvested "
            "reserve), trades (buy/sell share deltas at the cached price, SELLS FIRST so cash exists before it is "
            "spent), metrics (expected return, volatility, Sharpe ratio, turnover), risk_free_rate (the rate assumed "
            "and where it came from; source 'default' means 0% was assumed and the user should set one), and as_of. "
            "Important: by default every holding may be sold; set constraints.max_turnover to cap how much of the "
            "portfolio's value is sold. A held ticker absent from optimized_portfolio means sell it all. Constraint "
            "combinations that cannot be satisfied are refused with a remedy; apply the remedy and retry rather "
            "than dropping the constraint. WolvWealth never places orders: hand the trades to the user or a broker "
            "tool."
        ),
        input_schema=OPTIMIZE_SCHEMA,
        handler=run_optimize,
    ),
    Tool(
        name="import_holdings",
        description=(
            "Write holdings into a stored portfolio (creating it with create_portfolio if needed): a ticker-to-shares "
            "object, or raw text/CSV such as a brokerage positions export. Can set the cash balance and the "
            "risk-free rate in the same call. mode 'merge' (default) adds or updates the listed tickers and keeps the "
            "rest; mode 'replace' makes the portfolio exactly the import and drops unlisted holdings. Unknown tickers "
            "fail the whole import unless skip_unknown is true; nothing is written on failure. Returns the "
            "portfolio's state afterwards."
        ),
        input_schema=IMPORT_SCHEMA,
        handler=run_import,
        read_only=False,
        # Only mode "replace" removes anything; the hint is for the whole tool.
        destructive=True,
    ),
)

BY_NAME = {tool.name: tool for tool in TOOLS}
