<!-- User intent: the three full detail pages — task, issue, bug — read as one RR template in both themes, by keyboard and at phone width; a missing or failed record says so in words; the bug page acts through in-app forms instead of browser prompts; and the bug route stays pinned by tests. -->

# Viewer × Reality Reprojection — Plan 3c: Detail Pages

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Task detail, Issue detail and Bug detail as full pages on one §6 detail template — Technical meta line, the title as the page's only `h1`, a marker row, labelled sections of rendered markdown, a rail of relations — with the task page's graph view, state blocks and topbar finished, the issue and bug pages rebuilt from scratch on the template, the bug page's actions moved from `window.prompt/confirm/alert` to the shared entity form, and the bug route pinned by pytest.

**Architecture:** A new `components/detail-page.js` holds the template's builders (meta line, head, section, markdown body, grid, rail panel and group, dates, tags, copy button). The task document (`task-detail-document.js`, plan 2a) is refactored onto it with no DOM change, so the detail modal (3a) keeps working untouched; the graph view, the issue page and the bug page are built from the same builders and the same `td-*` classes, whose rules already live in `css/screens/task-detail.css`. What only issue and bug pages need (action row, location and repro lists, the visually hidden marker keys) goes in `css/screens/detail-pages.css` — the file 3d Task 2 creates by moving the legacy issue/bug detail rules into it verbatim, and which is 3c's from that merge on. Bug actions are three small schemas on `openEntityModal` plus one `confirmDialog`.

**Tech Stack:** Vanilla JS ES modules, plain CSS on RR tokens, `node --test` + jsdom, Playwright with mocked APIs (`viewer/tests/mock-api.js`), axe-core; Python `taskmaster/backlog_server.py` + pytest.

**Spec:** `docs/specs/2026-10-01-viewer-reality-reprojection-design.md` §5 (consumption), §6 "Detail template", §7 (bug route, issue field names, inline styles), §8 (server pytest), §11 amendments. Audit `docs/specs/2026-10-01-viewer-audit.md` §5 (TD-01…TD-10), §12 (ID-01…ID-03), §14 (BD-01), §18 (X-03, X-07, X-08, X-12). Carry-in: `.superpowers/sdd/plan-3-4-carries.md` section "3c" and "Controller allocation". Index (binding): `docs/plans/2026-10-06-viewer-rr-3-screens.md`.

**Depends on:** plan 2b (integration HEAD 638acec). Cross-track waits: Tasks 3 and 9 wait for 3a Task 2 (topbar row 1 at phone width); Tasks 7 and 8 wait for 3d Task 1 (`ISSUE_STATUS`, `severityKey`/`severityMeta`/`severityMarker` in `status.js`, `components/stale-tag.js`) and 3d Task 2 (`css/screens/detail-pages.css` holding the moved legacy rules); Task 10 waits for 3e Task 1, "The generic right rail" (see "Order").

## Carry-in (checked against 638acec)

Dropped as already fixed: TD-01, TD-02, TD-03, TD-04, TD-05, TD-08, TD-10 (2a); task-detail italics and shadows (`task-detail.css` is in `ENFORCED`, `grep -n "italic\|shadow"` finds only a comment); TK-06 (`main.js` and `screens/task-detail.js` hold no style literal; `task-missing.mock.spec.js` "no task id shows the empty state without inline styles" pins it); ID-01 field names (`util/issue-fields.js`, `issueEvidence` called once); the refused title message inside the heading (2b Task 4, `.td-title-message`); the bug route itself (51fa3e5 — Task 1 only pins its edges); "refused-reason wording on other screens" and "`.if-error` full-row rule outside task detail" (no screen but the task document mounts inline fields: `grep -rn mountInlineField viewer/js` → `task-detail-document.js` only); the inline `aria-describedby` wrapper item (settled in 2b). Moved by the controller: `http()` 404 detection to plan 4 (this plan still switches its own screens to `e.code === 404`, which `api.js:58` already sets). Not taken, with reason: locale and time zone of absolute dates — `lib/time.js` has 14 importers; one owner should change how every screen reads a stamp (recommend plan 4). Taken from the 3b list because the index gives 3c `task-detail-graph.js` and `task-detail.css`: the graph's 11px non-uppercase labels (the node shadow is already gone).

## Global Constraints

The plan 3 index's Global Constraints, File ownership and Review Focus apply in full (reference: `docs/plans/2026-10-06-viewer-rr-3-screens.md`). 3c specifics:

- Worktree `C:/Users/gruku/Files/Claude/taskmaster/.worktrees/rr3c` (written `<wt>`), branch `rr3/c-detail-pages`. Mocked specs on port **8833**: `MOCK_PORT=8833 npm --prefix <wt>/viewer run test:mock -- --workers=2 <spec>`. Server: `env --chdir=<wt> timeout 900 C:/Users/gruku/Files/Claude/taskmaster/.venv/Scripts/python.exe -m pytest tests -k "server or viewer" -q -p no:cacheprovider`.
- **The task document's embedded contract holds.** Every change to `task-detail-document.js` keeps `mountTaskDetailDocument(root, { chrome: 'embedded', titleHost, … })` working for 3a's detail modal: same classes, same `data-test`/`data-focus` hooks, no page-only control (meta line, row-1 Edit, Document/Graph switch) leaking into the dialog. `npm --prefix <wt>/viewer run test:mock -- --workers=2 task-detail.mock.spec.js` passes after every task that touches it.
- **Template classes.** Issue and bug pages use the `td-*` template classes (rules in `task-detail.css`) plus `dp-*` for what only they need (`detail-pages.css`). They never use the legacy `.issue-detail`, `.id-*`, `.bug-detail*` or `.aging-bar*` classes. 3d Task 2 moves those rules verbatim from `issues.css`/`bugs.css` into `detail-pages.css` (linked after `ideas.css`); 3c deletes each once no page uses it (Tasks 7, 8) and then enforces the file. 3c never edits `issues.css` or `bugs.css`. Page roots carry `td-doc td-doc--page td-page`, so the shell's scroll policy (`shell.css:323,339`) applies without editing `shell.css`.
- **One `h1` per page**: the entity title, in every view (the graph view included).
- **State blocks** are `stateBlock()` from `components/empty-state.js` with `label` = the id, a headline, a one-sentence hint, at most one action. Not found is `e.code === 404`, never a regex over `e.message`.
- **No `window.prompt/confirm/alert`** on any detail page; no `innerHTML` with record data except `renderMarkdown()` and `linkPillsEl()` nodes.
- **Leaving a page with a form open** (task Edit, a bug action): on screen dispose, when `openModalCount() > 0`, the page calls `topModal().requestClose()` once — a clean form closes, a typed one asks "Discard changes?"; "Keep editing" leaves it over the new screen, still writing to the record it was opened for (the 2b detail-modal ruling, applied to pages).
- `detail-pages.css` joins `ENFORCED` in whichever of Tasks 7 and 8 runs second (the one that deletes the last legacy rule); 3d Task 2 already links it in `viewer/index.html`, so 3c adds no link.

## Review Focus

The index's five, pinned on 3c's screens:

1. **Another writer changes the task while an inline editor is open on the full page** (a section being written, the title being typed, or a reload already in flight when the editor opens). The editor stays open with its text; the change shows once the editor closes; focus is never dropped to `<body>`. → Task 3, tests "another writer's change waits while a section is edited on the page, then shows" and "a reload already under way when the title editor opens does not paint over it" (`task-page.mock.spec.js`).
2. **An id that does not exist, or a load that fails** (`#/task/NOPE-999`, `#/issue/ISS-999`, `#/bug/B-999`, a 500 with a Python traceback). A state block in words with the id as its label and one way on; no raw API text; no control left in either topbar row from the previous record; the id not remembered as the last one opened. → Task 3 "missing task opened after a real one drops that task's topbar controls" (`task-missing.mock.spec.js`, updated for row 1) and "a load that fails says so in words and offers Try again" (`task-page.mock.spec.js`), Task 7 "ISS-999 is not found in words, and a failed load says so without the server's text", Task 8 "B-999 is not found in words; a 500 says so without the server's text; no action is offered without a record".
3. **Both themes**, including the graph view, the stale tag, the bug action row and the forms. → axe `color-contrast` zero in dark and light in Tasks 5, 7, 8, 9 and on every route in Task 10.
4. **Real data volume at 390px** (a 120-character title with no break, a 200-character location path, 40 dependencies in the graph). `scrollWidth <= innerWidth`, IDs never broken mid-ID, cut text keeps its `title`. → Task 5 "the graph at 390 scrolls nothing sideways and every cut label keeps its full text", Tasks 7 and 8 "a long title and a long path stay inside 390px", Task 10 measurements.
5. **Leaving a page with something open** (the first load in flight; the task Edit form; a bug action form, clean or typed). No page error, nothing of the old page in either topbar row, a typed form asks first. → Task 3 "leaving the page while it loads leaves nothing behind" and "leaving with Edit open: clean closes, typed asks"; Task 9 "leaving the bug page with an action form open: clean closes, typed asks".

Keyboard walks (index item 3): Task 5 (graph: tabs by arrow keys, nodes as links), Task 7 (issue page), Task 8 (bug page).

## Order

| # | Task | Depends on | Notes |
|---|---|---|---|
| 1 | Bug route pins (pytest) | — | Python only |
| 2 | Detail template builders; task document on them | — | |
| 3 | Task page frame: states, Edit in row 1, view switch, another writer, carried refusals, leaving | 2; **3a Task 2 merged** | |
| 4 | Task markers and strips: epic swatch, gate words | 3 | same files as 3 |
| 5 | Graph view | 2, 4 | `task-detail.css` after 4 |
| 6 | Conflict banner speaks in option words | — | shared files (see report) |
| 7 | Issue page | 2; **3d Tasks 1 and 2 merged** | |
| 8 | Bug page (read view) | 2; **3d Tasks 1 and 2 merged** | |
| 9 | Bug actions as in-app forms | 8; **3a Task 2 merged** | |
| 10 | Verification | 1–9; **3e Task 1 (right rail) merged** | |

Serial run order: 1, 2, 3, 4, 5, 6, 8, 9, 7, 10. Tasks 1, 6 and 8 have no source file in common with 3–5 and may run beside them in sibling worktrees if the controller wants (`ENFORCED` merges as a union); Task 8, like 7, waits for 3d Tasks 1 and 2.

**The index's "3c's rail panels wait for 3e's right-rail task":** in code, 3c's rails need nothing new from `right-rail.js` — the task rail is `railPanels()` as it is today, the rail's look (`.td-rail`, `.td-panel`, `.td-dep`, `.link-pill`) is in 3c's `task-detail.css`, and the issue and bug rails are built with `railPanel`/`railGroup` from Task 2 (same markup as `right-rail.js`'s private `panel`/`sub`). 3e's plan agrees: its Task 1 keeps `railPanels`, `statusPill` and `openStatusMenu` signatures and output for 3c. The wait applies to Task 10, whose task-page rail scenes and axe runs include 3e's handover pill and its failure words (`.ho-status-error`).

---

### Task 1: Bug route — pin the single, the 404 and the exact list match

The route was fixed in plan 1 (51fa3e5, `backlog_server.py:10724-10760`) with tests in `tests/test_server_bugs.py:81-116`. This task pins the edges spec §7 names that are not yet tested and the native-store parity the plan 1 review deferred.

**Files:**
- Modify: `tests/test_server_bugs.py`, `tests/test_native_routing_viewer.py`
- Modify only if a test fails: `taskmaster/backlog_server.py` (GET branch `elif clean_path.startswith("/api/bugs/")` / `elif clean_path == "/api/bugs"`)

**Interfaces:**
- Consumes: `running_server` (`tests/test_server_api.py`), `_post`, `_get`, `_status` (`test_server_bugs.py`); `twins`, `same` (`test_native_routing_viewer.py`; its `_seed` creates bug `B-001` "A bug" with body "Bug body").
- Produces: the contract Tasks 8–9 rely on — `GET /api/bugs/<id>` → 200 with one object carrying every stored field plus `summary` (the body, stripped); unknown id, empty id or an id outside `[A-Za-z0-9_-]+` → 404 `{ ok: false, error }`; `GET /api/bugs` (only that exact path, any query) → a JSON list.

- [ ] **Step 1: Write the tests** — append to `tests/test_server_bugs.py`:

```python
def _raw(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def test_bug_get_single_carries_body_and_location(running_server, tmp_path):
    base, _ = running_server
    a = _post(f"{base}/api/bugs", {
        "title": "edge", "discovered_by": "user", "found_in": "T-102",
        "location": ["viewer/css/screens/kanban.css:87"], "body": "The border is **too faint**.",
    })
    one = _get(f"{base}/api/bugs/{a['id']}")
    assert one["summary"] == "The border is **too faint**."
    assert one["location"] == ["viewer/css/screens/kanban.css:87"]
    assert one["found_in"] == "T-102"
    assert "_body" not in one


def test_bug_get_single_ignores_query(running_server, tmp_path):
    base, _ = running_server
    a = _post(f"{base}/api/bugs", {"title": "alpha", "discovered_by": "user"})
    assert _get(f"{base}/api/bugs/{a['id']}?include_archive=1")["id"] == a["id"]


def test_bug_get_empty_id_is_404_not_the_list(running_server, tmp_path):
    base, _ = running_server
    _post(f"{base}/api/bugs", {"title": "alpha", "discovered_by": "user"})
    status, body = _raw(f"{base}/api/bugs/")
    assert status == 404
    assert not body.lstrip().startswith("[")


def test_bug_list_route_matches_only_its_own_path(running_server, tmp_path):
    base, _ = running_server
    _post(f"{base}/api/bugs", {"title": "alpha", "discovered_by": "user"})
    status, body = _raw(f"{base}/api/bugsx")
    assert not (status == 200 and body.lstrip().startswith("["))
    assert isinstance(_get(f"{base}/api/bugs?found_in=T-102&include_archive=true"), list)


def test_bug_get_404_body_is_a_reason(running_server, tmp_path):
    base, _ = running_server
    status, body = _raw(f"{base}/api/bugs/B-999")
    assert status == 404
    assert json.loads(body) == {"ok": False, "error": "unknown bug B-999"}
```

Append to `tests/test_native_routing_viewer.py`:

```python
def test_single_bug_reads_match(twins):
    legacy, _native = same(twins, "GET", "/api/bugs/B-001")
    assert legacy[0] == 200, "the seed's bug must exist, or this compares two 404s"
    for path in ("/api/bugs/B-404", "/api/bugs/", "/api/bugs/B-001?x=1"):
        same(twins, "GET", path)
```

- [ ] **Step 2: Run** `env --chdir=<wt> timeout 900 C:/Users/gruku/Files/Claude/taskmaster/.venv/Scripts/python.exe -m pytest tests/test_server_bugs.py tests/test_native_routing_viewer.py -q -p no:cacheprovider -k "bug"` — Expected: PASS (these pin a fixed route). Any FAIL is a real defect: fix it in the GET branch named above (the list branch must stay `clean_path == "/api/bugs"`; the single branch keeps its `re.fullmatch(r"[A-Za-z0-9_\-]+", bug_id)` guard) and re-run.
- [ ] **Step 3: Run** the server command from Global Constraints — Expected: no new failures; report the dot and failure counts.
- [ ] **Step 4: Commit** — `git -C <wt> add tests/test_server_bugs.py tests/test_native_routing_viewer.py` (plus `taskmaster/backlog_server.py` only if changed) — `test(server): pin GET /api/bugs/<id> — body and location carried, empty and unknown ids 404 with a reason, the list matches only /api/bugs, native store answers the same`

---

### Task 2: The detail template builders, and the task document on them

**Files:**
- Create: `viewer/js/components/detail-page.js`, `viewer/tests/unit/detail-page.test.js`
- Modify: `viewer/js/components/task-detail-document.js` (use the builders; export `taskMeta`)
- Test: `viewer/tests/unit/task-detail-document.test.js` (unchanged, must pass), `viewer/tests/task-detail.mock.spec.js` (unchanged, must pass)

**Interfaces:**
- Consumes: `h` (`util/h.js`), `renderMarkdown`, `formatStamp`, `copyToClipboard`, `icon`.
- Produces (`viewer/js/components/detail-page.js`):

```js
// User intent: one detail template for tasks, issues and bugs — a quiet Technical meta line, the title as the page's
// only h1, a row of markers, labelled sections of rendered text and a rail of what the record relates to — so the three
// pages read as one, in both themes and at phone width.
import { h } from '../util/h.js';
import { renderMarkdown } from './markdown.js';
import { formatStamp } from '../lib/time.js';
import { copyToClipboard } from '../lib/copy.js';
import { icon } from './icon.js';

const COPIED_MS = 1500;

// The meta line: parts in order (null, false and '' dropped; a string becomes a span), a '·' between them that screen
// readers skip.
export function detailMeta(parts, { test = 'meta' } = {}) {
  const line = h('div', { class: 'td-meta', 'data-test': test });
  parts.filter((p) => p != null && p !== false && p !== '').forEach((part, i) => {
    if (i) line.appendChild(h('span', { class: 'td-sep', 'aria-hidden': 'true' }, '·'));
    line.appendChild(typeof part === 'string' ? h('span', {}, part) : part);
  });
  return line;
}

// A date a person reads, the exact instant in its title; null when the value is not a date.
export function stampEl(iso, { prefix = '' } = {}) {
  const stamp = formatStamp(typeof iso === 'string' ? iso : null);
  if (!stamp.title) return null;
  const time = h('time', { datetime: iso, title: stamp.title }, stamp.text);
  return prefix ? h('span', { class: 'td-meta__created' }, `${prefix} `, time) : time;
}

// A button that copies `value` and says so in a live region. `timers` (a Set) collects its reset timer for the caller
// to clear on unmount.
export function copyButton({ value, label, children = [], className = '', focus, test, timers }) {
  const status = h('span', { class: 'td-copy__status', role: 'status' });
  const glyph = h('span', { class: 'td-copy__icon' }, icon('copy', { size: 14 }));
  const btn = h('button', {
    type: 'button', class: `td-copy ${className}`.trim(), 'aria-label': label, 'data-focus': focus, 'data-test': test,
  }, [...children, glyph, status]);
  let timer;
  btn.addEventListener('click', async () => {
    const ok = await copyToClipboard(value);
    status.textContent = ok ? 'Copied' : 'Copy failed';
    glyph.replaceChildren(icon(ok ? 'check' : 'alert', { size: 14 }));
    clearTimeout(timer);
    timers?.delete(timer);
    timer = setTimeout(() => { status.textContent = ''; glyph.replaceChildren(icon('copy', { size: 14 })); }, COPIED_MS);
    timers?.add(timer);
  });
  return btn;
}

// The id as a copy button, the way every detail page opens its meta line.
export function copyId({ id, noun, timers }) {
  return copyButton({
    value: id, label: `Copy ${noun} id`, className: 'td-id', focus: 'copy:id', test: `${noun}-id`, timers,
    children: [h('span', { class: 'td-id-text' }, id || '—')],
  });
}

export function detailTitle(text) {
  return h('h1', { class: 'td-title', 'data-test': 'title' }, text || '(untitled)');
}

export function detailHead({ meta, title, after = [] }) {
  return h('header', { class: 'td-head' }, [meta, title, ...after]);
}

// A secondary fact in the marker row: a Technical key and its value.
export function detailTag(name, label, value) {
  return h('span', { class: 'td-tag', 'data-tag': name },
    [h('span', { class: 'td-tag__k' }, label), h('span', { class: 'td-tag__v' }, value)]);
}

export function sectionHeading(label, level = 2, extra = null) {
  return h('div', { class: 'td-section-head' }, [h(`h${level}`, { class: 'td-section-h' }, label), extra]);
}

export function markdownBody(source) {
  const el = h('div', { class: 'md-body' });
  // renderMarkdown sanitises; it is the only path record text takes into innerHTML.
  el.innerHTML = renderMarkdown(typeof source === 'string' ? source : '');
  return el;
}

export function detailSection({ key, label, level = 2, body, extra = null, test }) {
  return h('section', { class: 'td-section', 'data-section': key, 'data-test': test ?? `sec-${key}` },
    [sectionHeading(label, level, extra), body]);
}

// Labelled dates; null when none of them is a date.
export function datesList(cells) {
  const items = cells.map(([label, iso]) => [label, stampEl(iso)]).filter(([, el]) => el);
  if (!items.length) return null;
  return h('dl', { class: 'td-dates', 'data-test': 'dates' },
    items.map(([label, el]) => h('div', { class: 'td-date' }, [h('dt', {}, label), h('dd', {}, el)])));
}

// Body and rail; with no panels there is no rail and the body takes the width.
export function detailGrid({ body, panels = [], railLabel = 'Related' }) {
  const rail = panels.length ? h('aside', { class: 'td-rail', 'data-test': 'rail', 'aria-label': railLabel }, panels) : null;
  return h('div', { class: `td-grid${rail ? '' : ' td-grid--solo'}` }, [body, rail]);
}

// The same markup as right-rail.js's task panels, for rails that are not a task's.
export function railPanel({ name, label, level = 2, children = [] }) {
  return h('section', { class: `td-panel td-panel-${name}`, 'data-panel': name },
    [h(`h${level}`, { class: 'td-rail-h' }, label), ...children]);
}

export function railGroup({ name, label, level = 2, body }) {
  return h('div', { class: 'td-rail-group', 'data-sub': name }, [h(`h${level + 1}`, { class: 'td-rail-sub' }, label), body]);
}
```

  `task-detail-document.js` additionally exports `taskMeta(raw, { timers }) → HTMLDivElement` (today's `renderMeta`, built with `detailMeta`, `copyId({ id, noun: 'task', timers })` — keeping `data-test="task-id"`, `data-focus="copy:id"`, `aria-label="Copy task id"` — and `stampEl(raw.created, { prefix: 'created' })`). Embedded contract: unchanged; `taskMeta` is only called for `chrome: 'page'`.

- [ ] **Step 1: Unit tests** — `detail-page.test.js` (jsdom, set up like `task-detail-document.test.js:1-22`): `detailMeta(['A', null, '', false, h('a',{href:'#/x'},'B')])` has children `span`, `span.td-sep[aria-hidden]`, `a` and text `A·B`; `stampEl('2026-09-28T09:00:00Z')` is a `time` with `datetime` and a non-empty `title`, `stampEl('nope')` and `stampEl(null)` are `null`, `stampEl(iso, { prefix: 'created' })` is `span.td-meta__created` whose text starts with `created `; `copyId({ id: 'B-031', noun: 'bug' })` has `aria-label="Copy bug id"`, `data-test="bug-id"`, and a click writes `B-031` to the stub clipboard and sets `.td-copy__status` to `Copied`; `detailTitle('')` is an `h1.td-title` reading `(untitled)`; `detailSection({ key: 'evidence', label: 'Evidence', body: markdownBody('**x**') })` has `data-section="evidence"`, `data-test="sec-evidence"`, an `h2.td-section-h` and a `strong`; `markdownBody('<img src=x onerror=alert(1)>')` contains no element with an `onerror` attribute; `datesList([['Created', null]])` is `null`, `datesList([['Created', '2026-09-28T09:00:00Z'], ['Done', '']])` has one `dt`; `detailGrid({ body: div, panels: [] })` has class `td-grid td-grid--solo` and no `aside`, with one panel an `aside.td-rail[aria-label="Related"]`; `railPanel({ name: 'relations', label: 'Relations', level: 2, children: [x] })` is `section.td-panel.td-panel-relations[data-panel="relations"] > h2.td-rail-h`; `railGroup({ name: 'links', label: 'Links', level: 2, body })` is `div.td-rail-group[data-sub="links"] > h3.td-rail-sub`.
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/detail-page.test.js` — Expected: FAIL (module not found).
- [ ] **Step 3: Implement** `detail-page.js` as above. In `task-detail-document.js` replace `copyButton`, `renderMeta`, `tag`, `heading`, `mdBody`, `renderDates` and the grid assembly with the builders (`copyButton` calls pass `timers`; `heading(label, extra)` → `sectionHeading(label, level, extra)`; the grid → `detailGrid({ body, panels })`), export `taskMeta`. The markup must not change: same tags, classes, attributes and order.
- [ ] **Step 4: Run** `npm --prefix <wt>/viewer run test:unit` and `MOCK_PORT=8833 npm --prefix <wt>/viewer run test:mock -- --workers=2 task-detail.mock.spec.js task-missing.mock.spec.js` — Expected: PASS with no test edited.
- [ ] **Step 5: Commit** — `git -C <wt> add viewer/js/components/detail-page.js viewer/tests/unit/detail-page.test.js viewer/js/components/task-detail-document.js` — `refactor(viewer): one detail template — meta line, head, sections, dates, grid, rail panels — and the task document built on it with no change to its markup`

---

### Task 3: Task page frame — state blocks, Edit in row 1, the view switch, another writer, leaving

**Depends on:** Task 2; **3a Task 2 merged** (topbar row 1 at phone width — the row-1 Edit and its 44px check stand on it; index wait "3c T3/T9 ← 3a T2").

**Files:**
- Modify: `viewer/js/screens/task-detail.js`, `viewer/js/components/task-detail-document.js` (`mountTaskTopbar`, `rememberView`, and the `data-stored` mark in `inlineField`)
- Create: `viewer/tests/task-page.mock.spec.js`
- Modify tests: `viewer/tests/task-missing.mock.spec.js`, `viewer/tests/unit/task-detail-document.test.js`

**Interfaces:**
- Consumes: `stateBlock` (`empty-state.js`), `claimTopbar`, `claimTopbarPrimary`, `tmSegmented`, `tmAction`, `openModalCount`, `topModal` (`modal.js`), `getTaskDetailFull`, `rememberView`.
- Produces: `mountTaskTopbar({ view, onToggleVariant, onEdit })` — row 2 holds only the Document/Graph `tmSegmented`; Edit is `tmAction({ icon: 'edit', label: 'Edit', title: 'Edit task', variant: 'primary', onClick })` appended to `claimTopbarPrimary()`. Signature unchanged (Task 5's graph calls it). Embedded chrome never calls it.
- Behaviour (each line is a test):
  1. **No id** (no remembered task): `stateBlock({ state: 'empty', label: 'Task', headline: 'No task open', hint: 'Pick a task from a board.', action: { label: 'Open the Kanban', href: '#/kanban' } })`. The screen-local `stateBlock` function is deleted.
  2. **Not found** (`e?.code === 404`): `stateBlock({ state: 'missing', label: id, headline: 'Task not found', hint: 'It may have been archived, renamed or removed.', action: { label: 'Open the Kanban', href: '#/kanban' } })`.
  3. **Failed** (any other error): `stateBlock({ state: 'error', label: id, headline: 'Could not load this task', hint: 'Something went wrong while loading it. Try again in a moment.', action: { label: 'Try again', onClick: () => refresh() } })`. The page text never contains the error message, a status code or `/api`.
  4. Both topbar rows are claimed on every outcome; after a not-found or error `#topbar-primary` is empty and row 2 holds no switch.
  5. The switch shows the view on screen: `#/task/T-102?view=B` → "Graph" `aria-pressed="true"`; pressing "Document" shows `.td-page-A`, presses "Document", rewrites the hash to `#/task/T-102` and saves `screens.task_detail.view = 'A'` (TD-07, pinned).
  6. **Another writer while editing** (Review Focus 1): unchanged mechanism (`refresh()` returns early while `store.isEditing(id)`, and re-checks after the fetch; `store.endEdit` re-emits `task:<id>`), now pinned on the page.
  7. **Leaving** (Review Focus 5): dispose runs `generation++` before anything else, so a load that resolves later paints nothing; then, if `openModalCount() > 0`, `topModal().requestClose()` once.
  8. **A carried refusal is dropped once the field's stored value changes** (handed over by 3a; affects the page and the modal, both re-mount through `rememberView`). `inlineField()` marks each wrapper with `data-stored` = `JSON.stringify(task[fieldKey] ?? null)` of the record it was mounted from. `rememberView(scope)` captures `[key, text, stored]` per refusing field, and its restore says a refusal again only into a wrapper whose `data-stored` equals the captured `stored`. Another writer's change to that field drops the reason; a change to another field keeps it (today's behaviour). Embedded contract: unchanged API; the modal gets the fix through the same function.

- [ ] **Step 1: Mocked tests** — `task-page.mock.spec.js` (header comment = user intent; `mockApi` before `goto`; `afterEach` asserts `unmockedWrites(page)` empty; table: `/api/board` and `/api/backlog` = `BOARD`, `/api/bugs` = `[]`, `/api/task/T-102/detail` = `taskDetail(DETAIL_TASK, 't1:fixture', RICH_RELATED)`, `PUT /api/viewer/prefs` = `{}`; copy `renamedElsewhere` from `task-detail.mock.spec.js:370-377` into this file). Record `pageerror` in every test and assert none.
  - "the view switch shows the view on screen and saves the choice": `goto('/#/task/T-102?view=B')`; `#topbar-actions .tm-segmented button[aria-pressed="true"]` has text `Graph` and `.td-page-B` is visible; click "Document" → pressed `Document`, `.td-page-A` visible, `location.hash === '#/task/T-102'`, the last `PUT /api/viewer/prefs` body has `screens.task_detail.view === 'A'`; click "Graph" → pressed `Graph`.
  - "Edit sits in row 1 as the page's primary and the switch alone in row 2": `#topbar-primary [title="Edit task"]` has classes `btn` and `btn--primary`; `#topbar-actions [title="Edit task"]` count 0; at 390×844 the Edit button is visible and ≥44px tall.
  - "another writer's change waits while a section is edited on the page, then shows": click `[data-focus="edit:notes"]`; type ` more` at the end of the textarea; `renamedElsewhere(page, 'Renamed elsewhere')`; after 300ms the `h1` still reads `DETAIL_TASK.title`, the textarea is focused and its value ends with ` more`; press Escape → `h1` reads `Renamed elsewhere`; `document.activeElement` is not `body` and is connected; no PATCH was sent.
  - "a reload already under way when the title editor opens does not paint over it": route `**/api/task/T-102/detail` so the next request waits on a promise the test holds; bump the board revision (as `renamedElsewhere` does) so the page starts that fetch; click the `h1 .ef-editable`; type `Draft`; release the held request with `taskDetail({ ...DETAIL_TASK, title: 'Renamed elsewhere' }, 't1:other', RICH_RELATED)`; after 300ms the title input is still present, focused, and holds `Draft`; Escape → `h1` reads `Renamed elsewhere`.
  - "leaving the page while it loads leaves nothing behind": hold the first `/api/task/T-102/detail`; `goto('/#/task/T-102')`; `location.hash = '#/kanban'`; release the request with the task; expect `#screen-mount .td-doc` count 0, `#topbar-primary [title="Edit task"]` count 0, no `.tm-segmented` with a "Graph" button, no page error.
  - "leaving with Edit open: clean closes, typed asks": on the page click Edit; `location.hash = '#/kanban'` → `.modal` count 0, no `alertdialog`. Back to `#/task/T-102`, Edit, type into the title field, `location.hash = '#/kanban'` → `getByRole('alertdialog', { name: 'Discard changes?' })` visible; "Keep editing" → the form is still open with the typed title; Escape → "Discard" → `.modal` count 0; no PATCH sent.
  - "a load that fails says so in words and offers Try again": first answer 500 `{ error: 'Traceback: KeyError depends_on' }` → `.tm-empty[data-state="error"]` with `.tm-empty__label` `T-102` and headline `Could not load this task`; page text has no `Traceback`, `500`, `/api`; then route the detail to the task and click "Try again" → `h1` reads the title.
  - "a refused reason goes when another writer changes that field, and stays when they change another" (behaviour 8): `PATCH /api/tasks/T-102` → 409 `{ ok: false, error: 'Completion blocked: review-gate is still open' }`; on the page open the status picker, choose Done, press Tab → `[data-field="status"] .if-error` reads the reason; a `renamedElsewhere`-style re-read with `{ ...DETAIL_TASK, title: 'Renamed elsewhere' }` → the `h1` updates and the reason is still shown; a second re-read with `{ ...DETAIL_TASK, title: 'Renamed elsewhere', status: 'in-review' }` → the status marker reads `In review` and `[data-field="status"] .if-error` is empty. The last step FAILS today (the reason is carried beside the new value).
  - Unit (`task-detail-document.test.js`): a page whose status save answers `{ error: 'No' }` shows `No`; `const restore = rememberView(root)`; re-mount with `status: 'in-review'` and call `restore(root)` → no `.if-error` text; repeat with the status unchanged and the title changed → `No` is shown again; every `.if-wrap` carries `data-stored`.
- [ ] **Step 2: Update** `task-missing.mock.spec.js`: in "missing task opened after a real one drops that task's topbar controls" expect `#topbar-primary [aria-label="Edit task"]` visible on REAL-1 and, after NOPE-999, `#topbar-primary` with no children; add `await expect(page.locator('#screen-mount .tm-empty__label')).toHaveText('NOPE-999')` to "missing task shows not-found and clears the topbar". Replace "the not-found link takes the signature colour…" with "the not-found state offers one way on, a link styled as a button": `#screen-mount .tm-empty a.btn` has text `Open the Kanban`, `href` `#/kanban`, and axe `color-contrast` on `#screen-mount` is clean in dark and light. In "no task id shows the empty state without inline styles" also expect the `Open the Kanban` link.
- [ ] **Step 3: Run** `MOCK_PORT=8833 npm --prefix <wt>/viewer run test:mock -- --workers=2 task-page.mock.spec.js task-missing.mock.spec.js` — Expected: the row-1, state-block, Try-again, leaving-with-Edit and refusal-on-a-changed-field tests FAIL; the view-switch and another-writer tests may already pass (they pin 2a behaviour).
- [ ] **Step 4: Implement** per behaviour 1–8 in `screens/task-detail.js`, `mountTaskTopbar`, `inlineField` and `rememberView`.
- [ ] **Step 5: Run** the two specs plus `task-detail.mock.spec.js conflict-banner.mock.spec.js shell.mock.spec.js` and the unit suite — Expected: PASS.
- [ ] **Step 6: Commit** — `git -C <wt> add viewer/js/screens/task-detail.js viewer/js/components/task-detail-document.js viewer/tests/task-page.mock.spec.js viewer/tests/task-missing.mock.spec.js viewer/tests/unit/task-detail-document.test.js` — `feat(viewer): the task page says missing and failed in state blocks with one way on, puts Edit in topbar row 1, and is pinned against another writer, a reload mid-edit and leaving with a form open`

---

### Task 4: Task markers and strips — the epic as a swatch and a name, gates in words

**Depends on:** Task 3 (same file).

**Files:**
- Modify: `viewer/js/components/task-detail-document.js` (epic tag), `viewer/js/components/gate-pipeline.js` (`renderGatePipeline` only; `laneBadge` untouched), `viewer/css/screens/task-detail.css` (swatch classes, gate word)
- Test: `viewer/tests/unit/gate-pipeline.test.js`, `viewer/tests/unit/task-detail-document.test.js`, `viewer/tests/task-page.mock.spec.js`

**Interfaces:**
- Consumes: `epicSwatch(epicId, epics) → 1..6 | null` (`lib/epics.js`, exists since 2b); `store.getBacklog().epics`.
- Produces:
  - The epic tag: `a.td-tag.td-epic[data-tag="epic"][href="#/epic/<id>"]` → `span.td-swatch.td-swatch--cat-<n>[aria-hidden]` (no swatch span when `epicSwatch` is `null`), `span.td-tag__k` "Epic", `span.td-tag__v` = the epic's `name`, else its id. No `style` attribute. `task-detail-document.js` no longer imports `assignEpicColors`, `epicColor` or `epicCssVar` (3a may then retire them in `lib/epics.js`).
  - `renderGatePipeline(task)` (still an escaped HTML string): each gate `span.gp-gate.gate--<state>.marker.marker--<tone>[title="<Gate label>: <state word>"]` → `.marker__shape` (unchanged), `span.marker__word` = gate label, `span.gp-gate__state` = state word (visible). `.gp-word` is gone. Labels: `spec-review` Spec review, `plan-review` Plan review, `design-review` Design review, `review-gate` Review gate, anything else sentence-cased. State words: done `done`, pass `passed`, warn `passed with warnings`, fail `failed`, skipped `skipped`, pending `pending`.
  - `task.gate_state` (`'<gate>:<state>'`): no line when its gate is on the lane's track (the node already says it); otherwise `span.gp-state` "Current step: <Gate label> — <state word>"; a value not of that shape is not printed. The raw string never reaches the page.
  - Embedded contract: the modal's marker row shows the same epic tag and gate words.
- CSS (`task-detail.css`, tokens only): `.td-swatch` background `var(--foreground-subtle)`; `.td-swatch--cat-1 … --cat-6 { background: var(--cat-N); }`; `.gp-gate__state { color: var(--foreground-subtle); font-family: var(--font-technical); font-weight: var(--font-technical-weight); font-size: var(--size-technical-small); }`; the `.td-doc :is(.gp-word, .ml-word)` rule becomes `.td-doc .ml-word`.

- [ ] **Step 1: Tests.** `gate-pipeline.test.js`: `renderGatePipeline({ lane: 'full', gates: { 'spec-review': { verdict: 'pass' }, 'plan-review': { verdict: 'warn' } }, gate_state: 'review-gate:pending' })` parsed into a jsdom `div` → three `.gp-gate`; their `.marker__word` texts `['Spec review', 'Plan review', 'Review gate']`; `.gp-gate__state` texts `['passed', 'passed with warnings', 'pending']`; no `.gp-state`; no text `review-gate:pending`; `{ lane: 'express', gates: {}, gate_state: 'spec-review:fail' }` → `.gp-state` text `Current step: Spec review — failed`; `gate_state: 'weird'` → no `.gp-state`; a gate name `<b>x</b>` in `gate_state` is text. `task-detail-document.test.js`: with `FAKE_BACKLOG.epics = [{ id: 'core', name: 'Core platform' }]`, the page's `[data-tag="epic"]` has no `style` attribute, a `.td-swatch--cat-1` and `.td-tag__v` `Core platform`; an epic missing from the backlog shows its id and no swatch; the embedded chrome shows the same tag. `task-page.mock.spec.js` "gates and the epic read as words in both themes": on `#/task/T-102` the gate strip's visible text contains `Spec review passed` and `Plan review passed with warnings` and not `review-gate:pending`; the epic tag reads `Viewer re-skin`; axe `color-contrast` on `[data-test="gate-pipeline"]` and `[data-tag="epic"]` clean in dark and light.
- [ ] **Step 2: Run** the two unit files and the spec — Expected: FAIL.
- [ ] **Step 3: Implement.** Update any `gate-pipeline.test.js` / `task-detail-document.test.js` assertion that read `.gp-word` or the epic tag's `style`.
- [ ] **Step 4: Run** unit suite + `MOCK_PORT=8833 npm --prefix <wt>/viewer run test:mock -- --workers=2 task-page.mock.spec.js task-detail.mock.spec.js` — PASS; style-rules: `screens/task-detail.css` 0 violations.
- [ ] **Step 5: Commit** — `git -C <wt> add viewer/js/components/task-detail-document.js viewer/js/components/gate-pipeline.js viewer/css/screens/task-detail.css viewer/tests/unit/gate-pipeline.test.js viewer/tests/unit/task-detail-document.test.js viewer/tests/task-page.mock.spec.js` — `feat(viewer): a task's epic is a category swatch beside its name, and each gate says its state in a word instead of a raw machine string`

---

### Task 5: Graph view — the page's head, nodes as links, real tabs, no dead controls

**Depends on:** Tasks 2, 4.

**Files:**
- Modify: `viewer/js/components/task-detail-graph.js`, `viewer/css/screens/task-detail.css` (graph block)
- Create: `viewer/tests/unit/task-detail-graph.test.js`
- Test: `viewer/tests/task-page.mock.spec.js`, `viewer/tests/unit/topbar.test.js` (only if it asserts graph controls)

**Interfaces:**
- Consumes: `detailHead`, `detailTitle`, `markdownBody`, `detailGrid` (Task 2); `taskMeta` (Task 2); `statusMeta`, `priorityMeta`, `statusMarker`, `priorityMarker` (`status.js`); `linkRoute` (`link-pills.js`); `computeGraphLayout` (unchanged); `mountTaskTopbar` (Task 3); `stateBlock`.
- Produces: `mountTaskDetailGraph(root, ctx) → cleanup` (signature unchanged). Behaviour (each line is a test):
  1. **Head:** `detailHead({ meta: taskMeta(task, { timers }), title: detailTitle(task.title), after: [markers] })` where `markers` is `div.td-markers[data-test="chips"]` holding `statusMarker('task', status)` and `priorityMarker(priority)` (read-only). The page has exactly one `h1`. `.td-head-block` / `.td-head-title` are gone.
  2. **Nodes:** each neighbour node is an SVG `<a class="node node--link" href="#/task/<id>">` (SVG namespace, `href` attribute) holding a `<title>` = `<id> · <full title> · <status word>`; the centre node is a `<g class="node node--center" role="img" aria-label="This task: <id> · <full title>">`. A click or Enter on a node follows the link (the page's `data-detail-links="follow"` keeps it a navigation). The `onNavigate` click handler is removed.
  3. **Status shape and word on every node:** the circle is replaced by `svgShape(shape, x, y, tone)` drawing the `status.js` shape for the node's status — ring (stroked circle r 3.5), half (stroked circle + filled left half path), triangle, diamond, dot (filled circle r 4), arrow, cross — 8×8px, class `node-shape node-shape--<tone>` (`color: var(--tone-<tone>)`, `fill`/`stroke: currentColor`). The bottom line of every node reads `<status word> · <priority word> · <estimate>` (parts that exist).
  4. **Cut text keeps its words:** a title or step cut to fit ends in `…`; the node's `<title>` holds the uncut text. The graph's local `truncate` is renamed `cut` (it is SVG text, not `lib/text.js`'s DOM helper).
  5. **Legend and axis rail removed** (the words on each node carry status; the column labels name the sides).
  6. **Controls:** "Depth: 2" and "Show all" are removed (they did nothing — X-08). "Hide context" is `button.btn.btn--ghost.btn--sm[aria-pressed]`, rendered only when the context band has content, toggling the band's `hidden`. "Fullscreen" is `button.btn.btn--ghost.btn--sm`, rendered only when `document.fullscreenEnabled`.
  7. **Context band:** issue pills are `a.ctx-pill.issue[href=linkRoute(id)]` showing the id (the `!` glyph span goes); handover pills stay `span.ctx-pill.handover` with the id (no `§`).
  8. **Tabs** are ARIA tabs: `div.td-tabs[role="tablist"][aria-label="Task documents"]`; each `button.td-tab[role="tab"][id][aria-selected][aria-controls][tabindex="0|-1"]`; each `div.td-tab-panel[role="tabpanel"][aria-labelledby][tabindex="0"]` with `hidden` when not selected. ArrowLeft/ArrowRight (wrapping), Home and End move focus and select. Labels: Spec, Plan, Notes, Activity, Anchors, **Raw JSON** (it is `JSON.stringify`). An empty panel is one `.td-empty` line.
  9. **Empty graph:** unchanged state block ("No dependencies to draw"), inside the bordered recess.
  10. **Accessible frame:** the SVG has `role="group"` and `aria-label="Dependencies and unblocks of <id>"`; guides, edges and column labels are `aria-hidden="true"`.
- CSS (tokens only): `.node-id` and `.node-meta` use `--size-technical-small` (12px; 11px stays only on the uppercase `.col-label`); `.node-shape--neutral|accent|warning|critical|success|orange { color: var(--tone-*) }`; `.td-graph-svg a.node:focus-visible .node-rect { stroke: var(--border-focus); stroke-width: 2; }` (an SVG `a` takes no CSS outline offset: the ring is the rect's stroke) and `.td-graph-svg a.node:hover .node-rect { fill: var(--card-bg-hover); }`; `.td-tab[aria-selected="true"]` replaces `.td-tab.on`; `.td-tab-panel[hidden] { display: none; }`; delete `.td-graph-rail*`, `.status-dot*`, `.td-graph-controls .gc-btn*`, `.td-page-B .td-head-block`, `.td-page-B .td-head-title`. At ≤768px `.td-tab` and the graph buttons are ≥44px tall.

- [ ] **Step 1: Unit tests** — `task-detail-graph.test.js` (jsdom; ctx with `task` = a copy of the `DETAIL_TASK` shape, `related` with two dependencies and one unblock, stub `store`/`api`; stub `document.getElementById` slots `#topbar-actions`, `#topbar-primary`, `#topbar-count` in the jsdom body): lines 1 (one `h1`, `.td-markers` has two `.marker`), 2 (neighbour count of `a.node[href]` = 3 with `href="#/task/T-101"` etc.; centre `g.node--center[role="img"]`; each `a.node` has a `title` child containing the full title), 3 (a `done` neighbour has `.node-shape--success`; node text contains `Done`), 4 (a 60-character title: the visible `text.node-title` ends in `…`, `<title>` holds all 60), 6 (no `[data-id="depth"]`, no `[data-id="show-all"]`, every graph control is a `.btn`), 7 (`a.ctx-pill.issue[href="#/issue/ISS-012"]`), 8 (tablist; ArrowRight on the first tab selects Plan and focuses it; End selects Raw JSON; its panel holds a `pre`; ArrowRight from Raw JSON wraps to Spec), 10.
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/task-detail-graph.test.js` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Mocked tests** — `task-page.mock.spec.js`, on `#/task/T-102?view=B`:
  - "the graph walks by keyboard: nodes are links and the tabs move by arrow keys": Tab from the `h1`'s copy button onwards reaches `a.node[href="#/task/T-101"]` (assert by `document.activeElement.getAttribute('href')`); Enter → `location.hash === '#/task/T-101'` and the page shows T-101 (mock `/api/task/T-101/detail` = `taskDetail(DONE_TASK)`); go back to T-102 in Graph; focus the "Spec" tab, ArrowRight → "Plan" is selected and focused, its panel visible.
  - "the graph at 390 scrolls nothing sideways and every cut label keeps its full text" (Review Focus 4): mock T-105 with `LONG_TASK` / `LONG_RELATED` at 390×844 on `#/task/T-105?view=B`: `document.documentElement.scrollWidth <= innerWidth`; every `text.node-title` whose text ends with `…` has a sibling `title` whose text is longer.
  - "axe (dark|light): the graph view has no contrast, nested-interactive or name violation": axe on `#screen-mount` with `runOnly: ['color-contrast', 'nested-interactive', 'link-name', 'svg-img-alt', 'aria-allowed-role', 'aria-required-children']` — zero, both themes.
- [ ] **Step 5: Run** unit suite + `MOCK_PORT=8833 npm --prefix <wt>/viewer run test:mock -- --workers=2 task-page.mock.spec.js task-missing.mock.spec.js` — PASS; `screens/task-detail.css` 0 violations.
- [ ] **Step 6: Commit** — `git -C <wt> add viewer/js/components/task-detail-graph.js viewer/css/screens/task-detail.css viewer/tests/unit/task-detail-graph.test.js viewer/tests/task-page.mock.spec.js` — `feat(viewer): the graph view keeps the page's head and h1, its nodes are links with a shape and a word for their status, its tabs are real tabs, and its dead controls are gone`

---

### Task 6: The conflict banner speaks in option words

From the carry list (2b Task 12 re-review): on the detail page a lost race on Status shows `in-progress`, not "In progress".

**Depends on:** nothing in this plan.

**Files (all outside 3c's ownership table — shared edit components; see the final report):**
- Modify: `viewer/js/components/edit/conflict-banner.js`, `viewer/js/components/edit/inline-field.js` (one argument), `viewer/js/components/edit/task-actions.js` (one argument)
- Test: `viewer/tests/unit/conflict-banner.test.js`, `viewer/tests/task-page.mock.spec.js`

**Interfaces:**
- Consumes: field specs with `options: [{ value, label }]` (`taskSchema` status, priority, epic, phase).
- Produces (`conflict-banner.js`):
  ```js
  export function optionText(spec) → (v) => string
    // the label of the option whose value is v (spec.options read at call time: some are getters); conflictValueText(v)
    // for a value no option has, for an array (each item mapped, joined ', '), and when spec has no options
  showFieldConflict({ …unchanged, text = conflictValueText })   // text(v) → string, used for both sides
  showFullConflict({ …unchanged, texts = {} })                  // { [key]: (v) → string }; a key without one uses conflictValueText
  ```
  `inline-field.js` passes `text: optionText(fieldSpec)`; `task-actions.js` passes `texts: Object.fromEntries(schema.fields.filter((f) => f.options).map((f) => [f.key, optionText(f)]))`. Existing callers that pass neither keep today's output.
- [ ] **Step 1: Tests.** Unit: `optionText({ options: [{ value: 'in-progress', label: 'In progress' }] })('in-progress') === 'In progress'`; unknown value `'weird'` → `'weird'`; `null` → `'—'`; an options getter is read at call time; `showFieldConflict({ …, localValue: 'done', currentValue: 'in-review', text: optionText(statusSpec) })` → `.cb-val-mine` `Done`, `.cb-val-server` `In review`; `showFullConflict` with `texts.status` maps that row and leaves `title` as text. Mocked (`task-page.mock.spec.js`) "a lost race on Status names both statuses in words": `PATCH /api/tasks/T-102` → `{ status: 409, json: { ok: false, error: 'stale', current: { ...DETAIL_TASK, status: 'in-review' }, current_etag: 't1:fresh' } }`; on the page choose Done in the status picker → `#conflict-banner-host .cb-val-mine` `Done`, `.cb-val-server` `In review`; the banner contains no `in-review`; click "Use server" and expect the banner gone and no second PATCH.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** unit suite + `MOCK_PORT=8833 npm --prefix <wt>/viewer run test:mock -- --workers=2 task-page.mock.spec.js conflict-banner.mock.spec.js task-form.mock.spec.js` — PASS.
- [ ] **Step 5: Commit** — `git -C <wt> add viewer/js/components/edit/conflict-banner.js viewer/js/components/edit/inline-field.js viewer/js/components/edit/task-actions.js viewer/tests/unit/conflict-banner.test.js viewer/tests/task-page.mock.spec.js` — `fix(viewer): the conflict banner shows a choice field's values in the words the form uses, not their stored keys`

---

### Task 7: Issue page on the detail template

**Depends on:** Task 2; **3d Task 2 merged** (`css/screens/detail-pages.css` with the legacy `.issue-detail`, `.id-*`, `.bug-detail*`, `.aging-bar*` rules moved verbatim, linked after `ideas.css`); **3d Task 1 merged**, which brings `severityKey`/`severityMeta`/`severityMarker` (`severityMarker(v)` is `null` for null, empty or whitespace), `components/stale-tag.js` (`staleTag(issue, agingCfg = {}, now = Date.now()) → HTMLSpanElement | null`: `span.marker.marker--warning.stale-tag` reading "stale <n>d", `title` "Open <n> days — past the aging window for its severity", `null` unless the issue is open or investigating and its tier is Stale) and `ISSUE_STATUS` with `STATUS_KINDS.issue` (keys `open`, `investigating`, `fixed`, `wontfix`, `duplicate` — `taskmaster_v3.ISSUE_STATUSES`). 3d owns the labels: this task never restates one, and its tests read a label through `statusMeta('issue', key).label` (e.g. 3d's `Won't fix`). If 3d's merged exports differ in a name or key, or are absent, raise NEEDS_CONTEXT; do not add them here.

**Files:**
- Rewrite: `viewer/js/screens/issue-detail.js`
- Create: `viewer/tests/issue-detail.mock.spec.js`
- Modify: `viewer/css/screens/detail-pages.css` (add the `dp-*` rules below; delete `.issue-detail`, `.aging-bar*` and `.id-crumb*`; if Task 8 has already run, also delete every remaining `.id-*` rule and the file's legacy media block, and add the file to `ENFORCED`)
- Modify: `viewer/tests/unit/detail-pages-css.test.js` (3d Task 2's guard for the moved rules: drop the assertions for `.issue-detail`, `.aging-bar*` and `.id-crumb*`; if Task 8 has already run, rewrite it to assert the file holds no `.issue-detail`, `.id-`, `.bug-detail` or `.aging-bar` selector and is listed in `ENFORCED`)
- Modify (append-only): `viewer/tests/unit/style-rules.test.js` (`ENFORCED` += `screens/detail-pages.css`, only when this task deletes the last legacy rule), `viewer/tests/mock-fixtures.js` (`ISSUE`, `ISSUES`, `LONG_ISSUE`)

**Interfaces:**
- Consumes: Task 2 builders; `statusMarker('issue', …)`, `severityMarker` (3d, `status.js`); `issueDiscovered`, `issueEvidence`; `staleTag` (3d, `components/stale-tag.js`); `linkPillsEl`, `legacyLinksToTyped` (`link-pills.js`); `stateBlock`; `claimTopbar`; `api.getIssues`; `store.getIssues/setIssues/getPrefs`; `prefs.patch`.
- Produces: `mount(root, { params, store, prefs, subpath }) → cleanup`; `meta = { title: 'Issue', icon: '!', sidebarKey: 'issues' }` unchanged. The page (each line is a test):
  1. Root classes `td-doc td-doc--page td-page dp-page dp-page--issue`, removed on cleanup. No `.id-*`, `.issue-detail` or `.id-crumb` element; no "‹ Issues".
  2. **Head:** meta = `copyId({ id, noun: 'issue' })` · `a[href="#/issues"]` "Issues" · `stampEl(issueDiscovered(issue), { prefix: 'discovered' })`; `detailTitle(title)`; marker row `div.td-markers[data-test="chips"]`: `span.td-marker-host[data-field="severity"]` (`span.dp-key` "Severity" + `severityMarker(issue.severity_label ?? issue.severity)`; no severity host when it returns `null`), `span.td-marker-host[data-field="status"]` (`span.dp-key` "Status" + `statusMarker('issue', status || 'open')`), and `staleTag(issue, store.getPrefs()?.issues?.aging ?? {})` wrapped in `span.td-marker-host[data-tag="stale"]`, appended only when it is not `null` (it already returns `null` for a fixed, won't-fix or duplicate issue). No aging bar; `aging-bar.js` is no longer imported.
  3. **Body** (`div.td-body`), each `detailSection` only when it has content, in this order: Evidence (`markdownBody(issueEvidence(issue))`), Reproduction (`ol.dp-steps`, one `li` of text per step; heading "Reproduction · <n> step(s)"), Impact (`markdownBody`), Notes (`markdownBody(issue.summary)`), Location (`ul.dp-paths` of `li > code` per path). With none: one `p.td-empty` "Nothing written for this issue yet." Then `datesList([['Discovered', issueDiscovered(issue)], ['Resolved', issue.resolved]])`.
  4. **Rail:** `railPanel({ name: 'relations', label: 'Relations', children: [railGroup({ name: 'links', label: 'Links', body: linkPillsEl(links) })] })` when `links` (typed, else `legacyLinksToTyped(issue, 'issue')`) is non-empty; otherwise no rail (`td-grid--solo`). No `innerHTML`.
  5. **Lookup:** the issue is looked for in `store.getIssues()`; when absent there (empty cache, or an issue made after it was filled) the page fetches `api.getIssues({ includeResolved: true })` once, stores it and looks again.
  6. **Not found:** `stateBlock({ state: 'missing', label: id, headline: 'Issue not found', hint: 'It may have been resolved and archived, or renamed.', action: { label: 'Open Issues', href: '#/issues' } })`. **Failed fetch:** `stateBlock({ state: 'error', label: id, headline: 'Could not load this issue', hint: 'Something went wrong while loading it. Try again in a moment.', action: { label: 'Try again', onClick } })`. **No id:** `stateBlock({ state: 'empty', label: 'Issue', headline: 'No issue open', hint: 'Pick one from the Issues board.', action: { label: 'Open Issues', href: '#/issues' } })`. Never `e.message`.
  7. `prefs.patch({ ui: { last_issue_id: id } })` runs only after the issue is found and painted.
  8. `claimTopbar()` on every outcome; no primary action.
- `detail-pages.css` (tokens only; header = user intent): `.dp-key` visually hidden (`position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap;`); `.dp-steps, .dp-paths { margin: 0; padding-left: var(--space-lg); display: flex; flex-direction: column; gap: var(--space-xs); color: var(--foreground-default); font-size: var(--size-narrator-small); line-height: var(--leading-body); }`; `.dp-paths { padding-left: 0; list-style: none; }`; `.dp-paths code { font-family: var(--font-technical); font-weight: var(--font-technical-weight); font-size: var(--size-technical-small); overflow-wrap: anywhere; }`; `.dp-page .td-title { word-break: break-word; }`.
- Fixtures (`mock-fixtures.js`, appended): `ISSUE = { id: 'ISS-012', title: 'Card edge vanishes on the light page ground', severity: 'P1', severity_label: 'High', status: 'investigating', discovered: '2026-08-01T09:00:00Z', evidence: 'Seen on **three** laptops in light theme.', repro: ['Open the board in light', 'Look at a card edge'], impact: 'Cards blur into the column; `--card-bg` sits too close to `--col-bg`.', summary: '## Notes\n\nTracked in T-102.', location: ['viewer/css/screens/kanban.css:87', 'viewer/css/tokens.css'], links: [{ type: 'relates_to', target: 'T-102' }, { type: 'duplicate_of', target: 'ISS-009' }] }`; `ISSUES = { issues: [ISSUE, { id: 'ISS-009', title: 'Light card edge', severity: 'P2', status: 'fixed', discovered: '2026-07-01T09:00:00Z', resolved: '2026-07-10T09:00:00Z' }] }`; `LONG_ISSUE = { ...ISSUE, id: 'ISS-1234', title: 'x'.repeat(120), location: ['viewer/' + 'deeply/nested/'.repeat(14) + 'file.css:1'] }`.

- [ ] **Step 1: Mocked tests** — `issue-detail.mock.spec.js` (mocks: `/api/board` = `BOARD`, `/api/issues` = `ISSUES` unless a test says otherwise, prefs with `issues: { aging: { High: 30 } }` so ISSUE is stale; record `pageerror`):
  - "the issue reads as the detail template": on `#/issue/ISS-012`: one `h1` with the title; meta contains `Issues` link and `discovered`; `[data-field="severity"] .marker__word` `High`, `[data-field="status"] .marker__word` `Investigating`; `[data-tag="stale"]` visible with text matching `/stale\s*\d+d/i` (3d's tag wording); sections in order `evidence, repro, impact, notes, location` (by `data-section`); Evidence contains a `strong`; Location shows both paths in `code`; rail `getByRole('complementary', { name: 'Related' })` holds `a.link-pill[href="#/task/T-102"]` and `a.link-pill[href="#/issue/ISS-009"]`; no `.id-crumb`, no text `‹`.
  - "a fixed issue shows no stale tag and its resolved date": `#/issue/ISS-009` → no `[data-tag="stale"]`, `[data-test="dates"]` has `Resolved`.
  - "ISS-999 is not found in words, and a failed load says so without the server's text" (Review Focus 2): `#/issue/ISS-999` → `.tm-empty[data-state="missing"]`, label `ISS-999`, headline `Issue not found`, a link `Open Issues`; no `PUT /api/viewer/prefs` body contains `ISS-999`. Then with `/api/issues` → 500 `{ error: 'Traceback: KeyError severity' }` on a fresh page `#/issue/ISS-012` → `.tm-empty[data-state="error"]`, no `Traceback`, `500`, `/api` on the page; route `/api/issues` to `ISSUES` and press "Try again" → the `h1` shows.
  - "an issue made after the list was cached is still found": mock `/api/issues` as `{ issues: [ISSUES.issues[1]] }`; `goto('/#/issue/ISS-009')` (this fills the store with that one issue); re-route `/api/issues` to `ISSUES`; `location.hash = '#/issue/ISS-012'` → the `h1` shows ISSUE's title, and exactly one more `GET /api/issues` was made.
  - "walks by keyboard": Tab from the start of `#screen-mount` visits, in order, the copy-id button, the Issues link, then the two rail links; no other stop.
  - "a long title and a long path stay inside 390px" (Review Focus 4): `LONG_ISSUE` at 390×844 → `scrollWidth <= innerWidth`; the `h1` box right edge ≤ `innerWidth`; the meta line's id text is `ISS-1234` on one line (its `getClientRects().length === 1`).
  - "axe (dark|light): the issue page has no violations": axe on `#screen-mount`, tags `wcag2a, wcag2aa` — zero in both themes.
- [ ] **Step 2: Run** `MOCK_PORT=8833 npm --prefix <wt>/viewer run test:mock -- --workers=2 issue-detail.mock.spec.js` — Expected: FAIL.
- [ ] **Step 3: Implement** the page, the `dp-*` rules and the legacy-rule deletions in `detail-pages.css`, the `detail-pages-css.test.js` update, `ENFORCED` (when this task runs second), the fixtures. No stylesheet link: 3d Task 2 added it.
- [ ] **Step 4: Run** unit suite (style-rules: `screens/detail-pages.css` 0 violations once enforced; until then the report-only count drops) + `MOCK_PORT=8833 npm --prefix <wt>/viewer run test:mock -- --workers=2 issue-detail.mock.spec.js router.mock.spec.js shell.mock.spec.js` — PASS. `grep -n "innerHTML\|id-crumb\|id-title" viewer/js/screens/issue-detail.js` → no match.
- [ ] **Step 5: Commit** — `git -C <wt> add viewer/js/screens/issue-detail.js viewer/css/screens/detail-pages.css viewer/tests/unit/detail-pages-css.test.js viewer/tests/issue-detail.mock.spec.js viewer/tests/mock-fixtures.js` (plus `viewer/tests/unit/style-rules.test.js` when this task enforced the file) — `feat(viewer): the issue page is the detail template — severity and status as markers, a stale tag instead of a bar, rendered sections, links in the rail, and missing or failed loads said in words`

---

### Task 8: Bug page on the detail template (read view)

**Depends on:** Task 2; **3d Task 1 merged** (`severityMarker`) and **3d Task 2 merged** (`detail-pages.css` holding the legacy rules). Task 9 adds the actions.

**Files:**
- Rewrite: `viewer/js/screens/bug-detail.js`
- Modify: `viewer/css/screens/detail-pages.css` (add Task 7's `dp-*` rules if Task 7 has not run yet; delete every `.bug-detail*` rule and its part of the legacy media block; if Task 7 has already run, also delete every remaining `.id-*` rule and add the file to `ENFORCED`), `viewer/tests/mock-fixtures.js` (append `BUG`, `BUG_FIXED`, `LONG_BUG`)
- Modify: `viewer/tests/unit/detail-pages-css.test.js` (drop the assertions for `.bug-detail*`; if Task 7 has already run, rewrite it to assert the file holds no `.issue-detail`, `.id-`, `.bug-detail` or `.aging-bar` selector and is listed in `ENFORCED`)
- Create: `viewer/tests/bug-detail.mock.spec.js`

**Interfaces:**
- Consumes: Task 2 builders; `statusMarker('bug', …)`, `statusMeta`; `severityMarker` (3d, `status.js`); `linkRoute`; `stateBlock`; `claimTopbar`, `claimTopbarPrimary`; `api.getBug`; `store.getBacklog()`.
- Produces: `mount(root, { params, subpath, store, prefs }) → cleanup`; internal `render(bug)` reused by Task 9 after an action; `meta` unchanged. The page (each line is a test):
  1. Root classes `td-doc td-doc--page td-page dp-page dp-page--bug`; no `.id-*`, `.bug-detail*`, "‹ Bugs".
  2. **Head:** meta = `copyId({ id, noun: 'bug' })` · `a[href="#/bugs"]` "Bugs" · `span` "found in " + `a[href=linkRoute(found_in)]` (when `found_in`) · `stampEl(bug.discovered, { prefix: 'discovered' })` · `span` "reported by <discovered_by>" (when set); `detailTitle(title)`; marker row: `[data-field="status"]` (`.dp-key` "Status" + `statusMarker('bug', status)`), `[data-field="severity"]` only when `severityMarker(bug.severity)` is not `null` (`.dp-key` "Severity" + that marker; no default "Medium" for a bug without one), `detailTag('components', 'Components', components.join(', '))` when non-empty, `copyButton` tag for `fix_commit` (`label: 'Copy fix commit <value>'`, class `td-tag`, `data-tag="fix_commit"`), `detailTag('archived', 'Archived', 'yes')` when `archived`.
  3. **Body:** Summary (`detailSection({ key: 'summary', label: 'Summary', body: markdownBody(bug.summary) })`) and Location (`ul.dp-paths`), each only with content; none → `p.td-empty` "Nothing written for this bug yet."; then `datesList([['Discovered', bug.discovered]])`.
  4. **Rail:** `railPanel({ name: 'relations', label: 'Relations' })` with groups, each only when set: "Found in", "Adopted into" — `ul.td-dep-list > li > a.td-dep[href]` with `span.td-dep__id`, `span.td-dep__title` (the board task's title when the board has it) and `statusMarker('task', status)` (when known) — and "Promoted to" — `a.link-pill[href=linkRoute(promoted_to)]` with `span.link-pill__label` "Issue" and `span.link-pill__id`.
  5. **States:** not found (`e.code === 404`): `stateBlock({ state: 'missing', label: id, headline: 'Bug not found', hint: 'It may have been archived or renamed.', action: { label: 'Open Bugs', href: '#/bugs' } })`; failed: `stateBlock({ state: 'error', label: id, headline: 'Could not load this bug', hint: 'Something went wrong while loading it. Try again in a moment.', action: { label: 'Try again', onClick } })`; no id: `stateBlock({ state: 'empty', label: 'Bug', headline: 'No bug open', hint: 'Pick one from the Bugs list.', action: { label: 'Open Bugs', href: '#/bugs' } })`. A non-object answer (an array, `null`) is treated as not found. No action control exists in any of these states.
  6. `claimTopbar()` on every outcome. (Task 9 adds the row-1 primary.)
- Fixtures (appended): `BUG = { id: 'B-031', title: 'Card edge vanishes on the light ground', status: 'open', severity: 'P2', found_in: 'T-102', discovered: '2026-09-30T10:00:00Z', discovered_by: 'user', components: ['viewer'], location: ['viewer/css/screens/kanban.css:87'], summary: 'The card border uses `--border-subtle`.\n\n1. Light theme\n2. Laptop screen' }`; `BUG_FIXED = { ...BUG, id: 'B-030', status: 'fixed', severity: null, fix_commit: 'abfb1b9c0ffee', adopted_into: 'T-101', promoted_to: 'ISS-012' }`; `LONG_BUG = { ...BUG, id: 'B-1234', title: 'y'.repeat(120), location: ['viewer/' + 'deeply/nested/'.repeat(14) + 'file.css:1'] }`.

- [ ] **Step 1: Mocked tests** — `bug-detail.mock.spec.js` (mocks: `/api/board` = `BOARD`, `/api/bugs/B-031` = `BUG`, `/api/bugs/B-030` = `BUG_FIXED`, `/api/bugs/B-1234` = `LONG_BUG`, `/api/bugs/B-999` → `{ status: 404, json: { ok: false, error: 'unknown bug B-999' } }`; record `pageerror` and `page.on('dialog')`, assert neither fired):
  - "the bug reads as the detail template, with its summary and location": `#/bug/B-031` → `h1` title; status `Open`, severity `Medium`; `[data-section="summary"]` holds a `code` and an `ol`; Location `code` text `viewer/css/screens/kanban.css:87`; meta link `T-102` → `#/task/T-102`; rail "Found in" row reads `T-102` and `Re-skin the Kanban cards and columns`.
  - "a fixed bug shows its commit, where it went, and no severity it never had": `#/bug/B-030` → status `Fixed`; no `[data-field="severity"]`; `[data-tag="fix_commit"]` with `abfb1b9c0ffee`; rail groups "Found in", "Adopted into", "Promoted to" with `a[href="#/issue/ISS-012"]`.
  - "B-999 is not found in words; a 500 says so without the server's text; no action is offered without a record" (Review Focus 2): B-999 → `.tm-empty[data-state="missing"]`, label `B-999`, headline `Bug not found`, link `Open Bugs`; `#topbar-primary` empty; no `button` inside `#screen-mount` (the one way on is the `.tm-empty` link). Then `/api/bugs/B-031` → 500 `{ error: 'Traceback: KeyError found_in' }` → `data-state="error"`, no `Traceback`/`500`/`/api`; route back to `BUG`, "Try again" → `h1` shows.
  - "a bug opened after a missing one starts clean": `#/bug/B-999` then `location.hash = '#/bug/B-031'` → `h1` shows, one `.td-doc`, no `.tm-empty`.
  - "walks by keyboard": Tab stops in order: copy id, Bugs, T-102 (meta), the fix-commit copy (B-030 only), rail links.
  - "a long title and a long path stay inside 390px" (Review Focus 4): `#/bug/B-1234` at 390×844 → `scrollWidth <= innerWidth`; id text on one line.
  - "axe (dark|light): the bug page has no violations" on `#screen-mount` for B-031 and B-030 — zero in both themes.
- [ ] **Step 2: Run** `MOCK_PORT=8833 npm --prefix <wt>/viewer run test:mock -- --workers=2 bug-detail.mock.spec.js` — Expected: FAIL.
- [ ] **Step 3: Implement.** The old action bar is not carried over in this task (Task 9 brings the actions back as forms); `prompt`, `confirm`, `alert`, `escapeHtml` and `mkBtn` go.
- [ ] **Step 4: Run** unit suite + `MOCK_PORT=8833 npm --prefix <wt>/viewer run test:mock -- --workers=2 bug-detail.mock.spec.js router.mock.spec.js` — PASS; `grep -nE "prompt\(|confirm\(|alert\(|innerHTML|id-crumb|bug-detail__" viewer/js/screens/bug-detail.js` → no match.
- [ ] **Step 5: Commit** — `git -C <wt> add viewer/js/screens/bug-detail.js viewer/css/screens/detail-pages.css viewer/tests/unit/detail-pages-css.test.js viewer/tests/bug-detail.mock.spec.js viewer/tests/mock-fixtures.js` (plus `viewer/tests/unit/style-rules.test.js` when this task enforced the file) — `feat(viewer): the bug page is the detail template — its summary and location finally shown, status and severity as markers, relations in the rail, and missing or failed loads said in words`

---

### Task 9: Bug actions as in-app forms

**Depends on:** Task 8; **3a Task 2 merged** (topbar row 1 at phone width — "Mark fixed" is the row-1 primary; index wait "3c T3/T9 ← 3a T2").

**Files:**
- Create: `viewer/js/components/edit/bug-actions.js`, `viewer/tests/unit/bug-actions.test.js`
- Modify: `viewer/js/screens/bug-detail.js`, `viewer/css/screens/detail-pages.css` (`.dp-actions`)
- Modify (outside 3c's ownership table — see the final report): `viewer/js/components/edit/entity-modal.js` (`title`, `eyebrow`, `saveLabel` options), `viewer/js/api.js` (`updateBug`, `promoteBugs` through `http()`)
- Test: `viewer/tests/unit/entity-modal.test.js`, `viewer/tests/unit/api.test.js`, `viewer/tests/bug-detail.mock.spec.js`

**Interfaces:**
- Consumes: `openEntityModal`, `confirmDialog`, `describeWriteError`, `TextField`, `EnumSelect`, `MdField`, `severityMeta` (3d; option labels Critical/High/Medium/Low for P0–P3), `topModal`, `openModalCount`.
- Produces:
  ```js
  // entity-modal.js — three optional options; omitted, today's behaviour is unchanged
  openEntityModal({ schema, mode, initialEntity, onSave, onCancel, onClose,
                    title,       // dialog title; default `${create ? 'Create' : 'Edit'} ${noun}`
                    eyebrow,     // default create ? '' : initial.id
                    saveLabel }) // Save button text, restored after "Saving…"; default 'Save'
  // api.js — named exports and on the `api` object; errors now carry `code` and `reason` like every http() write
  export const updateBug = (bugId, patch) => http('POST', `/api/bugs/${encodeURIComponent(bugId)}`, patch);
  export const promoteBugs = ({ bug_ids, title, severity, evidence_text, components, body }) =>
    http('POST', '/api/bugs/promote', { bug_ids, title, severity, evidence_text, components, body });
  // edit/bug-actions.js
  export function openMarkFixed({ bug, onDone, onClose })            // fix_commit: TextField, required, maxLength 200
  export function openAdopt({ bug, getBacklog, onDone, onClose })    // adopted_into: TextField, required; validate:
                                                                     //   a task id on the board, else "No task <v> on the board"
  export function openPromote({ bug, onDone, onClose })              // title (TextField, required, 140, prefilled),
                                                                     // severity (EnumSelect P0–P3 labelled Critical/High/
                                                                     // Medium/Low, required, prefilled bug.severity ?? 'P1'),
                                                                     // evidence (MdField, required, label "Evidence — why it
                                                                     // is recurring, systemic or outstanding"); onDone(issueId)
  export async function shelveBug({ bug }) → { error } | { cancelled: true } | undefined   // undefined = shelved
  ```
  Each `open*` opens `openEntityModal({ mode: 'create', title, eyebrow: bug.id, saveLabel, … })` — titles/save labels: "Mark fixed"/"Mark fixed", "Adopt into a task"/"Adopt", "Promote to an issue"/"Promote". `onSave` calls the API and answers `{ error: describeWriteError(e, { noun: 'bug' }) }` on a throw, else calls `onDone`. `shelveBug` asks `confirmDialog({ title: \`Shelve ${bug.id}?\`, message: 'It leaves the open list. It can still be marked fixed, adopted or promoted later.', confirmLabel: 'Shelve', cancelLabel: 'Keep open' })`.
- Bug page behaviour (each line is a test):
  1. For `open` and `shelved` bugs, `claimTopbarPrimary()` holds `tmAction({ icon: 'check', label: 'Mark fixed', variant: 'primary', title: 'Mark this bug fixed' })`; the body holds `div.dp-actions[role="group"][aria-label="Bug actions"]` with `button.btn.btn--secondary` "Shelve" (open only), "Adopt into task", "Promote to issue", and `div.dp-actions__message[role="alert"]` for a refused shelve. Other statuses: no primary, no action row.
  2. After a successful fix, adopt or shelve the page re-reads the bug and paints it (status marker updated, actions gone); a promote navigates to `#/issue/<issue_id>`.
  3. A refused write is words in the form footer (or, for Shelve, in `.dp-actions__message`); never a status code, `/api` or `{`.
  4. No `window.prompt`, `window.confirm` or `window.alert` anywhere in `bug-detail.js` or `bug-actions.js`.
  5. Leaving (Review Focus 5): dispose → if `openModalCount() > 0`, `topModal().requestClose()` once; a form still open after that keeps working and its `onDone` paints nothing on a disposed page.
- `.dp-actions { display: flex; flex-wrap: wrap; gap: var(--space-xs); }` `.dp-actions__message:empty { display: none; }` `.dp-actions__message { flex: 1 0 100%; color: var(--foreground-bold); }` (the message is words, the hue stays out of text); at ≤768px `.dp-actions .btn { flex: 1 1 100%; min-height: 44px; }`.

- [ ] **Step 1: Unit tests.** `entity-modal.test.js`: `openEntityModal({ …, mode: 'create', title: 'Mark fixed', eyebrow: 'B-031', saveLabel: 'Mark fixed' })` → `.modal-title` `Mark fixed`, `.modal-eyebrow` `B-031`, `[data-save]` text `Mark fixed` before and after a save that answers `{ error }`; omitted → `Create <noun>` and `Save` as before. `api.test.js`: `api.updateBug('B-1', { status: 'fixed' })` against a 400 `{ ok: false, error: 'Error: status=fixed requires fix_commit to be set' }` rejects with `code === 400` and `reason` that string; a 200 resolves to the JSON; `promoteBugs` sends `POST /api/bugs/promote` and a 201 `{ ok: true, issue_id: 'ISS-030' }` resolves to it. `bug-actions.test.js` (jsdom set up like `entity-modal.test.js`; the module calls the imported `updateBug`/`promoteBugs`, so stub `globalThis.fetch` with `withFetch` as `api.test.js:7-12` does and assert on the recorded requests): `openAdopt` with a board holding `T-101` refuses `T-999` with "No task T-999 on the board" and sends nothing; `openMarkFixed` Save is disabled until a commit is typed; `openPromote` prefills the title and severity `P2` from `BUG`; `readFileSync` of `bug-actions.js` and `screens/bug-detail.js` matches no `/\b(prompt|confirm|alert)\(/`.
- [ ] **Step 2: Run** the three unit files — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Mocked tests** (`bug-detail.mock.spec.js`; `page.on('dialog')` asserted never to fire):
  - "Mark fixed asks for the commit in a form and the page shows the bug fixed": `POST /api/bugs/B-031` → `{ ok: true, id: 'B-031', status: 'fixed' }`, and after it `/api/bugs/B-031` answers `{ ...BUG, status: 'fixed', fix_commit: 'abc1234' }`; click row 1 "Mark fixed" → `getByRole('dialog', { name: 'Mark fixed' })`; Save is disabled; type `abc1234`; Save → request body deep-equals `{ status: 'fixed', fix_commit: 'abc1234' }`; dialog gone; status marker `Fixed`; no `.dp-actions`; `#topbar-primary` empty.
  - "a refused fix is said in words in the form": the POST answers 400 `{ ok: false, error: 'Error: status=fixed requires fix_commit to be set' }` → footer `[role="alert"]` contains `requires fix_commit`, not `400`, `/api` or `{`; a 500 → "The server could not save this change. Try again in a moment."
  - "Shelve asks in the app and Keep open sends nothing": click "Shelve" → `alertdialog` "Shelve B-031?"; "Keep open" → no POST recorded; again, "Shelve" → POST body `{ status: 'shelved' }`, the page shows `Shelved` and no "Shelve" button.
  - "Adopt refuses a task that is not on the board, then adopts": type `T-999` → message "No task T-999 on the board", no POST; type `T-101` → POST body `{ status: 'adopted', adopted_into: 'T-101' }`.
  - "Promote opens the new issue": `POST /api/bugs/promote` → 201 `{ ok: true, issue_id: 'ISS-030' }`; `/api/issues` = `{ issues: [{ id: 'ISS-030', title: 'Promoted', severity: 'P1', status: 'open' }] }`; fill Evidence `Recurring: 3 bugs`; Promote → request body has `bug_ids: ['B-031']`, `title: BUG.title`, `severity: 'P2'`, `evidence_text: 'Recurring: 3 bugs'`; `location.hash === '#/issue/ISS-030'`.
  - "leaving the bug page with an action form open: clean closes, typed asks" (Review Focus 5): open "Mark fixed", `location.hash = '#/bugs'` → no `.modal`; back to `#/bug/B-031`, open "Mark fixed", type `abc`, navigate → `alertdialog` "Discard changes?" → "Discard" → no `.modal`, no POST; no page error.
  - "axe (dark|light): the action row and each form are clean": axe on `#screen-mount` and on each open dialog — zero `color-contrast`, `label`, `select-name`, `aria-*`; at 390×844 every `.dp-actions .btn` is ≥44px tall.
- [ ] **Step 5: Run** unit suite + `MOCK_PORT=8833 npm --prefix <wt>/viewer run test:mock -- --workers=2 bug-detail.mock.spec.js task-form.mock.spec.js ideas-form.mock.spec.js` — PASS (the form specs prove the entity-modal options changed nothing by default).
- [ ] **Step 6: Commit** — `git -C <wt> add viewer/js/components/edit/bug-actions.js viewer/tests/unit/bug-actions.test.js viewer/js/screens/bug-detail.js viewer/css/screens/detail-pages.css viewer/js/components/edit/entity-modal.js viewer/js/api.js viewer/tests/unit/entity-modal.test.js viewer/tests/unit/api.test.js viewer/tests/bug-detail.mock.spec.js` — `feat(viewer): bug actions are in-app forms — a required fix commit, a task checked against the board, a promote form that opens the new issue — with Mark fixed in topbar row 1 and refusals in words`

---

### Task 10: Verification

**Depends on:** Tasks 1–9; **3e Task 1 merged** (`2026-10-06-viewer-rr-3e-sessions-archived-dashboard-settings.md`, "The generic right rail": its handover pill now says a failed status change in words beside itself, inside the task page rail).

**Files:**
- Modify (append-only): `viewer/tests/tools/capture-modals.mjs` (scenes and their fixtures; never a live server), `docs/specs/2026-10-01-viewer-reality-reprojection-design.md` (§11 bullets)
- Modify: `viewer/tests/mock-fixtures.js` (append three exported table builders), `viewer/tests/task-page.mock.spec.js`, `viewer/tests/task-missing.mock.spec.js`, `viewer/tests/issue-detail.mock.spec.js`, `viewer/tests/bug-detail.mock.spec.js` (build their base tables from them)

- [ ] **Step 1: Scenes.** Add to `ALL_SCENES`, with the routes they need added to `TABLE` (`/api/issues` = `F.ISSUES`, `/api/bugs/B-031` = `F.BUG`, `/api/bugs/B-030` = `F.BUG_FIXED`, `/api/bugs/B-1234` = `F.LONG_BUG`, `/api/bugs/B-999` 404, `/api/task/T-101/detail` = `F.taskDetail(F.DONE_TASK)`), each `fullPage: true, scope: '#screen-mount'` unless noted:
  - `page-graph` — `#/task/T-102?view=B`; `page-graph-long` — `#/task/T-105?view=B`; `page-missing` — `#/task/NOPE-999` (404 mocked); `page-gates` — `#/task/T-107` (in review, failed gate);
  - `issue-rich` — `#/issue/ISS-012` (prefs `issues.aging.High = 30`); `issue-fixed` — `#/issue/ISS-009`; `issue-long` — `#/issue/ISS-1234` (add `F.LONG_ISSUE` to the issues list); `issue-missing` — `#/issue/ISS-999`;
  - `bug-open` — `#/bug/B-031`; `bug-fixed` — `#/bug/B-030`; `bug-long` — `#/bug/B-1234`; `bug-missing` — `#/bug/B-999`;
  - `bug-mark-fixed` — `#/bug/B-031` with the Mark fixed form open (`scope` default: the dialog); `bug-promote` — the Promote form open; `bug-shelve-confirm` — the Shelve confirm open.
- [ ] **Step 2:** `node <wt>/viewer/tests/tools/capture-modals.mjs C:/Users/gruku/Files/Claude/taskmaster/.worktrees/viewer-rr/.superpowers/sdd/2026-10-06-viewer-rr-3c-detail-pages/shots-task-10 --port=8833` (images are never committed) (all scenes, both themes, both widths). LOOK at every new image and at `page-rich`/`page-empty` in both themes. Check against spec §6 "Detail template": meta line → title → marker row, sections with Technical labels, empty sections collapsed, rail only when non-empty, no "‹ back", segmented control matching the view, graph frame a bordered recess. In `metrics.json` every 3c scene has `overflowX <= 0`, zero axe violations, no page error, no unmocked write, no native dialog. Fix what is wrong inside 3c's files, with a test where one makes sense. Describe each new image plainly in the report and list anything that belongs to another track.
- [ ] **Step 3: Measure** (Review Focus 4) at 390×844 in both themes for `#/task/T-105`, `#/task/T-105?view=B`, `#/issue/ISS-1234`, `#/bug/B-1234`: `document.documentElement.scrollWidth <= innerWidth` and `document.documentElement.scrollHeight` (report the four numbers; none may exceed 20,000px).
- [ ] **Step 3b: Reusable mocks for plan 4's a11y gate** (index constraint "Every screen has a mocked spec plan 4 can reuse"). Append to `mock-fixtures.js`, each returning a fresh `mockApi` table (no `page.route` of its own) and taking `{ theme = 'dark' } = {}` for `/api/viewer/prefs`:
  ```js
  // The task page's routes: T-102 rich (document and ?view=B), T-101 done, T-105 long, NOPE-999 missing.
  export function taskPageMocks({ theme = 'dark' } = {}) → {
    '/api/viewer/prefs': { theme, ui: {}, screens: {} }, 'PUT /api/viewer/prefs': {},
    '/api/board': BOARD, '/api/backlog': BOARD, '/api/bugs': [],
    '/api/task/T-102/detail': taskDetail(DETAIL_TASK, 't1:fixture', RICH_RELATED),
    '/api/task/T-101/detail': taskDetail(DONE_TASK),
    '/api/task/T-105/detail': taskDetail(LONG_TASK, 't1:fixture', LONG_RELATED),
    '/api/task/NOPE-999/detail': { status: 404, json: { ok: false, error: 'unknown task' } },
  }
  // The issue page's routes: ISS-012 stale, ISS-009 fixed, ISS-1234 long; ISS-999 is absent from the list.
  export function issueDetailMocks({ theme = 'dark' } = {}) → {
    '/api/viewer/prefs': { theme, ui: {}, screens: {}, issues: { aging: { High: 30 } } }, 'PUT /api/viewer/prefs': {},
    '/api/board': BOARD, '/api/backlog': BOARD,
    '/api/issues': { issues: [...ISSUES.issues, LONG_ISSUE] },
  }
  // The bug page's routes: B-031 open, B-030 fixed, B-1234 long, B-999 missing.
  export function bugDetailMocks({ theme = 'dark' } = {}) → {
    '/api/viewer/prefs': { theme, ui: {}, screens: {} }, 'PUT /api/viewer/prefs': {},
    '/api/board': BOARD, '/api/backlog': BOARD,
    '/api/bugs/B-031': BUG, '/api/bugs/B-030': BUG_FIXED, '/api/bugs/B-1234': LONG_BUG,
    '/api/bugs/B-999': { status: 404, json: { ok: false, error: 'unknown bug B-999' } },
  }
  ```
  Switch the four specs' base tables to these (a test that needs a different answer spreads the builder and overrides one key). The "content is loaded" selector per route — each names the record's own heading or the not-found block's own state, never anything an error block (`.tm-empty[data-state="error"]`) or the loading block also matches:

  | Route | Builder | Content selector |
  |---|---|---|
  | `#/task/T-102` | `taskPageMocks` | `.td-page-A h1.td-title` |
  | `#/task/T-102?view=B` | `taskPageMocks` | `.td-page-B h1.td-title` |
  | `#/task/NOPE-999` | `taskPageMocks` | `.tm-empty[data-state="missing"]` |
  | `#/issue/ISS-012` | `issueDetailMocks` | `.dp-page--issue h1.td-title` |
  | `#/issue/ISS-999` | `issueDetailMocks` | `.tm-empty[data-state="missing"]` |
  | `#/bug/B-031` | `bugDetailMocks` | `.dp-page--bug h1.td-title` |
  | `#/bug/B-999` | `bugDetailMocks` | `.tm-empty[data-state="missing"]` |

  Each spec waits on its route's selector before asserting. List builder + selector per route in the report, exactly as in this table (or as changed, with the reason).
- [ ] **Step 4: Full runs** — `npm --prefix <wt>/viewer run test:unit`; `MOCK_PORT=8833 npm --prefix <wt>/viewer run test:mock -- --workers=2` (full mocked suite); the server command from Global Constraints. Report exact pass/fail/skip counts for each, and that port 8833 has no listener afterwards (`netstat -ano | grep 8833` empty).
- [ ] **Step 5:** Append to spec §11 one bullet per ruling:
  - **§6 detail template (plan 3c).** Task, issue and bug pages share one set of builders (`components/detail-page.js`) and the `td-*` classes; issue and bug specifics are `dp-*` in `css/screens/detail-pages.css`. The graph view keeps the page's head and `h1`.
  - **§6 graph (plan 3c).** "Depth" and "Show all" are removed, not built (they did nothing); nodes are SVG links with the status shape and word; the tabs are ARIA tabs and the raw tab is "Raw JSON".
  - **§6 issue detail (plan 3c).** Staleness on the detail page is 3d's "stale Nd" tag (`staleTag()`, the one the Issues board shows; IS-04), not a bar.
  - **§6 bug detail (plan 3c).** Bug actions are in-app forms on the shared entity form; "Mark fixed" requires a commit (the server always did) and is the page's primary action in row 1; after an action the page shows the bug's new state, and a promote opens the new issue.
  - **§5.10 gates (plan 3c).** Each gate shows its state as a visible word; the raw `gate_state` string is never printed.
  - **§5.7 leaving a page (plan 3c).** Leaving a detail page with a form open asks the form to close, exactly as leaving the detail modal does.
- [ ] **Step 6: Commit** — `git -C <wt> add viewer/tests/tools/capture-modals.mjs viewer/tests/mock-fixtures.js viewer/tests/task-page.mock.spec.js viewer/tests/task-missing.mock.spec.js viewer/tests/issue-detail.mock.spec.js viewer/tests/bug-detail.mock.spec.js` — `test(viewer): capture the task, issue and bug pages in both themes and widths; their mocked specs build on named table builders plan 4 can reuse`; then `git -C <wt> add docs/specs/2026-10-01-viewer-reality-reprojection-design.md` — `docs(viewer): spec §11 records plan 3c's rulings`
