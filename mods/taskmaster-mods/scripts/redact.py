# User intent: keep real Taskmaster replies usable as committed test fixtures without leaking backlog content — every word
# that is not format structure becomes x's of the same length, so ids, statuses, punctuation and line shapes survive.
from __future__ import annotations

import json
import re
import unicodedata

KEEP = frozenset("""
tasks task showing first more pass status epic phase filters or limit for all no found matching any waiting on human
tldr next step priority estimate bundle component design change lane gate state area skip merge freshness depends
related issues issue started completed branch worktree blockers action autogen docs available open handovers handover
links link pipeline outstanding none ready done laneless pre protocol enforced pending skipped error not todo in progress
review blocked archived critical high medium low full light spec plan code record verdict fail warn true false null view
total truncated items id type title where class timestamp age days decide resume clean up ambient decision idea date
created thread session kind tip commit target superseded by returned omitted seq export warning updated field cannot
complete expected one of picked locked to this already ok holder expires at live expired released the an is and with
""".split())
# Ids, dates and commit SHAs survive when they stand alone. The guards are Unicode-aware (\w) and include `-`, so an
# id-shaped run inside a longer slug (`2026-06-10-token-diet-shipped-tm-3-16-0`, `écrit-12`) is content, not an id. A SHA
# needs a digit, so an all-hex English word (`defaced`) is still a word.
ID = re.compile(
    r"(?<![\w-])(?:[a-z][a-z0-9]*(?:-[a-z0-9]+)*-\d{2,4}|(?:B|DEC|ISS|IDEA|N)-\d+|P[0-3]"
    r"|\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?|(?=[a-f]*\d)[0-9a-f]{7,40})(?![\w-])"
)
APOSTROPHES = frozenset("'’")
JSON_KEEP = frozenset({"task_id", "task_ids", "type", "status", "action_class", "timestamp", "created", "date", "session_kind",
                       "tip_commit", "view", "age_days", "returned", "total", "truncated", "archived_omitted",
                       "ok", "state", "seq", "live", "expired", "expires_at"})
# Not kept verbatim although they look structural: `superseded_by` holds a handover id (a free-text slug) and `error` can
# carry a path or a title; both go through redact_text, which still keeps their dates, ids and format words.
ID_KEYS = frozenset({"task_id", "task_ids"})
# A slug id: its prefix (the epic or project name) and its numeric tail (`sp-audit-0719-001` → `sp-audit`, `-0719-001`).
SLUG_ID = re.compile(r"([a-z][a-z0-9]*(?:-[a-z0-9]+)*?)((?:-\d[0-9a-z]*)+)")


class IdPseudonyms:
    """One capture's mapping from real id prefixes to neutral ones — epic-a, epic-b, … in first-seen order — so a work
    backlog's project and epic names never reach a fixture while every id keeps its numbers and stays consistent."""

    def __init__(self, keep: tuple[str, ...] = ()) -> None:
        # `keep`: prefixes that are ours, not the backlog's (the capture's `zz-missing` probe id), left as they are.
        self._keep = frozenset(keep)
        self._names: dict[str, str] = {}

    def _name(self, prefix: str) -> str:
        if prefix in self._keep:
            return prefix
        if prefix not in self._names:
            n, letters = len(self._names), ""
            while True:
                n, r = divmod(n, 26)
                letters = chr(ord("a") + r) + letters
                if n == 0:
                    break
                n -= 1
            self._names[prefix] = f"epic-{letters}"
        return self._names[prefix]

    def __call__(self, token: str) -> str:
        m = SLUG_ID.fullmatch(token)
        return token if m is None else self._name(m.group(1)) + m.group(2)


def _is_letter(ch: str) -> bool:
    return unicodedata.category(ch).startswith("L")


def _continues_word(ch: str) -> bool:
    return unicodedata.category(ch)[0] in "LM" or ch in APOSTROPHES


def _words(chunk: str) -> str:
    """Every word (a letter of any script, then letters, combining marks or apostrophes) not in KEEP becomes x's."""
    out: list[str] = []
    i, n = 0, len(chunk)
    while i < n:
        if not _is_letter(chunk[i]):
            out.append(chunk[i])
            i += 1
            continue
        j = i + 1
        while j < n and _continues_word(chunk[j]):
            j += 1
        word = chunk[i:j]
        out.append(word if word.lower() in KEEP else "x" * len(word))
        i = j
    return "".join(out)


def _keep_id(token: str, ids: IdPseudonyms | None) -> str:
    # Only slug ids carry a name; dates, SHAs and B-/DEC-/ISS-/IDEA-/N- ids are kept as they are.
    return ids(token) if ids is not None and token[:1].islower() and "-" in token else token


def redact_text(text: str, ids: IdPseudonyms | None = None) -> str:
    out: list[str] = []
    pos = 0
    for m in ID.finditer(text):
        out.append(_words(text[pos:m.start()]))
        out.append(_keep_id(m.group(), ids))
        pos = m.end()
    out.append(_words(text[pos:]))
    return "".join(out)


def _redact_json(value, key=None, ids: IdPseudonyms | None = None):
    if isinstance(value, dict):
        return {k: _redact_json(v, k, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_json(v, key, ids) for v in value]
    if isinstance(value, str):
        if ids is not None and key in ID_KEYS:
            return ids(value) if SLUG_ID.fullmatch(value) else redact_text(value, ids)
        return value if key in JSON_KEEP else redact_text(value, ids)
    return value


def redact_reply(text: str, ids: IdPseudonyms | None = None) -> str:
    """With `ids`, every slug id's prefix is also pseudonymised through that one capture-wide mapping."""
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            return json.dumps(_redact_json(json.loads(stripped), None, ids), ensure_ascii=False, indent=2)
        except ValueError:
            pass
    return redact_text(text, ids)
