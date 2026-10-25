"""Landing page."""
import flask

from wolvwealth.web import context
from wolvwealth.web.views import blueprint


@blueprint.route("/", methods=["GET"])
def show_landing():
    """Display the landing page."""
    return flask.render_template("index.html", workspace=context.current_workspace())
