"""JSON API on localhost. No auth; machine-readable errors.

DomainError subclasses become ``{"error": {"message", "status_code", "payload"?}}``
with 400 (validation) or 404 (not found).
"""

import flask

from wolvwealth.domain.errors import DomainError, NotFoundError
from wolvwealth.domain.market import UNIVERSE_ALIASES
from wolvwealth.services import holdings_import, optimize
from wolvwealth.web import context

blueprint = flask.Blueprint("api", __name__)


@blueprint.errorhandler(DomainError)
def handle_domain_error(error: DomainError):
    """Serialize a domain error as JSON."""
    status = 404 if isinstance(error, NotFoundError) else 400
    body = {"error": {"message": error.message, "status_code": status}}
    if error.details:
        body["error"]["payload"] = error.details
    return flask.jsonify(body), status


def _json_body() -> dict:
    """Return the request body as a dict; raise DomainError (a 400) if it is not one."""
    try:
        body = flask.request.get_json(force=True)
    except Exception as err:  # pylint: disable=broad-except
        raise DomainError("Parse Error. Unable to parse request as JSON.") from err
    if not isinstance(body, dict):
        raise DomainError("Parse Error. Request body must be a JSON object.")
    return body


@blueprint.route("/", methods=["GET", "POST"])
def index():
    """Usage summary."""
    return flask.jsonify(
        {
            "/api/": "GET: this index",
            "/api/market/": "GET: price date, ticker universe, universe aliases",
            "/api/optimize/": "POST: optimize a portfolio; returns target allocation, trades, metrics",
            "/api/workspaces/": "GET: list workspaces and their portfolios",
            "/api/holdings/import/": "POST: bulk-load holdings into a stored portfolio (holdings map or csv text)",
            "contract": "API.md in the install directory documents every field and error.",
            "note": "Prices come from a local cache; check as_of before acting on a trade list.",
        }
    )


@blueprint.route("/market/", methods=["GET"])
def market():
    """Price-cache freshness and the tickers a request may name.

    An agent checks ``as_of`` before trusting a trade list, and uses
    ``tickers`` to validate symbols without a round trip per guess.
    """
    data = context.market()
    return flask.jsonify(
        {
            "as_of": str(data.as_of),
            "age_days": data.age_days(),
            "universe_size": len(data.universe),
            "tradeable_size": len(data.tradeable),
            "universe_aliases": sorted(UNIVERSE_ALIASES),
            "tickers": list(data.tradeable),
        }
    )


@blueprint.route("/workspaces/", methods=["GET"])
def workspaces():
    """Every workspace with its portfolios and holdings."""
    repo = context.repo()
    return flask.jsonify(
        {
            "workspaces": [
                {
                    "name": workspace.name,
                    "created": workspace.created,
                    "portfolios": [
                        {"name": p.name, "cash": p.cash, "holdings": p.holdings}
                        for p in repo.list_portfolios(workspace.name)
                    ],
                }
                for workspace in repo.list_workspaces()
            ]
        }
    )


@blueprint.route("/optimize/", methods=["POST"])
def run_optimize():
    """Optimize explicit holdings or a stored portfolio."""
    result = optimize.optimize(
        _json_body(),
        context.market(),
        repo=context.repo(),
        default_workspace=flask.session.get("workspace"),
        settings=context.services().settings,
    )
    return flask.jsonify(result)


@blueprint.route("/holdings/import/", methods=["POST"])
def import_holdings():
    """Bulk-import holdings into a stored portfolio.

    Body: workspace (optional; session fallback), portfolio, create_portfolio,
    holdings (object) OR csv (string), cash, mode ("merge"|"replace"), skip_unknown.
    """
    body = _json_body()
    repo = context.repo()
    if "holdings" in body and "csv" in body:
        raise DomainError('Pass either "holdings" (object) or "csv" (string), not both.')
    if "holdings" not in body and "csv" not in body:
        raise DomainError('Missing input. Pass "holdings" (object) or "csv" (string).')
    portfolio = holdings_import.resolve_target(
        repo,
        body.get("workspace", flask.session.get("workspace")),
        body.get("portfolio"),
        create=body.get("create_portfolio") is True,
    )
    outcome = holdings_import.import_holdings(
        repo,
        context.market(),
        portfolio,
        text=body.get("csv"),
        holdings=body.get("holdings"),
        cash=body.get("cash"),
        mode=body.get("mode", "merge"),
        skip_unknown=body.get("skip_unknown", False),
    )
    return flask.jsonify(
        {
            "workspace": outcome.portfolio.workspace,
            "portfolio": outcome.portfolio.name,
            "mode": "replace" if outcome.replaced else "merge",
            "imported": outcome.imported,
            "skipped_unknown": outcome.skipped_unknown,
            "ignored_lines": outcome.ignored_lines,
            "cash": outcome.portfolio.cash,
            "holdings": outcome.portfolio.holdings,
        }
    )
