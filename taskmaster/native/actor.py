# User intent: one definition of the command actor {handle, agent, clone_id}: its shape in envelopes, receipts and events, and the agent slugs, shared by the contracts, the coordinator's resolver and the MCP server.
"""The actor of a native command (team-identity-002)."""
from __future__ import annotations

KEYS = ("handle", "agent", "clone_id")
AGENTS = frozenset({"claude", "codex", "taskmaster"})   # "" means unknown or none
INTERNAL_AGENT = "taskmaster"
MAX_FIELD = 128


def make(handle: str = "", agent: str = "", clone_id: str = "") -> dict:
    return {"handle": handle, "agent": agent, "clone_id": clone_id}


def check(value) -> None:
    if (not isinstance(value, dict) or not set(value) <= set(KEYS)
            or not all(isinstance(item, str) and len(item) <= MAX_FIELD for item in value.values())):
        raise ValueError("invalid actor")


def normalize_agent(value) -> str:
    return value if isinstance(value, str) and value in AGENTS else ""


def agent_for_client(name) -> str:
    if name == "claude-code":
        return "claude"
    if name == "codex" or (isinstance(name, str) and name.startswith("codex-")):
        return "codex"
    return ""


def advisory(value) -> dict:
    return {"agent": value}
