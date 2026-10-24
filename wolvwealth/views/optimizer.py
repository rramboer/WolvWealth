"""Routes and code for optimizer pages.

GET  /optimizer/      - optimizer page (React shell)
POST /optimizer/run/  - optimize one saved portfolio, render the results
"""

import flask
import wolvwealth
import wolvwealth.portfolios as portfolios
from wolvwealth.api.api_exceptions import InvalidUsage
from wolvwealth.api.optimize import Optimization
from wolvwealth.api.state import ApplicationState
from wolvwealth.views.workspaces import current_workspace

UNIVERSE_CHOICES = ["top20", "top50", "top100", "top200", "top300", "top400", "top500"]


@wolvwealth.app.route("/optimizer/", methods=["GET"])
def show_optimizer():
    """Display /optimizer route."""
    workspace = current_workspace()
    if workspace is None:
        return flask.redirect(flask.url_for("show_workspaces"))
    context = {"workspace": workspace}
    return flask.render_template("optimizer.html", **context)


@wolvwealth.app.route("/optimizer/run/", methods=["POST"])
def run_optimizer():
    """Optimize one saved portfolio and render the result."""
    workspace = current_workspace()
    if workspace is None:
        return flask.redirect(flask.url_for("show_workspaces"))
    try:
        portfolio_id = int(flask.request.form.get("portfolio_id", ""))
    except ValueError:
        return flask.redirect(flask.url_for("show_workspace"))
    portfolio = portfolios.fetch_portfolio(workspace, portfolio_id=portfolio_id)
    if portfolio is None:
        flask.flash("That portfolio no longer exists.")
        return flask.redirect(flask.url_for("show_workspace"))

    universe = flask.request.form.get("universe", "top100")
    if universe not in UNIVERSE_CHOICES:
        universe = "top100"
    # Held tickers join the universe so the optimizer may keep them.
    input_json = {
        "initial_cash": portfolio["cash"],
        "initial_holdings": portfolio["holdings"],
        "universe": [universe] + list(portfolio["holdings"]),
        "constraints": {},
    }
    max_positions = flask.request.form.get("max_positions", "").strip()
    if max_positions:
        try:
            input_json["constraints"]["max_positions"] = int(max_positions)
        except ValueError:
            flask.flash("Max positions must be a whole number.")
            return flask.redirect(flask.url_for("show_workspace"))
    max_weight = flask.request.form.get("max_weight", "").strip()
    if max_weight:
        try:
            input_json["constraints"]["max_weight"] = float(max_weight)
        except ValueError:
            flask.flash("Max weight must be a number between 0 and 1.")
            return flask.redirect(flask.url_for("show_workspace"))

    try:
        result = Optimization(input_json).result
    except InvalidUsage as error:
        flask.flash(error.message)
        return flask.redirect(flask.url_for("show_workspace"))

    allocation = sorted(result["optimized_portfolio"].items(), key=lambda kv: kv[1]["value"], reverse=True)
    context = {
        "workspace": workspace,
        "portfolio": portfolio,
        "result": result,
        "allocation": allocation,
        "universe": universe,
        "prices_as_of": str(ApplicationState().HISTORICAL_PRICES.index[-1].date()),
    }
    return flask.render_template("results.html", **context)
