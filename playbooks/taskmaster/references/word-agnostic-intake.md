# Word-Agnostic Intake for "Issue" / "Bug"

Users say "issue" colloquially when they mean "bug". The router decides what to file based on the **evidence in the description**, not the word.

## Algorithm

1. Parse the finding from context.
2. Check for cited evidence of the Issue bar (see `issue:references/issue-bar.md`): recurring across ≥2 prior occurrences, systemic (≥2 components), or P0 severity.
3. If evidence present → `taskmaster:issue` (log-issue entry point).
   Echo: "Logged as ISS-NNN — [criterion matched]."
4. If no evidence → `taskmaster:bug` (log-bug entry point).
   Echo: "Logged as B-NNN. You said 'issue' but I filed it as a Bug — it reads as a single defect, not a recurring/systemic pattern. If it's actually been showing up elsewhere, tell me what's recurring/systemic and I'll promote B-NNN → ISS-NNN."

## Why default to Bug?

Promotion (Bug → Issue) is one cheap step (`backlog_bug_promote`). Demotion is not. When the evidence is ambiguous, the safer route is Bug — it preserves optionality without over-committing to Issue-level tracking.

## Phrase coverage

| Phrase | Router decision |
|---|---|
| "log an issue" | Word-agnostic intake (this algorithm) |
| "this is an issue" | Word-agnostic intake (this algorithm) |
| "file an issue" | Word-agnostic intake (this algorithm) |
| "log a bug" | Always → `taskmaster:bug` |
| "this is a bug" | Always → `taskmaster:bug` |
| "I found a bug" | Always → `taskmaster:bug` |
| "this is broken" | Always → `taskmaster:bug` |
| "track this defect" | Always → `taskmaster:bug` |
| "promote B-XX to an issue" | `taskmaster:bug` promote subflow → `taskmaster:issue` |
| "shelve this", "park this for later" | `taskmaster:bug` (disposition: shelved) |
