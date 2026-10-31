"""PortfolioRepository: every read and write of workspaces, portfolios, and holdings.

Takes an open connection; the caller owns the transaction. Every portfolio
query is scoped by workspace so a guessed id can never reach another
workspace's data (tidiness, not security: there are no passwords).
"""

from wolvwealth.domain.models import Portfolio, Workspace

#: Every column of ``portfolios`` the Portfolio dataclass carries, in one place.
PORTFOLIO_COLUMNS = "id, workspace, name, cash, risk_free_rate, created"


class PortfolioRepository:
    """SQL for the three tables, returning domain objects."""

    def __init__(self, connection) -> None:
        """Wrap an open sqlite3 connection with dict rows."""
        self.connection = connection

    # --- workspaces ---------------------------------------------------------

    def list_workspaces(self) -> list:
        """All workspaces with portfolio counts, oldest first."""
        rows = self.connection.execute(
            "SELECT w.name, w.created, COUNT(p.id) AS portfolio_count "
            "FROM workspaces w LEFT JOIN portfolios p ON p.workspace = w.name "
            "GROUP BY w.name ORDER BY w.created, w.name"
        ).fetchall()
        return [Workspace(**row) for row in rows]

    def workspace_exists(self, name) -> bool:
        """Return true if a workspace with this name exists."""
        row = self.connection.execute("SELECT 1 FROM workspaces WHERE name = ?", (name,)).fetchone()
        return row is not None

    def create_workspace(self, name: str) -> None:
        """Create an empty workspace."""
        self.connection.execute("INSERT INTO workspaces(name) VALUES (?)", (name,))

    def delete_workspace(self, name: str) -> None:
        """Delete a workspace and, via cascade, its portfolios and holdings."""
        self.connection.execute("DELETE FROM workspaces WHERE name = ?", (name,))

    # --- portfolios ---------------------------------------------------------

    def _with_holdings(self, row: dict) -> Portfolio:
        holdings = self.connection.execute(
            "SELECT ticker, shares FROM holdings WHERE portfolio_id = ? ORDER BY ticker", (row["id"],)
        ).fetchall()
        return Portfolio(holdings={h["ticker"]: h["shares"] for h in holdings}, **row)

    def list_portfolios(self, workspace: str) -> list:
        """All portfolios in a workspace, each with holdings."""
        rows = self.connection.execute(
            f"SELECT {PORTFOLIO_COLUMNS} FROM portfolios WHERE workspace = ? ORDER BY created, name",
            (workspace,),
        ).fetchall()
        return [self._with_holdings(row) for row in rows]

    def get_portfolio(self, workspace: str, name: str = None, portfolio_id: int = None):
        """One portfolio by name or id within a workspace, or None."""
        if portfolio_id is not None:
            row = self.connection.execute(
                f"SELECT {PORTFOLIO_COLUMNS} FROM portfolios WHERE workspace = ? AND id = ?",
                (workspace, portfolio_id),
            ).fetchone()
        else:
            row = self.connection.execute(
                f"SELECT {PORTFOLIO_COLUMNS} FROM portfolios WHERE workspace = ? AND name = ?",
                (workspace, name),
            ).fetchone()
        return None if row is None else self._with_holdings(row)

    def create_portfolio(self, workspace: str, name: str, cash: float = 0.0) -> Portfolio:
        """Create a portfolio and return it."""
        self.connection.execute(
            "INSERT INTO portfolios(workspace, name, cash) VALUES (?, ?, ?)", (workspace, name, cash)
        )
        return self.get_portfolio(workspace, name=name)

    def delete_portfolio(self, workspace: str, portfolio_id: int) -> None:
        """Delete a portfolio and its holdings."""
        self.connection.execute("DELETE FROM portfolios WHERE workspace = ? AND id = ?", (workspace, portfolio_id))

    def set_cash(self, workspace: str, portfolio_id: int, cash: float) -> None:
        """Set the uninvested cash of a portfolio."""
        self.connection.execute(
            "UPDATE portfolios SET cash = ? WHERE workspace = ? AND id = ?", (cash, workspace, portfolio_id)
        )

    def set_risk_free_rate(self, workspace: str, portfolio_id: int, rate) -> None:
        """Set the yield a portfolio's uninvested money earns, as a fraction; None clears it."""
        self.connection.execute(
            "UPDATE portfolios SET risk_free_rate = ? WHERE workspace = ? AND id = ?", (rate, workspace, portfolio_id)
        )

    # --- holdings -----------------------------------------------------------

    def set_holding(self, workspace: str, portfolio_id: int, ticker: str, shares: float) -> None:
        """Upsert one holding. Zero shares removes it."""
        self.write_holdings(workspace, portfolio_id, {ticker: shares})

    def write_holdings(self, workspace: str, portfolio_id: int, holdings: dict, replace: bool = False) -> int:
        """Write many holdings; return how many tickers now hold shares.

        replace=True drops every existing holding first so the portfolio ends
        up exactly equal to ``holdings``. Otherwise each ticker is upserted and
        zero shares removes it. The caller's transaction makes this atomic.
        """
        if self.get_portfolio(workspace, portfolio_id=portfolio_id) is None:
            return 0
        if replace:
            self.connection.execute("DELETE FROM holdings WHERE portfolio_id = ?", (portfolio_id,))
        written = 0
        for ticker, shares in holdings.items():
            if shares == 0:
                self.connection.execute(
                    "DELETE FROM holdings WHERE portfolio_id = ? AND ticker = ?", (portfolio_id, ticker)
                )
            else:
                self.connection.execute(
                    "INSERT INTO holdings(portfolio_id, ticker, shares) VALUES (?, ?, ?) "
                    "ON CONFLICT(portfolio_id, ticker) DO UPDATE SET shares = excluded.shares",
                    (portfolio_id, ticker, shares),
                )
                written += 1
        return written

    def dump(self) -> dict:
        """Every row of every table, for the dump-db command."""
        return {
            table: self.connection.execute(f"SELECT * FROM {table}").fetchall()  # fixed table names
            for table in ("workspaces", "portfolios", "holdings")
        }
