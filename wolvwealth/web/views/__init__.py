"""Server-rendered pages. One blueprint; each module registers its routes on it."""
import flask

blueprint = flask.Blueprint("views", __name__)

from wolvwealth.web.views import index, optimizer, portfolios, workspaces  # noqa: E402,F401  pylint: disable=wrong-import-position
