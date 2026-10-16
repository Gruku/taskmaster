# Spec Review: Codex Adversarial Pass (Gate C)

Gate C is the Codex pass for spec-level blind spots. Skip silently when Codex isn't detected and wasn't requested.

Codex is detected when `~/.claude/plugins/cache/openai-codex/` exists or `codex` is on PATH. When `--codex` was passed but Codex isn't available, say so in one line and continue without it.

Default by task priority:
- `critical`: suggest it and ask before running
- `high`: offer it when Codex is detected, default off
- `medium` / `low`: don't run

## Constraint

`/codex:adversarial-review` works on diffs only and can't review prose, so it doesn't fit a spec. Dispatch the Codex rescue sub-agent with an inspect-and-report brief instead.

## Dispatch pattern

Build the focus from what Gate B didn't flag, so the pass doesn't duplicate your review. If your tool can't dispatch sub-agents, run the same checklist inline.

<!-- cc-only:start -->
```
Agent({
  subagent_type: "codex:codex-rescue",
  description: "Codex adversarial spec review",
  prompt: "Inspect the spec and the code and report; do not implement changes or propose patches.

Read the spec at <abs path to spec> and the surrounding codebase. Produce an adversarial design review challenging the chosen approach.

Claude's own review already flagged: <bullet list of Gate B findings>.

Look for blind spots Claude may have missed:
- Repeated bugs in this area that the spec doesn't address.
- Implicit assumptions about <area-specific concerns>.
- Cross-codebase patterns suggesting this approach was tried and rejected before.
- Edge cases in <specific functions or flows> that the spec glosses over.

Return a concise list of issues with file:line citations, grouped Critical / Important / Minor. Add only new findings; don't restate Claude's."
})
```
<!-- cc-only:end -->

## Merging into the report

Tag Codex findings with `(codex)` so the source is visible in the gate rows. Pass `codex_used=true` when recording.
