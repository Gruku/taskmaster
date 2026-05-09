# Triage walkthrough

A long-form description of the `taskmaster:handover triage` interaction. The
SKILL.md `triage` section is the authoritative spec; this file is the
narrative companion you can read when you want context behind the choices.

## Why 14 days?

A handover younger than two weeks is still potentially the active working
context for an ongoing task. Anything older has either been picked up (status
should already be in-progress) or deferred (should be marked done or
explicitly held as todo). 14 days catches stale stragglers without churning
recently-written handovers.

## Why cap at 20 per invocation?

A user with 50 stale handovers will lose patience halfway through. 20 is
roughly five minutes of decisions, which is the right granularity for a
single attention span. The remaining stragglers surface on the next triage
run.

## When triage and supersession overlap

If a handover is `milestone-complete` or `pivot` and you're triaging it, prefer
the supersede path — it preserves the chain plus the SUPERSEDED callout in
the old file. The auto-flip-to-done in `apply_supersession` makes this a
single-step operation from the user's perspective.
