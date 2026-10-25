"""Use case: optimize a portfolio, given either explicit inputs or a stored portfolio reference."""
from wolvwealth.domain import optimizer
from wolvwealth.domain.errors import NotFoundError, ValidationError
from wolvwealth.domain.market import MarketData
from wolvwealth.storage.repository import PortfolioRepository


def resolve_portfolio_reference(payload, repo: PortfolioRepository, default_workspace: str = None) -> dict:
    """Expand ``{"workspace": ..., "portfolio": ...}`` into initial_cash and initial_holdings.

    Explicit initial_cash / initial_holdings in the same payload win over the
    stored values. ``default_workspace`` is the browser session's open
    workspace, used when the payload names none. Returns a new dict.
    """
    if not isinstance(payload, dict) or "portfolio" not in payload:
        return payload
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
    return payload


def optimize(payload, market: MarketData, repo: PortfolioRepository = None, default_workspace: str = None) -> dict:
    """Resolve a stored-portfolio reference if present, then validate and solve."""
    if repo is not None:
        payload = resolve_portfolio_reference(payload, repo, default_workspace)
    return optimizer.optimize(payload, market)


def payload_for_portfolio(portfolio, universe_alias: str, max_positions=None, max_weight=None) -> dict:
    """Build the request the browser form implies for a stored portfolio.

    Held tickers join the universe so the optimizer is allowed to keep them.
    """
    constraints = {}
    if max_positions is not None:
        constraints["max_positions"] = max_positions
    if max_weight is not None:
        constraints["max_weight"] = max_weight
    return {
        "initial_cash": portfolio.cash,
        "initial_holdings": portfolio.holdings,
        "universe": [universe_alias] + list(portfolio.holdings),
        "constraints": constraints,
    }
