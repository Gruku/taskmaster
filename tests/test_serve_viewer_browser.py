# User intent: prove in a real Chromium that the public viewer runs under its strict CSP with zero violations, that hostile text in every rendered field never executes, that the sandboxed file page zooms, and that signing out works, including from a stale tab.
import json
import shutil
from pathlib import Path

import pytest

from taskmaster import backlog_server as bs
from taskmaster.bounded_run import run_bounded
from test_native_service_build import root  # noqa: F401  (fixture)
from test_serve_viewer import (CADDY, PUBLIC, VIEWER_JS, git, login, make_viewer, prefs_put, req,  # noqa: F401
                               session_headers, team, v, work_tree)

PWN = "window.__pwned=1"
CORPUS = [
    f'"><svg onload="{PWN}">',
    f'<img src=x onerror="{PWN}">',
    f"[x](javascript:{PWN})",
    f'<a href="jav&#x09;ascript:{PWN}">x</a>',
    f'<noscript><p title="</noscript><img src=x onerror={PWN}">',
    f"TM-INJECT-7f3a-marker <script>{PWN}</script>",
    f'<a href="data:text/html,<script>{PWN}</script>">d</a>',
    f'<details open ontoggle="{PWN}">t</details>',
    f'<math><mtext><table><mglyph><style><!--</style><img title="--&gt;&lt;img src=1 onerror={PWN}&gt;">',
]
ALL = "\n\n".join(CORPUS)

SCRIPT = """
const path = require('path');
const { chromium } = require(path.join(process.argv[2], 'node_modules', '@playwright', 'test'));
const [,, , base, key, routesJson] = process.argv;
const routes = JSON.parse(routesJson);
(async () => {
  let browser;
  try { browser = await chromium.launch(); } catch (e) { console.log('SKIP ' + e.message.split(String.fromCharCode(10))[0]); return; }
  const out = { views: {}, errors: [] };
  const context = await browser.newContext();
  await context.addInitScript(() => {
    window.__csp = [];
    document.addEventListener('securitypolicyviolation', (e) => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI));
  });
  const page = await context.newPage();
  page.on('pageerror', (e) => out.errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') out.errors.push('console: ' + m.text() + ' @ ' + (m.location().url || '')); });
  async function snapshot(name) {
    out.views[name] = await page.evaluate(() => ({ csp: window.__csp || [], pwned: window.__pwned === undefined ? null : window.__pwned, marker: document.body.innerText.includes('TM-INJECT-7f3a-marker') }));
  }
  await page.goto(base + '/login');
  await page.fill('#token', key);
  await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);
  await page.waitForSelector('.sidebar-link');
  for (const route of routes) {
    await page.evaluate((r) => { location.hash = r; }, route);
    await page.waitForTimeout(1500);
    await snapshot(route);
  }
  // The sandboxed file page: a top-level navigation, scripts and styles from /static/v3/ only, zoom works.
  await page.goto(base + '/file/docs/a.md');
  await page.waitForSelector('#content h1');
  await page.click('#zoom-in');
  out.zoomLabel = await page.textContent('#zoom-label');
  await snapshot('file:/docs/a.md');
  out.fileH1 = await page.textContent('#content h1');
  // Links out of the file page (recorded, not fixed: design Q12).
  await Promise.all([page.waitForNavigation().catch(() => null), page.click('.topbar a[href="/"]')]);
  await page.waitForTimeout(500);
  out.backlogLinkLandedOn = page.url().replace(base, '');
  await page.goto(base + '/');
  await page.waitForSelector('.sidebar-signout');
  // Stale tab: a second sign-in in the same jar, then Sign out from the first tab.
  const second = await context.newPage();
  await second.goto(base + '/');
  await second.waitForSelector('.sidebar-link');
  const again = await context.request.post(base + '/login', { form: { token: key }, headers: { Origin: base }, maxRedirects: 0 });
  out.secondLoginStatus = again.status();
  out.cookiesBefore = (await context.cookies()).map((c) => c.name + '=' + c.value);
  await page.click('.sidebar-signout');
  await page.waitForURL(base + '/login', { timeout: 10000 });
  out.signedOutLanded = page.url().replace(base, '');
  await second.waitForURL(base + '/login', { timeout: 15000 }).then(() => { out.secondTabLanded = '/login'; }).catch(() => { out.secondTabLanded = second.url().replace(base, ''); });
  await browser.close();
  console.log('RESULT ' + JSON.stringify(out));
})().catch((e) => { console.log('FAIL ' + e.stack); process.exit(1); });
"""


def seed(v, cookie, csrf):
    """Hostile text in every field the viewer renders. Epics and tasks go through the tools the team uses; ideas,
    notes and bugs go through the guarded viewer's own write API, as a member (design 028 step 10)."""
    bs.backlog_add_epic(epic_id="xss-epic", name=CORPUS[0], done_when=CORPUS[1], description=ALL)
    bs.backlog_add_task(title=CORPUS[1], epic="xss-epic", phase="dev", tldr=CORPUS[0], notes=ALL, next_step=CORPUS[2])
    headers = {**session_headers(v, cookie, csrf), "Content-Type": "application/json"}
    for path, body in (("/api/ideas", {"title": CORPUS[1], "body": ALL, "tags": ["xss", CORPUS[0]]}),
                       ("/api/notes", {"text": ALL}),
                       ("/api/bugs", {"title": CORPUS[0], "body": ALL, "found_in": CORPUS[1]})):
        resp = req(v, "POST", path, headers=headers, body=json.dumps(body))
        assert resp.status in (200, 201), (path, resp.status, resp.text[:300])


def tracked_md(work_tree):
    (work_tree / "docs" / "a.md").write_text("# File page\n\n" + ALL + "\n", encoding="utf-8")
    git(work_tree, "add", "docs/a.md")
    git(work_tree, "commit", "-q", "-m", "a.md with the corpus")


@pytest.fixture
def node():
    exe = shutil.which("node")
    if exe is None or not (VIEWER_JS / "node_modules" / "@playwright" / "test").exists():
        pytest.skip("node or @playwright/test is not installed (npm ci in viewer/)")
    return exe


def run_browser(node, tmp_path, v, routes, key):
    script = tmp_path / "proof.js"
    script.write_text(SCRIPT, encoding="utf-8")
    result = run_bounded([node, str(script), str(VIEWER_JS), v.loopback, key, json.dumps(routes)], timeout=240)
    out = result.stdout.decode("utf-8", "replace").strip()
    if out.startswith("SKIP"):
        pytest.skip(out)
    assert result.returncode == 0 and "RESULT " in out, (out[-2000:], result.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(out.split("RESULT ", 1)[1])


def test_the_viewer_sweeps_clean_under_its_csp_and_hostile_text_never_runs(make_viewer, team, work_tree, tmp_path, node):
    v = make_viewer(authenticator=team.authenticator)
    key = team.keys["alice"][0]
    tracked_md(work_tree)
    v.clock.now += 31                                                       # the tracked-file list is cached for 30 s
    cookie, csrf = login(v, key)
    seed(v, cookie, csrf)
    # Prefs are project-wide: one member writes corpus strings, every member loads them (M8). Values the API refuses
    # are fine; the ones it stores are rendered in the sweep.
    for theme_value in CORPUS[:3]:
        prefs_put(v, session_headers(v, cookie, csrf), theme=theme_value)
    board = json.loads(req(v, "GET", "/api/board", headers={"Cookie": cookie}).body)
    task_ids = [t["id"] for t in board.get("tasks", [])] or ["test-epic-001"]
    bugs = json.loads(req(v, "GET", "/api/bugs", headers={"Cookie": cookie}).body)
    bug_ids = [b["id"] for b in (bugs.get("bugs", bugs) if isinstance(bugs, dict) else bugs)]
    routes = ["#/dashboard", "#/kanban", "#/table", "#/epics", "#/sessions", "#/issues", "#/bugs", "#/ideas",
              "#/archived", "#/settings", *(f"#/task/{i}" for i in task_ids[:3]), "#/epic/xss-epic",
              *(f"#/bug/{i}" for i in bug_ids[:2])]
    out = run_browser(node, tmp_path, v, routes, key)
    for name, view in out["views"].items():
        assert view["csp"] == [], (name, view["csp"])
        assert view["pwned"] is None, name
    shown = [name for name, view in out["views"].items() if view["marker"]]
    print("views that showed the injected marker as inert text:", shown)
    assert "file:/docs/a.md" in shown and any(n.startswith("#/task/") or n.startswith("#/epic/") or n.startswith("#/bug") for n in shown)
    assert [e for e in out["errors"] if "pageerror" in e] == [], out["errors"]
    assert out["zoomLabel"] == "110%"
    assert out["fileH1"] == "File page"
    # Sign out, including from a stale tab (M1).
    assert out["signedOutLanded"] == "/login"
    assert out["secondTabLanded"] == "/login", out
    assert out["secondLoginStatus"] == 303
    replay = out["cookiesBefore"][0]
    assert req(v, "GET", "/api/board", headers={"Cookie": replay}).status == 401
    # Recorded baseline for design Q12 (links out of the sandboxed file page); not asserted beyond "it landed somewhere".
    print("Q12 baseline: '<- Backlog' from /file/docs/a.md landed on", out["backlogLinkLandedOn"])
    assert out["backlogLinkLandedOn"] in ("/", "/login")
    console_errors = [e for e in out["errors"] if e.startswith("console:")]
    print("console errors:", console_errors)
