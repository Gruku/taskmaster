<!-- User intent: executable plan for the first slice of the viewer's Reality Reprojection re-skin — the token/theme/shell foundation and the data bugs — so every later screen pass builds on one verified base. -->

# Viewer × Reality Reprojection — Plan 1: Foundation and Data Fixes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the viewer on Reality Reprojection tokens, fonts and dark/light themes with a rebuilt shell, and fix the four data bugs that make detail screens show wrong or empty content.

**Architecture:** `css/tokens.css` is regenerated from the pinned RR `tokens.json` (primitives + semantic roles, dark on `:root`, light on `[data-theme="light"]`), followed by a hand-written block of viewer tokens and a temporary block that aliases every old variable name to an RR role, so all screens flip palette at once without being edited. A style-rules test ratchets file by file. Data fixes are independent of the CSS work.

**Tech Stack:** Vanilla JS ES modules, plain CSS, Python stdlib HTTP server (`taskmaster/backlog_server.py`), `node --test` + jsdom, Playwright, pytest.

**Spec:** `docs/specs/2026-10-01-viewer-reality-reprojection-design.md` (stages 1 and 3). Audit: `docs/specs/2026-10-01-viewer-audit.md`. RR snapshot: `docs/specs/assets/reality-reprojection-2026-10-01/` (abbreviated `RR/` below).

**Plan series:** 1 (this) → 2 shared components → 3 screens (five parallel tasks) → 4 cleanup. Plans 2–4 are written after this one merges, against the code it produces.

## Global Constraints

- Branch `feat/viewer-reality-reprojection`, cut from `feat/database-native-foundation`. No pushes.
- No `box-shadow` anywhere. No `transform` inside a `:hover` rule. No colored `border-left` (only `var(--border-*)` colors). No `outline: none`.
- Focus ring: `outline: 2px solid var(--border-focus); outline-offset: 2px`.
- Token values come only from `RR/tokens.json`. Never invent a color, size, radius, duration or easing.
- RR shadow and grain tokens are not imported.
- Fonts are local files; no network font requests.
- No text below 11px; 11px only for uppercase section labels. No italic.
- Every new file starts with a 1–3 line `User intent:` header comment.
- The viewer server refuses the taskmaster repo as its root. Run it with `TASKMASTER_ROOT=C:/Users/gruku/Files/Claude/claude-tools`.
- All commands run from the repo root with absolute paths or `--prefix`/`-C`; never `cd &&`.
- Windows: use the repo `.venv` python; never leave a server or browser running after a step.

## Review Focus

1. **Saved theme pref is an unknown value** (e.g. `"blue"`, `null`, missing key): the UI must fall back to the system theme, not render unstyled. → Task 7 unit test.
2. **`localStorage` unavailable** (private mode throws on access): the pre-paint script must not throw and must still set `data-theme`. → Task 7 Playwright test.
3. **Bug id with regex-special or path characters** (`B-1/../x`, `B-1?x=1`, empty id): the server must return 404 or the list, never a 500 or another file. → Task 1 pytest.
4. **Epic stats where `done + archived > total` or all zeros** (stale counters): progress must clamp to 0–100 and never show `NaN%`. → Task 3 unit test.
5. **Old variable names still used by screen CSS after the token swap**: every `var(--x)` must resolve; an unresolved one silently inherits and produces invisible text. → Task 5 test (undefined-var rule enforced for all files from day one).

---

### Task 0: Branch and tooling

**Files:**
- Modify: `viewer/package.json`
- Create: `viewer/tests/playwright.mock.config.js`, `viewer/tests/mock-api.js`

**Interfaces:**
- Produces: `mockApi(page, table)` from `viewer/tests/mock-api.js`; npm script `test:mock`; mocked specs are named `*.mock.spec.js`.

- [ ] **Step 1: Create the branch**

```bash
git -C C:/Users/gruku/Files/Claude/taskmaster switch -c feat/viewer-reality-reprojection
```

- [ ] **Step 2: Install viewer dev dependencies**

```bash
npm --prefix C:/Users/gruku/Files/Claude/taskmaster/viewer install
npm --prefix C:/Users/gruku/Files/Claude/taskmaster/viewer install --save-dev axe-core
npm --prefix C:/Users/gruku/Files/Claude/taskmaster/viewer exec playwright install chromium
```

Expected: `viewer/node_modules/@playwright/test` exists; `package.json` devDependencies gains `axe-core`.

- [ ] **Step 3: Baseline the existing unit suite**

Run: `npm --prefix C:/Users/gruku/Files/Claude/taskmaster/viewer run test:unit`
Expected: all pass. Record the count; later tasks must not reduce it.

- [ ] **Step 4: Write the mocked-API helper**

`viewer/tests/mock-api.js`:

```js
// User intent: one place to mock the viewer's API so UI specs never touch a live backlog or a fixed port.
// `table` maps a pathname to a JSON value, or to { status, json } for non-200 replies.
export async function mockApi(page, table = {}) {
  const base = {
    '/api/identity': { version: '0.0.0-test' },
    '/api/viewer/prefs': { theme: 'system', ui: {}, screens: {} },
  };
  const merged = { ...base, ...table };
  await page.route('**/api/**', (route) => {
    const { pathname } = new URL(route.request().url());
    const hit = merged[pathname];
    if (hit && typeof hit === 'object' && 'status' in hit && 'json' in hit) {
      return route.fulfill({ status: hit.status, json: hit.json });
    }
    return route.fulfill({ json: hit === undefined ? {} : hit });
  });
}
```

- [ ] **Step 5: Write the mocked Playwright config**

`viewer/tests/playwright.mock.config.js`:

```js
// User intent: run UI specs against the static viewer with every API call mocked — no live server, no shared port.
import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';

const viewerDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const PORT = Number(process.env.MOCK_PORT || 8792);

export default defineConfig({
  testDir: '.',
  testMatch: /\.mock\.spec\.js$/,
  timeout: 15_000,
  retries: 0,
  use: { baseURL: `http://127.0.0.1:${PORT}`, headless: true },
  webServer: {
    command: `python -m http.server ${PORT} --bind 127.0.0.1`,
    cwd: viewerDir,
    url: `http://127.0.0.1:${PORT}/index.html`,
    reuseExistingServer: false,
    timeout: 20_000,
  },
});
```

The catch-all answers unknown endpoints with `{}`. If a screen throws on that shape, add the smallest valid fixture for its endpoint to `base` in `mock-api.js` (read the endpoint's real shape from `viewer/js/api.js` and `viewer/js/store.js`) rather than loosening the screen.

Add to `viewer/package.json` scripts: `"test:mock": "playwright test --config tests/playwright.mock.config.js"`.

- [ ] **Step 6: Commit**

```bash
git -C C:/Users/gruku/Files/Claude/taskmaster add viewer/package.json viewer/package-lock.json viewer/tests/mock-api.js viewer/tests/playwright.mock.config.js
git -C C:/Users/gruku/Files/Claude/taskmaster commit -m "test(viewer): mocked-API Playwright config and helper"
```

---

### Task 1: `GET /api/bugs/<id>` returns one bug (BD-01)

**Files:**
- Modify: `taskmaster/backlog_server.py` (the `elif clean_path.startswith("/api/bugs"):` branch, ~line 10720)
- Modify: `viewer/js/screens/bug-detail.js:44-66`
- Test: `tests/test_server_bugs.py`

**Interfaces:**
- Produces: `GET /api/bugs/<id>` → `200 {id, title, status, …, summary}` or `404 {"ok": false, "error": "unknown bug <id>"}`. `GET /api/bugs` unchanged.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_server_bugs.py`)

```python
import urllib.error


def _status(url: str) -> int:
    try:
        with urllib.request.urlopen(url) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_bug_get_single_returns_one_object(running_server, tmp_path):
    base, _ = running_server
    a = _post(f"{base}/api/bugs", {"title": "alpha", "discovered_by": "user"})
    _post(f"{base}/api/bugs", {"title": "beta", "discovered_by": "user"})
    one = _get(f"{base}/api/bugs/{a['id']}")
    assert isinstance(one, dict)
    assert one["id"] == a["id"]
    assert one["title"] == "alpha"
    assert "summary" in one


def test_bug_get_single_unknown_is_404(running_server, tmp_path):
    base, _ = running_server
    _post(f"{base}/api/bugs", {"title": "alpha", "discovered_by": "user"})
    assert _status(f"{base}/api/bugs/B-999") == 404


def test_bug_get_single_finds_archived(running_server, tmp_path):
    base, _ = running_server
    b = _post(f"{base}/api/bugs", {"title": "old", "discovered_by": "user"})
    _post(f"{base}/api/bugs/{b['id']}", {"status": "fixed", "fix_commit": "abc"})
    _post(f"{base}/api/bugs/{b['id']}/archive", {})
    assert _get(f"{base}/api/bugs/{b['id']}")["id"] == b["id"]


@pytest.mark.parametrize("tail", ["B-1%2F..%2Fx", "..", "B-1/extra"])
def test_bug_get_single_rejects_odd_ids(running_server, tmp_path, tail):
    base, _ = running_server
    assert _status(f"{base}/api/bugs/{tail}") == 404


def test_bug_list_still_a_list(running_server, tmp_path):
    base, _ = running_server
    _post(f"{base}/api/bugs", {"title": "alpha", "discovered_by": "user"})
    assert isinstance(_get(f"{base}/api/bugs"), list)
    assert isinstance(_get(f"{base}/api/bugs?status=open"), list)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_server_bugs.py -k "single or still_a_list" -v`
Expected: `single_returns_one_object`, `unknown_is_404`, `finds_archived`, `rejects_odd_ids` FAIL (response is a list / status 200); `still_a_list` passes.

- [ ] **Step 3: Implement**

Replace the line `elif clean_path.startswith("/api/bugs"):` with a single-bug branch followed by an exact-match list branch. The list branch body stays as it is.

```python
        elif clean_path.startswith("/api/bugs/"):
            bug_id = clean_path[len("/api/bugs/"):]
            found = None
            snapshot = self._snapshot()
            if snapshot is not None and re.fullmatch(r"[A-Za-z0-9_\-]+", bug_id):
                data, etag = snapshot
                for bid, fm, body in _dict_rows(data, "bug", include_archived=True):
                    if bid == bug_id:
                        found = {k: v for k, v in fm.items() if k != "_body"}
                        found["summary"] = (body or "").strip()
                        break
            if found is None:
                self._send_json(404, {"ok": False, "error": f"unknown bug {bug_id}"})
                return
            self._send_json(200, found, etag=etag)
            return
        elif clean_path == "/api/bugs":
```

Before writing, confirm with `grep -n "/api/bugs" viewer/js/api.js` that `getBug` is the only GET under `/api/bugs/`.

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_server_bugs.py -v`
Expected: all PASS.

- [ ] **Step 5: Make the screen treat 404 as not-found**

In `viewer/js/screens/bug-detail.js`, the `catch (e)` block currently prints `Could not load bug`. Read `http()` in `viewer/js/api.js` to see how it reports a non-2xx status (thrown error message contains `→ 404`). Change the catch to:

```js
  } catch (e) {
    if (/\b404\b/.test(String(e?.message))) {
      bug = null;
    } else {
      const empty = document.createElement('div');
      empty.className = 'id-empty';
      empty.textContent = `Could not load bug ${id}.`;
      root.appendChild(empty);
      claimTopbar();
      return () => { root.classList.remove('bug-detail'); };
    }
  }
```

The existing `if (!bug)` block then renders "Bug … not found" with the Back link and no action buttons. Also add, directly after the fetch, a guard for a non-object reply: `if (Array.isArray(bug)) bug = null;`.

- [ ] **Step 6: Commit**

```bash
git -C C:/Users/gruku/Files/Claude/taskmaster add taskmaster/backlog_server.py tests/test_server_bugs.py viewer/js/screens/bug-detail.js
git -C C:/Users/gruku/Files/Claude/taskmaster commit -m "fix(viewer): GET /api/bugs/<id> returns one bug or 404; bug detail shows not-found"
```

---

### Task 2: Issue detail reads the fields the API sends (ID-01)

**Files:**
- Create: `viewer/js/util/issue-fields.js`
- Modify: `viewer/js/screens/issue-detail.js:115,142-149,216`
- Test: `viewer/tests/unit/issue-fields.test.js`

**Interfaces:**
- Produces: `issueDiscovered(issue) → string|null`, `issueEvidence(issue) → string`.

- [ ] **Step 1: Write the failing test**

```js
// User intent: pin the issue field names the API really sends, so the detail page can't silently show blanks again.
import test from 'node:test';
import assert from 'node:assert/strict';
import { issueDiscovered, issueEvidence } from '../../js/util/issue-fields.js';

test('reads API names', () => {
  const i = { discovered: '2026-05-01', evidence: 'stack trace' };
  assert.equal(issueDiscovered(i), '2026-05-01');
  assert.equal(issueEvidence(i), 'stack trace');
});
test('falls back to legacy names', () => {
  const i = { created: '2026-04-01', symptom: 'old text' };
  assert.equal(issueDiscovered(i), '2026-04-01');
  assert.equal(issueEvidence(i), 'old text');
});
test('API names win over legacy', () => {
  assert.equal(issueDiscovered({ discovered: 'a', created: 'b' }), 'a');
  assert.equal(issueEvidence({ evidence: 'a', symptom: 'b' }), 'a');
});
test('missing or null issue', () => {
  assert.equal(issueDiscovered(undefined), null);
  assert.equal(issueEvidence(null), '');
  assert.equal(issueEvidence({ evidence: null }), '');
});
```

- [ ] **Step 2: Run** `node --test viewer/tests/unit/issue-fields.test.js` — Expected: FAIL, module not found.

- [ ] **Step 3: Implement** `viewer/js/util/issue-fields.js`

```js
// User intent: single definition of which issue fields hold the discovery date and the evidence text.
export function issueDiscovered(issue) {
  return issue?.discovered ?? issue?.created ?? null;
}
export function issueEvidence(issue) {
  return issue?.evidence ?? issue?.symptom ?? '';
}
```

- [ ] **Step 4: Use it in the screen**

In `viewer/js/screens/issue-detail.js` import both helpers, then:
- line 115: `created.textContent = \`since ${_fmtDate(issueDiscovered(issue))}\`;`
- the Symptom section (lines ~142-149): gate on `issueEvidence(issue)`, set heading text to `Evidence`, body `textContent = issueEvidence(issue)`, and drop the `id-body--italic` class.
- line 216: `['Discovered', _fmtRel(issueDiscovered(issue))],`

- [ ] **Step 5: Run** `npm --prefix viewer run test:unit` — Expected: all PASS.

- [ ] **Step 6: Commit** — `fix(viewer): issue detail reads discovered/evidence`

---

### Task 3: One source for epic progress (ED-01)

**Files:**
- Modify: `viewer/js/lib/epic-format.js`, `viewer/js/components/epic-detail-document.js:42,57-58`, `viewer/js/screens/epics.js:44-55`
- Test: `viewer/tests/unit/epic-format.test.js`

**Interfaces:**
- Produces: `epicProgress(stats) → { total, done, archived, closed, pct, label }`. `progressPercent(stats)` stays exported and returns `epicProgress(stats).pct`.

- [ ] **Step 1: Write the failing tests** (append to `viewer/tests/unit/epic-format.test.js`, adding `epicProgress` to the import)

```js
test('epicProgress — closed = done + archived, label and pct agree', () => {
  const p = epicProgress({ total: 55, done: 25, archived: 10 });
  assert.deepEqual(
    { closed: p.closed, pct: p.pct, label: p.label },
    { closed: 35, pct: 64, label: '35/55 closed · 25 done · 10 archived' },
  );
});
test('epicProgress — no archived part when zero', () => {
  assert.equal(epicProgress({ total: 4, done: 1 }).label, '1/4 closed · 1 done');
});
test('epicProgress — empty and missing stats', () => {
  assert.deepEqual(epicProgress(undefined), { total: 0, done: 0, archived: 0, closed: 0, pct: 0, label: '0/0 closed' });
  assert.equal(epicProgress({ total: 0, done: 3 }).pct, 0);
});
test('epicProgress — clamps stale counters', () => {
  const p = epicProgress({ total: 3, done: 3, archived: 2 });
  assert.equal(p.closed, 3);
  assert.equal(p.pct, 100);
});
test('progressPercent delegates', () => {
  assert.equal(progressPercent({ total: 4, done: 1, archived: 1 }), 50);
});
```

- [ ] **Step 2: Run** `node --test viewer/tests/unit/epic-format.test.js` — Expected: FAIL, `epicProgress` is not exported.

- [ ] **Step 3: Implement** in `viewer/js/lib/epic-format.js` (replace `progressPercent`)

```js
export function epicProgress(stats) {
  const total = Math.max(0, stats?.total || 0);
  const done = Math.max(0, stats?.done || 0);
  const archived = Math.max(0, stats?.archived || 0);
  const closed = Math.min(total, done + archived);
  const pct = total ? Math.round((closed / total) * 100) : 0;
  const parts = [`${closed}/${total} closed`];
  if (total) {
    parts.push(`${done} done`);
    if (archived) parts.push(`${archived} archived`);
  }
  return { total, done, archived, closed, pct, label: parts.join(' · ') };
}

export function progressPercent(stats) {
  return epicProgress(stats).pct;
}
```

- [ ] **Step 4: Use it in both consumers**

`epic-detail-document.js`: `const prog = epicProgress(epic.stats);` — bar width `prog.pct`, label `${prog.label} · ${prog.pct}%`.
`epics.js`: `const prog = epicProgress(stats);` — count cell `${prog.closed}/${prog.total}`, `title="${prog.label}"`, bar width `prog.pct`.

- [ ] **Step 5: Run** `npm --prefix viewer run test:unit` — Expected: all PASS (fix any existing epic test that asserted the old `done/total` text).

- [ ] **Step 6: Commit** — `fix(viewer): epic progress count and percent come from one function`

---

### Task 4: Missing task never leaves a stale topbar (TD-06, TK-06)

**Files:**
- Modify: `viewer/js/screens/task-detail.js`
- Test: `viewer/tests/task-missing.mock.spec.js`

- [ ] **Step 1: Write the failing spec**

```js
// User intent: a task that doesn't exist must show a plain not-found state and no controls from the previously opened task.
import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api.js';

test('missing task shows not-found and clears the topbar', async ({ page }) => {
  await mockApi(page, {
    '/api/task/NOPE-999/detail': { status: 404, json: { ok: false, error: 'unknown task' } },
  });
  await page.goto('/#/task/NOPE-999');
  await expect(page.locator('#screen-mount .tm-empty__headline')).toHaveText('Task not found');
  await expect(page.locator('#screen-mount')).not.toContainText('GET /api');
  await expect(page.locator('#topbar-actions > *')).toHaveCount(0);
});

test('no task id shows the empty state without inline styles', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#/task');
  await expect(page.locator('#screen-mount .tm-empty__headline')).toHaveText('No task open');
  expect(await page.locator('#screen-mount [style]').count()).toBe(0);
});
```

- [ ] **Step 2: Run** `npm --prefix viewer run test:mock -- task-missing` — Expected: FAIL (raw error text; inline styles present).

- [ ] **Step 3: Implement**

Add to `task-detail.js`:

```js
import { claimTopbar } from '../lib/topbar.js';

function stateBlock(headline, hint) {
  const wrap = document.createElement('div');
  wrap.className = 'tm-empty';
  const h = document.createElement('div');
  h.className = 'tm-empty__headline';
  h.textContent = headline;
  const p = document.createElement('div');
  p.className = 'tm-empty__hint';
  p.append(hint, ' ');
  const a = document.createElement('a');
  a.href = '#/kanban';
  a.textContent = 'Open the Kanban';
  p.appendChild(a);
  wrap.append(h, p);
  return wrap;
}
```

- No-id branch (after the `lastId` redirect): `claimTopbar(); root.replaceChildren(stateBlock('No task open', 'Pick a task from a board.')); return () => {};`
- `refresh()` catch block: replace `root.textContent = …` with
  ```js
  claimTopbar();
  const missing = /\b404\b/.test(String(e?.message));
  root.replaceChildren(stateBlock(missing ? 'Task not found' : 'Could not load task', missing ? `${id} does not exist.` : 'The server did not answer.'));
  ```

- [ ] **Step 4: Run** the spec again — Expected: PASS.

- [ ] **Step 5: Commit** — `fix(viewer): missing task shows not-found state and clears stale topbar`

---

### Task 5: Style-rules test (ratchet)

**Files:**
- Create: `viewer/tests/unit/style-rules.test.js`

**Interfaces:**
- Produces: `ENFORCED` (array of CSS paths relative to `viewer/css/`) — later tasks and plans append their files. The undefined-variable rule applies to **every** file from the start.

- [ ] **Step 1: Write the test**

```js
// User intent: keep the re-skin from decaying — banned patterns and off-token values fail the build, file by file.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const CSS_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'css');

// Files that must satisfy every rule. Append as files are converted; plan 4 replaces this with "all".
export const ENFORCED = [];

function walk(dir) {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : p.endsWith('.css') ? [p] : [];
  });
}
const strip = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '');

// Innermost `selector { body }` blocks, with any enclosing at-rule preludes ignored.
function rules(css) {
  const out = [];
  const stack = [];
  let buf = '';
  for (const ch of strip(css)) {
    if (ch === '{') { stack.push(buf.trim()); buf = ''; }
    else if (ch === '}') {
      const selector = stack.pop() ?? '';
      if (!buf.includes('{')) out.push({ selector, body: buf });
      buf = '';
    } else buf += ch;
  }
  return out;
}

const files = walk(CSS_DIR).map((p) => ({
  path: p, rel: relative(CSS_DIR, p).replaceAll('\\', '/'), css: readFileSync(p, 'utf8'),
}));
const tokens = files.find((f) => f.rel === 'tokens.css');
const defined = new Set([...strip(tokens.css).matchAll(/(--[a-z0-9-]+)\s*:/gi)].map((m) => m[1]));

function violations(f) {
  const v = [];
  const isTokens = f.rel === 'tokens.css';
  for (const { selector, body } of rules(f.css)) {
    const where = `${f.rel} { ${selector.slice(0, 60)} }`;
    if (/\bbox-shadow\s*:/.test(body)) v.push(`${where}: box-shadow`);
    if (/:hover/.test(selector) && /\btransform\s*:/.test(body)) v.push(`${where}: transform on hover`);
    if (/\boutline\s*:\s*(none|0)\b/.test(body)) v.push(`${where}: outline none`);
    for (const m of body.matchAll(/\bborder-left(?:-color)?\s*:\s*([^;]+)/g)) {
      const val = m[1].trim();
      const neutral = /^(0|none|transparent)\b/.test(val) || /var\(--border-[a-z]+\)/.test(val) || !/#|rgb|var\(/.test(val);
      if (!neutral) v.push(`${where}: colored border-left (${val})`);
    }
    if (!isTokens) {
      if (/#[0-9a-f]{3,8}\b/i.test(body) || /\brgba?\(/.test(body)) v.push(`${where}: color literal`);
      for (const m of body.matchAll(/\bfont-size\s*:\s*([^;]+)/g)) {
        if (!/var\(--/.test(m[1]) && !/^(inherit|1em)\s*$/.test(m[1].trim())) v.push(`${where}: raw font-size (${m[1].trim()})`);
      }
      if (/(^|[;\s])--[a-z0-9-]+\s*:/i.test(body) && !/style\s*=/.test(selector)) v.push(`${where}: custom property defined outside tokens.css`);
    }
  }
  return v;
}

test('every var(--x) used in any CSS file is defined in tokens.css', () => {
  const missing = [];
  for (const f of files) {
    for (const m of strip(f.css).matchAll(/var\(\s*(--[a-z0-9-]+)\s*(,)?/gi)) {
      // Variables set from JS as inline style are declared in tokens.css with a default.
      if (!defined.has(m[1])) missing.push(`${f.rel}: ${m[1]}`);
    }
  }
  assert.deepEqual([...new Set(missing)], []);
});

test('enforced files satisfy every style rule', () => {
  const bad = files.filter((f) => ENFORCED.includes(f.rel)).flatMap(violations);
  assert.deepEqual(bad, []);
});

test('report: violations in files not yet enforced', () => {
  const rest = files.filter((f) => !ENFORCED.includes(f.rel));
  const counts = Object.fromEntries(rest.map((f) => [f.rel, violations(f).length]).filter(([, n]) => n));
  console.log('style-rules backlog:', JSON.stringify(counts, null, 1));
});
```

- [ ] **Step 2: Run** `node --test viewer/tests/unit/style-rules.test.js`
Expected: the undefined-variable test FAILS listing `--ink-1`, `--bl`, `--surface-1` and the other names from audit TK-03 plus per-screen custom properties; the enforced test passes (empty list); the report prints counts. This failure is fixed by Task 6 — do not commit yet.

---

### Task 6: Tokens, themes and fonts

**Files:**
- Create: `viewer/tools/gen-tokens.mjs`, `viewer/vendor/fonts/{DMSans,JetBrainsMono,LeagueSpartan}-Variable.woff2`, `viewer/tests/unit/tokens-parity.test.js`
- Rewrite: `viewer/css/tokens.css`
- Modify: `viewer/css/shell.css` (line 1 `@import`, `:root` block, `html, body`, `.serif`), every screen CSS file that defines a custom property (move the definition into `tokens.css`)
- Test: `tests/test_server_api.py` (font served)

**Interfaces:**
- Produces: all RR tokens as `--<rr-name>`; viewer tokens `--page-gutter`, `--col-bg`, `--card-bg`, `--card-bg-hover`, `--cat-1 … --cat-6`; legacy aliases for every old name (deleted in plan 4).

- [ ] **Step 1: Write the parity test**

```js
// User intent: tokens.css must carry the design system's exact values — a drifted hex is a silent brand bug.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const rr = JSON.parse(readFileSync(join(here, '../../../docs/specs/assets/reality-reprojection-2026-10-01/tokens.json'), 'utf8'));
const css = readFileSync(join(here, '../../css/tokens.css'), 'utf8');
const block = (re) => (css.match(re) || [, ''])[1];
const dark = block(/@generated:rr-dark \*\/([\s\S]*?)\/\* @generated:end/);
const light = block(/@generated:rr-light \*\/([\s\S]*?)\/\* @generated:end/);
const cssVal = (v) => String(v).replace(/^\{(.+)\}$/, 'var(--$1)');
const has = (src, name, v) => new RegExp(`--${name}:\\s*${cssVal(v).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*;`).test(src);

test('every RR color token is present with its dark and light value', () => {
  const miss = [];
  for (const t of rr.color.tokens) {
    const d = typeof t.value === 'string' ? t.value : t.value.dark;
    if (!has(dark, t.name, d)) miss.push(`dark ${t.name}`);
    if (typeof t.value === 'object' && t.value.light && !has(light, t.name, t.value.light)) miss.push(`light ${t.name}`);
  }
  assert.deepEqual(miss, []);
});

test('scalar families are present', () => {
  const miss = [];
  for (const fam of ['spacing', 'radius', 'typescale', 'leading', 'tracking', 'weight', 'blur', 'easing', 'duration', 'stagger']) {
    for (const t of rr[fam].tokens) if (!has(dark, t.name, t.value)) miss.push(`${fam} ${t.name}`);
  }
  assert.deepEqual(miss, []);
});

test('shadow and grain families are not imported', () => {
  for (const t of [...rr.shadow.tokens, ...rr.grain.tokens]) assert.ok(!css.includes(`--${t.name}:`), t.name);
});

test('no survivalist theme, no banned declarations', () => {
  assert.ok(!/survivalist/.test(css.replace(/\/\*[\s\S]*?\*\//g, '')));
  assert.ok(!/box-shadow/.test(css));
});
```

Run: `node --test viewer/tests/unit/tokens-parity.test.js` — Expected: FAIL (no generated blocks).

- [ ] **Step 2: Write the generator**

`viewer/tools/gen-tokens.mjs`:

```js
// User intent: regenerate the design-system part of tokens.css from the pinned Reality Reprojection tokens.json, so values are copied by machine, never by hand.
// Usage: node viewer/tools/gen-tokens.mjs   (rewrites the two @generated blocks in viewer/css/tokens.css)
import { readFileSync, writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const SRC = join(here, '../../docs/specs/assets/reality-reprojection-2026-10-01/tokens.json');
const OUT = join(here, '../css/tokens.css');
const rr = JSON.parse(readFileSync(SRC, 'utf8'));
const v = (x) => String(x).replace(/^\{(.+)\}$/, 'var(--$1)');
const line = (name, val) => `  --${name}: ${v(val)};`;

const dark = [];
const light = [];
for (const t of rr.color.tokens) {
  if (typeof t.value === 'string') { dark.push(line(t.name, t.value)); continue; }
  dark.push(line(t.name, t.value.dark));
  if (t.value.light) light.push(line(t.name, t.value.light));
}
for (const [k, fam] of Object.entries(rr.type.families)) {
  if (k === 'declaration-alt' || k === 'survivalist') continue;
  dark.push(`  --font-${k}: ${fam};`);
}
for (const fam of ['spacing', 'radius', 'typescale', 'leading', 'tracking', 'weight', 'blur', 'easing', 'duration', 'stagger']) {
  for (const t of rr[fam].tokens) dark.push(line(t.name, t.value));
}

let css = readFileSync(OUT, 'utf8');
const put = (tag, body) => {
  const re = new RegExp(`(/\\* @generated:${tag} \\*/)[\\s\\S]*?(/\\* @generated:end \\*/)`);
  if (!re.test(css)) throw new Error(`marker @generated:${tag} missing in tokens.css`);
  css = css.replace(re, `$1\n${body.join('\n')}\n  $2`);
};
put('rr-dark', dark);
put('rr-light', light);
writeFileSync(OUT, css);
console.log(`tokens.css: ${dark.length} base tokens, ${light.length} light overrides`);
```

- [ ] **Step 3: Copy the fonts**

```bash
mkdir -p C:/Users/gruku/Files/Claude/taskmaster/viewer/vendor/fonts
cp C:/Users/gruku/Files/Claude/taskmaster/docs/specs/assets/reality-reprojection-2026-10-01/fonts/*.woff2 C:/Users/gruku/Files/Claude/taskmaster/viewer/vendor/fonts/
```

- [ ] **Step 4: Rewrite `viewer/css/tokens.css`** with this skeleton, then run the generator.

```css
/* User intent: single source of truth for the viewer's design tokens — Reality Reprojection values (generated),
   plus the few viewer-specific roles. Screen CSS reads var(--*) only and defines no tokens of its own. */

@font-face { font-family: 'League Spartan'; src: url('../vendor/fonts/LeagueSpartan-Variable.woff2') format('woff2'); font-weight: 100 900; font-display: swap; }
@font-face { font-family: 'DM Sans';        src: url('../vendor/fonts/DMSans-Variable.woff2') format('woff2');        font-weight: 100 1000; font-display: swap; }
@font-face { font-family: 'JetBrains Mono'; src: url('../vendor/fonts/JetBrainsMono-Variable.woff2') format('woff2'); font-weight: 100 800; font-display: swap; }

:root {
  /* @generated:rr-dark */
  /* @generated:end */

  /* ── Viewer roles (not in RR) ── */
  --page-gutter: var(--space-lg);
  --col-bg: var(--bg-recessed);              /* board column = milled channel */
  --card-bg: var(--surface-raised);
  --card-bg-hover: var(--ground-15);
  --cat-1: var(--signature);                 /* categorical palette: epics, bundles — swatch only, never text */
  --cat-2: var(--accent-cyan);
  --cat-3: var(--accent-pink);
  --cat-4: var(--accent-orange);
  --cat-5: var(--accent-lime);
  --cat-6: var(--pastel-orange);

  /* ── Layout ── */
  --sidebar-w: 240px;
  --rail-w: 720px;
  --bp-sm: 480px; --bp-md: 768px; --bp-lg: 1024px;   /* documentation only: @media needs literals */

  /* ── Legacy aliases — DELETE in plan 4. Old name → RR role. ── */
  --bg-canvas: var(--bg-page);
  --bg-shell: var(--surface-ground);
  --bg-panel: var(--surface-raised);
  --bg-card: var(--card-bg);
  --bg-card-hover: var(--card-bg-hover);
  --bg-board-col: var(--col-bg);
  --bg-deep: var(--bg-recessed);
  --bg-issue: var(--surface-raised);
  --border: var(--border-subtle);
  --border-soft: var(--border-subtle);
  --ink: var(--foreground-bold);
  --ink-1: var(--foreground-bold);
  --ink-2: var(--foreground-default);
  --ink-3: var(--foreground-subtle);
  --ink-4: var(--foreground-subtle);
  --ink-on-accent: var(--on-signature);
  --accent: var(--signature);
  --accent-2: var(--signature-vivid);
  --accent-soft: var(--signature-glow);
  --accent-blue: var(--signature-text);
  --accent-edit: var(--signature-text);
  --accent-green: var(--color-success);
  --green: var(--color-success);
  --amber: var(--color-warning);
  --gold: var(--color-warning);
  --red: var(--color-critical);
  --purple: var(--pastel-signature);
  --diff-add: var(--color-success);
  --diff-mod: var(--color-warning);
  --diff-del: var(--color-critical);
  --sev-critical: var(--color-critical);
  --sev-high: var(--accent-orange);
  --sev-medium: var(--color-warning);
  --sev-low: var(--foreground-subtle);
  --epic-1: var(--cat-1); --epic-2: var(--cat-2); --epic-3: var(--cat-3);
  --epic-4: var(--cat-4); --epic-5: var(--cat-5); --epic-6: var(--cat-6);
  --bundle-1: var(--cat-2); --bundle-2: var(--cat-3); --bundle-3: var(--cat-4);
  --bundle-4: var(--cat-5); --bundle-5: var(--cat-6); --bundle-6: var(--cat-1);
  --font-sans: var(--font-narrator);
  --font-mono: var(--font-technical);
  --font-serif: var(--font-narrator);
  --text-xs: var(--size-technical-small);
  --text-sm: var(--size-narrator-small);
  --text-base: var(--size-narrator-default);
  --text-md: var(--size-narrator-default);
  --text-lg: var(--size-narrator-large);
  --text-xl: var(--size-declaration-h4);
  --text-2xl: var(--size-declaration-h4);
  --text-3xl: var(--size-declaration-h3);
  --sp-1: var(--space-micro); --sp-2: var(--space-xs); --sp-3: var(--space-sm);
  --sp-4: var(--space-md); --sp-5: var(--space-md); --sp-6: var(--space-lg);
  --sp-7: var(--space-lg); --sp-8: var(--space-lg); --sp-9: var(--space-xl);
  --sp-10: var(--space-xl); --sp-12: var(--space-2xl); --sp-13: var(--space-3xl);
  --r-sm: var(--radius-lg); --r-md: var(--radius-xl); --r-lg: var(--radius-xl);
  --r-xl: var(--radius-xl); --r-2xl: var(--radius-xl);
  --t-fast: var(--dur-micro); --t-base: var(--dur-standard); --t-slow: var(--dur-macro);
  --ease: var(--ease-hourglass);
  --page-pad: var(--page-gutter);
  --page-gap: var(--space-md);
}

[data-theme="light"] {
  /* @generated:rr-light */
  /* @generated:end */
  --cat-2: var(--pastel-cyan-grounded);
  --cat-3: var(--pastel-pink-grounded);
  --cat-5: var(--pastel-lime-grounded);
  --cat-6: var(--pastel-orange-grounded);
}

@media (max-width: 768px) { :root { --page-gutter: var(--space-md); } }

@keyframes pulse { 0%, 100% { opacity: 0.5; } 50% { opacity: 1; } }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }
}
```

Run: `node viewer/tools/gen-tokens.mjs`. Expected: prints token counts; the two blocks are filled.

- [ ] **Step 5: Resolve every undefined or stray variable**

Run `node --test viewer/tests/unit/style-rules.test.js`. For each name the undefined-variable test lists:
- A name with an RR equivalent → add a legacy alias in the block above (`--bl: var(--border-default)`, `--surface: var(--surface-raised)`, `--surface2: var(--surface-overlay)`, `--surface-1: var(--surface-raised)`, `--s2: var(--surface-overlay)`, `--danger-ink: var(--color-critical)`, `--danger-border: var(--color-critical)`, `--danger-bg: var(--color-critical-subtle)`, `--amber-tint-bg: var(--color-warning-subtle)`, `--amber-tint-border: var(--color-warning)`, `--muted-tint-bg: var(--ground-15)`, `--muted-tint-border: var(--border-default)`).
- A custom property defined in a screen file (audit TK-04: task-detail 12, desk 8, kanban 4, issues 3, shell 3) → move its definition into the legacy block of `tokens.css`, expressed through RR tokens. Shadow-valued ones (`--graph-frame-shadow`, `--card-recent-glow`, `--shadow-card`, `--shadow-lifted`, `--recess-inset`) become `none`; `--recess-bg` becomes `var(--bg-recessed)`. Variables that JS sets inline (e.g. `--tilt`, `--paper`, epic color vars) get a default in the legacy block.

Repeat until the undefined-variable test passes.

- [ ] **Step 6: Shell base styles**

In `viewer/css/shell.css`: delete line 1 (`@import url('https://fonts.googleapis.com…')`), delete the `:root { --shell-* }` block (moved in step 5), and set:

```css
html, body {
  margin: 0; padding: 0; height: 100%; overflow: hidden;
  font-family: var(--font-narrator);
  font-weight: var(--font-narrator-weight);
  font-size: var(--size-narrator-default);
  line-height: var(--leading-body);
  background: var(--bg-page);
  color: var(--foreground-default);
}
button, input, select, textarea { font: inherit; color: inherit; }
:focus-visible { outline: 2px solid var(--border-focus); outline-offset: 2px; }
::selection { background: var(--signature-dim); }
.mono { font-family: var(--font-technical); font-weight: var(--font-technical-weight); }
.serif { font-family: var(--font-narrator); font-style: normal; }
```

- [ ] **Step 7: Font is served by the live server**

Append to `tests/test_server_api.py`:

```python
def test_viewer_font_is_served(running_server):
    base, _ = running_server
    with urllib.request.urlopen(f"{base}/static/v3/vendor/fonts/DMSans-Variable.woff2") as r:
        assert r.status == 200
        assert r.headers["Content-Type"].startswith("font/woff2")
        assert len(r.read()) > 10_000
```

(Add `import urllib.request` if the module lacks it.) Run: `.venv/Scripts/python -m pytest tests/test_server_api.py -k font -v` — Expected: PASS.

- [ ] **Step 8: Run all unit tests**

Run: `npm --prefix viewer run test:unit` — Expected: parity, undefined-variable and all pre-existing tests PASS.

- [ ] **Step 9: Commit** — `feat(viewer): Reality Reprojection tokens, dark/light values, local fonts`
Stage: `viewer/css`, `viewer/tools`, `viewer/vendor/fonts`, `viewer/tests/unit/style-rules.test.js`, `viewer/tests/unit/tokens-parity.test.js`, `tests/test_server_api.py`.

---

### Task 7: Theme switching

**Files:**
- Create: `viewer/js/lib/theme.js`, `viewer/tests/unit/theme.test.js`, `viewer/tests/theme.mock.spec.js`
- Modify: `viewer/index.html`, `viewer/js/main.js`, `taskmaster/taskmaster_v3.py:3612`

**Interfaces:**
- Produces: `resolveTheme(pref, systemDark) → 'dark'|'light'`; `normalizePref(v) → 'dark'|'light'|'system'`; `initTheme({ store, prefs })`; `setThemePref(pref)`; `currentPref()`; DOM event `theme:changed` with `detail: { pref, theme }`. localStorage key `tm.theme`.

- [ ] **Step 1: Write the failing unit test**

```js
// User intent: an unknown or missing saved theme must fall back to the system theme, never to an unstyled page.
import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveTheme, normalizePref } from '../../js/lib/theme.js';

test('explicit choices win', () => {
  assert.equal(resolveTheme('dark', false), 'dark');
  assert.equal(resolveTheme('light', true), 'light');
});
test('system follows the OS', () => {
  assert.equal(resolveTheme('system', true), 'dark');
  assert.equal(resolveTheme('system', false), 'light');
});
for (const bad of [undefined, null, '', 'blue', 42, {}]) {
  test(`unknown pref ${JSON.stringify(bad)} behaves as system`, () => {
    assert.equal(normalizePref(bad), 'system');
    assert.equal(resolveTheme(bad, true), 'dark');
    assert.equal(resolveTheme(bad, false), 'light');
  });
}
```

Run: `node --test viewer/tests/unit/theme.test.js` — Expected: FAIL, module not found.

- [ ] **Step 2: Implement** `viewer/js/lib/theme.js`

```js
// User intent: dark and light are equal themes; the user's choice persists, and with no choice the viewer follows the OS.
const KEY = 'tm.theme';
const PREFS = new Set(['dark', 'light', 'system']);
let pref = 'system';
let savePref = null;

export function normalizePref(v) { return PREFS.has(v) ? v : 'system'; }
export function resolveTheme(p, systemDark) {
  const n = normalizePref(p);
  return n === 'system' ? (systemDark ? 'dark' : 'light') : n;
}
export function currentPref() { return pref; }

function systemDark() {
  return typeof matchMedia === 'function' ? matchMedia('(prefers-color-scheme: dark)').matches : true;
}
function apply() {
  const theme = resolveTheme(pref, systemDark());
  document.documentElement.dataset.theme = theme;
  document.dispatchEvent(new CustomEvent('theme:changed', { detail: { pref, theme } }));
}
export function setThemePref(next) {
  pref = normalizePref(next);
  try { localStorage.setItem(KEY, pref); } catch { /* storage unavailable: the choice still applies for this page */ }
  savePref?.(pref);
  apply();
}
export function initTheme({ store, prefs }) {
  savePref = (p) => prefs.patch({ theme: p });
  pref = normalizePref(store.getPrefs()?.theme);
  try { localStorage.setItem(KEY, pref); } catch { /* ignore */ }
  if (typeof matchMedia === 'function') {
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (pref === 'system') apply(); });
  }
  apply();
}
```

Run the unit test — Expected: PASS.

- [ ] **Step 3: Pre-paint script and markup**

In `viewer/index.html`: change `<html lang="en" data-theme="dark">` to `<html lang="en">`, and insert as the first child of `<head>` after the charset meta:

```html
  <script>
    (function () {
      var p = 'system';
      try { p = localStorage.getItem('tm.theme') || 'system'; } catch (e) {}
      var dark = true;
      try { dark = matchMedia('(prefers-color-scheme: dark)').matches; } catch (e) {}
      document.documentElement.dataset.theme = p === 'dark' || p === 'light' ? p : (dark ? 'dark' : 'light');
    })();
  </script>
```

Change the favicon fill from `%234a9eff` to `%235e79e6`.

- [ ] **Step 4: Boot wiring and server default**

`viewer/js/main.js`: `import { initTheme } from './lib/theme.js';` and call `initTheme({ store, prefs });` immediately after `store.setPrefs(prefsData);`.
`taskmaster/taskmaster_v3.py:3612`: `"theme": "system",        # dark | light | system`. Then `grep -rn '"theme"' taskmaster tests` and update any test that asserts the default `"dark"`.

- [ ] **Step 5: Theme transition** — append to `viewer/css/shell.css`:

```css
body, .shell, .sidebar, .topbar, .main {
  transition: background-color var(--dur-standard) var(--ease-hourglass),
              color var(--dur-standard) var(--ease-hourglass),
              border-color var(--dur-standard) var(--ease-hourglass);
}
```

- [ ] **Step 6: Write the Playwright spec** (the toggle button itself arrives in Task 8; this spec drives the module)

```js
// User intent: the theme choice survives reloads, never flashes the wrong theme, and still works when storage is blocked.
import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api.js';

test('light pref from the server is applied', async ({ page }) => {
  await mockApi(page, { '/api/viewer/prefs': { theme: 'light', ui: {}, screens: {} } });
  await page.goto('/#/settings');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(230, 228, 221)');   // ground-5 light = #e6e4dd
});

test('system pref follows the OS scheme', async ({ page }) => {
  await mockApi(page);
  await page.emulateMedia({ colorScheme: 'light' });
  await page.goto('/#/settings');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
});

test('stored choice is applied before first paint', async ({ page }) => {
  await mockApi(page);
  await page.addInitScript(() => localStorage.setItem('tm.theme', 'light'));
  await page.route('**/css/tokens.css', async (r) => { await new Promise((ok) => setTimeout(ok, 300)); r.continue(); });
  await page.goto('/#/settings', { waitUntil: 'commit' });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});

test('blocked localStorage does not break boot', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.addInitScript(() => {
    Object.defineProperty(window, 'localStorage', { get() { throw new Error('denied'); } });
  });
  await mockApi(page);
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/#/settings');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(page.locator('#sidebar .sidebar-link').first()).toBeVisible();
  expect(errors).toEqual([]);
});

test('fonts load locally with no network font request', async ({ page }) => {
  const external = [];
  page.on('request', (r) => { if (!r.url().startsWith('http://127.0.0.1')) external.push(r.url()); });
  await mockApi(page);
  await page.goto('/#/settings');
  await page.evaluate(() => document.fonts.ready);
  expect(await page.evaluate(() => document.fonts.check('600 16px "DM Sans"'))).toBe(true);
  expect(external).toEqual([]);
});
```

Run: `npm --prefix viewer run test:mock -- theme` — Expected: all PASS.

- [ ] **Step 7: Commit** — `feat(viewer): dark/light/system theme with pre-paint apply`

---

### Task 8: Shell — topbar, sidebar, drawer, search

**Files:**
- Create: `viewer/js/components/icon.js`, `viewer/tests/unit/icon.test.js`, `viewer/tests/shell.mock.spec.js`
- Modify: `viewer/index.html`, `viewer/css/shell.css` (full conversion), `viewer/js/components/sidebar.js`, `viewer/js/lib/topbar.js`, `viewer/js/screens/task-detail.js` (`sidebarKey: null`), `viewer/tests/unit/style-rules.test.js` (`ENFORCED = ['shell.css']`), `viewer/tests/smoke.spec.js` if it counts sidebar links

**Interfaces:**
- Produces: `icon(name, { size = 20, label } = {}) → SVGElement`; icon names `arrow check chevron copy dismiss document edit external folder grid minus more plus polarity search sliders kanban table alert bug idea archive menu`. Topbar DOM: `#topbar > .topbar-row1 (#page-title, #topbar-count, #topbar-primary, #theme-toggle)` and `#topbar-actions.topbar-row2`. `claimTopbar()` still returns `#topbar-actions` and now also empties `#topbar-count` and `#topbar-primary`; new `claimTopbarPrimary() → HTMLElement`, `setTopbarCount(text)`.

- [ ] **Step 1: Icon unit test**

```js
// User intent: icons come from one inline set drawn to the design system's rules, never from emoji or Unicode glyphs.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><body></body>');
globalThis.document = dom.window.document;
const { icon, ICONS } = await import('../../js/components/icon.js');

test('every icon renders as a 24-box stroke svg using currentColor', () => {
  for (const name of Object.keys(ICONS)) {
    const el = icon(name);
    assert.equal(el.getAttribute('viewBox'), '0 0 24 24', name);
    assert.equal(el.getAttribute('stroke'), 'currentColor', name);
    assert.equal(el.getAttribute('stroke-width'), '2.25', name);
    assert.equal(el.getAttribute('aria-hidden'), 'true', name);
    assert.ok(el.children.length > 0, name);
  }
});
test('labelled icon is exposed to assistive tech', () => {
  const el = icon('search', { label: 'Search' });
  assert.equal(el.getAttribute('role'), 'img');
  assert.equal(el.getAttribute('aria-label'), 'Search');
  assert.equal(el.getAttribute('aria-hidden'), null);
});
test('unknown icon throws', () => { assert.throws(() => icon('nope')); });
test('the 16 design-system glyphs and the 7 viewer glyphs exist', () => {
  for (const n of ['arrow','check','chevron','copy','dismiss','document','edit','external','folder','grid','minus','more','plus','polarity','search','sliders','kanban','table','alert','bug','idea','archive','menu']) assert.ok(ICONS[n], n);
});
```

Run: `node --test viewer/tests/unit/icon.test.js` — Expected: FAIL, module not found.

- [ ] **Step 2: Implement `viewer/js/components/icon.js`**

The sixteen design-system glyphs below are the inner markup of `RR/icons/*.svg` with ink fills changed to `currentColor`; the seven viewer glyphs follow the same drawing rules.

```js
// User intent: one inline icon set for the whole viewer — the Reality Reprojection utility pack plus the few glyphs it lacks, drawn to the same rules (24 box, 2.25 stroke, round caps).
const NS = 'http://www.w3.org/2000/svg';

export const ICONS = {
  // ── Reality Reprojection pack (inner markup of docs/specs/assets/reality-reprojection-2026-10-01/icons/<name>.svg; ink fills → currentColor) ──
  polarity: '<circle cx="12" cy="12" r="9"></circle><path d="M12 3 A9 9 0 0 1 12 21 Z" fill="currentColor"></path>',
  grid: '<rect x="4" y="4" width="7" height="7" rx="1"></rect><rect x="13" y="4" width="7" height="7" rx="1"></rect><rect x="4" y="13" width="7" height="7" rx="1"></rect><rect x="13" y="13" width="7" height="7" rx="1"></rect>',
  chevron: '<path d="M9 5 L16 12 L9 19"></path>',
  arrow: '<path d="M3.5 12 H13"></path><path d="M12 5.5 L20 12 L12 18.5 Z" fill="currentColor"></path>',
  check: '<path d="M4.5 12.5 L9.5 17.5 L19.5 6.5"></path>',
  copy: '<rect x="4" y="8" width="11" height="12" rx="1"></rect><path d="M8 8 V4 H19 V16 H15"></path>',
  dismiss: '<path d="M5.5 5.5 L18.5 18.5 M18.5 5.5 L5.5 18.5"></path>',
  document: '<path d="M6 3 H15 L19 7 V21 H6 Z"></path><path d="M14 3 V8 H19"></path><path d="M9 12 H16 M9 15.5 H16 M9 19 H13" stroke-width="2"></path>',
  edit: '<path d="M4 20 L4 16 L16 4 L20 8 L8 20 Z"></path><path d="M13 7 L17 11"></path>',
  external: '<path d="M9 5 H19 V15"></path><path d="M19 5 L9.5 14.5"></path><path d="M14 19 H5 V10"></path>',
  folder: '<path d="M3 6 H10 L12 9 H21 V19 H3 Z"></path>',
  minus: '<path d="M4 12 H20"></path>',
  more: '<rect x="3.75" y="10.625" width="3.5" height="2.75" rx="0.5" fill="currentColor" stroke="none"></rect><rect x="10.25" y="10.625" width="3.5" height="2.75" rx="0.5" fill="currentColor" stroke="none"></rect><rect x="16.75" y="10.625" width="3.5" height="2.75" rx="0.5" fill="currentColor" stroke="none"></rect>',
  plus: '<path d="M12 4 V20 M4 12 H20"></path>',
  search: '<circle cx="10.5" cy="10.5" r="6"></circle><path d="M15 15 L20 20"></path>',
  // sliders: the source fills the knobs with the light ground (#f5f3ed) to mask the track; here the knob takes the surface it sits on.
  sliders: '<path d="M4 7 H20 M4 12 H20 M4 17 H20"></path><rect x="6.75" y="4.5" width="3.5" height="5" rx="0.75" fill="var(--surface-ground)"></rect><rect x="13.75" y="9.5" width="3.5" height="5" rx="0.75" fill="var(--surface-ground)"></rect><rect x="8.75" y="14.5" width="3.5" height="5" rx="0.75" fill="var(--surface-ground)"></rect>',

  // ── Viewer glyphs ──
  kanban: '<rect x="3.5" y="4" width="4.5" height="16" rx="1"></rect><rect x="9.75" y="4" width="4.5" height="10" rx="1"></rect><rect x="16" y="4" width="4.5" height="13" rx="1"></rect>',
  table: '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"></rect><path d="M3.5 10h17M3.5 14.75h17M9.5 10v9.5"></path>',
  alert: '<path d="M12 4.5 20.5 19h-17Z"></path><path d="M12 10v4.25M12 16.9v.1"></path>',
  bug: '<rect x="8" y="8" width="8" height="11" rx="4"></rect><path d="M9.5 8a2.5 2.5 0 0 1 5 0M4 13h4M16 13h4M5 7.5l3 2.5M19 7.5 16 10M5 19l3-2.5M19 19l-3-2.5"></path>',
  idea: '<path d="M8.5 14a5.5 5.5 0 1 1 7 0c-.7.6-1 1.3-1 3h-5c0-1.7-.3-2.4-1-3Z"></path><path d="M10 20h4"></path>',
  archive: '<rect x="3.5" y="5" width="17" height="4" rx="1"></rect><path d="M5 9v9.5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4"></path>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"></path>',
};

export function icon(name, { size = 20, label } = {}) {
  const inner = ICONS[name];
  if (!inner) throw new Error(`unknown icon: ${name}`);
  const el = document.createElementNS(NS, 'svg');
  el.setAttribute('viewBox', '0 0 24 24');
  el.setAttribute('width', String(size));
  el.setAttribute('height', String(size));
  el.setAttribute('fill', 'none');
  el.setAttribute('stroke', 'currentColor');
  el.setAttribute('stroke-width', '2.25');
  el.setAttribute('stroke-linecap', 'round');
  el.setAttribute('stroke-linejoin', 'round');
  if (label) { el.setAttribute('role', 'img'); el.setAttribute('aria-label', label); }
  else el.setAttribute('aria-hidden', 'true');
  el.classList.add('icon');
  el.innerHTML = inner;
  return el;
}
```

Run the test — Expected: PASS.

- [ ] **Step 3: Topbar markup** — in `viewer/index.html` replace the `<header>` with:

```html
      <header class="topbar" id="topbar">
        <div class="topbar-row1">
          <h1 id="page-title">Loading…</h1>
          <span class="topbar-count" id="topbar-count"></span>
          <div class="topbar-primary" id="topbar-primary"></div>
          <button type="button" class="btn-icon" id="theme-toggle" aria-pressed="false"></button>
        </div>
        <div class="topbar-actions topbar-row2" id="topbar-actions"></div>
      </header>
```

- [ ] **Step 4: Topbar helpers** — in `viewer/js/lib/topbar.js`:

```js
export function claimTopbar() {
  document.getElementById('topbar-count')?.replaceChildren();
  document.getElementById('topbar-primary')?.replaceChildren();
  const root = document.getElementById('topbar-actions');
  if (!root) return null;
  root.replaceChildren();
  return root;
}
export function claimTopbarPrimary() { return document.getElementById('topbar-primary'); }
export function setTopbarCount(text = '') {
  const el = document.getElementById('topbar-count');
  if (el) el.textContent = text;
}
```

In `tmSearch`: default `kbd` becomes `undefined` and resolves to `/Mac|iPhone|iPad/.test(navigator.platform) ? '⌘K' : 'Ctrl K'`; `ariaLabel` defaults to `placeholder.replace(/…$/, '')`; replace the `⌕` text icon with `icon('search', { size: 16 })`; mark the input `data-global-search`. In `rightCluster` replace the inline `style.cssText` with the class `tm-right` only (CSS in shell.css: `margin-left: auto; display: inline-flex; align-items: center; gap: var(--space-xs)`). In `tmSegmented` no change.

Add once in `viewer/js/main.js` after `routerInit(...)`:

```js
window.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    const input = document.querySelector('[data-global-search]');
    if (input) { e.preventDefault(); input.focus(); input.select(); }
  }
});
```

Theme toggle wiring, also in `main.js` after `initTheme`:

```js
import { icon } from './components/icon.js';
import { setThemePref } from './lib/theme.js';

const toggle = document.getElementById('theme-toggle');
toggle.appendChild(icon('polarity', { size: 20 }));
document.addEventListener('theme:changed', (e) => {
  const next = e.detail.theme === 'dark' ? 'light' : 'dark';
  toggle.setAttribute('aria-label', `Switch to ${next} theme`);
  toggle.title = `Switch to ${next} theme`;
  toggle.setAttribute('aria-pressed', String(e.detail.theme === 'light'));
  toggle.dataset.next = next;
});
toggle.addEventListener('click', () => setThemePref(toggle.dataset.next));
```

Register the `theme:changed` listener before calling `initTheme` so the first event is received. Also replace the inline-styled boot error in `main.js` with `sidebarEl.replaceChildren(Object.assign(document.createElement('div'), { className: 'boot-error', textContent: \`Boot failed: ${e.message}\` }));`.

- [ ] **Step 5: Sidebar** — in `viewer/js/components/sidebar.js`:

- `SECTIONS` items use icon names instead of glyphs and the Task entry is removed:
  `dashboard→grid, kanban→kanban, table→table, epics→folder, sessions→document, issues→alert, bugs→bug, ideas→idea, archived→archive, settings→sliders`.
- Link markup built with DOM calls: `<span class="ic">` containing `icon(item.icon)`, `<span class="lbl">`. No `.badge` span.
- Logo: `<div class="brand"><div class="name">TASKMASTER</div><div class="ver" id="sidebar-version">v?</div></div>`; no `.mark`. Collapse button holds `icon('chevron', { size: 16 })` and toggles class `is-collapsed` (CSS rotates it 180° — a static transform, not hover) instead of swapping `‹`/`›` text.
- Remove the footer block entirely.
- Hamburger: `icon('menu')` / `icon('dismiss')` instead of `☰`/`✕`; set `aria-controls="sidebar"` and keep `aria-expanded` in sync in `openDrawer`/`closeDrawer`.
- `openDrawer()` additionally: remember `document.activeElement`, then focus the first `.sidebar-link`. `closeDrawer()` restores focus to the hamburger.
- Add a `keydown` listener on `document` while the drawer is open: `Escape` → `closeDrawer()`.
- In `viewer/js/screens/task-detail.js` set `sidebarKey: null`.

- [ ] **Step 6: Convert `viewer/css/shell.css` fully**

Rewrite the file so it passes every style rule. Mechanical rules: every `px` spacing → nearest `--space-*`; every radius → `--radius-*`; every color → an RR role; every `font-size` → a type token; remove all `box-shadow` (dividers become `border-bottom: 1px solid var(--border-subtle)`), remove `outline: none`. The new or changed rules, verbatim:

```css
.shell { display: grid; grid-template-columns: var(--sidebar-w) minmax(0, 1fr); grid-template-rows: 100vh; height: 100vh; min-height: 0; overflow: hidden; background: var(--bg-page); }
.sidebar { background: var(--surface-ground); border-right: 1px solid var(--border-subtle); display: flex; flex-direction: column; min-height: 0; }
.sidebar-logo .name { font-family: var(--font-declaration); font-weight: var(--font-declaration-weight); font-size: var(--size-declaration-h5); letter-spacing: var(--tracking-tight); line-height: var(--leading-tight); color: var(--foreground-bold); }
.sidebar-logo .ver { font-family: var(--font-technical); font-size: var(--size-technical-small); color: var(--foreground-subtle); }
.sidebar-section-h { font-family: var(--font-technical); font-weight: 800; font-size: var(--size-technical-label); letter-spacing: var(--tracking-ultra); text-transform: uppercase; color: var(--foreground-subtle); padding: var(--space-md) var(--space-lg) var(--space-xs); }
.sidebar-link { display: flex; align-items: center; gap: var(--space-sm); padding: var(--space-xs) var(--space-lg); color: var(--foreground-default); text-decoration: none; font-size: var(--size-narrator-small); border: 1px solid transparent; }
.sidebar-link:hover { background: var(--ground-10); color: var(--foreground-bold); }
.sidebar-link.active { background: var(--signature-glow-strong); color: var(--foreground-bold); border-color: var(--signature-dim); }
.sidebar-link.active .ic { color: var(--signature-text); }
.sidebar-link .ic { display: inline-flex; color: var(--foreground-subtle); }
.sidebar-collapse-btn.is-collapsed .icon { transform: rotate(180deg); }

.topbar { display: flex; flex-direction: column; border-bottom: 1px solid var(--border-subtle); background: var(--bg-page); padding: 0 var(--page-gutter); }
.topbar-row1 { display: flex; align-items: center; gap: var(--space-md); height: 56px; }
.topbar-row2 { display: flex; align-items: center; gap: var(--space-xs); height: 48px; overflow-x: auto; overflow-y: hidden; white-space: nowrap; scrollbar-width: thin; }
.topbar-row2:empty { display: none; }
#page-title { margin: 0; font-family: var(--font-declaration); font-weight: var(--font-declaration-weight); font-size: var(--size-declaration-h4); letter-spacing: var(--tracking-tight); line-height: var(--leading-tight); text-transform: uppercase; color: var(--foreground-bold); }
.topbar-count { font-family: var(--font-technical); font-size: var(--size-technical-small); color: var(--foreground-subtle); }
.topbar-primary { margin-left: auto; display: inline-flex; align-items: center; gap: var(--space-xs); }
.btn-icon { display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px; padding: 0; background: transparent; border: 1px solid var(--border-default); border-radius: var(--radius-md); color: var(--foreground-default); cursor: pointer; transition: background-color var(--dur-standard) var(--ease-hourglass), border-color var(--dur-standard) var(--ease-hourglass), color var(--dur-standard) var(--ease-hourglass); }
.btn-icon:hover { background: var(--ground-15); border-color: var(--border-strong); color: var(--foreground-bold); }
#theme-toggle { color: var(--signature-text); }

.screen-mount { padding: var(--page-gutter); overflow: auto; min-height: 0; }
.tm-right { margin-left: auto; display: inline-flex; align-items: center; gap: var(--space-xs); }
.boot-error { padding: var(--space-md); color: var(--foreground-bold); font-size: var(--size-narrator-small); }

@media (max-width: 768px) {
  .sidebar-collapse-btn { display: none; }
  .sidebar { position: fixed; inset: 0 auto 0 0; width: var(--sidebar-w); transform: translateX(-100%); transition: transform var(--dur-macro) var(--ease-hourglass); z-index: 40; }
  .shell.sidebar-drawer-open .sidebar { transform: translateX(0); }
  .sidebar-backdrop { position: fixed; inset: 0; background: var(--overlay-bg); z-index: 39; display: none; }
  .shell.sidebar-drawer-open .sidebar-backdrop { display: block; }
}
```

Screens that set their own outer padding on `#screen-mount` keep doing so until plan 3; do not touch screen CSS here. Then set `export const ENFORCED = ['shell.css'];` in `style-rules.test.js`.

- [ ] **Step 7: Shell spec**

```js
// User intent: the shell must be keyboard-usable, free of the banned visual patterns, and its theme toggle must work.
import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api.js';

test.beforeEach(async ({ page }) => { await mockApi(page); });

test('theme toggle flips and persists the choice', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/#/settings');
  const puts = [];
  page.on('request', (r) => { if (r.method() === 'PUT') puts.push(r.postData()); });
  const toggle = page.locator('#theme-toggle');
  await expect(toggle).toHaveAttribute('aria-label', 'Switch to light theme');
  await toggle.click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(toggle).toHaveAttribute('aria-label', 'Switch to dark theme');
  expect(await page.evaluate(() => localStorage.getItem('tm.theme'))).toBe('light');
  await expect.poll(() => puts.join('')).toContain('"theme":"light"');
});

test('active nav item has no shadow and no left rail; Task item is gone', async ({ page }) => {
  await page.goto('/#/kanban');
  const active = page.locator('.sidebar-link.active');
  await expect(active).toHaveAttribute('data-key', 'kanban');
  const cs = await active.evaluate((el) => { const s = getComputedStyle(el); return { sh: s.boxShadow, l: s.borderLeftWidth, r: s.borderRightWidth }; });
  expect(cs.sh).toBe('none');
  expect(cs.l).toBe(cs.r);
  await expect(page.locator('.sidebar-link[data-key="task"]')).toHaveCount(0);
  await expect(page.locator('.sidebar-footer')).toHaveCount(0);
});

test('Ctrl+K focuses the search field', async ({ page }) => {
  await page.goto('/#/kanban');
  await page.locator('[data-global-search]').waitFor();
  await page.keyboard.press('Control+k');
  await expect(page.locator('[data-global-search]')).toBeFocused();
  const outline = await page.locator('[data-global-search]').evaluate((el) => getComputedStyle(el).outlineStyle + getComputedStyle(el.parentElement).outlineStyle);
  expect(outline).toContain('solid');
});

test('topbar row 1 keeps the same height on every route', async ({ page }) => {
  const heights = new Set();
  for (const h of ['#/dashboard', '#/kanban', '#/issues', '#/ideas', '#/settings']) {
    await page.goto('/' + h);
    await page.locator('#page-title').waitFor();
    heights.add(await page.locator('.topbar-row1').evaluate((el) => el.offsetHeight));
  }
  expect([...heights]).toEqual([56]);
});

test.describe('mobile drawer', () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test('opens with focus inside, closes on Escape, returns focus', async ({ page }) => {
    await page.goto('/#/kanban');
    const burger = page.locator('.topbar-hamburger');
    await expect(burger).toHaveAttribute('aria-expanded', 'false');
    await burger.click();
    await expect(burger).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('.sidebar-link').first()).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(burger).toHaveAttribute('aria-expanded', 'false');
    await expect(burger).toBeFocused();
    await expect(page.locator('.sidebar-collapse-btn')).toBeHidden();
  });
});
```

If the focused search wrapper (not the input) carries the ring, add `.tm-search:focus-within { outline: 2px solid var(--border-focus); outline-offset: 2px; }` to `shell.css` and give the input `outline-style: none` only via that wrapper rule's sibling `.tm-search input:focus-visible { outline-width: 0; }` (width, not `outline: none`).

Run: `npm --prefix viewer run test:mock` — Expected: all PASS. Run: `npm --prefix viewer run test:unit` — Expected: all PASS with `shell.css` enforced.

- [ ] **Step 8: Update pre-existing specs** — `grep -rn "sidebar-link\|sidebar-footer\|data-key=\"task\"\|⌘K\|\.mark" viewer/tests` and fix assertions that depended on the removed Task nav item, footer, or glyph text.

- [ ] **Step 9: Commit** — `feat(viewer): Reality Reprojection shell — two-row topbar, theme toggle, icon set, accessible drawer`

---

### Task 9: Verification sweep

**Files:**
- Create: `viewer/tests/tools/capture.mjs`

- [ ] **Step 1: Port the audit capture script**

Copy `RR/audit-scripts/capture.js` to `viewer/tests/tools/capture.mjs`, converting `require` to `import` (`import { chromium } from '@playwright/test'`, `createRequire` for `axe-core/axe.min.js`), and extend it: `BASE = process.argv[2]`, `OUT = process.argv[3]`; loop `for (const theme of ['dark', 'light'])` setting `localStorage['tm.theme']` via `ctx.addInitScript` and mocking the `GET /api/viewer/prefs` response's `theme` field to match; filenames `${name}.${theme}.${vk}.png`; keep the non-GET block. Keep the header comment and update it.

- [ ] **Step 2: Run it against real data**

```bash
TASKMASTER_ROOT=C:/Users/gruku/Files/Claude/claude-tools C:/Users/gruku/Files/Claude/taskmaster/.venv/Scripts/python -c "from taskmaster.backlog_server import _make_server; s, p = _make_server(host='127.0.0.1', port=8799); s.serve_forever()"
```

(run in the background), then `node viewer/tests/tools/capture.mjs http://127.0.0.1:8799 <scratch>/stage1`, then stop the server.

- [ ] **Step 3: Check the results**

From `metrics.json`, every route × theme × width must satisfy: `shadowCount` only from files not yet converted (zero from `.sidebar`, `.topbar`, `.shell`); `leftRailCount == 0`; `fontFamilies` contains only `DM Sans`, `JetBrains Mono`, `League Spartan` (quotes aside); no console errors. Record the axe `color-contrast` node count per route in the commit message as the stage-1 baseline — it must be lower than the audit's on every route (the audit's numbers are in `docs/specs/2026-10-01-viewer-audit.md`, X-02).

- [ ] **Step 4: Fresh-context visual review**

Dispatch a reviewer agent with: the spec, the audit, the stage1 screenshot folder, and the question "does the shell match spec §3–§4 in both themes; list anything unreadable, misaligned or off-token". Fix what it finds that belongs to this plan's files; list the rest for plan 2/3.

- [ ] **Step 5: Full test run**

```bash
npm --prefix C:/Users/gruku/Files/Claude/taskmaster/viewer run test:unit
npm --prefix C:/Users/gruku/Files/Claude/taskmaster/viewer run test:mock
C:/Users/gruku/Files/Claude/taskmaster/.venv/Scripts/python -m pytest C:/Users/gruku/Files/Claude/taskmaster/tests -k "server or viewer" -q
```

Expected: all PASS.

- [ ] **Step 6: Commit** — `test(viewer): two-theme capture tool; stage-1 baseline`
