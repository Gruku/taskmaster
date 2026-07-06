# Lesson Candidate Marker — XML Format

Mid-session, Claude flags lesson candidates with an inline XML tag. **No tool call is made** — the tag is plain text in Claude's response. End-session and disk-transcript scans recover the tags later.

## Tag schema

```xml
<lesson-candidate kind="gotcha" topic="multi-tab fanout" scope="point">
useEffect reading useLocation().state without active-tab guard.
Recurred on cb6927c0; fix is chatId === activeTabId early-return.
</lesson-candidate>
```

| Attr | Required | Values | Default | Purpose |
|------|----------|--------|---------|---------|
| `kind` | optional | `pattern` \| `anti-pattern` \| `gotcha` | (none) | Pre-fills the lesson `kind`; lets the sweep filter. |
| `topic` | optional | one-line free string | (none) | Grouping handle; one-word handle preferred. |
| `scope` | optional | `point` \| `session` | `point` | `session` flags the active handover for retro-extraction (see session-retro.md). |

**Body:** 1–3 sentences — what it is and why it matters. No file paths or commits required at the time of emit; auto-extraction fills those later.

**Attrs are optional.** An attrless `<lesson-candidate>...</lesson-candidate>` is valid — it scans as `kind=""`, `topic=""`, `scope="point"` (defaults). The sweep will prompt for `kind` during review since it can't be inferred from the tag alone. Prefer including at least `kind` when you have it.

## Grep-optimized conventions

These conventions exist so a literal `grep '<lesson-candidate '` over chat logs Just Works. Follow them exactly:

- **Stable opening anchor**: `<lesson-candidate ` (one trailing space). Never abbreviated, never variant.
- **Tags on their own lines**: opening + closing tags get dedicated lines. Body can be multi-line.
- **No nesting**: a candidate never contains another candidate.
- **Attrs are double-quoted**: `kind="gotcha"`, never `kind='gotcha'`, never `kind=gotcha`.

The backend regex (`scan_transcripts_for_candidates` in `taskmaster_v3.py`) anchors on the literal opening string and parses attrs with `(\w+)="([^"]*)"`. Single-quoted or unquoted attrs are silently skipped.

## When to emit a tag

Heuristic for emit, not a hard rule. Silence is the default when uncertain. Flag a candidate when ANY of:

1. **User correction repeats** — same correction made earlier this session, OR a feedback-memory entry matches a code pattern Claude just produced and got corrected on.
2. **Bug second-encounter** — Claude is debugging an issue and notices the resolution path matches an issue debugged earlier (same root cause shape).
3. **Architectural ground rule emerges** — user states a project rule conversationally ("we always X here", "never Y in this codebase") not yet captured as a lesson.

When in doubt, do not emit. Over-flagging trains the user to ignore the tags.

## Compaction handling

The risk: when Claude `/compact`s, the literal `<lesson-candidate>` tags in past turns get summarized away. Three defenses, in order:

1. **PreCompact hook (v3-skills-006, future)** — hook scans the about-to-be-compacted transcript for tags and persists new ones to `_candidates.md` before compaction completes. Durable defense; not yet shipped.
2. **Disk-transcript fallback** — `backlog_lesson_candidates_scan(days=7)` re-reads the raw `.jsonl` (which is uncompacted on disk) and recovers tags. **This is the only recovery path until the PreCompact hook ships.** Invoke explicitly when end-session detects compaction happened mid-session.
3. **Soft "important" defer** — when Claude emits a high-value tag (rare), it MAY also call `backlog_lesson_candidate_defer` immediately. Most candidates skip this — text-only is the default and zero-cost.

## Choosing `scope`

- `scope="point"` (default): a single concrete lesson (a gotcha, a pattern, an anti-pattern). The sweep proposes one lesson from this tag.
- `scope="session"`: the *whole session* is interesting and worth retro-extracting later. The sweep does NOT propose a single lesson — instead, it stamps the next handover with `flag_for_review: true`. Days or weeks later, the user can run `taskmaster:lesson` with that handover id to batch-extract candidates from the session's commits + transcript.

If you can name one specific gotcha, use `point`. If the value is "this whole session was a learning experience and I'll want to come back to it", use `session`.

## `<idea-candidate>` (sister tag)

Mirrors `<lesson-candidate>` but for ideas — lightweight thoughts, parking-lot items, future-work observations not yet ready to be a task.

### Schema

```xml
<idea-candidate title="<short title>" tags="<comma-separated>" status="<freeform>" related-task="<task-id>">
Optional one-paragraph context. Keep it short — the body of the resulting IDEA-NNN.md will be roughly this paragraph plus any quoted user phrasing that triggered the tag.
</idea-candidate>
```

### Attributes

- `title` (required) — short title for the idea (becomes IDEA-NNN.md title field)
- `tags` (optional) — comma-separated freeform tags
- `status` (optional) — freeform status. End-session sets `status="candidate"` automatically when committing tags it found, so don't pre-fill it unless you have a specific value in mind ("parking-lot", "exploring", etc.)
- `related-task` (optional) — task id this idea attaches to (e.g. the active task at the moment of emission)
- `related-issue`, `related-lesson` — same pattern, optional

### When to emit

When the user expresses an *ambient* idea — hedged ("hmm could be cool…"), tangential to the current task, low confidence. End-session sweeps these tags and commits each as IDEA-NNN.md with `status="candidate"`.

If the idea is *sharp* (explicit framing, direct request, concrete-and-named), do NOT emit a tag — call `backlog_idea_create` directly and announce inline. See the start-session skill's "Mid-session behavior" section for the heuristic.
