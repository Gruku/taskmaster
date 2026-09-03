# /// script
# requires-python = ">=3.11"
# dependencies = ["fastmcp", "pyyaml"]
# ///
"""Entry shim: keeps `.mcp.json` (`uv run ${CLAUDE_PLUGIN_ROOT}/backlog_server.py`)
working unchanged after the core moved into the taskmaster/ package.

Dependencies are declared twice by design: here (uv script mode) and in
pyproject.toml (pip consumers). Keep them in sync.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# `mcp` is re-exported on purpose: tool-enumeration tests and any external
# embedder load this shim and read `mcp` off it.
from taskmaster.backlog_server import main, mcp  # noqa: E402,F401

if __name__ == "__main__":
    main()
