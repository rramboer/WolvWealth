"""Use case: describe every workspace with its portfolios, as the API and the MCP tools publish them."""

from wolvwealth.storage.repository import PortfolioRepository


def _describe(portfolio) -> dict:
    """One portfolio as published; ``problems`` appears only when stored values had to be left out."""
    body = {
        "name": portfolio.name,
        "cash": portfolio.cash,
        "risk_free_rate": portfolio.risk_free_rate,
        "holdings": portfolio.holdings,
    }
    if portfolio.problems:
        body["problems"] = portfolio.problems
    return body


def list_all(repo: PortfolioRepository) -> dict:
    """Return ``{"workspaces": [...]}`` with each portfolio's cash, risk-free rate, and holdings."""
    return {
        "workspaces": [
            {
                "name": workspace.name,
                "created": workspace.created,
                "portfolios": [_describe(p) for p in repo.list_portfolios(workspace.name)],
            }
            for workspace in repo.list_workspaces()
        ]
    }
