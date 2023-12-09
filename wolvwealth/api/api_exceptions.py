"""API Exceptions File."""

import flask
import wolvwealth


class InvalidUsage(Exception):
    """Exception subclass for better error handling."""

    status_code: int = 400

    def __init__(
        self, message: str, status_code: int = None, payload: dict = None
    ) -> None:
        """Invalid usage init and payload creation."""
        Exception.__init__(self)
        self.message = str(message)
        if status_code is not None:
            self.status_code = status_code
        if payload is not None:
            self.payload = payload
        else:
            self.payload = {}

    def to_dict(self) -> dict:
        """Create dictionary for exception json response."""
        rvv = {}
        rvv["error"] = {"message": self.message, "status_code": self.status_code}
        if self.payload != {}:
            rvv["error"]["payload"] = self.payload
        return rvv


@wolvwealth.app.errorhandler(InvalidUsage)
def handle_invalid_usage(error):
    """Handle invalid usage."""
    response = flask.jsonify(error.to_dict())
    response.status_code = error.status_code
    return response
