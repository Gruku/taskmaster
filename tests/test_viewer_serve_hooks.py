# User intent: the viewer hooks server mode needs leave the solo viewer byte-for-byte as it was.
import urllib.request

import pytest

from test_server_api import running_server  # noqa: F401  (fixture)


def _get(url, method="GET"):
    request = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(request) as resp:
        return resp.status, resp.headers, resp.read()


def test_solo_cors_header_unchanged(running_server):  # noqa: F811
    base, _ = running_server
    for path in ("/api/identity", "/api/viewer/prefs", "/v3", "/static/v3/js/main.js"):
        _, headers, _ = _get(base + path)
        assert headers.get("Access-Control-Allow-Origin") == "*", path
    status, headers, _ = _get(base + "/api/x", method="OPTIONS")
    assert status == 204 and headers["Access-Control-Allow-Origin"] == "*"
    assert headers["Access-Control-Allow-Methods"] == "GET, PUT, POST, OPTIONS"
    assert headers["Access-Control-Allow-Headers"] == "Content-Type"


def test_solo_index_html_unchanged(running_server):  # noqa: F811
    from taskmaster import backlog_server as bs
    base, _ = running_server
    html = (bs.SCRIPT_DIR / "viewer" / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="css/', 'href="/static/v3/css/').replace('src="js/', 'src="/static/v3/js/')
    html = html.replace('src="vendor/', 'src="/static/v3/vendor/').replace('href="vendor/', 'href="/static/v3/vendor/')
    _, _, body = _get(base + "/v3")
    assert body.decode("utf-8") == html
    assert "taskmaster-csrf" not in html


def test_solo_md_full_path_unchanged(running_server, tmp_path):  # noqa: F811
    base, _ = running_server
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("# hi\n", encoding="utf-8")
    _, _, body = _get(base + "/file/docs/a.md")
    expected = str((tmp_path / "docs" / "a.md").resolve()).replace("\\", "/")
    assert f"vscode://file/{expected}" in body.decode("utf-8")


def test_serve_mode_start_viewer_is_a_noop(monkeypatch):
    from taskmaster import backlog_server as bs

    class Forbidden:
        def __init__(self, *args, **kwargs):
            raise AssertionError("a viewer was bound in serve mode")
    monkeypatch.setenv("TASKMASTER_SERVE", "1")
    monkeypatch.setattr(bs, "_viewer_started", False)
    monkeypatch.setattr(bs, "ThreadingHTTPServer", Forbidden)
    monkeypatch.setattr(bs, "VIEWER_PORT", 4321)
    assert bs._start_viewer_server() == 4321
    assert bs._viewer_started is False


def _open_viewer(bs):
    tool = bs.backlog_open_viewer
    return getattr(tool, "fn", tool)()


def test_serve_mode_open_viewer_never_opens_a_browser(monkeypatch):
    from taskmaster import backlog_server as bs

    def forbidden(url):
        raise AssertionError("a browser was opened on the host")
    monkeypatch.setattr(bs.webbrowser, "open", forbidden)
    monkeypatch.setenv("TASKMASTER_SERVE", "1")
    monkeypatch.setattr(bs, "VIEWER_PUBLIC_URL", "https://v.example/")
    assert _open_viewer(bs) == "The viewer is served at https://v.example/; sign in with your key."
    monkeypatch.setattr(bs, "VIEWER_PUBLIC_URL", None)
    monkeypatch.setattr(bs, "VIEWER_PORT", 7471)
    assert _open_viewer(bs) == "The viewer is served at http://127.0.0.1:7471/; sign in with your key."


def test_solo_open_viewer_unchanged(monkeypatch):
    from taskmaster import backlog_server as bs
    opened = []
    monkeypatch.delenv("TASKMASTER_SERVE", raising=False)
    monkeypatch.setattr(bs.webbrowser, "open", opened.append)
    monkeypatch.setattr(bs, "_viewer_started", True)
    monkeypatch.setattr(bs, "VIEWER_PORT", 6801)
    assert _open_viewer(bs) == "Opened backlog viewer at http://127.0.0.1:6801/"
    assert opened == ["http://127.0.0.1:6801/"]


@pytest.mark.parametrize("value", ["0", "", "true"])
def test_only_exactly_one_turns_serve_mode_on(monkeypatch, value):
    from taskmaster import backlog_server as bs
    monkeypatch.setenv("TASKMASTER_SERVE", value)
    assert bs._serve_mode() is False
