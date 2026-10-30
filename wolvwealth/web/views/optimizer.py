"""Optimize one saved portfolio from the browser and render the result."""

import flask

from wolvwealth.domain.errors import DomainError
from wolvwealth.domain.market import UNIVERSE_ALIASES
from wolvwealth.services import optimize
from wolvwealth.web import context
from wolvwealth.web.views import blueprint


@blueprint.route("/optimizer/run/", methods=["POST"])
def run_optimizer():
    """Read the form, build the request, solve, render results.html."""
    workspace = context.current_workspace()
    if workspace is None:
        return flask.redirect(flask.url_for("views.show_workspaces"))
    back = flask.redirect(flask.url_for("views.show_workspace"))
    try:
        portfolio_id = int(flask.request.form.get("portfolio_id", ""))
    except ValueError:
        return back
    portfolio = context.repo().get_portfolio(workspace, portfolio_id=portfolio_id)
    if portfolio is None:
        flask.flash("That portfolio no longer exists.")
        return back

    universe = flask.request.form.get("universe", "top100")
    if universe not in UNIVERSE_ALIASES:
        universe = "top100"
    max_positions = max_weight = None
    raw = flask.request.form.get("max_positions", "").strip()
    if raw:
        try:
            max_positions = int(raw)
        except ValueError:
            flask.flash("Max positions must be a whole number.")
            return back
    raw = flask.request.form.get("max_weight", "").strip()
    if raw:
        try:
            max_weight = float(raw)
        except ValueError:
            flask.flash("Max weight must be a number between 0 and 1.")
            return back

    market = context.market()
    try:
        payload = optimize.payload_for_portfolio(portfolio, universe, max_positions, max_weight)
        result = optimize.optimize(payload, market)
    except DomainError as error:
        flask.flash(error.message)
        return back

    allocation = sorted(result["optimized_portfolio"].items(), key=lambda kv: kv[1]["value"], reverse=True)
    return flask.render_template(
        "results.html",
        workspace=workspace,
        portfolio=portfolio,
        result=result,
        allocation=allocation,
        universe=universe,
        prices_as_of=str(market.as_of),
    )
