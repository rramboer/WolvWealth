"""Use case: describe every workspace with its portfolios, as the API and the MCP tools publish them."""

from wolvwealth.storage.repository import PortfolioRepository


def list_all(repo: PortfolioRepository) -> dict:
    """Return ``{"workspaces": [...]}`` with each portfolio's cash, risk-free rate, and holdings."""
    return {
        "workspaces": [
            {
                "name": workspace.name,
                "created": workspace.created,
                "portfolios": [
                    {"name": p.name, "cash": p.cash, "risk_free_rate": p.risk_free_rate, "holdings": p.holdings}
                    for p in repo.list_portfolios(workspace.name)
                ],
            }
            for workspace in repo.list_workspaces()
        ]
    }
