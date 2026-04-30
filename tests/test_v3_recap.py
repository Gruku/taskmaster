"""Recap entity + handover-kind viewer mapping."""
import pytest


def test_recap_schema_version_is_one():
    from taskmaster_v3 import RECAP_SCHEMA_VERSION
    assert RECAP_SCHEMA_VERSION == 1


def test_handover_kind_to_viewer_kind_maps_all_four():
    from taskmaster_v3 import HANDOVER_KIND_TO_VIEWER_KIND, HANDOVER_KINDS
    # Spec §3.12 — viewer renders the four storage kinds as four UI kinds:
    assert HANDOVER_KIND_TO_VIEWER_KIND["end-of-day"]      == "wrap"
    assert HANDOVER_KIND_TO_VIEWER_KIND["context-handoff"] == "mid-task"
    assert HANDOVER_KIND_TO_VIEWER_KIND["crash-recovery"]  == "checkpoint"
    assert HANDOVER_KIND_TO_VIEWER_KIND["auto-stage"]      == "standalone"
    # Mapping covers every storage kind:
    assert set(HANDOVER_KIND_TO_VIEWER_KIND.keys()) == set(HANDOVER_KINDS)
    # All viewer kinds are valid:
    assert set(HANDOVER_KIND_TO_VIEWER_KIND.values()) == {
        "mid-task", "checkpoint", "wrap", "standalone"
    }


def test_recap_path_resolves_under_taskmaster_recaps():
    from taskmaster_v3 import recap_path
    p = recap_path("SES-0184")
    assert str(p).replace("\\", "/").endswith(".taskmaster/recaps/SES-0184.md")


def test_format_recap_markdown_round_trip():
    from taskmaster_v3 import _format_recap_markdown, _parse_recap_markdown
    fm = {
        "session_id": "SES-0184",
        "snapshot_before": "SNAP-0183",
        "snapshot_after": "SNAP-0184",
        "generator": "claude",
        "generated_at": "2026-04-26T16:48:00Z",
        "token_cost": 1840,
    }
    md = _format_recap_markdown(
        frontmatter=fm,
        title="Stitched the worktree review gate",
        what_happened="Started in <em>worktree-shadow</em>. Got blocked by *PKCE*.",
        what_landed="Three tasks closed. One handover.",
        whats_next="Pick up the rebased branch tomorrow.",
    )
    assert md.startswith("---\n")
    assert "session_id: SES-0184" in md
    assert "# Stitched the worktree review gate" in md
    assert "## What happened" in md
    assert "## What landed" in md
    assert "## What's next" in md

    parsed = _parse_recap_markdown(md)
    assert parsed["frontmatter"]["session_id"] == "SES-0184"
    assert parsed["title"] == "Stitched the worktree review gate"
    assert parsed["what_happened"].startswith("Started in <em>worktree-shadow</em>")
    assert parsed["what_landed"].startswith("Three tasks closed.")
    assert parsed["whats_next"].startswith("Pick up the rebased branch")


def test_save_recap_writes_file_with_expected_shape(tmp_path, monkeypatch):
    from taskmaster_v3 import save_recap, recap_path, RECAP_SCHEMA_VERSION
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster").mkdir()

    save_recap(
        session_id="SES-0184",
        frontmatter={
            "snapshot_before": "SNAP-0183",
            "snapshot_after":  "SNAP-0184",
            "generator":       "claude",
            "generated_at":    "2026-04-26T16:48:00Z",
            "token_cost":      1840,
        },
        title="Stitched the worktree review gate",
        what_happened="Started in worktree-shadow.",
        what_landed="Three tasks closed.",
        whats_next="Rebase tomorrow.",
    )
    p = recap_path("SES-0184")
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    # Frontmatter pinned: session_id and schema_version are auto-injected.
    assert "session_id: SES-0184" in text
    assert f"schema_version: {RECAP_SCHEMA_VERSION}" in text
    assert "snapshot_before: SNAP-0183" in text
    assert "## What happened" in text
    assert "## What landed" in text
    assert "## What's next" in text
