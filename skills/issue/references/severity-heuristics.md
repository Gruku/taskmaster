# Issue Severity Heuristics

Severity is a commitment, not a label. It drives the fix-window target and dashboard sort order (P0 floats to the top of every list). Get it right at triage time — reclassify immediately if new information changes the blast radius.

## Decision flowchart

Work through these questions in order. Stop at the first rule that fires.

1. **Is there data loss, a security exposure, a crash on startup, or a complete prod outage with no workaround?**
   → P0. Escalate immediately. Do not log and forget.

2. **Does the bug block a core user flow entirely, with no workaround?** Or is it a regression that was working in a prior prod release?
   → P1. Needs a fix within 30 days; do not let it sit in the backlog untouched.

3. **Is the bug functional (the system behaves incorrectly) but a workaround exists?** Or is it a non-critical regression in a secondary flow?
   → P2. Plan a fix; don't block other work on it.

4. **Is the bug cosmetic, edge-case, or a nice-to-have correction?**
   → P3. Log it and handle when bandwidth allows.

When uncertain between two levels, ask the user before filing. Never silently assign P0.

## Fix-window targets

| Severity | Label | Fix target |
|---|---|---|
| P0 | Critical | ≤14 days |
| P1 | High | ≤30 days |
| P2 | Medium | ≤60 days |
| P3 | Low | ≤120 days |

These targets feed `compute_issue_aging` in `taskmaster_v3.py`, which computes how far through the fix window an issue has traveled. Dashboard renderers use `aging_cfg` keyed by severity label to get `base_days`.

## Aging tiers

An issue's age is expressed as a percentage of its severity window:

| Tier | Percent of window elapsed | Meaning |
|---|---|---|
| Fresh | < 25% | On track; no urgency signal |
| Aging | 25% – 59% | Approaching; worth reviewing |
| Stale | ≥ 60% | Overdue; surface prominently in triage |

Stale P0s should be escalated to the user immediately when seen in `triage-review`. Stale P1s warrant an explicit "this is overdue" call-out during triage.

## Examples (from real CodeMaestro patterns)

| Severity | Example | Why |
|---|---|---|
| P0 | Server crash on startup when config file is missing | No startup = no users; no workaround |
| P1 | Multi-tab fanout: `initialMessage` useEffect fires in every `ChatWindow` without active-tab guard — third regression | Blocks core chat flow; no workaround; confirmed regression from prior fix |
| P1 | Orphan route: `ProjectMemoriesAndRules.tsx` navigates to `/welcome` (unrouted) — dispatch silently dropped | Core init flow silently broken; no user-visible error |
| P2 | Parallel tool-call orphans: `_pending_tools` keyed by `tool_name` causes N identical tool calls to collapse — 2 phantom ENDs | Functional defect in parallel tool dispatch; workaround is to avoid parallel calls to same tool |
| P3 | Dashboard column headers misalign by 1px when sidebar is collapsed | Visual only; no functional impact |

## When to ask vs. infer

Infer from language: "app crashes", "data is gone", "users are blocked" → P0/P1. "breaks X when Y", "wrong result", "unexpected behavior" → P1/P2. "looks off", "minor annoyance" → P2/P3.

Ask the user when: the impact scope is unclear ("it breaks for some users — how many?"), or when you would assign P0 based on phrasing but have no direct evidence of a prod outage or data loss.

The cost of a wrong P0 is high: it triggers urgency signals and inflates the Stale counter quickly. A cautious P1 that gets promoted to P0 after investigation is always better than an incorrect P0 assigned too early.
