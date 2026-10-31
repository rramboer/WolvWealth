"""Optimize one saved portfolio from the browser and render the result."""

import flask

from wolvwealth.domain.errors import DomainError
from wolvwealth.domain.market import UNIVERSE_ALIASES
from wolvwealth.services import optimize
from wolvwealth.web import context
from wolvwealth.web.views import blueprint

#: Optional numeric form fields: name -> (parser, message flashed when it does not parse).
OPTIONAL_NUMBERS = {
    "max_positions": (int, "Max positions must be a whole number."),
    "max_weight": (float, "Max weight must be a number between 0 and 1."),
    "max_turnover": (float, "Max turnover must be a number between 0 and 1."),
}


def _optional_numbers(form) -> dict:
    """Parse the optional numeric fields; blank means absent. Raises ValueError with the message to flash."""
    values = {}
    for name, (parse, message) in OPTIONAL_NUMBERS.items():
        raw = form.get(name, "").strip()
        if not raw:
            values[name] = None
            continue
        try:
            values[name] = parse(raw)
        except ValueError as err:
            raise ValueError(message) from err
    return values


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
    try:
        options = _optional_numbers(flask.request.form)
    except ValueError as error:
        flask.flash(str(error))
        return back

    market = context.market()
    try:
        payload = optimize.payload_for_portfolio(portfolio, universe, **options)
        # The stored portfolio carries the risk-free rate its owner set, if any.
        result = optimize.optimize(payload, market, portfolio=portfolio)
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
