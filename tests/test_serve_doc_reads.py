# User intent: on a team server a task's docs path reads only git-tracked project files, so no caller can read host secrets through a document section.
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from taskmaster import backlog_server as bs
from taskmaster.coordinator import client as client_module
from taskmaster.native_routing import documents
from taskmaster.serve import tracked as tracked_module
from taskmaster.serve.tracked import TrackedFiles, tracked_file_allowed
from test_native_service_build import root  # noqa: F401  (fixture)
from test_serve_viewer import git

MEMBER = "a" * 16
TASK = "test-epic-001"
MARKERS = {".env": "ENV-MARKER-1", "notes.md": "NOTES-MARKER-2",
           ".taskmaster/local/serve/operator.token": "TOKEN-MARKER-3", "outside": "OUTSIDE-MARKER-4"}


def _symlink(target, link):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        return False
    return True


@pytest.fixture
def served(root, tmp_path, monkeypatch):  # noqa: F811
    """A native store that is a git work tree, with tracked docs, tracked symlinks to secrets and untracked secrets."""
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "spec.md").write_text("# Spec\nTRACKED-SPEC\n", encoding="utf-8")
    (root / ".gitignore").write_text(".taskmaster/local/\n", encoding="utf-8")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text(MARKERS["outside"], encoding="utf-8")
    links = all([
        _symlink(os.path.join("..", ".taskmaster", "local", "serve", "operator.token"), root / "docs" / "tok"),
        _symlink(os.path.join("..", ".env"), root / "docs" / "env"),
        _symlink(str(outside), root / "docs" / "out")])
    git(root, "init", "-q")
    git(root, "-c", "core.symlinks=true", "add", "docs", ".gitignore")
    git(root, "-c", "core.symlinks=true", "commit", "-q", "-m", "init")
    for rel, marker in MARKERS.items():
        if rel != "outside":
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(marker + "\n", encoding="utf-8")
    from tests.native_coordinator_helpers import compatibility_client
    compatibility_client(root)                       # an in-process owner; tools never launch one
    monkeypatch.setattr(client_module, "ORIGIN_PROVIDER", lambda: MEMBER)
    return root, outside, links


def _set_spec(path):
    answer = bs.backlog_update_task(task_id=TASK, field="docs", value=f"spec:{path}")
    assert "Updated" in answer, answer


def _spec_text():
    return bs.backlog_get_task(task_id=TASK, sections=["spec"])


def _refused_paths(root_, outside):
    return [str(outside), outside.as_posix(), "../outside-secret.txt", "docs/../../outside-secret.txt",
            "C:/x", "C:\\x", "\\\\host\\share\\x", "docs\\spec.md", ".env", "notes.md",
            ".taskmaster/local/serve/operator.token", ".git/config"]


SYMLINKS = ["docs/tok", "docs/env", "docs/out"]


def test_the_shared_rule(served):
    root_, outside, links = served
    assert tracked_file_allowed(root_, "docs/spec.md")
    for rel in _refused_paths(root_, outside) + ["docs/spec.md\0"]:
        assert not tracked_file_allowed(root_, rel), rel
    if not links:
        pytest.skip("symlinks unavailable here")
    assert set(SYMLINKS) <= TrackedFiles(root_).get()          # the premise: git tracks the links
    for rel in SYMLINKS:
        assert not tracked_file_allowed(root_, rel), rel


def _assert_refused(answer):
    assert "(refused:" in answer, answer
    assert not any(marker in answer for marker in MARKERS.values()), answer


@pytest.mark.parametrize("index", range(12))
def test_a_served_docs_path_reads_only_tracked_files(served, index):
    root_, outside, _ = served
    _set_spec(_refused_paths(root_, outside)[index])
    _assert_refused(_spec_text())
    detail = bs.backlog_document(kind="task", entity_id=TASK, sections=["spec"], provenance=True)
    assert "refused" in detail and not any(marker in detail for marker in MARKERS.values()), detail


@pytest.mark.parametrize("link", SYMLINKS)
def test_a_tracked_symlink_to_a_secret_is_refused(served, link):
    _, _, links = served
    if not links:
        pytest.skip("symlinks unavailable here")
    _set_spec(link)
    _assert_refused(_spec_text())


def test_a_tracked_doc_still_reads(served):
    _set_spec("docs/spec.md")
    assert "TRACKED-SPEC" in _spec_text()
    detail = bs.backlog_document(kind="task", entity_id=TASK, sections=["spec"], provenance=True)
    assert "TRACKED-SPEC" in detail and "filesystem" in detail


@pytest.mark.parametrize("path", [".env", "docs/tok"])
def test_a_served_import_refuses_untracked_and_symlinked_files(served, path):
    _, _, links = served
    if path == "docs/tok" and not links:
        pytest.skip("symlinks unavailable here")
    _set_spec(path)
    answer = bs.backlog_document_import(kind="task", entity_id=TASK, sections=["spec"])
    assert answer.startswith("Error:") and "not a git-tracked project file" in answer, answer
    assert not any(marker in answer for marker in MARKERS.values())
    detail = bs.backlog_document(kind="task", entity_id=TASK, sections=["spec"], provenance=True)
    assert "refused" in detail and not any(marker in detail for marker in MARKERS.values()), detail


def test_a_served_import_of_a_tracked_doc_works(served):
    _set_spec("docs/spec.md")
    answer = bs.backlog_document_import(kind="task", entity_id=TASK, sections=["spec"])
    assert not answer.startswith("Error:"), answer
    assert "TRACKED-SPEC" in _spec_text()


def test_served_read_without_project_root_is_refused(served, monkeypatch):
    root_, outside, _ = served
    monkeypatch.setattr(documents, "project_root", lambda: None)
    for path in (str(outside), ".env"):
        _set_spec(path)
        _assert_refused(_spec_text())
    _set_spec("docs/spec.md")
    assert "TRACKED-SPEC" in _spec_text()            # the root is bs.ROOT, not project_root


def test_no_served_root_fails_closed(served, monkeypatch):
    _set_spec("docs/spec.md")
    monkeypatch.setattr(bs, "ROOT", None)
    from taskmaster import taskmaster_v3
    text, facts = taskmaster_v3.read_doc_section("docs/spec.md", None)
    assert text.startswith("(refused:") and facts["source"] == "refused"


def test_tracked_files_failure_fails_closed(served, monkeypatch):
    monkeypatch.setattr(TrackedFiles, "get", lambda self: frozenset())
    _set_spec("docs/spec.md")
    _assert_refused(_spec_text())


def test_solo_reads_are_unchanged(served, monkeypatch):
    root_, outside, _ = served
    monkeypatch.setattr(client_module, "ORIGIN_PROVIDER", None)
    _set_spec("notes.md")
    assert MARKERS["notes.md"] in _spec_text()
    _set_spec(str(root_ / "docs" / "spec.md"))
    assert "TRACKED-SPEC" in _spec_text()
    _set_spec(str(outside))
    assert MARKERS["outside"] in _spec_text()        # solo: the user's own machine, as before


def test_solo_reads_never_import_the_serve_package(served, monkeypatch):
    root_, _, _ = served
    code = ("import sys; from taskmaster import taskmaster_v3 as v3; "
            f"text, facts = v3.read_doc_section('docs/spec.md', __import__('pathlib').Path({str(root_)!r})); "
            "assert 'TRACKED-SPEC' in text, text; "
            "print(any(m.startswith('taskmaster.serve') for m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
    assert tracked_module                               # imported here, by the test only


# ── gate finding M5: a tracked doc missing from the work tree reads as not found ──

def test_a_missing_tracked_doc_is_not_found(served):
    root_, _, _ = served
    gone = root_ / "docs" / "gone.md"
    gone.write_text("GONE\n", encoding="utf-8")
    git(root_, "add", "docs/gone.md")
    git(root_, "commit", "-q", "-m", "gone")
    gone.unlink()
    tracked_module._SHARED.clear()                       # the fixture's listing predates this commit
    _set_spec("docs/gone.md")
    answer = _spec_text()
    assert "(unresolved: docs/gone.md)" in answer and "(refused:" not in answer, answer
    imported = bs.backlog_document_import(kind="task", entity_id=TASK, sections=["spec"])
    assert "no file at docs/gone.md" in imported, imported
    _set_spec("docs/never-existed.md")                    # untracked and absent: still the refusal
    assert "(refused:" in _spec_text()
