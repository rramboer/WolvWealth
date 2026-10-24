"""The workspace page: portfolios and holdings of the active workspace."""

import flask
from flask import render_template
import wolvwealth
import wolvwealth.portfolios as portfolios
from wolvwealth.api.state import ApplicationState
from wolvwealth.views.workspaces import current_workspace


def _require_workspace():
    """Return the active workspace name or None (caller should redirect)."""
    return current_workspace()


def _priced(portfolio: dict, state: ApplicationState) -> dict:
    """Add per-holding and total values at the latest cached price."""
    rows = []
    total = portfolio["cash"]
    for ticker, shares in portfolio["holdings"].items():
        price = state.fetch_ticker_price(ticker)
        value = round(shares * price, 2)
        total += value
        rows.append({"ticker": ticker, "shares": shares, "price": price, "value": value})
    portfolio["holding_rows"] = rows
    portfolio["total_value"] = round(total, 2)
    return portfolio


@wolvwealth.app.route("/workspace/", methods=["GET"])
def show_workspace():
    """Display the active workspace with all its portfolios."""
    workspace = _require_workspace()
    if workspace is None:
        return flask.redirect(flask.url_for("show_workspaces"))
    state = ApplicationState()
    context = {
        "workspace": workspace,
        "portfolios": [_priced(p, state) for p in portfolios.fetch_portfolios(workspace)],
        "prices_as_of": str(state.HISTORICAL_PRICES.index[-1].date()),
    }
    return render_template("workspace.html", **context)


@wolvwealth.app.route("/portfolios/create/", methods=["POST"])
def portfolios_create():
    """Create a portfolio in the active workspace."""
    workspace = _require_workspace()
    if workspace is None:
        return flask.redirect(flask.url_for("show_workspaces"))
    name = (flask.request.form.get("name") or "").strip()
    if not portfolios.valid_name(name):
        flask.flash("Portfolio names are 1-64 letters, digits, spaces, dots, dashes, or underscores.")
        return flask.redirect(flask.url_for("show_workspace"))
    if portfolios.fetch_portfolio(workspace, name=name) is not None:
        flask.flash(f"Portfolio '{name}' already exists in this workspace.")
        return flask.redirect(flask.url_for("show_workspace"))
    cash = _parse_cash(flask.request.form.get("cash", "0"))
    if cash is None:
        return flask.redirect(flask.url_for("show_workspace"))
    portfolios.create_portfolio(workspace, name, cash)
    return flask.redirect(flask.url_for("show_workspace"))


@wolvwealth.app.route("/portfolios/delete/", methods=["POST"])
def portfolios_delete():
    """Delete a portfolio from the active workspace."""
    workspace = _require_workspace()
    if workspace is None:
        return flask.redirect(flask.url_for("show_workspaces"))
    portfolio_id = _parse_portfolio_id()
    if portfolio_id is not None:
        portfolios.delete_portfolio(workspace, portfolio_id)
    return flask.redirect(flask.url_for("show_workspace"))


@wolvwealth.app.route("/portfolios/cash/", methods=["POST"])
def portfolios_cash():
    """Set the uninvested cash of a portfolio."""
    workspace = _require_workspace()
    if workspace is None:
        return flask.redirect(flask.url_for("show_workspaces"))
    portfolio_id = _parse_portfolio_id()
    cash = _parse_cash(flask.request.form.get("cash", ""))
    if portfolio_id is not None and cash is not None:
        portfolios.set_cash(workspace, portfolio_id, cash)
    return flask.redirect(flask.url_for("show_workspace"))


@wolvwealth.app.route("/portfolios/holdings/", methods=["POST"])
def portfolios_holdings():
    """Add or update one holding. Zero shares removes it."""
    workspace = _require_workspace()
    if workspace is None:
        return flask.redirect(flask.url_for("show_workspaces"))
    portfolio_id = _parse_portfolio_id()
    if portfolio_id is None:
        return flask.redirect(flask.url_for("show_workspace"))
    ticker = (flask.request.form.get("ticker") or "").strip().upper()
    if ticker not in ApplicationState().TICKER_UNIVERSE:
        flask.flash(f"Unknown ticker '{ticker}'. It must be in the cached price data.")
        return flask.redirect(flask.url_for("show_workspace"))
    try:
        shares = float(flask.request.form.get("shares", ""))
    except ValueError:
        flask.flash("Shares must be a number.")
        return flask.redirect(flask.url_for("show_workspace"))
    if shares < 0:
        flask.flash("Shares must be zero or more (zero removes the holding).")
        return flask.redirect(flask.url_for("show_workspace"))
    portfolios.set_holding(workspace, portfolio_id, ticker, shares)
    return flask.redirect(flask.url_for("show_workspace"))


def _parse_portfolio_id():
    """Read the portfolio_id form field as an int, or None."""
    try:
        return int(flask.request.form.get("portfolio_id", ""))
    except ValueError:
        return None


def _parse_cash(raw: str):
    """Parse a cash amount; flashes and returns None when invalid."""
    try:
        cash = float(raw or "0")
    except ValueError:
        flask.flash("Cash must be a number.")
        return None
    if cash < 0:
        flask.flash("Cash must be zero or more.")
        return None
    return round(cash, 2)
