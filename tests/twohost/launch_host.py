# User intent: a test-only front door for the serve daemon that can move the claim clock, inject one named fault, and exit with its parent so a killed test run never leaves orphan daemons (015 A4). Never packaged, refuses to run outside a harness run.
"""Usage: launch_host.py --parent-pid N --base DIR [--clock-file F] [--keeper-seconds S] [--harness-fault TOOL] [--build VERSION:DIGEST] -- <serve args>"""
from __future__ import annotations

import argparse
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

WATCHDOG_INTERVAL = 0.25


class LaunchRefused(SystemExit):
    def __init__(self, message: str):
        super().__init__(f"launch_host: {message}")
        self.code = 2


SENTINEL = ".twohost-run"          # written by Host.start into the run's base; a base without it is not a harness run


def serve_root(serve_args: list[str]) -> Path | None:
    for index, value in enumerate(serve_args):
        if value == "--root" and index + 1 < len(serve_args):
            return Path(serve_args[index + 1])
        if value.startswith("--root="):
            return Path(value.split("=", 1)[1])
    return None


def check_launch(environ, serve_args: list[str], base: Path) -> Path:
    """Refuse unless the harness opted in and the served root is under the run's base directory."""
    if environ.get("TASKMASTER_TWOHOST") != "1":
        raise LaunchRefused("TASKMASTER_TWOHOST=1 is not set; this launcher is for the two-host harness only")
    if not (Path(base) / SENTINEL).is_file():
        raise LaunchRefused(f"{base} is not a harness run base (no {SENTINEL})")
    if sum(1 for value in serve_args if value == "--root" or value.startswith("--root=")) > 1:
        raise LaunchRefused("more than one --root: serve honours the last, this guard checks the first")
    root = serve_root(serve_args)
    if root is None:
        raise LaunchRefused("no --root given")
    resolved, parent = root.resolve(), Path(base).resolve()
    if parent != resolved and parent not in resolved.parents:
        raise LaunchRefused(f"--root {resolved} is not under the run's base directory {parent}")
    return resolved


def start_parent_watchdog(parent_pid: int, *, interval: float = WATCHDOG_INTERVAL, on_gone=None) -> threading.Thread:
    """Call `on_gone()` (default: os._exit(0)) within about one interval of the parent pid going away."""
    try:
        from .procid import alive
    except ImportError:               # run as a script: tests/twohost is on sys.path
        from procid import alive
    exit_now = on_gone or (lambda: os._exit(0))

    idle = threading.Event()          # never set: wait() is the pause between liveness checks

    def watch():
        while alive(parent_pid):
            idle.wait(interval)
        exit_now()
    thread = threading.Thread(target=watch, name="twohost-parent-watchdog", daemon=True)
    thread.start()
    return thread


def clock_offset(clock_file: Path) -> timedelta:
    try:
        return timedelta(seconds=float(clock_file.read_text(encoding="utf-8").strip() or 0))
    except (OSError, ValueError):
        return timedelta(0)


def patch_claim_clock(clock_file: Path) -> None:
    """Claims read time through `claims.now_utc`; the keeper binds it as a default argument at class definition,
    so the patch lands before `claim_keeper` is imported (Step 0 finding)."""
    from taskmaster.native import claims
    claims.now_utc = lambda: datetime.now(timezone.utc) + clock_offset(clock_file)


def patch_keeper_seconds(seconds: float) -> None:
    try:
        from taskmaster.serve import claim_keeper
    except ImportError:
        return
    if hasattr(claim_keeper, "KEEP_SECONDS"):
        claim_keeper.KEEP_SECONDS = seconds


def patch_build(version: str, digest: str) -> None:
    """Report an older (or any other) build to the coordinator handshake, as an unpatched older release would (S12)."""
    from taskmaster.coordinator import protocol
    protocol._BUILD = (version, digest)


def install_fault_tool(tool: str, root: Path) -> None:
    """Register `tool` so that calling it raises an OSError carrying a host path (the sanitizer's reproducible case)."""
    backlog_server = sys.modules["taskmaster.backlog_server"]     # present once the daemon has started

    def fault() -> str:
        raise OSError(f"[Errno 13] Permission denied: '{root / '.taskmaster' / 'local' / 'secret.db'}'")
    fault.__name__ = tool
    backlog_server.mcp._inner.tool(fault, name=tool)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        raise LaunchRefused("usage: launch_host.py [options] -- <serve args>")
    split = argv.index("--")
    options, serve_args = argv[:split], argv[split + 1:]
    parser = argparse.ArgumentParser(prog="launch_host")
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--clock-file", type=Path)
    parser.add_argument("--keeper-seconds", type=float)
    parser.add_argument("--harness-fault")
    parser.add_argument("--build", help="VERSION:DIGEST this process reports as its build (S12's older daemon)")
    args = parser.parse_args(options)
    root = check_launch(os.environ, serve_args, args.base)
    start_parent_watchdog(args.parent_pid)
    if args.clock_file is not None:
        patch_claim_clock(args.clock_file)
    if args.build:
        patch_build(*args.build.split(":", 1))
    if args.keeper_seconds is not None:
        patch_keeper_seconds(args.keeper_seconds)
    if args.harness_fault:
        # serve points the server module at its root while starting, so the tool goes in right after start()
        from taskmaster.serve import daemon
        original = daemon.ServeDaemon.start

        def start_then_inject(self, *a, **k):
            result = original(self, *a, **k)
            install_fault_tool(args.harness_fault, root)
            return result
        daemon.ServeDaemon.start = start_then_inject
    from taskmaster.serve import cli
    return cli.main(serve_args)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
