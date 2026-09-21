"""User intent: document detail must be served from the store's own prose, so
`backlog_document` and `backlog_get_task(provenance=True)` answer over the native
retriever while the sections answer an agent already relies on never regresses.

The regression fence is `test_get_task_sections_still_answer_the_document_text`:
`backlog_get_task(sections=["spec"])` returns the spec's text today and must keep
returning it, imported or not. Provenance becomes honest; the answer does not change.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from taskmaster import backlog_server as bs
from native_twins import make_twins

HANDOVER = "2026-09-17-handover-with-sections"


def _seed_documents():
    bs.backlog_add_task(title="Documented task", epic="test-epic", phase="dev",
                        notes="Inline notes",
                        options={"docs": "spec:docs/spec.md;plan:docs/missing.md"})
    bs.backlog_handover_create(tldr="Handover with sections",
                               body="## Decisions\nChose the store\n\n## Blockers\nNone\n")
    bs.backlog_issue_create(title="Documented issue", severity="P2", evidence="x",
                            body="## Repro\nRun it twice\n\n## Notes\nWatch the seq\n")
    bs.backlog_bug_create(title="Documented bug", found_in="test-epic-001")
    docs = Path("docs")
    docs.mkdir(exist_ok=True)
    (docs / "spec.md").write_text("# Spec\n\nThe real spec text.\n", encoding="utf-8")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed_documents)


# ── The capability that must not regress ────────────────────────────────────


def test_get_task_sections_still_answer_the_document_text(twins):
    for kwargs in ({"sections": ["spec"]}, {"sections": ["notes", "spec"]},
                   {"sections": ["plan"]}, {"sections": ["spec", "plan", "notes"]},
                   {"sections": ["review_instructions"]}, {"sections": []},
                   {"sections": ["bogus"]}):
        twins.same("backlog_get_task", task_id="test-epic-001", **kwargs)
    legacy, native = twins.same("backlog_get_task", task_id="test-epic-001", sections=["spec"])
    assert "The real spec text." in native
    # A doc path with no file on disk keeps the literal the tool has always shown.
    assert "(unresolved: docs/missing.md)" in twins.same(
        "backlog_get_task", task_id="test-epic-001", sections=["plan"])[1]


def test_get_task_default_answers_are_untouched_by_the_new_parameter(twins):
    for kwargs in ({}, {"verbose": True}, {"expand_links": True}, {"provenance": True}):
        twins.same("backlog_get_task", task_id="test-epic-001", **kwargs)
    plain = twins.same("backlog_get_task", task_id="test-epic-001", sections=["notes"])[1]
    assert plain == twins.same("backlog_get_task", task_id="test-epic-001",
                               sections=["notes"], provenance=False)[1]


def test_get_task_provenance_reports_the_filesystem_when_nothing_is_imported(twins):
    _, native = twins.same("backlog_get_task", task_id="test-epic-001",
                           sections=["notes", "spec", "plan"], provenance=True)
    assert "source: inline" in native
    assert "source: filesystem" in native and "docs/spec.md" in native
    assert "source: missing" in native and "docs/missing.md" in native
    assert "imported: false" in native
    # Provenance annotates, it never replaces: the prose is still there in full.
    assert "The real spec text." in native and "Inline notes" in native


# ── The kind-general retriever ──────────────────────────────────────────────


def test_document_answers_stored_prose_for_every_kind(twins):
    _, task = twins.same("backlog_document", kind="task", entity_id="test-epic-001",
                         sections=["notes", "spec"])
    assert "Inline notes" in task and "The real spec text." in task
    _, handover = twins.same("backlog_document", kind="handover", entity_id=HANDOVER,
                             sections=["decisions", "blockers"])
    assert "Chose the store" in handover and "None" in handover
    _, issue = twins.same("backlog_document", kind="issue", entity_id="ISS-001",
                          sections=["repro", "notes"])
    assert "Run it twice" in issue and "Watch the seq" in issue
    _, whole = twins.same("backlog_document", kind="issue", entity_id="ISS-001")
    assert "## Repro" in whole


def test_document_provenance_matches_the_task_sections_view(twins):
    _, native = twins.same("backlog_document", kind="task", entity_id="test-epic-001",
                           sections=["spec", "plan"], provenance=True)
    assert "source: filesystem" in native and "source: missing" in native


@pytest.mark.parametrize("kwargs", [
    {"kind": "task", "entity_id": "ghost-1", "sections": ["notes"]},
    {"kind": "issue", "entity_id": "ISS-404"},
    {"kind": "issue", "entity_id": "ISS-001", "sections": ["bogus"]},
    {"kind": "issue", "entity_id": "ISS-001", "sections": []},
    {"kind": "bug", "entity_id": "B-001", "sections": ["notes"]},
    {"kind": "task", "entity_id": "test-epic-001", "sections": ["decisions"]},
])
def test_document_refusals_match(twins, kwargs):
    legacy, native = twins.same("backlog_document", **kwargs)
    assert legacy.startswith(("Error:", "Not found")), legacy


def test_document_whole_body_of_a_kind_without_canonical_sections(twins):
    twins.same("backlog_document", kind="bug", entity_id="B-001")


# -- The narrow importer (S11, decision D7) -----------------------------------


def test_import_moves_the_sections_answer_into_the_store(twins):
    with twins.at(twins.native):
        report = bs.backlog_document_import(kind="task", entity_id="test-epic-001")
        assert "spec" in report and "docs/spec.md" in report
        # A declared doc with no file is reported, not refused, and nothing is stored.
        assert "docs/missing.md" in report
        # From here the answer is the store's, so an edit on disk is not served.
        (twins.native / "docs" / "spec.md").write_text("Edited on disk.\n", encoding="utf-8")
        answer = bs.backlog_get_task(task_id="test-epic-001", sections=["spec"], provenance=True)
        assert "The real spec text." in answer and "Edited on disk." not in answer
        assert "source: import" in answer and "content_hash" in answer
        document = bs.backlog_document(kind="task", entity_id="test-epic-001", sections=["spec"])
        assert "The real spec text." in document


def test_import_is_idempotent_and_reimports_an_edited_file_on_request(twins):
    with twins.at(twins.native):
        bs.backlog_document_import(kind="task", entity_id="test-epic-001", sections=["spec"])
        assert "unchanged" in bs.backlog_document_import(kind="task", entity_id="test-epic-001",
                                                         sections=["spec"])
        (twins.native / "docs" / "spec.md").write_text("Second revision.\n", encoding="utf-8")
        bs.backlog_document_import(kind="task", entity_id="test-epic-001", sections=["spec"])
        assert "Second revision." in bs.backlog_document(kind="task", entity_id="test-epic-001",
                                                          sections=["spec"])


@pytest.mark.parametrize("kwargs,expected", [
    ({"kind": "task", "entity_id": "ghost-1"}, "not found"),
    ({"kind": "task", "entity_id": "test-epic-001", "sections": ["notes"]}, "section"),
    ({"kind": "task", "entity_id": "test-epic-001", "sections": []}, "sections=[]"),
    ({"kind": "handover", "entity_id": HANDOVER}, "task"),
])
def test_import_refusals(twins, kwargs, expected):
    with twins.at(twins.native):
        answer = bs.backlog_document_import(**kwargs)
    assert answer.startswith("Error:") and expected in answer, answer


def test_import_refuses_on_a_legacy_store(twins):
    with twins.at(twins.legacy):
        answer = bs.backlog_document_import(kind="task", entity_id="test-epic-001")
    assert answer.startswith("Error:") and "native" in answer


# -- The importer reads only the project's own files (review B, item 4) --------


def _declare(task_id, section, path):
    answer = bs.backlog_update_task(task_id=task_id, field="docs", value=f"{section}:{path}")
    assert "Error" not in answer, answer


def _stored(task_id, section):
    return bs.backlog_document(kind="task", entity_id=task_id, sections=[section], provenance=True)


@pytest.mark.parametrize("where", ["absolute", "parent"])
def test_import_refuses_a_path_outside_the_project(twins, tmp_path, where):
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("TOP SECRET OUTSIDE PROJECT\n", encoding="utf-8")
    with twins.at(twins.native):
        path = str(secret) if where == "absolute" else f"../{secret.name}"
        assert (twins.native / path).resolve() == secret.resolve()
        _declare("test-epic-001", "design", path)

        answer = bs.backlog_document_import(kind="task", entity_id="test-epic-001", sections=["design"])

        assert answer.startswith("Error:") and "outside the project" in answer, answer
        assert "TOP SECRET" not in answer
        stored = _stored("test-epic-001", "design")
        assert "source: import" not in stored, stored


def test_import_refuses_a_link_that_leads_outside_the_project(twins, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("TOP SECRET BEHIND A LINK\n", encoding="utf-8")
    with twins.at(twins.native):
        link = twins.native / "docs" / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            # Windows grants symlinks only to privileged users; a junction needs
            # no privilege and is resolved the same way.
            _winapi = pytest.importorskip("_winapi")
            _winapi.CreateJunction(str(outside), str(link))
        _declare("test-epic-001", "design", "docs/linked/secret.md")

        answer = bs.backlog_document_import(kind="task", entity_id="test-epic-001", sections=["design"])

        assert answer.startswith("Error:") and "outside the project" in answer, answer
        assert "source: import" not in _stored("test-epic-001", "design")


def test_import_of_a_directory_is_a_clean_error_not_a_crash(twins):
    with twins.at(twins.native):
        (twins.native / "adir").mkdir()
        _declare("test-epic-001", "design", "adir")

        answer = bs.backlog_document_import(kind="task", entity_id="test-epic-001", sections=["design"])

        assert answer.startswith("Error:") and "adir" in answer and "not a file" in answer, answer
