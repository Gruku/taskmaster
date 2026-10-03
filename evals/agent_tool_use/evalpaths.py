# User intent: one place that decides where the agent tool-use eval keeps its scratch data
# (seeded backlog, per-run copies, logs, rendered ground truth) — always outside the repo, so a
# test agent told to stay out of the repo cannot stumble on the answers.
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parents[1]
DEFAULT_SERVER = REPO_ROOT / "backlog_server.py"
MARKER = ".tm-agent-tool-use-eval"
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


def home(explicit: str | None = None) -> Path:
    """The eval home: --home, else TM_EVAL_HOME, else a fixed folder under the temp dir."""
    raw = explicit or os.environ.get("TM_EVAL_HOME") or str(Path(tempfile.gettempdir()) / "tm-agent-tool-use-eval")
    path = Path(raw).resolve()
    if path == REPO_ROOT or REPO_ROOT in path.parents:
        raise SystemExit(f"eval home must be outside the repo, got {path}")
    return path


def seed_dir(h: Path) -> Path:
    return h / "seed"


def run_dir(h: Path, run: str) -> Path:
    return h / "runs" / run


def log_path(h: Path, run: str) -> Path:
    return h / "logs" / f"{run}.jsonl"


def check_run_id(run: str) -> str:
    if not RUN_ID.fullmatch(run or ""):
        raise SystemExit("run id must be 1-64 chars of letters, digits, '_', '.', '-' (starting alphanumeric)")
    return run
