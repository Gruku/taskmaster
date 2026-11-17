# User intent: prove a team member can never make the server host run Git. Every tool a member may call, every read
# view, the tools batch and the merge-stamp route run against a live daemon while every Git child is recorded; only the
# host's own read-only shapes on its own files pass (team-server-011 D8).
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import httpx
import pytest

from test_serve_read_api import root, serve  # noqa: F401  (fixtures)
from test_serve_clone_fields import CLONE_A, T1, T3, call  # noqa: F401

ALLOWED_HOST_GIT = (
    (("rev-parse", "--show-toplevel", "--git-common-dir"), "taskmaster"),
    (("config", "--list", "--show-origin", "-z"), "taskmaster"),
    (("ls-files", "-z", "--eol", "--", "."), "taskmaster"),
    (("ls-files", "-z", "--full-name", "--", "local"), "taskmaster"),
    (("check-attr", "-z", "--stdin", "text", "eol"), "root"),
    # The coordinator's own publication-pin checks on the host checkout (`coordinator/git.py: repository`): fixed flags,
    # cwd exactly the host root, no member-supplied path (a deviation from the design table, noted in the resume file).
    (("rev-parse", "--show-toplevel"), "host"),
    (("rev-parse", "--absolute-git-dir"), "host"),
    (("rev-parse", "--git-path", "index"), "host"),
)
SHA = "a" * 40


def _inside(path, base) -> bool:
    try:
        Path(path).resolve().relative_to(Path(base).resolve())
        return True
    except ValueError:
        return False


def _violations(recorded, host_root) -> list:
    bad = []
    for argv, cwd, stdin in recorded:
        args = [a for a in argv[1:] if a != "--no-optional-locks"]
        cwd = Path(cwd or ".")
        if args[:1] == ["-C"]:
            where, args = Path(args[1]), args[2:]
            if not _inside(where, host_root) or args[:1] != ["rev-parse"]:
                bad.append((argv, cwd))
            continue
        ok = False
        for shape, scope in ALLOWED_HOST_GIT:
            if tuple(args[:len(shape)]) != shape or len(args) != len(shape):
                continue
            if scope == "host":
                ok = cwd.resolve() == Path(host_root).resolve()
            elif scope == "taskmaster":
                ok = _inside(cwd, Path(host_root) / ".taskmaster")
            elif scope == "root":
                ok = _inside(cwd, host_root) and all(str(p).replace("\\", "/").startswith(".taskmaster/")
                                                     for p in (stdin or "").split("\0") if p)
            if ok:
                break
        if not ok:
            bad.append((argv, cwd))
    return bad


@pytest.fixture
def recorded(monkeypatch):
    seen = []
    real = subprocess.Popen.__init__

    def spy(self, args, *a, **kw):
        argv = [str(x) for x in (args if isinstance(args, (list, tuple)) else [args])]
        if argv and Path(argv[0]).stem.lower() == "git":
            seen.append((argv, kw.get("cwd"), None))
        return real(self, args, *a, **kw)
    monkeypatch.setattr(subprocess.Popen, "__init__", spy)
    return seen


async def _every_tool(world):
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    transport = StreamableHttpTransport(world.url, headers={"X-Taskmaster-Clone": CLONE_A}, auth=world.keys["ann"])
    async with Client(transport) as client:
        names = sorted(tool.name for tool in await client.list_tools())
        for name in names:
            try:
                await client.call_tool(name, {}, raise_on_error=False)
            except Exception:       # a refusal or a missing argument is an answer; only Git is under test
                pass
    return names


def test_no_tool_view_or_route_makes_the_host_run_git_on_a_members_say_so(serve, recorded):
    world = serve()
    host = world.root
    names = asyncio.run(_every_tool(world))
    assert len(names) > 40, names
    targeted = [
        ("backlog_update_task", {"task_id": T1, "field": "branch", "value": "../../etc"}),
        ("backlog_update_task", {"task_id": T1, "field": "worktree", "value": "C:/Windows"}),
        ("backlog_blast_radius", {"mode": "evidence", "task_id": T1}),
        ("backlog_sync", {}),
        ("backlog_record_merge", {"task_id": T3, "rung": "qa", "sha": SHA}),
        ("backlog_pick_task", {"task_id": T1}),
        # Security review m-11: the calls that name a path or reach Git-adjacent code with real arguments, not {}.
        ("backlog_update_task", {"task_id": T1, "field": "sub_repo", "value": "../.."}),
        ("backlog_update_task", {"task_id": T1, "field": "sub_repo", "value": "services/api"}),
        ("backlog_blast_radius", {"mode": "evidence", "task_id": T1}),
        ("backlog_update_task", {"task_id": T1, "field": "docs", "value": "plan:../../outside.md"}),
        ("backlog_get_task", {"task_id": T1, "verbose": True}),
        ("backlog_document", {"kind": "task", "entity_id": T1}),
        ("backlog_document_import", {"kind": "task", "entity_id": T1, "sections": ["plan"],
                                     "content": {"plan": "# Plan"}}),
        ("backlog_project_set", {"yaml_content": "schema_version: 1\nmeta:\n  name: X\n  slug: x\n"
                                                 "repos:\n  - name: up\n    path: ../..\n"}),
        ("backlog_linear", {"action": "link", "task_id": T1, "external_key": "ENG-1"}),
        ("backlog_linear", {"action": "retry"}),
        ("backlog_pick_task", {"task_id": T1}),
    ]
    for tool, args in targeted:
        try:
            call(world, "ann", tool, args, CLONE_A)
        except Exception:
            pass
    headers = {"Authorization": f"Bearer {world.keys['ann']}"}
    for view in ("meta", "paths", "merge-gate", "merge-target"):
        httpx.get(f"{world.base}/read/v1/{view}", params={"branch": "solo-b", "command": "git merge solo-b"},
                  headers=headers, timeout=60)
    for path in ("../../outside.md", "src/a.py", ".git/config"):
        httpx.get(f"{world.base}/read/v1/paths", params={"path": path}, headers=headers, timeout=60)
    httpx.post(f"{world.base}/read/v1/tools", json={"calls": [{"tool": "backlog_status", "args": {}}]},
               headers=headers, timeout=60)
    httpx.post(f"{world.base}/write/v1/merge-stamp", timeout=60, headers=headers,
               json={"branch": "solo-b", "target": "qa-line", "sha": SHA, "merged_at": "2026-11-07T10:15",
                     "clone": CLONE_A})
    assert _violations(recorded, host) == [], _violations(recorded, host)


def test_the_ratchet_itself_flags_a_stray_git_argv(tmp_path):
    host = tmp_path / "host"
    (host / ".taskmaster").mkdir(parents=True)
    ok = [(["git", "ls-files", "-z", "--eol", "--", "."], str(host / ".taskmaster"), None),
          (["git", "-C", str(host / "sub"), "rev-parse", "--git-dir"], None, None)]
    assert _violations(ok, host) == []
    for argv, cwd in ((["git", "log"], str(host)), (["git", "rev-parse", "--show-toplevel"], str(tmp_path)), (["git", "-C", str(tmp_path), "rev-parse", "HEAD"], None),
                      (["git", "merge-base", "a", "b"], str(host / ".taskmaster")),
                      (["git", "ls-files", "-z", "--eol", "--", "."], str(tmp_path))):
        assert _violations([(argv, cwd, None)], host)
