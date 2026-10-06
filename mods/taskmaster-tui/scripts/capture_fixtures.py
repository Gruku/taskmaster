# /// script
# requires-python = ">=3.11"
# dependencies = ["fastmcp>=3.4,<4"]
# ///
# User intent: capture what the Taskmaster MCP server really answers — read-only from the claude-tools (legacy) and
# CodeMaestro (native) backlogs, and the write paths from a throwaway scratch store — as redacted TypeScript fixtures.
"""Capture Taskmaster MCP replies as taskmaster-tui test fixtures.

    uv run --no-project --with "fastmcp>=3.4,<4" python mods/taskmaster-tui/scripts/capture_fixtures.py [--server PATH]

Real backlogs get read-only calls only. Every write runs against a scratch store in a temporary folder.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

sys.path.insert(0, str(Path(__file__).resolve().parent))
from redact import IdPseudonyms, redact_reply  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "tests" / "fixtures" / "captured"
DEFAULT_SERVER = Path.home() / ".claude" / "plugins" / "cache" / "gruku-tools" / "taskmaster" / "7.1.0" / "backlog_server.py"
# The last field: pseudonymise id prefixes. CodeMaestro is work, so its project and epic names are replaced; claude-tools
# ids are taskmaster's own, already public, and stay.
STORES = (
    ("LEGACY", "legacy", Path(r"C:\Users\gruku\Files\Claude\claude-tools"), False),
    ("NATIVE", "native", Path(r"C:\Users\gruku\Files\Work\CodeMaestro"), True),
)
MISSING_PREFIX = "zz-missing"
READS = (
    ("store_status", "backlog_store_status", {}),
    ("list_in_review", "backlog_list_tasks", {"status": "in-review", "limit": 40}),
    ("list_waiting", "backlog_list_tasks", {"status": "in-review", "waiting_on_human": True, "limit": 40}),
    ("list_in_progress", "backlog_list_tasks", {"status": "in-progress", "limit": 3}),
    ("continuity_review", "backlog_continuity_items", {"action_class": "review", "limit": 40}),
    ("continuity_decide", "backlog_continuity_items", {"action_class": "decide", "limit": 40}),
    ("handovers_open", "backlog_handover_list", {"format": "json", "status": "open", "limit": 5}),
    ("get_missing", "backlog_get_task", {"task_id": f"{MISSING_PREFIX}-999"}),
    ("pipeline_missing", "backlog_task_pipeline", {"task_id": f"{MISSING_PREFIX}-999"}),
)
FIRST_ROW_ID = re.compile(r"^- `([^`]+)`", re.M)
ANY_TASK_ID = re.compile(r"`([a-z][a-z0-9]*(?:-[a-z0-9]+)*-\d+)`")


def transport(server: Path, root: Path, log: Path) -> StdioTransport:
    return StdioTransport(command="uv", args=["run", "--quiet", str(server)],
                          env=dict(os.environ, TASKMASTER_ROOT=str(root)), cwd=str(root), log_file=log)


async def call(client: Client, tool: str, args: dict) -> dict:
    result = await client.call_tool(tool, args, raise_on_error=False)
    text = "\n".join(getattr(block, "text", str(block)) for block in result.content)
    return {"tool": tool, "args": args, "isError": bool(getattr(result, "is_error", False)), "text": text}


async def capture_real(server: Path, root: Path, log: Path, pseudonymise: bool) -> dict:
    replies: dict[str, dict] = {}
    async with Client(transport(server, root, log)) as client:
        for name, tool, args in READS:
            replies[name] = await call(client, tool, args)
        for name, source in (("review", "list_in_review"), ("active", "list_in_progress")):
            found = FIRST_ROW_ID.search(replies[source]["text"])
            if found:
                replies[f"get_{name}"] = await call(client, "backlog_get_task", {"task_id": found.group(1)})
                replies[f"pipeline_{name}"] = await call(client, "backlog_task_pipeline", {"task_id": found.group(1)})
    if not pseudonymise:
        return {key: {**value, "text": redact_reply(value["text"])} for key, value in replies.items()}
    # One mapping for the whole capture, applied in reply order, to the texts and to the task ids in the call args.
    ids = IdPseudonyms(keep=(MISSING_PREFIX,))
    out: dict[str, dict] = {}
    for key, value in replies.items():
        text = redact_reply(value["text"], ids)
        args = {k: ids(v) if k == "task_id" and isinstance(v, str) else v for k, v in value["args"].items()}
        out[key] = {**value, "args": args, "text": text}
    return out


async def capture_scratch(server: Path, log: Path) -> tuple[str, dict]:
    replies: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="tm-tui-fixtures-", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        async with Client(transport(server, root, log)) as client:
            replies["init"] = await call(client, "backlog_init", {"project_name": "tm-tui-fixtures"})
            replies["add_phase"] = await call(client, "backlog_add_phase", {"phase_id": "fx-phase", "name": "Fixtures"})
            replies["add_epic"] = await call(client, "backlog_add_epic", {"epic_id": "fx", "name": "Fixtures", "done_when": "never"})
            replies["add_task"] = await call(client, "backlog_add_task", {
                "title": "Scratch review task (with parentheses) — and a dash", "epic": "fx", "phase": "fx-phase", "priority": "high"})
            found = ANY_TASK_ID.search(replies["add_task"]["text"])
            if not found:
                raise SystemExit(f"could not read the new task id from: {replies['add_task']['text']!r}")
            tid = found.group(1)
            steps = (
                ("pick", "backlog_pick_task", {"task_id": tid}),
                ("set_human_action", "backlog_update_task", {"task_id": tid, "field": "human_action",
                                                             "value": "Check the scratch thing\nsecond line of the check"}),
                ("to_in_review", "backlog_update_task", {"task_id": tid, "field": "status", "value": "in-review"}),
                ("list_in_review", "backlog_list_tasks", {"status": "in-review", "limit": 0}),
                ("get_in_review", "backlog_get_task", {"task_id": tid}),
                ("pipeline", "backlog_task_pipeline", {"task_id": tid}),
                ("continuity_review", "backlog_continuity_items", {"action_class": "review", "limit": 0}),
                ("complete_missing", "backlog_complete_task", {"task_id": "zz-missing-999", "done": "Signed off in review queue"}),
                ("back_status", "backlog_update_task", {"task_id": tid, "field": "status", "value": "in-progress"}),
                ("back_clear_human_action", "backlog_update_task", {"task_id": tid, "field": "human_action", "value": ""}),
                ("back_clear_again", "backlog_update_task", {"task_id": tid, "field": "human_action", "value": ""}),
                ("back_next_step", "backlog_update_task", {"task_id": tid, "next_step": "Back from review: tighten the copy"}),
                ("in_review_without_human_action", "backlog_update_task", {"task_id": tid, "field": "status", "value": "in-review"}),
                ("claim_status", "backlog_claim", {"action": "status", "task_id": tid}),
                ("complete", "backlog_complete_task", {"task_id": tid, "done": "Signed off in review queue"}),
                ("complete_again", "backlog_complete_task", {"task_id": tid, "done": "Signed off in review queue"}),
            )
            for name, tool, args in steps:
                replies[name] = await call(client, tool, args)
    return tid, replies


def emit_ts(const: str, label: str, meta: dict, replies: dict) -> str:
    body = json.dumps({"meta": meta, "replies": replies}, indent=2, ensure_ascii=False)
    return ("// User intent: real Taskmaster MCP replies, captured by scripts/capture_fixtures.py, so the parsers are tested\n"
            f"// against what the server actually says ({label} store). GENERATED: rerun the script to refresh.\n\n"
            f"export const {const} = {body} as const\n")


async def run(server: Path, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    log = Path(tempfile.gettempdir()) / "tm-tui-capture.server.log"
    today = date.today().isoformat()
    for const, label, root, pseudonymise in STORES:
        if (root / ".taskmaster").exists():
            replies = await capture_real(server, root, log, pseudonymise)
            meta = {"store": label, "captured": today, "redacted": True, "idsPseudonymised": pseudonymise}
        else:
            replies, meta = {}, {"store": label, "captured": today, "skipped": f"no .taskmaster under the {label} root"}
        (out / f"{label}.ts").write_text(emit_ts(const, label, meta, replies), encoding="utf-8", newline="\n")
        print(f"wrote {label}.ts ({len(replies)} replies)")
    tid, replies = await capture_scratch(server, log)
    meta = {"store": "scratch", "captured": today, "redacted": False, "taskId": tid}
    (out / "scratch.ts").write_text(emit_ts("SCRATCH", "scratch", meta, replies), encoding="utf-8", newline="\n")
    print(f"wrote scratch.ts ({len(replies)} replies, task {tid}); server log: {log}")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--server", type=Path, default=DEFAULT_SERVER)
    parser.add_argument("--out", type=Path, default=OUT)
    ns = parser.parse_args()
    if not ns.server.exists():
        raise SystemExit(f"no Taskmaster server at {ns.server}: pass --server")
    return asyncio.run(run(ns.server, ns.out))


if __name__ == "__main__":
    sys.exit(main())
