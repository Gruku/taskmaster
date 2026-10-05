<!-- User intent: one place for everything the TUI-mods spec relies on — external docs, verified platform facts, Taskmaster tool shapes, prototype decisions — so a fresh session can implement without re-researching. -->

# Taskmaster TUI mods — References

Companion to [`2026-10-05-tui-mods-design.md`](2026-10-05-tui-mods-design.md). Everything here was read or verified on 2026-10-05 against Claude Code v2.1.289.

## 1. External docs

| What | Where | Why it matters |
|---|---|---|
| Announcement | https://claude.com/blog/claude-code-mods | Scope, early-access caveat, mods ship inside plugins |
| Getting-started guide | https://claude.dev/blog/getting-started-with-claude-code-mods/ | Worked band/pane/command examples (token-weather, blast-radius, replay-theater) |
| Mods overview | https://code.claude.com/docs/en/plugins/mods/overview | Where mods draw per app; built-in mods |
| Create a mod | https://code.claude.com/docs/en/plugins/mods/create | Validator rules (`$` never a value), types for the build, sharing |
| Draw in the interface | https://code.claude.com/docs/en/plugins/mods/interface | Pane vs band, placement widths, focus and hotkeys, `$.state` vs `$.store`, reload after `/clear` |
| React to events | https://code.claude.com/docs/en/plugins/mods/events | `tool.call` observe/answer, matchers, order mods run in, `.catch` |
| Use the mods API | https://code.claude.com/docs/en/plugins/mods/api | Commands, status/toast/log, timers, `$.mcp`, `$.process` |
| Reference | https://code.claude.com/docs/en/plugins/mods/reference | Events, methods, render sites, elements, limits |
| Test a mod | https://code.claude.com/docs/en/plugins/mods/test | Stubs table, `mock.clock`/`mock.store`, `$.ui.mount`, testing after `/clear` |
| Troubleshoot | https://code.claude.com/docs/en/plugins/mods/troubleshoot | Refusal messages, debug log |
| Gallery | https://code.claude.com/docs/en/plugins/mods/gallery | How each element draws in the terminal (focused Button = inverse) |
| Built-in mods source | https://github.com/anthropics/claude-code/tree/main/mods | `diff` (pane, `ui.focus`/`ui.scroll`), `telemetry` (noun contract), `sec-default` |
| Design discussion | https://github.com/anthropics/claude-code/issues/91870 | Rationale; early-access history |
| Build's types (authoritative) | `<mod>/.claude-plugin/types/claude-code/index.d.ts` once loaded; or the `plugin-authoring` skill's `types/claude-code.d.ts` | Every event, method and element prop for the installed build |

Load the `plugin-authoring` skill before writing mod code; it starts the hot-reload watch and names the types file.

## 2. Design sources

- **Reality Reprojection Design System:** https://claude.ai/artifact/TAGXgW2cX1PabtPpN3C9ZG — read `project/README.md`, then `project/tokens.json` (per-token `dark`/`light`/`survivalist` values).
- **Interactive prototype:** https://claude.ai/artifact/Q1vJvojxTFqYNnttQ6WSyx — `Main` (session, band, review queue, handovers), `Gallery` (`/rr-gallery`), `Docked` (fullscreen sidebar).
- **User's style overrides** (beat RR where they conflict): no coloured side rails, no hover motion, no box-shadows.

## 3. Verified platform facts

Probe mods that established these are kept in [`assets/2026-10-05-tui-mods/probe/`](assets/2026-10-05-tui-mods/probe/) (`probe-rr` provides a noun, `probe-tm` consumes it and reports via `/probe`). They're throwaway reference code, not a starting point.

- Hex works for `Text` `color`/`backgroundColor` and `Box` `borderColor`/`backgroundColor`; `Box` background fills the box. Border styles `single`, `round`, `bold` draw.
- Windows Terminal: `COLORTERM` unset, `WT_SESSION` set. `theme` config row reads `"dark"`.
- `$.ui.status` renders as yellow `⚠ <plugin>: <text>` under the prompt, above the user's own statusLine (both visible).
- `$.mcp.call('plugin:taskmaster:tm', tool, args)` works from a separate mod; `plugin_taskmaster_tm` spelling also works. Result `{ content: [{type:'text', text}], isError }`, no `structuredContent`.
- `engine.create` noun: methods only (a plain value unloads the provider: "an interface is an object of methods"); calls are async; returning `h('Box', props, h('Text', …))` trees works and the consumer can place them in its own tree.
- After the provider reloads, the consumer must reload too (its `$` was built before).
- Validator: `$` cannot be passed, enumerated or read as a value; noun methods must be called (`$.rr.x()`), never referenced.
- A pane is a sidebar only in Claude Code's fullscreen layout at ≥110 columns (144 if opened unasked); otherwise a framed region above the prompt. The mod can't choose.
- `session.start` input has no `source`; resets come through `classic.SessionStart` (`source`: `clear` | `resume` | `fork`). Not yet observed live (user kept the session) — test with `$.classic.SessionStart({ source: 'clear' })` in `claude plugin test`.

## 4. Taskmaster MCP tool shapes (from server code, not yet captured live)

Most replies are markdown text; only these are JSON: `backlog_continuity_items`, `backlog_context`, `backlog_handover_list(format: "json")`. Capture real fixtures from both the claude-tools and CodeMaestro backlogs (legacy and native-store routing) before writing parsers.

- **Needs-a-human state:** status `in-review` requires `human_action` (free text). `backlog_complete_task(target_status: "in-review", human_action)` or `backlog_update_task(id, "human_action", text)` set it; reaching `done` clears it. This is the sign-off state the review queue walks.
- **`backlog_continuity_items(action_class, limit)`** → `{"view","total","truncated"?,"items":[{id,type,title,where,next,action_class,timestamp,age_days,task_id,branch}]}`. `review` = `in-review` tasks + P0/P1 open issues; `decide` = open decisions; `resume` = open handovers.
- **`backlog_get_task(id)`** slim → `## \`id\` — title` then `**field:** value` lines (`status`, `lane`, `gate_state`, `blockers`, `human_action`, `depends_on`, `branch`, `open_handovers`, …).
- **`backlog_task_pipeline(id)`** → `## Pipeline \`id\` — lane: **x**`, `gate_state:`, ``- `gate`: pass|done|fail|warn|○ pending|⚠ skipped — reason``, `**Outstanding:** …` or `none — ready for done ✓`. Laneless tasks: one line, "is laneless (pre-protocol) — no pipeline enforced."
- **`backlog_handover_list(format: "json", status)`** → `{"handovers":[{id,date,created,thread,session_kind,status,tldr,next_action,task_ids[],tip_commit,branch,links[{type,target}],superseded_by}],"returned","total","truncated","archived_omitted"}`.
- **`backlog_complete_task`** accepts `in-progress` | `in-review` | `blocked`; refuses open `found_in` bugs and unsatisfied blocking gates (spec-review, plan-review, design-review, review-gate; pass/done/skipped satisfy, warn/fail don't); clears `human_action`, releases the claim, smart-closes handovers; PROGRESS.md entry only when `session_title` or `done` is passed. Does not require a recorded merge.
- **Transitions** (lane'd tasks): `in-review → done | in-progress | blocked | archived`.
- **Decisions:** `backlog_decision(action: "resolve", decision_id, resolved_with, rationale?)`; v1 of the mod doesn't call it (routes to the decision skill via the prompt).
- Code pointers: `taskmaster/backlog_server.py` (handlers), `taskmaster/native/domain.py` (statuses, transitions, gate check), `taskmaster/taskmaster_v3.py` (lanes, continuity items), `taskmaster/native_routing/` (native-store variants).

## 5. Decisions surfaced by the prototype (confirmed and folded into the spec 2026-10-05)

- Decisions count in "waiting on you" and come after tasks in the queue.
- `confirm done?` focuses `n`, so Enter cancels.
- `o` (open) and `r` (resume) fill the prompt and leave the pane open; only "back to agent" closes it.
- `[-]` collapses the band to one summary line (`[+]` restores).
- Priority glyphs: ◆ Critical, ▲ High, ⓘ Medium, `·` Low.
- Button colour (decided 2026-10-05 from the probe's five variants): outline = primary card action, chip 12% = secondary actions, chip 24% = states, keycap = key legends. See spec §5.4. The prototype predates this and still uses bracket buttons.
- How the band gets keyboard focus is engine-defined (Ctrl+X Tab or click); digit hotkeys `1`/`2` work from an empty prompt without focus.

## 6. RR-in-terminal findings (token gaps — decided 2026-10-05: fix upstream in RR before rr-tui)

- `signature-text` in light: 4.0:1 on raised, 3.2:1 on overlay. Dark `#5e79e6` is 3.8:1 on overlay. Prototype uses `foreground-bold` for labels in light and `signature-vivid` (`#8a9eeb`, 5.8:1) in dark.
- `border-subtle` in dark (`#272725`) equals the overlay surface: invisible. Prototype uses `border-default` for rules.
- `surface-recessed` maps to `ground-5` in tokens.json (same as `bg-page`); the README says recessed channels use `ground-0`. Prototype follows the README.
- The `⚠` status-line yellow is the engine's, not an RR token.
