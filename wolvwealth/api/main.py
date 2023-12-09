"""WolvWealth Main API."""
import flask
import wolvwealth


@wolvwealth.app.route("/api/")
def api_default():
    """WolvWealth API Usage Endpoint."""
    return flask.jsonify(
        {
            "/api/": "API Info",
            "/api/optimize/": "Optimize Portfolio",
        }
    )
