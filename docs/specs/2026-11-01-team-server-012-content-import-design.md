<!-- User intent: the task-level design (with its TDD plan) for team-server-012. A remote teammate has no files on the
     host, so they import a document by sending its text, never a host path, and no remote caller can make the
     server read host files into the shared store; solo projects work exactly as before. -->

# team-server-012: content-based import for remote callers (design and plan, 2026-11-01)

**Task.** `team-server-012`: "Content-based `document_import` / `backlog_sync` for remote callers.
Done when remote callers import by content rather than host paths. Addendum §8."
**Source.** `docs/specs/2026-10-31-team-server-mode-addendum.md` §8 ("`backlog_sync` and
`backlog_document_import` operate on files on the host. Imports from a teammate's machine take content,
not paths: the tool gains a `content` argument for remote callers"), §6 (teammates' clones keep no
local store), §13 (go-live order).
**Builds on.** 001 (serve daemon), 002 (`ORIGIN_PROVIDER`, `Origin(principal=key_id)`,
`ServeActorSource`, actor `clone_id = key_id`), 006 (`serve/tracked.py`, the 2 MiB body cap, sanitizing,
`DOMAIN_ERRORS`), and 005 (`coordinator/allowlist.py`, the `_send` and `ServeCoordinator.dispatch`
gates, `client.serving()`/`served_principal()`, Part B's `taskmaster_v3.served_doc_text`).
**Verified against.** `feat/team-collab` at `748cd29`, and `feature/team-server-005` at `846be5f`
(in review, not merged). Line numbers marked "005" are on that branch. No probe was needed; every claim
below is cited to code.
**Lane.** Standard: one fresh design review covers this spec and its plan (§13) before any code.
**Review.** The design review (`D:\scratchbook\taskmaster\ts012-review\review.md`, on `68e074e`) gave
WARN: 0 Critical, 2 Important, 8 Minor. This revision fixes I1 (§7, D2, R6) and I2 (D7, Step 5).
Minors M1 to M7 are folded in; M8 is declined (§12). The user ruled Q1 to Q4 as recommended (§12).
The scoped re-review (same file, on `806deee`) gave WARN: I2 closed; R-I1 (013's restore conflicted
with M3 and M5) and R-M1 (a large flagged file can't be cleared). Round 2 adds the shared restore
contract with 013 that the orchestrator set (D10, §7) and the conflict-read limit (D7).

## 1. Problem

Two tools take host paths, and in server mode both run on the host:

| Tool | What it reads on the host | Who gets it |
|---|---|---|
| `backlog_document_import(kind, entity_id, sections)` | the file at each declared `docs` path (`native_routing/documents.py:74-107`, 005) | the store, then every reader of the backlog |
| `backlog_sync(files, sync_id)`, `backlog_handover_resync`, `backlog_issue_resync` | `.taskmaster/` files, through `sync_files.observe` in `sync_prepare.prepare` (`coordinator/sync_prepare.py:38`) | the store, then every reader |

For a remote teammate this is wrong both ways:

- **It doesn't work.** A spec the teammate wrote lives on their machine, on a branch the host has never
  seen. The declared path names a file the host doesn't have. Their clone has no `.taskmaster/` to
  hand-edit either: code branches stop tracking it (addendum §8) and 009 refuses a local store there
  (§6).
- **It reaches host files.** 005 Part B limits document reads in the serve process to git-tracked
  files. But 005's review (codex, `D:\scratchbook\taskmaster\ts005-gate\codex-review.md`, finding 2,
  Important) found that an ordinary `sync` stays in 005's remote class (`allowlist.py:16`, 005), and
  `prepare` observes any `.taskmaster/` file on disk. So a member key calling
  `backlog_sync(files=["notes/NOTE-123.md"])` imports an untracked host note into the store, and can
  then read it back. Neither `take_file` nor `worktree` is needed. The same review's finding 1
  (Important) is next door: `backlog_resolve_conflict` compares a flagged file with
  `read_bytes()` (`native_routing/projection.py:304-309`), which follows symlinks.

## 2. Scope

In scope:

- the remote sync rule: what a remote principal may make the host read on the sync surface, and how
  005's finding 2 is closed (D1);
- the content contract for `backlog_document_import` (D2), its limits (D3), provenance and
  attribution (D4), and conflicts (D5);
- what remote callers may still do by path (D6);
- 005 finding 1, the conflict-file reads (D7; ruled into 012, Q2);
- that solo is unchanged (D8), and the error shapes (D9).

Out of scope: §11.

## 3. Threat model

The host is internet-facing behind Caddy (addendum §13). 002 authenticates, 006 throttles and
sanitizes, 005 decides which coordinator methods a principal reaches. 012 decides which host bytes a
principal can bring into the store through the import surface.

| Attacker | Wants to | Today (005 tip) | Stopped by (012) |
|---|---|---|---|
| A member key, a leaked key, or a prompt-injected teammate agent | import an untracked host `.taskmaster/` file (an operator's draft, a stray note) and read it back | reachable through `backlog_sync(files=…)`, `backlog_sync()` and the resync tools | path sync is operator-only (D1) |
| same | poll or attach to another principal's sync by id, `backlog_sync(sync_id=…)` | reachable | the same rule: `sync_job` is operator-only (D1) |
| same | have the store record host bytes as their own edit, or credit the operator with their import | sync imports are submitted as the host (`sync_worker.py:347-353`, origin `LOCAL`) | members never start a sync (D1); content imports carry the member's actor (D4) |
| same | read host files through a declared `docs` path | 005 Part B: tracked files only | unchanged; 012 adds no path logic (D6) |
| same | read a secret through a flagged conflict file swapped for a symlink or junction | `read_bytes()` follows it (finding 1) | the conflict read goes through sync's own reader, which refuses links and reparse points (D7) |
| same | use the backup-restore path to plant prose credited to another teammate, or to bypass the team-mode identity gate | n/a (new in 012) | `restore=True` is accepted only from the host-local operator, at both 005 gates; no tool exposes it (D10) |
| same | silently replace a teammate's imported document | last writer wins, and the old body is gone from the store | an optional precondition, and an answer that names whose copy was replaced (D5) |
| same | exhaust memory or the writer with a huge upload | n/a | 006's body cap before the tool runs; the 1 MiB per-document cap; 006's per-key rate limit (D3) |
| A host process or the operator over loopback | anything above | trusted (002 §7, 005 D2) | unchanged: the operator keeps path sync |

## 4. Decisions

### D1. Remote path sync is refused; it is not narrowed to tracked files

**The rule.** In a serve process, `sync` and `sync_job` are operator-only coordinator methods. A
member key, and a daemon message with no principal (005 D4), is refused for all of them: no files, a
list of files, `take_file`, `worktree`, and a `sync_id` poll. The bootstrap operator over loopback and
host processes (`LOCAL`) keep every form.

**Why refuse, not observe only tracked files** (the alternative the orchestrator named):

- **No remote caller has a legitimate use.** A sync imports hand edits of the host's `.taskmaster/`
  files. Only the operator can make those. Teammates change the backlog through the tools, and bring a
  document in by content (D2).
- **Tracked-only would depend on 013 and 014.** Whether the host checkout tracks `.taskmaster/` is
  theirs to decide. Addendum §8 says code branches stop tracking it.
  - If it is untracked, a tracked-only sync refuses every file anyway, with a misleading reason.
  - If the host checks out `taskmaster-state`, a member could still import a committed host edit.
    The coordinator would attribute it to the host (`sync_worker.py:347-353`) and to no person.
- **It needs no change to the sync core.** The coordinator package must not import `serve`
  (005 D1), and observation runs in the coordinator, not in the tool. A tracked check there would need
  a new hook through `sync_files.observe`, `prepare` and the job scan. That is the worker's most
  delicate code (revision fences, rechecks before submit and after commit).
- **It is one table edit at the two gates 005 already enforces.** Every IPC message from the daemon
  passes `Client._send`, and every message the coordinator accepts passes `ServeCoordinator.dispatch`.
  A future tool that calls `sync` is refused for members without anyone remembering to add it.

**Mechanism.**

- `coordinator/allowlist.py`:
  - `REMOTE` loses `sync` and `sync_job`;
  - `WHAT` gains `"sync"` and `"sync_job"`, both `HOST_SYNC_WHAT`;
  - `refusal` keeps 005's precedence: for `sync`, `host_only_sync(arguments)` names `take_file` or
    `worktree` first (their texts and hint are unchanged). Otherwise, for both `sync` and `sync_job`
    (which carries the `sync_id` poll), it returns `HOST_SYNC_WHAT` with `REMOTE_SYNC_HINT`
    appended. That is an explicit branch, not the generic `WHAT` branch, which adds no hint (review M1).
  - 005's inventory ratchet is unchanged, since `METHODS` is unchanged.
- **The tool gate, for an early and clear answer** (005 D6 pattern). In `native_routing/resync.py`,
  `backlog_sync`, `backlog_handover_resync` and `backlog_issue_resync` first call
  `client.served_principal()`. When serving and `allowlist.action_refusal(HOST_SYNC_WHAT, principal)`
  refuses, they answer `Error: <refusal><REMOTE_SYNC_HINT>`, the same text the gates give.
  `@adapter` has already opened the call (and may have probed the coordinator with `status`) by
  then, but the answer comes before `sync_files.discover`, before any file read, and before any
  `sync`/`sync_job` message (review M7). The two gates stay the authority; the tool gate only answers sooner.
- **What stays remote on the sync surface:**
  - `backlog_store_status`: store metadata, file names only;
  - `backlog_resolve_conflict`: the list, the compare (D7) and `take="store"`. `take="file"` stays
    005's operator-only.
- **Checked:** no other tool sends `sync` or `sync_job`. The senders are `resync.py:75`, `:256` and
  `conflicts.py:115` (take_file). `git.py:812` and `sync_jobs.py:141` run inside the coordinator. Nothing
  under `serve/`, `viewer/`, `mods/` or `hooks/` does. 005's sync-job thread carries the origin it
  resolved in the calling thread (`_sync_job(extra=…)`, 005), so an operator's `backlog_sync` keeps
  working.

**Texts (exact):**

- `HOST_SYNC_WHAT = "importing hand edits of the host's .taskmaster/ files (backlog_sync and the resync
  tools)"`.
- `REMOTE_SYNC_HINT = "; on a team server, change the backlog with the backlog_* tools, and bring a
  document from your machine with backlog_document_import(content=...)"`.
- The full refusal is `ADMIN_REFUSED.format(what=HOST_SYNC_WHAT) + REMOTE_SYNC_HINT`.

### D2. The content contract

```
backlog_document_import(kind: Literal["task"], entity_id: str,
                        sections: list[str] | None = None,
                        content: dict[str, str] | None = None,
                        expected_hash: dict[str, str] | None = None) -> str
```

- **`content`** maps a doc section (`spec`, `plan`, `design`, `analysis`, `roadmap`) to that
  document's text as it is on the caller's machine. Nothing is read from disk for those sections, on
  any host.
- **`sections` and `content` are exclusive.** Passing both gives `Error: pass sections (import the
  declared files) or content (the text of each section), not both`. Without `content` the tool behaves
  exactly as today, under 005 Part B in a serve process.
- **Each `content` section must already be declared** in the task's `docs`. The store keys an
  imported document by its declared path, and `retrieve` serves it only while `docs[section]` still
  names that path (`native/documents.py:37`, `:58-68`). The upload is stored against that path,
  meaning "the text of `<path>` in my checkout". An undeclared section fails with `the task declares
  no <section> path; set one first with backlog_update_task(field="docs", value="<section>:<path in
  your checkout>")`. Declaring and importing in one call is not offered; it would add a second way to
  write `docs`.
- **Text is normalized as a file import is.** `read_text` and 005's `served_doc_text` turn CRLF and
  CR into LF, so the content does too. Then the same document gives the same `content_hash` whether it
  came from a Windows upload or from a file on the host. No other change: a BOM, tabs and NUL survive,
  as they do in a file import.
- **Text that cannot be stored is refused per section.** A string with an unpaired surrogate (legal in
  JSON as `\ud800`, not encodable as UTF-8) fails with `the uploaded text is not valid UTF-8 (it holds
  an unpaired surrogate)`. Without this check, the string would fail earlier than the writer: a
  `UnicodeEncodeError` (a `ValueError`) at `.encode("utf-8")` in `contracts.validate` and
  `protocol.encode`, which would surface as a raw, unhelpful FAILED reason. The check exists for its
  text (review M7).
- **Each section is its own commit**, as today (`native_routing/documents.py:136-158`, 005). One bad section neither
  undoes nor blocks the others. The answer lists every section and leads with `Error:` if any failed.
- **Where the durable copy lives (review I1).** Before 012, `external_documents` held copies of files
  in the shared repository, and losing it cost one re-import. After 012, an upload can be the host's
  only copy: the file exists only in the teammate's checkout. No projection file carries
  `external_documents`, so it survives a lost-disk restore only through 013's backup, which the
  orchestrator ruled covers `external_documents`, uploads included (§7). The tool docstring says so:
  "An uploaded document is stored on the team server. A restore of the server keeps it only through
  the server's state backup (team-server-013); the file in your checkout stays the source, so commit
  it to the code repository if it must outlive both."
- **It works in solo too.** That costs nothing, gives one code path that tests can drive without a
  daemon, and helps an agent that can't see the file the task declares. Solo calls without `content`
  are unchanged (D8).

**Answers, per section (exact prefixes; the plan's tests match them):**

- `- spec: imported from your upload as docs/specs/x.md (sha256 1a2b3c4d5e6f…)`;
- `- spec: unchanged — the store already holds this text`;
- either may end with ` — replaced the copy bob imported at seq 812` when the stored copy was imported
  by a different handle (D5);
- `- spec: FAILED — <reason> — nothing imported`.

### D3. Limits

| Limit | Value | Where | On breach |
|---|---|---|---|
| HTTP body of one call | 2 MiB default (`--max-body-bytes`, 006 D3, D8) | `RequestGuard`, before auth reads the body and before the tool runs | 413 with 006's fixed body; nothing imported |
| One document | `contracts.MAX_BYTES` (1 MiB) of UTF-8, after normalization | the tool, before `execute`, with the same check and helper a file import uses | that section fails: `the uploaded text is N bytes, over the 1048576-byte (1 MiB) limit on one imported document` |
| One command envelope | 1 MiB encoded (`contracts.py:86`) | the coordinator | that section fails with the envelope message. JSON escaping of newlines and quotes can push a body just under 1 MiB over (`encode` uses `ensure_ascii=False`, so non-ASCII text is not inflated) |
| Sections per call | at most 5 (the doc sections) | the tool, by key validation | `Error: section must be one of …` |
| Calls | 006's per-key rate (240/min, burst 120) and in-flight cap (32) | `PrincipalLimits` | 429 |

- There is no separate per-call total in the tool. The body cap bounds a call on the wire, and the tool
  never holds more than one call's content.
- A client that escapes non-ASCII (`\uXXXX`) inflates CJK text about 2x on the wire. So a near-1 MiB
  section of such text can meet the 413 first. The docstring says to send large sections one per call,
  and the operator may raise `--max-body-bytes`.
- No size is logged with content, and no content is ever logged (006's rules).

### D4. Provenance and attribution

- **Attribution comes from 002, unchanged.** The import is an `execute`. In the daemon it carries
  `origin={"principal": key_id}` (002 D8; 005 D3), and `ActorResolver.stamp` (`service.py:289`)
  records `{handle, agent, clone_id: key_id}` on the receipt and, on schema 5, on
  `domain_events.actor`. The caller can't name a handle.
- **An upload is attribution-bearing; a file import stays exempt (review M5).** Today every
  `document.import` is exempt from the team-mode identity gate (`team_identity._exempt`, `:201-203`),
  and `ServeIdentityGate.admit` returns early for exempt operations (`serve/identity.py:52-53`). Left
  that way, an upload in team mode would skip three of 002's rules: a person whose profile was deleted
  could still upload; the operator token, which is not a person, could upload prose every teammate
  reads; and the schema-5 upgrade would be skipped, so `imported_by` could be missing on a fresh
  store's first write. So `is_attribution_bearing` (`team_identity.py:207-212`, which already takes the
  arguments) treats `document.import` with `source == "upload"` as attribution-bearing.
  - In team mode an upload then needs a person: a member key passes; the operator and host-local
    callers get 002's `NOT_A_PERSON` refusal.
  - File imports stay exempt, so the operator's host-side imports keep working.
  - Outside team mode the gate does nothing, as today.
  - The one exception is the restore path (D10): `document.import` with `restore=True` is exempt,
    because the operator replays someone else's upload there, and D10's gate admits only the host
    operator to it.
- **The command records the source, only when it is an upload (review M3).** On the normal path,
  `document.import` accepts an optional `source`, whose only allowed value is `"upload"`. (The
  restore path, D10, also accepts `"file"`.) Absent means a file import:
  every existing caller, every replay, and every new file import, which therefore sends exactly
  today's envelope. `import_document` adds `source: "upload"` to the event's `after` for uploads
  only, so a file import's event and change feed are unchanged.
  - The `external_documents` table is unchanged: no schema change, no migration.
  - Re-importing identical text under the same path writes nothing (`commands.py:241-242`), so the
    first source stands. The text is the same, so that is accurate enough, and the docstring says so.
- **Reads show it, for uploads only.** `native/documents.retrieve` looks up the `document.import`
  event at `imported_seq`. When its `after` has `source: "upload"`, it adds two facts to the
  section's provenance:
  - `origin: "upload"`;
  - `imported_by`: that event's `actor.handle`, when the store has the `actor` column (schema 5) and
    the value is non-empty.

  A file import's provenance is unchanged: no new keys, so solo answers stay byte-identical.
  - It is one primary-key lookup per imported section (`domain_events.seq`).
  - The tool's "replaced the copy … imported" line (D5) uses the same lookup helper, which also
    returns the handle of a file import. That handle goes only into that answer line, never into
    provenance. For a restored row, the helper returns the original `imported_by` the event
    recorded, not the operator who ran the restore (D10).
  - A restored row's provenance follows D10.
  - `sections_with_provenance` passes the facts through (`{"source": "import", **facts}`), so
    `backlog_document(provenance=True)` and `backlog_get_task` provenance show them with no renderer
    change. 007's read endpoint should pass them through too (§10).

### D5. Conflicts

A document import has no merge. The store holds one copy per section, and a re-import replaces it,
event and all. The old body is not kept (the event's `before` has only its hash). Two teammates
uploading different versions of one section therefore race, and the later one wins.

- **The optional precondition, `expected_hash`.** It maps a section to the `content_hash` the caller
  expects the store to hold now, or `""` for "nothing imported yet". It is checked inside the
  `document.import` transaction (`native/documents.apply`), so it is race-free. On a mismatch it raises
  `contracts.Conflict`, and nothing is written.
  - The section then fails with: `the stored copy changed: it is sha256 <12 hex>… (imported by
    <handle> at seq <n>), not the <12 hex>… you expected; read it with
    backlog_document(kind="task", entity_id="<id>", sections=["<section>"], provenance=True) and retry
    with expected_hash={"<section>": "<current>"} to replace it`.
  - `expected_hash` is validated in the tool: 64 lowercase hex, or `""`, for a section the call imports.
  - It applies to path imports too, at no extra cost.
  - **The stored copy it is compared with (review M4b)** is the `external_documents` row for that
    section only when its `path` equals the declared path, the same rule `retrieve` uses. A row under
    an older path counts as "nothing stored": `""` passes, a hash fails, and the conflict text says
    `nothing is imported under <path>`. So the caller never meets a hash it couldn't have read.
  - **Identical text wins over a stale hash (review M4a).** When the body equals the stored body under
    the declared path, the answer is `unchanged`, whatever `expected_hash` says. That is the existing
    short-circuit (`commands.py:241-242`), checked first. Nothing is written, so no update can be lost.
    The precondition is checked only when a write would happen.
- **Without `expected_hash`, the last writer wins**, as today. The answer names whose copy was replaced
  when the handle differs (D2), using the provenance the tool read before `execute`. That read can be
  stale in a race; it is information, not a guard. The docstring says to pass `expected_hash` when more
  than one person imports the same task's documents. A cross-person overwrite doesn't require it
  (ruling Q3).
- **A declared path that changes mid-call** fails that section, as today (`does not declare`,
  `native/documents.py:37-38`).
- **Sync conflicts are not 012's.** Remote callers no longer start syncs (D1). The flagged-file
  workflow (`backlog_resolve_conflict`) keeps its behaviour, with D7's read change.

### D6. What remote callers may still do by path

| By path | On a team server, for a member |
|---|---|
| `backlog_document_import(sections=…)` reading declared files | allowed, through 005 Part B's tracked-only reader (`served_doc_text` → `serve.tracked.read_tracked_file(bs.ROOT, path)`). 012 adds no path logic |
| `backlog_get_task(sections=…)` / `backlog_document` on an unimported doc | unchanged (005 Part B) |
| `backlog_sync`, `backlog_handover_resync`, `backlog_issue_resync` | refused (D1) |
| `backlog_resolve_conflict(file=…)` list / compare / `take="store"` | allowed; reads contained per D7 |
| `backlog_resolve_conflict(take="file")` | refused (005) |

- **Why keep the path import for members.** After Part B it reads only git-tracked files of the repo
  the team already shares. Refusing it would gain nothing, because `backlog_get_task` must read the
  same files through the same rule.
- **The serve-mode hint.** In a serve process, a path import whose read is refused, or finds nothing
  (005's reader returns `None` for both), ends its line with `; to import the file from your machine,
  pass its text: content={"<section>": "..."}`. 005's `SERVED_DOC_REFUSED` text itself is unchanged.

### D7. Conflict-file reads go through sync's own reader (finding 1; ruled in, Q2; review I2)

- **Who can flag a file.** Once D1 lands, only host activity flags files: the operator's sync, or the
  exporter meeting a hand-edited file. A flagged path is always a store projection path under
  `.taskmaster/` (`safe_relative`; `classify`). What is left of finding 1 is the read itself:
  `projection.read_file` (`native_routing/projection.py:304-309`), used by the compare
  (`conflicts.py:57`) and by `take="store"` (`:89`). It calls `read_bytes()`, which follows a symlink
  or a junction anywhere on the path.
- **Reuse, don't write a reader.** `coordinator/sync_files.py` is already the one sanctioned reader of
  authored projection files: the N08 bypass gate names it (`tests/test_native_bypass_gate.py:29-31`),
  and `conflicts.py` already calls `sync_files.observe` in the same tool (`_already_taken`). So
  `read_file(backlog_dir, rel)` becomes a call to
  `sync_files.observe(backlog_dir, rel, limit=git.MAX_PROJECTION_BYTES)`, returning
  `observation.content`, or `None` when it returns `None` (nothing at the path). 012 adds no reader,
  and does not touch `serve/tracked.py` or 006's tests.
- **The rule is `observe`'s, stated exactly** (`sync_files.py:118-151`, `projection_paths.py:10-59`):
  - `rel` must be a plain relative POSIX path: no `:`, `\`, NUL, absolute form or `..`;
  - it must classify as a projection path (`classify`);
  - no component from the backlog directory down to the leaf may be a symlink or a name-surrogate
    reparse point (a junction or mount point); `check_component` runs on each one;
  - the candidate must resolve inside the backlog directory;
  - the leaf must be a regular file. The size bound is `coordinator/git.py:53`'s
    `MAX_PROJECTION_BYTES` (64 MiB), the largest projection file the store handles, not sync's 1 MiB
    import limit (re-review R-M1). The security property here is "no link, regular file"; the size
    bound only keeps a runaway file from being read into memory;
  - its dev/inode/size/mtime signature must be the same before the open, at the open, after the
    read, and on a recheck, so a swap during the read is refused.

  No link is ever followed. That replaces the previous draft's "resolved form inside `.taskmaster/`"
  rule, which would have followed a link between two `.taskmaster/` files and contradicted its own
  test.
- **The root** is `call.backlog_dir`, which the caller already holds, not `bs.ROOT` plus a prefix.
  `.taskmaster/local/` can't be reached: no `local/` path classifies as a projection file.
- **All modes, not only the serve process.** There is one reader and no serve import, and the
  conflict reads stay in the same module as their sibling `_already_taken`. In solo this tightens
  only states the store itself can't create: the exporter and sync already refuse linked paths
  (`native/projection.py:540-541,584-587`). D8 states the change.
- **A large flagged file can still be cleared with `take="store"` (re-review R-M1).** The resolution
  keeps the replaced bytes in its event as `replaced_base64`. A file over about 768 KiB inflates past
  the 1 MiB command envelope (`contracts.py:86`). So before 012 such a file couldn't be cleared
  either: the envelope refused it, whatever the read did.
  - `conflicts.py` therefore sends the bytes inline only up to `RESOLVE_INLINE_BYTES = 640 KiB`
    (about 853 KiB as base64, leaving room for the envelope).
  - Above that it sends `replaced_hash` alone.
    `native/projection.validate_resolve` accepts the hash without the bytes when
    `replaced_omitted: true` is set. The rule that both come together stays for every other call,
    and old receipts replay unchanged.
  - `apply_resolve` then records the hash as the file's base, exactly as today, so the next export
    replaces exactly those bytes and a newer edit is flagged again. The event's `before` gets
    `file: null`, `file_base64: null` and `file_omitted: true`.
  - The answer says so: `Resolved <rel>: kept the store version. The file's <n> MiB were too large to
    keep in the history; only their hash is recorded.`
- **Not tracked-only** (codex's proposed fix), for D1's reason. Whether the host's `.taskmaster/` is
  tracked is 013/014's choice, and if it isn't, every compare would be refused.
- **Answers.** `observe` raises `UnsafePath` (link, reparse point, not a regular file, escape),
  `ValueError` (over the limit, or not a projection path) or `ChangedDuringRead` (both are
  `ValueError`s). `read_file` maps each to one refusal, carrying the reason without the absolute path:
  - Compare: the file block reads `(the file can't be read safely: <reason>; compare it on the
    host)`, and the store version is shown as today.
  - `take="store"`: `Error: <rel> can't be read safely (<reason>), so its bytes can't be kept in the
    resolution's history; resolve it on the host. Nothing was changed.` The flag stays.
  - A missing file keeps today's behaviour (`replaced` is `None`).
  - Reasons: `it is a link or junction`, `it is not a regular file`, `it is over the 64 MiB
    projection limit`, `it changed while being read; retry`.

### D8. Solo is unchanged

- Without `content` and `expected_hash`, `backlog_document_import` behaves byte for byte as today.
  That includes its event `after` and its read provenance, which gain keys only for uploads (D4,
  review M3).
- `backlog_sync` and the resync tools take their new branch only when `client.serving()` is true.
  Only the daemon installs a provider.
- The allowlist applies only to a serve coordinator and to a client with a provider installed (005).
- `document.import` without `source` or `expected_hash` validates and applies as today. Existing
  receipts replay unchanged: both new arguments are optional, and an absent one is not added to the
  envelope. A file import never sends `source`.
- **The one solo change is D7's.** A flagged conflict file that is a link, a junction, not a regular
  file, or over 64 MiB is now refused on compare and on `take="store"`, instead of being read through.
  The store never creates such a file (D7). A flagged file between 640 KiB and 64 MiB can now be
  cleared with `take="store"`, which the envelope refused before.
- **D10's restore path** is reachable in solo only by a local caller (there is no other kind). No MCP
  tool exposes it, so no solo answer changes.
- Tests prove it with the existing solo suites (§13 regression set) and one subprocess check that a
  solo content import never imports `taskmaster.serve`.

### D9. Error shapes

- **Refusals from D1** are `ValueError`s with path-free text. They take 005's shapes (005 D9): the
  client gate raises before the message is sent; the coordinator gate answers IPC 409 with type
  `ValueError`; the tool gate answers `Error: …`.
- **The D5 conflict is `contracts.Conflict`.** It is already in the client's error map
  (`client.py:148`) and in 006's `DOMAIN_ERRORS` (`sanitize.py:170`), so it passes the sanitizer with
  its text. The text holds only the section, the task id, hashes, a handle and a seq.
- **No new exception type**, for 005 D9's reason: 006's sanitizer rewrites unknown types to
  `internal coordinator error`.
- **Logging.** The coordinator and client gates log `refused sync for key …` as 005 does. The tool
  logs nothing new.

### D10. The restore path: the shared restore contract with 013 (re-review R-I1; orchestrator ruling)

**The conflict it resolves.** 013 restores each backed-up document by submitting `document.import` as
the operator. After M5 and M3, that path broke in two ways:

- an upload is attribution-bearing, so `ServeIdentityGate.admit` refuses an operator or `LOCAL`
  origin with `NOT_A_PERSON` (`serve/identity.py:58-60`);
- `source="file"` was refused outright.

Also, 013's manifest kept no uploader, so even a working restore would have lost `imported_by`.

**The contract (the same text is in 013's design):**

- **The manifest.** 013 records, for each `external_documents` row:
  - `section`;
  - the declared `path`;
  - `sha256` (the stored `content_hash`);
  - `source`: `"upload"` or `"file"`;
  - `imported_by`: a handle, or `""` when unknown, for example a pre-schema-5 store;
  - `imported_seq`: the original seq.

  013 reads `source` and `imported_by` through 012's one lookup helper (D4). For an already-restored
  row, the helper returns the original values, so a second backup cycle loses nothing.
- **The sanctioned path.** `document.import` with `restore=True`.

**`validate`:**

- **On the restore path:**
  - `restore` must be exactly `True`;
  - `source` is required, and may be `"upload"` or `"file"`;
  - `imported_by` is required: `""`, or a handle matching the existing handle rule (`actor.make`'s);
  - `original_seq` (the manifest's `imported_seq`) is optional, a positive int;
  - `expected_hash` is refused, because a restore replays a known state.
- **On the normal path:** `restore`, `imported_by` and `original_seq` are refused, and M3's rule
  holds (`source` may only be `"upload"`).

**Who may use it: the host-local operator only.**

- The rule is added to 005's one predicate, `allowlist.refusal("execute", arguments, principal)`. An
  `execute` whose envelope is a `document.import` with `restore` present is refused unless `principal`
  is `allowlist.OPERATOR`. Every other `execute` stays remote, as 005 has it.
- So both 005 gates enforce it:
  - `Client._send` in the daemon refuses it for a member key, and for no principal (005 D4,
    fail closed), before anything is sent;
  - `ServeCoordinator.dispatch` maps `LOCAL` (a host process with no served origin, such as 013's
    `restore-documents`) to the operator, and refuses any member principal.
- Refused for every member key, including one with role `operator`. The check is `key_id ==
  "operator"`, which 002 D7 and 005 D8 tie to `is_host_local`; a member's role is never read (005 D2).
- Text: `RESTORE_WHAT = "restoring a document from the state backup (document.import restore)"`,
  through `ADMIN_REFUSED`.
- **Kept out of every MCP tool schema (013 final review).** The gate admits the host-local operator,
  and that includes an agent session over loopback with the bootstrap token. So if a tool exposed the
  restore path, that session could forge who imported a document. The only sender is therefore the
  command path that `serve export restore-documents` uses: a host process submitting through an
  in-process `ServeCoordinator` as `LOCAL`.
  - `backlog_document_import`'s schema has no `restore`, `imported_by`, `original_seq` or `source`
    parameter. The tool derives `source="upload"` itself from `content` (D2), so even the upload
    marker can't be named by a caller.
  - `backlog_batch_update` can't carry it either: `document.import` is refused inside a batch
    (`batch_commands.py:37`).
  - No other tool builds a `document.import` envelope (`documents.py` is its only sender).
  - Tests pin all three. The gate stays the authority for members, in case a future tool builds the
    envelope.

**What it writes:**

- **Identity gate.** `is_attribution_bearing` returns `False` for `document.import` with `restore`.
  So the `NOT_A_PERSON` bypass exists only on this path, and the gate in the bullet above admits only
  the operator to it.
- **The actor is the operator.** 002's `ActorResolver.stamp` gives `ServeActorSource`'s
  `("operator", "operator")` for `LOCAL` and for the operator key. The event records who ran the
  restore; nothing claims a member did.
- **The event's `after`:** `{section, path, content_hash, source: "restore", restored_source:
  "upload"|"file", imported_by: <original handle>, original_seq: <n>}`. The `external_documents` row
  is written as for any import, with a new `imported_seq`.
- **Provenance of a restored row:**
  - `origin: "restore"`;
  - `restored_origin`: `"upload"` or `"file"`;
  - `imported_by`: the original handle, when it isn't `""`;
  - `original_seq`, when given.

  The D5 "replaced the copy … imported" line names the original `imported_by`.
- **The manifest's values are kept verbatim** (013 rev 2 at `90b1457`):
  - The stored record (the event's `after`) keeps the manifest's `source`, as `restored_source`, and
    its `imported_by`, unchanged, alongside `source: "restore"`.
  - Provenance shows them as `restored_origin` and `imported_by`, next to `origin: "restore"`.
  - Provenance can't reuse the key `source`, because `sections_with_provenance` already sets
    `source: "import"` on every imported section.
  - The lookup helper 013 reads (D4) maps a restored row back to `source = restored_source` and
    `imported_by = imported_by`, so a second 013 backup of a restored store writes the same manifest
    values as the first.
- **Identical text** already stored under the same path writes nothing (`commands.py:241-242`), so a
  restore into a store that already holds a row is a no-op for it, and is safe to run twice.

**In solo,** the base `Coordinator` has no allowlist, and every caller is the local user (`LOCAL`),
which is the operator rule's equivalent. No tool sends `restore`, so solo behaviour doesn't change.

## 5. How a member's sync is refused, and how their document arrives

```
ann's agent --HTTPS--> Caddy --> RequestGuard (006) --> auth (002) --> PrincipalLimits (006) --> tool
  backlog_sync(files=["notes/NOTE-123.md"])
    resync.backlog_sync: served_principal() -> (True, ann) -> action_refusal(HOST_SYNC_WHAT)   (D1, tool gate)
    -> "Error: importing hand edits of the host's .taskmaster/ files ... nothing was changed; on a team
        server, change the backlog with the backlog_* tools, and bring a document ... content=...)"
    (had the tool not answered: Client._send refuses `sync_job` for ann, and so would ServeCoordinator.dispatch)

  backlog_document_import(kind="task", entity_id="web-007", content={"spec": "<text of docs/specs/x.md>"})
    documents.document_import: declared docs["spec"] == "docs/specs/x.md"; normalize; size check     (D2, D3)
    call.execute("document.import", {..., "path": "docs/specs/x.md", "body": text, "source": "upload"})
      origin {"principal": ann's key id} -> ActorResolver.stamp -> actor {ann, agent, clone_id}        (D4)
    -> "- spec: imported from your upload as docs/specs/x.md (sha256 ...)"
```

## 6. The seam with 005

- **Order.** 012 is built on 005 merged into `feat/team-collab`. It edits 005's `allowlist.py` and
  `documents.py`, and it rewrites four of 005's tests (plan Step 1). It does not start until that
  merge.
- **Ownership.** 005 owns the gates, the method table, `served_doc_text` and the path rule. 012 owns
  which sync forms are remote (it moves `sync`/`sync_job` out of `REMOTE`), the content path, and D7.
- **What 005 does with its finding 2.** 005's review records finding 2 as owned by 012 and doesn't fix
  it. 012's plan Step 1 closes it. Step 1 is independent of Steps 2 to 5, touches three source files,
  and can merge on its own (Q4).
- **Finding 1** is 012's (ruling Q2): D7, plan Step 5. 005 records it as owned by 012, as it does
  finding 2.
- **Finding 3** (`backlog_blast_radius` evidence mode reading a caller-chosen `worktree` root) is not
  on the import surface. It belongs to 011 (per-principal machine-local fields), or to 015.
- **Go-live.** D1 closes an Important host-file read. Addendum §13 issues no key before 005, 006 and
  015; 012's Step 1 joins that list.

## 7. Seams with the parallel lanes

| Lane | Overlap | Files both may touch | Note |
|---|---|---|---|
| 003 sessions | `call.session` / `caller_scope` used by the content import's `execute` | `backlog_server.py` (003 edits `SESSION_ID` sites; 012 edits only the `backlog_document_import`, `backlog_sync` and resync docstrings and signature) | textual conflicts at most; no shared function |
| 007 read endpoint | if it serves document sections, it should pass `origin` and `imported_by` through | none expected (`serve/` read endpoint vs `native/documents.py`) | provenance is additive; 007 pins its own response schema |
| 009 split-brain fence | 009 is why teammates have no `.taskmaster/` to sync, which D1 relies on | none (`native_routing/gate.py`) | 009's refusal points to the remote MCP; 012's sync refusal points to the tools and `content` |
| 013 state export | **012 → 013 (review I1; orchestrator ruling):** 013 backs up `external_documents`, uploads included, and restores it. After 012 an upload can be the host's only copy, and no projection file carries the table. D1 and D7 are deliberately independent of whether the host tracks `.taskmaster/` | none (013 reads the table and calls 012's provenance helper; 012 doesn't change the table's shape) | **The shared restore contract (D10, orchestrator ruling; the same text is in 013).** The manifest records per document: section, declared path, sha256, source (`upload`/`file`), imported_by and imported_seq, and the body. 013 restores through `document.import(restore=True)`, which only the host-local operator may send. It keeps the original imported_by, records `origin: "restore"`, and is attributed to the operator. 013 needs no `domain_events` backup for this. Until 013 lands, a lost-disk restore silently turns every uploaded section into `not_imported` (R6). 012's docstring states the dependency (D2) |
| 016 CI/deploy | the Linux lane runs D7's symlink tests for real; Step 5's junction and directory cases are red on Windows without symlink rights (review M6) | none | the how-to should say teammates import documents with `content`, and that uploads are backed up only by 013's state backup |
| 004 receipts | refusing `sync_job` for members also closes a cross-principal `sync_id` poll | `allowlist.py` (004 moves `receipt`, `cancel`, `sync_status` to `REMOTE`) | adjacent lines; trivial conflict |
| 011 machine-local fields | finding 3 | none | §6 |

## 8. Files 012 changes

| File | Change | Step |
|---|---|---|
| `taskmaster/coordinator/allowlist.py` | `sync`, `sync_job` out of `REMOTE`; `HOST_SYNC_WHAT`, `REMOTE_SYNC_HINT` (Step 1); the restore rule for `execute`, `RESTORE_WHAT` (Step 2b) | 1, 2b |
| `taskmaster/native_routing/resync.py` | tool gate in `backlog_sync`, `handover_resync`, `issue_resync` | 1 |
| `taskmaster/backlog_server.py` | `backlog_document_import` signature and docstring; one docstring sentence each for `backlog_sync` and the resync tools ("on a team server only the host operator can run it") | 1, 4 |
| `taskmaster/native/documents.py` | `validate`: optional `source` (`"upload"` only), `expected_hash`; the restore form (D10); `apply`: the precondition, after the identical-text check; `retrieve`: `origin`, `imported_by` for uploads and restores | 2, 2b, 3 |
| `taskmaster/native/commands.py` | `import_document(..., source=None, restore=None)`: `source` into the event's `after` for uploads only; D10's `after` for restores | 2, 2b |
| `taskmaster/team_identity.py` | `is_attribution_bearing`: `document.import` with `source == "upload"` is attribution-bearing (review M5), unless `restore` (D10) | 2, 2b |
| `taskmaster/native/projection.py` | `validate_resolve` / `apply_resolve`: `replaced_hash` alone with `replaced_omitted: true` (D7, R-M1) | 5 |
| `taskmaster/native/batch_commands.py` | the `document.import` refusal text names `content` | 2 |
| `taskmaster/native_routing/documents.py` | the content path, normalization, shared size check, precondition, answers, serve hint | 4 |
| `tests/fixtures/native_contracts.json` | regenerated with `scripts/native_contract_inventory.py --write`; the reviewed diff touches only these tools' docs and parameters | 1, 4 |
| `taskmaster/native_routing/projection.py` | `read_file` delegates to `sync_files.observe(…, limit=git.MAX_PROJECTION_BYTES)`, and maps its refusals to D7's reasons | 5 |
| `taskmaster/native_routing/conflicts.py` | D7's compare text and `take="store"` refusal; `RESOLVE_INLINE_BYTES` and the hash-only resolution | 5 |

`taskmaster/serve/tracked.py` and 006's tests are not touched (review I2).
| `docs/native-store.md`, `CHANGELOG.md` | "On a team server" paragraph; unreleased entry | 6 |

## 9. Risks

- **R1. A teammate agent follows the server instructions** ("a hand edit … takes effect only after
  `backlog_sync()`", `backlog_server.py:230`) and meets the refusal. The refusal names the way forward.
  Serve-mode instructions are a follow-up (§11).
- **R2. 005's tests change under 005's feet.** Step 1 rewrites `test_plain_sync_is_remote`,
  `test_origin_travels_on_every_method_with_a_provider`,
  `test_remote_methods_pass_without_a_principal`,
  `test_sync_job_resolves_the_origin_in_the_calling_thread` and `test_remote_methods_pass_for_a_member`
  (`tests/test_serve_allowlist.py:329-338`, review M2). They are rewritten for the new rule (member
  refused; the operator for the origin checks), never deleted. Hence the strict order in §6.
- **R3. An operator who drives the daemon through the MCP over loopback** still syncs (bootstrap
  token, host-local). An operator who uses a member key does not, by design (005 D2).
- **R4. Last writer wins on documents** (D5; ruling Q3). The answer makes an overwrite visible.
- **R5. Provenance lookup cost:** one `domain_events` primary-key read per imported section on
  provenance reads.
- **R6. Uploads depend on 013's backup (review I1).** Until 013 backs up `external_documents`, a
  lost-disk restore of the host drops every uploaded section, with no notice. The teammate's own
  file is then the only copy. 012 states this in the tool docstring (D2) and in the §7 seam. 016's
  how-to repeats it.

## 10. Rollback

Revert the step commits.

- There is no schema or projection-format change.
- Events written with `source` (or D10's restore keys) stay valid: older readers ignore the extra
  keys.
- A hash-only resolution event (D7) has `file: null`, which older readers already accept for a file
  that was missing.
- Reverting Step 2b breaks 013's `restore-documents` for uploads in team mode, so revert 013's restore
  with it.
- Uploaded documents stay in `external_documents`, and are served exactly as file imports are.
- Reverting Step 1 reopens finding 2.

## 11. Out of scope

| Not here | Owner / why |
|---|---|
| A content-upload form of `backlog_sync` (send an edited projection file) | ruled out (Q1) |
| Backing up and restoring `external_documents` | 013 (§7, ruling on review I1) |
| Marking uploaded sections in the default, non-provenance render | declined (review M8, §12) |
| Serve-mode MCP instructions that drop the "hand edit then `backlog_sync()`" advice for teammates | follow-up for 015/016; the refusal text covers it meanwhile |
| Finding 3, the blast-radius `worktree` root | 011 or 015 (§6) |
| Validating stored `docs` paths when they are set | 005 F1 note; 015 |
| Receipt, `cancel`, `sync_status` scoping | 004 |
| Declaring a `docs` path and importing in one call | not offered (D2) |

## 12. Rulings (user, 2026-11-01)

The user accepted every recommendation below. Each is a binding decision; no questions remain open.

- **Orchestrator calls on the design review.** I1: 013 backs up `external_documents` (§7). I2: conflict
  reads reuse `sync_files`' reader; no new reader in `serve/tracked.py` (D7).
- **Orchestrator call on the re-review (R-I1).** The shared 012/013 restore contract: the manifest
  records section, declared path, sha256, source, imported_by and imported_seq;
  `document.import(restore=True)` is accepted only from the host-local operator, keeps the original
  imported_by, records `origin="restore"`, and is attributed to the operator (D10). R-M1: conflict
  reads take a larger limit, so a large flagged file can still be cleared (D7).
- **Review M8, declined.** Marking uploads in the default `backlog_get_task(sections=…)` render would
  change the answer shape agents parse, for every upload. `provenance=True` already shows `origin` and
  `imported_by`.

- **Q1. Should 012 also build a content-upload form of `backlog_sync`?** Ruled **no**.
  - A sync records the bytes it observed as the file's published base and manifest hash
    (`native/sync.py:177-185`). An upload that never reached the host's disk would be recorded as what
    the disk holds. The host's next sync would then see the real file as a hand edit, and could import
    the old text over the upload.
  - Fixing that changes the sync core.
  - Sync imports are attributed to the host (`sync_worker.py:347-353`), so attribution would need its
    own plumbing too.
  - The tools already edit every field, `content` covers documents, and addendum §8 places
    `backlog_sync` on the host. If a real need appears (bulk prose edits), file it as its own task.
- **Q2. Does 005's finding 1 (conflict-file reads) belong to 012?** Ruled **yes, as Step 5**, with
  D7's read through `sync_files.observe` (as revised for review I2). Reason: it is the same surface,
  and not tracked-only, because of D1's dependence on 013.
- **Q3. Should replacing another person's imported copy require `expected_hash`?** Ruled **no, for
  now**. Most imports are by the task's owner, and a mandatory precondition costs every
  first-time cross-person import a read round trip. The answer already names whose copy was replaced,
  and the precondition is there for agents that want it. Revisit after the first weeks of use.
- **Q4. Should Step 1 merge ahead of the rest of 012?** Ruled **yes**, once 005 has merged. It is a go-live
  blocker, three files and their tests, and needs only 005 merged. Steps 2 to 5 follow on the same
  branch.

## 13. Implementation plan (TDD)

**Conventions.**

- Run from the worktree with `C:\Users\gruku\Files\Claude\taskmaster\.venv\Scripts\python -m pytest`,
  in the foreground and serially, with `PYTHONIOENCODING=utf-8` and
  `--basetemp D:\scratchbook\taskmaster\ts012\bt-<step>`. Logs go to `D:\scratchbook\taskmaster\ts012\`.
- Each step writes its tests first, runs them red for the stated reason, implements, runs them green,
  then runs the step's regression subset. Then it commits code and tests together.
- Reuse the fixtures:
  - `served` from `tests/test_serve_doc_reads.py` (a native store in a git work tree,
    `compatibility_client`, a member provider);
  - the HTTP daemon fixtures and the throwaway-tool pattern from `tests/test_serve_allowlist_e2e.py`;
  - `root` from `test_native_service_build`.

### Step 0. Base

Merge `feat/team-collab` once 005 is in it. Run 005's serve set:
`tests/test_serve_allowlist*.py tests/test_serve_doc_reads.py`.

**Proving check:** green, apart from 005's known `test_concurrent_mutations_serialise` Windows flake,
which passes when rerun alone.

### Step 1. Remote path sync refused (D1; closes 005 finding 2)

**Tests first:**

- `tests/test_serve_allowlist.py`:
  - Replace `test_plain_sync_is_remote` with `test_plain_sync_is_host_only`. For a member and for
    `None`: `refusal("sync", {...})` with no files and with a list, and `refusal("sync_job", {...})`,
    contain `HOST_SYNC_WHAT` and `REMOTE_SYNC_HINT`. The operator gets `None`.
  - Add `"sync:plain"` and `"sync_job"` to `ARGS`, so the existing
    `test_admin_methods_are_refused_in_the_daemon_without_the_operator` and
    `test_the_operator_passes_the_client_gate` cover them.
  - `test_origin_travels_on_every_method_with_a_provider`: sync with the operator provider.
  - `test_remote_methods_pass_without_a_principal`: drop `sync`; add `test_sync_without_a_principal_is_refused`.
  - `test_sync_job_resolves_the_origin_in_the_calling_thread`: operator provider.
  - `test_remote_methods_pass_for_a_member` (`:329-338`, review M2): keep `status` and `flush` for the
    member; move `sync` into a new operator case, and assert the member's `sync` is refused at the
    serve coordinator.
- New `tests/test_serve_remote_sync.py`, in a `served`-style fixture. The host has an untracked, valid
  `.taskmaster/notes/NOTE-900.md` whose title holds `NOTE-MARKER-9`, and
  `sync_files.discover`/`sync_files.observe` are monkeypatched to raise `AssertionError`.
  - For each of `backlog_sync()`, `backlog_sync(files=["notes/NOTE-900.md"])`,
    `backlog_sync(sync_id="<a valid-shaped id>")`, `backlog_handover_resync()` and
    `backlog_issue_resync()`, as a member: the answer starts `Error:` and holds the D1 text; no marker
    appears; no `AssertionError` fires.
  - After those calls, `backlog_search("NOTE-MARKER-9")` finds nothing, and the store has no NOTE-900.
  - With the operator provider and the monkeypatch removed, `backlog_sync(files=["notes/NOTE-900.md"])`
    imports it, which proves the operator path is kept.
  - With no provider (solo), the same import works as today.
- `tests/test_serve_allowlist_e2e.py`, over HTTP:
  - the throwaway tool's plain `client.sync(files=None)` and `client.sync_job(...)` are refused for a
    member key and pass the gate for the operator;
  - finding 2's exact trigger: a member key calls `backlog_sync(files=["notes/NOTE-900.md"])`, then
    reads; the marker never comes back.

**Red because** today a member's sync imports the note (the marker comes back), and the unit tests see
`None`.

**Implement:** `allowlist.py`, the tool gate in `resync.py`, and the docstring sentences in
`backlog_server.py`. Then regenerate the contract fixture and review its diff.

**Proving checks:**

- green;
- a mutation (local only, restored with `git checkout`): put `"sync_job"` back in `REMOTE`, and the
  e2e and unit tests fail;
- `tests/test_native_resync.py`, `tests/test_native_service_sync.py` and
  `tests/test_native_resolve_take_file.py` pass unchanged.

### Step 2. `document.import` takes `source` and `expected_hash` (D4, D5)

**Tests first,** in `tests/test_native_documents.py`:

- `validate` accepts the old five arguments, and those plus `source="upload"` and/or `expected_hash`
  (64 hex or `""`). It refuses any other `source` value (`"file"` included), a malformed hash and
  unknown keys.
- An upload import writes an event whose `after` has `source: "upload"`. A file import's `after` is
  exactly today's `{section, path, content_hash}` (review M3).
- With `expected_hash` matching, it imports. A mismatch raises `Conflict`, and writes no
  `external_documents` row and no event. `""` passes only when no copy is stored under the declared
  path.
- A row stored under an older path: `""` passes, and a hash fails with the `nothing is imported under
  <path>` text (review M4b).
- Identical text with a stale `expected_hash` answers unchanged, with no write and no `Conflict`
  (review M4a).
- Re-importing identical text writes nothing, whatever the `source`.
- The same `request_id` replays to the same receipt.
- An envelope without the new keys has the same fingerprint as before (receipts replay).
- `team_identity.is_attribution_bearing` (review M5): `document.import` with `source="upload"` is
  attribution-bearing, and without `source` it is not. On a team-mode serve coordinator:
  - an upload as the operator is refused with 002's `NOT_A_PERSON` text, and nothing is written;
  - an upload by a member whose profile was deleted is refused;
  - a member's first write being an upload creates the member's profile and upgrades the store, so
    `imported_by` is present;
  - a file import as the operator still works.

**Red because** `validate` refuses the extra keys, and `is_attribution_bearing` answers `False` for
every `document.import`.

**Implement:** `native/documents.validate` and `apply`, `commands.import_document(source=None)`,
`team_identity.is_attribution_bearing`, and the `batch_commands.py` text.

**Proving check:** green; `tests/test_native_routing_documents.py` and `tests/test_native_contracts.py`
pass.

### Step 2b. The restore path (D10; the shared contract with 013)

**Tests first:**

- In `tests/test_native_documents.py`, `validate` on the restore form:
  - it accepts `restore=True` with `source` `"upload"` or `"file"`, `imported_by` (a handle or `""`)
    and an optional `original_seq`;
  - it refuses: `restore` that isn't `True`; a missing `source` or `imported_by`; a bad handle;
    `expected_hash` with `restore`; and `imported_by` or `original_seq` without `restore`.
- In `tests/test_serve_allowlist.py`:
  - `refusal("execute", {"envelope": <restore import>}, …)` answers `RESTORE_WHAT` for a member and for
    `None`, and `None` for `OPERATOR`;
  - a member's ordinary `execute` still answers `None`;
  - in the daemon (member provider), `Client.execute` of a restore envelope raises before the message
    is sent.
- New `tests/test_serve_document_restore.py`, on a team-mode `ServeCoordinator` with a registry
  holding `ann` (member) and `ops` (a member key with role `operator`):
  - **A member can't restore.** Origin ann, and origin ops, each submit a restore of an upload. Each is
    refused with the D10 text, writes no row and no event, and never reaches the identity gate (so no
    `NOT_A_PERSON` text).
  - **The operator restores an upload with the original imported_by intact.** Origin `operator`, and
    `LOCAL` (a host process), each restore `{source: "upload", imported_by: "ann", original_seq: 812}`.
    There is no `NOT_A_PERSON`. The provenance reads `origin: "restore"`, `restored_origin:
    "upload"`, `imported_by: "ann"`, `original_seq: 812`. The event's actor handle is `operator`.
  - A restore of `source: "file"` passes, and reads `restored_origin: "file"`.
  - **Restoring twice** is a no-op the second time.
  - **The record keeps the manifest's values.** After the operator restores
    `{source: "upload", imported_by: "ann"}`:
    - the event's `after` has `source: "restore"`, `restored_source: "upload"` and
      `imported_by: "ann"`;
    - the provenance has `origin: "restore"`, `restored_origin: "upload"` and `imported_by: "ann"`.

    The same holds for `{source: "file", imported_by: "bob"}`.
  - **A second backup reproduces the same values.** The helper 013 reads (D4) returns
    `source: "upload"`, `imported_by: "ann"` for the restored row, not `restore`/`operator`. Then, for
    both an upload and a file import:
    - restoring that row into a second fresh store and reading the helper again gives the same pair;
    - section, path and sha256 match too.
- **The restore path stays out of the tool surface** (013 final review):
  - New `tests/test_document_import_schema.py`, solo, no daemon: the registered
    `backlog_document_import` tool's input schema (`bs.mcp` tool listing) has no `restore`,
    `imported_by`, `original_seq` or `source` property.
  - Over HTTP, `tools/list` on the served instance gives the same schema for a member and for the
    operator.
  - A `backlog_batch_update` command carrying `document.import` with `restore` is refused, and writes
    nothing.
  - A source scan finds `"document.import"` passed to `call.execute` only in
    `native_routing/documents.py`.
  - In `tests/test_serve_content_import.py` (HTTP e2e), a throwaway tool that sends a restore envelope
    under a member key is refused.
- **Solo is unchanged.** Every existing solo suite passes. A base `Coordinator` accepts a restore from
  `LOCAL`, and nothing else in solo sends one.

**Red because** `validate` refuses `restore`, the allowlist passes every `execute`, and the identity
gate refuses an operator upload with `NOT_A_PERSON`.

**Implement:**

- `allowlist.refusal`'s `execute` rule;
- `native/documents.validate` and `apply`;
- `commands.import_document(restore=…)`;
- `team_identity.is_attribution_bearing`'s restore exemption;
- the restore branch of the provenance helper (Step 3 uses it).

**Proving checks:**

- green;
- a mutation: drop the `execute` rule from `allowlist.refusal`, and the member-restore cases fail;
- `tests/test_serve_identity.py`, `tests/test_identity_gate.py` and `tests/test_team_identity.py`
  pass.

### Step 3. Provenance shows `origin` and `imported_by` (D4)

**Tests first,** in `tests/test_native_documents.py` and `tests/test_native_routing_documents.py`:

- On a schema-5 store with an actor source returning `("ann", key)`, `retrieve` gives an uploaded
  section's provenance `origin: "upload"` and `imported_by: "ann"`, and
  `backlog_document(provenance=True)` renders both.
- A file import's provenance, and a pre-012 event's, are exactly today's: no `origin`, no
  `imported_by` (review M3).
- On a schema-4 store, an upload's provenance has `origin` but no `imported_by`.

**Red because** the keys are missing.

**Implement:** the `domain_events` lookup in `retrieve`.

**Proving check:** green; `tests/test_server_task_detail.py` passes.

### Step 4. The content contract in the tool (D2, D3, D5, D6)

**Tests first, solo,** in `tests/test_native_routing_documents.py`, with no file on disk at the
declared path:

- `content={"spec": "A\r\nB"}` imports, and `backlog_get_task(sections=["spec"])` then serves `A\nB`.
  Its hash equals a file import of the same text with LF endings.
- `sections` and `content` together → the D2 error.
- An undeclared section, an unknown section, an unpaired surrogate, and 1 MiB + 1 byte each give the
  exact per-section `FAILED` reasons. The other sections in the call still import.
- A stale `expected_hash` → the D5 conflict line, and the store is unchanged.
- An overwrite by a different handle (two actor stubs) → the "replaced the copy … imported" suffix.
- A subprocess check: a solo content import never imports `taskmaster.serve`.

**Tests first, served,** in new `tests/test_serve_content_import.py`, in-process with the `served`
fixture:

- a member imports content for `docs/specs/only-on-my-machine.md`, which the host doesn't have;
- the event's actor has `handle` equal to the member's handle and `clone_id` equal to the key id;
- a path import of an untracked host file → 005's refusal plus the D6 `content=` hint.

**Tests first, served, HTTP e2e** (same file, the daemon fixtures from
`test_serve_allowlist_e2e.py`, two member keys):

- Ann imports, and Bob's import with Ann's old hash as `expected_hash` → conflict;
- Bob without `expected_hash` → replaced, and the answer names Ann;
- a request body over `--max-body-bytes` (set low in the fixture) → 413, and nothing is imported.
  This one is a **006 regression guard**, not a red test: it passes before 012 (review M6).

**Red because** the tool has no `content` parameter (every case but the 413 guard).

**Implement:**

- the signature and docstring in `backlog_server.py`;
- the content path in `native_routing/documents.py`, sharing the size check and answer lines with the
  file path (one helper, not two);
- regenerate the contract fixture.

**Proving checks:**

- green;
- a mutation: drop `source="upload"`, and the provenance assertions fail;
- 005's `tests/test_serve_doc_reads.py` passes unchanged.

### Step 5. Conflict-file reads through `sync_files.observe` (D7; ruling Q2, review I2)

**Tests first,** in new `tests/test_native_conflict_reads.py`. D7 applies in every mode, so these run
on a solo store with a flagged `tasks/<id>.md`. Each case puts an outside directory or file holding
`CONFLICT-MARKER-7` behind the flagged path.

- **Junction (Windows; red here without any privilege; review M6).** Replace `.taskmaster/tasks` with a
  directory junction (`_winapi.CreateJunction`, or `cmd /c mklink /J`) to an outside directory holding
  a `<id>.md` with the marker. Then:
  - `backlog_resolve_conflict(file=…)` shows D7's `it is a link or junction` text, and no marker;
  - `take="store"` answers D7's error, the flag stays, and no event holds the marker.
  The case skips on POSIX, where the symlink case covers it.
- **A directory at the flagged path** (every OS): the compare and `take="store"` give `it is not a
  regular file`.
- **Symlinks** (skip when the OS refuses links, as `test_serve_doc_reads.py` does): the flagged file
  itself as a symlink to the outside marker file, and a symlink from one `.taskmaster/` file to
  another (the case the previous draft's rule would have followed). Both are refused.
- **Large files (re-review R-M1):**
  - A 2 MiB regular flagged file compares, and `take="store"` clears the flag with a hash-only
    resolution. The event has `file_omitted: true`, the answer names the size, and the next export
    replaces the file. This is red today: the envelope refuses the inline bytes.
  - A 600 KiB file still resolves with its bytes inline.
  - Over `MAX_PROJECTION_BYTES` (monkeypatched low in the test): `it is over the 64 MiB projection
    limit`.
  - `validate_resolve` still refuses `replaced_hash` alone without `replaced_omitted: true`.
- **A regular flagged file:** the compare shows its bytes, and `take="store"` records them, as today.
- **Served:** in a `served`-style fixture, the junction case answers the same for a member key, and
  for the operator.

**Red because** `read_bytes()` follows the junction (and the link), and reads a directory as an
`OSError` rather than a refusal.

**Implement:**

- `projection.read_file` delegates to
  `sync_files.observe(backlog_dir, rel, limit=git.MAX_PROJECTION_BYTES)`, and maps `UnsafePath`,
  `ValueError` and `ChangedDuringRead` to D7's reasons;
- the D7 texts, `RESOLVE_INLINE_BYTES` and the hash-only send in `conflicts.py`;
- `native/projection.validate_resolve` and `apply_resolve` for `replaced_omitted`.

Leave `serve/tracked.py` untouched.

**Proving checks:**

- green;
- a mutation: restore the `read_bytes()` body, and the junction case fails on Windows;
- `tests/test_native_projection_resolve.py`, `tests/test_native_resolve_take_file.py`,
  `tests/test_native_bypass_gate.py` and 006's `tests/test_serve_viewer.py` pass unchanged.

### Step 6. Docs

- `docs/native-store.md` gets "On a team server": members import documents with `content`, and only
  the host operator runs `backlog_sync` and the resync tools.
- `CHANGELOG.md` gets an unreleased entry.

**Proving check:** `tests/test_packaging.py` and `tests/test_server_instructions.py` pass.

### Regression set (run on the final tip, serially)

- **Serve:** `tests/test_serve_*.py`, `tests/test_viewer_serve_hooks.py`.
- **Native:**
  - `tests/test_native_documents.py`, `tests/test_native_routing_documents.py`;
  - `tests/test_native_resync.py`, `tests/test_native_service_sync.py`;
  - `tests/test_native_resolve_take_file.py`, `tests/test_native_projection_resolve.py`,
    `tests/test_native_conflict_reads.py`, `tests/test_native_bypass_gate.py`;
  - `tests/test_serve_document_restore.py` (in the serve glob above; listed for 013's lane, which
    must run it too);
  - `tests/test_native_service.py`, `tests/test_native_service_identity.py`, `tests/test_team_identity.py`, `tests/test_identity_gate.py`,
    `tests/test_native_service_actor.py`, `tests/test_native_service_adapter.py`;
  - `tests/test_native_contracts.py`, `tests/test_packaging.py`, `tests/test_server_instructions.py`,
    `tests/test_server_task_detail.py`.
- **Full suite:** at the review gate, with `-n 3` (memory `test-suite-speed`).

**Done when:**

- a member key imports a document by content, attributed to that member;
- no member call on the sync surface makes the host read a `.taskmaster/` file into the store;
- the operator's path sync still works;
- solo suites pass unchanged.
