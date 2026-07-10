<!--
ISSUE BODY TEMPLATE
Used by `taskmaster:issue` write subflow. Auto-extractor fills each section
from session signals; user reviews before write.

## Repro — REQUIRED when steps are known. If no repro steps yet, write
   "Investigation pending" and update later via backlog_issue_update(issue_id, "body", ...).

## Expected — REQUIRED alongside Repro. Describe the correct behavior the
   user or spec calls for.

## Investigation notes — OPTIONAL. Add when root-cause hypotheses or
   evidence exist. Omit entirely if the issue is freshly filed with no
   investigation underway.
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
