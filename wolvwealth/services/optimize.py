"""Use case: optimize a portfolio, given either explicit inputs or a stored portfolio reference.

The risk-free rate is resolved here, in this order: the request's own
``risk_free_rate`` (a number, or ``"tbill"`` for the cached Treasury
yield); the rate stored on the portfolio the request refers to; otherwise
the domain's 0% fallback, which the response flags.
"""

from wolvwealth.domain import optimizer
from wolvwealth.domain.errors import NotFoundError, ValidationError
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


def payload_for_portfolio(
    portfolio, universe_alias: str, max_positions=None, max_weight=None, max_turnover=None
) -> dict:
    """Build the request the browser form implies for a stored portfolio.

    Held tickers join the universe so the optimizer is allowed to keep them.
    The stored risk-free rate is not copied in: ``optimize`` reads it from
    the portfolio itself so the response can say that is where it came from.
    """
    constraints = {}
    if max_positions is not None:
        constraints["max_positions"] = max_positions
    if max_weight is not None:
        constraints["max_weight"] = max_weight
    if max_turnover is not None:
        constraints["max_turnover"] = max_turnover
    return {
        "initial_cash": portfolio.cash,
        "initial_holdings": portfolio.holdings,
        "universe": [universe_alias] + list(portfolio.holdings),
        "constraints": constraints,
    }
