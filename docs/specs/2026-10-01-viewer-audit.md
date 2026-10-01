<!-- User intent: the evidence base for the viewer re-skin — every visual, UX and accessibility defect found in the viewer before the Reality Reprojection work, so the spec can cite findings by ID. -->

# Taskmaster Viewer — UI/UX Audit (pre-RR re-skin) — part 1/4
Date 2026-10-01. Viewer v7.0.0, feat/database-native-foundation @ 75989f3.
Server: TASKMASTER_ROOT=C:/Users/gruku/Files/Claude/claude-tools (taskmaster repo root refuses: "plugin directory as a project root"). 230 tasks/27 epics/23 bugs/24 issues/31 ideas/30 sessions/0 threads.
Playwright 1.60 + axe-core in scratchpad (viewer/node_modules empty). 24 routes @1440x900 (.d) and 390x844 (.m). Non-GET /api/** intercepted.
Shots: audit\shots\, crops: audit\crops\, data: metrics.json interact.json interact2.json.
Format: ID | route | sev | cat | description | evidence

## 1. Shell
SH-01 | all | med | visual | Active sidebar link has 3px accent left rail via box-shadow inset (banned). | css/shell.css:97-102
SH-02 | all | low | visual | Empty .sidebar-footer renders 31px dead band; CSS display overrides [hidden]. | css/shell.css:117; js/components/sidebar.js
SH-03 | all | med | ux | Topbar height varies by route (48/81/145px); title jumps on navigation. | #topbar; kanban/issues/ideas
SH-04 | list routes | med | ux | "⌘K" hint in search but no handler; wrong glyph on Windows. | js/lib/topbar.js:19
SH-05 | list routes | med | a11y | Topbar search has no visible focus state; placeholder is the only accessible name. | js/lib/topbar.js:19-31
SH-06 | all @390 | med | a11y | Mobile drawer: no aria-expanded/controls, Escape doesn't close, focus not moved, desktop collapse "‹" visible in drawer. | js/components/sidebar.js
SH-07 | all | med | visual | Content gutters inconsistent (first column x from 248 to 315px); #screen-mount padding has 6 values. | probe.js
SH-08 | /task/:id | low | a11y | Nested second <main> (.td-body) — duplicate landmark. | axe
SH-09 | sidebar | low | ux | "Task" nav item isn't a screen (redirect to last task / empty); sidebar .badge never populated. | js/screens/task-detail.js:10-26

## 2. Dashboard
DB-01 | /dashboard | med | visual | Sticky-note bodies hard-clipped at max-height 16em mid-line, no fade/expand. | css/screens/desk.css:84
DB-02 | /dashboard | low | visual | Notes use hardcoded light paper colors + rotate tilt; off-token. | css/screens/desk.css:15-17,26,50-52
DB-03 | /dashboard | med | ux | Almost no project state; "+17 older" plain text at 2.26:1; prefs list widgets that never render. | .dk-older; /api/viewer/prefs
DB-04 | /dashboard | low | a11y | Clean-up row mouse-only; meta text 1.93:1. | .co-row

## 3. Kanban
KB-01 | /kanban | HIGH | visual | .card-title padding-right:100px wraps titles 1-2 words/line; IDs break over 3 lines. | css/screens/kanban.css:96
KB-02 | /kanban | HIGH | a11y | Cards are divs with click, no tabindex/role; 211 mouse-only targets. | js/components/card.js:25,43
KB-03 | /kanban @390 | HIGH | visual | Columns stack into 52,824px page, no column switcher; blocked rail ~200px; stepper clipped. | css/screens/kanban.css:777+
KB-04 | /kanban | med | ux | Non-active phases truncated to "P…", "R…". | .phs-future-card
KB-05 | /kanban | med | visual | Epic filter chips wrap 3-4 lines; uneven row. | .kanban-epic-chip
KB-06 | /kanban | med | ux | Epic counts disagree between chip and More dropdown (55 vs 20); unlabelled. | epic-chips vs .ed-list
KB-07 | /kanban | med | a11y | Epic More dropdown ignores Escape; its rows intercept board clicks. | js/components/epic-dropdown.js
KB-08 | /kanban | med | a11y | Group/Sort selects unnamed; nested interactive "open ↗" in chip button. | js/components/epic-chips.js:130-141; js/screens/kanban.js:111-145
KB-09 | /kanban | low | ux | Priority toggles labelled "Cr/Hi/Me/Lo" (also accessible name). | .kanban-pri-tog
KB-10 | /kanban | low | a11y | Disabled phase-slide buttons use .disabled class, still focusable. | button.phs-slide-btn
KB-11 | /kanban | med | visual | Banned box-shadows (recent glow, chip glows, dot ring, dropdown elevation). | css/screens/kanban.css:85,442,503,561,694,814
KB-12 | /kanban | low | visual | Group/Sort native selects w/ UA arrows; Arial fallback. | js/screens/kanban.js:111,124

## 4. Table
TB-01 | /table | HIGH | visual | Table overflows to x=1973 at 1440; Branch clipped, Started off-screen, no scroll cue. | table.d.png
TB-02 | /table | HIGH | ux | ID column truncated ("v3-polish…"); titles truncated; no title tooltips. | interact2.json
TB-03 | /table | med | a11y | Sortable th click-only: no tabindex/button/aria-sort. | th.tbl-th.is-sortable
TB-04 | /table | med | visual | Epic chips wrap 2-3 lines @1440; cut mid-word @390, no scroll cue. | .tbl-chip
TB-05 | /table @390 | med | ux | Only ID + truncated title visible; 8 columns off-canvas. | crops/table.m.top.png
TB-06 | /table | med | a11y | 24 contrast failures, mostly 10px uppercase pills. | axe


## 5. Task detail
TD-01 | /task/:id | HIGH | visual | Status pill accent-blue for every status (Done == In Progress). | css/screens/task-detail.css:58
TD-02 | /task/:id + modal | HIGH | ux | Spec/Plan/Notes markdown not rendered (raw ## and pipe tables). | js/components/task-detail-document.js:216-225
TD-03 | /task/:id | HIGH | a11y | Doc body unreachable by keyboard; "‹ back" is a span; editable sections mouse-only. | task-detail-document.js:110
TD-04 | task/issue/modal | med | visual | .link-pill has no CSS: UA #0000EE (1.88:1), no space between label and ID. | js/components/link-pills.js
TD-05 | /task/:id | med | ux | Empty sections print "no content" in full body ink. |
TD-06 | /task/NOPE-999 | med | ux | Raw 404 error string shown; stale previous-task topbar stays live (Edit opens previous task). | js/screens/task-detail.js:55-62 (no claimTopbar on error)
TD-07 | /task/:id?view=B | med | ux | Graph view renders but segmented toggle highlights Document; empty graph is dark recess. | task-detail.js:42-43
TD-08 | /task/:id | low | visual | Raw ISO date strings. |
TD-09 | /task/:id | med | visual | Handover rail DONE/TODO labels 1.18:1; tldr shows raw "## Run summary" in italic serif. | .ho-status-pill-*
TD-10 | /task/:id?view=B | med | visual | Graph frame box-shadow + radial-gradient recess (banned elevation). | css/screens/task-detail.css:276

## 6. Detail modal
DM-01 | modal | HIGH | a11y | No focus trap; no aria-labelledby; close named "close"; focus not returned to card. | js/components/detail-modal.js:28-41
DM-02 | modal | med | visual | .dm-body padding 0 (flush); page chrome (back, breadcrumb) repeated inside; title twice. | css/components/detail-modal.css:24
DM-03 | modal @390 | low | visual | "Open full ↗" wraps; modal ends 44px above bottom. |

## 7. Create/edit modals
EM-01 | +Task/Edit | HIGH | ux | Untouched forms count dirty → native confirm("Discard changes?") on Esc/backdrop. | js/components/edit/entity-modal.js:115-118,128-134
EM-02 | /ideas New Idea | HIGH | visual | Create Idea modal uses unstyled white native controls; separate implementation. | js/screens/ideas.js ~300-330
EM-03 | +Task | med | a11y | 13 inputs unlabeled (labels not bound); label text 4.22:1. |
EM-04 | +Task/Edit | med | a11y | ef-* inputs outline:none with no replacement; Title accent border at rest. | css/components/edit-fields.css
EM-05 | +Task | med | ux | "1 field need attention" on open (ungrammatical); Save disabled w/o indication. |
EM-06 | +Task/Edit | low | ux | Estimate free-text instead of enum; Stage input arbitrarily narrow. |
EM-07 | modals | low | visual | Modal entrance scale(0.96→1) — check vs RR motion. | css/components/entity-modal.css:25-26
EM-08 | modals/topbar | low | visual | Three primary-button variants (filled/outlined/ghost). |

## 8. Epics
EP-01 | /epics | med | ux | Every epic labelled "Exploring" (design_status); lifecycle status hidden. | js/screens/epics.js:53
EP-02 | /epics | med | visual | Progress bar only on some rows; meta columns misalign. | css/screens/epics.css:7
EP-03 | /epics @390 | HIGH | visual | Progress bars spill past card; titles 1 word/line; fixed grid, no media query. | css/screens/epics.css:7
EP-04 | /epics | low | ux | No count/search/sort unlike other list screens. |
EP-05 | /epics | med | a11y | 15/27 contrast failures. |

## 9. Epic detail
ED-01 | /epic/:id | HIGH | ux | "25/55 done · 64%": percent counts done+archived, numerator done only. | js/lib/epic-format.js:22-26 vs js/components/epic-detail-document.js:57-58
ED-02 | /epic/:id | med | ux | No task list / status breakdown. |
ED-03 | /epic/:id | low | ux | "Exploring" chip beside "active" text — two vocabularies. | .ed-ds
ED-04 | /epic/nope | low | visual | Missing-epic link unstyled default blue. | .ed-empty > a


## 10. Sessions
SE-01 | /sessions | med | visual | Off-state "superseded" chip has no border/bg; reads as text. | .status-chip:not(.on)
SE-02 | /sessions | med | a11y | Kind/status chips are spans w/ click, not focusable; 65 mouse-only incl. cards. | js/screens/sessions.js:23-34
SE-03 | /sessions | low | ux | Thread titles truncated slugs ~40ch; rail repeats tldr twice. |
SE-04 | /sessions | low | visual | 9px uppercase badges; counts 6.8pt at 3.78-4.34:1. | css/screens/sessions.css:73,123
SE-05 | /sessions | low | token-hygiene | Status chip tints from undefined tokens w/ literal fallbacks (--amber-tint-*, --muted-tint-*, --bl, --surface-1). | sessions.css:144-164

## 11. Issues
IS-01 | /issues | HIGH | visual | Board cards collapse: head is one flex row; titles 1 word/line, Fixed column ~3 chars clipped; Investigating card overflows. | css/screens/issues.css:154-166
IS-02 | /issues @390 | HIGH | visual | 4-col board ~60px columns, 35,863px page; only media query (720) covers resolved list. | issues.css:313
IS-03 | /issues | med | ux | View switcher labelled "COMPONENTS:" (wrong). |
IS-04 | /issues | med | ux | Every card full-width red STALE bar — signal carries no info. | js/components/aging-bar.js
IS-05 | /issues | med | visual | Severity glyph same amber hexagon for High and Medium. | js/components/severity-glyph.js
IS-06 | /issues | low | visual | Column headers italic serif, unlike every other screen. |
IS-07 | /issues | low | ux | "Critical 0" chip clickable → empty board. |
IS-08 | /issues | low | a11y | Fixed column scroll body not focusable; task pills mouse-only spans. |

## 12. Issue detail
ID-01 | /issue/:id | HIGH | ux | Reads issue.created/symptom; API sends discovered/evidence → "since —", empty body. | js/screens/issue-detail.js:115,146,216
ID-02 | /issue/:id | med | visual | Title 26px italic serif, differs from task/epic detail. |
ID-03 | /issue/:id | low | visual | Related link pill UA blue (TD-04). |

## 13. Bugs
BG-01 | /bugs | med | ux | Severity never shown; OPEN pill low-emphasis; off Archive filter reads as text. |
BG-02 | /bugs | med | visual | Titles 1-line ellipsis, no tooltip; @390 ~15ch, ~45px side inset. |
BG-03 | /bugs | med | a11y | article.bug-card clickable not focusable (23). |
BG-04 | /bugs | low | ux | No visible sort control; order (date desc) not inferable. |

## 14. Bug detail
BD-01 | /bug/:id | HIGH | ux | Every bug detail blank: GET /api/bugs/<id> hits startswith("/api/bugs") list branch; screen treats array as bug. B-999 identical (no not-found); actions live on blank record; Promote prompt pre-fills undefined. | taskmaster/backlog_server.py:10720; js/screens/bug-detail.js:44-66,199

## 15. Ideas
IE-01 | /ideas | HIGH | visual | Topbar overloaded: 4 status + 13 tag chips + search + toggles wrap 4 rows, 145px topbar. |
IE-02 | /ideas | med | visual | Titles ellipsis no tooltip; @390 ~12ch, tags hidden, list inset. |
IE-03 | /ideas | med | a11y | Status/tag filter chips mouse-only spans (17). | .ideas__status-chip, .ideas__tag-chip
IE-04 | /ideas | low | visual | Outlined "–" circle for no status is ambiguous. |
IE-05 | /ideas | med | a11y | 26 contrast failures (mono IDs, tag text). |

## 16. Archived
AR-01 | /archived @390 | med | visual | Titles hard-clipped by card edge, no ellipsis; group cards don't fill width. |
AR-02 | /archived | low | visual | "superseded" note floats far-right with big gap; titles ellipsis no tooltip. |
AR-03 | /archived | low | a11y | h3 with no h2; 40 contrast failures. |

## 17. Settings
ST-01 | /settings | low | visual | Native radios UA accent, off-palette; selected row not highlighted. |
ST-02 | /settings | low | ux | Single setting; card density only on Kanban; no theme control though prefs carry `theme`. |


## 18. Cross-cutting
X-01 | all | HIGH | visual | Status drawn 6+ ways (Kanban dot + "Waiting on human"; Table 10px pill; Task always-blue pill; Bug mono box; Issue outlined caps; Ideas lowercase mono; Sessions rounded chips). in-review label drifts.
X-02 | all | HIGH | a11y | Token-level contrast: --ink-3 on --bg-card 4.22:1 (118 nodes); #000 on #17181d 1.18:1; #0000ee 1.88:1; --ink-4 1.93-2.26:1; #60646f on #1d1e23 2.81:1. Fails on 17/24 routes.
X-03 | all | HIGH | a11y | Mouse-only click targets: Kanban 211, Sessions 65, Bugs 23, Ideas 17, Table 9, Issues 7, Task back link + dep rows.
X-04 | all | med | a11y | Focus styling inconsistent: only .tm-action has :focus-visible ring (components.css:280); 10 outline:none without replacement.
X-05 | all | med | visual | 22 rendered font sizes; ~1,060 nodes at 9-11px; 116 raw font-size literals.
X-06 | all | med | visual | 11 distinct radii vs 5 tokens (6/9/12/15/18).
X-07 | all | med | ux | Truncation hides meaning, no tooltips (table 500 nodes; bug/idea/archived titles; phases; slugs).
X-08 | various | med | ux | Dead "coming soon" controls visible (Task Archive, Sessions New note, Issues + Issue, ⌘K). | task-detail-document.js:89; sessions.js:53-57; issues.js:89
X-09 | lists @390 | med | visual | ~45px extra inset per side on list screens @390.
X-10 | all | low | visual | Form controls fall back to Arial (no font: inherit), 156 nodes.
X-11 | all | low | visual | Fonts via render-blocking Google Fonts @import; offline falls back. | css/shell.css:1
X-12 | error routes | med | ux | Error/empty states inconsistent (raw API error / fake untitled record / blue link).
X-13 | shared | med | visual | Shared components use box-shadow elevation (right rail components.css:96, edit dropdown edit-fields.css:117, issues.css:194); tokens --shadow-card, --shadow-lifted, --recess-inset.

## 19. Tokens
TK-01 inventory: tokens.css 134 lines, 89 props, dark only. Surfaces 8 (#14151a canvas, #0f1014 shell, #17181d panel, #1f2025 card, #25262c hover, #0f131c board-col, #0a0c11 deep, #181a20 issue). Ink: #e6e7eb/#a8a8ae/#7c8290/#4d4d54. Accent #4a9eff (+ dup #6ea8ff accent-blue/accent-edit). Status green/amber/red/purple(+gold alias). Severity 4, epic 6, bundle 6 (#5fcdb8 triple-use). Type Inter/JetBrains Mono/Source Serif Pro, 8 sizes 12-28, no lh/weight tokens. Spacing 3px grid 12 steps. Radius 6/9/12/15/18. Shadows 4 (banned). Motion 3 dur + 1 easing. No light theme though html data-theme + prefs.theme exist.
TK-02 | med | Hardcoded totals: 63 hex, 176 rgba, ~1,280 px, 116 raw font-size. Worst: kanban.css (1180 lines, 45 rgba, 261 px, 13 box-shadow), issues.css, task-detail.css.
TK-03 | med | 14 undefined tokens: --ink-1 (no fallback; detail-modal.css:17,22; components.css:353,367; epic-detail.css:24,33; epics.css:15; settings.css:8); --bl, --surface, --surface2, --surface-1, --s2, --danger-ink/-border/-bg, --amber-tint-bg/-border, --muted-tint-bg/-border (epics.css:12, sessions.css:144-164, table.css:31,49,64, ideas.css:331-334).
TK-04 | low | 30 custom props defined outside tokens.css (task-detail 12, desk 8, kanban 4, issues 3, shell 3).
TK-05 | med | Banned patterns:
  box-shadow: components.css:96; edit-fields.css:117; issues.css:194; kanban.css:85,442,503,561,694,814; task-detail.css:276; shell.css:101 (sidebar rail — also left-rail violation); shell.css:199 (topbar hairline inset, neutral); tokens --shadow-card/--shadow-lifted/--recess-inset/--recess-bg.
  hover transforms: none.
  other transforms: phase stepper slide kanban.css:523-524; modal scale entity-modal.css:25-26; note tilt desk.css:26; drawer slide shell.css:400-408; collapsed column rotate kanban.css:754.
  colored left rail: shell.css:101 only. Neutral structural left borders OK: sessions.css:96-108, task-detail.css:191, issues.css:204.
TK-06 | low | Inline style literals in JS: main.js:63; task-detail.js:18-23.

## Not checked
Thread board (no threads in data); write paths (drag-drop, save, conflict banner, toasts); light theme (none); touch/iOS; screen reader; kanban keyboard reorder; contrast over gradients.

## Totals
110: high 21, med 56, low 33. visual 47, ux 33, a11y 24, token 6.
High: KB-01 KB-02 KB-03 TB-01 TB-02 TD-01 TD-02 TD-03 DM-01 EM-01 EM-02 EP-03 ED-01 IS-01 IS-02 ID-01 BD-01 IE-01 X-01 X-02 X-03.
