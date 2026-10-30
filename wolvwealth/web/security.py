"""Reject cross-site requests. Binding to 127.0.0.1 is not enough.

Any web page open in the same browser may *send* requests to
http://localhost:8000 — the browser only stops it from reading the
responses. For state-changing endpoints that is already an attack: a
malicious page could silently overwrite holdings or delete workspaces while
its visitor reads an article. Worse, a page on a domain whose DNS answer is
re-pointed at 127.0.0.1 after the first lookup ("DNS rebinding") is treated
as same-origin by the browser and can read responses too — holdings included.

Two checks close both holes, applied to every request:

1. The Host header must name this machine (localhost or a loopback IP).
   A rebound domain arrives as ``Host: attacker.example`` and is refused, so
   there is nothing for the attacker's page to read.
2. Requests a browser marks as coming from another site are refused.
   Modern browsers send ``Sec-Fetch-Site`` on every request; older ones send
   ``Origin`` on the cross-origin POSTs we care about. The one cross-site
   thing allowed through is a plain top-level navigation (clicking a link to
   the app), which is a GET and changes nothing.

Agents, curl and the app's own pages are untouched: tools send none of these
headers, and the app's pages are same-origin by definition. Refusals are the
API's usual JSON error shape with status 403.
"""
import urllib.parse

import flask

#: Names under which this server may be addressed. Anything else in the Host
#: header is a proxy or a rebound domain, not a person at this machine.
ALLOWED_HOSTNAMES = {"localhost", "127.0.0.1", "[::1]", "::1"}


def install(app: flask.Flask) -> None:
    """Attach the cross-site checks and cookie policy to the app."""
    # The session cookie never rides along on requests other sites initiate.
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.before_request(_reject_cross_site)


def _reject_cross_site():
    """Refuse the request unless it is addressed to, and made from, this machine."""
    reason = rejection_reason(flask.request)
    if reason is None:
        return None
    body = {"error": {"message": reason, "status_code": 403}}
    return flask.jsonify(body), 403


def rejection_reason(request) -> str:
    """Return why this request must be refused, or None if it is fine."""
    if _hostname(request.host) not in ALLOWED_HOSTNAMES:
        return f"Refused: Host '{request.host}' does not name this machine (DNS rebinding protection)."
    if request.headers.get("Sec-Fetch-Site") == "cross-site" and not _is_plain_navigation(request):
        return "Refused: cross-site requests to this local app are not allowed."
    origin = request.headers.get("Origin")
    if origin and request.method not in ("GET", "HEAD", "OPTIONS"):
        if _hostname_of_origin(origin) not in ALLOWED_HOSTNAMES:
            return f"Refused: cross-site request from origin '{origin}' is not allowed."
    return None


def _is_plain_navigation(request) -> bool:
    """Return true for a link click into the app: a GET that changes nothing."""
    return request.method in ("GET", "HEAD") and request.headers.get("Sec-Fetch-Mode") == "navigate"


def _hostname(host: str) -> str:
    """Return the hostname part of a Host header, port stripped, IPv6 brackets kept."""
    if host.startswith("["):  # "[::1]:8000"
        return host[: host.index("]") + 1] if "]" in host else host
    return host.rsplit(":", 1)[0] if ":" in host else host


def _hostname_of_origin(origin: str) -> str:
    """Return the hostname of an Origin header value; '' for opaque origins like 'null'."""
    return urllib.parse.urlsplit(origin).hostname or ""
