"""WolvWealth REST API Backend."""
import flask

app = flask.Flask(__name__)  # pylint: disable=invalid-name

app.config.from_object('wolvwealth.config')

app.config.from_envvar('INSTA485_SETTINGS', silent=True)

import wolvwealth.api  # noqa: E402  pylint: disable=wrong-import-position
import wolvwealth.views  # noqa: E402  pylint: disable=wrong-import-position
import wolvwealth.model  # noqa: E402  pylint: disable=wrong-import-position
