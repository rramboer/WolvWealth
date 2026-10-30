"""Cross-site and DNS-rebinding protection: who gets refused, who must not be."""
import pytest

OPTIMIZE = {"initial_cash": 1000, "universe": ["AAA", "BBB"]}


def assert_refused(response, fragment):
    assert response.status_code == 403
    error = response.get_json()["error"]
    assert error["status_code"] == 403 and fragment in error["message"]


# --- attacks that must be refused -------------------------------------------


def test_cross_site_api_post_is_refused_by_origin(client):
    response = client.post("/api/optimize/", json=OPTIMIZE, headers={"Origin": "https://evil.example"})
    assert_refused(response, "evil.example")


def test_cross_site_api_post_is_refused_by_fetch_metadata(client):
    response = client.post("/api/optimize/", json=OPTIMIZE, headers={"Sec-Fetch-Site": "cross-site"})
    assert_refused(response, "cross-site")


def test_cross_site_form_post_to_a_view_is_refused(client):
    """The browser forms mutate workspaces; a hostile page must not drive them."""
    response = client.post(
        "/workspaces/create/",
        data={"name": "attacker"},
        headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403


def test_cross_site_fetch_get_is_refused(client):
    """fetch() in no-cors mode is a GET without navigation; nothing legitimate looks like this."""
    response = client.get(
        "/api/market/", headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"}
    )
    assert response.status_code == 403


def test_opaque_null_origin_is_refused(client):
    response = client.post("/api/optimize/", json=OPTIMIZE, headers={"Origin": "null"})
    assert response.status_code == 403


@pytest.mark.parametrize("host", ["attacker.example", "attacker.example:8000", "localhost.evil.example"])
def test_rebound_dns_names_are_refused(client, host):
    """DNS rebinding arrives with the attacker's Host header; it must see nothing."""
    response = client.get("/api/market/", headers={"Host": host})
    assert_refused(response, "DNS rebinding")


# --- legitimate callers that must keep working ------------------------------


def test_agents_and_curl_send_no_browser_headers_and_pass(client):
    assert client.post("/api/optimize/", json=OPTIMIZE).status_code == 200


@pytest.mark.parametrize("host", ["localhost", "localhost:8000", "127.0.0.1:8000", "[::1]:8000"])
def test_loopback_hosts_pass(client, host):
    assert client.get("/api/", headers={"Host": host}).status_code == 200


def test_the_apps_own_pages_pass(client):
    headers = {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Origin": "http://localhost:8000"}
    assert client.post("/api/optimize/", json=OPTIMIZE, headers=headers).status_code == 200


def test_browser_form_post_from_the_app_itself_passes(client):
    headers = {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost:8000"}
    response = client.post("/workspaces/create/", data={"name": "mine"}, headers=headers)
    assert response.status_code == 302  # the normal redirect into the workspace


def test_clicking_a_link_into_the_app_from_elsewhere_passes(client):
    """A cross-site top-level navigation is how people open the app; it is a harmless GET."""
    headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
    assert client.get("/", headers=headers).status_code == 200


def test_direct_address_bar_navigation_passes(client):
    headers = {"Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "navigate"}
    assert client.get("/workspaces/", headers=headers).status_code == 200


def test_session_cookie_is_samesite_lax(app):
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_refusals_use_the_api_error_shape(client):
    body = client.post("/api/optimize/", json=OPTIMIZE, headers={"Origin": "https://evil.example"}).get_json()
    assert set(body) == {"error"} and set(body["error"]) == {"message", "status_code"}
