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
