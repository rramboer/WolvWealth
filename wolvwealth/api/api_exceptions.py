"""API Exceptions File."""

import flask
import wolvwealth


class InvalidUsage(Exception):
    """Exception subclass for better error handling."""

    status_code = 400

    def __init__(self, message, status_code=None, payload=None):
        """Invalidusage init and payload creation."""
        Exception.__init__(self)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        self.payload = payload

    def to_dict(self):
        """Create dictionary for exception json response."""
        rvv = dict(self.payload or ())
        rvv['message'] = self.message
        rvv['status_code'] = self.status_code
        return rvv


@wolvwealth.app.errorhandler(InvalidUsage)
def handle_invalid_usage(error):
    """Handle invalid usage."""
    response = flask.jsonify(error.to_dict())
    response.status_code = error.status_code
    return response
