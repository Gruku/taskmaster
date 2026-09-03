<!-- User intent: implementation plan for the derived SQLite index + ambient edit hook + backlog_query +
     handover paste block, so fresh-context implementers can build it task by task against the spec. -->

# Derived Index & Ambient Resurfacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the agent a derived SQLite index of the whole backlog, an edit-time hook that surfaces open work for the file being edited, a read-only SQL tool, and a Telegram-ready handover paste block.

**Architecture:** `taskmaster/index.py` builds `.taskmaster/local/index.db` incrementally from `backlog.yaml` plus every entity file; the MCP server refreshes it inside `_load()` so any tool call keeps it current. A stdlib-only PostToolUse hook reads the DB (never builds it) and prints one line. `backlog_query` exposes guarded SELECTs over the same DB.

**Tech Stack:** Python 3.11, stdlib `sqlite3` with FTS5, PyYAML (server side only), pytest. No new dependencies.

**Spec:** `docs/specs/2026-09-03-derived-index-ambient-resurfacing-design.md`

## Global Constraints

- Files remain canonical. Nothing writes entity data through the index. Deleting `index.db` must always be safe.
- The hook script imports only the standard library. It must never import `yaml`, `fastmcp`, or the `taskmaster` package (hooks run under system Python, not the uv venv).
- Hook output is exactly one line of `additionalContext`, or nothing. No second line.
- Hook exit code is always 0. Exceptions are logged to `.taskmaster/local/hook.log`, never raised.
- New hooks.json entries use the exact command form `CLAUDE_HOOK_SCRIPT=<script>.py . "${CLAUDE_PLUGIN_ROOT}/hooks/run_hook.sh"` with `"timeout": 10`.
- Task `anchors` are relative to the task's `sub_repo`; bug and issue `location` entries are project-root relative and may carry a `:LINE` suffix.
- Status vocab: open task = `todo|in-progress|blocked|in-review`; open bug = `open|adopted`; open issue = `open|investigating`; open handover = `open`. Everything else counts as closed.
- Every new file starts with a 1–3 line user-intent header comment.
- Run the full suite with `uv run pytest -q` from the repo root before each commit.
- Commit per task on the current branch `design/derived-index-ambient-resurfacing`.

**TDD note (de-prescribed on purpose):** each task lists its tests and the implementation contract. Write the test first, watch it fail, implement, watch it pass, commit. The plan does not repeat those five micro-steps per task.

**Loader helpers you will reuse** (all in `taskmaster/taskmaster_v3.py`): `parse_frontmatter(text) -> (fm, body)`, `read_task_file(path)`, `load_v3(backlog_path)`, `load_v4(backlog_path)`, `iter_task_files(backlog_path)`, `list_bug_ids(bp, include_archive=True)`, `read_bug(bp, id)`, `list_issue_ids`, `list_handover_ids`, `list_decision_ids`, `list_idea_ids`, `handover_path`, `bug_path`. `SCHEMA_V4 = 4` lives there too; `backlog.yaml` `meta.schema_version` decides v3 vs v4 (CodeMaestro is still v3: tasks inline in `epics[].tasks[]`).

---

### Task 1: Index builder (`taskmaster/index.py`)

**Files:**
- Create: `taskmaster/index.py`
- Create: `tests/test_index_build.py`
- Create: `tests/fixtures/index_backlog/` (a small v3 `.taskmaster/` tree, see below)

**Interfaces:**
- Produces:
  ```python
  SCHEMA_VERSION = 1
  SCHEMA_SQL = """..."""                          # the CREATE statements below, as one executescript string
  DB_RELPATH = Path("local") / "index.db"          # relative to .taskmaster/

  @dataclass
  class IndexReport:
      built_at: str; full_rebuild: bool; files_ingested: int
      pending_files: list[str]; stale: bool; row_counts: dict[str, int]
      elapsed_ms: int; errors: list[str]

  def db_path(backlog_path: Path) -> Path
  def load_backlog_data(backlog_path: Path) -> dict      # v3/v4 dispatch, no fastmcp import
  def build_index(backlog_path: Path, data: dict | None = None, *, budget_s: float | None = None) -> IndexReport
  def open_ro(backlog_path: Path) -> sqlite3.Connection   # raises FileNotFoundError if missing
  def last_report(backlog_path: Path) -> IndexReport | None   # from meta table
  def normalize_task_anchor(anchor: str, sub_repo: str | None) -> tuple[str, str]  # (path, match_kind)
  def normalize_location(loc: str) -> str                 # strips :LINE, backslashes→/, leading ./
  def extract_prose_paths(text: str) -> list[str]
  def infer_repo(paths: list[str], repos: list[tuple[str, str]]) -> str | None   # repos = [(name, path_prefix)]
  ```
- CLI: `python -m taskmaster.index <project_root_or_backlog_path> [--full]` prints the report as JSON.

**Fixture** `tests/fixtures/index_backlog/.taskmaster/` contains:
- `backlog.yaml` (v3, `meta.schema_version: 3`) with one epic `eng` holding tasks:
  - `eng-001` status `in-progress`, `sub_repo: api`, `anchors: ["src/svc/model.py", "src/svc/"]`, `notes: "touches ModelUsageService"`, `links: [{type: depends_on, target: eng-002}]`
  - `eng-002` status `done`, no anchors, `notes: "see src/svc/model.py and B-001"`
- `project.yaml` with `repos: [{name: api, path: ./api}, {name: web, path: ./web}]`
- `bugs/B-001.md` status `open`, `location: ["api/src/svc/model.py:42"]`, `components: [billing]`, body mentions `api/src/svc/other.py`
- `bugs/B-002.md` status `fixed`, `location: ["api/src/svc/model.py", "api/src/svc/legacy.py"]`
- `issues/ISS-001.md` status `open`, `location: ["web/app/page.tsx"]`
- `handovers/2026-09-01-fixture-handover.md` status `open`, `task_ids: [eng-001, eng-002]`, `thread: fixture-thread`, body mentions `api/src/svc/model.py` and `C:\Users\x\.claude\projects\foo\abc.jsonl`
- `decisions/DEC-001.md` status `open`, `task_id: eng-001`
- `ideas/IDEA-001.md`
- `PROGRESS.md` stub (`## Changelog`)

Tests copy the fixture into `tmp_path` (`shutil.copytree`) so mtime edits are hermetic.

**Schema** (create exactly; `IF NOT EXISTS` on every statement):
```sql
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE sources(file TEXT PRIMARY KEY, mtime REAL, size INTEGER, kind TEXT);
CREATE TABLE entities(id TEXT PRIMARY KEY, kind TEXT, status TEXT, title TEXT, epic TEXT,
  phase TEXT, lane TEXT, repo TEXT, priority TEXT, created TEXT, updated TEXT,
  archived INTEGER DEFAULT 0, file TEXT);
CREATE TABLE entity_paths(entity_id TEXT, path TEXT, match_kind TEXT, source TEXT);
CREATE TABLE links(src TEXT, type TEXT, dst TEXT);
CREATE TABLE handovers(id TEXT PRIMARY KEY, thread TEXT, tldr TEXT, next_action TEXT,
  session_kind TEXT, branch TEXT, tip_commit TEXT, supersedes TEXT);
CREATE TABLE handover_tasks(handover_id TEXT, task_id TEXT);
CREATE TABLE related(a TEXT, b TEXT, via TEXT, weight INTEGER);
CREATE VIRTUAL TABLE entity_fts USING fts5(id UNINDEXED, kind UNINDEXED, title, body,
  tokenize='porter unicode61');
CREATE INDEX ix_paths_path ON entity_paths(path);
CREATE INDEX ix_entities_kind_status ON entities(kind, status);
CREATE INDEX ix_entities_repo ON entities(repo);
CREATE INDEX ix_links_dst ON links(dst);
CREATE INDEX ix_related_a ON related(a);
```
`meta` rows: `schema_version`, `built_at` (ISO UTC), `built_at_epoch` (float as str), `source_mtime_max` (float as str), `last_report` (JSON of IndexReport).

**Build algorithm:**
1. Open (or create) the DB at `db_path`. If `meta.schema_version != SCHEMA_VERSION` or any `sqlite3.DatabaseError` on open: delete the file and start a full rebuild. Write `.taskmaster/local/.gitignore` containing `*\n` if missing.
2. Enumerate source files: `backlog.yaml`, `project.yaml`, `tasks/*.md`, `epics/*.md`, `phases/*.md`, `bugs/*.md`, `bugs/archive/*.md`, `issues/*.md`, `handovers/*.md`, `handovers/archive/*.md`, `decisions/*.md`, `ideas/*.md`. Compare `(mtime, size)` with `sources`. If `backlog.yaml` or `project.yaml` changed, all tasks and epics are re-ingested (they come from `data`). Otherwise only changed per-file entities are re-ingested; rows for vanished files are deleted (`entities`, `entity_paths`, `links`, `handovers`, `handover_tasks`, `entity_fts` by id, `related` by a or b).
3. `budget_s`: check `time.perf_counter()` after each file; when exceeded, stop, leave remaining files in `pending_files`, set `stale=True`. `related` recompute is skipped on a stale build.
4. Tasks: from `data["epics"][*]["tasks"]` (call `load_backlog_data` when `data is None`). Columns: `kind='task'`, `epic`, `phase`, `lane`, `repo = sub_repo or infer_repo(...)`, `priority`, `created`, `updated = last_referenced or completed or started or created`, `archived = 1 if status=='archived' or 'archived' in task`, `file = tasks/<id>.md` if it exists else `backlog.yaml`. Paths: each anchor via `normalize_task_anchor`. Links: each `links[]` entry and each legacy `depends_on[]` id become `(src,type,dst)` plus the reverse row using the existing `REVERSE_TYPE` map from `taskmaster_v3`. FTS body = `title + notes + description + review_instructions`.
5. Epics: `kind='epic'`, title = name, FTS body = description.
6. Bugs and issues: frontmatter `status`, `title`, `severity` → `priority`, `discovered` → `created`, `location[]` via `normalize_location` with `source='location'`, `match_kind='exact'`; body prose via `extract_prose_paths` with `source='prose'` (dedupe against location rows). Bugs under `bugs/archive/` get `archived=1`. `adopted_into` (bug) and `related_tasks` (issue) become `links(src, 'relates_to', dst)`.
7. Handovers: `entities` row (`kind='handover'`, title = tldr, `created` = date) plus `handovers` row plus one `handover_tasks` row per `task_ids[]`. `supersedes` → `links(id,'supersedes',target)`. Body paths → prose rows. FTS body = tldr + next_action + body.
8. Decisions and ideas: `entities` row (title, status, created), `task_id` / `related_tasks` → `relates_to` links, body prose paths, FTS body.
9. After a non-stale build, recompute `related` from scratch:
   - `via='path'`: for every pair of distinct entities sharing a normalized exact path, or where one entity's exact path matches another's glob (`fnmatch`-style with `**`), insert `(min, max, 'path', shared_count)`. Only `source in ('anchors','location')` rows participate.
   - `via='handover'`: every pair of task ids in the same `handover_tasks` group.
10. Write `meta` rows, commit, return the report.

**`normalize_task_anchor(anchor, sub_repo)`:** replace `\` with `/`, strip leading `./`. If `sub_repo` and anchor does not already start with `sub_repo + "/"`, prefix it. If the result ends with `/`, return `(result + "**", "glob")`. If it contains `*` or `?`, return `(result, "glob")`. Else `(result, "exact")`.

**`extract_prose_paths(text)`:** regex `(?<![\w:/\\])((?:[A-Za-z0-9_.-]+/){1,}[A-Za-z0-9_.-]+\.(?:py|ts|tsx|js|jsx|cs|csproj|md|yaml|yml|json|toml|html|css|scss|sql|sh|ps1|cpp|h|hpp|c|rs|go|java|kt|swift|uasset|ini|cfg|txt))(?::\d+)?`. Drop matches containing `://`, matches whose first segment is a Windows drive or `Users`, and any path ending in `.jsonl` (already excluded by the extension list). Return unique, in order of first appearance.

**`infer_repo(paths, repos)`:** repos are `(name, prefix)` with prefix normalized (`./api` → `api`). Return the name of the longest prefix such that some path starts with `prefix + "/"`. Single-repo projects (no `repos` in project.yaml) return `None`.

**Tests** (`tests/test_index_build.py`), each against a fresh fixture copy:
```python
def test_full_build_row_counts(fixture_tm):
    rep = build_index(fixture_tm / "backlog.yaml")
    assert rep.full_rebuild and not rep.stale
    assert rep.row_counts["entities"] == 9   # 2 tasks, 1 epic, 2 bugs, 1 issue, 1 handover, 1 decision, 1 idea
    con = open_ro(fixture_tm / "backlog.yaml")
    assert con.execute("select repo from entities where id='eng-001'").fetchone()[0] == "api"
    assert con.execute("select repo from entities where id='B-001'").fetchone()[0] == "api"
    assert con.execute("select repo from entities where id='ISS-001'").fetchone()[0] == "web"

def test_anchor_normalization():
    assert normalize_task_anchor("src/svc/model.py", "api") == ("api/src/svc/model.py", "exact")
    assert normalize_task_anchor("src/svc/", "api") == ("api/src/svc/**", "glob")
    assert normalize_task_anchor("api/src/x.py", "api") == ("api/src/x.py", "exact")
    assert normalize_task_anchor(".\\src\\a.py", None) == ("src/a.py", "exact")

def test_location_strips_line_suffix():
    assert normalize_location("api/src/svc/model.py:42") == "api/src/svc/model.py"

def test_prose_paths_exclude_jsonl_and_urls():
    got = extract_prose_paths("see api/src/svc/other.py and https://x.y/a/b.py and C:\\Users\\x\\abc.jsonl")
    assert got == ["api/src/svc/other.py"]

def test_entity_paths_sources(fixture_tm):
    build_index(fixture_tm / "backlog.yaml")
    con = open_ro(fixture_tm / "backlog.yaml")
    rows = set(con.execute("select entity_id, path, match_kind, source from entity_paths").fetchall())
    assert ("eng-001", "api/src/svc/model.py", "exact", "anchors") in rows
    assert ("eng-001", "api/src/svc/**", "glob", "anchors") in rows
    assert ("B-001", "api/src/svc/model.py", "exact", "location") in rows
    assert ("B-001", "api/src/svc/other.py", "exact", "prose") in rows
    assert not any(p.endswith(".jsonl") for _, p, _, _ in rows)

def test_related_edges(fixture_tm):
    build_index(fixture_tm / "backlog.yaml")
    con = open_ro(fixture_tm / "backlog.yaml")
    rel = set(con.execute("select a, b, via from related").fetchall())
    assert ("B-001", "eng-001", "path") in rel          # shared exact path
    assert ("eng-001", "eng-002", "handover") in rel    # same handover task_ids
    assert ("B-001", "B-002", "path") in rel

def test_links_include_reverse(fixture_tm):
    build_index(fixture_tm / "backlog.yaml")
    con = open_ro(fixture_tm / "backlog.yaml")
    assert con.execute("select 1 from links where src='eng-002' and type='blocks' and dst='eng-001'").fetchone()

def test_incremental_reingest_on_mtime(fixture_tm):
    bp = fixture_tm / "backlog.yaml"
    build_index(bp)
    bug = fixture_tm / "bugs" / "B-001.md"
    bug.write_text(bug.read_text(encoding="utf-8").replace("status: open", "status: fixed"), encoding="utf-8")
    os.utime(bug, (time.time() + 5, time.time() + 5))
    rep = build_index(bp)
    assert not rep.full_rebuild and rep.files_ingested == 1
    con = open_ro(bp)
    assert con.execute("select status from entities where id='B-001'").fetchone()[0] == "fixed"

def test_removed_file_rows_deleted(fixture_tm):
    bp = fixture_tm / "backlog.yaml"
    build_index(bp)
    (fixture_tm / "bugs" / "B-002.md").unlink()
    build_index(bp)
    con = open_ro(bp)
    assert con.execute("select count(*) from entities where id='B-002'").fetchone()[0] == 0
    assert con.execute("select count(*) from entity_paths where entity_id='B-002'").fetchone()[0] == 0

def test_budget_marks_stale(fixture_tm, monkeypatch):
    bp = fixture_tm / "backlog.yaml"
    rep = build_index(bp, budget_s=0.0)
    assert rep.stale and rep.pending_files

def test_schema_bump_forces_full_rebuild(fixture_tm, monkeypatch):
    bp = fixture_tm / "backlog.yaml"
    build_index(bp)
    con = sqlite3.connect(db_path(bp)); con.execute("update meta set value='0' where key='schema_version'"); con.commit(); con.close()
    assert build_index(bp).full_rebuild

def test_corrupt_db_rebuilds(fixture_tm):
    bp = fixture_tm / "backlog.yaml"
    build_index(bp)
    db_path(bp).write_bytes(b"not a database")
    assert build_index(bp).full_rebuild

def test_fts_ranks_handover(fixture_tm):
    bp = fixture_tm / "backlog.yaml"
    build_index(bp)
    con = open_ro(bp)
    ids = [r[0] for r in con.execute("select id from entity_fts where entity_fts match 'ModelUsageService' order by bm25(entity_fts)")]
    assert "eng-001" in ids

def test_local_gitignore_written(fixture_tm):
    build_index(fixture_tm / "backlog.yaml")
    assert (fixture_tm / "local" / ".gitignore").read_text() == "*\n"

def test_cli_prints_report(fixture_tm):
    out = subprocess.run([sys.executable, "-m", "taskmaster.index", str(fixture_tm.parent)], capture_output=True, text=True, cwd=PLUGIN_ROOT)
    assert out.returncode == 0 and json.loads(out.stdout)["row_counts"]["entities"] == 9
```
`fixture_tm` fixture: copies `tests/fixtures/index_backlog/.taskmaster` to `tmp_path/.taskmaster`, returns that path.

- [ ] Write fixture tree and tests
- [ ] Implement `taskmaster/index.py` until green
- [ ] Commit: `feat(index): derived SQLite index builder with FTS5, paths, links, related edges`

---

### Task 2: Server integration — refresh in `_load()`, `backlog_index_status`, SessionStart warm

**Files:**
- Modify: `taskmaster/backlog_server.py` (`_load()` at ~392; add tool near `backlog_search` ~1418; `__main__` at ~8747)
- Modify: `backlog_server.py` (root shim, `__main__` at line 18) to forward `--build-index`
- Modify: `hooks/session-start.sh`
- Create: `tests/test_index_server_integration.py`

**Interfaces:**
- Consumes: `index.build_index`, `index.last_report`, `index.db_path`.
- Produces: MCP tool `backlog_index_status(rebuild: bool = False) -> str`; CLI flag `backlog_server.py --build-index [path]`.

**Changes:**
1. In `_load()`, after `_LOAD_SNAPSHOT` assignment and before `return data`:
   ```python
   try:
       from taskmaster import index as _index
       _index.build_index(bp, data, budget_s=1.5)
   except Exception as exc:  # index is derived; never break a tool call
       _log_index_error(exc)
   ```
   `_log_index_error` appends one line to `bp.parent / "local" / "index.log"` (create dir if missing, cap file at 1 MB by truncating to the last 512 KB when exceeded).
2. Add `backlog_index_status(rebuild: bool = False) -> str`: if `rebuild`, delete `db_path(bp)` then `build_index(bp)`; else `last_report(bp)` (build if none). Render:
   ```
   Index: <path>
   Built: <built_at>  (full rebuild: yes/no, stale: yes/no, <elapsed_ms> ms)
   Rows: entities=<n> entity_paths=<n> links=<n> handovers=<n> related=<n> fts=<n>
   Pending: <k> files  (list first 10)
   Errors: <k>  (list first 5)
   ```
3. `__main__` in `taskmaster/backlog_server.py`: if `"--build-index" in sys.argv`, resolve the backlog path (optional positional after the flag, else `_backlog_path()`), call `build_index`, print JSON of the report, `sys.exit(0)`. Root shim already forwards `sys.argv` by importing the module's main; verify and keep.
4. `hooks/session-start.sh`: after the existing context output, append
   ```bash
   if command -v uv >/dev/null 2>&1 && [ -f ".taskmaster/backlog.yaml" ]; then
     (uv run "${PLUGIN_ROOT}/backlog_server.py" --build-index >/dev/null 2>&1 &)
   fi
   ```
   Keep it after the JSON is printed so a slow uv start cannot delay the hook.

**Tests:**
```python
def test_load_refreshes_index(tmp_taskmaster, monkeypatch):
    # tmp_taskmaster fixture from conftest; write one v3 task via the server's own API
    import backlog_server as bs
    bs.backlog_add_epic("e1", "Epic one")
    bs.backlog_add_task("e1", "t1", "Task one")
    bs._load()
    assert (tmp_taskmaster / ".taskmaster" / "local" / "index.db").exists()

def test_index_status_reports_rows(tmp_taskmaster):
    import backlog_server as bs
    bs.backlog_add_epic("e1", "Epic one"); bs.backlog_add_task("e1", "t1", "Task one")
    out = bs.backlog_index_status()
    assert "entities=" in out and "Built:" in out

def test_index_status_rebuild(tmp_taskmaster):
    import backlog_server as bs
    bs.backlog_add_epic("e1", "Epic one")
    assert "full rebuild: yes" in bs.backlog_index_status(rebuild=True)

def test_load_survives_index_failure(tmp_taskmaster, monkeypatch):
    import backlog_server as bs
    from taskmaster import index
    monkeypatch.setattr(index, "build_index", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    bs.backlog_add_epic("e1", "Epic one")
    assert bs._load()["epics"]          # tool path unaffected
    assert "boom" in (tmp_taskmaster / ".taskmaster" / "local" / "index.log").read_text()

def test_build_index_cli_flag(tmp_taskmaster):
    out = subprocess.run([sys.executable, str(PLUGIN_ROOT / "backlog_server.py"), "--build-index", str(tmp_taskmaster / ".taskmaster" / "backlog.yaml")], capture_output=True, text=True, env={**os.environ, "TASKMASTER_ROOT": str(tmp_taskmaster)})
    assert out.returncode == 0 and "row_counts" in out.stdout
```
Check the exact names of the add-epic and add-task tool functions in `backlog_server.py` (`backlog_add_epic`, `backlog_add_task`) and their positional signatures before writing the tests; adjust arguments, not intent.

- [ ] Tests, then implementation
- [ ] Run `uv run pytest -q tests/test_hooks_json.py tests/test_hook_shims.py` too (session-start.sh is asserted unchanged in name only; confirm still green)
- [ ] Commit: `feat(index): refresh index on load, backlog_index_status tool, --build-index flag, SessionStart warm`

---

### Task 3: Ambient edit hook (`hooks/edit_resurface.py`)

**Files:**
- Create: `hooks/edit_resurface.py`
- Modify: `hooks/hooks.json` (new PostToolUse matcher `Edit|Write|MultiEdit`)
- Create: `tests/test_edit_resurface_hook.py`
- Modify: `tests/test_hooks_json.py` (add one test asserting the new entry's command form and timeout)

**Interfaces:**
- Consumes: `index.db` schema from Task 1 (read only). Hook payload fields: `session_id`, `cwd`, `tool_name`, `tool_input.file_path`, `tool_response` (present on PostToolUse).
- Produces: stdout JSON `{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "<one line>"}}` or nothing.

**Algorithm (stdlib only: `json, os, sys, sqlite3, fnmatch, re, time, pathlib`):**
1. Parse stdin JSON; on any failure exit 0 silently. Require `tool_name in {"Edit","Write","MultiEdit"}` and a string `file_path`.
2. Find project root: walk up from `cwd` (fallback: the file's parent) until a directory containing `.taskmaster/backlog.yaml`. None → exit 0.
3. Skip if the file is under `<root>/.taskmaster/` or outside `<root>`.
4. Relative path: `os.path.relpath(file, root)` with `/` separators. If it starts with `.worktrees/<name>/`, strip those two segments (worktree edits map to the same repo paths).
5. DB at `<root>/.taskmaster/local/index.db`. Missing → exit 0. Open `file:...?mode=ro` with `uri=True`.
6. Dedupe: `<root>/.taskmaster/local/hook-seen/<session_id>.json` holds a list of paths; if present, exit 0. On write, delete sibling files older than 7 days. Session id missing → use `"nosession"`.
7. Query:
   ```sql
   SELECT e.id, e.kind, e.status, p.match_kind, p.path, p.source
   FROM entity_paths p JOIN entities e ON e.id = p.entity_id
   WHERE (p.match_kind='exact' AND p.path=?) OR p.match_kind='glob'
   ```
   For glob rows apply `_glob_match(pattern, rel)` where `**` matches across `/` (translate `**` → `.*`, `*` → `[^/]*`, `?` → `[^/]`, anchor both ends).
8. Classify each matched entity once: open per Global Constraints; prose-only matches (every matching row has `source='prose'`) count toward `+N prose` and are not listed.
9. If no open ids and no closed and no prose → exit 0. If no open ids but closed/prose exist → exit 0 (silent; history lives behind `backlog_query`).
10. Line: `TM: <rel> → ` + ids ordered bug, issue, task, handover, each as `<id> <status>` (handover ids truncated to 24 chars + `…`, status omitted), max 6 then `+N more`; then ` (+N closed, +N prose)` omitting zero parts; then ` (index stale)` when `float(meta.source_mtime_max) < os.stat(backlog.yaml).st_mtime` or any of `bugs/ issues/ handovers/` dir mtimes exceed `built_at` epoch (store `built_at_epoch` in `meta` in Task 1 to make this a single compare — add that meta row in Task 1 if you are reading this before Task 1 ships).
11. Print the JSON via `json.dumps`, exit 0. Wrap everything after step 1 in `try/except Exception`: append `f"{time.time()} {exc!r}\n"` to `<root>/.taskmaster/local/hook.log` (truncate to last 512 KB when over 1 MB), exit 0.

**hooks.json addition** (inside `PostToolUse`):
```json
{
  "matcher": "Edit|Write|MultiEdit",
  "hooks": [
    { "type": "command",
      "command": "CLAUDE_HOOK_SCRIPT=edit_resurface.py . \"${CLAUDE_PLUGIN_ROOT}/hooks/run_hook.sh\"",
      "timeout": 10 }
  ]
}
```

**Tests** (subprocess harness copied from `tests/test_worktree_submodule_init_hook.py`; build a real index from the Task 1 fixture into `tmp_path` first via `build_index`, then create the edited file paths on disk):
```python
def payload(root, rel, tool="Edit", session="s1"):
    return {"session_id": session, "cwd": str(root), "tool_name": tool,
            "tool_input": {"file_path": str(root / rel)}, "tool_response": {"success": True}}

def test_open_items_one_line(indexed_root):
    r = run(payload(indexed_root, "api/src/svc/model.py"), indexed_root)
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert ctx.startswith("TM: api/src/svc/model.py → B-001 open, eng-001 in-progress, 2026-09-01-fixture-hando…")
    assert "(+1 closed" in ctx and "\n" not in ctx

def test_closed_only_is_silent(indexed_root):
    # api/src/svc/legacy.py is referenced only by B-002 (fixed) → closed-only → silent
    r = run(payload(indexed_root, "api/src/svc/legacy.py"), indexed_root)
    assert r.stdout == ""

def test_glob_anchor_matches_nested(indexed_root):
    r = run(payload(indexed_root, "api/src/svc/deep/new.py"), indexed_root)
    assert "eng-001 in-progress" in json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]

def test_prose_only_counted_not_listed(indexed_root):
    r = run(payload(indexed_root, "api/src/svc/other.py"), indexed_root)
    assert r.stdout == ""   # B-001 prose-only, no open exact/anchor → silent

def test_worktree_prefix_stripped(indexed_root):
    r = run(payload(indexed_root, ".worktrees/wt-1/api/src/svc/model.py"), indexed_root)
    assert "B-001 open" in r.stdout

def test_dedupe_per_session(indexed_root):
    run(payload(indexed_root, "api/src/svc/model.py"), indexed_root)
    r = run(payload(indexed_root, "api/src/svc/model.py"), indexed_root)
    assert r.stdout == ""
    r2 = run(payload(indexed_root, "api/src/svc/model.py", session="s2"), indexed_root)
    assert r2.stdout != ""

def test_outside_root_and_taskmaster_dir_skipped(indexed_root, tmp_path):
    assert run(payload(indexed_root, ".taskmaster/bugs/B-001.md"), indexed_root).stdout == ""
    other = tmp_path / "elsewhere.py"; other.write_text("x")
    p = payload(indexed_root, "x"); p["tool_input"]["file_path"] = str(other)
    assert run(p, indexed_root).stdout == ""

def test_missing_db_silent(fixture_root_without_index):
    assert run(payload(fixture_root_without_index, "api/src/svc/model.py"), fixture_root_without_index).stdout == ""

def test_stale_flag(indexed_root):
    bl = indexed_root / ".taskmaster" / "backlog.yaml"
    os.utime(bl, (time.time() + 60, time.time() + 60))
    assert "(index stale)" in run(payload(indexed_root, "api/src/svc/model.py"), indexed_root).stdout

def test_non_edit_tool_ignored(indexed_root):
    assert run(payload(indexed_root, "api/src/svc/model.py", tool="Read"), indexed_root).stdout == ""

def test_malformed_stdin_exit_zero(indexed_root):
    r = subprocess.run([sys.executable, HOOK], input="not json", text=True, capture_output=True, cwd=indexed_root)
    assert r.returncode == 0 and r.stdout == ""

def test_query_budget_under_50ms(tmp_path):
    # synthetic DB: 3000 entities, 6000 entity_paths (10% globs); time only the in-process resolve function
    from importlib import util
    spec = util.spec_from_file_location("edit_resurface", HOOK); mod = util.module_from_spec(spec); spec.loader.exec_module(mod)
    db = tmp_path / "index.db"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA_SQL)   # import SCHEMA_SQL from taskmaster.index (expose it as a module constant in Task 1)
    con.executemany("insert into entities(id,kind,status) values (?,?,?)",
                    [(f"t-{i}", "task", "todo" if i % 2 else "done") for i in range(3000)])
    con.executemany("insert into entity_paths values (?,?,?,?)",
                    [(f"t-{i}", f"repo{i%7}/src/mod{i}/file{i}.py", "exact", "anchors") for i in range(3000)] +
                    [(f"t-{i}", f"repo{i%7}/src/area{i%50}/**", "glob", "anchors") for i in range(0, 3000, 10)] +
                    [(f"t-{i}", f"repo{i%7}/src/mod{i}/extra.py", "exact", "prose") for i in range(3000)])
    con.execute("insert into meta values ('built_at_epoch', ?)", (str(time.time()),)); con.commit(); con.close()
    t = time.perf_counter(); mod.resolve(db, "repo1/src/area1/x.py"); assert (time.perf_counter() - t) < 0.05
```
Expose `resolve(db_path, rel) -> ResolveResult` and `format_line(rel, result, stale) -> str` as module-level functions so the timing test and the formatting tests can call them in-process.

- [ ] Tests, then hook, then hooks.json and the hooks_json test
- [ ] Commit: `feat(hook): ambient edit resurfacing — one-line open-work context per edited file`

---

### Task 4: `backlog_query` read-only SQL tool

**Files:**
- Modify: `taskmaster/backlog_server.py` (new tool after `backlog_index_status`)
- Create: `taskmaster/query_guard.py`
- Create: `tests/test_backlog_query.py`

**Interfaces:**
- Produces: `query_guard.validate(sql: str) -> str` (returns normalized SQL or raises `ValueError`), `query_guard.authorizer(action, arg1, arg2, dbname, source) -> int`, MCP tool `backlog_query(sql: str, limit: int = 50) -> str`.

**Guard rules (`validate`):** strip leading/trailing whitespace and a single trailing `;`. Reject if: empty; first keyword (case-insensitive, after stripping `--` and `/* */` comments) is not `SELECT` or `WITH`; any `;` remains outside single-quoted strings; regex `\b(ATTACH|DETACH|PRAGMA|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|VACUUM|REINDEX)\b` matches outside strings. Error message names the rule. **Authorizer:** allow `SQLITE_SELECT`, `SQLITE_READ` on the eight tables plus `entity_fts*` shadow tables and `sqlite_master`, `SQLITE_FUNCTION`; deny everything else (`sqlite3.SQLITE_DENY`).

**Tool:**
```python
@mcp.tool()
def backlog_query(sql: str, limit: int = 50) -> str:
    """Read-only SQL over the derived backlog index (.taskmaster/local/index.db). Use it to dig
    deeper than the one-line edit hook: closed history for a path, titles, related entities, FTS.

    Tables: entities(id,kind,status,title,epic,phase,lane,repo,priority,created,updated,archived,file)
      entity_paths(entity_id,path,match_kind,source) links(src,type,dst)
      handovers(id,thread,tldr,next_action,session_kind,branch,tip_commit,supersedes)
      handover_tasks(handover_id,task_id) related(a,b,via,weight) entity_fts(id,kind,title,body)
    kind: task|epic|bug|issue|handover|decision|idea. Open statuses: task todo|in-progress|blocked|in-review,
    bug open|adopted, issue open|investigating, handover open.
    Examples:
      SELECT id,status,title FROM entities WHERE kind='bug' AND repo='facade' AND status IN ('open','adopted')
      SELECT e.id,e.kind,e.status,e.title FROM entity_paths p JOIN entities e ON e.id=p.entity_id WHERE p.path LIKE '%ModelUsageService.cs'
      SELECT id,title FROM entity_fts WHERE entity_fts MATCH 'credit exhaustion' AND kind='handover' ORDER BY bm25(entity_fts) LIMIT 10

    Args:
        sql: one SELECT (or WITH ... SELECT). Writes, PRAGMA, ATTACH and multiple statements are rejected.
        limit: row cap, 1..500 (default 50).
    """
```
Body: clamp `limit` to `[1, 500]`; `validate(sql)`; ensure the index exists (`build_index` if missing); `con = open_ro(bp)`; `con.set_authorizer(authorizer)`; execute `f"SELECT * FROM ({sql}) LIMIT {limit + 1}"`; render an aligned table (header from `cursor.description`, cells `str(v)` truncated to 80 chars, `None` → empty); if `len(rows) > limit` drop the extra and append `\n{limit} rows (capped)`, else `\n{n} rows`. On `sqlite3.Error` or `ValueError`: return `f"Error: {exc}\n\nSchema: " + <the table list from the docstring>`.

**Tests:**
```python
@pytest.mark.parametrize("bad", ["DELETE FROM entities", "PRAGMA user_version", "ATTACH 'x' AS y", "SELECT 1; SELECT 2",
                                 "/* hi */ UPDATE entities SET status='x'", "WITH t AS (SELECT 1) DELETE FROM entities", ""])
def test_guard_rejects(bad):
    with pytest.raises(ValueError): validate(bad)

def test_guard_accepts_select_and_with_and_trailing_semicolon():
    assert validate("select 1;").lower().startswith("select")
    assert validate("WITH t AS (SELECT 1) SELECT * FROM t")

def test_guard_allows_semicolon_inside_string():
    assert validate("SELECT * FROM entities WHERE title='a;b'")

def test_query_returns_table_and_caps(indexed_server):
    out = backlog_query("SELECT id FROM entities ORDER BY id", limit=2)
    assert out.count("\n") >= 3 and "2 rows (capped)" in out

def test_query_error_includes_schema(indexed_server):
    out = backlog_query("SELECT nope FROM entities")
    assert out.startswith("Error:") and "entity_paths(" in out

def test_authorizer_blocks_sqlite_master_writes_and_functions(indexed_server):
    # a SELECT that calls a write-capable function must still fail under the authorizer
    out = backlog_query("SELECT writefile('x.txt','y')")
    assert out.startswith("Error:")

def test_limit_clamped(indexed_server):
    assert "rows" in backlog_query("SELECT 1", limit=10_000)
```
`indexed_server` fixture: `tmp_taskmaster` plus the Task 1 fixture copied in and `build_index` run, `bs.ROOT` pointed at it.

- [ ] Tests, guard, tool
- [ ] Commit: `feat(query): backlog_query read-only SQL tool over the derived index`

---

### Task 5: `backlog_search` upgrade (FTS5 + kinds)

**Files:**
- Modify: `taskmaster/backlog_server.py:1418-1467`
- Create: `tests/test_backlog_search_fts.py`

**Interfaces:**
- Produces: `backlog_search(query: str, kinds: list[str] | None = None) -> str`. Output shape unchanged: header `**N match(es)** for `q`:` then `- \`id\` — title (priority, epic, status)` lines, max 15. Non-task kinds render `- \`id\` — title (kind, status)`.

**Behavior:** if the index opens and `entity_fts` exists: run
```sql
SELECT f.id, e.kind, e.status, e.title, e.priority, e.epic, bm25(f) AS rank
FROM entity_fts f JOIN entities e ON e.id = f.id
WHERE entity_fts MATCH ? [AND e.kind IN (?, ...)]
ORDER BY rank LIMIT 15
```
with the query sanitized for FTS: split on whitespace, quote each token (`"tok"`), join with spaces, so punctuation like `-` or `:` cannot break the MATCH grammar; on `sqlite3.OperationalError` fall through. Fallback (index missing or any error): the existing substring scan, unchanged, restricted to tasks. `kinds=None` means all kinds. Header counts total matches (run a second `COUNT(*)` with the same WHERE).

**Tests:**
```python
def test_search_finds_bug_and_handover(indexed_server):
    out = backlog_search("ModelUsageService")
    assert "eng-001" in out

def test_search_kinds_filter(indexed_server):
    out = backlog_search("model", kinds=["bug"])
    assert "B-00" in out and "eng-001" not in out

def test_search_punctuation_does_not_error(indexed_server):
    assert "Error" not in backlog_search("abuse-path-001: thing")

def test_search_fallback_without_index(tmp_taskmaster, monkeypatch):
    from taskmaster import index
    monkeypatch.setattr(index, "open_ro", lambda bp: (_ for _ in ()).throw(FileNotFoundError()))
    bs.backlog_add_epic("e1", "Epic one"); bs.backlog_add_task("e1", "t1", "Widget frobnicator")
    assert "t1" in bs.backlog_search("frobnicator")
```
Also run the existing search tests (`grep -l backlog_search tests/`) and keep them green.

- [ ] Tests, implementation
- [ ] Commit: `feat(search): backlog_search uses FTS5 across all entity kinds with substring fallback`

---

### Task 6: Handover paste block and reverse lookup

**Files:**
- Modify: `taskmaster/backlog_server.py:2181-2193` (`backlog_handover_create` response)
- Modify: `playbooks/handover/playbook.md` (steps 2, 9)
- Modify: `playbooks/handover/references/auto-extraction.md` (new source 7)
- Modify: `tests/test_handover_skill_lint.py`, `tests/test_api_handover_status.py` or the test that covers `backlog_handover_create` output (find with `grep -l "Handover written" tests/`)

**Server change:** after the `- File:` line add `f"- Path: {target.resolve()}"` (absolute, native separators).

**Playbook step 9 replacement text:**
```
**9. Confirm with the paste block.** End your reply with exactly this fenced block and nothing after it — the user copies it into a chat as the durable pointer:

    ```
    <tldr>
    <absolute path from the server's `- Path:` line>
    Resume: <thread> — <next_action>
    ```

The `Resume:` line is the server's final line verbatim; pasting the thread name into any future session resumes via `backlog_thread_resume`. Surface any WARNING line from the response above the block, never inside it.
```

**Playbook step 2 addition** (append one sentence): `Then run source 7 in references/auto-extraction.md — the index reverse lookup — and propose its open ids for task_ids; for open bugs whose location you edited, ask in one line whether this session fixed them. Never change a status without the answer.`

**auto-extraction.md source 7:**
```
7. **Index reverse lookup** — for every Touched path, `backlog_query("SELECT DISTINCT e.id,e.kind,e.status,e.title FROM entity_paths p JOIN entities e ON e.id=p.entity_id WHERE p.source IN ('anchors','location') AND (p.path='<rel>' OR (p.match_kind='glob' AND '<rel>' GLOB p.path))")`. Open results feed `task_ids` (tasks) and the fix question (bugs). Skip silently when `backlog_query` reports a missing index.
```
Note: SQLite `GLOB` treats `*` as matching `/` too, which is acceptable here (over-inclusion is cheap per the file's own rule).

**Tests:**
```python
def test_handover_create_returns_absolute_path(tmp_taskmaster):
    out = bs.backlog_handover_create(tldr="Did a thing", next_action="Do next")
    line = next(l for l in out.splitlines() if l.startswith("- Path: "))
    assert Path(line[len("- Path: "):]).is_absolute() and Path(line[len("- Path: "):]).exists()

def test_playbook_step9_defines_paste_block():
    text = (PLAYBOOK_DIR / "playbook.md").read_text(encoding="utf-8")
    assert "paste block" in text and "- Path:" in text and "Resume: <thread>" in text

def test_auto_extraction_has_reverse_lookup_source():
    text = (PLAYBOOK_DIR / "references" / "auto-extraction.md").read_text(encoding="utf-8")
    assert "7. **Index reverse lookup**" in text and "backlog_query" in text
```
Keep `test_skill_body_within_budget` green; if the playbook exceeds its token budget, trim prose in steps 4 to 7 rather than the new content.

- [ ] Tests, server line, playbook edits
- [ ] Commit: `feat(handover): paste block with absolute path; index reverse lookup in auto-extraction`

---

### Task 7: Release bookkeeping and submodule bump

**Files:**
- Modify: `CHANGELOG.md`, `pyproject.toml`, `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json` → version `5.2.0`
- Modify: `README.md` (one short section "Derived index and ambient resurfacing": what the hook prints, `backlog_query`, `backlog_index_status`, that `local/index.db` is disposable)
- Modify: `docs/TASKMASTER.md` if it lists MCP tools (add the two new tools; check with `grep -n backlog_search docs/TASKMASTER.md`)

**CHANGELOG entry** under a new `## 5.2.0` heading, four bullets: derived index (`local/index.db`, rebuilt on load, disposable); ambient edit hook (one line, open items only, `(+N closed)`); `backlog_query` and `backlog_index_status`; `backlog_search` now FTS5 across all kinds; handover paste block with absolute path.

Then, outside this repo, after all tests pass:
```bash
git -C C:/Users/gruku/Files/Claude/claude-tools/plugins/taskmaster fetch C:/Users/gruku/Files/Claude/taskmaster design/derived-index-ambient-resurfacing
git -C C:/Users/gruku/Files/Claude/claude-tools/plugins/taskmaster checkout FETCH_HEAD
git -C C:/Users/gruku/Files/Claude/claude-tools add plugins/taskmaster
git -C C:/Users/gruku/Files/Claude/claude-tools commit -m "chore: bump taskmaster submodule to 5.2.0 (derived index, edit hook, backlog_query)"
```
No push. The submodule pointer references a commit that exists only locally until the taskmaster branch is pushed; say so in the final report.

- [ ] Version bump, CHANGELOG, README
- [ ] `uv run pytest -q` full suite green
- [ ] Commit: `chore: release 5.2.0`
- [ ] Submodule bump commit in claude-tools (local only)
