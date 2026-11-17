# User intent: prove the server marker and latch are read the way the fence relies on: strict, bounded,
# never following links, fail closed on anything odd, and written canonically (team-server-009 Step 1).
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from taskmaster import server_marker as sm

HOST_ID = "9f0c3e7a5b1d4e2f8a6c0b9d7e5f3a1c"
EXAMPLE = ('{\n  "format": 1,\n  "host_id": "9f0c3e7a5b1d4e2f8a6c0b9d7e5f3a1c",\n  "mode": "server",\n'
           '  "url": "https://tm.example.com/mcp"\n}\n')
PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _doc(**changes):
    doc = {"format": 1, "host_id": HOST_ID, "mode": "server", "url": "https://tm.example.com/mcp"}
    doc.update(changes)
    return json.dumps(doc).encode()


def test_example_parses_and_renders_byte_for_byte():
    marker = sm.parse(EXAMPLE.encode())
    assert marker == sm.Marker(url="https://tm.example.com/mcp", host_id=HOST_ID)
    assert sm.render(marker) == EXAMPLE


def test_key_order_and_whitespace_render_canonically():
    raw = b'{"url":"https://tm.example.com/mcp","mode":"server","host_id":"%s","format":1}' % HOST_ID.encode()
    assert sm.render(sm.parse(raw)) == EXAMPLE


@pytest.mark.parametrize("raw, reason", [
    (_doc(format=2), "newer"),
    (_doc(format=0), "format 0"),
    (_doc(format=True), "format must be an integer"),
    (_doc(format="1"), "format must be an integer"),
    (_doc(extra=1), "unknown key"),
    (json.dumps({"format": 1, "mode": "server", "url": "https://a.b/mcp"}).encode(), "missing key"),
    (_doc(mode="solo"), "mode"),
    (_doc(url="https://user:pw@tm.example.com/mcp"), "userinfo"),
    (_doc(url="https://tm.example.com/mcp?x=1"), "query"),
    (_doc(url="https://tm.example.com/mcp#f"), "fragment"),
    (_doc(url="http://tm.example.com/mcp"), "loopback"),
    (_doc(url="ftp://tm.example.com/mcp"), "https://"),
    (_doc(url="https://tm.example.com/m cp"), "whitespace"),
    (_doc(url="https://" + "a" * 2050), "longer"),
    (_doc(url=""), "non-empty"),
    (_doc(host_id="ABC"), "host_id"),
    (_doc(host_id=HOST_ID.upper()), "host_id"),
    (b"[1]", "not a JSON object"),
    (b"{nope", "not JSON"),
    (b"\xff\xfe{}", "not UTF-8"),
    (b" " * 4097, "larger"),
])
def test_validation_refuses_with_a_reason(raw, reason):
    with pytest.raises(sm.ServerConfigError) as caught:
        sm.parse(raw)
    assert reason in caught.value.reason


@pytest.mark.parametrize("url", ["http://127.0.0.1:7470/mcp", "http://[::1]:9/mcp",
                                 "https://tm.example.com:8443/mcp"])
def test_loopback_http_and_https_ports_are_allowed(url):
    assert sm.parse(_doc(url=url)).url == url


def test_absent_marker_is_none(tmp_path):
    assert sm.read_marker(tmp_path) is None
    assert sm.server_mode(tmp_path) is None


def test_directory_at_the_marker_path_is_unreadable(tmp_path):
    (tmp_path / sm.MARKER_NAME).mkdir()
    with pytest.raises(sm.ServerConfigError, match="not a regular file"):
        sm.read_marker(tmp_path)


def _symlink(target, link):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available to this account")


def test_dangling_symlink_is_unreadable(tmp_path):
    _symlink(tmp_path / "missing.json", tmp_path / sm.MARKER_NAME)
    with pytest.raises(sm.ServerConfigError, match="not a regular file"):
        sm.read_marker(tmp_path)


def test_symlink_to_a_valid_marker_is_not_followed(tmp_path):
    (tmp_path / "real.json").write_text(EXAMPLE, encoding="utf-8")
    _symlink(tmp_path / "real.json", tmp_path / sm.MARKER_NAME)
    with pytest.raises(sm.ServerConfigError, match="not a regular file"):
        sm.read_marker(tmp_path)


def test_oversized_marker_reads_at_most_the_bound(tmp_path, monkeypatch):
    (tmp_path / sm.MARKER_NAME).write_bytes(b" " * (sm.MAX_BYTES * 4))
    read = []
    real = os.read

    def spy(fd, n):
        data = real(fd, n)
        read.append(len(data))
        return data
    monkeypatch.setattr(sm.os, "read", spy)
    with pytest.raises(sm.ServerConfigError, match="larger"):
        sm.read_marker(tmp_path)
    assert sum(read) <= sm.MAX_BYTES + 1


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO")
def test_fifo_is_refused_without_blocking(tmp_path):
    # Refused on lstat, before any open: an open of a FIFO without a writer would block.
    os.mkfifo(tmp_path / sm.MARKER_NAME)
    with pytest.raises(sm.ServerConfigError, match="not a regular file"):
        sm.read_marker(tmp_path)


def test_write_marker_is_canonical_and_atomic(tmp_path):
    path = sm.write_marker(tmp_path, "https://tm.example.com/mcp", HOST_ID)
    assert path.read_bytes() == EXAMPLE.encode()
    assert sm.read_marker(tmp_path).host_id == HOST_ID
    sm.write_marker(tmp_path, "https://tm.example.com/mcp", HOST_ID)
    assert path.read_bytes() == EXAMPLE.encode()
    assert sorted(p.name for p in tmp_path.iterdir()) == [sm.MARKER_NAME]


def test_write_marker_refuses_and_writes_nothing(tmp_path):
    with pytest.raises(sm.ServerConfigError):
        sm.write_marker(tmp_path, "http://tm.example.com/mcp", HOST_ID)
    assert list(tmp_path.iterdir()) == []


def test_latch_needs_a_git_directory(tmp_path):
    marker = sm.parse(EXAMPLE.encode())
    assert sm.record_latch(tmp_path, marker) is False
    (tmp_path / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    assert sm.record_latch(tmp_path, marker) is False
    assert not (tmp_path / ".git").is_dir()


def test_latch_is_written_once_and_honoured_by_server_mode(tmp_path):
    (tmp_path / ".git").mkdir()
    marker = sm.parse(EXAMPLE.encode())
    assert sm.record_latch(tmp_path, marker) is True
    first = sm.latch_path(tmp_path).read_bytes()
    assert sm.record_latch(tmp_path, marker) is False
    assert sm.latch_path(tmp_path).read_bytes() == first
    assert sm.read_marker(tmp_path) is None
    assert sm.server_mode(tmp_path) == marker
    assert [p.name for p in (tmp_path / ".git").iterdir()] == [sm.LATCH_NAME]
    assert sm.clear_latch(tmp_path) is True
    assert sm.clear_latch(tmp_path) is False
    assert sm.server_mode(tmp_path) is None


def test_marker_wins_over_latch(tmp_path):
    (tmp_path / ".git").mkdir()
    sm.record_latch(tmp_path, sm.Marker(url="https://old.example.com/mcp", host_id=HOST_ID))
    sm.write_marker(tmp_path, "https://tm.example.com/mcp", HOST_ID)
    assert sm.server_mode(tmp_path).url == "https://tm.example.com/mcp"


def test_record_latch_never_raises(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()

    def boom(*args, **kwargs):
        raise PermissionError("read-only")
    monkeypatch.setattr(sm, "_publish", boom)
    assert sm.record_latch(tmp_path, sm.parse(EXAMPLE.encode())) is False


@pytest.mark.parametrize("content", [b"{}", b"garbage", json.dumps({"marker": {"format": 1}}).encode()])
def test_unreadable_latch_raises(tmp_path, content):
    (tmp_path / ".git").mkdir()
    sm.latch_path(tmp_path).write_bytes(content)
    with pytest.raises(sm.ServerConfigError):
        sm.server_mode(tmp_path)


def test_module_imports_only_stdlib():
    src = (PLUGIN_ROOT / "taskmaster" / "server_marker.py").read_text(encoding="utf-8")
    for banned in ("import yaml", "import fastmcp", "from taskmaster", "import taskmaster"):
        assert banned not in src


def test_module_imports_with_yaml_and_fastmcp_unimportable():
    import subprocess
    code = ("import sys; sys.modules['yaml'] = None; sys.modules['fastmcp'] = None; "
            f"sys.path.insert(0, {str(PLUGIN_ROOT)!r}); import taskmaster.server_marker as m; print(m.MARKER_NAME)")
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == sm.MARKER_NAME
