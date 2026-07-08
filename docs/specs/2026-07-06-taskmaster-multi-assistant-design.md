# Taskmaster Multi-Assistant Generalization + Repo Extraction — Design

**Date:** 2026-07-06
**Status:** Approved (design), pending implementation plan
**Supersedes / reconciles:** the extraction thread in `2026-05-09-agentic-os-v1-implementation.md` §3.7–3.8 and memory `project_taskmaster_repo_split.md`. This spec is now the single extraction plan.

## Goal

Make taskmaster work across many coding assistants — Claude Code, ZCode, OpenAI Codex CLI, Cursor/Zed/opencode — by designing against standards (MCP, AGENTS.md, plain files) rather than per-tool ports. Ship it as its own canonical repo, `gruku/taskmaster`, which The Fold (Agentic OS, on hold) later consumes as a dependency.

## Key decisions

| Decision | Choice |
|---|---|
| Scope | Taskmaster only (first); other plugins follow the proven pattern later |
| Degradation model | Tiered: core everywhere, extras where supported |
| Sequencing | Generalization combined with the repo extraction |
| Name | **Keep `taskmaster`.** No rename. The 27k-star `claude-task-master` collision only affects marketplace discoverability; `gruku/taskmaster` is unambiguous as a repo path. |
| Relation to The Fold | Taskmaster is its own repo; The Fold links it in (git submodule preferred — The Fold needs viewer + playbooks, not just the Python package). Fold OS features (wiki, shell, tabs) stay on hold and are NOT part of this effort. |
| Internal surfaces | `backlog_*` MCP tool names and `.taskmaster/` per-project dir are **unchanged** — zero user-facing breakage at extraction time |

## Architecture: tiered portability

- **Tier 1 — everywhere (any MCP-speaking assistant):** the MCP server (`taskmaster_v3.py` / `backlog_server.py`), the `.taskmaster/` on-disk format, `project.py`, the viewer. Needs only CC-ism cleanup (no `${CLAUDE_PLUGIN_ROOT}` assumptions, no CC-specific paths).
- **Tier 2 — workflow knowledge (CC + ZCode natively; Codex/Cursor/Zed/opencode via AGENTS.md pointers):** the discipline currently embedded in skills (start-session, end-session, pick-task, review-gate, spec-review, plan-review, handover, bug/issue/lesson/decision/idea flows, init, migrate, check-todos, linear). Moves once into assistant-neutral `playbooks/`.
- **Tier 3 — CC-class only (Claude Code + ZCode):** hooks (gate enforcement, version-bump guard), statusline integration, subagent dispatch conventions. Clearly marked; hook-less assistants follow the same discipline advisorily because the playbooks describe it.

A `docs/capability-matrix.md` documents what each assistant gets per tier.

## Approach: neutral playbooks + thin native wrappers (no codegen)

Workflow knowledge exists exactly once, in `playbooks/<name>.md`, written assistant-neutrally:

- "Ask the user" — never `AskUserQuestion`
- "Delegate to a sub-agent if your tool supports it; otherwise do it inline" — never Agent-tool specifics
- No model-name references (`opus`/`sonnet`)
- No `${CLAUDE_PLUGIN_ROOT}`; paths resolved relative to the plugin/repo root by the wrapper
- MCP tools referenced by their `backlog_*` names (the one shared vocabulary across all assistants)

Each assistant gets trivial native entry points:

- **Claude Code / ZCode:** `skills/<name>/SKILL.md` keeps its **full trigger `description` frontmatter** (the trigger surface loses nothing) but the body becomes ~5 lines: "Read `<root>/playbooks/<name>.md` and follow it." ZCode consumes the same plugin format directly — one adapter serves both.
- **Codex CLI:** `adapters/codex/` — AGENTS.md fragment + `prompts/` entries pointing at the same playbooks + a `config.toml` snippet registering the MCP server.
- **Cursor / Zed / opencode:** `adapters/agents-md/` — a generic AGENTS.md/rules file listing the playbooks and when to read them (all three read AGENTS.md-style rules).

**Drift prevention:** `scripts/check_adapter_coverage.py` (sibling to the existing version-bump checker) verifies every playbook has a wrapper in every adapter; wired into the same pre-PR ritual.

**Rejected alternatives:** (A) generated adapters via codegen — zero drift by construction but adds a generator to maintain and makes skills painful to iterate; (B) CC plugin canonical + hand-written AGENTS.md shims — cheapest but duplicates workflow content, which drifts immediately.

## New repo layout

```
gruku/taskmaster/
├── .claude-plugin/plugin.json      ← manifest at repo root → directly installable
│                                      by Claude Code AND ZCode
├── taskmaster/                     ← Python package: MCP server, project.py, backlog core
├── viewer/                         ← assistant-agnostic web UI (unchanged)
├── playbooks/                      ← tier-2 core: assistant-neutral workflow markdown
├── skills/                         ← thin CC/ZCode wrappers (frontmatter + pointer body)
├── commands/                       ← CC slash-command surface (thin, where still needed)
├── hooks/                          ← tier 3, CC-class only, clearly marked
├── agents/                         ← CC subagents (tier 3), if/when they ship
├── adapters/
│   ├── codex/                      ← AGENTS.md fragment + prompts/ + config.toml snippet
│   └── agents-md/                  ← generic rules for Cursor / Zed / opencode
├── scripts/check_adapter_coverage.py
├── pyproject.toml                  ← pip-installable for Hermes/Codex/The Fold
└── docs/capability-matrix.md
```

## Extraction mechanics

1. `git filter-repo --subdirectory-filter plugins/taskmaster` from `claude-tools` → full history preserved.
2. Restructure to the layout above; add `pyproject.toml`; move plugin manifest to repo root.
3. `claude-tools/plugins/taskmaster/` shrinks to a **thin wrapper** pinning the external repo via marketplace ref — existing claude-tools users see no disruption. The claude-tools marketplace.json entry points at the new source.
4. The Fold linkage (when it resumes): git submodule of `gruku/taskmaster` (preferred — it needs viewer + playbooks), with `pip install git+…@<ref>` as the Python-only alternative.

## Sequencing (each phase independently mergeable)

| # | Phase | Output | Verification |
|---|---|---|---|
| 1 | Playbook extraction + thin-wrapper skills, **in-place in claude-tools** | `playbooks/` exists; skills are pointers; behavior unchanged | Daily-driver use in Claude Code; existing test suite green |
| 2 | Repo extraction with new layout; wrapper plugin in claude-tools | `gruku/taskmaster` local repo; claude-tools installs from it | Fresh plugin install works in Claude Code |
| 3 | Codex + agents-md adapters | AGENTS.md fragments, prompts, MCP config snippets | Verified live in ZCode and Codex CLI |
| 4 | Cursor / Zed / opencode verification | Capability matrix filled in | As tools are available |
| 5 | Push `gruku/taskmaster` to GitHub | Public canonical repo | **Gated on explicit user approval** |

## Testing

- Existing viewer unit tests + route-mocked specs remain the regression gate (e2e suite rot per ISS-025 noted; not in scope here).
- `check_adapter_coverage.py` gets its own unit tests.
- Phase-1 acceptance: every taskmaster skill still triggers and behaves identically in Claude Code (the wrapper conversion must be behavior-neutral).
- Phase-3 acceptance: in Codex CLI, the MCP server registers and a full session loop (start-session → pick-task → end-session discipline followed advisorily) works against a test project.

## Error handling / edge cases

- **Playbook path resolution:** wrappers reference playbooks relative to the plugin root; the CC wrapper uses `${CLAUDE_PLUGIN_ROOT}` *inside the SKILL.md wrapper only* (allowed — it's a CC-native file), never inside playbooks.
- **Assistant without MCP:** out of scope; taskmaster requires MCP as its floor.
- **Version skew** between claude-tools wrapper and canonical repo: wrapper pins an explicit ref; the existing three-part version protocol continues, with the canonical repo's CHANGELOG as source of truth.
- **Hooks on non-CC tools:** absent, by design (tier 3). Playbooks state the gate discipline so agents follow it without enforcement.

## Out of scope

- The Fold OS features (wiki, shell, tabs, registry) — parked, per its on-hold status
- Renaming `taskmaster`, `backlog_*` tools, or the `.taskmaster/` directory
- Generalizing other plugins (guard-hooks, reflect, statusline, …) — they follow the proven pattern later
- Rebuilding hook-grade enforcement on non-CC assistants (git-hooks fallback is a future idea)
- e2e suite repair (ISS-025)
