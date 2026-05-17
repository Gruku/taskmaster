"""Smoke test: pick-task glance path token budget.

Simulates MCP tool calls the glance flow triggers, counts total tokens, asserts budget.
"""
from pathlib import Path

SKILL_MD = (
    Path(__file__).resolve().parents[1] / "skills" / "pick-task" / "SKILL.md"
)

# Realistic slim-mode mock payloads.
_GET_TASK_SLIM = """id: T-001
title: Rewrite auth middleware
tldr: Replace JWT storage in localStorage with httpOnly cookies.
next_step: Backfill migration script for existing users.
status: in-progress
priority: high
depends_on: [T-002]
related_issues: [ISS-007]
related_lessons: [L-003]
docs_available: [spec, plan]
open_handovers: [HND-012]
""" * 1  # ~600 chars → ~150 tokens

_DEPENDENCIES_SLIM = """T-002: done ✓ Setup Redis session store
""" * 1  # ~80 chars → ~20 tokens

_HANDOVER_LIST_TASK_SLIM = """HND-012 ▸ T-001: "rewriting auth middleware — next: backfill migration" [open]
""" * 1  # ~150 chars → ~37 tokens

_LESSON_MATCH_SLIM = """L-007 (gotcha): auth/session.ts — read before edit, never patch blindly
L-014 (anti-pattern): avoid raw SQL in auth handlers
L-022 (pattern): test names must include scenario + expected outcome
""" * 1  # ~360 chars → ~90 tokens

_ISSUE_LIST_TASK_SLIM = """ISS-007 (P1, open): Login accepts whitespace-only passwords
""" * 1  # ~80 chars → ~20 tokens

_LINKAGE_PILLS = "depends_on: T-002 · fixes: ISS-007 · informed_by: L-003\n"
# ~55 chars → ~14 tokens

_CHARS_PER_TOKEN = 4


def _token_estimate(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN


def test_glance_skill_body_budget():
    """SKILL.md body alone must be ≤1,300 tokens."""
    text = SKILL_MD.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.index("---", 3)
        body = text[end + 3:].strip()
    else:
        body = text.strip()
    tokens = _token_estimate(body)
    assert tokens <= 1_300, f"SKILL.md body ~{tokens} tokens; limit 1,300"


def test_glance_mcp_payload_budget():
    """Sum of slim MCP payloads for the pick-task glance path must be ≤800 tokens."""
    total_chars = (
        len(_GET_TASK_SLIM)
        + len(_DEPENDENCIES_SLIM)
        + len(_HANDOVER_LIST_TASK_SLIM)
        + len(_LESSON_MATCH_SLIM)
        + len(_ISSUE_LIST_TASK_SLIM)
        + len(_LINKAGE_PILLS)
    )
    tokens = total_chars // _CHARS_PER_TOKEN
    assert tokens <= 800, (
        f"Glance MCP payload ~{tokens} tokens; limit 800. "
        "Check Plan A slim-mode response sizes for pick-task tools."
    )


def test_combined_glance_budget():
    """Skill body + MCP payloads combined must be ≤2,100 tokens."""
    text = SKILL_MD.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.index("---", 3)
        body = text[end + 3:].strip()
    else:
        body = text.strip()
    mcp_chars = (
        len(_GET_TASK_SLIM)
        + len(_DEPENDENCIES_SLIM)
        + len(_HANDOVER_LIST_TASK_SLIM)
        + len(_LESSON_MATCH_SLIM)
        + len(_ISSUE_LIST_TASK_SLIM)
        + len(_LINKAGE_PILLS)
    )
    total_tokens = _token_estimate(body) + (mcp_chars // _CHARS_PER_TOKEN)
    assert total_tokens <= 2_100, (
        f"Combined glance budget ~{total_tokens} tokens; limit 2,100."
    )
