"""Lint checks for the taskmaster:start-session skill (glance-first redesign).

NOTE: Body budget (1300 tokens) is xfail until Plan D merges — Plan D owns the
start-session SKILL.md restructuring. Description budget check runs immediately.
"""
from pathlib import Path
import re
import yaml
import pytest
from skill_budget_helper import body_token_count, description_word_count

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "start-session"
SKILL_MD = SKILL_DIR / "SKILL.md"
DEEP_MODE_REF = SKILL_DIR / "references" / "deep-mode.md"

# Approx characters-per-token for plain prose/markdown.
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


def test_frontmatter_required_fields():
    fm = _read_frontmatter_full()
    assert fm.get("name") == "start-session"
    assert "description" in fm


def test_deep_mode_reference_exists():
    assert DEEP_MODE_REF.exists(), (
        "references/deep-mode.md missing — deep ceremony content must live here"
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
    actual = body_token_count("start-session")
    assert actual <= 1_300, f"body is {actual} tokens (budget: 1300)"


def test_skill_md_body_under_token_budget():
    """Glance body (SKILL.md minus frontmatter) must be ≤1,300 tokens.

    This is a guardrail — content tests are the primary TDD signal.
    """
    body = _body_without_frontmatter(SKILL_MD)
    tokens = _token_estimate(body)
    assert tokens <= 1_300, (
        f"SKILL.md body is ~{tokens} tokens (limit 1,300). "
        "Move deep-ceremony content to references/deep-mode.md."
    )


# ── Description budget ───────────────────────────────────────────────────────

def test_description_within_word_budget():
    count = description_word_count("start-session")
    assert count <= 60, f"description is {count} words (budget: 60)"


def test_description_contains_key_trigger_phrases():
    fm = _read_frontmatter_full()
    desc = fm["description"].lower()
    must_have = ["let's get started", "what should i work on", "show me the backlog"]
    missing = [p for p in must_have if p not in desc]
    assert not missing, f"description missing: {missing}"


# ── Content / glance-first checks ────────────────────────────────────────────

def test_glance_mcp_calls_listed_in_body():
    """Glance path must reference the slim MCP calls from spec §4."""
    body = _body_without_frontmatter(SKILL_MD)
    required_calls = [
        "backlog_status",
        "backlog_handover_list",
    ]
    missing = [c for c in required_calls if c not in body]
    assert not missing, f"Glance MCP calls missing from SKILL.md body: {missing}"


def test_handover_list_filters_to_open():
    """start-session glance must filter handovers to status=open (Plan B requirement)."""
    body = _body_without_frontmatter(SKILL_MD)
    assert 'backlog_handover_list' in body
    assert 'status="open"' in body or "status='open'" in body, (
        "start-session glance must call backlog_handover_list with status=open (Plan B requirement)"
    )


def test_deep_ceremony_not_inline_in_body():
    """Heavy deep-mode calls must NOT appear in the glance body.

    backlog_recap, backlog_lesson_digest, backlog_lesson_get, and backlog_last_session
    are deep-mode only — they belong in references/deep-mode.md.
    """
    body = _body_without_frontmatter(SKILL_MD)
    deep_only_calls = ["backlog_recap", "backlog_lesson_digest", "backlog_lesson_get"]
    present = [c for c in deep_only_calls if c in body]
    assert not present, (
        f"Deep-mode MCP calls found in SKILL.md glance body: {present}. "
        "Move them to references/deep-mode.md."
    )


def test_deep_flag_mentioned_in_body():
    """SKILL.md must mention --deep so users know how to access the full ceremony."""
    body = _body_without_frontmatter(SKILL_MD)
    assert "--deep" in body, "SKILL.md must document the --deep flag"


def test_deep_mode_reference_linked_from_body():
    """SKILL.md must link to references/deep-mode.md."""
    body = _body_without_frontmatter(SKILL_MD)
    assert "references/deep-mode.md" in body, (
        "SKILL.md must link to references/deep-mode.md for the deep ceremony"
    )
