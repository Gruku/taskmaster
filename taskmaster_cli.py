# /// script
# requires-python = ">=3.11"
# dependencies = ["anyio", "fastmcp>=3.4,<4", "h11>=0.16", "httpx", "mcp>=1.28,<2", "pydantic>=2", "pyyaml", "starlette>=1.0.1,<2", "uvicorn>=0.35,<1"]
# ///
"""User intent: one packaged front door for Taskmaster's operator CLIs, so an installed
plugin runs them in its own uv environment instead of a `python` that cannot import
the package or its dependencies.

    uv run <plugin>/taskmaster_cli.py cutover --root <project> --dry-run
    uv run <plugin>/taskmaster_cli.py git status          (cwd = the project)
    uv run <plugin>/taskmaster_cli.py git-hook pre-commit  (inside a user's Git hook)
    uv run <plugin>/taskmaster_cli.py coordinator {status,stop} [--root <project>]
    uv run <plugin>/taskmaster_cli.py serve --root <project> [--host 127.0.0.1] [--port 7470]
    uv run <plugin>/taskmaster_cli.py remote set-key --url <team server MCP URL>   (key on stdin)

Each command runs its module exactly as `python -m <module>` would. The server module
is never imported: importing it starts the viewer. `serve` is the
exception: it imports the server module to host it, but only after its settings are validated,
so `serve --help` stays import-free. Keep this file's name out of the
cutover's launcher inventory (taskmaster/native/quiesce.py): the scan excludes only its
own pid, and the `uv run` parent carries this command line too.
"""
import runpy
import sys
from pathlib import Path

COMMANDS = {
    "cutover": "taskmaster.native.cutover",
    "git": "taskmaster.coordinator.git_cli",
    "git-hook": "taskmaster.coordinator.git_hook",
    "coordinator": "taskmaster.coordinator.control_cli",
    "serve": "taskmaster.serve.cli",
    "remote": "taskmaster.remote.cli",
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in COMMANDS:
        print(f"usage: taskmaster_cli.py {{{','.join(COMMANDS)}}} [args...]", file=sys.stderr)
        return 2
    command, rest = argv[0], argv[1:]
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.argv = [f"taskmaster_cli.py {command}", *rest]
    runpy.run_module(COMMANDS[command], run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
