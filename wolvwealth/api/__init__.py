"""WolvWealth API Init."""

import flask
import wolvwealth
import wolvwealth.portfolios
import wolvwealth.api.api_exceptions
import wolvwealth.api.optimize
from wolvwealth.api.state import ApplicationState


@wolvwealth.app.route("/api/", methods=["GET", "POST"])
def api_default():
    """WolvWealth API Usage Endpoint."""
    return flask.jsonify(
        {
            "/api/": "API Usage Info",
            "/api/optimize/": "Optimize Portfolio (POST JSON; accepts workspace/portfolio or explicit holdings)",
            "/api/workspaces/": "List workspaces and their portfolios",
        }
    )


@wolvwealth.app.route("/api/workspaces/", methods=["GET"])
def api_workspaces():
    """List all workspaces with their portfolios and holdings."""
    output = {"workspaces": []}
    for workspace in wolvwealth.portfolios.fetch_workspaces():
        portfolios = wolvwealth.portfolios.fetch_portfolios(workspace["name"])
        output["workspaces"].append(
            {
                "name": workspace["name"],
                "created": workspace["created"],
                "portfolios": [
                    {"name": p["name"], "cash": p["cash"], "holdings": p["holdings"]} for p in portfolios
                ],
            }
        )
    return flask.jsonify(output)
