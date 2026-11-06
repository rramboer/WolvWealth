"""Use case: optimize a portfolio, given either explicit inputs or a stored portfolio reference.

The risk-free rate is resolved here, in this order: the request's own
``risk_free_rate`` (a number, or ``"tbill"`` for the cached Treasury
yield); the rate stored on the portfolio the request refers to; otherwise
the domain's 0% fallback, which the response flags.
"""

from wolvwealth.domain import optimizer
from wolvwealth.domain.errors import ConflictError, NotFoundError, ValidationError
from wolvwealth.domain.market import MarketData
from wolvwealth.domain.models import Portfolio
from wolvwealth.services import risk_free
from wolvwealth.storage.repository import PortfolioRepository


def resolve_portfolio_reference(payload, repo: PortfolioRepository, default_workspace: str = None) -> tuple:
    """Expand ``{"workspace": ..., "portfolio": ...}`` into initial_cash and initial_holdings.

    Explicit initial_cash / initial_holdings in the same payload win over the
    stored values. ``default_workspace`` is the browser session's open
    workspace, used when the payload names none. Returns ``(payload,
    portfolio)``: a new dict, and the stored portfolio it drew on, or None.
    """
    if not isinstance(payload, dict) or "portfolio" not in payload:
        return payload, None
    payload = dict(payload)
    name = payload.pop("portfolio")
    if not isinstance(name, str):
        raise ValidationError("Invalid portfolio. portfolio must be a string (a portfolio name).")
    workspace = payload.pop("workspace", None)
    if workspace is None:
        workspace = default_workspace
    if workspace is None:
        raise ValidationError('No workspace given. Pass "workspace" or open one in the browser first.')
    if not isinstance(workspace, str):
        raise ValidationError("Invalid workspace. workspace must be a string.")
    portfolio = repo.get_portfolio(workspace, name=name)
    if portfolio is None:
        raise NotFoundError("Unknown portfolio for this workspace.")
    payload.setdefault("initial_cash", portfolio.cash)
    payload.setdefault("initial_holdings", portfolio.holdings)
    return payload, portfolio


def optimize(
    payload,
    market: MarketData,
    repo: PortfolioRepository = None,
    default_workspace: str = None,
    portfolio: Portfolio = None,
    tbill: dict = None,
) -> dict:
    """Resolve a stored-portfolio reference if present, then validate and solve.

    ``portfolio`` is the stored portfolio the browser form is running (the
    API finds its own through the payload's reference). ``tbill`` is the
    cached Treasury yield, needed only if the request says ``"tbill"``.
    """
    if repo is not None:
        payload, portfolio = resolve_portfolio_reference(payload, repo, default_workspace)
    if not isinstance(payload, dict):
        raise ValidationError("Parse Error. Request body must be a JSON object.")
    if portfolio is not None and portfolio.problems:
        raise ConflictError(
            f"Portfolio {portfolio.workspace}/{portfolio.name} has stored values that cannot be used, so no "
            f"trades were planned: {'; '.join(portfolio.problems)}.",
            {"problems": portfolio.problems},
        )
    payload = dict(payload)
    rate, source = risk_free.resolve(payload.get("risk_free_rate"), tbill)
    if rate is None and portfolio is not None and portfolio.risk_free_rate is not None:
        rate, source = portfolio.risk_free_rate, "portfolio"
    if rate is not None:
        payload["risk_free_rate"] = rate
    request = optimizer.parse_request(payload, market)
    if source is not None:
        request.risk_free_rate_source = source
    return optimizer.solve(request, market)


def universe_with_held(payload: dict) -> list:
    """Return the request's universe (or the default) plus every held ticker, so keeping a holding is possible.

    The raw API leaves this to the caller and documents the consequence: a
    held name outside the universe is sold in full. The browser form, the
    CLI and the MCP tools all add the held names instead, which is the
    safer default when a person or a model is about to act on the trades.
    """
    universe = payload.get("universe")
    holdings = payload.get("initial_holdings", {})
    if universe is None or universe == []:
        universe = [f"top{optimizer.DEFAULT_UNIVERSE_SIZE}"]
    if not isinstance(universe, list) or not isinstance(holdings, dict):
        return universe  # malformed: left as given so parse_request names the field
    return universe + [ticker for ticker in holdings if ticker not in universe]


def optimize_keeping_held(payload, market: MarketData, repo: PortfolioRepository, tbill: dict = None) -> tuple:
    """Resolve a stored-portfolio reference, add the held tickers to the universe, and solve.

    Returns ``(result, universe, portfolio)``: the API response body, the
    universe entries actually used, and the stored portfolio drawn on (or
    None). Shared by the CLI and the MCP ``optimize`` tool.
    """
    if not isinstance(payload, dict):
        raise ValidationError("Parse Error. Request body must be a JSON object.")
    payload, portfolio = resolve_portfolio_reference(payload, repo)
    payload = dict(payload, universe=universe_with_held(payload))
    result = optimize(payload, market, portfolio=portfolio, tbill=tbill)
    return result, payload["universe"], portfolio


def payload_for_portfolio(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    portfolio,
    universe_alias: str,
    max_positions=None,
    max_weight=None,
    max_turnover=None,
    cash_reserve=None,
    min_history_days=None,
) -> dict:
    """Build the request the browser form implies for a stored portfolio.

    Held tickers join the universe so the optimizer is allowed to keep them.
    The stored risk-free rate is not copied in: ``optimize`` reads it from
    the portfolio itself so the response can say that is where it came from.
    ``min_history_days`` is the form's override of the domain's default
    seasoning rule; None leaves the default in force.
    """
    constraints = {}
    if max_positions is not None:
        constraints["max_positions"] = max_positions
    if max_weight is not None:
        constraints["max_weight"] = max_weight
    if max_turnover is not None:
        constraints["max_turnover"] = max_turnover
    if cash_reserve is not None:
        constraints["cash_reserve"] = cash_reserve
    payload = {
        "initial_cash": portfolio.cash,
        "initial_holdings": portfolio.holdings,
        "universe": [universe_alias] + list(portfolio.holdings),
        "constraints": constraints,
    }
    if min_history_days is not None:
        payload["min_history_days"] = min_history_days
    return payload
