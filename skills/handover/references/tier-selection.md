# Handover Tier Selection

Three body-template tiers. Pick one based on the heuristic below. The user can force a tier with `--light`, `--standard`, or `--full`; respect the override.

## Tier sizes (rough targets)

| Tier | Lines | When to use |
|---|---|---|
| `light` | 10–30 | `end-of-day` or `exploration` with ≤ 30 turns and no in-flight task. |
| `standard` | 60–130 | Default for `milestone-complete` and `context-handoff`. Or any session 30–100 turns with an in-flight task. |
| `full` | 150–200 | `pivot`. Or any session > 100 turns. Or > 200k tokens. Or audit handovers that need full dispatch templates. |

## Selection flow

1. **User passed `--light` / `--standard` / `--full`** → use it. Stop.
2. **`session_kind == "auto-stage"`** → use `light` (frontmatter is the only thing that gets loaded anyway).
3. **`session_kind == "context-handoff"`** → use `standard` (body loaded; default detail level).
4. **`session_kind == "milestone-complete"`** → use `standard` unless the session has > 100 turns or > 200k tokens, then `full`.
5. **`session_kind == "pivot"`** → use `full` (the *why* is load-bearing).
6. **`session_kind == "exploration"`** → use `light`.
7. **`session_kind == "end-of-day"`**:
    - ≤ 30 turns, no in-flight task → `light`
    - 30–100 turns, in-flight task → `standard`
    - \> 100 turns or > 200k tokens → `full`

## Token-count and turn-count estimation

These are heuristics, not exact:

- **Turn count** — count user/assistant message pairs in the current conversation. The orchestrator passes this number in if it knows; otherwise estimate from the conversation length.
- **Token count** — `len(conversation_text) / 4` is a rough byte-to-token ratio. Good enough for the > 200k threshold; do not promise precision.

## What `--light` doesn't change

A `--light` override on a heavy session trims the **output**, not the **inputs**. Auto-extraction still runs, supersession still chains; we just write a shorter body. This avoids losing information the user explicitly opted out of writing down.
