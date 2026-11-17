# User intent: a clone is a server clone exactly when 009's marker or latch says so (fail closed when unreadable),
# one server has one canonical identity, and a key is only ever found for the origin it was stored or bound for.
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from taskmaster import server_marker
from taskmaster.coordinator import ownership
from taskmaster.remote import cli, keys, locate

REPO = Path(__file__).resolve().parents[1]
HOST_ID = "9f0c3e7a5b1d4e2f8a6c0b9d7e5f3a1c"
KEY = "tm_u_0123456789abcdef_" + "A" * 43


def marker(url="https://tm.example.com/mcp", **extra):
    return json.dumps({"format": 1, "host_id": HOST_ID, "mode": "server", "url": url, **extra})


def write_marker(root: Path, text: str) -> None:
    (root / server_marker.MARKER_NAME).write_text(text, encoding="utf-8")


# ── server_marker (the 009 API, minimal) ────────────────────────


def test_no_marker_is_none(tmp_path):
    assert server_marker.read_marker(tmp_path) is None
    assert server_marker.server_mode(tmp_path) is None


def test_a_valid_marker(tmp_path):
    write_marker(tmp_path, marker())
    found = server_marker.read_marker(tmp_path)
    assert found.url == "https://tm.example.com/mcp" and found.host_id == HOST_ID and found.source == "marker"


@pytest.mark.parametrize("text, reason", [
    ("{not json", "not JSON"),
    ("[1]", "not a JSON object"),
    (marker(format=2).replace('"format": 1, ', ""), "newer"),
    (marker(extra="x"), "keys must be exactly"),
    (marker().replace('"server"', '"solo"'), "mode"),
    (marker("http://tm.example.com/mcp"), "loopback"),
    (marker("http://localhost:7470/mcp"), "loopback"),
    (marker("https://u:p@tm.example.com/mcp"), "userinfo"),
    (marker("https://tm.example.com/mcp?x=1"), "query"),
    (marker().replace(HOST_ID, "XYZ"), "host_id"),
    ("{" + " " * 5000 + "}", "larger"),
])
def test_invalid_markers_are_config_errors(tmp_path, text, reason):
    write_marker(tmp_path, text)
    with pytest.raises(server_marker.ServerConfigError) as raised:
        server_marker.read_marker(tmp_path)
    assert reason in str(raised.value)


def test_http_to_a_loopback_literal_is_allowed(tmp_path):
    write_marker(tmp_path, marker("http://127.0.0.1:7470/mcp"))
    assert server_marker.read_marker(tmp_path).url.startswith("http://127.0.0.1")


def test_a_directory_at_the_marker_path_is_present_and_unreadable(tmp_path):
    (tmp_path / server_marker.MARKER_NAME).mkdir()
    with pytest.raises(server_marker.ServerConfigError):
        server_marker.server_mode(tmp_path)


def test_the_latch_counts_when_the_marker_is_absent(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / server_marker.LATCH_NAME).write_text(marker(first_seen="2026-11-01T00:00:00Z"),
                                                               encoding="utf-8")
    found = server_marker.server_mode(tmp_path)
    assert found.source == "latch" and found.url == "https://tm.example.com/mcp"


def test_the_marker_module_is_stdlib_only():
    text = (REPO / "taskmaster" / "server_marker.py").read_text(encoding="utf-8")
    for banned in ("from taskmaster", "import yaml", "import fastmcp"):
        assert banned not in text


# ── locate ──────────────────────────────────────────────────────


def test_locate_solo_and_server(tmp_path):
    assert locate.locate(tmp_path) is None
    write_marker(tmp_path, marker("https://TM.Example.com:443/mcp"))
    ref = locate.locate(tmp_path)
    assert ref.origin == "https://tm.example.com"
    assert ref.read_base == "https://tm.example.com/read/v1/"
    assert ref.server_id == locate.server_id("https://tm.example.com") and len(ref.server_id) == 16


def test_locate_reports_an_invalid_marker_as_config(tmp_path):
    write_marker(tmp_path, "{")
    with pytest.raises(server_marker.ServerConfigError):
        locate.locate(tmp_path)


@pytest.mark.parametrize("url", ["https://Host:443/mcp", "https://host/mcp", "https://HOST/other", "HTTPS://host"])
def test_one_server_one_origin(url):
    assert locate.canonical_origin(url) == "https://host"


def test_canonical_origin_keeps_a_non_default_port_and_encodes_idna():
    assert locate.canonical_origin("https://host:8443/x") == "https://host:8443"
    assert locate.canonical_origin("https://bücher.example/mcp") == "https://xn--bcher-kva.example"
    assert locate.canonical_origin("http://[::1]:7470/mcp") == "http://[::1]:7470"
    assert locate.canonical_origin("http://127.0.0.5/mcp") == "http://127.0.0.5"


@pytest.mark.parametrize("url", ["http://localhost/mcp", "http://example.com/mcp", "ftp://host", "https://"])
def test_canonical_origin_refuses(url):
    with pytest.raises(ValueError):
        locate.canonical_origin(url)


# ── keys ────────────────────────────────────────────────────────


@pytest.fixture
def server():
    return locate.server_ref("https://tm.example.com/mcp")


def env_for(tmp_path, **extra):
    return {"TASKMASTER_CONFIG_DIR": str(tmp_path / "cfg"), **extra}


def test_the_key_format_is_002s():
    from taskmaster.serve.principals import KEY_RE
    assert keys.KEY_RE.pattern == KEY_RE.pattern


def test_no_key_names_set_key(tmp_path, server):
    with pytest.raises(keys.NoKey) as raised:
        keys.load_key(server, env_for(tmp_path))
    assert "taskmaster remote set-key" in str(raised.value)


def test_a_stored_key_is_found_for_its_origin_only(tmp_path, server):
    env = env_for(tmp_path)
    path = keys.write_key("https://TM.example.com:443/other", KEY, env)
    ownership.verify_private(path)
    found = keys.load_key(server, env)
    assert found.value == KEY and found.key_id == "0123456789abcdef" and found.source == "file"
    assert KEY not in repr(found)
    with pytest.raises(keys.NoKey):
        keys.load_key(locate.server_ref("https://attacker.example/mcp"), env)


def test_the_env_key_needs_a_matching_bound_url(tmp_path, server):
    with pytest.raises(keys.NoKey) as raised:
        keys.load_key(server, env_for(tmp_path, TASKMASTER_SERVER_KEY=KEY))
    assert "nothing was sent" in str(raised.value)
    with pytest.raises(keys.NoKey) as raised:
        keys.load_key(server, env_for(tmp_path, TASKMASTER_SERVER_KEY=KEY,
                                      TASKMASTER_SERVER_URL="https://attacker.example/mcp"))
    assert "https://attacker.example" in str(raised.value) and "https://tm.example.com" in str(raised.value)
    found = keys.load_key(server, env_for(tmp_path, TASKMASTER_SERVER_KEY=KEY,
                                          TASKMASTER_SERVER_URL="https://tm.example.com:443/whatever"))
    assert found.source == "env" and found.marker.startswith("env:") and KEY not in found.marker


def test_operator_and_malformed_keys_are_refused(tmp_path, server):
    for value, text in (("tm_op_" + "x" * 40, "operator"), ("hello", "does not hold")):
        with pytest.raises(keys.NoKey) as raised:
            keys.load_key(server, env_for(tmp_path, TASKMASTER_SERVER_KEY=value,
                                          TASKMASTER_SERVER_URL="https://tm.example.com"))
        assert text in str(raised.value)
        with pytest.raises(keys.NoKey):
            keys.write_key(server.url, value, env_for(tmp_path))


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_a_loose_key_file_is_refused(tmp_path, server):
    env = env_for(tmp_path)
    path = keys.write_key(server.url, KEY, env)
    path.chmod(0o644)
    with pytest.raises(keys.NoKey) as raised:
        keys.load_key(server, env)
    assert "owner-only" in str(raised.value)


def test_a_linked_key_file_is_refused(tmp_path, server):
    env = env_for(tmp_path)
    path = keys.write_key(server.url, KEY, env)
    real = tmp_path / "real.key"
    real.write_text(KEY, encoding="utf-8")
    path.unlink()
    try:
        path.symlink_to(real)
    except OSError:
        pytest.skip("symlinks need privileges here")
    with pytest.raises(keys.NoKey):
        keys.load_key(server, env)


# ── set-key ─────────────────────────────────────────────────────


def test_set_key_reads_stdin_and_stores_owner_only(tmp_path, server):
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(["set-key", "--url", "https://tm.example.com/mcp"], stdin=io.StringIO(KEY + "\n"),
                    stdout=out, stderr=err, env=env_for(tmp_path))
    assert code == 0, err.getvalue()
    assert KEY not in out.getvalue()
    assert keys.load_key(server, env_for(tmp_path)).value == KEY


def test_set_key_refuses_a_key_on_the_command_line(tmp_path):
    err = io.StringIO()
    code = cli.main(["set-key", "--url", "https://tm.example.com/mcp", KEY], stdin=io.StringIO(""),
                    stdout=io.StringIO(), stderr=err, env=env_for(tmp_path))
    assert code == 2 and "stdin" in err.getvalue()
    assert not (tmp_path / "cfg").exists()


def test_set_key_refuses_a_bad_url_or_key(tmp_path):
    for url, value in (("http://example.com/mcp", KEY), ("https://tm.example.com/mcp", "nope")):
        assert cli.main(["set-key", "--url", url], stdin=io.StringIO(value), stdout=io.StringIO(),
                        stderr=io.StringIO(), env=env_for(tmp_path)) == 2


def test_set_key_through_the_front_door(tmp_path):
    env = dict(os.environ, TASKMASTER_CONFIG_DIR=str(tmp_path / "cfg"))
    done = subprocess.run([sys.executable, str(REPO / "taskmaster_cli.py"), "remote", "set-key", "--url",
                           "https://tm.example.com/mcp"], input=KEY, text=True, capture_output=True, env=env,
                          timeout=60)
    assert done.returncode == 0, done.stderr
    assert "stored the key for https://tm.example.com" in done.stdout
