# Playbook Authoring Conventions

Playbooks are assistant-neutral workflow documents. Any coding agent that can
read files and call the `backlog_*` MCP tools must be able to follow one.

## Layout

- `playbooks/<name>/playbook.md` — the workflow. One per skill wrapper.
- `playbooks/<name>/references/`, `templates/` — supporting files, referenced
  from the playbook by paths relative to the playbook's own directory
  (`references/foo.md`, never absolute, never `${CLAUDE_PLUGIN_ROOT}`).

## Neutrality rules

1. **Ask, don't name the tool.** Write "ask the user (use your
   structured-question tool if available)" — never `AskUserQuestion`.
2. **Delegation is optional.** Write "delegate to a sub-agent if your tool
   supports it; otherwise do it inline" — never Agent-tool call syntax,
   `subagent_type`, or model names (opus/sonnet/haiku).
3. **MCP names are the shared vocabulary.** Refer to `backlog_*` tools
   directly; every supported assistant reaches them via MCP.
4. **Cross-playbook references** point at the playbook path first, with the
   native invocation as a hint: "follow the bug playbook
   (`../bug/playbook.md`; on Claude Code/ZCode: `taskmaster:bug`)".
5. **Assistant-specific content** (e.g. Codex-subagent dispatch snippets)
   is allowed only between `<!-- cc-only:start -->` and `<!-- cc-only:end -->`
   markers, with a one-line neutral fallback outside the markers.
6. **No `${CLAUDE_PLUGIN_ROOT}`** anywhere in playbooks. Wrappers (SKILL.md)
   may be CC-flavored; playbooks may not.

## Wrapper contract (skills/<name>/SKILL.md)

- Frontmatter: `name` + `description` **verbatim from before the conversion**
  — the description is the trigger surface and must not change.
- Body: ≤ 12 non-empty lines containing the literal path
  `../../playbooks/<name>/playbook.md` (relative to the skill's base dir).

Enforced by `scripts/check_adapter_coverage.py` (run with `--strict` in CI
rituals; default mode during incremental conversion).
