"""JSON-RPC 2.0 over newline-delimited stdio: the transport MCP's stdio servers speak.

Knows nothing about WolvWealth or about MCP's methods. ``serve`` reads one
JSON message per line, dispatches it to a handler by method name, and
writes one JSON response per line. Requests carry an ``id`` and get a
response; notifications carry none and get nothing, whatever happens.
"""

import json

#: JSON-RPC's reserved error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class RpcError(Exception):
    """A handler's way of answering a request with a JSON-RPC error instead of a result."""

    def __init__(self, code: int, message: str, data=None) -> None:
        """Carry the code, the message, and optional structured data."""
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


def error_response(request_id, code: int, message: str, data=None) -> dict:
    """Build an error response."""
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def result_response(request_id, result) -> dict:
    """Build a success response."""
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def dispatch(message, methods: dict, log=None):
    """Handle one parsed message; return the response dict, or None when none is owed.

    ``methods`` maps a method name to ``handler(params) -> result``. A
    handler may raise ``RpcError`` to answer with a specific error; any
    other exception becomes an internal error (and is logged), so one bad
    request never takes the server down.
    """
    if not isinstance(message, dict) or "method" not in message:
        # A malformed request, or a response to a request we never sent.
        if isinstance(message, dict) and "id" in message and "method" not in message and "result" not in message:
            return error_response(message.get("id"), INVALID_REQUEST, "Invalid request: no method.")
        return None if isinstance(message, dict) else error_response(None, INVALID_REQUEST, "Invalid request.")
    request_id = message.get("id")
    is_request = "id" in message
    if not isinstance(message["method"], str):
        return error_response(request_id, INVALID_REQUEST, "Invalid request: method must be a string.")
    handler = methods.get(message["method"])
    if handler is None:
        return (
            error_response(request_id, METHOD_NOT_FOUND, f"Method not found: {message['method']}")
            if is_request
            else None
        )
    params = message.get("params")
    try:
        result = handler(params if params is not None else {})
    except RpcError as error:
        return error_response(request_id, error.code, error.message, error.data) if is_request else None
    except Exception as error:  # pylint: disable=broad-except  # the loop must survive any handler failure
        if log is not None:
            log(f"wolvwealth mcp: {message['method']} failed: {type(error).__name__}: {error}")
        message_text = f"Internal error handling {message['method']}: {type(error).__name__}: {error}"
        return error_response(request_id, INTERNAL_ERROR, message_text) if is_request else None
    return result_response(request_id, result) if is_request else None


def write(stdout, payload) -> None:
    """Write one message as a single line. ``stdout`` is a binary stream."""
    # A message must not contain a raw newline: json.dumps escapes the ones
    # inside strings, and compact separators keep the framing to one line.
    stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
    stdout.flush()


def serve(methods: dict, stdin, stdout, log=None) -> None:
    """Read messages from ``stdin`` until it closes, answering each on ``stdout``. Both are binary streams."""
    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, RecursionError) as error:
            write(stdout, error_response(None, PARSE_ERROR, f"Parse error: {error}"))
            continue
        if isinstance(message, list):  # a JSON-RPC batch (protocol versions before 2025-06-18)
            if not message:
                write(stdout, error_response(None, INVALID_REQUEST, "Invalid request: empty batch."))
                continue
            responses = [r for r in (dispatch(item, methods, log) for item in message) if r is not None]
            if responses:
                write(stdout, responses)
            continue
        response = dispatch(message, methods, log)
        if response is not None:
            write(stdout, response)
