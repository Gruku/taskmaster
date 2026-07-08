"""Smoke test: start-session glance path token budget.

Simulates MCP tool calls the glance flow triggers, counts total tokens, asserts budget.
Uses fake minimal responses sized to realistic slim-mode payloads.
"""
from pathlib import Path

SKILL_MD = (
    Path(__file__).resolve().parents[1] / "skills" / "start-session" / "SKILL.md"
)

# Realistic slim-mode mock payloads (as strings, length/4 = token estimate).
# Each reflects what Plan A slim tools would return.

_BACKLOG_STATUS_SLIM = """**Schema:** v3
**Phase:** Development (3/8 tasks done)
**In progress:** T-001 Rewrite auth middleware · T-003 Add rate limiting
**In review:** T-007 Fix token refresh
**Stale tasks (14d+):** T-009 SAML support (stale 21d)
**Counts:** 12 tasks · 3 in-progress · 1 in-review · 4 todo · 2 done
**Open issues:** 3 (1 P0) · **Flagged handovers:** 1
""" * 1  # ~380 chars → ~95 tokens

_HANDOVER_LIST_SLIM = """HND-012 ▸ T-001: "rewriting auth middleware — next: backfill migration" [open]
HND-010 ▸ T-003: "rate limiting implemented — next: add integration test" [open]
HND-008 ▸ T-007: "token refresh fix pending review" [flagged: T-007 done but handover open ▸ next_action references T-005] [open]
""" * 1  # ~600 chars → ~150 tokens

_COUNTS_LINE = "3 new issues (1 P0) · 1 stale task · 1 flagged handover\n"
# ~60 chars → ~15 tokens

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
    """Sum of slim MCP payloads for the glance path must be ≤1,000 tokens."""
    total_chars = (
        len(_BACKLOG_STATUS_SLIM)
        + len(_HANDOVER_LIST_SLIM)
        + len(_COUNTS_LINE)
    )
    tokens = total_chars // _CHARS_PER_TOKEN
    assert tokens <= 1_000, (
        f"Glance MCP payload ~{tokens} tokens; limit 1,000. "
        "Check Plan A slim-mode response sizes."
    )


def test_combined_glance_budget():
    """Skill body + MCP payloads combined must be ≤2,300 tokens (≤1,300 + ≤1,000)."""
    text = SKILL_MD.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.index("---", 3)
        body = text[end + 3:].strip()
    else:
        body = text.strip()
    mcp_chars = len(_BACKLOG_STATUS_SLIM) + len(_HANDOVER_LIST_SLIM) + len(_COUNTS_LINE)
    total_tokens = _token_estimate(body) + (mcp_chars // _CHARS_PER_TOKEN)
    assert total_tokens <= 2_300, (
        f"Combined glance budget ~{total_tokens} tokens; limit 2,300."
    )
