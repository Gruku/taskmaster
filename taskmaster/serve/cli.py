# User intent: the operator's commands: serve --root, serve token for the operator token, add-user, rotate-user, revoke-user and list-users for each teammate's own API key, and serve export for the backup branch (team-server-013).
"""Run Taskmaster's tools for a team over streamable HTTP.

  taskmaster_cli.py serve --root <project> [--host 127.0.0.1] [--port 7470] [--path /mcp]
                          [--allowed-host tm.example.com] [--insecure-bind]
                          [--log-level info] [--session-idle-seconds 1800]
                          [--max-body-bytes 2097152] [--header-timeout 10] [--body-timeout 30]
                          [--linger-seconds 2] [--linger-bytes 65536]
                          [--max-connections 128] [--rate-limit 240] [--rate-burst 120] [--max-inflight 32]
                          [--viewer-port 7471 | --no-viewer] [--viewer-origin https://viewer.example]...
  taskmaster_cli.py serve --local --root <project> --result-file FILE [--idle-exit 600] [--plugin-dir DIR]
  taskmaster_cli.py serve token --root <project> [--rotate]
  taskmaster_cli.py serve add-user    <handle> --root <project> [--name "Ann Lee"] [--email a@b.c]...
                                      [--role member|operator] [--key-file PATH]
  taskmaster_cli.py serve rotate-user <handle> --root <project> [--key-file PATH]
  taskmaster_cli.py serve revoke-user <handle> --root <project>
  taskmaster_cli.py serve list-users  --root <project> [--json]
  taskmaster_cli.py serve set-user    <handle> --owns-pre-team-history --root <project>
  taskmaster_cli.py serve export status|run|restore-documents --root <project> ...
  taskmaster_cli.py serve marker write --root <project> --url https://tm.example.com/mcp [--rotate-host-id]
  taskmaster_cli.py serve marker show --root <project> [--json]
  taskmaster_cli.py serve marker clear-latch --root <project>
  taskmaster_cli.py serve init pack --root <project> --url https://tm.example.com/mcp --out FILE [--branch B]... [--json]
  taskmaster_cli.py serve init pack --undo --not-pushed --root <project> [--json]
  taskmaster_cli.py serve init --root <host clone> --from FILE [--export-branch B] [--export-push REMOTE | --no-export] [--json]

The local profile (--local, local-server-002) is the one hidden server per project that the plugin's stdio bridge starts
and every Claude Code session on that project shares: loopback and an OS-chosen port, the operator token only, its own
guarded viewer on the project's hashed 6800-6899 port (--no-viewer turns it off; `backlog_open_viewer` opens it with a
one-time link), no state export, no add-user, and an exit after --idle-exit seconds with no bridge connected. Its launch outcome
is written to --result-file (ready, lease_held, store_owned_busy, refused or error), because stdout is not read.

State export (team-server-013): the daemon commits the store's export to the taskmaster-state branch every
--export-interval seconds and, with --export-push REMOTE, pushes it fast-forward to a dedicated backup repo.

Every request needs `Authorization: Bearer <key>`. Each teammate has their own key from add-user; it is
printed once (stdout) and never stored: the server keeps only its SHA-256, owner-only, in
<project>/.taskmaster/local/serve/principals.json. Lost keys are replaced with rotate-user. The user
commands work whether or not the daemon runs and take effect on its next request. The operator
bootstrap token (<project>/.taskmaster/local/serve/operator.token, created on first start) works only
on the host. Give your own laptop's agent a key from add-user (--role operator). The default bind is
loopback; put a TLS reverse proxy (Caddy) in front of it. A non-loopback bind is refused unless
--insecure-bind is given.

Exit codes: 0 stopped cleanly; 2 refused (bad settings, non-loopback bind, unsafe files, not an
activated native store); 69 unavailable (port in use, another coordinator owns the store, the HTTP
server failed to start); 75 the coordinator was lost (a supervisor should restart the daemon).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import (DEFAULT_BODY_TIMEOUT, DEFAULT_HEADER_TIMEOUT, DEFAULT_HOST, DEFAULT_LINGER_BYTES,
                     DEFAULT_LINGER_SECONDS, DEFAULT_MAX_BODY_BYTES,
                     DEFAULT_MAX_CONNECTIONS, DEFAULT_MAX_INFLIGHT, DEFAULT_PATH, DEFAULT_PORT, DEFAULT_RATE_BURST,
                     DEFAULT_RATE_PER_MINUTE, DEFAULT_SESSION_IDLE_SECONDS, DEFAULT_VIEWER_PORT, EXIT_REFUSED,
                     EXIT_UNAVAILABLE, DEFAULT_LOCAL_IDLE_EXIT, PROFILE_LOCAL, ServeConfig, ServeLeaseHeld, ServeRefused,
                     ServeUnavailable, StoreOwnedBusy)

# The local profile serves ~10 sessions plus the mods from one process; a bucket is one bridge (serve/local.limit_key).
LOCAL_RATE_PER_MINUTE = 1200
LOCAL_RATE_BURST = 400


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["token"]:
        return token_main(argv[1:])
    if argv[:1] == ["export"]:
        return export_main(argv[1:])
    if argv[:1] == ["marker"]:
        return marker_main(argv[1:])
    if argv[:1] == ["init"]:
        return init_main(argv[1:])
    commands = {"add-user": add_user_main, "rotate-user": rotate_user_main, "revoke-user": revoke_user_main,
                "list-users": list_users_main, "set-user": set_user_main}
    if argv[:1] and argv[0] in commands:
        return commands[argv[0]](argv[1:])
    return run_main(argv)


def build_parser() -> argparse.ArgumentParser:
    """The `serve` argument parser, on its own so the shipped systemd unit's ExecStart can be checked against it."""
    parser = argparse.ArgumentParser(prog="taskmaster serve", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, required=True, help="the native project to serve")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"IP address or localhost (default {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"default {DEFAULT_PORT}")
    parser.add_argument("--path", default=DEFAULT_PATH, help=f"MCP endpoint path (default {DEFAULT_PATH})")
    parser.add_argument("--allowed-host", action="append", default=[],
                        help="a Host header value to accept besides loopback (repeatable)")
    parser.add_argument("--insecure-bind", action="store_true",
                        help="allow a non-loopback bind of plaintext HTTP (the risk is yours)")
    parser.add_argument("--log-level", choices=("debug", "info", "warning"), default="info")
    parser.add_argument("--session-idle-seconds", type=float, default=DEFAULT_SESSION_IDLE_SECONDS,
                        help="drop an MCP session idle this long (default 1800)")
    limits = parser.add_argument_group("hardening")
    limits.add_argument("--max-body-bytes", type=int, default=DEFAULT_MAX_BODY_BYTES,
                        help=f"refuse request bodies over this size with 413 (default {DEFAULT_MAX_BODY_BYTES})")
    limits.add_argument("--header-timeout", type=float, default=DEFAULT_HEADER_TIMEOUT,
                        help="close a connection whose request headers take longer (seconds, default 10)")
    limits.add_argument("--body-timeout", type=float, default=DEFAULT_BODY_TIMEOUT,
                        help="answer 408 when a request body takes longer (seconds, default 30)")
    limits.add_argument("--linger-seconds", type=float, default=DEFAULT_LINGER_SECONDS,
                        help="after an early answer, drain the unread body this long before closing (default 2)")
    limits.add_argument("--linger-bytes", type=int, default=DEFAULT_LINGER_BYTES,
                        help=f"...or until this many bytes are discarded (default {DEFAULT_LINGER_BYTES})")
    limits.add_argument("--max-connections", type=int, default=DEFAULT_MAX_CONNECTIONS,
                        help=f"open connections before 503 (default {DEFAULT_MAX_CONNECTIONS})")
    limits.add_argument("--rate-limit", dest="rate_per_minute", type=int, default=DEFAULT_RATE_PER_MINUTE,
                        help=f"requests per minute per key (default {DEFAULT_RATE_PER_MINUTE})")
    limits.add_argument("--rate-burst", type=int, default=DEFAULT_RATE_BURST,
                        help=f"requests a key may burst (default {DEFAULT_RATE_BURST})")
    limits.add_argument("--max-inflight", type=int, default=DEFAULT_MAX_INFLIGHT,
                        help=f"concurrent requests per key, below --max-connections (default {DEFAULT_MAX_INFLIGHT})")
    viewer = parser.add_mutually_exclusive_group()
    viewer.add_argument("--viewer-port", type=int, default=DEFAULT_VIEWER_PORT,
                        help=f"the login-protected viewer on 127.0.0.1 (default {DEFAULT_VIEWER_PORT})")
    viewer.add_argument("--no-viewer", action="store_true", help="serve MCP only")
    parser.add_argument("--viewer-origin", action="append", default=[],
                        help="a public https origin the viewer is served on through the proxy (repeatable)")
    export = parser.add_argument_group("state export (backup and history)")
    export.add_argument("--export-interval", type=int, default=None,
                        help="commit the export to --export-branch every N seconds (60..86400; 0 = off). Default "
                             "900 in a host clone's main checkout, off on a linked worktree or outside Git")
    export.add_argument("--export-branch", default="taskmaster-state", help="default taskmaster-state")
    export.add_argument("--export-push", default=None, metavar="REMOTE",
                        help="push the branch fast-forward to this configured Git remote (a dedicated backup repo)")
    local = parser.add_argument_group("local profile")
    local.add_argument("--local", action="store_true",
                       help="run as the project's shared local server (loopback, operator token, guarded viewer on the project's hashed port, no export)")
    local.add_argument("--result-file", type=Path, default=None, metavar="FILE",
                       help="where the launch outcome is written (owner-only JSON); the bridge reads only this")
    local.add_argument("--idle-exit", type=_idle_seconds, default=DEFAULT_LOCAL_IDLE_EXIT, metavar="SECONDS",
                       help="exit this long after the last bridge disconnects (default 600; 0 = never)")
    local.add_argument("--plugin-dir", type=Path, default=None, metavar="DIR",
                       help="the plugin directory this server runs from (recorded in the discovery file)")
    upgrade = parser.add_argument_group("upgrade")
    upgrade.add_argument("--force-upgrade", action="store_true",
                         help="replace a running older daemon of this root: ask it to retire (host-local, proved "
                              "with the operator token), wait for it to drain, then start this build")
    upgrade.add_argument("--upgrade-timeout", type=float, default=120.0, metavar="SECONDS",
                         help="how long --force-upgrade waits for the old daemon to drain (default 120)")
    return parser


def _idle_seconds(text: str):
    """A non-number is kept as text so the local profile's own validation refuses it and the launch result file says so
    (argparse would exit 2 with no result, and the bridge would wait out its timeout)."""
    try:
        return float(text)
    except ValueError:
        return text


def _team_flags_given(args) -> list[str]:
    """Team-profile options present on a --local command line (local-server-002 M6): refused, not silently ignored."""
    given = []
    checks = (("--host", args.host != DEFAULT_HOST), ("--port", args.port != DEFAULT_PORT),
              ("--allowed-host", bool(args.allowed_host)), ("--insecure-bind", args.insecure_bind),
              ("--session-idle-seconds", args.session_idle_seconds != DEFAULT_SESSION_IDLE_SECONDS),
              ("--viewer-port", args.viewer_port != DEFAULT_VIEWER_PORT), ("--viewer-origin", bool(args.viewer_origin)),
              ("--export-interval", args.export_interval is not None), ("--export-branch", args.export_branch != "taskmaster-state"),
              ("--export-push", args.export_push is not None), ("--force-upgrade", args.force_upgrade),
              ("--upgrade-timeout", args.upgrade_timeout != 120.0))
    return [name for name, present in checks if present]


def _local_flags_given(args) -> list[str]:
    """Local-profile options present on a team command line: refused, not silently ignored (review 004 m6)."""
    checks = (("--idle-exit", args.idle_exit != DEFAULT_LOCAL_IDLE_EXIT), ("--result-file", args.result_file is not None),
              ("--plugin-dir", args.plugin_dir is not None))
    return [name for name, present in checks if present]


def _local_config(args) -> ServeConfig:
    """The local profile's settings: loopback, an OS-chosen port, the guarded viewer on the project's hashed port (unless
    --no-viewer), no export, no session idle timeout."""
    return ServeConfig(
        root=args.root, host=DEFAULT_HOST, port=0, path=args.path, log_level=args.log_level, profile=PROFILE_LOCAL,
        max_body_bytes=args.max_body_bytes, header_timeout=args.header_timeout, body_timeout=args.body_timeout,
        linger_seconds=args.linger_seconds, linger_bytes=args.linger_bytes, max_connections=args.max_connections,
        rate_per_minute=LOCAL_RATE_PER_MINUTE if args.rate_per_minute == DEFAULT_RATE_PER_MINUTE else args.rate_per_minute,
        rate_burst=LOCAL_RATE_BURST if args.rate_burst == DEFAULT_RATE_BURST else args.rate_burst,
        max_inflight=args.max_inflight, viewer_port=None, viewer_band=not args.no_viewer, export_interval=0,
        idle_exit_seconds=args.idle_exit, result_file=args.result_file, plugin_dir=args.plugin_dir)


def _local_result(args, status: str, reason: str) -> None:
    """Record a launch outcome that the daemon itself could not (it never got as far as ready). Best effort."""
    if args.result_file is None:
        return
    from taskmaster import localserve
    try:
        args.result_file.parent.mkdir(parents=True, exist_ok=True)
        localserve.write_result(args.result_file, status, reason)
    except OSError:
        pass


def run_main(argv) -> int:
    args = build_parser().parse_args(argv)
    from .daemon import ServeDaemon
    local = args.local
    if local:
        team_flags = _team_flags_given(args)
        if team_flags:
            reason = f"--local takes no team option: {', '.join(team_flags)}"
            print(f"taskmaster serve: {reason}", file=sys.stderr)
            _local_result(args, "refused", reason)
            return EXIT_REFUSED
        config = _local_config(args)
    else:
        local_flags = _local_flags_given(args)
        if local_flags:
            print(f"taskmaster serve: {', '.join(local_flags)} only apply with --local", file=sys.stderr)
            return EXIT_REFUSED
        config = ServeConfig(force_upgrade=args.force_upgrade, upgrade_timeout=args.upgrade_timeout,
            root=args.root, host=args.host, port=args.port, path=args.path,
            allowed_hosts=tuple(args.allowed_host), insecure_bind=args.insecure_bind,
            log_level=args.log_level, session_idle_seconds=args.session_idle_seconds,
            max_body_bytes=args.max_body_bytes, header_timeout=args.header_timeout, body_timeout=args.body_timeout,
            linger_seconds=args.linger_seconds, linger_bytes=args.linger_bytes,
            max_connections=args.max_connections, rate_per_minute=args.rate_per_minute, rate_burst=args.rate_burst,
            max_inflight=args.max_inflight, viewer_port=None if args.no_viewer else args.viewer_port,
            viewer_origins=tuple(args.viewer_origin), export_interval=args.export_interval,
            export_branch=args.export_branch, export_push=args.export_push)
    daemon = ServeDaemon(config)
    try:
        daemon.start()
    except ServeRefused as exc:
        print(f"taskmaster serve: {exc}", file=sys.stderr)
        if local:
            status = ("lease_held" if isinstance(exc, ServeLeaseHeld) else "store_owned_busy"
                      if isinstance(exc, StoreOwnedBusy) else "error" if isinstance(exc, ServeUnavailable) else "refused")
            _local_result(args, status, str(exc))
        return exc.exit_code
    except RuntimeError as exc:
        # e.g. fastmcp internals changed: retrying cannot help, so systemd must not restart-loop.
        print(f"taskmaster serve: {exc}", file=sys.stderr)
        if local:
            _local_result(args, "error", str(exc))
        return EXIT_REFUSED
    except OSError as exc:
        print(f"taskmaster serve: cannot start: {exc.strerror or exc}", file=sys.stderr)
        if local:
            _local_result(args, "error", f"cannot start: {exc.strerror or exc}")
        return EXIT_UNAVAILABLE
    code = daemon.serve()
    if local and not daemon.ready.is_set():
        _local_result(args, "error", "the http server failed to start")
    return code


def token_main(argv) -> int:
    parser = argparse.ArgumentParser(prog="taskmaster serve token",
                                     description="Print the operator token, creating it on first use.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--rotate", action="store_true", help="replace the token (takes effect without a restart)")
    args = parser.parse_args(argv)
    from .auth import ensure_operator_token, rotate_operator_token
    try:
        root = args.root.resolve(strict=True)
        if not (root / ".taskmaster/local").is_dir():
            raise ServeRefused(f"{root} is not a taskmaster project with a local store")
        token = rotate_operator_token(root) if args.rotate else ensure_operator_token(root)[0]
    except FileNotFoundError:
        print(f"taskmaster serve: {args.root} does not exist", file=sys.stderr)
        return ServeRefused.exit_code
    except ServeRefused as exc:
        print(f"taskmaster serve: {exc}", file=sys.stderr)
        return exc.exit_code
    print(token)
    return 0


SHOW_ONCE = ("This key is shown once and is not stored. Give it to {handle} privately; "
             "their agent sends it as `Authorization: Bearer <key>`.")


def marker_main(argv) -> int:
    """`serve marker write|show|clear-latch` (team-server-009 section 10). Never opens the store."""
    parser = argparse.ArgumentParser(prog="taskmaster serve marker", allow_abbrev=False,
                                     description="Write, inspect or clear the server marker that fences a clone.")
    commands = parser.add_subparsers(dest="command", required=True)
    write = commands.add_parser("write", help="write .taskmaster-server.json and this clone's latch (host clone)")
    write.add_argument("--url", required=True, help="the server's public MCP URL (https://, or http:// to loopback)")
    write.add_argument("--rotate-host-id", action="store_true", help="replace this host clone's id")
    show = commands.add_parser("show", help="the marker, the latch, the host-id binding and the lease")
    show.add_argument("--json", action="store_true")
    commands.add_parser("clear-latch", help="forget that this clone saw server mode (the marker must be gone)")
    for sub_parser in commands.choices.values():
        sub_parser.add_argument("--root", type=Path, required=True, help="the project root")
    args = parser.parse_args(argv)
    from taskmaster import server_fence, server_marker
    try:
        if args.command == "clear-latch":
            return _marker_clear_latch(args, server_marker)
        if args.command == "show":
            return _marker_show(args, server_marker, server_fence)
        return _marker_write(args, server_marker, server_fence)
    except ServeRefused as exc:
        print(f"taskmaster serve marker: {exc}", file=sys.stderr)
        return exc.exit_code


def init_main(argv) -> int:
    """`serve init pack` on the source laptop, `serve init --from FILE` on the host clone (team-server-014)."""
    if argv[:1] == ["pack"]:
        return _init_pack(argv[1:])
    parser = argparse.ArgumentParser(prog="taskmaster serve init", allow_abbrev=False,
                                     description="Install a pack bundle on this host clone (run `serve init pack` on the "
                                                 "laptop first).")
    parser.add_argument("--root", type=Path, required=True, help="the host clone")
    parser.add_argument("--from", dest="bundle", type=Path, required=True, help="the bundle `serve init pack` wrote")
    parser.add_argument("--export-branch", default="taskmaster-state")
    exporting = parser.add_mutually_exclusive_group()
    exporting.add_argument("--export-push", default=None, metavar="REMOTE")
    exporting.add_argument("--no-export", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    from . import migration
    return _migration_run(args, lambda: migration.init(
        args.root, bundle=args.bundle, export_branch=args.export_branch, export_push=args.export_push,
        no_export=args.no_export), migration)


def _init_pack(argv) -> int:
    parser = argparse.ArgumentParser(prog="taskmaster serve init pack", allow_abbrev=False,
                                     description="Freeze and snapshot this solo project into a bundle, and untrack "
                                                 ".taskmaster/ with a local commit. Nothing is pushed.")
    parser.add_argument("--root", type=Path, required=True, help="the project (the main checkout)")
    parser.add_argument("--url", help="the server's public MCP URL")
    parser.add_argument("--out", type=Path, help="where to write the bundle (outside the repository)")
    parser.add_argument("--branch", action="append", default=[], help="another local branch to untrack (repeatable)")
    parser.add_argument("--undo", action="store_true", help="undo a pack that was never pushed")
    parser.add_argument("--not-pushed", action="store_true", help="your statement that no pack commit has been pushed")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    from . import migration
    if args.undo:
        return _migration_run(args, lambda: migration.undo(args.root, not_pushed=args.not_pushed), migration)
    if not args.url or not args.out:
        parser.error("--url and --out are required (or --undo --not-pushed)")
    return _migration_run(args, lambda: migration.pack(args.root, url=args.url, out=args.out, branches=args.branch),
                          migration)


def _migration_run(args, action, migration) -> int:
    try:
        report = action()
    except ServeRefused as exc:
        _say(f"taskmaster serve init: {exc}")
        return exc.exit_code
    except (migration.untrack.UntrackError, migration.se.ExportError, migration.se.ExportRefused) as exc:
        _say(f"taskmaster serve init: {exc}")
        return 1
    except OSError as exc:
        _say(f"taskmaster serve init: {exc.strerror or exc}")
        return EXIT_REFUSED
    print(json.dumps(report, indent=1, sort_keys=True) if args.json else migration.render_report(report), flush=True)
    return 0


def _marker_write(args, server_marker, server_fence) -> int:
    root = _project(args.root)
    problem = server_marker.url_problem(args.url)
    if problem:
        raise ServeRefused(problem)
    host_id = server_fence.ensure_host_id(root, rotate=args.rotate_host_id)
    path = server_marker.publish_marker(root, args.url, host_id)
    print(f"wrote {path} (host_id {host_id})")
    print(f"commit it so teammates' clones are fenced:  git add {server_marker.MARKER_NAME}  &&  "
          'git commit -m "chore: mark this project as served by a Taskmaster server"')
    return 0


def _marker_show(args, server_marker, server_fence) -> int:
    import subprocess
    root = args.root.resolve()
    report = {"root": str(root), "marker": None, "marker_error": None, "latch": None, "latch_error": None}
    for name, read in (("marker", server_marker.read_marker), ("latch", server_marker.read_latch)):
        try:
            found = read(root)
            report[name] = None if found is None else {"url": found.url, "host_id": found.host_id}
        except server_marker.ServerConfigError as exc:
            report[f"{name}_error"] = str(exc)
    mine = server_fence.read_host_id(root)
    present = report["marker"] or report["latch"]
    report["host_id"] = mine
    report["host_id_matches"] = None if not present else mine is not None and mine == present["host_id"]
    report["host_lease_held"] = server_fence.host_lease_held(root)
    try:
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", server_marker.MARKER_NAME], cwd=root,
                                 capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        tracked = None
    report["marker_tracked"] = tracked
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for key, value in report.items():
            print(f"{key}: {value}")
    return 0


def _marker_clear_latch(args, server_marker) -> int:
    from taskmaster import server_fence
    roots = server_fence.fence_roots(args.root.resolve())
    for each in roots:
        marker = server_marker.marker_path(each)
        if marker.exists() or marker.is_symlink():
            raise ServeRefused(f"{server_marker.MARKER_NAME} is still present in {each}; remove it (the project "
                               "has left server mode) before clearing the latch")
    root = roots[-1]                  # the latch lives in the main checkout's .git, also for a linked worktree
    print("latch cleared" if server_marker.clear_latch(root) else "no latch to clear")
    return 0


def _say(text) -> None:
    print(text, file=sys.stderr)


def _project(path: Path) -> Path:
    try:
        root = path.resolve(strict=True)
    except FileNotFoundError:
        raise ServeRefused(f"{path} does not exist") from None
    if not (root / ".taskmaster/local").is_dir():
        raise ServeRefused(f"{root} is not a taskmaster project with a local store")
    return root


def _user_parser(name: str, description: str, *, handle=True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"taskmaster serve {name}", description=description, allow_abbrev=False)
    if handle:
        parser.add_argument("handle")
    parser.add_argument("--root", type=Path, required=True, help="the project the server serves")
    return parser


def _guarded(action) -> int:
    try:
        return action()
    except ServeRefused as exc:
        _say(f"taskmaster serve: {exc}")
        return exc.exit_code


def _hand_over(handle: str, key: str, key_file) -> None:
    if key_file is None:
        print(key, flush=True)
        _say(SHOW_ONCE.format(handle=handle))
    else:
        print(f"key for {handle} written to {key_file} (owner-only)", flush=True)


def _refuse_while_local(root) -> None:
    """add-user is refused for a project a local server is serving (local-server-002 ruling): the local profile accepts
    the operator token only, so a key minted now would belong to no server. Team mode is a separate serve deployment."""
    from taskmaster import localserve
    try:
        serving = localserve.read_discovery(root) is not None
    except PermissionError:
        serving = False
    if serving:
        raise ServeRefused("a local taskmaster server is serving this project; it takes the operator token only and has "
                           "no users. A team server runs as its own deployment (docs/runbooks/team-server-deploy.md)")


def add_user_main(argv) -> int:
    parser = _user_parser("add-user", "Mint an API key for a teammate; it is printed once.")
    parser.add_argument("--name", help="the person's name (default: the handle)")
    parser.add_argument("--email", action="append", default=[], help="repeatable")
    parser.add_argument("--role", choices=("member", "operator"), default="member")
    parser.add_argument("--key-file", type=Path, help="write the key to this new owner-only file instead of stdout")
    args = parser.parse_args(argv)

    def action():
        from . import principals
        root = _project(args.root)
        _refuse_while_local(root)
        principals.check_person(args.handle, args.name or args.handle, list(args.email), args.role)
        from taskmaster import server_fence
        if server_fence.refusal(root) is not None:
            _say("profile check skipped: server mode; the daemon checks on first write")
            check = principals.StoreCheck("skipped", None, None)
        else:
            check = principals.inspect_store(root, args.handle)
        if check.profile == "deleted":
            raise ServeRefused(f"profile {args.handle} was deleted from this backlog; choose another handle")
        if check.profile == "live":
            _say(f"uses the existing profile {args.handle} ({check.name})")
            if args.name or args.email:
                _say("--name/--email are ignored for an existing profile")
        elif check.profile == "unknown":
            _say("could not read the store to check for an existing profile; the server checks on first write")
        if check.team_mode is False:
            _say(f"team mode is off for this project: {args.handle}'s writes still carry the handle, but no profile "
                 "is created and the member upload check is off until team mode is on (team.sync.mode \"manual\"; "
                 "docs/runbooks/team-server-deploy.md section 5)")
        key, record, reinstated = principals.add_user(root, args.handle, name=args.name, emails=args.email,
                                                      role=args.role, key_file=args.key_file)
        if reinstated:
            _say(f"reinstating {args.handle}; earlier keys stay revoked")
        _hand_over(args.handle, key, args.key_file)
        return 0
    return _guarded(action)


def rotate_user_main(argv) -> int:
    parser = _user_parser("rotate-user", "Replace a teammate's key; the old key stops working at once.")
    parser.add_argument("--key-file", type=Path, help="write the key to this new owner-only file instead of stdout")
    args = parser.parse_args(argv)

    def action():
        from . import principals
        root = _project(args.root)
        key, new, old = principals.rotate_user(root, args.handle, key_file=args.key_file)
        _say(f"rotated {args.handle}: {old.key_id} -> {new.key_id}")
        _hand_over(args.handle, key, args.key_file)
        return 0
    return _guarded(action)


def revoke_user_main(argv) -> int:
    args = _user_parser("revoke-user", "Revoke a teammate's key; open sessions end within seconds.").parse_args(argv)

    def action():
        from . import principals
        record = principals.revoke_user(_project(args.root), args.handle)
        print(f"revoked {record.handle} ({record.key_id})")
        return 0
    return _guarded(action)


def set_user_main(argv) -> int:
    parser = _user_parser("set-user", "Set a flag on a teammate (027): who owns the migrated pre-team history.")
    parser.add_argument("--owns-pre-team-history", action="store_true", required=True,
                        help="text at or below the migration's sequence is this person's (shown to everyone else as theirs, "
                             "to them as their own); one person at most")
    args = parser.parse_args(argv)

    def action():
        from . import principals
        principals.set_pre_team_owner(_project(args.root), args.handle)
        print(f"{args.handle} owns the pre-team history")
        return 0
    return _guarded(action)


def list_users_main(argv) -> int:
    parser = _user_parser("list-users", "List every issued key. Hashes are never printed.", handle=False)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    def action():
        from . import principals
        records = principals.list_users(_project(args.root))
        owner = principals.pre_team(_project(args.root))[0]
        if args.json:
            print(json.dumps([{**r.public(), **({"owns_pre_team_history": True} if r.handle == owner and r.active else {})}
                              for r in records], indent=1))
        elif not records:
            print("(no users)")
        else:
            for record in records:
                state = "active" if record.active else f"{record.revoked_reason} {record.revoked}"
                note = " (owns pre-team history)" if record.active and record.handle == owner else ""
                print(f"{record.handle:<32} {record.role:<8} {record.key_id} {record.created} {state}{note}")
        return 0
    return _guarded(action)


EXPORT_USAGE = """taskmaster serve export status            --root <project>
taskmaster serve export run               --root <project> [--export-branch B] [--export-push REMOTE] [--adopt]
taskmaster serve export restore-documents --root <project> --from <commit|branch> [--export-branch B]

status never opens the store: exit 0 healthy, 1 failing or stale, 2 not configured. run and restore-documents
need the daemon stopped (exit 69 while anything owns the store)."""


def export_main(argv) -> int:
    commands = {"status": export_status_main, "run": export_run_main, "restore-documents": export_restore_main}
    if not argv or argv[0] not in commands:
        _say(EXPORT_USAGE)
        return EXIT_REFUSED
    return commands[argv[0]](argv[1:])


def _export_failure(exc) -> int:
    from .state_export import ExportError, ExportRefused
    if isinstance(exc, ExportRefused):
        _say(f"taskmaster serve export: {exc}")
        return EXIT_REFUSED
    if isinstance(exc, ExportError):
        _say(f"taskmaster serve export: {exc}")
        return 1
    if isinstance(exc, OSError):
        _say(f"taskmaster serve export: {exc.strerror or exc}")
        return EXIT_REFUSED
    raise exc


def export_status_main(argv) -> int:
    parser = _user_parser("export status", "Show the state export's health; never opens the store.", handle=False)
    args = parser.parse_args(argv)
    from .state_export import status
    report, code = status(args.root.resolve())
    print(json.dumps(report, indent=1, sort_keys=True))
    return code


def export_run_main(argv) -> int:
    parser = _user_parser("export run", "Export once now, with the daemon stopped.", handle=False)
    parser.add_argument("--export-branch", default="taskmaster-state")
    parser.add_argument("--export-push", default=None, metavar="REMOTE")
    parser.add_argument("--adopt", action="store_true",
                        help="adopt a branch another store exported (a restore); recorded, and allowed once")
    args = parser.parse_args(argv)

    def action():
        from . import state_export as se
        root = _project(args.root)
        settings = se.Settings(root, branch=args.export_branch, interval=se.DEFAULT_INTERVAL, push=args.export_push)
        try:
            se.check_settings(settings)
            with se.offline_owner(root) as owner:
                se.drain(owner)
                outcome = se.export_once(owner, settings, adopt=args.adopt)
        except (se.ExportRefused, se.ExportError, OSError) as exc:
            return _export_failure(exc)
        record = outcome.get("record") or {}
        print(json.dumps({"state": outcome.get("state"), "reason": outcome.get("reason"),
                          "commit": record.get("commit"), "pushed": record.get("pushed"),
                          "push_state": record.get("push_state"), "push_error": record.get("push_error"),
                          "adopted": record.get("adopted")}, indent=1, sort_keys=True))
        ok = outcome.get("state") in ("committed", "unchanged") and record.get("push_state") not in (
            "push_failed", "push_blocked")
        return 0 if ok else 1
    return _guarded(action)


def export_restore_main(argv) -> int:
    parser = _user_parser("export restore-documents", "Re-import a backup's stored documents, as the host "
                          "operator, keeping each document's uploader.", handle=False)
    parser.add_argument("--from", dest="source", required=True, help="a full commit id, or the export branch")
    parser.add_argument("--export-branch", default="taskmaster-state")
    args = parser.parse_args(argv)

    def action():
        from . import state_export as se
        root = _project(args.root)
        try:
            with se.offline_owner(root) as owner:
                report = se.restore_documents(owner, root, args.source, branch=args.export_branch)
                report.update({k: v for k, v in se.restore_branches(owner, root, args.source,
                                                                    branch=args.export_branch).items() if k != "commit"})
        except (se.ExportRefused, se.ExportError, OSError) as exc:
            return _export_failure(exc)
        print(json.dumps(report, indent=1, sort_keys=True))
        return 1 if report["failed"] or report["branches_failed"] else 0
    return _guarded(action)


if __name__ == "__main__":
    sys.exit(main())
