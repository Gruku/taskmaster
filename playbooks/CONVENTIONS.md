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
   In routing tables, a single preamble line may state the
   native-invocation mapping once instead of repeating it per row.
5. **Assistant-specific content** (e.g. Codex-subagent dispatch snippets)
   is allowed only between `<!-- cc-only:start -->` and `<!-- cc-only:end -->`
   markers, with a one-line neutral fallback outside the markers.
6. **No `${CLAUDE_PLUGIN_ROOT}`** anywhere in playbooks. Wrappers (SKILL.md)
   may be CC-flavored; playbooks may not.

## Wrapper contract (skills/<name>/SKILL.md)

- Frontmatter: `name` + `description`. The description is the trigger surface:
  it says what the skill does, when (user phrasings), and its boundary with
  the neighbouring skill. Descriptions change together with their
  trigger-phrase tests.
- Body: at most 12 non-empty lines, and it contains the literal path
  `../../playbooks/<name>/playbook.md`. Use this text:

  ```
  Follow the playbook at `../../playbooks/<name>/playbook.md` (relative to
  this skill's base directory); it holds the tool calls, store rules and
  user checkpoints for this workflow. `references/` and `templates/` paths
  inside it resolve relative to the playbook's own directory.
  ```

- Codex prompt (`adapters/codex/prompts/<name>.md`):

  ```
  Follow `{{TASKMASTER_HOME}}/playbooks/<name>/playbook.md`; read its
  `references/` and `templates/` files (relative to the playbook's own
  directory) when a step points to one. Taskmaster's `backlog_*` MCP tools
  must be available (see the taskmaster codex adapter README if they are not).

  ARGUMENTS: $ARGUMENTS
  ```

Enforced by `scripts/check_adapter_coverage.py` (run with `--strict` in CI
rituals; default mode during incremental conversion).

## Prompt style

Enforced in part by `tests/test_prompt_style_lint.py`. Taskmaster should not
get in the way of the model's work while it keeps the backlog, gates and
handovers current.

1. Open with what the workflow is for and why, in one or two sentences. State
   outcomes and constraints, each with its reason, rather than a script.
2. Write at normal volume: no all-caps emphasis words, alarm emoji, or bold
   used for alarm. The lint holds the word list.
3. Number steps only where exactly one order is safe, and say why that order.
4. Ask the user only when the work can't continue without them, or before a
   destructive, hard-to-reverse or outward-facing step. Otherwise act and
   announce in one line. Confirmations that guard state the user owns stay:
   an AI-noticed bug, init seed mode, migrate adopt, check-todos task
   creation, linear mapping apply, bundle descope, merge-gate approval,
   worktree removal, related-issue close.
5. Phrase optional reads as conditions ("when the table doesn't settle it,
   read ..."), never as standing reads.
6. Name the exact tool and arguments, verified against the server. Don't
   discourage tool use or impose "minimise calls" limits.
7. Leave out thresholds the model can't observe (token or turn counts),
   change history ("since 6.0", issue ids as rationale), and internal Python
   names.
8. State each rule once, in its home, and link to it elsewhere (lane rule:
   SessionStart hook plus one router paragraph; Bug/Issue bar:
   `issue/references/issue-bar.md`; lifecycle: `references/task-lifecycle.md`;
   companion-field-first: tool docstrings plus one line per playbook).
9. Don't request reasoning in the output or add "think carefully" lines.
10. Address the reader as "you". Don't say "Claude" in playbooks, except
    inside cc-only blocks or as a stored literal value.
11. Commits and pushes are the user's call. Only end-session commits, and only
    its own tracking files.
12. Use XML tags only where a prompt structures input or output (sub-agent
    briefs). Mark untrusted pasted or transcript text and say how to treat it.
13. A sub-agent brief gives the goal, the reason, the inputs (tagged), the
    constraints, the output contract, and the completion and scope line.
    Model tiers and `subagent_type` go inside cc-only blocks.

Don't document features that don't exist (commit-message auto-resolution of
decisions, nonexistent slash commands, session-bundle helper functions); the
lint holds the list of dead names.
