"""JSON API: bulk-load holdings into a stored portfolio.

This is the agent's way to put a brokerage export into a workspace before
calling /api/optimize/ on it. No auth, localhost only, machine-readable
errors via InvalidUsage.
"""
import flask
import wolvwealth
import wolvwealth.holdings_import as holdings_import
import wolvwealth.portfolios as portfolios
from wolvwealth.api.api_exceptions import InvalidUsage
from wolvwealth.api.state import ApplicationState


def _resolve_target(body: dict):
    """Return (workspace, portfolio dict) for the request, creating the portfolio if asked."""
    workspace = body.get("workspace", flask.session.get("workspace"))
    if workspace is None:
        raise InvalidUsage('No workspace given. Pass "workspace" or open one in the browser first.')
    if not isinstance(workspace, str) or not portfolios.workspace_exists(workspace):
        raise InvalidUsage("Unknown workspace.", status_code=404)
    name = body.get("portfolio")
    if not isinstance(name, str):
        raise InvalidUsage("Invalid portfolio. portfolio must be a string (a portfolio name).")
    portfolio = portfolios.fetch_portfolio(workspace, name=name)
    if portfolio is None:
        if body.get("create_portfolio") is not True:
            raise InvalidUsage(
                "Unknown portfolio for this workspace. Pass create_portfolio: true to create it.", status_code=404
            )
        if not portfolios.valid_name(name):
            raise InvalidUsage("Invalid portfolio name: 1-64 letters, digits, spaces, dots, dashes, or underscores.")
        portfolios.create_portfolio(workspace, name)
        portfolio = portfolios.fetch_portfolio(workspace, name=name)
    return workspace, portfolio


def _parse_body(body: dict) -> holdings_import.ParsedHoldings:
    """Turn either a ``holdings`` map or a ``csv`` text block into ParsedHoldings."""
    universe = ApplicationState().TICKER_UNIVERSE
    if "holdings" in body and "csv" in body:
        raise InvalidUsage('Pass either "holdings" (object) or "csv" (string), not both.')
    if "holdings" in body:
        holdings = body["holdings"]
        if not isinstance(holdings, dict):
            raise InvalidUsage("Invalid holdings. holdings must be an object of ticker to share count.")
        parsed = holdings_import.ParsedHoldings()
        known = set(universe)
        for ticker, shares in holdings.items():
            symbol = holdings_import.normalize_ticker(ticker)
            if not isinstance(shares, (int, float)) or isinstance(shares, bool):
                parsed.errors.append(f"{symbol}: shares must be a number")
            elif shares < 0:
                parsed.errors.append(f"{symbol}: shares must be zero or more")
            elif symbol not in known:
                parsed.unknown.append(symbol)
            else:
                parsed.holdings[symbol] = float(shares)
        if not holdings:
            parsed.errors.append("No holdings given.")
        return parsed
    if "csv" in body:
        if not isinstance(body["csv"], str):
            raise InvalidUsage("Invalid csv. csv must be a string.")
        return holdings_import.parse_holdings(body["csv"], universe=universe)
    raise InvalidUsage('Missing input. Pass "holdings" (object) or "csv" (string).')


@wolvwealth.app.route("/api/holdings/import/", methods=["POST"])
def api_holdings_import():
    """Bulk-import holdings into a stored portfolio.

    Request JSON:
        workspace (str, optional; falls back to the browser session),
        portfolio (str), create_portfolio (bool, default false),
        holdings ({ticker: shares}) OR csv (str, same formats as the paste box),
        cash (number, optional; a CASH row in csv also sets it),
        mode ("merge" default | "replace"), skip_unknown (bool, default false).

    All-or-nothing: a 400 lists every problem and writes nothing.
    """
    try:
        body = flask.request.get_json(force=True)
    except Exception as err:  # pylint: disable=broad-except
        raise InvalidUsage("Parse Error. Unable to parse request as JSON.") from err
    if not isinstance(body, dict):
        raise InvalidUsage("Parse Error. Request body must be a JSON object.")

    mode = body.get("mode", "merge")
    if mode not in ("merge", "replace"):
        raise InvalidUsage('Invalid mode. mode must be "merge" or "replace".')
    skip_unknown = body.get("skip_unknown", False)
    if not isinstance(skip_unknown, bool):
        raise InvalidUsage("Invalid skip_unknown. skip_unknown must be a boolean.")
    cash = body.get("cash")
    if cash is not None and (not isinstance(cash, (int, float)) or isinstance(cash, bool) or cash < 0):
        raise InvalidUsage("Invalid cash. cash must be a number of zero or more.")

    parsed = _parse_body(body)
    if parsed.errors or (parsed.unknown and not skip_unknown):
        raise InvalidUsage(
            "Nothing imported. Fix the listed problems or set skip_unknown.",
            payload={"errors": parsed.errors, "unknown_tickers": parsed.unknown},
        )

    workspace, portfolio = _resolve_target(body)
    written = portfolios.import_holdings(workspace, portfolio["id"], parsed.holdings, replace=mode == "replace")
    final_cash = cash if cash is not None else parsed.cash
    if final_cash is not None:
        portfolios.set_cash(workspace, portfolio["id"], round(final_cash, 2))
    portfolio = portfolios.fetch_portfolio(workspace, portfolio_id=portfolio["id"])
    return flask.jsonify(
        {
            "workspace": workspace,
            "portfolio": portfolio["name"],
            "mode": mode,
            "imported": written,
            "skipped_unknown": parsed.unknown,
            "ignored_lines": parsed.ignored,
            "cash": portfolio["cash"],
            "holdings": portfolio["holdings"],
        }
    )
