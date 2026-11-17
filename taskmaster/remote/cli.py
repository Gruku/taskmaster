# User intent: let a teammate store the key the operator gave them, owner-only, for exactly one server, without the
# key ever appearing on a command line or in shell history (team-server-007 D9).
"""`taskmaster remote set-key --url <mcp url>`: the key is read from stdin."""
from __future__ import annotations

import argparse
import sys

from .keys import NoKey, write_key
from .locate import server_ref

EXIT_OK, EXIT_REFUSED = 0, 2


def main(argv=None, *, stdin=None, stdout=None, stderr=None, env=None) -> int:
    stdin = stdin or sys.stdin
    stdout, stderr = stdout or sys.stdout, stderr or sys.stderr
    parser = argparse.ArgumentParser(prog="taskmaster remote", description="Client settings for a team server.")
    sub = parser.add_subparsers(dest="command", required=True)
    set_key = sub.add_parser("set-key", help="store your key for a team server (read from stdin)")
    set_key.add_argument("--url", required=True, help="the server's MCP URL, as the clone's marker names it")
    set_key.add_argument("extra", nargs="*", help=argparse.SUPPRESS)
    clone = sub.add_parser("clone-id", help="print this clone's id (created on first use), for X-Taskmaster-Clone")
    clone.add_argument("--root", default=None, help="the clone's checkout (default: the current directory's)")
    args = parser.parse_args(argv)
    if args.command == "clone-id":
        return _clone_id(args.root, stdout, stderr)
    if args.extra:
        print("the key is read from stdin, never from the command line (it would land in shell history); "
              "for example: taskmaster remote set-key --url URL < key.txt", file=stderr)
        return EXIT_REFUSED
    try:
        server = server_ref(args.url)
    except ValueError as exc:
        print(f"refused: {exc}", file=stderr)
        return EXIT_REFUSED
    if getattr(stdin, "isatty", lambda: False)():
        print("paste the key, then press Enter:", file=stderr)
    value = stdin.readline().strip()
    try:
        path = write_key(args.url, value, env)
    except NoKey as exc:
        print(f"refused: {exc}", file=stderr)
        return EXIT_REFUSED
    except (OSError, ValueError) as exc:
        print(f"refused: {exc}", file=stderr)
        return EXIT_REFUSED
    print(f"stored the key for {server.origin} at {path}", file=stdout)
    return EXIT_OK


def _clone_id(root, stdout, stderr) -> int:
    from pathlib import Path
    from . import clone
    try:
        from taskmaster.root import resolve_root
        top = resolve_root(Path(root or Path.cwd())).root
    except Exception as exc:  # noqa: BLE001
        print(f"refused: cannot find this clone's root ({exc})", file=stderr)
        return EXIT_REFUSED
    found = clone.clone_id(top)
    if found is None:
        print(f"refused: {top} has no usable .git directory for a clone id (a submodule, a link, or not owner-only)",
              file=stderr)
        return EXIT_REFUSED
    print(found, file=stdout)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
