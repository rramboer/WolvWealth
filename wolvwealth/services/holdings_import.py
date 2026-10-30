"""Use case: bulk-load holdings into a stored portfolio.

Shared by the browser form (pasted text or an uploaded file) and the JSON
API (a holdings map or csv text). All-or-nothing: any problem raises
ValidationError carrying every issue in ``details`` and nothing is written.
"""

import dataclasses

from wolvwealth.domain import holdings_parse
from wolvwealth.domain.errors import NotFoundError, ValidationError
from wolvwealth.domain.market import MarketData
from wolvwealth.domain.models import NAME_RULE, Portfolio, valid_name
from wolvwealth.storage.repository import PortfolioRepository

MODES = ("merge", "replace")


@dataclasses.dataclass
class ImportOutcome:
    """What an import did."""

    portfolio: Portfolio  # state after the import
    imported: int  # tickers that now hold shares as a result of this import
    replaced: bool
    skipped_unknown: list
    ignored_lines: list
    cash_set: float = None


def resolve_target(repo: PortfolioRepository, workspace, name, create: bool = False) -> Portfolio:
    """Find (or, with ``create``, create) the portfolio an import targets."""
    if workspace is None:
        raise ValidationError('No workspace given. Pass "workspace" or open one in the browser first.')
    if not isinstance(workspace, str) or not repo.workspace_exists(workspace):
        raise NotFoundError("Unknown workspace.")
    if not isinstance(name, str):
        raise ValidationError("Invalid portfolio. portfolio must be a string (a portfolio name).")
    portfolio = repo.get_portfolio(workspace, name=name)
    if portfolio is not None:
        return portfolio
    if not create:
        raise NotFoundError("Unknown portfolio for this workspace. Pass create_portfolio: true to create it.")
    if not valid_name(name):
        raise ValidationError(f"Invalid portfolio name: {NAME_RULE}.")
    return repo.create_portfolio(workspace, name)


def parse_holdings_map(holdings, market: MarketData) -> holdings_parse.ParsedHoldings:
    """Validate a ``{ticker: shares}`` object the way the text parser validates lines."""
    if not isinstance(holdings, dict):
        raise ValidationError("Invalid holdings. holdings must be an object of ticker to share count.")
    parsed = holdings_parse.ParsedHoldings()
    if not holdings:
        parsed.errors.append("No holdings given.")
    for ticker, shares in holdings.items():
        symbol = holdings_parse.normalize_ticker(ticker)
        if not isinstance(shares, (int, float)) or isinstance(shares, bool):
            parsed.errors.append(f"{symbol}: shares must be a number")
        elif shares < 0:
            parsed.errors.append(f"{symbol}: shares must be zero or more")
        elif symbol not in market:
            parsed.unknown.append(symbol)
        else:
            parsed.holdings[symbol] = float(shares)
    return parsed


def import_holdings(
    repo: PortfolioRepository,
    market: MarketData,
    portfolio: Portfolio,
    *,
    text: str = None,
    holdings: dict = None,
    cash=None,
    mode: str = "merge",
    skip_unknown: bool = False,
) -> ImportOutcome:
    """Parse, validate, and write holdings into ``portfolio`` in one transaction."""
    if mode not in MODES:
        raise ValidationError('Invalid mode. mode must be "merge" or "replace".')
    if not isinstance(skip_unknown, bool):
        raise ValidationError("Invalid skip_unknown. skip_unknown must be a boolean.")
    if cash is not None and (not isinstance(cash, (int, float)) or isinstance(cash, bool) or cash < 0):
        raise ValidationError("Invalid cash. cash must be a number of zero or more.")
    if (text is None) == (holdings is None):
        raise ValidationError('Pass either "holdings" (object) or "csv" (string), not both.')

    if holdings is not None:
        parsed = parse_holdings_map(holdings, market)
    else:
        if not isinstance(text, str):
            raise ValidationError("Invalid csv. csv must be a string.")
        parsed = holdings_parse.parse_holdings(text, universe=market.universe)

    if parsed.errors or (parsed.unknown and not skip_unknown):
        raise ValidationError(
            "Nothing imported. Fix the listed problems or set skip_unknown.",
            details={"errors": parsed.errors, "unknown_tickers": parsed.unknown},
        )

    replace = mode == "replace"
    imported = repo.write_holdings(portfolio.workspace, portfolio.id, parsed.holdings, replace=replace)
    final_cash = cash if cash is not None else parsed.cash
    if final_cash is not None:
        final_cash = round(final_cash, 2)
        repo.set_cash(portfolio.workspace, portfolio.id, final_cash)
    return ImportOutcome(
        portfolio=repo.get_portfolio(portfolio.workspace, portfolio_id=portfolio.id),
        imported=imported,
        replaced=replace,
        skipped_unknown=parsed.unknown,
        ignored_lines=parsed.ignored,
        cash_set=final_cash,
    )
