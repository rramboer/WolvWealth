"""Cross-site and DNS-rebinding protection: who gets refused, who must not be."""

import pytest

OPTIMIZE = {"initial_cash": 1000, "universe": ["AAA", "BBB"]}
EVIL = {"Origin": "https://evil.example"}
SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost:8000"}


def send(client, method, path, headers):
    """Issue the request with a body that fits the route: JSON to the API, a form to a view."""
    if method == "get":
        return client.get(path, headers=headers)
    if path.startswith("/api/"):
        return client.post(path, json=OPTIMIZE, headers=headers)
    return client.post(path, data={"name": "attacker"}, headers=headers)


@pytest.mark.parametrize(
    "method, path, headers, fragment",
    [
        ("post", "/api/optimize/", EVIL, "evil.example"),
        ("post", "/api/optimize/", {"Sec-Fetch-Site": "cross-site"}, "cross-site"),
        ("post", "/api/optimize/", {"Origin": "null"}, "null"),
        # The browser forms mutate workspaces; a hostile page must not drive them.
        ("post", "/workspaces/create/", {**EVIL, "Sec-Fetch-Site": "cross-site"}, "cross-site"),
        # fetch() in no-cors mode is a GET without navigation; nothing legitimate looks like this.
        ("get", "/api/market/", {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"}, "cross-site"),
        # DNS rebinding arrives with the attacker's Host header; it must see nothing.
        ("get", "/api/market/", {"Host": "attacker.example"}, "DNS rebinding"),
        ("get", "/api/market/", {"Host": "attacker.example:8000"}, "DNS rebinding"),
        ("get", "/api/market/", {"Host": "localhost.evil.example"}, "DNS rebinding"),
    ],
)
def test_cross_site_and_rebound_requests_are_refused_in_the_api_error_shape(client, method, path, headers, fragment):
    response = send(client, method, path, headers)
    assert response.status_code == 403
    body = response.get_json()
    assert set(body) == {"error"} and set(body["error"]) == {"message", "status_code"}
    assert body["error"]["status_code"] == 403 and fragment in body["error"]["message"]


@pytest.mark.parametrize(
    "method, path, headers, status",
    [
        ("post", "/api/optimize/", {}, 200),  # agents and curl send no browser headers
        ("get", "/api/", {"Host": "localhost"}, 200),
        ("get", "/api/", {"Host": "localhost:8000"}, 200),
        ("get", "/api/", {"Host": "127.0.0.1:8000"}, 200),
        ("get", "/api/", {"Host": "[::1]:8000"}, 200),
        ("post", "/api/optimize/", {**SAME_ORIGIN, "Sec-Fetch-Mode": "cors"}, 200),  # the app's own pages
        ("post", "/workspaces/create/", SAME_ORIGIN, 302),  # the app's own forms: the normal redirect
        # A cross-site top-level navigation is how people open the app; it is a harmless GET.
        ("get", "/", {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}, 200),
        ("get", "/workspaces/", {"Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "navigate"}, 200),  # the address bar
    ],
)
def test_legitimate_callers_pass(client, method, path, headers, status):
    assert send(client, method, path, headers).status_code == status


def test_session_cookie_is_samesite_lax(app):
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
