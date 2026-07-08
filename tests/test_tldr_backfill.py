from taskmaster.taskmaster_v3 import backfill_tldr


def test_backfill_tldr_adds_when_missing():
    fm = {"id": "T-001", "title": "Refactor auth"}
    body = "Refactor auth middleware. Steps follow."
    new_fm, changed = backfill_tldr(fm, body)
    assert changed is True
    assert new_fm["tldr"] == "Refactor auth middleware."
    assert new_fm["tldr_autogen"] is True


def test_backfill_tldr_skips_when_present():
    fm = {"id": "T-001", "title": "Refactor auth", "tldr": "Existing tldr."}
    body = "Some body."
    new_fm, changed = backfill_tldr(fm, body)
    assert changed is False
    assert new_fm["tldr"] == "Existing tldr."
    assert "tldr_autogen" not in new_fm


def test_backfill_tldr_uses_title_when_body_empty():
    fm = {"id": "T-001", "title": "Refactor auth middleware"}
    new_fm, changed = backfill_tldr(fm, "")
    assert changed is True
    assert new_fm["tldr"] == "Refactor auth middleware"
    assert new_fm["tldr_autogen"] is True


import os
import subprocess
from pathlib import Path

import yaml


def test_backfill_preserves_horizontal_rules_in_body(tmp_taskmaster):
    """Body with --- separators must not lose content during round-trip."""
    lessons_dir = Path(tmp_taskmaster) / ".taskmaster" / "lessons"
    lessons_dir.mkdir(parents=True, exist_ok=True)
    legacy = lessons_dir / "L-rules.md"
    body_with_rule = "## Why\n\nReason A.\n\n---\n\n## What to do\n\nStep 1.\n"
    legacy.write_text(
        f"---\nid: L-rules\ntitle: Lesson with rule\nkind: pattern\ntldr: placeholder\n---\n{body_with_rule}",
        encoding="utf-8",
    )
    # Remove tldr so backfill actually runs on it
    legacy.write_text(
        f"---\nid: L-rules\ntitle: Lesson with rule\nkind: pattern\n---\n{body_with_rule}",
        encoding="utf-8",
    )

    worktree_root = Path(__file__).resolve().parents[3]
    env = {**os.environ, "PYTHONPATH": str(worktree_root)}
    subprocess.run(
        ["python", "-m", "taskmaster.scripts.backfill_tldr",
         "--root", str(tmp_taskmaster)],
        env=env, capture_output=True, text=True, check=True,
    )

    after = legacy.read_text(encoding="utf-8")
    # Body should still contain the rule and all original content
    assert "Reason A" in after
    assert "Step 1" in after
    # The --- rule must survive in the body portion (after frontmatter fences)
    # Frontmatter is delimited by first and second ---, body follows the second
    parts = after.split("---", 2)
    assert len(parts) == 3, f"Expected frontmatter fences in output: {after!r}"
    body_part = parts[2]
    assert "---" in body_part, f"Horizontal rule lost from body: {body_part!r}"
    # Tldr was added
    fm = yaml.safe_load(parts[1]) or {}
    assert "tldr" in fm, f"tldr not backfilled: {fm}"


def test_backfill_script_processes_all_entities(tmp_taskmaster):
    # Pre-populate ONE legacy entity per supported type (no tldr field)
    tasks_dir = Path(tmp_taskmaster) / ".taskmaster" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    legacy_task = tasks_dir / "T-legacy-1.md"
    legacy_task.write_text(
        "---\n"
        "id: T-legacy-1\n"
        "title: Legacy task missing tldr\n"
        "status: todo\n"
        "---\n"
        "Body of the legacy task. Has detail.\n",
        encoding="utf-8",
    )

    issues_dir = Path(tmp_taskmaster) / ".taskmaster" / "issues"
    issues_dir.mkdir(parents=True, exist_ok=True)
    legacy_issue = issues_dir / "ISS-legacy-1.md"
    legacy_issue.write_text(
        "---\n"
        "id: ISS-legacy-1\n"
        "title: Legacy issue\n"
        "severity: P2\n"
        "---\n"
        "Legacy issue body.\n",
        encoding="utf-8",
    )

    lessons_dir = Path(tmp_taskmaster) / ".taskmaster" / "lessons"
    lessons_dir.mkdir(parents=True, exist_ok=True)
    legacy_lesson = lessons_dir / "L-legacy-1.md"
    legacy_lesson.write_text(
        "---\n"
        "id: L-legacy-1\n"
        "title: Legacy lesson\n"
        "kind: pattern\n"
        "---\n"
        "Always commit small.\n",
        encoding="utf-8",
    )

    # Run the CLI
    import os
    worktree_root = Path(__file__).parent.parent.parent.parent
    env = {**os.environ, "PYTHONPATH": str(worktree_root)}
    result = subprocess.run(
        ["python", "-m", "taskmaster.scripts.backfill_tldr",
         "--root", str(tmp_taskmaster)],
        capture_output=True, text=True, check=True,
        env=env,
    )
    assert "backfilled" in result.stdout.lower()

    # Verify tldr was added to each
    for path in (legacy_task, legacy_issue, legacy_lesson):
        content = path.read_text(encoding="utf-8")
        # Frontmatter is the YAML block between the first two ---
        parts = content.split("---", 2)
        assert len(parts) >= 3, f"No frontmatter in {path.name}"
        fm = yaml.safe_load(parts[1]) or {}
        assert "tldr" in fm, f"Missing tldr in {path.name}: {fm}"
        assert fm.get("tldr_autogen") is True, f"Missing tldr_autogen flag in {path.name}: {fm}"
