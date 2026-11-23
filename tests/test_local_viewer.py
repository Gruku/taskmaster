# User intent: prove the local profile's guarded viewer (local-server-004): it binds the project's hashed 6800-6899 port without
# ever failing the server, and `backlog_open_viewer` signs the person in through a one-time, short-lived, loopback-only
# link instead of a token prompt; the team profile's viewer text and the add-user refusal stay as they were.
from __future__ import annotations

import hashlib
import socket
import threading
from types import SimpleNamespace

import httpx
import pytest

from taskmaster import backlog_server as bs
from taskmaster.serve import viewer_auth
from taskmaster.serve.viewer_auth import LINK_MAX_PENDING, LINK_PATH, LINK_TTL_SECONDS, LocalLinkLogin
from test_native_service_build import root  # noqa: F401,E402
from test_serve_local import local  # noqa: F401,E402


def hashed_port(project_root) -> int:
    return 6800 + int(hashlib.md5(str(project_root.resolve()).encode()).hexdigest(), 16) % 100


def hold(port: int) -> socket.socket:
    """Something else listening on 127.0.0.1:port (a fallback session's solo viewer, say)."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", port))
    sock.listen(1)
    return sock


@pytest.fixture
def argv(monkeypatch):
    """What the server handed the browser opener (it ends up on a command line); no real browser starts."""
    given = []
    monkeypatch.setattr(bs.webbrowser, "open", lambda url: given.append(url) or True)
    return given


def link_in_page(url: str) -> str:
    """The sign-in link inside the redirect page the browser is pointed at (read before the server deletes it)."""
    import html as _html
    import re
    from urllib.request import url2pathname
    from urllib.parse import urlparse
    text = open(url2pathname(urlparse(url).path), encoding="utf-8").read()
    return _html.unescape(re.search(r'content="0;url=([^"]+)"', text).group(1))


@pytest.fixture
def opened(monkeypatch):
    """The sign-in links the server asked a browser to open (no real browser starts), read out of the redirect page."""
    links = []
    monkeypatch.setattr(bs.webbrowser, "open", lambda url: links.append(link_in_page(url)) or True)
    return links


def viewer_url(world) -> str:
    return f"http://127.0.0.1:{world.daemon.viewer_port}"


def test_the_local_viewer_binds_a_port_in_the_projects_band_and_asks_for_sign_in(local):
    world = local(viewer_band=True)
    port = world.daemon.viewer_port
    assert port is not None and 6800 <= port <= 6899
    answer = httpx.get(f"http://127.0.0.1:{port}/", follow_redirects=False)
    assert answer.status_code == 303 and answer.headers["location"] == "/login"


def test_a_taken_port_moves_the_viewer_on_and_never_fails_the_server(local, root, monkeypatch):  # noqa: F811
    # Another xdist worker's viewer may already hold this project's hashed port: hold a free band port instead and
    # point the product's hash at it, so the test always exercises "the hashed port is taken".
    wanted = hashed_port(root)
    holder = None
    for candidate in [wanted, *range(6800, 6900)]:
        try:
            holder = hold(candidate)
            wanted = candidate
            break
        except OSError:
            continue
    assert holder is not None, "no free port in the band"
    monkeypatch.setattr(bs, "_project_port", lambda: wanted)
    try:
        world = local(viewer_band=True)
        assert world.daemon.viewer_port not in (None, wanted) and 6800 <= world.daemon.viewer_port <= 6899
        assert httpx.get(world.base_url + "/local/v1/status", headers=world.auth).status_code == 200
    finally:
        holder.close()


def test_no_free_port_runs_the_server_without_a_viewer(local, root, monkeypatch, opened):  # noqa: F811
    wanted = hashed_port(root)
    holder = hold(wanted)
    monkeypatch.setattr(bs, "_PORT_BAND", range(wanted, wanted + 1))
    try:
        world = local(viewer_band=True)
        assert world.daemon.viewer_port is None
        bridge = world.bridge("sess-a")
        assert "viewer is off" in bridge.call("backlog_open_viewer") and opened == []
        assert httpx.get(world.base_url + "/local/v1/status", headers=world.auth).status_code == 200
    finally:
        holder.close()


def test_open_viewer_opens_a_link_that_signs_in_once(local, opened):
    world = local(viewer_band=True)
    text = world.bridge("sess-a").call("backlog_open_viewer")
    assert "Opened the backlog viewer" in text and len(opened) == 1
    link = opened[0]
    assert link.startswith(viewer_url(world) + LINK_PATH + "?t=") and link.split("?t=")[1] not in text
    first = httpx.get(link, follow_redirects=False)
    assert first.status_code == 200 and b'http-equiv="refresh" content="0;url=/"' in first.content
    cookie = first.headers["set-cookie"]
    assert f"tm_session_{world.daemon.viewer_port}=" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert first.headers["referrer-policy"] == "same-origin"     # the viewer's own policy: the link never leaves the origin
    page = httpx.get(viewer_url(world) + "/", cookies={f"tm_session_{world.daemon.viewer_port}": cookie.split("=")[1].split(";")[0]})
    assert page.status_code == 200
    second = httpx.get(link, follow_redirects=False)
    assert second.status_code == 401 and "set-cookie" not in second.headers
    assert second.headers["content-type"].startswith("text/html") and b"already used or has expired" in second.content


def test_the_secret_is_never_on_the_openers_command_line_and_the_page_is_owner_only(local, monkeypatch):
    import os
    import stat
    from urllib.parse import urlparse
    from urllib.request import url2pathname
    seen = {}

    def opener(url):
        page = url2pathname(urlparse(url).path)
        seen.update(url=url, link=link_in_page(url), mode=stat.S_IMODE(os.stat(page).st_mode),
                    folder=os.path.dirname(page))
        return True
    monkeypatch.setattr(bs.webbrowser, "open", opener)
    monkeypatch.setattr(bs, "REDIRECT_FILE_LIFETIME_SECONDS", 0.2)
    world = local(viewer_band=True)
    text = world.bridge("sess-a").call("backlog_open_viewer")
    secret = seen["link"].split("?t=")[1]
    assert seen["url"].startswith("file:") and secret not in seen["url"] and secret not in text
    if os.name == "posix":
        assert seen["mode"] == 0o600 and stat.S_IMODE(os.stat(seen["folder"]).st_mode) == 0o700
    import time
    for _ in range(50):
        if not os.path.exists(seen["folder"]):
            break
        time.sleep(0.1)
    assert not os.path.exists(seen["folder"])                 # deleted shortly after
    assert httpx.get(seen["link"], follow_redirects=False).status_code == 200


def test_a_link_is_refused_for_any_other_method_or_shape(local, opened):
    world = local(viewer_band=True)
    world.bridge("sess-a").call("backlog_open_viewer")
    secret = opened[0].split("?t=")[1]
    base = viewer_url(world) + LINK_PATH
    assert httpx.post(base + "?t=" + secret).status_code == 405
    assert httpx.get(base).status_code == 401
    assert httpx.get(base + "?t=" + secret + "&t=" + secret).status_code == 401
    assert httpx.get(base + "?t=not-a-secret").status_code == 401
    assert httpx.get(base + "?t=" + secret, headers={"Host": "evil.example"}).status_code == 421
    # none of that spent the real link
    assert httpx.get(base + "?t=" + secret, follow_redirects=False).status_code == 200


def test_viewer_noise_never_throttles_the_mcp_app_or_the_status_route_or_the_real_link(local, opened):
    world = local(viewer_band=True)
    bridge = world.bridge("sess-a")
    bridge.call("backlog_status")                              # initialized before the noise
    cross = {"Sec-Fetch-Site": "cross-site"}
    for n in range(40):                                      # a hostile page's no-cors GETs: 404s, bad links, anonymous
        httpx.get(viewer_url(world) + "/nope-%d" % n, headers=cross)
        httpx.get(viewer_url(world) + LINK_PATH + "?t=x%d" % n, headers=cross)
        httpx.get(viewer_url(world) + "/api/x", headers=cross)
    assert httpx.get(world.base_url + "/local/v1/status", headers=world.auth).status_code == 200
    assert "Opened the backlog viewer" in bridge.call("backlog_open_viewer")      # the MCP app is not throttled
    assert world.bridge("sess-b").call("backlog_status")                                   # nor a new bridge's initialize
    link = opened[0]
    final = httpx.get(link, follow_redirects=False)
    assert final.status_code == 200, final.text         # the person's own link still works


def test_bad_links_are_bounded_on_the_viewer_only(local, opened):
    world = local(viewer_band=True)
    base = viewer_url(world) + LINK_PATH
    statuses = [httpx.get(base + "?t=guess-%d" % n).status_code for n in range(200)]
    assert 429 in statuses, "repeated bad links must be rate limited like wrong tokens"


def test_a_link_for_another_origin_host_is_refused_before_it_is_read(local, opened):
    world = local(viewer_band=True)
    world.bridge("sess-a").call("backlog_open_viewer")
    secret = opened[0].split("?t=")[1]
    wrong_port = world.daemon.viewer_port + 1
    assert httpx.get(viewer_url(world) + LINK_PATH + "?t=" + secret,
                     headers={"Host": f"127.0.0.1:{wrong_port}"}).status_code == 421


def test_without_a_browser_the_tool_returns_the_single_use_link(local, monkeypatch):
    monkeypatch.setattr(bs.webbrowser, "open", lambda url: False)   # (a failed open leaves no secret anywhere either)
    world = local(viewer_band=True)
    text = world.bridge("sess-a").call("backlog_open_viewer")
    link = next(word for word in text.split() if word.startswith("http") and LINK_PATH in word).rstrip(".")
    assert httpx.get(link, follow_redirects=False).status_code == 200
    assert httpx.get(link, follow_redirects=False).status_code == 401


def test_the_viewer_session_ends_when_the_operator_token_rotates(local, opened):
    from taskmaster.serve.auth import rotate_operator_token
    world = local(viewer_band=True)
    world.bridge("sess-a").call("backlog_open_viewer")
    first = httpx.get(opened[0], follow_redirects=False)
    jar = {f"tm_session_{world.daemon.viewer_port}": first.headers["set-cookie"].split("=")[1].split(";")[0]}
    assert httpx.get(viewer_url(world) + "/", cookies=jar).status_code == 200
    rotate_operator_token(world.root)
    assert httpx.get(viewer_url(world) + "/", cookies=jar, follow_redirects=False).status_code == 303


def test_no_viewer_flag_says_so(local, opened):
    world = local(viewer_band=False)
    assert world.daemon.viewer_port is None
    assert "viewer is off" in world.bridge("sess-a").call("backlog_open_viewer") and opened == []


# -- the link's own rules, on a fake clock --------------------------------------------------------------------------

class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


def make_login(clock):
    return LocalLinkLogin(authenticator=None, principals=None, inflight=None, clock=clock, operator_key=lambda: "k")


def test_a_link_expires_and_works_once_and_only_its_digest_is_kept():
    clock = Clock()
    login = make_login(clock)
    secret = login.mint_link()
    assert secret not in repr(login._links) and LINK_TTL_SECONDS <= 60
    assert login._take_link(secret) is True and login._take_link(secret) is False
    late = login.mint_link()
    clock.now += LINK_TTL_SECONDS + 1
    assert login._take_link(late) is False
    assert login._take_link("never-minted") is False


def test_pending_links_are_capped():
    clock = Clock()
    login = make_login(clock)
    secrets_ = [login.mint_link() for _ in range(LINK_MAX_PENDING + 3)]
    assert len(login._links) == LINK_MAX_PENDING
    assert login._take_link(secrets_[0]) is False and login._take_link(secrets_[-1]) is True


# -- the team profile's text is not the local profile's -----------------------------------------------------------

def test_the_team_profile_viewer_notice_is_unchanged(monkeypatch):
    monkeypatch.setattr(bs, "VIEWER_PUBLIC_URL", None)
    monkeypatch.setattr(bs, "VIEWER_PORT", 0)
    monkeypatch.setattr(bs, "VIEWER_LINK_MINTER", None)
    monkeypatch.setattr(bs, "VIEWER_LOCAL_OFF", None)
    assert bs._served_viewer_notice() == "The viewer is served at http://127.0.0.1:0/; sign in with your key."
    monkeypatch.setattr(bs, "VIEWER_PORT", 7471)
    assert bs._served_viewer_notice() == "The viewer is served at http://127.0.0.1:7471/; sign in with your key."
    monkeypatch.setattr(bs, "VIEWER_PUBLIC_URL", "https://v.example/")
    assert bs._served_viewer_notice() == "The viewer is served at https://v.example/; sign in with your key."


def _browser():
    import os, shutil
    for path in (shutil.which("chrome"), shutil.which("msedge"),
                 r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                 r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"):
        if path and os.path.exists(path):
            return path
    return None


@pytest.mark.skipif(_browser() is None, reason="no Chrome or Edge installed: SameSite=Strict is only applied by a real browser")
def test_a_real_browser_opening_the_redirect_page_lands_in_the_app_not_the_login_form(local, monkeypatch, tmp_path):
    """N1: the link is opened from a file: page (cross-site); the Strict cookie must still reach GET /."""
    import subprocess
    seen = {}

    def opener(url):
        result = subprocess.run([_browser(), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                                 f"--user-data-dir={tmp_path / 'profile'}", "--virtual-time-budget=8000", "--dump-dom", url],
                                capture_output=True, timeout=90, text=True, encoding="utf-8", errors="replace")
        seen["dom"] = result.stdout
        return True

    monkeypatch.setattr(bs.webbrowser, "open", opener)
    world = local(viewer_band=True)
    world.bridge("sess-a").call("backlog_open_viewer")
    dom = seen.get("dom", "")
    assert dom and 'name="token"' not in dom, dom[:400]
    assert "csrf" in dom.lower(), dom[:400]
