# Taskmaster Repo Extraction (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract `plugins/taskmaster` into a standalone repo at `C:/Users/gruku/Files/Claude/taskmaster` (full history preserved), restructure it to the approved multi-assistant layout, and make claude-tools consume it as a thin wrapper — with zero user-facing breakage.

**Architecture:** `git filter-repo --subdirectory-filter` from a fresh local clone gives the new repo with 972 commits of history. Python core modules move into a `taskmaster/` package (absolute imports); a root-level `backlog_server.py` shim keeps the MCP registration mechanics (`uv run` + PEP 723) identical to today. claude-tools then replaces `plugins/taskmaster/` with a **git submodule** pointing at the new repo — the marketplace `source: "./plugins/taskmaster"` keeps working unchanged, the submodule SHA is the "pinned ref", and the submodule URL flips from local path to GitHub at Phase 5.

**Tech Stack:** git filter-repo (installed, version a40bce548d2c), uv, pytest, setuptools/pyproject.

**Backlog:** Tasks `repo-split-001` (Tasks 1–5 below) and `repo-split-002` (Task 6). Spec: `docs/superpowers/specs/2026-07-06-taskmaster-multi-assistant-design.md`.

## Global Constraints

- `backlog_*` MCP tool names and `.taskmaster/` per-project dir are **unchanged** (spec: zero user-facing breakage).
- No rename: repo is `taskmaster`, plugin name stays `taskmaster`.
- New repo default branch: `master` (claude-tools convention).
- Version protocol: new repo bumps to **3.23.0** (structural minor); claude-tools `marketplace.json` entry must equal the submodule's `plugin.json` version.
- **NEVER push anything to any remote** — Phase 5 is gated on explicit user approval.
- claude-tools work happens on branch `feature/repo-split-002`; merge to local master only after review-gate, no push.
- Windows: if any git op fails with "Filename too long", run `git config --global core.longpaths true` — never work around with deletions.
- Never `rm -rf` a worktree or use `git worktree remove --force` (guard-hooks enforced).

## Design decisions locked here (rationale)

1. **Wrapper mechanism = git submodule**, not a marketplace-format experiment. Rationale: marketplace `source` stays a relative path (known-good format); works *before* the GitHub push exists; pinned-ref semantics come free (submodule SHA); claude-tools already ships a `worktree_submodule_init` hook so its worktree flows tolerate submodules. Alternative (marketplace github ref) becomes available at Phase 5 and can replace the submodule then if preferred.
2. **Root `backlog_server.py` entry shim** (PEP 723 header + import from package) instead of `uv run --project`-style module execution. Rationale: MCP registration (`.mcp.json`) stays byte-identical to today's mechanics — no new env-resolution behavior at session start; the package remains cleanly pip-importable for Hermes/The Fold. Cost: deps listed twice (shim header + pyproject) — acceptable, documented in both files.
3. **`merge_gate_decide.py` / `merge_recorder_stamp.py` move to `hooks/`** — they are standalone subprocess scripts called only by hooks (no taskmaster imports), i.e. tier-3 material, not package core.
4. **`taskmaster-workspace/` is dropped** (single stray `optimization.log` scratch). `evals/` and `references/` move over as-is.
5. **Specs travel with the repo**: `docs/superpowers/specs` is gitignored in claude-tools, so filter-repo won't carry the design spec — copy it (and this plan) into the new repo's `docs/` explicitly.

---

### Task 1: Extract history into the new repo

**Files:**
- Create: `C:/Users/gruku/Files/Claude/taskmaster/` (entire repo via clone+filter)

**Interfaces:**
- Produces: a git repo at `C:/Users/gruku/Files/Claude/taskmaster` whose root contains today's `plugins/taskmaster` content, history preserved, no remotes.

- [ ] **Step 1: Fresh clone (filter-repo refuses non-fresh clones)**

```bash
git clone --no-local "C:/Users/gruku/Files/Claude/claude-tools" "C:/Users/gruku/Files/Claude/taskmaster"
```

`--no-local` forces a clean object copy. This clones **local master** (d31c549 + unpushed playbook commits included).

- [ ] **Step 2: Run filter-repo**

```bash
git -C "C:/Users/gruku/Files/Claude/taskmaster" filter-repo --subdirectory-filter plugins/taskmaster
```

Expected: completes without error; filter-repo removes the `origin` remote automatically.

- [ ] **Step 3: Verify extraction**

```bash
ls "C:/Users/gruku/Files/Claude/taskmaster"
git -C "C:/Users/gruku/Files/Claude/taskmaster" rev-list --count master
git -C "C:/Users/gruku/Files/Claude/taskmaster" remote -v
git -C "C:/Users/gruku/Files/Claude/taskmaster" log --oneline -5
```

Expected: `taskmaster_v3.py`, `backlog_server.py`, `playbooks/`, `skills/`, `viewer/`, `hooks/`, `.claude-plugin/` at root; commit count ≈ 972 (the count of claude-tools commits touching `plugins/taskmaster`); **no remotes**; recent log shows the playbook-phase-1 commits.

- [ ] **Step 4: Baseline test run in the new repo (before any restructure)**

```bash
uv run --with pytest --with pyyaml --with fastmcp pytest "C:/Users/gruku/Files/Claude/taskmaster/tests" -q -m "not slow"
```

Expected: green (same pass count as the suite in claude-tools; ~1500 passed). If invocation differs from how tests run in claude-tools, replicate the claude-tools invocation exactly — the point is a green baseline before touching anything.

No commit (nothing changed yet).

---

### Task 2: Restructure Python core into a `taskmaster/` package

**Files (all inside `C:/Users/gruku/Files/Claude/taskmaster`):**
- Create: `taskmaster/__init__.py`
- Move: `taskmaster_v3.py`, `backlog_server.py`, `project.py`, `blast_radius.py`, `integrations/` → `taskmaster/`
- Move: `merge_gate_decide.py`, `merge_recorder_stamp.py` → `hooks/`
- Create: root `backlog_server.py` (entry shim)
- Modify: `hooks/merge_gate.py:156`, `hooks/merge_recorder.py:104`, `hooks/snapshot.py:20-25`, `tests/conftest.py`, all 86 test files with core imports, `scripts/*.py`
- Delete: `taskmaster-workspace/`

**Interfaces:**
- Produces: package `taskmaster` with absolute-import modules (`from taskmaster.taskmaster_v3 import …`, `from taskmaster.integrations.linear.client import …`); root script `backlog_server.py` that starts the MCP server exactly like today.

- [ ] **Step 1: Move files with git mv**

```bash
git -C "C:/Users/gruku/Files/Claude/taskmaster" mv taskmaster_v3.py backlog_server.py project.py blast_radius.py integrations taskmaster/  # after: mkdir taskmaster
git -C "C:/Users/gruku/Files/Claude/taskmaster" mv merge_gate_decide.py merge_recorder_stamp.py hooks/
git -C "C:/Users/gruku/Files/Claude/taskmaster" rm -r taskmaster-workspace
```

(Create `taskmaster/` dir first; `git mv` needs it to exist. `__init__.py` content: `"""Taskmaster core package."""` — nothing else; do NOT re-export modules from it, imports stay explicit.)

- [ ] **Step 2: Rewrite imports mechanically (sed across the repo)**

Patterns to rewrite in `taskmaster/*.py`, `taskmaster/integrations/**/*.py`, `tests/**/*.py`, `scripts/*.py`, `hooks/*.py`:

```
from taskmaster_v3 import   → from taskmaster.taskmaster_v3 import
import taskmaster_v3        → from taskmaster import taskmaster_v3
from backlog_server import  → from taskmaster.backlog_server import
import backlog_server       → from taskmaster import backlog_server
from project import         → from taskmaster.project import
import project              → from taskmaster import project
from blast_radius import    → from taskmaster.blast_radius import
from integrations.          → from taskmaster.integrations.
import integrations.        → import taskmaster.integrations.
```

Use word-boundary-safe sed (e.g. `sed -i -E 's/^from taskmaster_v3 import/from taskmaster.taskmaster_v3 import/'`) — anchor to line start to avoid mangling strings/comments mid-line; then grep for remaining mid-line occurrences (e.g. `importlib`, test monkeypatching like `monkeypatch.setattr("backlog_server.X")` / `"taskmaster_v3.X"` string targets) and fix those **by hand** — string-form module paths in `patch()`/`setattr()` calls MUST also become `taskmaster.backlog_server…` / `taskmaster.taskmaster_v3…`.

Aliased forms (`import taskmaster_v3 as v3`) become `from taskmaster import taskmaster_v3 as v3`.

- [ ] **Step 3: Root entry shim `backlog_server.py`**

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["fastmcp", "pyyaml"]
# ///
"""Entry shim: keeps `.mcp.json` (`uv run ${CLAUDE_PLUGIN_ROOT}/backlog_server.py`)
working unchanged after the core moved into the taskmaster/ package.
Dependencies are declared twice by design: here (uv script mode) and in
pyproject.toml (pip consumers)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from taskmaster.backlog_server import mcp  # noqa: E402

if __name__ == "__main__":
    mcp.run()
```

`.mcp.json` stays byte-identical (`uv run ${CLAUDE_PLUGIN_ROOT}/backlog_server.py`).

- [ ] **Step 4: Fix hook path/import references**

- `hooks/merge_gate.py:156`: `Path(__file__).parent.parent / "merge_gate_decide.py"` → `Path(__file__).parent / "merge_gate_decide.py"`
- `hooks/merge_recorder.py:104`: same change for `merge_recorder_stamp.py`
- `hooks/snapshot.py`: keep `sys.path.insert(0, str(_PLUGIN_DIR))` (`_PLUGIN_DIR` still resolves to repo root), change `import taskmaster_v3 as v3` → `from taskmaster import taskmaster_v3 as v3` (update the except-message text too)
- Grep `hooks/`, `tests/`, `scripts/` for the literal strings `merge_gate_decide` and `merge_recorder_stamp` and update any remaining **path** references (tests build paths to these scripts — they now live under `hooks/`).

- [ ] **Step 5: conftest + scripts sanity**

`tests/conftest.py`: `PLUGIN_ROOT = Path(__file__).resolve().parents[1]` still equals repo root — keep the sys.path insert (it's what makes `import taskmaster` resolve without installation). Update the stale comment. Check `scripts/migrate_handover_statuses.py`, `scripts/migrate_links.py`, `scripts/backfill_tldr.py` sys.path shims the same way.

- [ ] **Step 6: Run the full suite**

```bash
uv run --with pytest --with pyyaml --with fastmcp pytest "C:/Users/gruku/Files/Claude/taskmaster/tests" -q -m "not slow"
```

Expected: same green count as the Task 1 baseline. Grep for stragglers before declaring done:

```bash
grep -rnE "^(from|import) (taskmaster_v3|backlog_server|project|blast_radius|integrations)\b" "C:/Users/gruku/Files/Claude/taskmaster" --include="*.py" | grep -v __pycache__
```

Expected: no matches outside `taskmaster/` package internals (which should also be converted — ideally zero matches anywhere).

- [ ] **Step 7: Commit**

```bash
git -C "C:/Users/gruku/Files/Claude/taskmaster" add -A
git -C "C:/Users/gruku/Files/Claude/taskmaster" commit -m "refactor: move Python core into taskmaster/ package, merge helpers into hooks/ (repo-split-001)"
```

---

### Task 3: pyproject.toml, docs, version bump

**Files (new repo):**
- Create: `pyproject.toml`, `docs/capability-matrix.md`, `docs/specs/2026-07-06-taskmaster-multi-assistant-design.md` (copied), `docs/plans/2026-07-08-taskmaster-repo-extraction-phase2.md` (copied)
- Modify: `.claude-plugin/plugin.json` (3.23.0), `CHANGELOG.md`

**Interfaces:**
- Produces: `pip install <repo>` / `uv run --with-editable` importable `taskmaster` package.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "taskmaster"
version = "3.23.0"
description = "Task/backlog management MCP server with narrative continuity (handovers, lessons, issues)"
requires-python = ">=3.11"
dependencies = ["fastmcp", "pyyaml"]

[tool.setuptools.packages.find]
include = ["taskmaster*"]
```

- [ ] **Step 2: Verify pip-installability**

```bash
uv run --with-editable "C:/Users/gruku/Files/Claude/taskmaster" python -c "from taskmaster import taskmaster_v3, project, blast_radius; from taskmaster.integrations.linear import client; print('import ok')"
```

Expected: `import ok`.

- [ ] **Step 3: Copy specs/docs into the repo**

Copy from claude-tools (gitignored there, so filter-repo didn't carry them):
- `docs/superpowers/specs/2026-07-06-taskmaster-multi-assistant-design.md` → `docs/specs/`
- `docs/superpowers/plans/2026-07-06-taskmaster-playbooks-phase1.md` → `docs/plans/`
- this plan → `docs/plans/`

Create `docs/capability-matrix.md` stub with the tier table from the spec (Tier 1/2/3 rows, one column per assistant: Claude Code, ZCode, Codex CLI, Cursor, Zed, opencode; CC column filled, rest marked "pending Phase 3/4").

- [ ] **Step 4: Version bump 3.23.0**

- `.claude-plugin/plugin.json`: `"version": "3.23.0"`
- `CHANGELOG.md`: new `## 3.23.0` section — standalone repo extraction, package restructure, pyproject, entry shim; note "no MCP surface changes".

- [ ] **Step 5: Commit**

```bash
git -C "C:/Users/gruku/Files/Claude/taskmaster" add -A
git -C "C:/Users/gruku/Files/Claude/taskmaster" commit -m "feat: pyproject packaging, docs import, capability-matrix stub (tm 3.23.0)"
```

---

### Task 4: Functional smoke of the standalone repo

**Interfaces:**
- Consumes: Tasks 2–3 output.
- Produces: evidence the repo works as a plugin source.

- [ ] **Step 1: MCP server tool-surface smoke**

```bash
uv run "C:/Users/gruku/Files/Claude/taskmaster/backlog_server.py" --help 2>&1 | head -3 || true
uv run --with fastmcp --with pyyaml python -c "import sys; sys.path.insert(0, r'C:/Users/gruku/Files/Claude/taskmaster'); import asyncio; from taskmaster.backlog_server import mcp; print(len(asyncio.run(mcp.get_tools())), 'tools')"
```

Expected: second command prints ~125 tools (the audited tool count). If `mcp.get_tools()` isn't the right FastMCP API in the installed version, use `mcp._tool_manager` equivalents — the acceptance is "the server module imports and exposes the full backlog_* surface without error".

- [ ] **Step 2: Adapter coverage + skill wrappers**

```bash
python "C:/Users/gruku/Files/Claude/taskmaster/scripts/check_adapter_coverage.py" --strict
```

Expected: exit 0 (17 wrappers ↔ 17 playbooks, no banned tokens, no stale old-layout paths).

- [ ] **Step 3: Viewer unit tests**

```bash
npm --prefix "C:/Users/gruku/Files/Claude/taskmaster/viewer" run test:unit
```

Expected: pass (e2e suite is known-rotten per ISS-025 — do NOT run `test:e2e`, it is not a gate).

- [ ] **Step 4: Commit any fixes; run review-gate for repo-split-001**

If steps 1–3 required fixes, commit them. Then run the taskmaster review-gate on repo-split-001 (implementation lives in the new repo; cite test evidence from Tasks 1–4).

---

### Task 5: Record repo-split-001 completion

- [ ] Record gates / transition repo-split-001 per review-gate outcome (`backlog_record_gate`, status → in-review).
- [ ] Update memory `project_taskmaster_multi_assistant.md` (Phase 2a done, repo path, 3.23.0).

---

### Task 6 (repo-split-002): claude-tools thin wrapper via submodule

**Files (claude-tools):**
- Delete (tracked): `plugins/taskmaster/**` (replaced by submodule)
- Create: `.gitmodules` entry, submodule at `plugins/taskmaster`
- Modify: `.claude-plugin/marketplace.json` (taskmaster version → 3.23.0)

**Interfaces:**
- Consumes: the standalone repo at `C:/Users/gruku/Files/Claude/taskmaster` (Task 1–5).
- Produces: claude-tools whose `plugins/taskmaster` is a submodule pin; marketplace source string unchanged.

- [ ] **Step 1: Branch**

```bash
git -C "C:/Users/gruku/Files/Claude/claude-tools" checkout -b feature/repo-split-002
```

(Working tree has untracked cruft — png/html/test-results at root; leave untouched, use explicit pathspecs for all commits per subagent-commit-hygiene rule.)

- [ ] **Step 2: Replace directory with submodule**

```bash
git -C "C:/Users/gruku/Files/Claude/claude-tools" rm -r plugins/taskmaster
git -C "C:/Users/gruku/Files/Claude/claude-tools" submodule add "C:/Users/gruku/Files/Claude/taskmaster" plugins/taskmaster
```

If `git rm -r` trips a guard hook, stop and use the Approve/Deny ritual — do not work around it. NOTE: `git rm -r` leaves nothing untracked behind only if the dir had no ignored files — check `git -C … status plugins/taskmaster` and clear leftovers (e.g. `__pycache__`) manually **by reviewing each** before `submodule add` (it requires an empty path).

- [ ] **Step 3: Marketplace version sync**

`.claude-plugin/marketplace.json`: taskmaster entry `"version": "3.23.0"` (source stays `"./plugins/taskmaster"`).

- [ ] **Step 4: Version-bump check**

```bash
python "C:/Users/gruku/Files/Claude/claude-tools/scripts/check_plugin_version_bump.py" --base master
```

Expected: exit 0 (plugin.json inside submodule = 3.23.0 = marketplace). If the script can't read through the submodule boundary, fix the script in this branch (it reads files on disk, so it should work; the failure mode would be its git-diff-based change detection — adapt it to treat a changed submodule pointer as "plugin changed").

- [ ] **Step 5: Commit**

```bash
git -C "C:/Users/gruku/Files/Claude/claude-tools" add .gitmodules plugins/taskmaster .claude-plugin/marketplace.json
git -C "C:/Users/gruku/Files/Claude/claude-tools" commit -m "refactor: plugins/taskmaster -> submodule of standalone taskmaster repo (repo-split-002)"
```

- [ ] **Step 6: Live verification (needs user/new session)**

Fresh Claude Code session in a taskmaster project: MCP server registers, `backlog_status` works, a skill (e.g. `taskmaster:start-session`) triggers and resolves its playbook. **This step requires the user to restart / reinstall the plugin — pause and hand over here.** Only after this passes: review-gate repo-split-002, then merge `feature/repo-split-002` → local master (`--no-ff`, autonomous, NO push).

---

## Risks

- **Submodules + worktrees on Windows** — claude-tools task worktrees will now contain a submodule; `worktree_submodule_init` hook covers init, and guard-hooks + the safe-worktree-removal skill cover removal. Never `--force`-remove.
- **Plugin cache staleness** — after the marketplace flip, the installed plugin may serve the old cached copy until reinstalled; MCP-server restart required (known 3.22.0 gotcha).
- **String-form module references** in tests (`monkeypatch.setattr("backlog_server.X", …)`) are the most likely sed-miss — Task 2 Step 2 calls them out explicitly; the green suite is the net.
- **Rollback:** until Task 6 merges, claude-tools is untouched; rollback = delete the new repo dir and the branch.
