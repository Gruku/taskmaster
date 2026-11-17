# User intent: the mods' reader prints one JSON object whatever happens (exit 0), reads a tools batch through the
# hooks' client and cache, tells solo from server clones, never prints more than the mod can read, and runs on the
# system interpreter with nothing but the standard library (team-server-007 D14).
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

from taskmaster.remote import readcli
from test_hooks_server_mode import HOOKS, clone, teammate  # noqa: F401  (fixtures)
from test_serve_read_api import root, serve  # noqa: F401  (fixtures)

CALLS = {"calls": [{"tool": "backlog_get_task", "args": {"task_id": "test-epic-001"}},
                   {"tool": "backlog_query", "args": {"sql": "SELECT id FROM entities WHERE kind='task' ORDER BY id"}}]}


def cli(world, *args, stdin=CALLS, env=None):
    done = subprocess.run([sys.executable, "-I", str(HOOKS / "tm_read.py"), *args], input=json.dumps(stdin),
                          text=True, encoding="utf-8", capture_output=True, env=env or world.env, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_batch_is_read_then_served_from_the_cache(teammate):
    first = cli(teammate, "tools", "--root", str(teammate.clone))
    assert first["source"] == "server" and first["v"] == 1 and isinstance(first["elapsed_ms"], int)
    texts = [r["text"] for r in first["data"]["results"]]
    assert "Ann's work" in texts[0] and "test-epic-004" in texts[1]
    assert cli(teammate, "tools", "--root", str(teammate.clone))["source"] == "cache"
    assert cli(teammate, "tools", "--root", str(teammate.clone), "--fresh")["source"] == "server"


def test_every_failure_is_json_with_a_kind(teammate, tmp_path):
    env = dict(teammate.env, TASKMASTER_CONFIG_DIR=str(tmp_path / "none"))
    assert cli(teammate, "tools", "--root", str(teammate.clone), env=env)["error"]["kind"] == "no-key"
    assert cli(teammate, "tools", "--root", str(teammate.clone), stdin="nope")["error"]["kind"] == "bad-request"
    assert cli(teammate, "nonsense", "--root", "x")["error"]["kind"] == "bad-request"
    solo = tmp_path / "solo"
    (solo / ".taskmaster").mkdir(parents=True)
    out = cli(teammate, "tools", "--root", str(solo))
    assert out["error"] == {"kind": "config", "message": "not a server-mode project"}


def test_mode(teammate, tmp_path):
    assert cli(teammate, "mode", "--root", str(teammate.clone))["mode"] == "server"
    (tmp_path / "solo" / ".taskmaster").mkdir(parents=True)
    assert cli(teammate, "mode", "--root", str(tmp_path / "solo"))["mode"] == "solo"
    (tmp_path / "bare").mkdir()
    assert cli(teammate, "mode", "--root", str(tmp_path / "bare"))["mode"] == "none"
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / ".taskmaster-server.json").write_text("{", encoding="utf-8")
    assert cli(teammate, "mode", "--root", str(tmp_path / "broken"))["mode"] == "server"


def test_stdout_never_exceeds_what_the_mod_can_read(monkeypatch):
    monkeypatch.setattr(readcli, "run", lambda argv, stdin, started: {"data": "x" * (5 * 1024 * 1024)})
    out = io.StringIO()
    assert readcli.main(["tools", "--root", "."], io.StringIO(""), out) == 0
    assert len(out.getvalue()) < 4 * 1024 * 1024
    assert json.loads(out.getvalue())["error"]["kind"] == "bad-response"


def test_the_reader_needs_only_the_standard_library(tmp_path):
    blocked = ("yaml", "fastmcp", "starlette", "mcp", "httpx", "uvicorn", "pydantic")
    code = ("import sys\n"
            f"BLOCKED = {blocked!r}\n"
            "class Block:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name.split('.')[0] in BLOCKED: raise ImportError('blocked: ' + name)\n"
            "sys.meta_path.insert(0, Block())\n"
            f"sys.path.insert(0, {str(HOOKS.parent)!r})\n"
            "import taskmaster.remote.readcli, taskmaster.remote.hookside, taskmaster.remote.client\n"
            "import taskmaster.remote.cli, taskmaster.server_marker\n"
            "print('ok')\n")
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    assert done.stdout.strip() == "ok", done.stderr


def test_an_old_interpreter_exits_3_before_parsing_anything_newer():
    text = (HOOKS / "tm_read.py").read_text(encoding="utf-8")
    head = text.split("import os", 1)[0]
    assert "f\"" not in head and "f'" not in head and "annotations" not in head
    assert "sys.exit(TOO_OLD)" in head and "TOO_OLD = 3" in head
