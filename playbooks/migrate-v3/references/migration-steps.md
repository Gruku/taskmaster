# Adoption details

What `backlog_migrate_v3` does on disk, and what to do when it stops. Adoption is idempotent: running it on an adopted project re-reports counts and changes nothing.

## What moves

- Layout: a `.claude/` or project-root `backlog.yaml` and its sibling files move under `<root>/.taskmaster/`. The success response lists the count as "Canonicalized ... file(s) moved". A project already on `.taskmaster/` skips this move.
- Heavy fields (description, notes, docs, review_instructions) land in per-entity files at `.taskmaster/tasks/<id>.md`.
- The derived `context:` block is dropped from `backlog.yaml`; the store derives it.
- Epics, phases and tasks without ids get deterministic ids.
- `viewer.json`, `auto/`, `snapshots/` and `PROGRESS.md` move under `.taskmaster/local/`. Nothing is deleted; a name already taken there gets a `-2` suffix.
- Bugs, issues, handovers, decisions, ideas, notes, areas and trackers are imported as rows; the response counts each kind it found.

After the move the database under `.taskmaster/` is the authority. A later hand edit to a projected file takes effect after `backlog_sync()`.

## When it stops

- `Error: multiple backlog.yaml files exist`: the response lists them. The user keeps the live one and moves the others out of the project, then re-run.
- `Error: the canonical layout already holds different content`: nothing moved. The response lists each source and destination pair. The user decides which side wins; resolve the pairs by hand, then re-run.
- `Error: refusing to migrate on this storage`: the project sits on a filesystem the store cannot use safely (a network or synced folder). Move the project to local disk first.
- `adoption refused: ...`: names the entity whose rendered file did not read back as written. Nothing on disk changed. Correct that entity's file, then re-run.

## After adoption

- `backlog_store_status` lists quarantined files (could not be imported) and dirty files (waiting to be exported). The success response also prints both when present.
- `backlog_status` confirms the counts.
- A viewer tab that was open before adoption needs a hard refresh (Ctrl+Shift+R, Cmd+Shift+R on macOS).
