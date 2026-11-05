"""JSON API on localhost. No auth; machine-readable errors.

DomainError subclasses become ``{"error": {"message", "status_code", "payload"?}}``
with 400 (validation) or 404 (not found).
"""

import flask

from wolvwealth.domain.errors import DomainError, NotFoundError
from wolvwealth.services import holdings_import, market_data, optimize, workspaces as workspace_service
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
            "/api/market/": "GET: price date, ticker universe, universe aliases, current T-bill yield",
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
    services = context.services()
    report = market_data.status(data, services.market.tbill, services.settings.auto_refresh_max_age_days)
    report["tickers"] = list(data.tradeable)
    return flask.jsonify(report)


@blueprint.route("/workspaces/", methods=["GET"])
def workspaces():
    """Every workspace with its portfolios and holdings."""
    return flask.jsonify(workspace_service.list_all(context.repo()))


@blueprint.route("/optimize/", methods=["POST"])
def run_optimize():
    """Optimize explicit holdings or a stored portfolio."""
    result = optimize.optimize(
        _json_body(),
        context.market(),
        repo=context.repo(),
        default_workspace=flask.session.get("workspace"),
        tbill=context.services().market.tbill,
    )
    return flask.jsonify(result)


@blueprint.route("/holdings/import/", methods=["POST"])
def import_holdings():
    """Bulk-import holdings into a stored portfolio.

    Body: workspace (optional; session fallback), portfolio, create_portfolio,
    holdings (object) OR csv (string), cash, risk_free_rate (number or "tbill"),
    mode ("merge"|"replace"), skip_unknown.
    """
    result = holdings_import.run(
        context.repo(),
        context.market(),
        _json_body(),
        tbill=context.services().market.tbill,
        default_workspace=flask.session.get("workspace"),
    )
    return flask.jsonify(result)
