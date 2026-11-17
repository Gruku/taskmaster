# User intent: the mods' one door to the team server: a short-lived process that reads a batch of tools through the
# same client, cache and key as the hooks, and always answers one JSON object on stdout (team-server-007 D14).
"""`tm_read.py tools|mode --root R [--fresh]`: JSON out, exit 0 whatever happened."""
from __future__ import annotations

import argparse
import json
import sys
import time

STDOUT_CAP = 4 * 1024 * 1024 - 64 * 1024       # under the mod runtime's 4 MiB read
TOOLS_DEADLINE_SECONDS = 10.0


def _mode(root) -> str:
    from taskmaster import server_marker
    try:
        if server_marker.server_mode(root) is not None:
            return "server"
    except server_marker.ServerConfigError:
        return "server"            # present but unreadable: still a server clone, never solo
    from pathlib import Path
    return "solo" if (Path(root) / ".taskmaster").is_dir() else "none"


def run(argv, stdin, started: float) -> dict:
    parser = argparse.ArgumentParser(prog="tm_read.py")
    parser.add_argument("command", choices=("tools", "mode"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "mode":
        return {"mode": _mode(args.root)}
    from .client import ReadUnavailable
    from . import hookside
    if _mode(args.root) != "server":
        return {"error": {"kind": "config", "message": "not a server-mode project"}}
    try:
        payload = json.loads(stdin.read() or "null")
    except ValueError:
        return {"error": {"kind": "bad-request", "message": "stdin is not JSON"}}
    calls = payload.get("calls") if isinstance(payload, dict) else payload
    if not isinstance(calls, list) or not calls:
        return {"error": {"kind": "bad-request", "message": "stdin must hold a list of {tool, args} calls"}}
    try:
        session = hookside.connect(args.root)
        result = session.client.read("tools", {"calls": calls}, fresh=args.fresh,
                                     deadline=time.monotonic() + TOOLS_DEADLINE_SECONDS)
    except ReadUnavailable as failure:
        return {"error": {"kind": failure.kind, "message": failure.message}}
    return {"v": 1, "view": "tools", "revision": result.revision, "store_id": result.store_id, "data": result.data,
            "source": result.source, "age_s": round(result.age_s, 3)}


def main(argv=None, stdin=None, stdout=None) -> int:
    started = time.monotonic()
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    try:
        out = run(sys.argv[1:] if argv is None else argv, stdin, started)
    except SystemExit:
        out = {"error": {"kind": "bad-request", "message": "usage: tm_read.py tools|mode --root R [--fresh]"}}
    except Exception as exc:  # noqa: BLE001 - the mod reads stdout; never a traceback
        out = {"error": {"kind": "bad-response", "message": f"{type(exc).__name__}: {exc}"}}
    out["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    text = json.dumps(out, ensure_ascii=False)
    if len(text.encode("utf-8")) > STDOUT_CAP:
        text = json.dumps({"error": {"kind": "bad-response", "message": "the reply is too large for the mod"},
                           "elapsed_ms": out["elapsed_ms"]})
    stdout.write(text)
    stdout.flush()
    return 0
