"""Rate limits fire where they should, key on the right identity, and answer properly."""

import pytest

from tests.conftest import DEV_ADMIN_TOKEN, DEV_PASSWORD, DEV_USER, DEV_USER_TOKEN, install_csrf_token, login

BACKTEST = {"start": "2023-07-01", "universe": ["top3"], "lookback_days": 120}


@pytest.fixture
def limits(app):
    """Set limit strings for one test; restore the defaults afterwards."""
    saved = {key: value for key, value in app.config.items() if key.startswith("RATELIMIT_")}
    saved["TRUSTED_PROXY_COUNT"] = app.config["TRUSTED_PROXY_COUNT"]

    def set_limits(**values):
        for key, value in values.items():
            app.config[key] = value

    yield set_limits
    app.config.update(saved)


def post_login(client, username=DEV_USER, password="wrong-password", **kwargs):
    token = install_csrf_token(client)
    return client.post(
        "/accounts/login/", data={"username": username, "password": password, "csrf_token": token}, **kwargs
    )


class TestEveryApiRouteIsLimited:
    """Guard: a new /api/ route without @api_per_ip would fail here."""

    def api_rules(self, app):
        for rule in app.url_map.iter_rules():
            if rule.rule.startswith("/api/"):
                method = "POST" if "POST" in rule.methods else "GET"
                yield rule.rule.replace("<job_id>", "nojob"), method

    def test_each_route_returns_429_past_the_shared_api_limit(self, app, client, limits):
        from wolvwealth.ratelimit import limiter

        limits(RATELIMIT_API_PER_IP="1 per minute")
        rules = list(self.api_rules(app))
        assert len(rules) >= 12
        for url, method in rules:
            limiter.reset()
            client.open(url, method=method, json={})
            response = client.open(url, method=method, json={})
            assert response.status_code == 429, f"{method} {url} is not rate limited"

    def test_api_bucket_is_shared_across_routes(self, client, limits):
        limits(RATELIMIT_API_PER_IP="2 per minute")
        assert client.get("/api/").status_code == 200
        assert client.post("/api/optimize/", json={}).status_code == 401
        assert client.post("/api/account/", json={}).status_code == 429


class TestAnonymousFloods:
    def test_unauthenticated_requests_are_counted_and_cut_off(self, client, limits):
        limits(RATELIMIT_API_PER_IP="3 per minute")
        codes = [client.post("/api/backtest/", json=BACKTEST).status_code for _ in range(5)]
        assert codes == [401, 401, 401, 429, 429]

    def test_rotating_fake_api_keys_does_not_escape_the_ip_limit(self, client, limits):
        limits(RATELIMIT_API_PER_IP="3 per minute")
        codes = [
            client.post("/api/optimize/", json={}, headers={"Authorization": f"guess-{i}"}).status_code
            for i in range(5)
        ]
        assert codes == [403, 403, 403, 429, 429]


class TestPerCallerLimits:
    def test_optimize_is_limited_per_api_key(self, client, limits):
        limits(RATELIMIT_OPTIMIZE_PER_CALLER="2 per minute")
        body = {"initial_cash": 1000, "universe": ["top3"]}
        user = {"Authorization": DEV_USER_TOKEN}
        admin = {"Authorization": DEV_ADMIN_TOKEN}
        assert client.post("/api/optimize/", json=body, headers=user).status_code == 200
        assert client.post("/api/optimize/", json=body, headers=user).status_code == 200
        assert client.post("/api/optimize/", json=body, headers=user).status_code == 429
        # A different caller from the same IP still has its own allowance.
        assert client.post("/api/optimize/", json=body, headers=admin).status_code == 200

    def test_rate_limited_requests_are_not_charged(self, client, limits):
        limits(RATELIMIT_OPTIMIZE_PER_CALLER="1 per minute")

        def uses():
            account = client.post(
                "/api/account/", json={"username": DEV_USER}, headers={"Authorization": DEV_USER_TOKEN}
            )
            return account.get_json()["optimizations_remaining"]

        body = {"initial_cash": 1000, "universe": ["top3"]}
        client.post("/api/optimize/", json=body, headers={"Authorization": DEV_USER_TOKEN})
        before = uses()
        assert client.post("/api/optimize/", json=body, headers={"Authorization": DEV_USER_TOKEN}).status_code == 429
        assert uses() == before

    def test_backtest_start_is_limited_per_session_user(self, client, limits):
        limits(RATELIMIT_BACKTEST_START_PER_CALLER="1 per minute")
        login(client)
        token = install_csrf_token(client)
        first = client.post("/api/backtest/", json=BACKTEST, headers={"X-CSRF-Token": token})
        assert first.status_code in (200, 202)
        assert client.post("/api/backtest/", json=BACKTEST, headers={"X-CSRF-Token": token}).status_code == 429

    def test_backtest_polling_has_its_own_allowance(self, client, limits):
        limits(RATELIMIT_BACKTEST_START_PER_CALLER="1 per minute", RATELIMIT_BACKTEST_STATUS_PER_CALLER="3 per minute")
        headers = {"Authorization": DEV_USER_TOKEN}
        status_url = client.post("/api/backtest/", json=BACKTEST, headers=headers).get_json()["status_url"]
        codes = [client.get(status_url, headers=headers).status_code for _ in range(4)]
        assert codes[:3] == [200, 200, 200] or all(code in (200, 202) for code in codes[:3])
        assert codes[3] == 429


def login_from(client, ip, username=DEV_USER, password="wrong-password"):
    """A login attempt arriving through a trusted proxy from `ip`."""
    return post_login(client, username=username, password=password, headers={"X-Forwarded-For": ip})


def logged_in_as(client):
    with client.session_transaction() as session:
        return session.get("username")


class TestLogin:
    def test_failures_from_one_ip_are_cut_off(self, client, limits):
        limits(RATELIMIT_LOGIN_FAILURES_PER_IP="3 per minute")
        codes = [post_login(client, username=f"user{i}").status_code for i in range(5)]
        assert codes == [302, 302, 302, 429, 429]

    def test_successful_logins_do_not_count_as_failures(self, app, limits):
        """An office behind one NAT IP must not lock itself out by logging in normally."""
        limits(
            RATELIMIT_LOGIN_FAILURES_PER_IP="2 per minute", RATELIMIT_LOGIN_FAILURES_PER_ACCOUNT_CLIENT="2 per minute"
        )
        codes = []
        for _ in range(6):
            fresh = app.test_client()
            codes.append(post_login(fresh, password=DEV_PASSWORD).status_code)
            assert logged_in_as(fresh) == DEV_USER
        assert codes == [302] * 6

    def test_all_attempts_per_ip_still_bound_password_hashing(self, app, limits):
        limits(RATELIMIT_LOGIN_PER_IP="3 per minute")
        codes = [post_login(app.test_client(), password=DEV_PASSWORD).status_code for _ in range(4)]
        assert codes == [302, 302, 302, 429]

    def test_guessing_one_account_from_one_ip_does_not_affect_other_ips(self, app, limits):
        limits(RATELIMIT_LOGIN_FAILURES_PER_ACCOUNT_CLIENT="2 per hour", TRUSTED_PROXY_COUNT=1)
        attacker = app.test_client()
        assert [login_from(attacker, "203.0.113.1").status_code for _ in range(3)] == [302, 302, 429]
        # Someone else trying the same account from elsewhere is unaffected.
        other = app.test_client()
        assert login_from(other, "198.51.100.9", password=DEV_PASSWORD).status_code == 302
        assert logged_in_as(other) == DEV_USER

    def test_distributed_guessing_of_one_account_is_still_capped(self, app, limits):
        """Many IPs, no device cookie: the per-account limit stops a botnet."""
        limits(RATELIMIT_LOGIN_FAILURES_PER_ACCOUNT="3 per hour", TRUSTED_PROXY_COUNT=1)
        attacker = app.test_client()
        codes = [login_from(attacker, f"203.0.113.{i}").status_code for i in range(5)]
        assert codes == [302, 302, 302, 429, 429]

    def test_attacker_cannot_lock_the_owner_out_of_a_known_device(self, app, limits):
        """Regression: knowing a username was enough to lock its owner out of their account."""
        limits(RATELIMIT_LOGIN_FAILURES_PER_ACCOUNT="3 per hour", TRUSTED_PROXY_COUNT=1)
        owner = app.test_client()
        assert login_from(owner, "198.51.100.7", password=DEV_PASSWORD).status_code == 302  # earlier visit
        with owner.session_transaction() as session:
            session.pop("username")  # logged out since

        attacker = app.test_client()
        assert [login_from(attacker, f"203.0.113.{i}").status_code for i in range(5)][-1] == 429

        response = login_from(owner, "198.51.100.7", password=DEV_PASSWORD)
        assert response.status_code == 302
        assert logged_in_as(owner) == DEV_USER

    def test_known_device_is_still_throttled_per_device(self, app, limits):
        """A stolen or shared device cookie is not a free pass for guessing."""
        limits(RATELIMIT_LOGIN_FAILURES_PER_ACCOUNT_CLIENT="2 per hour", TRUSTED_PROXY_COUNT=1)
        device = app.test_client()
        login_from(device, "198.51.100.7", password=DEV_PASSWORD)
        with device.session_transaction() as session:
            session.pop("username")
        codes = [login_from(device, f"203.0.113.{i}").status_code for i in range(3)]
        assert codes == [302, 302, 429]  # same device, different IPs: one bucket

    def test_device_cookie_for_another_account_does_not_exempt(self, app, limits):
        from tests.conftest import DEV_ADMIN

        limits(RATELIMIT_LOGIN_FAILURES_PER_ACCOUNT="2 per hour", TRUSTED_PROXY_COUNT=1)
        browser = app.test_client()
        login_from(browser, "198.51.100.7", username=DEV_ADMIN, password=DEV_PASSWORD)
        with browser.session_transaction() as session:
            session.pop("username")
        codes = [login_from(browser, f"203.0.113.{i}", username=DEV_USER).status_code for i in range(3)]
        assert codes == [302, 302, 429]

    def test_forged_device_cookie_is_ignored(self, app, limits):
        from wolvwealth.device_cookies import COOKIE_NAME, COOKIE_PATH

        limits(RATELIMIT_LOGIN_FAILURES_PER_ACCOUNT="2 per hour", TRUSTED_PROXY_COUNT=1)
        attacker = app.test_client()
        attacker.set_cookie(COOKIE_NAME, '{"id": "x", "accounts": ["devuser"]}', path=COOKIE_PATH)
        codes = [login_from(attacker, f"203.0.113.{i}").status_code for i in range(3)]
        assert codes == [302, 302, 429]

    def test_device_cookie_attributes(self, client):
        from wolvwealth.device_cookies import COOKIE_NAME

        response = post_login(client, password=DEV_PASSWORD)
        cookie = next(header for header in response.headers.getlist("Set-Cookie") if header.startswith(COOKIE_NAME))
        assert "HttpOnly" in cookie
        assert "SameSite=Lax" in cookie
        assert "Path=/accounts/" in cookie

    def test_failed_login_sets_no_device_cookie(self, client):
        from wolvwealth.device_cookies import COOKIE_NAME

        response = post_login(client)
        assert not any(h.startswith(COOKIE_NAME) for h in response.headers.getlist("Set-Cookie"))

    def test_limit_page_is_html_with_retry_after(self, client, limits):
        limits(RATELIMIT_LOGIN_FAILURES_PER_IP="1 per minute")
        post_login(client)
        response = post_login(client)
        assert response.status_code == 429
        assert response.mimetype == "text/html"
        assert b"Too many requests" in response.data
        assert int(response.headers["Retry-After"]) >= 1


class TestRegistration:
    def test_account_creation_is_limited_per_ip(self, client, limits):
        limits(RATELIMIT_REGISTER_PER_IP="2 per hour")
        codes = []
        for i in range(3):
            token = install_csrf_token(client)
            response = client.post(
                "/accounts/register/",
                data={
                    "username": f"bot{i}",
                    "email": f"bot{i}@example.com",
                    "password": "pw-123456",
                    "csrf_token": token,
                },
            )
            codes.append(response.status_code)
        assert codes == [302, 302, 429]


class TestResponseShape:
    def test_api_429_is_json_with_retry_after_and_headers(self, client, limits):
        limits(RATELIMIT_API_PER_IP="1 per minute")
        client.get("/api/")
        response = client.get("/api/")
        assert response.status_code == 429
        error = response.get_json()["error"]
        assert error["status_code"] == 429
        assert error["message"].startswith("Too many requests. Try again in ")
        assert int(response.headers["Retry-After"]) >= 1
        assert response.headers["X-RateLimit-Remaining"] == "0"
        # The message and the header must agree (they used to differ by a second).
        assert f"in {response.headers['Retry-After']} second" in error["message"]

    def test_successful_responses_carry_rate_limit_headers(self, client):
        response = client.get("/api/")
        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers


class TestSiteWideLimit:
    def test_pages_are_limited_per_ip(self, client, limits):
        limits(RATELIMIT_SITE_PER_IP="3 per minute")
        codes = [client.get("/").status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]

    def test_static_files_are_exempt(self, client, limits):
        limits(RATELIMIT_SITE_PER_IP="1 per minute")
        codes = [client.get("/static/css/util.css").status_code for _ in range(5)]
        assert 429 not in codes


class TestTrustedProxies:
    def test_forwarded_for_is_ignored_by_default(self, client, limits):
        """With no trusted proxy, a client cannot spoof X-Forwarded-For to get a fresh bucket."""
        limits(RATELIMIT_API_PER_IP="2 per minute", TRUSTED_PROXY_COUNT=0)
        codes = [client.get("/api/", headers={"X-Forwarded-For": f"198.51.100.{i}"}).status_code for i in range(3)]
        assert codes == [200, 200, 429]

    def test_forwarded_for_is_used_behind_a_trusted_proxy(self, client, limits):
        """Behind nginx, each real client gets its own bucket instead of sharing 127.0.0.1's."""
        limits(RATELIMIT_API_PER_IP="1 per minute", TRUSTED_PROXY_COUNT=1)
        first = [client.get("/api/", headers={"X-Forwarded-For": f"198.51.100.{i}"}).status_code for i in range(3)]
        assert first == [200, 200, 200]
        assert client.get("/api/", headers={"X-Forwarded-For": "198.51.100.0"}).status_code == 429

    def test_only_the_trusted_hop_is_believed(self, client, limits):
        """A client-supplied left-most entry cannot override what the trusted proxy appended."""
        limits(RATELIMIT_API_PER_IP="1 per minute", TRUSTED_PROXY_COUNT=1)
        assert client.get("/api/", headers={"X-Forwarded-For": "1.1.1.1, 198.51.100.7"}).status_code == 200
        assert client.get("/api/", headers={"X-Forwarded-For": "2.2.2.2, 198.51.100.7"}).status_code == 429


class TestDisabled:
    def test_limits_can_be_turned_off(self, client, limits):
        from wolvwealth.ratelimit import limiter

        limits(RATELIMIT_API_PER_IP="1 per minute")
        limiter.enabled = False
        try:
            assert all(client.get("/api/").status_code == 200 for _ in range(3))
        finally:
            limiter.enabled = True
