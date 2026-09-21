# User intent: one call that answers "what do I need to know to work on X",
# bounded by bytes, so an agent orients without reading the whole backlog — and
# never learns it is clear when a mandatory producer could not answer.
"""The shared shape of a bounded context answer: vocabulary, continuation, assembly.

Pure: no sqlite handle is stored here and no store is assumed. Both the native
`Snapshot.context` and the legacy implementation of `backlog_context` gather their
own rows and then come through this module, exactly as `cursors` serves the change
feed on both stores (D8: the tools are dual, the presentation is shared). Two paths
that rendered their own answers would drift on the one thing that must not drift —
what `clear` means and what a byte budget counts.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json

from . import blockers, budget
from .migrate import encode

VERSION = 1
MAX_LENGTH = 4096

SCOPES = ("task", "session", "project")
# Fixed, like the mandatory set: a section name a caller invents cannot be served,
# and a section that quietly renamed itself would break a skill silently.
SECTIONS = ("spec", "plan", "body", "links", "handovers", "bugs", "issues",
            "notes", "dependencies", "siblings", "recent")
# Sections that describe one task. Without a focus they are empty rather than
# re-pointed at something else — a "dependencies" list of the whole project would
# answer a question nobody asked.
FOCUS_SECTIONS = frozenset({"spec", "plan", "body", "links", "dependencies", "siblings"})
DOCUMENT_SECTIONS = frozenset({"spec", "plan"})

DEFAULT_INCLUDE = {
    "task": ("dependencies", "handovers", "bugs", "links", "body"),
    "session": ("dependencies", "handovers", "bugs", "notes", "recent"),
    "project": ("handovers", "issues", "notes", "recent"),
}

# Rows read per section per page. The budget decides what is *returned*; this
# bounds what is *read*, so a section with ten thousand rows still costs one
# indexed page. The omission count never comes from here — it is a `COUNT(*)`.
PAGE = 50
# `recent` is inherently a top-N question, so its total is the cap: "of the 20
# most recent, 15 were omitted" is a number a caller can act on, where "of 3,412
# entities" is not.
RECENT = 20
MIN_BUDGET, MAX_BUDGET = 1, 1 << 20


class CursorInvalid(ValueError):
    """Start a new page; this continuation no longer describes this question."""


def check_scope(scope):
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {', '.join(SCOPES)}")
    return scope


def check_include(include, scope):
    """The selected sections, validated against the fixed vocabulary.

    `include` never governs mandatory content — it selects what is *added* to it —
    so an empty list is a legitimate request for blockers alone.
    """
    if include is None:
        return DEFAULT_INCLUDE[scope]
    if isinstance(include, str) or not isinstance(include, (list, tuple)):
        raise ValueError("include must be a list of section names")
    unknown = [name for name in include if name not in SECTIONS]
    if unknown:
        raise ValueError(f"include names unknown sections: {', '.join(map(str, unknown))}; "
                         f"expected any of {', '.join(SECTIONS)}")
    return tuple(dict.fromkeys(include))


def check_budget(budget_bytes):
    if type(budget_bytes) is not int or not MIN_BUDGET <= budget_bytes <= MAX_BUDGET:
        raise ValueError(f"budget_bytes must be an integer from {MIN_BUDGET} to {MAX_BUDGET}")
    return budget_bytes


# The fence is one digest rather than the tuple it is taken over, and a short one.
# A context cursor is spent out of the caller's own byte budget, so every byte it
# carries is a row it does not get; 128 bits is far past the point where two
# different questions could collide, and nothing here needs to be read back.
FENCE = 32


def identity(*, store_id, source_digest, sequence, scope, focus, include):
    """What a continuation is fenced on, as one opaque digest.

    Unlike the change cursor, this one *does* bind to the store's sequence: a
    context page is a paged snapshot read, and half of it taken before a write
    and half after would describe a project that never existed.
    """
    question = encode([str(store_id), str(source_digest), int(sequence),
                       "context", scope, focus or "", list(include)])
    return hashlib.sha256(question.encode()).hexdigest()[:FENCE]


def issue(ident, offsets):
    if not offsets:
        return ""
    payload = {"v": VERSION, "q": ident,
               "a": [[name, int(offsets[name])] for name in SECTIONS if name in offsets]}
    return base64.urlsafe_b64encode(encode(payload).encode()).decode()


def parse(cursor, ident):
    """The per-section offsets this continuation resumes from, or a refusal.

    A context cursor is refused rather than answered with a resync: unlike the
    change feed, there is no safe "start from now" for a half-read page — silently
    restarting it would hand the caller the first rows again as if they were new.
    """
    if not cursor:
        return {}
    if not isinstance(cursor, str) or len(cursor) > MAX_LENGTH:
        raise CursorInvalid("context cursor is missing, oversized or not a string")
    try:
        payload = json.loads(base64.b64decode(cursor.encode(), altchars=b"-_", validate=True))
        if payload["v"] != VERSION or payload["q"] != ident:
            raise ValueError("cursor identity")
        offsets = {}
        for entry in payload["a"]:
            name, value = entry
            if name not in SECTIONS or type(value) is not int or value < 0:
                raise ValueError("cursor offsets")
            offsets[name] = value
    except (ValueError, TypeError, KeyError, binascii.Error, UnicodeError):
        raise CursorInvalid("this context question, or the store, moved on since the "
                            "cursor was issued; ask again without it") from None
    return offsets


def no_focus() -> blockers.Blocker:
    """The blocker a context answer carries when there is no task to check.

    Not an empty blocker list: `clear` is a safety claim, and with no focus no
    mandatory producer ran at all.
    """
    return blockers.Blocker(kind=blockers.UNKNOWN_KIND, id="focus", state="unknown",
                            source="resolver",
                            extra={"reason": "no_focus_task",
                                   "detail": "no task is in focus, so nothing was checked"})


def source(name, focus):
    """Which question a section answered, as its provenance reports it.

    Spelled as the native store's own predicate, because that is the precise
    statement of the question; the legacy store answers the same question over its
    loaded documents. It names the predicate, never the storage the rows came from.
    """
    if name in FOCUS_SECTIONS and not focus:
        return "no_focus"
    return {"spec": "external_documents", "plan": "external_documents",
            "body": "entity_documents.body", "links": "declared_links.links",
            "handovers": "memberships.task_ids", "bugs": "extensions.found_in",
            "issues": "entity_core.status", "notes": "note_operational.pinned",
            "dependencies": "dependencies.depends_on", "siblings": "task_operational.epic",
            "recent": "entity_core.last_seq"}[name]


def assemble(*, store_id, sequence, scope, focus, resolution, selections, offsets,
             ident, budget_bytes, provenance):
    """One context answer, as the exact text the caller receives.

    Two passes, because the continuation lives inside the answer whose bytes it is
    counted against. The first pass budgets against the *widest* cursor this page
    could possibly issue — every section resumed at its own total — so the real
    cursor, which names a subset of those sections at smaller offsets, is never
    longer. The second pass re-renders the rows the first chose with the real
    cursor in place, and its `used_bytes` is therefore the bytes actually returned
    rather than an estimate of them.

    `over_budget` is decided by the second pass and means exactly one thing: the
    mandatory half alone does not fit. A page that fits but selected nothing —
    because the room left after the blockers could not hold even one row — is not
    over budget; its omission counts say so instead.
    """
    envelope = {"store_id": store_id, "sequence": int(sequence), "scope": scope,
                "focus": focus or None, "provenance": provenance}
    mandatory = resolution.as_dict()
    widest = issue(ident, {s.name: s.total for s in selections if s.total})
    first = budget.budget(envelope={**envelope, "cursor": widest}, mandatory=mandatory,
                          selections=selections, limit_bytes=budget_bytes)
    taken = {name: len(rows) for name, rows in first.selected.items()}
    if not any(taken.values()):
        # Reserving room for a continuation cost this page every row it had. If
        # the whole selection fits once that reserve is released, no continuation
        # was ever needed and the reserve was the only thing in the way.
        whole = budget.budget(envelope={**envelope, "cursor": ""}, mandatory=mandatory,
                              selections=selections, limit_bytes=budget_bytes)
        held = {name: len(rows) for name, rows in whole.selected.items()}
        if all(offsets.get(s.name, 0) + held.get(s.name, 0) >= s.total for s in selections):
            return whole.text
    remaining = {s.name: offsets.get(s.name, 0) + taken.get(s.name, 0) for s in selections
                 if offsets.get(s.name, 0) + taken.get(s.name, 0) < s.total}
    # A page that delivered nothing cannot be continued: its cursor would name the
    # same offsets again and the caller would loop on one answer forever.
    cursor = issue(ident, remaining) if any(taken.values()) else ""
    chosen = [budget.Selection(s.name, list(s.items)[:taken.get(s.name, 0)], s.total)
              for s in selections]
    return budget.budget(envelope={**envelope, "cursor": cursor}, mandatory=mandatory,
                         selections=chosen, limit_bytes=budget_bytes).text


def refusal(exc):
    """A refused context question, in the JSON shape this tool's answers take."""
    return json.dumps({"error": str(exc)}, ensure_ascii=False)
