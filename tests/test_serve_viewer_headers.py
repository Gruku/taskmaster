# User intent: every viewer response carries exactly one Content-Security-Policy chosen by response kind, with no unsafe-inline, no host paths, and no server banner (team-server-028 D6, section 4.2-4.3).
import socket

import pytest

from taskmaster.serve.viewer import response_headers
from test_native_service_build import root  # noqa: F401  (fixture)
from test_serve_viewer import (CADDY, PUBLIC, git, login, make_viewer, req, v, work_tree)  # noqa: F401

ORIGIN = "https://v.example"
STATIC = f"{ORIGIN}/static/v3/"
APP_CSP = (f"default-src 'none'; script-src {STATIC}; style-src {STATIC}; font-src {STATIC}; img-src 'self'; "
           "connect-src 'self'; manifest-src 'self'; form-action 'self'; base-uri 'none'; object-src 'none'; "
           "frame-ancestors 'none'")
FILE_MD_CSP = (f"sandbox allow-scripts; default-src 'none'; script-src {STATIC}; style-src {STATIC}; "
               f"font-src {STATIC}; img-src 'none'; connect-src 'none'; form-action 'none'; base-uri 'none'; "
               "object-src 'none'; frame-ancestors 'none'")
STATIC_CSP = "default-src 'none'; frame-ancestors 'none'"
FILE_RAW_CSP = "sandbox; default-src 'none'; frame-ancestors 'none'"
EXPECTED = {
    "app": (APP_CSP, {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
                      "Cross-Origin-Opener-Policy": "same-origin", "Cross-Origin-Resource-Policy": "same-origin"}, True),
    "static": (STATIC_CSP, {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                            "Referrer-Policy": "same-origin"}, False),
    "file-md": (FILE_MD_CSP, {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                              "Referrer-Policy": "same-origin", "Cross-Origin-Opener-Policy": "same-origin",
                              "Cache-Control": "no-store"}, True),
    "file-raw": (FILE_RAW_CSP, {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                                "Referrer-Policy": "same-origin", "Cross-Origin-Opener-Policy": "same-origin",
                                "Cross-Origin-Resource-Policy": "same-origin", "Cache-Control": "no-store"}, False),
}


@pytest.fixture
def md_files(work_tree):
    for name in ("a.js", "a.mjs", "a.css", "a.html", "a.svg", "a.xml"):
        (work_tree / "docs" / name).write_text("x\n", encoding="utf-8")
    git(work_tree, "add", "docs")
    git(work_tree, "commit", "-q", "-m", "files")
    return work_tree


def kind_responses(v, cookie, public):
    extra = dict(CADDY) if public else {}
    jar = {**extra, "Cookie": cookie}
    return {"app": req(v, "GET", "/api/board", headers=jar),
            "static": req(v, "GET", "/static/v3/css/tokens.css", headers=extra),
            "file-md": req(v, "GET", "/file/docs/a.md", headers=jar),
            "file-raw": req(v, "GET", "/file/nope.txt", headers=jar)}


@pytest.mark.parametrize("public", [True, False])
def test_one_csp_per_response_matching_the_table(v, public):
    cookie, _ = login(v, public=public)
    origin = ORIGIN if public else v.loopback
    for kind, resp in kind_responses(v, cookie, public).items():
        csp, headers, permissions = EXPECTED[kind]
        assert resp.headers.get_all("Content-Security-Policy") == [csp.replace(ORIGIN, origin)], kind
        for name, value in headers.items():
            assert resp.headers.get_all(name) == [value], (kind, name)
        assert (resp.headers.get("Permissions-Policy") is not None) == permissions, kind
        if kind == "static":
            assert "Cross-Origin-Resource-Policy" not in resp.headers
    assert "Permissions-Policy" in req(v, "GET", "/login", headers=CADDY if public else {}).headers


def test_response_headers_function_matches_the_table():
    for kind, (csp, headers, permissions) in EXPECTED.items():
        got = dict(response_headers(kind, ORIGIN))
        assert got["Content-Security-Policy"] == csp
        for name, value in headers.items():
            assert got[name] == value, (kind, name)
        assert ("Permissions-Policy" in got) == permissions


def test_csp_has_no_unsafe_and_no_self_for_scripts_and_styles():
    for kind in EXPECTED:
        csp = dict(response_headers(kind, ORIGIN))["Content-Security-Policy"]
        assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
        if kind in ("app", "file-md"):
            for directive in csp.split(";"):
                if directive.strip().startswith(("script-src", "style-src", "font-src")):
                    assert "'self'" not in directive


@pytest.mark.parametrize("name", ["a.js", "a.mjs", "a.css", "a.html", "a.svg", "a.xml"])
def test_a_tracked_file_is_never_a_script_or_style(v, md_files, name):
    cookie, _ = login(v)
    resp = req(v, "GET", f"/file/docs/{name}", headers={"Cookie": cookie})
    assert resp.status == 200
    ctype = resp.headers["Content-Type"].lower()
    assert ctype.startswith("text/plain"), ctype
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_the_server_header_names_no_python(v):
    for path in ("/login", "/static/v3/css/tokens.css", "/nope"):
        server = req(v, "GET", path).headers.get("Server", "")
        assert server == "taskmaster" and "Python" not in server


def raw_status_page(v, line):
    sock = socket.create_connection(("127.0.0.1", v.port), timeout=10)
    try:
        sock.sendall(f"{line}\r\nHost: {v.loopback_host}\r\n\r\n".encode())
        data = b""
        while chunk := sock.recv(65536):
            data += chunk
        return data
    finally:
        sock.close()


def test_no_unauthenticated_answer_leaks_host_facts(v):
    import platform
    import socket as socket_module
    from pathlib import Path
    secrets_ = [str(v.root), v.root.as_posix(), str(Path.home()), Path.home().as_posix(),
                socket_module.gethostname(), platform.python_version(), "Python/"]
    answers = [req(v, "GET", "/login"), req(v, "GET", "/api/board"), req(v, "POST", "/api/tasks", body="{}"),
               req(v, "GET", "/nope"), req(v, "GET", "/api/board", headers={"Host": "evil.example"}),
               req(v, "GET", "/static/v3/css/tokens.css"), req(v, "POST", "/login", headers={"Origin": v.loopback},
                                                                body="token=nope")]
    raw_answers = [raw_status_page(v, "GET http://x/ HTTP/1.1"), raw_status_page(v, "BREW /x HTTP/1.1"),
                   raw_status_page(v, "NOTHTTP")]
    texts = [(r.text + str(dict(r.headers))) for r in answers] + [r.decode("latin-1") for r in raw_answers]
    for text in texts:
        for secret in secrets_:
            assert secret not in text, secret



def test_head_answers_like_get_without_a_body(v):
    """028 gate M4: HEAD on the page, a static asset, a /file/ page and the API gives GET's status and headers."""
    cookie, _ = login(v)
    for path in ("/", "/static/v3/js/main.js", "/file/docs/a.md", "/api/board", "/favicon.ico", "/login"):
        headers = {"Cookie": cookie} if path != "/login" else {}
        got = req(v, "GET", path, headers=headers)
        head = req(v, "HEAD", path, headers=headers)
        assert head.status == got.status == 200, (path, head.status, got.status)
        assert head.body == b"", path
        assert head.headers.get("Content-Length") == got.headers.get("Content-Length"), path
        assert head.headers["Content-Security-Policy"] == got.headers["Content-Security-Policy"], path
    assert req(v, "HEAD", "/").status == 303                               # no session: the same redirect as GET
