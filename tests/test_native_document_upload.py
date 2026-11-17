# User intent: an agent can import a task's document by sending its text (content=), with no file on the host, under the same per-section limits and answers as a file import, an optional expected_hash guarding a replace, and solo file imports unchanged (team-server-012 D2, D3, D5).
from __future__ import annotations

import hashlib
import subprocess
import sys

import pytest

from taskmaster import backlog_server as bs
from test_native_routing_documents import twins  # noqa: F401  (fixture: declares spec:docs/spec.md, plan:docs/missing.md)

TASK = "test-epic-001"


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _upload(**kwargs):
    return bs.backlog_document_import(kind="task", entity_id=TASK, **kwargs)


def _plan():
    return bs.backlog_get_task(task_id=TASK, sections=["plan"])


def test_content_imports_with_no_file_on_disk(twins):
    with twins.at(twins.native):
        answer = _upload(content={"plan": "A\r\nB\rC"})
        assert f"- plan: imported from your upload as docs/missing.md (sha256 {_sha('A\nB\nC')[:12]}…)" in answer
        assert not answer.startswith("Error:")
        text = _plan()
        assert "A\nB\nC" in text and "(unresolved" not in text
        assert "unchanged — the store already holds this text" in _upload(content={"plan": "A\nB\nC"})


def test_an_upload_hashes_like_a_file_import_of_the_same_text(twins):
    with twins.at(twins.native):
        _upload(content={"spec": "Same text\r\n"})
        on_disk = (twins.native / "docs" / "spec.md")
        on_disk.write_text("Same text\n", encoding="utf-8")
        assert "unchanged" in _upload(sections=["spec"])


def test_sections_and_content_are_exclusive(twins):
    with twins.at(twins.native):
        assert _upload(sections=["plan"], content={"plan": "x"}) == (
            "Error: pass sections (import the declared files) or content (the text of each section), not both")


@pytest.mark.parametrize("content", [{}, {"plan": 5}])
def test_content_must_name_text(twins, content):
    with twins.at(twins.native):
        assert _upload(content=content).startswith("Error:")


def test_an_unknown_section_is_refused(twins):
    with twins.at(twins.native):
        assert _upload(content={"bogus": "x"}).startswith("Error: section must be one of")


def test_per_section_failures_leave_the_rest_imported(twins):
    with twins.at(twins.native):
        big = "x" * (1024 * 1024 + 1)
        answer = _upload(content={"design": "x", "plan": "Plan text.\n", "spec": big})
        assert answer.startswith("Error: 2 of 3 sections failed")
        assert ('- design: FAILED — the task declares no design path; set one first with '
                'backlog_update_task(field="docs", value="design:<path in your checkout>") — nothing imported') in answer
        assert ("- spec: FAILED — the uploaded text is 1048577 bytes, over the 1048576-byte (1 MiB) limit on one "
                "imported document — nothing imported") in answer
        assert "- plan: imported from your upload" in answer
        assert "Plan text." in _plan()


def test_an_unpaired_surrogate_is_refused_by_name(twins):
    with twins.at(twins.native):
        answer = _upload(content={"plan": "a\ud800b"})
        assert ("- plan: FAILED — the uploaded text is not valid UTF-8 (it holds an unpaired surrogate) — "
                "nothing imported") in answer


def test_a_stale_expected_hash_changes_nothing(twins):
    with twins.at(twins.native):
        _upload(content={"plan": "First.\n"})
        answer = _upload(content={"plan": "Second.\n"}, expected_hash={"plan": ""})
        assert "- plan: FAILED — the stored copy changed: it is sha256 " + _sha("First.\n")[:12] in answer
        assert "First." in _plan()
        answer = _upload(content={"plan": "Second.\n"}, expected_hash={"plan": _sha("First.\n")})
        assert "- plan: imported from your upload" in answer and "Second." in _plan()


@pytest.mark.parametrize("expected", [{"plan": "zz"}, {"plan": "A" * 64}, {"spec": ""}, {"plan": 5}])
def test_a_malformed_expected_hash_is_refused(twins, expected):
    with twins.at(twins.native):
        answer = _upload(content={"plan": "x"}, expected_hash=expected)
        assert answer.startswith("Error: expected_hash"), answer
        assert "(unresolved" in _plan()


def test_expected_hash_must_name_an_imported_section_on_the_path_form_too(twins):
    """012 gate M4: a precondition for a section the call skips is refused, not silently ignored."""
    with twins.at(twins.native):
        answer = _upload(sections=["spec"], expected_hash={"plan": ""})
        assert answer == "Error: expected_hash names a section this call does not import", answer


def test_a_file_import_answer_is_unchanged(twins):
    with twins.at(twins.native):
        answer = _upload(sections=["spec"])
        assert "- spec: imported from docs/spec.md" in answer


def test_the_upload_path_never_imports_the_serve_package():
    code = ("import sys; from taskmaster.native_routing import documents; "
            "from taskmaster.native import commands, documents as native_documents; "
            "print(any(m == 'taskmaster.serve' or m.startswith('taskmaster.serve.') for m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
