# Review Gate: Codex Integration (Gate 2b)

Gate 2b is the Codex precision pass: opt-in, run after Gate 2a so it can hunt for what Gate 2a missed. Skip silently when Codex isn't detected and wasn't requested.

Codex is detected when `~/.claude/plugins/cache/openai-codex/` exists or `codex` is on PATH. When `--codex` was passed but Codex isn't available, say so in one line and continue without it.

Default by task priority:
- `critical`: suggest it and ask before running
- `high`: offer it when Codex is detected, default off
- `medium` / `low`: don't run

## Case A: standard precision review (default)

Run `/codex:review` against the branch diff:

```bash
/codex:review --base <branch-base> --wait <focus text>
```

Make the focus text reflect what Gate 2a covered, so Codex looks for blind spots. For example: "Claude's review flagged X and Y. Look for error path correctness, edge cases in <specific function>, and sibling occurrences of the same bug pattern elsewhere in the codebase."

## Case B: repeated-bug verification

When Gate 2a found Criticals that were patched in-session, Codex is good at catching a fix that only covered the one site that hit the test. Frame it as a verification task. If your tool can't dispatch sub-agents, run the same check inline.

<!-- cc-only:start -->
```
Agent({
  subagent_type: "codex:codex-rescue",
  description: "Codex verifies fix is complete",
  prompt: "Inspect the code and report what you find; do not implement changes. The bug <describe> was fixed in commit <hash>. Check whether the fix is complete: search the codebase for sibling occurrences of the same pattern that may also need patching. Cite file:line for each."
})
```
<!-- cc-only:end -->

## Merging into the report

Tag Codex findings with `(codex)` in the gate matrix report so the source is visible.
