# User intent: the public viewer's session and logout hardening (team-server-028 section 3): origin-bound sessions, strict Sec-Fetch-Site, a logout that always works and clears only this origin's cache, and a log-only failed-login alarm.
import json
import logging

import pytest

from taskmaster.serve.limits import KeyedLimiter
from taskmaster.serve.viewer_auth import CookieLogin
from test_native_service_build import root  # noqa: F401  (fixture)
from test_serve_viewer import (CADDY, PUBLIC, Clock, login, make_viewer, prefs_put, req,  # noqa: F401
                               session_headers, theme, v, work_tree)


def test_a_session_works_only_on_the_origin_it_logged_in_on(v):
    loopback_cookie, _ = login(v)
    public_cookie, _ = login(v, public=True)
    for path, status in (("/api/board", 401), ("/", 303)):
        resp = req(v, "GET", path, headers={**CADDY, "Cookie": loopback_cookie})
        assert resp.status == status, path
    assert req(v, "GET", "/api/board", headers={"Cookie": public_cookie}).status == 401
    assert req(v, "GET", "/api/board", headers={**CADDY, "Cookie": public_cookie}).status == 200
    assert req(v, "GET", "/api/board", headers={"Cookie": loopback_cookie}).status == 200


@pytest.mark.parametrize("site,expected", [("same-site", 403), ("none", 403), ("cross-site", 403),
                                           ("same-origin", 200), (None, 200)])
def test_sec_fetch_site_must_be_absent_or_same_origin_on_writes(v, site, expected):
    cookie, csrf = login(v)
    headers = session_headers(v, cookie, csrf)
    if site:
        headers["Sec-Fetch-Site"] = site
    assert prefs_put(v, headers).status == expected


def post_retrying(v, path, **kwargs):
    """A refused POST closes with its body unread, which Windows can turn into a reset on the client: retry that."""
    for attempt in range(3):
        try:
            return req(v, "POST", path, **kwargs)
        except ConnectionError:
            if attempt == 2:
                raise


def test_login_refuses_same_site_and_none(v):
    for site in ("same-site", "none", "cross-site"):
        resp = post_retrying(v, "/login", headers={"Origin": v.loopback, "Sec-Fetch-Site": site}, body="token=ka")
        assert resp.status == 403 and "Set-Cookie" not in resp.headers, site
    ok = req(v, "POST", "/login", headers={"Origin": v.loopback, "Sec-Fetch-Site": "same-origin"}, body="token=ka")
    assert ok.status == 303


def logout(v, headers):
    return req(v, "POST", "/logout", headers=headers)


def assert_signed_out(resp):
    assert resp.status == 303 and resp.headers["Location"] == "/login"
    cookies = resp.headers.get_all("Set-Cookie")
    assert len(cookies) == 2 and all("Max-Age=0" in c for c in cookies)
    assert resp.headers.get_all("Clear-Site-Data") == ['"cache"']          # never "cookies" or "*" (I1)


def test_logout_without_a_session_is_303_uncharged_and_clears_only_the_cache(v):
    headers = {**CADDY, "Origin": PUBLIC}
    for _ in range(25):
        assert_signed_out(logout(v, headers))
    assert v.guard.failures.blocked("203.0.113.7") == 0
    assert logout(v, {**CADDY, "Origin": "https://evil.example"}).status == 403
    assert logout(v, {**headers, "Sec-Fetch-Site": "same-site"}).status == 403
    assert logout(v, {"Host": v.loopback_host}).status == 403                  # no Origin at all


def test_logout_with_a_session_needs_no_csrf_token_and_ends_it(v):
    cookie, csrf = login(v)
    resp = logout(v, {"Cookie": cookie, "Origin": v.loopback})
    assert_signed_out(resp)
    assert req(v, "GET", "/api/backlog", headers={"Cookie": cookie}).status == 401         # replay


def test_a_stale_tab_still_signs_out_the_current_session(v):
    cookie_a, csrf_a = login(v)
    cookie_b, csrf_b = login(v)                           # the browser's jar now names session B
    for token in (csrf_a, None):
        cookie_b, _ = login(v)
        headers = {"Cookie": cookie_b, "Origin": v.loopback}
        if token:
            headers["X-Taskmaster-CSRF"] = token
        assert_signed_out(logout(v, headers))
        assert req(v, "GET", "/api/board", headers={"Cookie": cookie_b}).status == 401
    assert csrf_b


def test_get_logout_goes_home_with_a_session_and_to_login_without(v):
    cookie, _ = login(v)
    resp = req(v, "GET", "/logout", headers={"Cookie": cookie})
    assert resp.status == 303 and resp.headers["Location"] == "/"
    assert req(v, "GET", "/api/board", headers={"Cookie": cookie}).status == 200
    resp = req(v, "GET", "/logout")
    assert resp.status == 303 and resp.headers["Location"] == "/login"


def test_the_operator_token_is_refused_publicly_and_its_loopback_cookie_is_no_session(v):
    cookie, _ = login(v, "kop")
    for path, status in (("/api/board", 401), ("/", 303)):
        assert req(v, "GET", path, headers={**CADDY, "Cookie": cookie}).status == status
    resp = req(v, "POST", "/login", headers={**CADDY, "Origin": PUBLIC}, body="token=kop")
    assert resp.status == 403 and "operator bootstrap token" in json.loads(resp.body)["error"]


def test_a_bearer_header_is_never_a_viewer_credential(v):
    assert req(v, "GET", "/api/board", headers={"Authorization": "Bearer ka"}).status == 401
    resp = req(v, "GET", "/", headers={"Authorization": "Bearer ka"})
    assert resp.status == 303 and resp.headers["Location"] == "/login"


def test_the_page_names_the_sign_in_and_sign_out_locations(v):
    cookie, _ = login(v)
    page = req(v, "GET", "/", headers={"Cookie": cookie}).text
    assert '<meta name="taskmaster-sign-in" content="/login">' in page
    assert '<meta name="taskmaster-sign-out" content="/logout">' in page
    assert '<meta name="taskmaster-csrf"' in page


def test_a_global_failed_login_alarm_warns_once_per_minute_and_never_refuses(make_viewer, caplog):
    clock = Clock()
    v = make_viewer(clock=clock)
    text = "viewer: more than 100 failed logins in the last minute"

    def fail(i):
        headers = {**CADDY, "X-Forwarded-For": f"198.51.{i // 250}.{i % 250 + 1}", "Origin": PUBLIC}
        assert req(v, "POST", "/login", headers=headers, body="token=nope").status == 401

    def alarms():
        return sum(text in r.getMessage() for r in caplog.records)

    with caplog.at_level(logging.WARNING, logger="taskmaster.serve"):
        for i in range(100):
            fail(i)
        assert alarms() == 0
        fail(100)
        assert alarms() == 1
        for i in range(101, 300):
            fail(i)
        assert alarms() == 1                                # the bucket stays empty: no second line inside 60 s
        clock.now += 61
        for i in range(300, 401):
            fail(i)
        assert alarms() == 2
    login(v, "ka", public=True)                             # the alarm never locks anyone out



def test_an_origin_must_match_the_host_on_writes_login_and_logout(v):
    """028 gate M1: an allowed origin is not enough; it must be the one the request's Host names."""
    cookie, csrf = login(v, public=True)
    loopback_on_public = {**CADDY, "Origin": v.loopback, "Content-Type": "application/x-www-form-urlencoded"}
    write = prefs_put(v, {**session_headers(v, cookie, csrf, public=True), "Origin": v.loopback})
    assert write.status == 403
    assert prefs_put(v, session_headers(v, cookie, csrf, public=True)).status == 200
    signin = post_retrying(v, "/login", headers=loopback_on_public, body="token=ka")
    assert signin.status == 403 and "Set-Cookie" not in signin.headers
    assert logout(v, {**CADDY, "Origin": v.loopback}).status == 403
    public_on_loopback = post_retrying(v, "/login", headers={"Origin": PUBLIC,
                                                             "Content-Type": "application/x-www-form-urlencoded"},
                                       body="token=ka")
    assert public_on_loopback.status == 403
    assert logout(v, {"Origin": PUBLIC}).status == 403
    assert_signed_out(logout(v, {**CADDY, "Origin": PUBLIC}))


def test_the_csp_kind_and_checked_file_are_reset_per_request(v):
    """028 gate M5: per-instance state set while answering one request never reaches the next."""
    class Fake:
        command, path, requestline = "GET", "/login", "GET /login HTTP/1.1"
        client_address = ("127.0.0.1", 1)
        viewer_kind, _checked_file = "file-md", object()

        class headers:
            @staticmethod
            def get(name, default=None):
                return {"Host": v.loopback_host}.get(name, default)

            @staticmethod
            def get_all(name):
                return [v.loopback_host] if name == "Host" else None

    fake = Fake()
    v.guard.auth.answer_public = lambda *args: None
    assert v.guard.admit(fake) is False
    assert fake.viewer_kind == "app" and fake._checked_file is None



def test_an_ended_sessions_polls_are_not_charged_but_guesses_still_are(make_viewer):
    """028 gate M2: after a restart or idle expiry the open tabs poll with a dead but well-formed cookie; those 401s
    must not spend the peer's failure budget (one office NAT would lose /login). A malformed cookie and a bad key
    still are charged."""
    v = make_viewer()
    cookie, _ = login(v, public=True)
    name, sid = cookie.split("=", 1)
    v.guard.auth.sessions.drop(sid)                          # what a restart does to every session
    for _ in range(30):
        assert req(v, "GET", "/api/board", headers={**CADDY, "Cookie": cookie}).status == 401
    assert v.guard.failures.blocked("203.0.113.7") == 0
    assert req(v, "GET", "/login", headers=CADDY).status == 200
    statuses = [req(v, "GET", "/api/board", headers={**CADDY, "Cookie": f"{name}=not-a-session"}).status
                for _ in range(25)]
    assert statuses[-1] == 429, statuses
    w = make_viewer()
    bad_login = {**CADDY, "Origin": PUBLIC, "Content-Type": "application/x-www-form-urlencoded",
                 "Cookie": f"{name}={sid}"}
    statuses = [post_retrying(w, "/login", headers=bad_login, body="token=wrong").status for _ in range(25)]
    assert statuses[-1] == 429, statuses
