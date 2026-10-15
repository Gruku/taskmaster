# Issue bar: criteria, evidence, anti-example

This is the single home of the bar. An Issue is the elevated tier; the bar keeps one-off defects (which belong in `taskmaster:bug`) out of it. Any one criterion is enough.

## The three criteria

### 1. Recurring

Two or more prior occurrences, each cited as an addressable artifact: a `B-NNN`, a `T-NNN`, a handover id or a session reference. "I've seen this before" doesn't count.

- Good: *Recurring: matches B-018 (same null-handler defect in handover reader) and B-031 (same in idea reader). Both fixed independently; root cause now confirmed as the shared loader.*
- Bad, so file a Bug: *Recurring: I think this has come up before.*

### 2. Systemic

Two or more affected components named, or a class-of-defect that names the pattern across the surface.

- Good: *Systemic: path-resolver mismatch affects handover reader, idea reader, issue reader. Class of defect: every `Path(".taskmaster")` literal in `taskmaster_v3.py` and `backlog_server.py`.*
- Bad, so file a Bug: *Systemic: it's in a few places.*

### 3. Outstanding

P0 or P1 with concrete blast radius: data loss, security exposure, prod outage, or a complete block of a core user flow.

- Good: *Outstanding: P0. Running the migration on stage drops all rows in `chat_messages` (verified via `count(*)` before and after).*
- Bad, so file a Bug: *Outstanding: feels critical, looks bad.*

## Severity is a sort hint

Severity (P0 to P3) drives the aging window and the dashboard sort. It doesn't qualify a defect by itself: a P2 or P3 Issue must also be recurring or systemic.

| Severity | Window | Note |
|---|---|---|
| P0 / Critical | 14 days | Justified by the outstanding criterion |
| P1 / High | 30 days | Usually outstanding; recurring also works |
| P2 / Medium | 60 days | Also needs recurring or systemic |
| P3 / Low | 120 days | Also needs recurring or systemic |

## Anti-example: ISS-015

> Handover status defaults to "open" but viewer expects "todo". Viewer has `|| 'todo'` fallback so filter still works; visual defect is the unstyled status pill (one undefined CSS class).

Not recurring (single location, no prior occurrences), not systemic (one file, one default, one component), not outstanding (cosmetic, and the fallback protects the filter). It is a Bug, severity P3 if any.

## When unsure, file a Bug

Promoting a Bug is one call: `backlog_bug_promote(bug_ids=["B-NNN"], title=..., severity=..., evidence_text=...)`. Demoting an Issue needs an explicit migration.
