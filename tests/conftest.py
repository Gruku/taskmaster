# plugins/taskmaster/tests/conftest.py
"""Shared pytest fixtures for taskmaster tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

# Make `import backlog_server` and `from taskmaster_v3 import ...` work
# exactly the same way the existing hermetic tests do.
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def pytest_configure(config):
    """Register custom markers (avoids PytestUnknownMarkWarning)."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (real git/bash subprocess)"
    )
    config.addinivalue_line(
        "markers",
        "allow_projection_bypass: disable the store projection write guard",
    )

# Make `import skill_budget_helper` work from tests that live in this directory.
TESTS_ROOT = Path(__file__).resolve().parent
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))


@pytest.fixture(autouse=True)
def _store_isolation():
    """Never let one test's SQLite store, cache, or root resolution leak forward.

    The server now routes every load/mutate through `taskmaster.store`, which
    keeps process-global connections, a dict cache and a resolved root.  Tests
    reuse `tmp_path` names and monkeypatch the root, so both ends need a reset.
    """
    from taskmaster import store  # noqa: PLC0415 — imported after sys.path setup

    store.reset_for_tests()
    try:
        yield
    finally:
        store.reset_for_tests()
        store.close_thread_connection()


@pytest.fixture()
def tmp_taskmaster(tmp_path, monkeypatch):
    """Create a minimal .taskmaster/ layout and redirect path resolution.

    Provides:
    - tmp_path/.taskmaster/backlog.yaml  (v3 schema with `meta.schema_version: 3`,
      empty epics/phases lists, `meta.updated` stub required by _mutate_and_save())
    - tmp_path/.taskmaster/PROGRESS.md   (stub with `## Changelog` header, required
      by the store's PROGRESS.md export, which reads it before rewriting)
    - tmp_path/.taskmaster/tasks/
    - tmp_path/.taskmaster/handovers/
    - tmp_path/.taskmaster/issues/
    - tmp_path/.taskmaster/ideas/

    Monkeypatches these backlog_server module attributes to point at tmp_path:
    - ROOT
    - CONFIG_PATH        (frozen at import time as ROOT / ".taskmaster" / "taskmaster.json")
    - LEGACY_CONFIG_PATH (frozen at import time as ROOT / ".claude" / "taskmaster.json")

    _resolve_paths() reads CONFIG_PATH and LEGACY_CONFIG_PATH directly — they
    are module-level constants captured at import time, not re-derived from
    ROOT on each call — so patching ROOT alone is insufficient for hermeticity.

    Returns the tmp_path (Path).
    """
    # Build directory structure
    tm_dir = tmp_path / ".taskmaster"
    for subdir in ("tasks", "handovers", "issues", "ideas", "local", "local/cache"):
        (tm_dir / subdir).mkdir(parents=True, exist_ok=True)

    # PROGRESS.md must exist so the store's export can read it.
    (tm_dir / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    (tm_dir / "local" / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")

    # Write a minimal v3 backlog.
    # meta.updated is required by _mutate_and_save(); meta.schema_version is the v3 marker
    # that _detect_schema_version reads to dispatch to load_v3/save_v3.
    backlog = {
        "version": 3,
        "project": "test-project",
        "meta": {"schema_version": 4},
        "epics": [],
        "phases": [],
        "context": {},
    }
    (tm_dir / "backlog.yaml").write_text(yaml.dump(backlog), encoding="utf-8")

    # Redirect path resolution in backlog_server. Default raising=True so a
    # rename or removal of any of these attributes fails the fixture loudly
    # instead of silently no-op'ing.
    from taskmaster import backlog_server  # noqa: PLC0415 — imported here so sys.path is set first

    monkeypatch.setattr(backlog_server, "ROOT", tmp_path)
    monkeypatch.setattr(
        backlog_server,
        "CONFIG_PATH",
        tmp_path / ".taskmaster" / "taskmaster.json",
    )
    monkeypatch.setattr(
        backlog_server,
        "LEGACY_CONFIG_PATH",
        tmp_path / ".claude" / "taskmaster.json",
    )

    # Tests that resolve relative paths against cwd should land in tmp_path —
    # keep this as a safety net for code paths that call Path.cwd() at runtime.
    monkeypatch.chdir(tmp_path)

    # The projection was just written; drop any store bound to a stale
    # view of this path so the first load bootstraps from these files.
    from taskmaster import store  # noqa: PLC0415

    store.reset_for_tests()

    return tmp_path


@pytest.fixture()
def tm_epic_phase(tmp_taskmaster):
    """tmp_taskmaster + a `test-epic` epic and `dev` phase ready for task writes."""
    from taskmaster import backlog_server  # noqa: PLC0415
    backlog_server.backlog_add_epic(epic_id="test-epic", name="Test Epic", done_when="all test tasks complete")
    backlog_server.backlog_add_phase(phase_id="dev", name="Development")
    return tmp_taskmaster


# ── Projection bypass guard ────────────────────────────────────────────────
# `taskmaster.store` is the only writer allowed to touch the backlog projection
# (`backlog.yaml` plus every entity directory beside it).  Anything else
# reaching those paths is a lost write waiting to happen, so the guard turns it
# into a loud, immediate failure during the test run.

_GUARDED_DIRS = (
    "tasks",
    "epics",
    "phases",
    "bugs",
    "issues",
    "handovers",
    "decisions",
    "ideas",
    "notes",
    "areas",
    "trackers",
    "integrations",
)
# A projection file is at most this many directory levels below its kind
# directory: `bugs/B-1.md` (1), `notes/_archive/N-1.md` and
# `integrations/trackers/t.md` (2), `handovers/_archive/<year>/h.md` (3).
_MAX_KIND_DEPTH = 3
# Derived files the store owns by name as well as by directory, the way
# `backlog.yaml` is owned: the regenerated ideas index and the Linear queue.
# Guarding the name catches a writer staging one outside its kind directory.
_GUARDED_FILES = ("IDEAS.md", "linear-queue.json")
# Configuration that lives at the backlog root rather than in a kind directory.
# It is shared state under `.taskmaster/` like everything else here, and the
# store owns its read-modify-write.
_GUARDED_ROOT_FILES = ("linear.yaml", "taskmaster.json")
_PACKAGE_DIR = PLUGIN_ROOT / "taskmaster"
_HOOKS_DIR = PLUGIN_ROOT / "hooks"
_STORE_FILE = _PACKAGE_DIR / "store.py"
# The one legacy module tests may still drive directly to seed a v3/v4
# projection *before* the store adopts it.  Production never enters here first —
# every real entry point is an MCP tool or a viewer handler in backlog_server.
_SEED_ENTRY_FILE = _PACKAGE_DIR / "taskmaster_v3.py"


# Captured before any test patches it, so a test can tell the guard's wrapper
# from the real thing.
_REAL_PATH_OPEN = Path.open


class ProjectionBypassError(AssertionError):
    """Raised when production code writes the projection outside the store."""


def _backlog_dir(directory: Path):
    """`directory` when it is a backlog root, else None."""
    if directory.name == ".taskmaster" or (directory / "backlog.yaml").exists():
        return directory
    return None


def _guard_path(target):
    """The backlog directory this write belongs to, or None when unguarded.

    Climbs from the file to its kind directory so the nested shapes are covered
    as well as the flat ones: `tasks/archive/` and `bugs/archive/`,
    `notes/_archive/`, the `handovers/_archive/<year>/` year bucket and the
    `integrations/trackers/` import fallback.  Files named by convention rather
    than by id — the derived `ideas/IDEAS.md` index and the Linear queue at
    `integrations/linear-queue.json` — are matched by name first, so they are
    guarded anywhere under a backlog root and not only in their canonical
    directory; the walk then covers them there as well.
    """
    try:
        path = Path(target)
    except TypeError:
        return None
    if path.name == "backlog.yaml":
        # `backlog_init` writes the very first backlog.yaml; there is no store
        # to route it through yet, and the store adopts it on the next read.
        if not path.exists() and not (path.parent / "local" / "store.db").exists():
            return None
        return path.parent
    if path.name in _GUARDED_ROOT_FILES:
        backlog_dir = _backlog_dir(path.parent)
        if backlog_dir is not None:
            # `backlog_init` writes `taskmaster.json` while scaffolding a
            # project, before any store exists to route it through — the same
            # bootstrap exemption `backlog.yaml` gets. Once the store is there,
            # every write to it is guarded.
            if (backlog_dir / "local" / "store.db").exists():
                return backlog_dir
            return None
    if path.name in _GUARDED_FILES:
        # No bootstrap exemption: both are regenerated from committed rows, so
        # there is never a legitimate first write outside the store.
        backlog_dir = _backlog_dir(path.parent)
        if backlog_dir is not None:
            return backlog_dir
    parent = path.parent
    for _ in range(_MAX_KIND_DEPTH):
        if parent.name in _GUARDED_DIRS:
            backlog_dir = _backlog_dir(parent.parent)
            if backlog_dir is not None:
                return backlog_dir
        if parent == parent.parent:  # filesystem root
            break
        parent = parent.parent
    return None


def _is_under(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _bypass_offender():
    """None when this write is legitimate, else the offending entry point.

    Walks the frames with `sys._getframe` rather than `inspect.stack()`: the
    latter resolves source context for every frame, and this runs on every
    guarded write in the suite — including the store's own temp-file opens.
    """
    import sys as _sys  # noqa: PLC0415

    files = []
    frame = _sys._getframe(1)
    while frame is not None:
        files.append(Path(frame.f_code.co_filename))
        frame = frame.f_back
    entry = None
    for path in reversed(files):  # outermost frame first
        if path == _STORE_FILE:
            return None  # the store owns the projection
        if entry is None and (
            _is_under(path, _PACKAGE_DIR) or _is_under(path, _HOOKS_DIR)
        ):
            entry = path
    if entry is None:
        return None  # a test seeding files itself, before any store bootstrap
    if entry == _SEED_ENTRY_FILE:
        return None  # legacy pure-module helper driven straight from a test
    return str(entry)


@pytest.fixture(autouse=True)
def projection_bypass_guard(request, monkeypatch):
    """Fail any production write to the backlog projection.

    Opt out with `@pytest.mark.allow_projection_bypass` for the handful of tests
    that deliberately drive a legacy migration writer.
    """
    if request.node.get_closest_marker("allow_projection_bypass"):
        yield
        return

    import os  # noqa: PLC0415
    import shutil  # noqa: PLC0415

    from taskmaster import taskmaster_v3  # noqa: PLC0415

    def _check(target, primitive: str) -> None:
        if _guard_path(target) is None:
            return
        offender = _bypass_offender()
        if offender is None:
            return
        raise ProjectionBypassError(
            f"projection bypass: {primitive} wrote {target!r} from {offender}; "
            "backlog entity writes must go through taskmaster.store"
        )

    real_atomic_write = taskmaster_v3.atomic_write
    real_write_task_file = taskmaster_v3.write_task_file
    real_write_text = Path.write_text
    real_write_bytes = Path.write_bytes
    real_replace = os.replace
    real_move = shutil.move
    real_unlink = Path.unlink
    real_remove = os.remove
    real_rename = Path.rename
    real_open = _REAL_PATH_OPEN

    def atomic_write(path, content):
        _check(path, "taskmaster_v3.atomic_write")
        return real_atomic_write(path, content)

    def write_task_file(path, frontmatter, body):
        _check(path, "taskmaster_v3.write_task_file")
        return real_write_task_file(path, frontmatter, body)

    def write_text(self, *args, **kwargs):
        _check(self, "Path.write_text")
        return real_write_text(self, *args, **kwargs)

    def write_bytes(self, data):
        _check(self, "Path.write_bytes")
        return real_write_bytes(self, data)

    def replace(src, dst, **kwargs):
        _check(dst, "os.replace")
        return real_replace(src, dst, **kwargs)

    def move(src, dst, *args, **kwargs):
        _check(dst, "shutil.move")
        return real_move(src, dst, *args, **kwargs)

    def unlink(self, *args, **kwargs):
        _check(self, "Path.unlink")
        return real_unlink(self, *args, **kwargs)

    def remove(path, **kwargs):
        _check(path, "os.remove")
        return real_remove(path, **kwargs)

    def rename(self, target):
        _check(self, "Path.rename")
        _check(target, "Path.rename")
        return real_rename(self, target)

    def open_(self, mode="r", *args, **kwargs):
        # A raw write open truncates exactly like `write_text`; leaving it
        # unwatched made the guard a matter of which primitive a bypass picked.
        if any(flag in mode for flag in ("w", "a", "x", "+")):
            _check(self, f"Path.open({mode!r})")
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(taskmaster_v3, "atomic_write", atomic_write)
    monkeypatch.setattr(taskmaster_v3, "write_task_file", write_task_file)
    monkeypatch.setattr(Path, "write_text", write_text)
    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(shutil, "move", move)
    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(os, "remove", remove)
    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(Path, "open", open_)
    yield
