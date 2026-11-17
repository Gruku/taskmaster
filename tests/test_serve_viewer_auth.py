# User intent: prove the viewer's authentication sits behind one interface (team-server-028 D12): a header-trusting test adapter drives the guard, and the guard source holds no cookie or CSRF knowledge.
import json
import re
from pathlib import Path

import pytest

from taskmaster import backlog_server as bs
from taskmaster.serve.auth import OPERATOR
from taskmaster.serve.limits import KeyedLimiter
from taskmaster.serve.viewer_auth import ViewerIdentity
from test_native_service_build import root  # noqa: F401  (fixture)
from test_serve_viewer import (ALICE, BOB, CADDY, PUBLIC, Clock, make_viewer, req, v, work_tree)  # noqa: F401

PEOPLE = {"alice": ALICE, "bob": BOB, "operator": OPERATOR}


class HeaderAuth:
    """Test-only adapter: trusts X-Test-Principal, has no public paths and no forgery token."""

    PUBLIC_PATHS: tuple = ()

    def configure(self, origins, viewer_port):
        self.origins = origins

    def identify(self, handler):
        who = PEOPLE.get(handler.headers.get("X-Test-Principal") or "")
        return ViewerIdentity(who, None, who.handle) if who else None

    def presents_credential(self, handler):
        return False

    def has_identity(self, handler):
        return self.identify(handler) is not None

    def answer_public(self, handler, path, peer, reply):
        reply.refuse(404, {"error": "not found"})

    def sign_in_location(self, handler):
        return "/portal-signin"

    def check_forgery(self, handler, identity):
        return None

    def page_meta(self, identity):
        return '<meta name="taskmaster-portal" content="1">'

    def sign_out(self, handler, identity, reply):
        reply.redirect("/portal-signout")


@pytest.fixture
def hv(make_viewer):
    return make_viewer(auth_adapter=HeaderAuth())


def as_(who, **extra):
    return {"X-Test-Principal": who, **extra}


def test_the_adapter_answers_who_and_the_guard_does_the_rest(hv):
    assert req(hv, "GET", "/api/board").status == 401
    page = req(hv, "GET", "/")
    assert page.status == 303 and page.headers["Location"] == "/portal-signin"
    assert req(hv, "GET", "/api/board", headers=as_("alice")).status == 200
    html = req(hv, "GET", "/", headers=as_("alice")).text
    assert '<meta name="taskmaster-portal" content="1">' in html


def test_login_is_404_when_the_adapter_has_no_public_paths(hv):
    assert req(hv, "GET", "/login").status == 404
    assert req(hv, "POST", "/login", headers={"Origin": hv.loopback}, body="token=ka").status == 404


def test_the_route_table_and_404_charging_hold_under_the_adapter(make_viewer):
    clock = Clock()
    v = make_viewer(auth_adapter=HeaderAuth(), clock=clock, failures=KeyedLimiter(20, 0.0, clock=clock))
    for path in ("/backlog.yaml", "/api/session", "/v3", "/index.html"):
        assert req(v, "GET", path, headers=as_("alice")).status == 404, path
    for _ in range(30):                                    # an identified member is never charged
        assert req(v, "GET", "/nope", headers=as_("alice")).status == 404
    for _ in range(20):
        assert req(v, "GET", "/nope").status == 404
    assert req(v, "GET", "/nope").status == 429


def test_the_operator_host_local_refusal_is_the_guards(hv):
    resp = req(hv, "GET", "/api/board", headers=as_("operator", **{"X-Forwarded-For": "203.0.113.7"}))
    assert resp.status == 403 and "operator bootstrap token" in json.loads(resp.body)["error"]
    assert req(hv, "GET", "/api/board", headers=as_("operator")).status == 200       # on the host itself


def test_per_key_rate_and_inflight_limits_are_the_guards(make_viewer):
    v = make_viewer(auth_adapter=HeaderAuth(), principals=KeyedLimiter(2, 0.0))
    statuses = [req(v, "GET", "/api/board", headers=as_("alice")).status for _ in range(4)]
    assert statuses == [200, 200, 429, 429]
    assert req(v, "GET", "/api/board", headers=as_("bob")).status == 200


def test_a_write_runs_as_the_principal(hv, monkeypatch):
    seen = []
    original = bs.ViewerHandler.do_PUT

    def recording(self):
        seen.append(self.principal.handle)
        return original(self)
    monkeypatch.setattr(bs.ViewerHandler, "do_PUT", recording)
    headers = {**as_("alice"), "Origin": hv.loopback, "Content-Type": "application/json"}
    resp = req(hv, "PUT", "/api/viewer/prefs", headers=headers, body=json.dumps({"theme": "light"}))
    assert resp.status == 200 and seen == ["alice"]
    cross = req(hv, "PUT", "/api/viewer/prefs", headers={**headers, "Origin": "https://evil.example"}, body="{}")
    assert cross.status == 403


def test_the_guard_source_has_no_cookie_or_csrf_knowledge():
    source = (Path(__file__).resolve().parent.parent / "taskmaster" / "serve" / "viewer.py").read_text(encoding="utf-8")
    for forbidden in (r"\bSimpleCookie\b", r"Set-Cookie", r"__Host-", r"\bCSRF_META\b", r"compare_digest",
                      r"""["']Cookie["']"""):
        assert not re.search(forbidden, source), forbidden
