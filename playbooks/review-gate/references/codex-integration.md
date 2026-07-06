# Review Gate — Codex Integration (Gate 2b)

Gate 2b is the Codex precision pass — opt-in, runs after Gate 2a.

Skip silently if Codex isn't detected and not requested. Run after Gate 2a so it can be focused.

Codex detection: `~/.claude/plugins/cache/openai-codex/` exists OR `codex` is on PATH. If `--codex` passed but Codex isn't available, WARN and continue without it.

Default behavior by priority:
- `critical`: auto-suggest, ask user before running
- `high`: offer if Codex detected, default off
- `medium` / `low`: never run

## Case A — Standard Precision Review (default)

Use `/codex:review` against the branch diff:

```bash
/codex:review --base <branch-base> --wait <focus text>
```

Focus text should reflect what Gate 2a covered so Codex hunts for blind spots, e.g.:
"Claude's review flagged X and Y. Look for: error path correctness, edge cases in <specific function>, and any sibling occurrences of the same bug pattern elsewhere in the codebase."

## Case B — Repeated-Bug Verification

When Gate 2a found criticals already patched in-session. Codex is unusually good at catching when "the bug fix only patched the one site that hit the test." Prefer `/codex:rescue` framed as a verification task.

If your tool cannot dispatch sub-agents, run the review inline against the same checklist.
<!-- cc-only:start -->
```
Agent({
  subagent_type: "codex:codex-rescue",
  description: "Codex verifies fix is complete",
  prompt: "REVIEW-ONLY. Do not modify code. The bug <describe> was fixed in
commit <hash>. Verify the fix is complete: search the codebase for sibling
occurrences of the same pattern that may also need patching. Cite file:line."
})
```
<!-- cc-only:end -->

## Merging into Report

Tag findings with `(codex)` when merging into the gate matrix report so the source is visible.
