"""WolvWealth Main API."""

import flask
# import pathlib
# import pypfopt
import wolvwealth


@wolvwealth.route("/api/")
def api_default():
    return flask.jsonify({"/api/": "API Info"})

