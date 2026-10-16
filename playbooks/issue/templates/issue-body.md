<!--
Issue body template for the log-issue flow. Fill each section from the
conversation; the user reviews the draft before it is written.

## Repro: required when steps are known. If not, write "Investigation
   pending" and update later via backlog_issue_update(issue_id, "body", ...).

## Expected: required alongside Repro. The correct behavior the user or
   spec calls for.

## Investigation notes: optional. Add when root-cause hypotheses or evidence
   exist; omit for a freshly filed Issue with nothing under investigation.
-->

## Repro
1. {{step}}
2. {{step}}
3. {{observed wrong outcome}}

## Expected
{{what should happen instead}}

## Investigation notes
- {{root-cause hypothesis or evidence}}
- {{related files / similar fixed issues}}
