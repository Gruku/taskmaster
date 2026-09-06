# plugins/taskmaster/tests/test_resolver_guard.py
"""Regression tests for tm-audit-001 — artifact-root hijack guard.

`backlog_server._resolve_paths()` must refuse to treat the plugin's own
directory as a project root, even when a `backlog.yaml` fixture sits next to
`backlog_server.py`, and even in the post-relocation case where only a
`.taskmaster/` subdir exists there (the guard is unconditional — it must not
depend on which fallback branch would otherwise fire; see spec
2026-07-04-tm-audit-001-artifact-root-hijack.md §9 Amendment A).

`taskmaster_v3._resolve_artifact_root()` carried a second copy of this guard
for its CWD-flavour readers. Those readers now take the resolved backlog path
from the caller, so `_resolve_paths` is the only resolver left and the only one
that needs guarding.
"""
import pytest
import yaml

REFUSAL = "Refusing to use the taskmaster plugin directory"


def test_resolve_paths_raises_on_plugin_dir_fixture(tmp_path, monkeypatch):
    """A backlog.yaml adjacent to backlog_server.py is refused."""
    (tmp_path / "backlog.yaml").write_text(yaml.safe_dump({"epics": [], "phases": []}))
    from taskmaster import backlog_server
    monkeypatch.setattr(backlog_server, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(backlog_server, "ROOT", tmp_path)
    with pytest.raises(RuntimeError, match=REFUSAL):
        backlog_server._resolve_paths()


def test_resolve_paths_raises_post_relocation_hole(tmp_path, monkeypatch):
    """Amendment B: no backlog.yaml adjacent, but a .taskmaster/ subdir exists
    (the real post-relocation state, e.g. holding project.yaml) — the guard
    must still fire because it is unconditional, not nested in the
    root-layout fallback branch that would otherwise go dead."""
    tm_dir = tmp_path / ".taskmaster"
    tm_dir.mkdir()
    (tm_dir / "backlog.yaml").write_text(yaml.safe_dump({"epics": [], "phases": []}))
    from taskmaster import backlog_server
    monkeypatch.setattr(backlog_server, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(backlog_server, "ROOT", tmp_path)
    with pytest.raises(RuntimeError, match=REFUSAL):
        backlog_server._resolve_paths()


def test_resolve_paths_negative_control_non_plugin_dir(tmp_path, monkeypatch):
    """A root-layout backlog.yaml NOT adjacent to the plugin resolves normally."""
    (tmp_path / "backlog.yaml").write_text(yaml.safe_dump({"epics": [], "phases": []}))
    from taskmaster import backlog_server
    plugin_dir = tmp_path / "not-the-plugin-dir"
    plugin_dir.mkdir()
    monkeypatch.setattr(backlog_server, "SCRIPT_DIR", plugin_dir)
    monkeypatch.setattr(backlog_server, "ROOT", tmp_path)
    bp, pp = backlog_server._resolve_paths()
    assert bp == tmp_path / "backlog.yaml"


def test_resolve_paths_positive_control_taskmaster_layout(tmp_path, monkeypatch):
    """.taskmaster/backlog.yaml layout in a non-plugin dir resolves normally;
    the guard stays dormant."""
    tm_dir = tmp_path / ".taskmaster"
    tm_dir.mkdir()
    (tm_dir / "backlog.yaml").write_text(yaml.safe_dump({"epics": [], "phases": []}))
    from taskmaster import backlog_server
    plugin_dir = tmp_path / "not-the-plugin-dir"
    plugin_dir.mkdir()
    monkeypatch.setattr(backlog_server, "SCRIPT_DIR", plugin_dir)
    monkeypatch.setattr(backlog_server, "ROOT", tmp_path)
    bp, pp = backlog_server._resolve_paths()
    assert bp == tm_dir / "backlog.yaml"