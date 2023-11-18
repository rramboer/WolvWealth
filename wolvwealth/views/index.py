"""WolvWealth Views."""

import flask
import wolvwealth

@wolvwealth.app.route("/")
def show_index():
    """Display '/' route."""
    context = {}
    return flask.render_template("index.html", **context)