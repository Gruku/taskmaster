"""Run a local check in its own process group, with bounded log inspection.

On Windows, use a windowless console and a separate process group. A detached
console-less parent can cause console children to allocate visible windows.
This only launches an explicitly supplied command; it never kills a process.
"""
import argparse
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in args.name):
        parser.error("name must contain only lowercase letters, digits, dash or underscore")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("command is required")
    logs = Path(__file__).resolve().parents[1] / "test-results"
    logs.mkdir(exist_ok=True)
    kwargs = ({"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
              if hasattr(subprocess, "CREATE_NO_WINDOW") else {"start_new_session": True})
    with (logs / f"{args.name}.out.log").open("xb") as out, (logs / f"{args.name}.err.log").open("xb") as err:
        child = subprocess.Popen(command, cwd=args.cwd.resolve(), stdin=subprocess.DEVNULL,
                                 stdout=out, stderr=err, **kwargs)
    print(f"Started PID {child.pid}; logs test-results/{args.name}.{{out,err}}.log")


if __name__ == "__main__":
    main()
