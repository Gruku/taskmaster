"""Lint checks for the taskmaster:pick-task skill (glance-first redesign)."""
from pathlib import Path
import re
import yaml
from skill_budget_helper import body_token_count, description_word_count

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "pick-task"
PLAYBOOK_DIR = Path(__file__).resolve().parents[1] / "playbooks" / "pick-task"
SKILL_MD = SKILL_DIR / "SKILL.md"
PLAYBOOK_MD = PLAYBOOK_DIR / "playbook.md"
DEEP_MODE_REF = PLAYBOOK_DIR / "references" / "deep-mode.md"
EXISTING_REF = PLAYBOOK_DIR / "references" / "v3-context-loading.md"

_CHARS_PER_TOKEN = 4


def _token_estimate(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN


def _read_frontmatter_full() -> dict:
    text = SKILL_MD.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    return yaml.safe_load(m.group(1)) or {} if m else {}


def _body_without_frontmatter(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.index("---", 3)
        return text[end + 3:].strip()
    return text.strip()


# ── Structure ────────────────────────────────────────────────────────────────

def test_skill_md_exists():
    assert SKILL_MD.exists(), "SKILL.md missing"


def test_playbook_exists():
    assert PLAYBOOK_MD.exists(), "playbooks/pick-task/playbook.md missing"


def test_wrapper_points_at_playbook():
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "../../playbooks/pick-task/playbook.md" in text


def test_frontmatter_required_fields():
    fm = _read_frontmatter_full()
    assert fm.get("name") == "pick-task"
    assert "description" in fm


def test_deep_mode_reference_exists():
    assert DEEP_MODE_REF.exists(), (
        "references/deep-mode.md missing — deep ceremony content must live here"
    )


def test_existing_v3_context_loading_ref_preserved():
    assert EXISTING_REF.exists(), (
        "references/v3-context-loading.md was deleted — it must be preserved or merged into deep-mode.md"
    )


def test_deep_mode_reference_is_not_stub():
    non_blank = [
        ln for ln in DEEP_MODE_REF.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert len(non_blank) >= 30, (
        f"references/deep-mode.md looks like a stub ({len(non_blank)} non-blank lines). "
        "Full deep ceremony must be written there."
    )


# ── Token budget ─────────────────────────────────────────────────────────────

def test_body_within_budget():
    actual = body_token_count("pick-task")
    assert actual <= 1_300, f"playbook is {actual} tokens (budget: 1300)"


def test_skill_md_body_under_token_budget():
    """Glance body (playbook.md) must be ≤1,300 tokens.

    This is a guardrail — content tests are the primary TDD signal.
    """
    body = _body_without_frontmatter(PLAYBOOK_MD)
    tokens = _token_estimate(body)
    assert tokens <= 1_300, (
        f"playbook.md body is ~{tokens} tokens (limit 1,300). "
        "Move deep-ceremony content to references/deep-mode.md."
    )


# ── Description budget ───────────────────────────────────────────────────────

def test_description_within_word_budget():
    count = description_word_count("pick-task")
    assert count <= 60, f"description is {count} words (budget: 60)"


def test_description_contains_key_trigger_phrases():
    fm = _read_frontmatter_full()
    desc = fm["description"].lower()
    must_have = ["pick a task", "continue where we left off", "continue this task"]
    missing = [p for p in must_have if p not in desc]
    assert not missing, f"description missing: {missing}"


# ── Content / glance-first checks ────────────────────────────────────────────

def test_glance_mcp_calls_present():
    """Glance path must reference the slim MCP calls from spec §4."""
    body = _body_without_frontmatter(PLAYBOOK_MD)
    required_calls = [
        "backlog_get_task",
        "backlog_dependencies",
        "backlog_handover_list",
        "backlog_lesson_match",
        "backlog_issue_list",
    ]
    missing = [c for c in required_calls if c not in body]
    assert not missing, f"Glance MCP calls missing from SKILL.md body: {missing}"


def test_handover_list_filters_to_open():
    """pick-task glance must filter handovers to status=open (Plan B requirement).

    Current step 5a calls backlog_handover_list(task_id=<id>, limit=3) WITHOUT
    status="open" — this test fails today and passes after the glance rewrite.
    """
    body = _body_without_frontmatter(PLAYBOOK_MD)
    assert 'backlog_handover_list' in body
    assert 'status="open"' in body or "status='open'" in body, (
        "pick-task glance must filter handovers to status=open (Plan B requirement)"
    )


def test_full_lesson_body_load_not_inline():
    """Full lesson body loading (backlog_lesson_get) must NOT appear in the glance body.

    In the glance path, lesson_match returns IDs+tldrs only.
    backlog_lesson_get belongs in references/deep-mode.md.
    """
    body = _body_without_frontmatter(PLAYBOOK_MD)
    assert "backlog_lesson_get" not in body, (
        "backlog_lesson_get found in SKILL.md glance body — move it to references/deep-mode.md. "
        "Glance path uses backlog_lesson_match IDs+tldrs only."
    )


def test_blast_radius_not_in_glance_body():
    """backlog_blast_radius is a deep-mode call — must not appear in glance body."""
    body = _body_without_frontmatter(PLAYBOOK_MD)
    assert "backlog_blast_radius" not in body, (
        "backlog_blast_radius found in SKILL.md glance body — it belongs in references/deep-mode.md"
    )


def test_deep_flag_mentioned_in_body():
    body = _body_without_frontmatter(PLAYBOOK_MD)
    assert "--deep" in body, "SKILL.md must document the --deep flag"


def test_deep_mode_reference_linked_from_body():
    body = _body_without_frontmatter(PLAYBOOK_MD)
    assert "references/deep-mode.md" in body, (
        "SKILL.md must link to references/deep-mode.md for the deep ceremony"
    )
