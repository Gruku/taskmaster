# User intent: shared fixtures for the team-server-014 migration tests: a disposable native project in a Git repository
# that tracks .taskmaster/ the way a solo project does, a root fingerprint that proves "a refusal writes nothing", and
# small Git helpers. Never touches a real repository.
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

from taskmaster.coordinator.service import Coordinator
from native_git_helpers import git, init_repo

PLUGIN = Path(__file__).resolve().parents[1]
URL = "https://tm.example.com/mcp"
TASK = "test-epic-001"
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
# Created or changed by a coordinator or the lease and not part of what "unchanged" means for a refusal.
VOLATILE = (".git/", ".taskmaster/local/serve/", ".taskmaster/local/coordinator/", ".taskmaster/local/store.db")


def run(cwd, *args, check=True, stdin=None):
    done = subprocess.run(["git", *args], cwd=cwd, env=CLEAN_ENV, input=stdin, capture_output=True, timeout=120, **FLAGS)
    if check and done.returncode:
        raise AssertionError(f"git {args} -> {done.returncode}: {done.stderr.decode(errors='replace')}")
    return done


def g(cwd, *args, check=True) -> str:
    return run(cwd, *args, check=check).stdout.decode("utf-8", "replace").strip()


def envelope(owner, key, operation="task.patch", arguments=None, scope="migration-tests"):
    return {"protocol": 2, "store_id": owner.identity["store_id"], "caller_scope": scope, "request_id": key,
            "operation": operation, "arguments": arguments or {"id": TASK, "set": {"title": key}},
            "expected_revisions": []}


def store_events(root) -> int:
    try:
        with contextlib.closing(sqlite3.connect(Path(root) / ".taskmaster" / "local" / "store.db")) as connection:
            return int(connection.execute("SELECT COALESCE(MAX(seq),0) FROM domain_events").fetchone()[0])
    except sqlite3.Error:
        return None


def fingerprint(root) -> dict:
    """Every file under the root except the volatile ones, by sha256, plus the store's event count; refs and the index."""
    root = Path(root)
    files = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_file() and not rel.startswith(VOLATILE):
            files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"files": files, "events": store_events(root) if (root / ".taskmaster/local/store.db").exists() else None,
            "refs": g(root, "for-each-ref", "--format=%(refname) %(objectname)"),
            "index": hashlib.sha256((root / ".git" / "index").read_bytes()).hexdigest()
            if (root / ".git" / "index").exists() else None,
            "head": g(root, "rev-parse", "HEAD", check=False)}


def make_source(root, *, with_document=True, tracked_extras=True) -> Path:
    """A native project in a repo whose branch `main` tracks `.taskmaster/` (not `local/`), clean and committed."""
    root = Path(root).resolve()
    init_repo(root)
    if with_document:
        from test_serve_state_export_cycle import _declare, _import
        _declare(root, {"spec": "docs/specs/x.md"})
        with Coordinator(root) as owner:
            _import(owner, "doc-spec", "spec", "docs/specs/x.md", "The spec.\n")
    if tracked_extras:
        (root / ".taskmaster" / "harvests").mkdir(parents=True, exist_ok=True)
        (root / ".taskmaster" / "harvests" / "x.md").write_text("harvest\n", encoding="utf-8")
        (root / ".taskmaster" / "local" / "viewer.json").write_text("{}\n", encoding="utf-8")
    run(root, "add", "-A")
    run(root, "commit", "-q", "-m", "backlog", check=False)
    return root


def manifest_of(path) -> dict:
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read("manifest.json"))


def cli(*args, cwd=None, timeout=300):
    """`python -I taskmaster_cli.py <args>` in a fresh process (one root per process, see load_server_module)."""
    return subprocess.run([sys.executable, "-I", str(PLUGIN / "taskmaster_cli.py"), *args], capture_output=True, text=True,
                          timeout=timeout, cwd=cwd or PLUGIN, env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"})
