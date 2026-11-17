# User intent: a member's door to the server, exactly as a teammate's machine has it: MCP over HTTP with their own key (optionally arriving as an off-host peer through X-Forwarded-For), raw HTTP for probes, and the member's own environment for the CLI (015 A2, A3).
"""One member (or the operator) of the harness's team."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import httpx

import serve_helpers
from .evidence import Evidence
from .world import Machine

OFF_HOST_XFF = {"X-Forwarded-For": "203.0.113.7"}      # what Caddy adds on the real path (A3)
REPO = Path(__file__).resolve().parents[2]


def machine_cli(machine: Machine, *args: str, stdin: str | None = None, timeout: float = 120,
                cwd: Path | None = None) -> subprocess.CompletedProcess:
    """taskmaster_cli.py in one machine's own environment, in its root (or `cwd`)."""
    return subprocess.run([sys.executable, str(REPO / "taskmaster_cli.py"), *args], input=stdin, text=True,
                          capture_output=True, env=machine.env, cwd=str(cwd or machine.root or machine.base), timeout=timeout)


class Member:
    def __init__(self, machine: Machine, host, key: str, evidence: Evidence | None = None):
        self.machine, self.host, self.key = machine, host, key
        self.name = machine.name
        self.evidence = evidence or Evidence()

    def mcp(self, *, off_host: bool = True, headers: dict | None = None) -> serve_helpers.Connection:
        """A context-managed MCP connection with this member's key; `off_host` adds the X-Forwarded-For Caddy would."""
        merged = {**(OFF_HOST_XFF if off_host else {}), **(headers or {})}
        return serve_helpers.open_connection(self.host.url, self.key, headers=merged)

    def call(self, tool: str, arguments: dict | None = None, *, off_host: bool = True, record: bool = True,
             framed: bool = False) -> str:
        """`framed=True` keeps the server's untrusted-data frames (team-server-027); the default strips them."""
        with self.mcp(off_host=off_host) as connection:
            text = connection.call(tool, arguments, framed=framed)
        if record:
            self.evidence.record("mcp", tool, text)
            self.evidence.mcp(who=self.name, tool=tool, arguments=arguments, key=self.key, answer=text)
        return text

    def http(self, method: str, path: str, *, key: str | None = ..., headers: dict | None = None, off_host: bool = True,
             **kwargs) -> httpx.Response:
        token = self.key if key is ... else key
        merged = {**(OFF_HOST_XFF if off_host else {}), **({"Authorization": f"Bearer {token}"} if token else {}),
                  **(headers or {})}
        response = httpx.request(method, self.host.url.rstrip("/").rsplit("/mcp", 1)[0] + path, headers=merged,
                                 timeout=30, **kwargs)
        sent = kwargs.get("content") or kwargs.get("data") or kwargs.get("json")
        self.evidence.transcript(who=self.name, method=method, target=path, authorization=merged.get("Authorization"),
                                 request=sent if isinstance(sent, str) else None if sent is None else str(sent),
                                 status=response.status_code, response=response.text)
        return response

    def cli(self, *args: str, stdin: str | None = None, timeout: float = 120) -> subprocess.CompletedProcess:
        """taskmaster_cli.py in this member's own environment (their home, config and cache dirs)."""
        return machine_cli(self.machine, *args, stdin=stdin, timeout=timeout)

    def set_key(self, url: str | None = None) -> subprocess.CompletedProcess:
        """`taskmaster remote set-key --url <marker url>` with the key on stdin, as the how-to says."""
        return self.cli("remote", "set-key", "--url", url or self.host.url, stdin=self.key + "\n")


HOOKS = REPO / "hooks"
HOST_ID = "0" * 31 + "1"


def run_git(machine: Machine, path: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(path), *args], env=machine.env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, (args, done.stderr)
    return done.stdout.strip()


def make_clone(member: Member, url: str, *, branches=("feature/x",)) -> Path:
    """The member's working clone: a Git repo with a server marker (what cloning the team repo gives them), one
    commit and the named branches. The marker's URL is the server's MCP URL, which 009 allows over loopback."""
    clone = member.machine.base / "machines" / member.name / "clone"
    clone.mkdir(parents=True, exist_ok=True)
    run_git(member.machine, clone, "init", "-q", "-b", "main")
    (clone / "README").write_text("team repo\n", encoding="utf-8")
    (clone / ".taskmaster-server.json").write_text(
        json.dumps({"format": 1, "host_id": HOST_ID, "mode": "server", "url": url}), encoding="utf-8")
    run_git(member.machine, clone, "add", "-A")
    run_git(member.machine, clone, "commit", "-q", "-m", "init")
    for branch in branches:
        run_git(member.machine, clone, "branch", branch)
    member.machine.root = clone
    return clone


def run_hook(member: Member, script: str, payload: dict, *, timeout: float = 60) -> subprocess.CompletedProcess:
    """A plugin hook as the host runs it: the script under the system interpreter, the payload on stdin, in the
    member's own environment and clone."""
    return subprocess.run([sys.executable, str(HOOKS / script)], input=json.dumps(payload), text=True, capture_output=True,
                          env=member.machine.env, cwd=str(member.machine.root), timeout=timeout)


SERVER = REPO / "backlog_server.py"


def stdio_calls(member_env: dict, cwd: Path, calls, root_env: Path | None = None) -> list[str]:
    """An ordinary local (stdio) Taskmaster session started the way an agent host starts it, in `cwd`."""
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    env = {k: v for k, v in member_env.items() if k not in ("TASKMASTER_ROOT", "PYTHONPATH")}
    if root_env:
        env["TASKMASTER_ROOT"] = str(root_env)

    async def go():
        out = []
        async with Client(StdioTransport(sys.executable, [str(SERVER)], env=env, cwd=str(cwd)), timeout=90) as client:
            for tool, arguments in calls:
                result = await client.call_tool(tool, arguments, raise_on_error=False)
                out.append(result.content[0].text if result.content else "")
        return out
    return asyncio.run(go())


def local_stores(path: Path) -> list[Path]:
    return [p for p in path.rglob(".taskmaster") if ".git" not in p.parts]
