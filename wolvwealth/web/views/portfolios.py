"""The workspace page: portfolios, holdings, cash, and bulk import."""

import flask

from wolvwealth.domain.errors import DomainError
from wolvwealth.domain.models import NAME_RULE, valid_name
from wolvwealth.services import holdings_import
from wolvwealth.web import context
from wolvwealth.web.views import blueprint


def _back():
    return flask.redirect(flask.url_for("views.show_workspace"))


def _to_picker():
    return flask.redirect(flask.url_for("views.show_workspaces"))


def _priced(portfolio, market) -> dict:
    """Template context for one portfolio: holdings valued at the latest cached price."""
    rows = []
    for ticker, shares in portfolio.holdings.items():
        # A ticker can drop out of the price data (delisting); show it as unpriced rather than failing the page.
        price = market.price(ticker) if market.has_price(ticker) else None
        rows.append({"ticker": ticker, "shares": shares, "price": price, "value": round(shares * (price or 0), 2)})
    return {
        "id": portfolio.id,
        "name": portfolio.name,
        "cash": portfolio.cash,
        "holdings": portfolio.holdings,
        "holding_rows": rows,
        "total_value": round(portfolio.cash + sum(r["value"] for r in rows), 2),
    }


def _portfolio_id():
    try:
        return int(flask.request.form.get("portfolio_id", ""))
    except ValueError:
        return None


def _cash(raw):
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


@blueprint.route("/workspace/", methods=["GET"])
def show_workspace():
    """Display the open workspace with all its portfolios."""
    workspace = context.current_workspace()
    if workspace is None:
        return _to_picker()
    market = context.market()
    return flask.render_template(
        "workspace.html",
        workspace=workspace,
        portfolios=[_priced(p, market) for p in context.repo().list_portfolios(workspace)],
        prices_as_of=str(market.as_of),
        risk_free_rate=context.services().settings.risk_free_rate,
    )


@blueprint.route("/portfolios/create/", methods=["POST"])
def portfolios_create():
    """Create a portfolio in the open workspace."""
    workspace = context.current_workspace()
    if workspace is None:
        return _to_picker()
    name = (flask.request.form.get("name") or "").strip()
    repo = context.repo()
    if not valid_name(name):
        flask.flash(f"Portfolio names are {NAME_RULE}.")
        return _back()
    if repo.get_portfolio(workspace, name=name) is not None:
        flask.flash(f"Portfolio '{name}' already exists in this workspace.")
        return _back()
    cash = _cash(flask.request.form.get("cash", "0"))
    if cash is not None:
        repo.create_portfolio(workspace, name, cash)
    return _back()


@blueprint.route("/portfolios/delete/", methods=["POST"])
def portfolios_delete():
    """Delete a portfolio."""
    workspace = context.current_workspace()
    if workspace is None:
        return _to_picker()
    portfolio_id = _portfolio_id()
    if portfolio_id is not None:
        context.repo().delete_portfolio(workspace, portfolio_id)
    return _back()


@blueprint.route("/portfolios/cash/", methods=["POST"])
def portfolios_cash():
    """Set a portfolio's uninvested cash."""
    workspace = context.current_workspace()
    if workspace is None:
        return _to_picker()
    portfolio_id = _portfolio_id()
    cash = _cash(flask.request.form.get("cash", ""))
    if portfolio_id is not None and cash is not None:
        context.repo().set_cash(workspace, portfolio_id, cash)
    return _back()


@blueprint.route("/portfolios/holdings/", methods=["POST"])
def portfolios_holdings():
    """Add or update one holding. Zero shares removes it."""
    workspace = context.current_workspace()
    if workspace is None:
        return _to_picker()
    portfolio_id = _portfolio_id()
    if portfolio_id is None:
        return _back()
    ticker = (flask.request.form.get("ticker") or "").strip().upper()
    if ticker not in context.market():
        flask.flash(f"Unknown ticker '{ticker}'. It must be in the cached price data.")
        return _back()
    try:
        shares = float(flask.request.form.get("shares", ""))
    except ValueError:
        flask.flash("Shares must be a number.")
        return _back()
    if shares < 0:
        flask.flash("Shares must be zero or more (zero removes the holding).")
        return _back()
    context.repo().set_holding(workspace, portfolio_id, ticker, shares)
    return _back()


@blueprint.route("/portfolios/import/", methods=["POST"])
def portfolios_import():
    """Bulk-load holdings from a pasted block or an uploaded file; all-or-nothing."""
    workspace = context.current_workspace()
    if workspace is None:
        return _to_picker()
    repo = context.repo()
    portfolio_id = _portfolio_id()
    portfolio = None if portfolio_id is None else repo.get_portfolio(workspace, portfolio_id=portfolio_id)
    if portfolio is None:
        flask.flash("That portfolio no longer exists.")
        return _back()

    text = flask.request.form.get("text") or ""
    upload = flask.request.files.get("file")
    if upload is not None and upload.filename:
        try:
            text = upload.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            flask.flash("The uploaded file is not UTF-8 text. Export as CSV and try again.")
            return _back()

    try:
        outcome = holdings_import.import_holdings(
            repo,
            context.market(),
            portfolio,
            text=text,
            mode=flask.request.form.get("mode", "merge"),
            skip_unknown=flask.request.form.get("skip_unknown") == "on",
        )
    except DomainError as error:
        problems = list(error.details.get("errors", []))
        unknown = error.details.get("unknown_tickers")
        if unknown:
            problems.append(
                "Unknown tickers (not in the cached price data): "
                + ", ".join(unknown)
                + ". Remove them or tick 'skip unknown tickers'."
            )
        if not problems:
            problems.append(error.message)
        flask.flash("Nothing imported. " + " ".join(problems[:10]) + (" ..." if len(problems) > 10 else ""))
        return _back()

    summary = f"Imported {outcome.imported} holding{'s' if outcome.imported != 1 else ''}"
    summary += " (replaced existing)." if outcome.replaced else "."
    if outcome.cash_set is not None:
        summary += f" Cash set to ${outcome.cash_set:,.2f}."
    if outcome.skipped_unknown:
        summary += " Skipped unknown tickers: " + ", ".join(outcome.skipped_unknown) + "."
    if outcome.ignored_lines:
        count = len(outcome.ignored_lines)
        summary += f" Ignored {count} line{'s' if count != 1 else ''} without a share count."
    flask.flash(summary)
    return _back()
