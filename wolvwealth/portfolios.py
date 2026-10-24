"""Database helpers for workspaces and portfolios.

Shared by the HTML views and the JSON API so both read and write portfolios
the same way. All functions expect to run inside a Flask request context.
"""
import re
import wolvwealth
import wolvwealth.model

WORKSPACE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
PORTFOLIO_NAME_RE = WORKSPACE_NAME_RE


def valid_name(name) -> bool:
    """Return true if name is usable as a workspace or portfolio name."""
    return isinstance(name, str) and WORKSPACE_NAME_RE.match(name) is not None


def fetch_workspaces() -> list:
    """Return all workspaces with their portfolio counts, oldest first."""
    connection = wolvwealth.model.get_db()
    return connection.execute(
        "SELECT w.name, w.created, COUNT(p.id) AS portfolio_count "
        "FROM workspaces w LEFT JOIN portfolios p ON p.workspace = w.name "
        "GROUP BY w.name ORDER BY w.created, w.name"
    ).fetchall()


def workspace_exists(name: str) -> bool:
    """Return true if a workspace with this name exists."""
    connection = wolvwealth.model.get_db()
    return connection.execute("SELECT 1 FROM workspaces WHERE name = ?", (name,)).fetchone() is not None


def create_workspace(name: str) -> None:
    """Create a new, empty workspace."""
    connection = wolvwealth.model.get_db()
    connection.execute("INSERT INTO workspaces(name) VALUES (?)", (name,))


def delete_workspace(name: str) -> None:
    """Delete a workspace and, via cascade, all its portfolios and holdings."""
    connection = wolvwealth.model.get_db()
    connection.execute("DELETE FROM workspaces WHERE name = ?", (name,))


def _attach_holdings(connection, portfolio: dict) -> dict:
    """Add a holdings dict (ticker -> shares) to a portfolio row."""
    rows = connection.execute(
        "SELECT ticker, shares FROM holdings WHERE portfolio_id = ? ORDER BY ticker",
        (portfolio["id"],),
    ).fetchall()
    portfolio["holdings"] = {row["ticker"]: row["shares"] for row in rows}
    return portfolio


def fetch_portfolios(workspace: str) -> list:
    """Return all portfolios in a workspace, each with its holdings."""
    connection = wolvwealth.model.get_db()
    portfolios = connection.execute(
        "SELECT id, name, cash, created FROM portfolios WHERE workspace = ? ORDER BY created, name",
        (workspace,),
    ).fetchall()
    return [_attach_holdings(connection, p) for p in portfolios]


def fetch_portfolio(workspace: str, name: str = None, portfolio_id: int = None):
    """Return one portfolio (with holdings) by name or id, or None.

    The workspace is always required so one workspace can never read another
    workspace's portfolio by guessing an id.
    """
    connection = wolvwealth.model.get_db()
    if portfolio_id is not None:
        row = connection.execute(
            "SELECT id, name, cash, created FROM portfolios WHERE workspace = ? AND id = ?",
            (workspace, portfolio_id),
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT id, name, cash, created FROM portfolios WHERE workspace = ? AND name = ?",
            (workspace, name),
        ).fetchone()
    if row is None:
        return None
    return _attach_holdings(connection, row)


def create_portfolio(workspace: str, name: str, cash: float = 0.0) -> None:
    """Create a portfolio in a workspace."""
    connection = wolvwealth.model.get_db()
    connection.execute(
        "INSERT INTO portfolios(workspace, name, cash) VALUES (?, ?, ?)",
        (workspace, name, cash),
    )


def delete_portfolio(workspace: str, portfolio_id: int) -> None:
    """Delete a portfolio and its holdings."""
    connection = wolvwealth.model.get_db()
    connection.execute(
        "DELETE FROM portfolios WHERE workspace = ? AND id = ?",
        (workspace, portfolio_id),
    )


def set_cash(workspace: str, portfolio_id: int, cash: float) -> None:
    """Set the uninvested cash of a portfolio."""
    connection = wolvwealth.model.get_db()
    connection.execute(
        "UPDATE portfolios SET cash = ? WHERE workspace = ? AND id = ?",
        (cash, workspace, portfolio_id),
    )


def set_holding(workspace: str, portfolio_id: int, ticker: str, shares: float) -> None:
    """Set the share count for one ticker. Zero shares removes the holding."""
    connection = wolvwealth.model.get_db()
    owned = connection.execute(
        "SELECT 1 FROM portfolios WHERE workspace = ? AND id = ?",
        (workspace, portfolio_id),
    ).fetchone()
    if owned is None:
        return
    if shares == 0:
        connection.execute(
            "DELETE FROM holdings WHERE portfolio_id = ? AND ticker = ?",
            (portfolio_id, ticker),
        )
    else:
        connection.execute(
            "INSERT INTO holdings(portfolio_id, ticker, shares) VALUES (?, ?, ?) "
            "ON CONFLICT(portfolio_id, ticker) DO UPDATE SET shares = excluded.shares",
            (portfolio_id, ticker, shares),
        )
