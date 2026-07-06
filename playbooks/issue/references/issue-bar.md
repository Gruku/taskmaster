# Issue Bar — Criteria, Evidence, Anti-Examples

An Issue is the elevated tier. The bar prevents over-eager filing of one-off defects (which belong in `taskmaster:bug`).

## The three criteria (any one is enough)

### 1. Recurring

≥2 prior occurrences cited concretely. Each citation must be an addressable artifact: a `B-NNN`, a `T-NNN`, a handover ID, or a session reference. Hand-waving like "I've seen this before" does NOT count.

**Good evidence:**
> Recurring: matches B-018 (same null-handler defect in handover reader) and B-031 (same in lesson reader). Both fixed independently; root cause now confirmed as the shared loader.

**Bad evidence (route to Bug):**
> Recurring: I think this has come up before.

### 2. Systemic

≥2 affected components named, OR a class-of-defect description that names the pattern across the surface.

**Good evidence:**
> Systemic: path-resolver mismatch affects handover reader, lesson reader, issue reader. Class-of-defect: every `Path(".taskmaster")` literal in `taskmaster_v3.py` and `backlog_server.py`.

**Bad evidence (route to Bug):**
> Systemic: it's in a few places.

### 3. Outstanding

P0 or P1 with concrete blast-radius evidence: data loss, security exposure, prod outage, or complete block of a core user flow.

**Good evidence:**
> Outstanding: P0 — running the migration on stage drops ALL rows in `chat_messages` (verified via `count(*)` before and after).

**Bad evidence (route to Bug):**
> Outstanding: feels critical, looks bad.

## Severity as sort hint only

Severity (P0–P3) feeds aging windows and dashboard sort. But severity does NOT, by itself, qualify a defect as an Issue. A P2 must also be recurring or systemic.

| Severity | Window | Note |
|---|---|---|
| P0 / Critical | 14 days | ALWAYS justified by "outstanding" criterion |
| P1 / High | 30 days | Usually outstanding; recurring also valid |
| P2 / Medium | 60 days | Must ALSO be recurring or systemic — severity alone is NOT enough |
| P3 / Low | 120 days | Must ALSO be recurring or systemic — severity alone is NOT enough |

## Anti-example: `ISS-015` (the canonical "this is a Bug")

The original entry:
> Handover status defaults to "open" but viewer expects "todo". Viewer has `|| 'todo'` fallback so filter still works; visual defect is the unstyled status pill (one undefined CSS class).

Walk the bar:
- **Recurring?** No — single location, no prior occurrences cited.
- **Systemic?** No — one file, one default, one component.
- **Outstanding?** No — cosmetic, no functional impact (the fallback protects the filter).

Verdict: **Bug**, severity P3 if any. Not an Issue.

## When unsure, file a Bug

Promotion (B → ISS) is one cheap call: `backlog_bug_promote(bug_ids=[B-NNN], evidence_text=...)`. Demotion (ISS → B) requires an explicit migration. So when in doubt: Bug.
