# Bug — Full Entry-Point Flows

## log-bug

Trigger: explicit user request — "log a bug", "file a bug", "I found a bug", "track this defect", "this is a bug", or "log this as an issue" when no evidence is present (router falls back here).

1. **Auto-extract fields** from conversation:
   - `title` — first sentence describing the defect, ≤80 chars
   - `found_in` — active in-progress task ID if any, else null
   - `discovered_by` — `user` (default for this entry point)
   - `components` — infer from epic/folder; from file paths if cited
   - `location` — `file:line` if mentioned
   - `severity` — leave null unless user volunteers
   - `body` — draft `## Repro` + `## Expected` skeleton from conversation

2. **Present the draft** — show title + found_in + components + body. Ask:
   > "Looks good? Severity is optional (P0–P3) if you want a sort hint. Disposition: should I (a) fix it now in this task, (b) spawn a follow-up task, (c) shelve it for later, or (d) just leave it open?"

3. **Write via `backlog_bug_create`**, then run the chosen disposition subflow (`disposition` entry point).

4. **Echo**: "Bug logged: `B-NNN` — Short title. Disposition: <chosen>."

## offer-on-explicit-finding

AI may offer to log a Bug ONLY when a substantive finding surfaces mid-session AND the user is likely to want it tracked. Replaces the deleted `flag-from-conversation` heuristic from the old `taskmaster:issue` skill.

**Permitted to offer (any one):**
- User says "wait that's wrong" or "that shouldn't happen" without instructing AI to fix
- A test failure exposes a defect outside the current task's scope
- A root-cause investigation surfaces a separate defect from the one being worked on

**NOT permitted to offer:**
- AI noticed a defect within the current task's scope — just fix it inline as part of the work, mention in commit
- AI noticed a defect during code reading that isn't relevant to the current task — mention once in chat, no offer
- Anything previously offered and declined in this session

Offer pattern (inline, non-blocking, one-shot):
> "I noticed [one-line symptom]. Want me to log it as a Bug? It would be `found_in: T-XXX`. (default: don't log)"

If user confirms, run `log-bug`. If declined, do not re-offer the same finding in the same session.

## disposition

Selector run at Bug creation and at task-close. Choices:

| Choice | Action | Side effects |
|---|---|---|
| **fix-now** | Apply the fix as part of the current task | status remains `open` until the commit lands; user comes back and sets `status=fixed, fix_commit=<sha>` |
| **spawn-task** | Create a follow-up task | `backlog_add_task` returns T-XXX; set bug `adopted_into=T-XXX` then `status=adopted` |
| **shelve** | Park for later | `status=shelved`; surfaces in start-session "shelved bugs to revisit" |
| **promote** | Cleared the Issue bar | Walk evidence citation; call `backlog_bug_promote(bug_ids=[B-XXX], evidence_text=...)`; bug → `promoted` |

## update-status

Trigger: `mark B-XXX fixed`, `shelve B-XXX`, `promote B-XXX`, `close B-XXX`, `fixed B-XXX in commit`.

1. Identify Bug ID from user message or ask: "Which bug? (B-NNN)"
2. Confirm target status + required fields per state machine.
3. `backlog_bug_update` takes one `field`/`value` per call. Set the companion field first, then the status: e.g. `backlog_bug_update(bug_id, "fix_commit", "<sha>")` then `backlog_bug_update(bug_id, "status", "fixed")` (likewise `adopted_into`→`adopted`, `promoted_to`→`promoted`). Validation reads the merged on-disk state, so the two-call order satisfies the lifecycle constraint.

## triage-review

Trigger: `list open bugs`, `what bugs are open`, `triage bugs`, `show me shelved bugs`.

1. `backlog_bug_list(status="open")` and `backlog_bug_list(status="shelved")` — show two grouped sections.
2. For each open Bug attached to a not-yet-done task, mark it as gating that task.
3. Offer actions: pick a disposition for any item.
4. If start-session: surface the shelved list as "Pick anything back up?" with one-click adopt/promote options.
