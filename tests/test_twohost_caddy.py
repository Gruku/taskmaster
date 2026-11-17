# User intent: judge S13 (the public viewer) the way the internet meets it at go-live: a real daemon behind the shipped Caddyfile (real Caddy, `tls internal`) reached from this machine's non-loopback address; logins are budgeted per real peer, cookies and headers survive the proxy, the operator token and the removed routes get nothing, signed-in reads and Origin+CSRF writes work, and the static files come from Caddy's disk (team-server-015 Step 15, after team-server-028).
from __future__ import annotations

import json
import re

import pytest

from twohost.caddy import CADDY, Caddy
from twohost.fixtures import team

pytestmark = [pytest.mark.twohost, pytest.mark.slow, pytest.mark.real_service_process,
              pytest.mark.xdist_group("heavy_processes"), pytest.mark.timeout(300)]

VIEWER_SNI = "viewer.tm.localhost"
GET_PATHS = ("/api/board", "/api/backlog", "/api/identity", "/api/threads", "/api/bugs", "/api/notes",
             "/api/viewer/prefs", "/api/dashboard/recent-events?since=2026-01-01T00:00:00Z")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    if not CADDY:
        pytest.skip("TASKMASTER_CADDY does not name a Caddy binary (the Linux lane's twohost tier sets it)")
    base = tmp_path_factory.mktemp("s13caddy") / "run"
    base.mkdir()
    caddy = Caddy(base / "caddy")
    if caddy.address is None:
        pytest.skip("this machine has no non-loopback address, so the forwarded peer would be loopback")
    patch = pytest.MonkeyPatch()
    try:
        with team(base, patch, flags=caddy.daemon_flags, name="s13caddy") as the_team:
            caddy.start(the_team.host)
            try:
                the_team.caddy = caddy
                yield the_team
            finally:
                caddy.stop()
    finally:
        patch.undo()


def login(world, client, key, **headers):
    return client.post("/login", content=f"token={key}", headers={
        "Origin": world.caddy.viewer_origin, "Content-Type": "application/x-www-form-urlencoded", **headers})


def session(world, client, key):
    done = login(world, client, key)
    assert done.status_code == 303, (done.status_code, done.text)
    cookie = done.headers["set-cookie"].split(";", 1)[0]
    page = client.get("/", headers={"Cookie": cookie})
    assert page.status_code == 200, page.status_code
    client.cookies.clear()                      # the jar would sign later requests in; the tests pass the cookie by hand
    return cookie, re.search(r'<meta name="taskmaster-csrf" content="([^"]+)">', page.text).group(1)


def test_a_member_signs_in_through_caddy_with_a_hardened_cookie_and_the_operator_token_gets_nothing(world):
    with world.caddy.viewer() as client:
        done = login(world, client, world.ann.key)
        assert done.status_code == 303, (done.status_code, done.text)
        line = done.headers["set-cookie"]
        parts = [p.strip() for p in line.split(";")]
        assert line.startswith("__Host-tm_session="), line
        assert {"HttpOnly", "Secure", "SameSite=Strict", "Path=/"} <= set(parts), line
        assert not any(p.lower().startswith("domain") for p in parts), line
        client.cookies.clear()
        refused = login(world, client, world.operator.key)
        assert refused.status_code == 403 and "works only on the host" in refused.text, (refused.status_code, refused.text)
        world.evidence.record("viewer-caddy", "/login-operator", refused.text)


def test_through_caddy_every_read_needs_the_session_and_answers_with_it(world):
    with world.caddy.viewer() as client:
        for path in GET_PATHS:
            assert client.get(path).status_code in (401, 403), path
        cookie, _ = session(world, client, world.ann.key)
        for path in GET_PATHS:
            answer = client.get(path, headers={"Cookie": cookie})
            assert answer.status_code == 200, (path, answer.status_code, answer.text[:200])
            assert "no-store" in answer.headers.get("cache-control", ""), path
            assert "strict-transport-security" in answer.headers and "server" not in answer.headers, path
            assert world.ann.key not in answer.text and world.operator.key not in answer.text
            world.evidence.record("viewer-caddy", path, answer.text)


def test_through_caddy_a_write_needs_origin_and_csrf_and_is_attributed_to_the_member(world):
    with world.caddy.viewer() as client:
        cookie, csrf = session(world, client, world.ann.key)
        base = {"Cookie": cookie, "Content-Type": "application/json"}
        origin = {"Origin": world.caddy.viewer_origin}
        token = {"X-Taskmaster-CSRF": csrf}
        body = json.dumps({"text": "from the viewer through caddy"})
        assert client.post("/api/notes", content=body, headers=base).status_code == 403
        assert client.post("/api/notes", content=body, headers={**base, **origin}).status_code == 403
        assert client.post("/api/notes", content=body, headers={**base, **token}).status_code == 403
        cross = client.post("/api/notes", content=body, headers={**base, **token, "Origin": "https://evil.example"})
        assert cross.status_code == 403
        made = client.post("/api/notes", content=body, headers={**base, **token, **origin})
        assert made.status_code in (200, 201), (made.status_code, made.text)
    seen = world.bob.call("backlog_query", {"sql": "SELECT session FROM changes WHERE tool LIKE '%note%'"})
    assert "ann@" in seen, seen


@pytest.mark.parametrize("path", ["/backlog.yaml", "/v3/dev/edit-demo", "/static/v3/dev/edit-demo.html", "/api/session",
                                  "/mcp", "/read/v1/meta", "/v3/../backlog.yaml",
                                  "/file/.taskmaster/local/serve/operator.token"])
def test_through_caddy_the_removed_and_private_paths_are_refused_for_everyone(world, path):
    with world.caddy.viewer() as client:
        cookie, _ = session(world, client, world.ann.key)
    host = f"{VIEWER_SNI}:{world.caddy.https_port}"
    for extra in ("", f"Cookie: {cookie}\r\n"):
        raw = world.caddy.raw(VIEWER_SNI, f"GET {path} HTTP/1.1\r\nHost: {host}\r\n{extra}Connection: close\r\n\r\n".encode())
        assert int(raw.split(b" ", 2)[1]) in (303, 400, 404), (path, extra[:6], raw[:200])
        assert world.operator.key.encode() not in raw


def test_the_mcp_host_serves_no_viewer_path_and_the_viewer_host_no_mcp(world):
    for sni, path in (("tm.localhost", "/login"), ("tm.localhost", "/api/board"), ("tm.localhost", "/static/v3/js/main.js"),
                      (VIEWER_SNI, "/mcp"), (VIEWER_SNI, "/write/v1/x")):
        raw = world.caddy.raw(sni, f"GET {path} HTTP/1.1\r\nHost: {sni}:{world.caddy.https_port}\r\nConnection: close\r\n\r\n".encode())
        assert int(raw.split(b" ", 2)[1]) == 404, (sni, path, raw[:200])


def test_static_files_come_from_caddys_disk_with_the_apps_security_headers(world):
    with world.caddy.viewer() as client:
        main = client.get("/static/v3/js/main.js")
        assert main.status_code == 200 and "last-modified" in main.headers      # file_server's mark; the app never sends it
        assert main.headers["x-content-type-options"] == "nosniff" and main.headers.get("content-security-policy")
        assert client.get("/static/v3/dev/edit-demo.html").status_code == 404
        assert client.get("/static/v3/package.json").status_code == 404


def test_a_logout_through_caddy_ends_the_session(world):
    with world.caddy.viewer() as client:
        cookie, csrf = session(world, client, world.ann.key)
        assert client.get("/api/board", headers={"Cookie": cookie}).status_code == 200
        ended = client.post("/logout", headers={"Cookie": cookie, "Origin": world.caddy.viewer_origin,
                                                "X-Taskmaster-CSRF": csrf})
        assert ended.status_code in (200, 303), (ended.status_code, ended.text)
        assert client.get("/api/board", headers={"Cookie": cookie}).status_code in (401, 403)


def test_login_failures_are_charged_to_the_real_peer_not_to_a_forged_forwarding_header(world):
    """Last in the file: 25 failures spend this peer's budget for the minute. Earlier unauthenticated 404 probes were
    charged to the same peer (design D6), so the daemon and Caddy restart first for a fresh budget."""
    world.caddy.stop()
    world.host.stop()
    world.host.start()
    world.caddy.start(world.host)
    try:
        with world.caddy.viewer() as client:
            statuses = [login(world, client, f"tm_u_{n:016x}_{'x' * 43}", **{"X-Forwarded-For": f"203.0.113.{n + 1}"}).status_code
                        for n in range(25)]
        assert statuses[:20] == [401] * 20 and statuses[20:] == [429] * 5, statuses
        log = world.host.log_text()
        assert world.caddy.address in log and "203.0.113." not in log
    finally:
        world.caddy.stop()          # the daemon this test started must not outlive it (conftest child guard)
        world.host.stop()
