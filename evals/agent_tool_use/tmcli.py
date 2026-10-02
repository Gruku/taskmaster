# /// script
# requires-python = ">=3.11"
# dependencies = ["fastmcp>=3.4,<4"]
# ///
# User intent: the only door a test agent has to the backlog — tool names first, descriptions on
# request, then calls — so we can observe which tools an agent picks from names and descriptions
# alone. Every action is logged as a JSON line so a run can be reconstructed and graded afterwards.
"""Backlog tools, loaded on demand.

    tmcli.py --run RUN names                      list the available tool names
    tmcli.py --run RUN describe TOOL [TOOL ...]   show a tool's description and parameters
    tmcli.py --run RUN call TOOL [ARGS]           call a tool (after `describe` has loaded it)

ARGS for `call`, any one of:
    '{"key": "value", "n": 3}'     a JSON object
    key=value key2=value2          pairs; each value is read as JSON when it parses, else as text
    @args.json                     a file holding the JSON object
    -                              the JSON object on stdin
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evalpaths  # noqa: E402

RESULT_LOG_CHARS = 2000
_EMPTY = re.compile(
    r"^\s*(No |\*\*0 match|\[\]\s*$|\{\}\s*$|0 rows)|\"total\": 0\b|\"returned\": 0\b|\"items\": \[\]|\n0 rows\s*$")
_ERROR = re.compile(r"^\s*(Error\b|\{\"error\")")


class ArgError(Exception):
    pass


def parse_call_args(raw: list[str]) -> dict:
    """Tool arguments from the command line, in whichever form survived the shell."""
    if not raw:
        return {}
    text = None
    if len(raw) == 1 and raw[0] == "-":
        text = sys.stdin.read()
    elif len(raw) == 1 and raw[0].startswith("@"):
        try:
            text = Path(raw[0][1:]).read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise ArgError(f"cannot read {raw[0][1:]}: {exc}") from exc
    elif raw[0].lstrip().startswith("{"):
        text = " ".join(raw)
    if text is not None:
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ArgError(
                f"arguments are not valid JSON ({exc}). Received: {text!r}. If your shell stripped the quotes, "
                "pass key=value pairs instead, or put the JSON in a file and pass @file.json."
            ) from exc
        if not isinstance(value, dict):
            raise ArgError("arguments must be a JSON object")
        return value
    args = {}
    for item in raw:
        key, sep, value = item.partition("=")
        if not sep or not key.isidentifier():
            raise ArgError(f"cannot read argument {item!r}: expected a JSON object, key=value pairs, @file.json or -")
        try:
            args[key] = json.loads(value)
        except json.JSONDecodeError:
            args[key] = value
    return args


def ensure_run_copy(home: Path, run: str) -> Path:
    """This run's private copy of the seeded backlog, made on first use."""
    target = evalpaths.run_dir(home, run)
    if target.exists():
        return target
    seed = evalpaths.seed_dir(home)
    if not (seed / ".taskmaster").exists():
        raise SystemExit("backlog is not available (not seeded)")
    staging = target.with_name(f".{run}.{os.getpid()}.staging")
    shutil.copytree(seed, staging)
    try:
        staging.rename(target)
    except OSError:  # another invocation of the same run won the race
        shutil.rmtree(staging, ignore_errors=True)
    return target


class Log:
    def __init__(self, home: Path, run: str):
        self.path = evalpaths.log_path(home, run)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run = run

    def described(self) -> set[str]:
        """Tools whose description this run has already fetched."""
        if not self.path.exists():
            return set()
        entries = (json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip())
        return {e["tool"] for e in entries if e["action"] == "describe" and not e["error"]}

    def write(self, action: str, *, tool: str = "", args=None, result: str = "", error: bool = False,
              started: float | None = None, **extra) -> None:
        text = result or ""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "run": self.run,
            "action": action,
            "tool": tool,
            "args": args,
            "result": text[:RESULT_LOG_CHARS],
            "result_chars": len(text),
            "result_truncated": len(text) > RESULT_LOG_CHARS,
            "error": bool(error),
            "empty": bool(action == "call" and not error and _EMPTY.search(text)),
            **extra,
        }
        if started is not None:
            entry["duration_ms"] = int((time.monotonic() - started) * 1000)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def describe_text(tool) -> str:
    """A tool as the server exposes it: its description verbatim, then its parameter schema."""
    return (f"### {tool.name}\n{tool.description or ''}\n\nParameters (JSON Schema):\n"
            f"{json.dumps(tool.inputSchema, indent=2, ensure_ascii=False)}\n")


async def run(ns, home: Path, log: Log) -> int:
    root = ensure_run_copy(home, ns.run)
    # The server's banner and logging go to a file, never into what the agent reads.
    transport = StdioTransport(command="uv", args=["run", "--quiet", str(ns.server)],
                               env=dict(os.environ, TASKMASTER_ROOT=str(root)), cwd=str(root),
                               log_file=log.path.with_suffix(".server.log"))
    started = time.monotonic()
    async with Client(transport) as client:
        if ns.action == "names":
            names = [tool.name for tool in await client.list_tools()]
            print("\n".join(names))
            log.write("names", result="\n".join(names), started=started, count=len(names))
            return 0
        if ns.action == "describe":
            tools = {tool.name: tool for tool in await client.list_tools()}
            status = 0
            for name in ns.tools:
                if name not in tools:
                    message = f"Unknown tool: {name}"
                    print(message + "\n")
                    log.write("describe", tool=name, result=message, error=True, started=started)
                    status = 1
                    continue
                text = describe_text(tools[name])
                print(text)
                log.write("describe", tool=name, result=text, started=started)
            return status
        # Deferred loading: a tool can be called only once its schema has been fetched in this run.
        if ns.tool not in log.described():
            message = (f"Error: tool `{ns.tool}` is not loaded. Run `describe {ns.tool}` first; "
                       "`names` lists the available tools.")
            print(message)
            log.write("call", tool=ns.tool, args=ns.parsed, result=message, error=True, not_loaded=True, started=started)
            return 1
        result = await client.call_tool(ns.tool, ns.parsed, raise_on_error=False)
        text = "\n".join(getattr(block, "text", str(block)) for block in result.content)
        protocol_error = bool(getattr(result, "is_error", False))
        print(text)
        log.write("call", tool=ns.tool, args=ns.parsed, result=text, started=started,
                  error=protocol_error or bool(_ERROR.match(text)), protocol_error=protocol_error)
        return 1 if protocol_error else 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="tmcli.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", default=os.environ.get("TM_EVAL_RUN"), help="run id (given to you with the task)")
    parser.add_argument("--home", help=argparse.SUPPRESS)
    parser.add_argument("--server", default=os.environ.get("TM_EVAL_SERVER") or str(evalpaths.DEFAULT_SERVER), help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("names", help="list the available tool names")
    describe = sub.add_parser("describe", help="show a tool's description and parameters")
    describe.add_argument("tools", nargs="+", metavar="TOOL")
    call = sub.add_parser("call", help="call a tool")
    call.add_argument("tool", metavar="TOOL")
    call.add_argument("args", nargs=argparse.REMAINDER, metavar="ARGS")
    ns = parser.parse_args()
    if not ns.run:
        parser.error("--run is required")
    evalpaths.check_run_id(ns.run)
    home = evalpaths.home(ns.home)
    log = Log(home, ns.run)
    if ns.action == "call":
        try:
            ns.parsed = parse_call_args(ns.args)
        except ArgError as exc:
            message = f"Error (arguments not sent to the tool): {exc}"
            print(message)
            log.write("call", tool=ns.tool, args={"_raw": ns.args}, result=message, error=True, harness_error=True)
            return 2
    try:
        return asyncio.run(run(ns, home, log))
    except Exception as exc:  # the server did not start or the transport broke: say so and log it
        message = f"Error (harness): {type(exc).__name__}: {exc}"
        print(message)
        log.write(ns.action, tool=getattr(ns, "tool", ""), args=getattr(ns, "parsed", None), result=message,
                  error=True, harness_error=True)
        return 3


if __name__ == "__main__":
    sys.exit(main())
