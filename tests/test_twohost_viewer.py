# User intent: treat the viewer as the public internet surface it is at go-live: brute-forcing /login is budgeted, the cookie is HttpOnly/Secure/SameSite=Strict, the operator token does not log in off-host, every API read needs the cookie, every write needs Origin and CSRF, /file/ serves nothing outside the tracked set, the two extra routes (F3) answer only after login, and a logout ends the session; viewer bodies join the leak record (team-server-015 S13, Step 12b; the app directly here, and through Caddy in tests/test_twohost_caddy.py).
from __future__ import annotations

import json
from pathlib import Path

import pytest

import surface_inventory as inv
from twohost import waits
from twohost.fixtures import team
from twohost.member import run_git
from twohost.viewer import PUBLIC_ORIGIN, Viewer

pytestmark = [pytest.mark.twohost, pytest.mark.slow, pytest.mark.real_service_process,
              pytest.mark.xdist_group("heavy_processes"), pytest.mark.timeout(240)]

REPO = Path(__file__).resolve().parents[1]
GET_PATHS = ("/api/board", "/api/backlog", "/api/identity", "/api/threads", "/api/sessions",
             "/api/bugs", "/api/issues", "/api/ideas", "/api/continuity", "/api/dashboard/recent-events?since=2026-01-01T00:00:00Z", "/api/notes",
             "/api/viewer/prefs")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    try:
        with team(tmp_path_factory.mktemp("s13") / "run", patch, flags=["--viewer-origin", PUBLIC_ORIGIN], name="s13") as the_team:
            yield the_team
    finally:
        patch.undo()


def refused(answer) -> bool:
    """A stranger is turned away: 401/403, or a redirect to /login for a page."""
    return answer.status in (401, 403) or (answer.status == 303 and answer.headers.get("Location") == "/login")


def fresh(world, peer) -> Viewer:
    return Viewer(world.host.viewer_url, peer=peer)


def test_a_wrong_key_is_charged_to_the_failure_budget_and_the_21st_failure_is_refused(world):
    brute = fresh(world, "203.0.113.50")
    statuses = [brute.login(f"tm_u_{i:016x}_{'x' * 43}").status for i in range(25)]
    assert statuses == [401] * 20 + [429] * 5, statuses        # the budget is 20 refusals a minute per peer
    assert brute.login(world.ann.key).status == 429            # and even a right key waits out the budget


def test_a_member_logs_in_and_gets_a_secure_cookie_and_the_operator_token_does_not_log_in_off_host(world):
    ann = fresh(world, "203.0.113.11")
    response = ann.login(world.ann.key)
    assert response.status == 303, (response.status, response.text)
    cookie = response.headers["Set-Cookie"]
    parts = [p.strip() for p in cookie.split(";")]
    assert cookie.startswith("__Host-tm_session="), cookie
    assert {"HttpOnly", "Secure", "SameSite=Strict", "Path=/"} <= set(parts), cookie
    assert not any(p.lower().startswith("domain") for p in parts), cookie
    operator = fresh(world, "203.0.113.12")
    refused = operator.login(world.operator.key)
    assert refused.status in (401, 403) and "only on the host" in refused.text, (refused.status, refused.text)


def test_every_api_read_needs_the_cookie_and_answers_with_it(world):
    anonymous, ann = fresh(world, "203.0.113.13"), fresh(world, "203.0.113.14")
    assert ann.login(world.ann.key).status == 303
    for path in GET_PATHS:
        assert refused(anonymous.request("GET", path)), path
        answer = ann.request("GET", path)
        assert answer.status == 200, (path, answer.status, answer.text[:200])
        world.evidence.record("viewer", path, answer.text)
        assert "no-store" in answer.headers.get("Cache-Control", "") or path.startswith("/static"), path
        assert world.ann.key not in answer.text and world.operator.key not in answer.text


def test_every_write_needs_origin_and_csrf_and_is_attributed_to_the_member(world):
    ann = fresh(world, "203.0.113.15")
    assert ann.login(world.ann.key).status == 303
    body = json.dumps({"theme": "light"})
    json_type = {"Content-Type": "application/json"}
    assert ann.request("PUT", "/api/viewer/prefs", headers=json_type, body=body).status == 403              # no Origin, no CSRF
    assert ann.request("PUT", "/api/viewer/prefs", headers=json_type, body=body, origin=True).status == 403  # Origin, no CSRF
    assert ann.request("PUT", "/api/viewer/prefs", headers=json_type, body=body, csrf=True).status == 403    # CSRF, no Origin
    cross = ann.request("PUT", "/api/viewer/prefs", headers={**json_type, "Origin": "https://evil.example"}, body=body, csrf=True)
    assert cross.status == 403
    assert ann.request("PUT", "/api/viewer/prefs", headers=json_type, body=body, origin=True, csrf=True).status == 200
    created = ann.request("POST", "/api/notes", headers=json_type, origin=True, csrf=True,
                          body=json.dumps({"text": "from the viewer"}))
    assert created.status in (200, 201), (created.status, created.text)
    sessions = world.bob.call("backlog_query", {"sql": "SELECT session FROM changes WHERE tool LIKE '%note%'"})
    assert "ann@" in sessions, sessions                           # the viewer's write is attributed to the person


def test_file_serves_nothing_but_tracked_files_and_028_removed_the_extra_routes(world):
    anonymous, ann = fresh(world, "203.0.113.16"), fresh(world, "203.0.113.17")
    assert ann.login(world.ann.key).status == 303
    (world.root / "docs").mkdir(exist_ok=True)
    (world.root / "docs" / "hello.txt").write_text("tracked and readable", encoding="utf-8")
    run_git(world.operator.machine, world.root, "init", "-q", "-b", "main")       # /file/ serves what git tracks
    run_git(world.operator.machine, world.root, "add", "docs/hello.txt")
    run_git(world.operator.machine, world.root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "tracked")
    served = waits.until(lambda: (r := ann.request("GET", "/file/docs/hello.txt")).status == 200 and r, 20,
                         "the tracked file to be served (positive control)", interval=0.5)
    assert "tracked and readable" in served.text
    for path in ("/file/.taskmaster/local/serve/operator.token", "/file/.taskmaster/local/serve/principals.json",
                 "/file/../../../../Windows/win.ini", "/file/.git/config", "/file/.env"):
        answer = ann.request("GET", path)
        assert answer.status in (400, 403, 404), (path, answer.status)
        assert world.operator.key not in answer.text
    # 028 closed F3: the whole-backlog dump and the dev demo page are gone for everyone, signed in or not
    for path in ("/backlog.yaml", "/v3/dev/edit-demo", "/static/v3/dev/edit-demo.html", "/static/v3/js/../../x", "/api/session"):
        assert anonymous.request("GET", path).status in (303, 400, 404), path
        gone = ann.request("GET", path)
        assert gone.status in (400, 404), (path, gone.status)
        assert world.operator.key not in gone.text
    surface = json.loads(inv.FIXTURE.read_text(encoding="utf-8"))["viewer"]
    assert "/backlog.yaml" not in surface["caddy_matcher"] and "/v3/dev/edit-demo" not in surface["caddy_matcher"]
    assert "/api/session" in surface["refused_exact"]


def test_logout_ends_the_session(world):
    ann = fresh(world, "203.0.113.18")
    assert ann.login(world.ann.key).status == 303
    assert ann.request("GET", "/api/board").status == 200
    ended = ann.request("POST", "/logout", origin=True, csrf=True)
    assert ended.status in (200, 303), (ended.status, ended.text)
    assert refused(ann.request("GET", "/api/board"))
