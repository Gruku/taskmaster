# Taskmaster Multi-Assistant Adapters (Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `adapters/codex/` and `adapters/agents-md/` in the standalone taskmaster repo so Codex CLI and AGENTS.md-reading editors (Cursor/Zed/opencode) get the same workflow playbooks + MCP server, with `check_adapter_coverage.py` guarding 1:1 coverage.

**Architecture:** Adapters are thin pointers, exactly like SKILL.md wrappers: the single source of workflow truth stays `playbooks/<name>/playbook.md`. Codex gets per-playbook custom prompts (`~/.codex/prompts/<name>.md` → `/<name>`), an AGENTS.md fragment with the routing table, and a `config.toml` MCP snippet. Editors get one generic AGENTS.md rules file. Install-location independence via a literal `{{TASKMASTER_HOME}}` placeholder that the README tells the user to replace.

**Backlog:** `repo-split-003`. Work in `C:/Users/gruku/Files/Claude/taskmaster/.worktrees/repo-split-003` (standalone repo, branch `feature/repo-split-003`).

## Global Constraints

- Playbook content is NEVER duplicated into adapters — pointers + routing only.
- Adapters may name their own assistant (Codex etc.) but CC-isms stay banned (`AskUserQuestion`, `CLAUDE_PLUGIN_ROOT`, `subagent_type`, model names) — same list as the checker.
- `backlog_*` MCP tool names unchanged; `.taskmaster/` format unchanged.
- No push to any remote (Phase 5, gated).
- Version bump: tm **3.24.0** (additive surface → minor) in plugin.json + CHANGELOG; claude-tools marketplace sync happens when the submodule pin advances (separate follow-up commit in claude-tools).

## Design decisions locked here

1. **Codex prompts are per-playbook, 1:1, all 17** — uniform "read the playbook and execute" body with `$ARGUMENTS` passthrough. Uniformity beats curation: the router playbook (`/taskmaster`) is included, so users who learn one entry point get the same routing as Claude Code.
2. **`{{TASKMASTER_HOME}}` placeholder** (not env var, not relative paths) — prompts/config get copied out of the repo into `~/.codex/`, so relative paths can't work; README gives the one-liner replace. An install script is deliberately out of scope (YAGNI until a second consumer proves the shape).
3. **Coverage rule in the checker:** every `playbooks/<name>/` must have `adapters/codex/prompts/<name>.md` AND be referenced (`playbooks/<name>/playbook.md`) in `adapters/agents-md/AGENTS.md`. Adapters are also scanned for banned tokens (outside `<!-- cc-only -->`, which shouldn't appear in adapters at all).
4. **Live Codex verification uses the real `~/.codex/config.toml`** — additive `[mcp_servers.taskmaster]` entry pointing at the canonical repo (not the worktree), reversible, reported to the user.

---

### Task 1: Author the adapters

**Files (all in the worktree):**
- Create: `adapters/codex/README.md`, `adapters/codex/AGENTS.md`, `adapters/codex/config.toml`, `adapters/codex/prompts/<name>.md` × 17
- Create: `adapters/agents-md/README.md`, `adapters/agents-md/AGENTS.md`

**Steps:**
- [ ] Codex prompt template (one file per playbook name, `<name>` substituted):

```markdown
Read `{{TASKMASTER_HOME}}/playbooks/<name>/playbook.md` in full and execute it
exactly. `references/` and `templates/` paths inside the playbook resolve
relative to the playbook's own directory. Taskmaster's `backlog_*` MCP tools
must be available (see adapters/codex/README.md if they are not).

ARGUMENTS: $ARGUMENTS
```

- [ ] `adapters/codex/config.toml`:

```toml
# Merge into ~/.codex/config.toml, replacing {{TASKMASTER_HOME}} with the
# absolute path of your taskmaster checkout.
[mcp_servers.taskmaster]
command = "uv"
args = ["run", "{{TASKMASTER_HOME}}/backlog_server.py"]
startup_timeout_sec = 60
```

- [ ] `adapters/codex/AGENTS.md`: fragment for project/global AGENTS.md — what taskmaster is (backlog in `.taskmaster/`), the intent→playbook routing table (mirroring `playbooks/taskmaster/playbook.md`'s table but pointing at `{{TASKMASTER_HOME}}/playbooks/...`), gate discipline advisory note (tier 3 hooks absent — follow review-gate/merge discipline because the playbooks say so), `/`-prompt list.
- [ ] `adapters/agents-md/AGENTS.md`: same content shape, tool-neutral wording (no Codex references), MCP registration pointer ("register the server in your tool's MCP config: `uv run {{TASKMASTER_HOME}}/backlog_server.py`").
- [ ] READMEs: 3-step install each (replace placeholder → copy/merge → verify with `backlog_status`).
- [ ] Commit: `feat(adapters): codex + agents-md adapters pointing at playbooks (repo-split-003)`

### Task 2: Extend check_adapter_coverage.py + tests

- [ ] Add to `check()`: for each converted playbook, require `adapters/codex/prompts/<name>.md` containing `playbooks/<name>/playbook.md`; require `adapters/agents-md/AGENTS.md` to reference `playbooks/<name>/playbook.md`. Scan all `adapters/**/*.md` + `config.toml` for BANNED tokens. Keep exit/reporting semantics.
- [ ] Extend `tests/test_adapter_coverage.py` with cases: missing prompt file, prompt missing pointer, agents-md missing reference, banned token in adapter. Run the file; expect green.
- [ ] Run `python scripts/check_adapter_coverage.py --strict` in the worktree → OK.
- [ ] Commit: `feat(adapters): coverage checker enforces codex + agents-md 1:1 (repo-split-003)`

### Task 3: Version bump + CHANGELOG

- [ ] plugin.json + pyproject.toml → 3.24.0; CHANGELOG `## 3.24.0` (additive: adapters).
- [ ] Full pytest run in worktree (same invocation as Phase 2) → parity (B-072's 2 pre-existing fails only).
- [ ] Commit.

### Task 4: Live Codex CLI verification (spec Phase-3 acceptance)

- [ ] Locate codex binary (PowerShell: `Get-Command codex`); add `[mcp_servers.taskmaster]` to `~/.codex/config.toml` pointing at the CANONICAL repo (`C:/Users/gruku/Files/Claude/taskmaster/backlog_server.py`) — report the edit to the user.
- [ ] Test project: temp dir with `backlog_init`-seeded `.taskmaster/` (create via MCP or file fixtures).
- [ ] `codex exec` (non-interactive) against the test project: prompt it to run the start-session playbook → confirm it calls `backlog_*` tools (status/last_session) and produces a dashboard; then pick-task → end-session advisorily. Evidence = transcript showing MCP tool calls.
- [ ] If codex exec can't run non-interactively with MCP, fall back to: verify MCP server registers (`codex mcp list` or config parse + manual session note) and hand the interactive loop to the user as the in-review test.

### Task 5: Gates + records

- [ ] Review-gate (fresh-context reviewer on the adapter content — checks pointer correctness, no duplicated playbook content, no CC-isms) → record gates → merge `feature/repo-split-003` into taskmaster repo master (local `--no-ff`) → advance claude-tools submodule pin + marketplace 3.24.0 in a small claude-tools commit → task in-review.

## Risks

- Codex prompt argument syntax (`$ARGUMENTS`) — verify against local Codex version's docs; degrade to "arguments follow" prose if unsupported.
- `uv run` from `~/.codex` context: config args use absolute paths, cwd-independent — the PEP 723 shim makes this safe.
- Windows path separators in TOML: use forward slashes.
