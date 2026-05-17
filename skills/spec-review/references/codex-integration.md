# Spec Review — Codex Adversarial Pass (Gate C)

Gate C is the Codex precision knife for spec-level blind spots. Skip silently if Codex isn't detected and not requested.

Codex detection: `~/.claude/plugins/cache/openai-codex/` exists OR `codex` is on PATH.

Default behavior:
- `critical`: auto-suggest, ask user before running
- `high`: offer if Codex detected, default off
- `medium` / `low`: never run

If `--codex` passed but Codex isn't available, WARN and continue without it.

## Constraints

- `/codex:adversarial-review` is **diff-only** and won't work on prose — do NOT use it here.
- Instead, dispatch the `codex:codex-rescue` subagent with explicit review-only framing.

## Dispatch Pattern

Build a focus from what Gate B *didn't* flag (avoids paying for a duplicate pass):

```
Agent({
  subagent_type: "codex:codex-rescue",
  description: "Codex adversarial spec review",
  prompt: "REVIEW-ONLY. Do not write or modify code. Do not propose patches.

Read the spec at <abs path to spec> and the surrounding codebase. Produce
an adversarial design review challenging the chosen approach.

Claude's own review already flagged: <bullet list of Gate B findings>.

Look specifically for blind spots Claude may have missed:
- Repeated bugs in this area that the spec doesn't address.
- Implicit assumptions about <area-specific concerns>.
- Cross-codebase patterns that suggest this approach has been tried/rejected before.
- Edge cases in <specific functions or flows> that the spec glosses over.

Return a concise list of issues with file:line citations. Group by Critical /
Important / Minor. Do not summarize Claude's findings — only add new ones."
})
```

## Merging into Report

Tag Codex findings with `(codex)` so the source is visible when merged into the gate matrix.
