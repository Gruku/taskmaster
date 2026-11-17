<!-- User intent: the team server's web viewer goes public at go-live (user ruling Q10, 2026-11-08). This design says
     how it is exposed through Caddy and how every route, header and rendered byte is made safe for the internet,
     folding in follow-up 017 (logout control, sanitized markdown, sandbox-safe zoom), with its own security review. -->

# team-server-028: the public viewer (design and TDD plan, 2026-11-08; rev 2.1, 2026-11-10)

- **Revision:** rev 2 answers the design and security review
  (`docs/handoffs/2026-11-01-team-server-lanes/reviews/team-server-028-design.md`, PASS WITH FIXES: I1-I3, M1-M9) and
  the user's portal hedge (rulings, "Portal proposal"). What changed is listed in "Rev 2 changes" below.
- **Task:** team-server-028, go-live (`progress.json` order 15.9). It absorbs follow-up 017.
- **Worktree:** `.worktrees/team-server-028`, branch `feature/team-server-028`, cut from `feat/team-collab` b948a23a.
- **Ruling:** `docs/handoffs/2026-11-01-team-server-lanes/rulings.md`, "015 open questions", Q10. The viewer is public
  at go-live, exposed through Caddy, with its own security review.
- **Builds on:**
  - 006 D10, the guarded viewer: `taskmaster/serve/viewer.py`, `ViewerGuard`.
  - 016, the deploy files: `deploy/caddy/Caddyfile`, `deploy/systemd/taskmaster.service`,
    `docs/runbooks/team-server-deploy.md`.
  - 025, the matcher ratchet: `config.PUBLIC_PREFIXES` and `tests/test_deploy_files.py`, merged into
    `feat/team-collab` at 9193e12a.
  - 015's review map: B2.3, F3, F4, X6, X8, M10, M13 and S13 in
    `.worktrees/team-server-015/docs/specs/2026-11-08-team-server-015-harness-and-security-review-design.md`.
- **Line numbers** are from b948a23a and were re-checked for rev 2 against `feat/team-collab` 63a747c1: none of the
  cited lines in `serve/viewer.py`, `backlog_server.py` or `viewer/` moved. 025 has merged (9193e12a), so the builder
  rebases onto `feat/team-collab` before Step 1, and §2.3's test edits apply to the merged `test_deploy_files.py`.

## Rev 2 changes

| Review item | Rev 2 answer | Where |
|---|---|---|
| **I1** `Clear-Site-Data: "cookies"` clears the whole registrable domain | Logout sends `Clear-Site-Data: "cache"` only. `"cache"` is origin-scoped, and also evicts back/forward-cache copies of the board. The two host-only expired `Set-Cookie`s remove the session cookie. A test asserts the header is exactly `"cache"`. | §3.4, Step 3 test 3 |
| **I2** static allow-list bypassed by a second decode | One decode, one value. `admit` decodes once and stores the result on the handler; the guarded handler serves static files from that value through a new base hook, `_static_rel`, so the solo second `unquote` never runs in server mode. `static_asset_allowed` also refuses any `%` left after the decode, and `admit` refuses targets that start with `//` (which `urlparse` reads as a host). | §1.2, §1.4, Steps 1-2, §8 probe 2 |
| **I3** h2 fan-out through Caddy over-dials the 32-connection cap | The viewer site's `reverse_proxy` gets `transport http { max_conns_per_host 16; keepalive off }`, so Caddy queues upstream requests instead of dialling a 33rd. A deploy-files test pins it at no more than half of `VIEWER_MAX_CONNECTIONS`, and a new WSL case V9 loads every allow-listed asset at once over h2 and expects no 5xx. | §2.2, §2.3, §5, Steps 12-13, §8 probe 8 |
| **Hedge** auth behind one interface | New `serve/viewer_auth.py`: the `ViewerAuth` protocol and its one implementation, `CookieLogin` (the code that is in `serve/viewer.py` today). The guard asks the interface who the request is and never touches a cookie. A portal adapter in 8.0 replaces `CookieLogin` and nothing else. A test-only header adapter proves it. | §3.6, D12, Step 3 |
| M1 stale-tab sign-out | Fixed: logout needs the allowed Origin and `Sec-Fetch-Site`, no CSRF token; `signOut` checks where it landed. | §3.4, Steps 3, 9 |
| M2 `'self'` covers `/file/*` | Fixed: the app CSP narrows `script-src`, `style-src` and `font-src` to `{origin}/static/v3/`, and a test pins that `/file/` never answers with a script or style MIME type. | §4.2, Step 4 |
| M3 style attributes and the `h({html})` sink | Fixed: `h()` sets `style` through CSSOM (`el.style.cssText`), which fixes `phase-strip.js` and `popover.js` in one place. The `html` key joins the sink audit and the source scan. | §4.1, §4.2, Steps 5, 8 |
| M4 solo editor link unescaped | Fixed: `_md_editor_link` escapes the path with `html.escape(…, quote=True)`. | §4.1 point 5, Step 7 |
| M5 links out of `/file/` land on `/login` | Deferred (Q12). Step 10 records the behaviour. | §9 |
| M6 CSRF header in the Caddy log | Fixed: a `format filter` deletes `X-Taskmaster-Csrf` from the viewer access log. | §2.2, Step 12 |
| M7 V8's expectation | Fixed: V8 asserts no app bytes and no viewer log line, and records Caddy's own status. | Step 13 |
| M8 prefs and the seeding route | Fixed: prefs values join the XSS corpus; fields the viewer API can't write are seeded over MCP as the member. Per-principal prefs is a follow-up. | Step 10, §10 |
| M9 settle M13 and the write scope | Fixed: §1.1 states which tables the viewer reads and the write scope, and Step 1 pins it with markers. | §1.1, Step 1 |
| Batch note | 025 has merged (above). `viewer/js/dev/` holds `edit-demo.js`, which static excludes either way. | §1.3 |
| **N1 (rev 2.1)** one unauthenticated h2 client can hold all 16 Caddy upstream slots by not reading assets larger than Caddy's 32 KiB copy buffer (re-review, `team-server-028-design-rereview.md`) | The viewer `reverse_proxy` gets `response_buffers 512KiB` and `request_buffers 64KiB`, so Caddy drains every allow-listed asset and buffers every login body before a slot is involved. A deploy test checks `response_buffers` against the largest allow-listed file, measured from disk, and `request_buffers` against `VIEWER_LOGIN_MAX_BYTES`. Step 12 mutates both, and the new WSL case V10 stalls 20 streams and expects other requests to answer within 5 s. §5's "timeouts bound the wait" is corrected: no Caddy write timeout exists, because it would cut MCP SSE streams. | §2.2, §2.3, §5, D13, Steps 12-13, §8 probe 8 |

Minors not fixed in 028 are listed with a reason in §10.

## 0. Decisions in one table

| # | Decision |
|---|---|
| D1 | The viewer gets **its own subdomain** (`viewer.tm.example.com`) and its own Caddy site block, not a path prefix on `tm.example.com` (§2). |
| D2 | **Routes are an allow-list in the guard**, mirrored exactly by the Caddy `@viewer` matcher and pinned by a test, the way 025 pins `@taskmaster`. Everything else gets a 404, both from Caddy and from the app (§1, §2.3). |
| D3 | **F3 is closed by removal.** `/backlog.yaml`, `/v3/dev/*`, the `/v3*` and `/index.html` aliases, and `/api/session` give 404 in server mode, with or without a login (§1.3). |
| D4 | **Static files are an allow-list too**: `css/`, `js/` and `vendor/`, by extension, with no `.html`, no `dev`, no `_dormant`, no `*-demo.js`, and no package or test files. The path is decoded once, and the file served is the one checked (§1.2, rev 2 I2). |
| D5 | **Sessions are bound to the origin they logged in on.** The new parts: logout needs no live session and no CSRF token, `Sec-Fetch-Site` must be absent or `same-origin` on writes, on login and on logout, logout sends `Clear-Site-Data: "cache"` (never `"cookies"`), and a global failed-login alarm logs a warning. No per-key lockout (§3). |
| D6 | **One CSP header per response, chosen by response kind.** Main app: scripts, styles and fonts only from `{origin}/static/v3/`, with no `unsafe-inline` and no `unsafe-eval`. `/file/*.md`: sandboxed, with the same script source (§4.2). |
| D7 | **Markdown rendering has one sanitizer with two loaders.** The allow-list sanitizer in `components/markdown.js` moves to a classic, dependency-free `js/lib/md-safe.js` that both the app and the sandboxed `/file/` page load. `/file/` pages stop loading `marked` from jsdelivr (§4.1). |
| D8 | **The inline script and styles leave the pages**: the theme pre-paint script, the login page `<style>`, and the markdown page's `<style>` and `<script>`. Zoom no longer crashes on the sandbox's `localStorage` (§4.1, §4.3). |
| D9 | **Limits stay shared with the daemon.** One per-key limiter, in-flight counter and per-peer failure budget serve MCP, the read API and the viewer. Viewer-only: the 4 KiB login body, the 32-connection cap, and a Caddy body cap of 1 MiB on the viewer site (§5). |
| D10 | **A "Sign out" control** sits in the sidebar footer, shown only in server mode (§3.4). |
| D11 | **`--viewer-origin` must name a host that isn't any `--allowed-host`.** The daemon refuses the path-prefix shape at start (exit 2) (§2.2). |
| D12 | **The viewer's authentication sits behind one interface**, `ViewerAuth` in `serve/viewer_auth.py`. Cookie login is its only implementation in 028; the 8.0 portal's signed-principal login replaces that one class (§3.6). |
| D13 | **Caddy queues viewer requests** at 16 upstream connections (`max_conns_per_host`), below the app's 32-connection cap, so an h2 page load never over-dials. Caddy buffers each viewer response (512 KiB) and request (64 KiB) in full, so a client that stalls can't hold a slot (§2.2, §5; rev 2.1, N1). |

## 1. The public surface inventory (settles item 1)

The viewer listens on `127.0.0.1:7471`, behind `ViewerGuard.admit` (`serve/viewer.py:288`). Its handlers are the solo
`ViewerHandler` (`backlog_server.py:11774`) behind the guarded subclass (`serve/viewer.py:514`). After 028, Caddy
forwards the viewer host to it, and only the paths below.

### 1.1 Routes

The **Auth** column means:

- **none**: served without a session.
- **session**: a live `__Host-tm_session` (https) or `tm_session_<port>` (loopback) cookie, re-authenticated on every
  request (006 D10.2), bound to its login origin (D5), and run under `auth.viewer_principal` (003 Q5).
- **write**: session, plus an allowed `Origin`, `Sec-Fetch-Site` absent or `same-origin`, and `X-Taskmaster-CSRF`.

| Path | Methods | What it exposes | Auth | 028 change |
|---|---|---|---|---|
| `/login` | GET, HEAD | static sign-in form | none | `<style>` moves to `/static/v3/css/login.css` |
| `/login` | POST | creates a session | none (Origin, `Sec-Fetch-Site`, 4 KiB, body deadline; 401 charged) | `Sec-Fetch-Site` rule |
| `/logout` | POST | drops the session if one is live, expires both cookies | none (allowed Origin and `Sec-Fetch-Site` only; no CSRF token, with or without a session) | always 303 `/login`, uncharged; `Clear-Site-Data: "cache"` (§3.4) |
| `/logout` | GET | 303 to `/` | session | none |
| `/favicon.ico` | GET, HEAD | vendored icon | none | none |
| `/static/v3/<asset>` | GET, HEAD | the plugin's own CSS, JS, fonts and icons | none | allow-list (§1.2) |
| `/` | GET, HEAD | `viewer/index.html` with the CSRF meta | session (303 `/login` without one) | the inline theme script goes external |
| `/file/<tracked path>` | GET, HEAD | a git-tracked file; `.md` rendered as a page | session | new template (§4.1), kind-specific CSP (§4.2) |
| `/api/viewer/prefs` | GET | the project's viewer prefs | session | none |
| `/api/task/<id>`, `/detail`, `/related` | GET | task records | session | none |
| `/api/epic/<id>` | GET | epic record | session | none |
| `/api/board`, `/api/backlog` | GET | board and compatibility payloads | session | none |
| `/api/identity` | GET | `{"version"}` (root dropped, 006) | session | none |
| `/api/dashboard/recent-events` | GET | change feed; a `ValueError` text is reflected (F4), and `_send_json` scrubs it | session | none |
| `/api/threads`, `/api/sessions`, `/api/sessions/<id>` | GET | handover-derived session list (not the `sessions` table, see below) | session | none; pinned by Step 1 test 6 (M13) |
| `/api/bugs`, `/api/bugs/<id>`, `/api/issues`, `/api/ideas`, `/api/continuity`, `/api/notes` | GET | record lists | session | none |
| `/api/decisions/<id>`, `/api/handover/<id>` | GET | record bodies | session | none |
| POST `/api/ideas`, `/api/notes`, `/api/notes/<id>/(update\|archive)`, `/api/handover/<id>/status`, `/api/tasks/validate`, `/api/tasks`, `/api/tasks/<id>/archive`, `/api/decisions/<id>/(resolve\|drop)`, `/api/bugs`, `/api/bugs/pattern-scan`, `/api/bugs/promote`, `/api/bugs/<id>/archive`, `/api/bugs/<id>` | POST | coordinator writes as the member (006 D10.10) | write | none |
| PUT `/api/viewer/prefs`, PUT/PATCH `/api/tasks/<id>` | PUT, PATCH | prefs (shared across the team) and task writes | write | none. Note: prefs are project-wide, so one member's write changes everyone's view. That is accepted, since members are trusted to write. |
| OPTIONS anything | OPTIONS | 405, no `Access-Control-*` | none | none |
| any other method | any | 401, 403 or 405 before the base handler | write | none |

**What the viewer reads (015 M13, settled here).** `/api/sessions` and `/api/sessions/<id>` are built from handover
rows (`backlog_server.py:11966-11985`: `list_sessions(_dict_rows(data, "handover"))`), not from the `sessions` table.
`/api/threads`, `/api/continuity` and the board read store records. No viewer reader touches the `sessions`,
`linear_queue` or `projection` tables. Step 1 test 6 pins this with markers, so a later reader that does gets caught.

**Whose writes these are.** Viewer writes run in the daemon process under the daemon's own `SESSION_ID`
(`native_routing/viewer.py:37`, `runtime.open_call(…, bs.current_session())`, with no bound MCP connection). The member
is named only through `auth.viewer_principal(key_id)`. They are not a per-member `<handle>@<nonce>` session. So 015's
M13 comparison and §8 probe 10 cover claim effects too (003 D11 and E7, the 409 path), not just reads: a status move
from the viewer must claim, refuse or release exactly as the same move over MCP by the same member would. 028 doesn't
change this; it states it so the reviewer checks it.

### 1.2 Static assets (`/static/v3/<rel>`, public)

The solo route (`backlog_server.py:11856`) serves **any** file under `viewer/`, including:

- `dev/edit-demo.html` and the `brainstorm-*.html` pages;
- `tests/`, `tools/` and `README.md`;
- `package.json` and `package-lock.json`;
- `node_modules/` wherever `npm ci` was run.

An HTML page served from the viewer origin is same-origin with the app. So a DOM-XSS in any stray page could read the
CSRF meta from `/` and write as the visitor.

In server mode, the new function `serve/viewer.py: static_asset_allowed(rel: str) -> bool` decides. `rel` is the path
after `/static/v3/`, **decoded exactly once** by `admit` (§1.4). It returns true only when all of these hold:

- the first segment is `css`, `js` or `vendor`;
- no segment is empty, `.` or `..`, or starts with `.`; no segment is `_dormant`, `dev`, `node_modules` or `tests`;
  there is no backslash, no NUL and **no `%`** (rev 2, I2: no asset name contains one, so a `%` left after one decode
  can only be an attempt at a second decode);
- the extension is one of `.css`, `.js`, `.woff2`, `.woff`, `.svg`, `.ico` or `.png`;
- the file name doesn't end in `-demo.js`.

Anything else gets a 404, charged when there is no session.

**The file served is the file checked (rev 2, I2).** Today the solo static branch decodes a second time
(`backlog_server.py:11858`, `rel = _unquote(clean_path[len("/static/v3/"):])`, after `clean_path` was already
unquoted at `:11827`). A check on the once-decoded value would then guard a different file than the one served:
`/static/v3/js/%255fdormant/flight-log.js` passes as `js/%5fdormant/…` and is served as `js/_dormant/…`. So:

- The solo branch reads `rel` through a new base hook, `ViewerHandler._static_rel(clean_path) -> str`. The base version
  keeps today's behaviour (`unquote` of the tail), so solo doesn't change.
- The guarded override returns `handler.viewer_path[len("/static/v3/"):]`: the exact value `admit` decoded and
  `static_asset_allowed` checked, with no further decode. `/favicon.ico` maps to `vendor/favicon.ico` the same way.
- The `%` rule above is the second layer: even if a future edit brought a second decode back, a double-encoded name is
  refused before it.

The solo route keeps its second decode; with no allow-list and the traversal check intact, it serves only files
under `viewer/`, which solo serves anyway.

### 1.3 Removed in server mode (404, with or without a session)

| Path | Why | Was |
|---|---|---|
| `/backlog.yaml` | F3. The viewer JS doesn't use it, and it skips the tracked-file rule | served after login |
| `/v3/dev/edit-demo`, `/v3/dev/edit-demo/` | F3. A dev page: the edit-field demos (`js/dev/` holds only `edit-demo.js`, which static excludes) | served after login |
| `/index.html`, `/v3`, `/v3/`, `/v3/index.html` | aliases of `/`. Fewer forms keep the Caddy matcher exact | served after login |
| `/api/session` | F4. It returns the **host's** `current_session()` and session task. No viewer JS calls it (grep of `viewer/js`, `mods/`) | served after login |
| everything not in §1.1 | the route allow-list | 404, or 303 to `/login` before the login |

**What this means for 015:** S13's F3 assertion becomes "404 with and without a login, direct and through Caddy",
instead of "only after login". B2.3 drops `/api/session` and the aliases.

### 1.4 The allow-list in code

These constants and the function go in `serve/viewer.py`, replacing `PUBLIC_PATHS` at `:41`:

```python
PUBLIC_EXACT = ("/favicon.ico", *CookieLogin.PUBLIC_PATHS)   # CookieLogin.PUBLIC_PATHS == ("/login", "/logout") (§3.6)
SESSION_EXACT = ("/",)
ROUTE_PREFIXES = ("/static/v3", "/api", "/file")   # each serves "<prefix>/..." only, never "<prefix>" itself
REFUSED_EXACT = frozenset({"/api/session"})

def route_allowed(path: str) -> bool: ...          # decoded path; exact or under a prefix, minus REFUSED_EXACT
def caddy_matcher_forms() -> list[str]:            # what Caddy's @viewer must list, exactly
    return [*PUBLIC_EXACT, *SESSION_EXACT, *(p + "/*" for p in ROUTE_PREFIXES)]
```

The auth adapter's public paths come from the adapter class, not a second literal, so swapping the adapter (§3.6)
changes the route allow-list and the Caddy parity test together.

**Today `admit` doesn't decode** (`serve/viewer.py:293`, `path = urlparse(handler.path).path`), while the base
`do_GET` does (`backlog_server.py:11827`). In rev 2, `admit` decodes once, with `unquote(urlparse(handler.path).path)`,
and stores the result as `handler.viewer_path`. Every later check runs on that one value: the auth adapter's public
paths, static, the allow-list and `/file/`. The guarded static hook serves from it too (§1.2), so no route checks one
string and serves another. The base handler decodes the same raw target the same single way for every other route,
so its `clean_path` equals `viewer_path` there; a Step 1 test pins that equality.

- The checks run in this order: blocked peer, then Host, then framing (Transfer-Encoding and Content-Length), then
  OPTIONS.
- After that, admit refuses four more things:
  - a request target that doesn't start with `/` (absolute-form): 400;
  - a request target that starts with `//`: 400. `urlparse` reads `//api/board` as host `api` and path `/board`, and
    Caddy's path matcher may collapse the slashes, so the two would disagree about which route this is. Refusing the
    form removes the disagreement instead of relying on it failing closed;
  - a decoded path with NUL, a backslash, or a `..` segment: 400;
  - `not route_allowed(path)`: 404.
- 400 is already charged when there is no session. **404 joins `CHARGED_WITHOUT_SESSION`**, so a path scanner spends
  its failure budget. A logged-in member who mistypes a URL is never charged.
- The route allow-list runs before the session check. A removed path therefore gives the same 404 whether or not a
  session exists, and a scanner learns nothing from 303 versus 404.

## 2. Routing through Caddy (settles item 2)

### 2.1 Subdomain, not a path prefix

**Recommendation: `viewer.tm.example.com`, its own site block.** Why a prefix doesn't work:

- **The viewer is root-relative throughout.** `index.html`'s rewrites (`backlog_server.py:11839`), every `fetch('/api/…')`
  in `viewer/js/api.js`, `/file/` links, `/login`, `/logout`, `location.assign('/login')` in `csrf.js`, and the
  `__Host-` cookie's mandatory `Path=/` all assume it. A prefix means rewriting all of them, or a Caddy `uri strip_prefix`
  that the app can't see, which breaks Origin and redirect logic.
- **The two surfaces stay apart.** A separate origin keeps viewer cookies, CSP and pages off the MCP host, and keeps
  bearer-key traffic off the cookie host. The two sites share no path, so 025's `@taskmaster` matcher and
  `PUBLIC_PREFIXES` don't change.
- **006 and 016 already assumed it.** 006 §9 and the commented block at `Caddyfile:49-62` name
  `viewer.tm.example.com`.

The cost is one more DNS record and one more ACME certificate. Certificate Transparency logs publish the name, which
is accepted: the name is no secret, and the login is the control.

`tm.example.com` and `viewer.tm.example.com` are **same-site**, so `SameSite=Strict` doesn't separate them. That
holds, because `tm.example.com` serves no HTML: only MCP and JSON, and 404 from Caddy. The Origin check names the
viewer origin exactly, so a write from `https://tm.example.com` gets 403. The `__Host-` prefix stops a sibling host
from planting the session cookie, and D5's origin binding makes a planted loopback cookie useless on the public Host.

### 2.2 The site block, the unit flag and the config check

`deploy/caddy/Caddyfile` replaces the commented block at `:49-62` with:

```
viewer.tm.example.com {
	tls {
		issuer acme {
			disable_http_challenge
		}
	}
	request_body {
		max_size 1MiB
	}
	header Strict-Transport-Security "max-age=31536000"
	header -Server
	log {
		output file /var/log/caddy/taskmaster-viewer-access.log {
			roll_interval 24h
			roll_keep 14
			roll_keep_for 336h
		}
		format filter {
			request>headers>X-Taskmaster-Csrf delete
		}
	}
	@viewer path / /login /logout /favicon.ico /static/v3/* /api/* /file/*
	handle @viewer {
		reverse_proxy 127.0.0.1:7471 {
			request_buffers 64KiB
			response_buffers 512KiB
			transport http {
				max_conns_per_host 16
				keepalive off
			}
		}
	}
	handle {
		respond 404
	}
}
```

- **`max_conns_per_host 16` (rev 2, I3).** The browser speaks h2 to Caddy (global `protocols h1 h2`) and multiplexes a
  page load on one connection: `viewer/index.html` has 38 stylesheets in its `<head>`, plus fonts and the module
  graph (127 files under `viewer/js`). The stdlib viewer speaks HTTP/1.0 (`ViewerHandler` sets no
  `protocol_version`) and closes after every response, so Caddy dials one upstream connection per request.
  `GuardedViewerServer.process_request` (`serve/viewer.py:628-631`) closes any connection beyond
  `VIEWER_MAX_CONNECTIONS` (32) at accept, with no answer, and Caddy turns that into a 502. One missing stylesheet or
  module leaves a blank or unstyled app. With the cap, Go's transport makes request 17 wait for a free connection
  instead of dialling. 16 leaves the other 16 app slots for `ssh -L` operator use and loopback mods. Static answers
  are file reads of a few ms, so the queue costs little, provided a slot is freed as soon as the app has answered.
  The two buffers below make sure of that.
- **`response_buffers 512KiB` and `request_buffers 64KiB` (rev 2.1, N1).** Without them the 16 slots are a shared
  resource one unauthenticated client can hold forever. Go's transport counts an upstream connection against
  `max_conns_per_host` until the response body reaches EOF, and Caddy copies the body to the client through a 32 KiB
  buffer. An h2 client that opens 16 streams for an asset larger than that buffer and never sends `WINDOW_UPDATE`
  blocks every copy, so no upstream body is drained and every other viewer request queues behind them. Three
  allow-listed, pre-login assets are larger than 32 KiB: `vendor/marked.min.js` (35,485 bytes),
  `vendor/fonts/JetBrainsMono-Variable.woff2` (40,404) and `vendor/fonts/DMSans-Variable.woff2` (62,724). Nothing
  bounds the stall: the global `servers` block sets only `read_header` and `idle`, and `idle` doesn't apply while
  streams are open. The app sees only finished 200s and charges nothing.
  - `response_buffers` makes Caddy read up to that many bytes of the upstream body before writing to the client. Every
    allow-listed asset fits, so Caddy reads it to EOF, the upstream connection is released, and a stalled client holds
    Caddy memory, not an upstream slot. 512 KiB is about 8 times the largest asset today. A deploy test computes the
    largest asset from the files (§2.3), so an asset that grows past the buffer fails the build rather than reopening
    the stall.
  - `request_buffers` makes Caddy read up to that many bytes of the request body before dialling the upstream. A login
    body (at most `VIEWER_LOGIN_MAX_BYTES`, 4 KiB) and every normal viewer write fit, so a slow upload, held for up to
    the app's 30 s body deadline, holds a Caddy buffer and not one of the 16 slots. Rev 2 halved the number of slow
    uploads needed to fill the app's slots; this removes that path through Caddy.
  - **What stays (accepted).** An authenticated `/api/*` answer larger than 512 KiB, or a member write larger than
    64 KiB, can still hold a slot while its client stalls. That needs a member key, and it is rate-limited and charged
    per key (§5). The memory a stalled client can pin is at most one asset per open stream, bounded by Caddy's h2
    stream limit per connection; a flood of connections is the network-level denial of service §5 already places out
    of scope.
  - **No second layer, and why.** A server-level `write` timeout would bound the stall, but both sites share Caddy's
    `:443` server, and a write timeout cuts the MCP site's long SSE streams; 016 forbids it, and
    `test_caddy_timeouts_match_the_daemon` pins that. A lower h2 per-connection stream limit wouldn't help: one client
    can open more connections. The buffers fix the cause, so neither is added.
- **`keepalive off`.** It states what the HTTP/1.0 upstream forces anyway: no upstream connection is reused, so none
  goes stale. V7 still pins it.
- **The log filter (M6).** Caddy's access log records request headers and redacts only `Cookie`, `Set-Cookie` and
  `Authorization` by default. The filter drops the CSRF header (Go's canonical spelling, `X-Taskmaster-Csrf`), so §8
  probe 9's "no tokens in any log" holds. The token is worthless without the cookie, but the rule is simpler with no
  exception. `caddy validate` (Step 13) proves the syntax on the deployed Caddy version.
- **`header -Server`** is deferred automatically, because it deletes, so it also strips the stdlib's
  `BaseHTTP/… Python/…` banner. The app strips it too (§4.3).
- **The header comment** gets one line naming 028 (015 B1 asks for that).
- **The unit** (`deploy/systemd/taskmaster.service:20-22`) adds `--viewer-origin https://viewer.tm.example.com`. The
  placeholder comment at `:4` names both hosts.
- **The config check:** `serve/config.py: validate` (near `:166`) refuses a `--viewer-origin` whose host equals the
  host of any `--allowed-host`, with this text:
  `--viewer-origin <o> must be its own host (e.g. viewer.<domain>), not an --allowed-host`. That refuses the path-prefix
  shape and the shared-cookie host at start, with exit 2. Loopback origins are exempt.

### 2.3 How it fits 025's matcher ratchet

025's tests (`tests/test_deploy_files.py` on `feature/team-server-025`) assume a single site. Some uses of `one(…)`
would now match twice: `reverse_proxy`, `max_size`, the TLS block in the smoke render, and the log path. The rules
written for one site carry over, applied to each site.

- **A shared parser, `tests/caddyfile.py`, holds one copy of each rule.** It provides:
  - `sites(text) -> dict[str, str]`: the top-level site blocks by brace depth, skipping the global `{…}`;
  - `directive(body, name) -> list[str]`;
  - `matcher(body, name) -> list[str]`;
  - `upstreams(body) -> list[str]`.

  015's Step 9 Caddyfile check (P-028) imports this module instead of writing a second parser.
- **What changes in `test_deploy_files.py`:**
  - `test_caddy_proxies_only_the_public_prefixes_to_the_daemon` runs on `sites(caddy)[ALLOWED_HOST]`. Its
    assertions don't change: one upstream `127.0.0.1:7470`, `@taskmaster` equal to `PUBLIC_PREFIXES` forms, and
    `respond 404`.
  - New: `test_the_viewer_site_proxies_only_the_viewer_routes`. The viewer site has exactly one upstream,
    `127.0.0.1:{config.DEFAULT_VIEWER_PORT}`, and `sorted(matcher(body, "@viewer")) ==
    sorted(viewer.caddy_matcher_forms())`. It has a `handle { respond 404 }`.
  - New (rev 2, I3): `test_the_viewer_upstream_queues_below_the_apps_connection_cap`. The viewer site's
    `reverse_proxy` has a `transport http` block with `keepalive off` and `max_conns_per_host N`, where
    `0 < N <= config.VIEWER_MAX_CONNECTIONS // 2`. The shared parser gains `subdirective(body, path) -> list[str]` for
    the nested read.
  - New (rev 2.1, N1): `test_the_viewer_proxy_buffers_every_static_asset_and_the_login_body`. The viewer site's
    `reverse_proxy` has exactly one `response_buffers` and one `request_buffers`. `caddy_size(response_buffers)` is at
    least the size of the largest file under `viewer/` that `static_asset_allowed` admits, **computed by walking the
    files in the test**, not from a literal, and the test asserts that walk found the three known large assets so an
    empty walk can't pass. `caddy_size(request_buffers) >= config.VIEWER_LOGIN_MAX_BYTES`. Neither appears on the
    daemon site (the MCP site streams SSE, and buffering it would break streaming).
  - New (rev 2, M6): `test_the_viewer_log_drops_the_csrf_header`. The viewer site's `log` block has a `format filter`
    with `request>headers>X-Taskmaster-Csrf delete`.
  - New: `test_each_site_has_tls_alpn_hsts_and_no_server_header`. In each site: `disable_http_challenge`, HSTS, and
    `header -Server`.
  - New: `test_the_two_sites_share_no_upstream_and_no_path`. 7470 appears only in the daemon site and 7471 only in the
    viewer site. No `@viewer` form falls under a `PUBLIC_PREFIXES` prefix, and no `@taskmaster` form falls under a
    viewer route.
  - New: `test_every_viewer_route_sits_under_the_matcher`. Every path in the §1.1 inventory, written as concrete
    samples in the test (`/api/board`, `/file/docs/a.md`, `/static/v3/js/main.js`, …), satisfies both `route_allowed`
    and a Caddy-path-semantics check against `caddy_matcher_forms()`. Every §1.3 path fails both.
  - `test_caddy_body_cap_sits_above_the_daemons` runs on the daemon site. A new twin asserts the viewer site's
    `max_size` ≤ `config.DEFAULT_MAX_BODY_BYTES`, so Caddy refuses an oversize viewer body before the stdlib server.
    Its early 413 isn't lingered (006 deviation 11).
  - `test_the_unit_drains_a_refused_body_as_far_as_caddy_can_send` reads the daemon site's `max_size`.
  - `test_caddy_timeouts_match_the_daemon` reads `keepalive` from the daemon site only (rev 2: the viewer site's
    `keepalive off` would otherwise make `one(…)` match twice). `read_header` stays global, and the "no SSE-cutting
    timeouts" scan still runs on the whole file.
  - `FORBIDDEN_CADDY` drops `r"\breverse_proxy\s+\S*:7471\b"`, and the self-test list drops the matching line. The
    shared-upstream test replaces them.
  - `test_exec_start_binds_loopback_on_the_default_ports`: `serve_args.viewer_origin ==
    ["https://viewer.tm.example.com"]`.
  - `test_the_unit_and_the_caddyfile_name_the_same_domain`: `list(sites(caddy)) == [*serve_args.allowed_host,
    *(urlparse(o).hostname for o in serve_args.viewer_origin)]`.
- **What changes in `test_serve_deploy_smoke.py`:**
  - `render_caddyfile`: `_once` gains `count: int = 1`. The TLS-block regex is replaced with `count=2`. The viewer site
    name, the viewer log path and `reverse_proxy 127.0.0.1:7471` are each replaced once.
  - `World.start_daemon` passes `--viewer-origin https://viewer.tm.localhost:<https_port>` and reads the viewer port
    from the ready line's `viewer_url` (`daemon.py:532`).
  - Case 10's parametrize list gains `/login` on the viewer host as an expected **pass**, while `/login` on the MCP host
    stays 404. The viewer cases are listed in §8.

### 2.4 Host, X-Forwarded-For and TLS

- **Host.**
  - Caddy forwards the client's `Host` unchanged; any `header_up Host` is still forbidden (016 list).
  - `ViewerGuard` accepts only the `host[:port]` of the allowed origins: `https://viewer.tm.example.com` gives
    `viewer.tm.example.com` and `viewer.tm.example.com:443`, plus the loopback pair. Anything else gets 421
    (`serve/viewer.py:300`). That blocks DNS rebinding.
  - An unknown SNI never reaches the viewer: Caddy has no certificate for it.
- **X-Forwarded-For.** Caddy has no `trusted_proxies`, so it drops the client's `X-Forwarded-For` and sets its own.
  `ViewerGuard.peer` (`:208`) takes the right-most entry when the TCP peer is loopback, which is always the real
  client. The failure budget keys on it, IPv6 on its /64 (006 D6). A host-local user can still forge it on
  `127.0.0.1:7471` directly (006 G4, accepted). V3 pins the Caddy behaviour.
- **The operator token.** Caddy always adds `X-Forwarded-For`, `-Proto` and `-Host`, so `auth.is_host_local` is false
  for every request through Caddy. The operator token's login gets 403 with 002's text (`:461`). A loopback-issued
  operator session gets 403 on every public request (`:336`). No session through Caddy can be an operator session.
  V2 pins it.
- **TLS.** TLS-ALPN-01 on 443 for the new name too (`disable_http_challenge`). Port 80 stays closed (016 I3). HSTS
  `max-age=31536000` is set by Caddy per site, without `includeSubDomains` (the parent domain isn't ours to pin). The
  app never sets HSTS: it doesn't terminate TLS.

## 3. Auth hardening (settles item 3)

### 3.1 Login rate limits and lockout

- **Keys can't be guessed online.**
  - A member key is `tm_u_<16 hex>_<token_urlsafe(32)>`, about 256 random bits (`serve/principals.py:96`). The
    operator token is the same (`serve/auth.py:80`).
  - At 20 failures per minute per peer, guessing is out of reach even from a large botnet. The failure budget is for
    noise and CPU, not secrecy.
  - It is **the same `failure_budget()` instance** as MCP's (`daemon.py:369`, `:374`, `:416`), so failures on either
    surface spend one budget per peer.
- **No per-key or per-account lockout.** There is no username: the form takes the whole key. A lockout keyed on the
  embedded key id would let anyone who knows a member's 16-hex id (shown in logs and change records) lock that member
  out by sending `tm_u_<id>_garbage`. A lockout is a denial-of-service lever, and buys nothing against 256-bit keys.
- **New: a global failed-login alarm.**
  - `LOGIN_ALARM_PER_MINUTE = 100` (in `serve/config.py`) feeds a process-wide `KeyedLimiter(100, 100/60)` under the
    single key `"*"`, charged on every 401 from `POST /login`.
  - When it empties, one WARNING: `viewer: more than 100 failed logins in the last minute`. There is at most one such
    line per 60 s, tracked by a last-warned timestamp. It never refuses a request, because a botnet could otherwise
    lock everyone out.
  - The line is for the operator's journald watch. The runbook says what to do: check the access log's peers, and
    rotate a key only on evidence that it leaked (028 gate M6: the runbook and this section now agree).
- **Unchanged:** a refused login answers 401 with the fixed text and is charged (`:457-460`). A good login spends the
  key's in-flight slot and a rate token (`:465-480`).

### 3.2 Session cookie, session binding and expiry

- **Cookie flags on the public path:**
  `__Host-tm_session=<sid>; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=86400` (`:482`), chosen because the
  login Origin is https. This stays as it is. Loopback keeps `tm_session_<port>` without `Secure` (006 D10.2).
- **New: the session is bound to its login origin.** This is cookie-login behaviour, so it lives in
  `CookieLogin.identify` (§3.6), not in the guard.
  - `ViewerSession` (`:59`) gains `origin: str`, the allowed origin the login's `Origin` matched.
  - The session lookup (today `ViewerGuard._session`, `:237`; after §3.6, `CookieLogin.identify`) treats a session as
    absent unless the request's Host is one of `_origin_host(session.origin)`.
  - A cookie value lifted from the loopback origin, from another local listener (006 G8) or from a log is therefore
    worthless on the public Host, and the reverse holds too. An operator session is already re-checked per request;
    this extends the rule to every session.
- **Expiry is unchanged:**
  - idle 8 h, absolute 24 h, at most 8 per key and 256 in all; a daemon restart signs everyone out;
  - re-authentication on every request, so revocation and rotation end a session on its next request;
  - the session id is `token_urlsafe(32)`, stored as `sha256`; a new id at every login, so there's no fixation (an
    unknown id is just no session).
  - The board's 3 s poll keeps an open tab's session alive until the 24 h cap, which is accepted (see Q5).

### 3.3 CSRF

- **Kept from 006:**
  - the Origin must equal an allowed origin (a missing Origin and `null` are refused);
  - a per-session `X-Taskmaster-CSRF` token, compared with `hmac.compare_digest`;
  - no CORS; OPTIONS answers 405;
  - `SameSite=Strict`;
  - login CSRF is blocked by the Origin check.
- **New: `Sec-Fetch-Site` must be absent or `same-origin`**, on every write, on `POST /login` and on
  `POST /logout`. Today only
  `cross-site` is refused (`:354`). The change also refuses `same-site`, which is a sibling such as `tm.example.com`,
  and `none`. Old browsers that don't send the header still pass on the Origin check.
- **What the CSRF token is worth to an attacker:** it lives only in the no-store `index.html` meta tag. A sandboxed
  `/file/` page has an opaque origin and can't read `/`. CSP bars any injected script from loading.

### 3.4 Logout

- **Server side:**
  - **`POST /logout` needs only the allowed `Origin` and `Sec-Fetch-Site` absent or `same-origin`**, with or without a
    live session, and no CSRF token (rev 2, M1). If the cookie names a live session, that session is dropped. The
    answer is always 303 to `/login` with both expired cookies, and it is never charged. Today a sessionless logout is
    a charged 401 (`:332`), and a logout with a live session needs the token.
  - Why no token: logout forgery can only sign someone out, and the Origin and `Sec-Fetch-Site` checks already refuse
    every cross-site and same-site sender. Requiring the token broke a real case: a user signs in again in a second
    tab (the cookie now names session B), clicks Sign out in the old tab (whose meta holds session A's token), gets
    403, and stays signed in on a shared machine while believing they signed out.
  - **The answer adds `Clear-Site-Data: "cache"`, and never `"cookies"` or `"*"`** (rev 2, I1). The `"cookies"`
    directive clears cookies for the response's whole registrable domain, subdomains included. On
    `viewer.tm.acme.com` it would sign the member out of everything on `acme.com`: SSO, mail, the intranet. The two
    expired `Set-Cookie` headers (`:502-505`) already remove the session cookie, and they are host-only. `"cache"` is
    origin-scoped; it drops the viewer's cached responses and back/forward-cache copies of the board, so Back after
    sign-out doesn't show it. Browsers apply it on https and on localhost.
  - Replaying a logged-out cookie gets 401 or 303: the session is dropped server-side (`:501`).
- **UI side (017):** the sidebar gets a footer control, "Sign out", rendered only in server mode.
  - The page carries the auth adapter's sign-in and sign-out locations next to the CSRF token (§3.6):
    `<meta name="taskmaster-sign-in" content="/login">` and `<meta name="taskmaster-sign-out" content="/logout">`.
    `csrf.js` reads them instead of hard-coding `/login` (today `:23`) and `/logout`, so the JS needs no change when the
    adapter changes.
  - `viewer/js/lib/csrf.js` gains `serverMode(doc) -> bool` (true when the sign-out meta is present) and
    `signOut(win)`. `signOut` runs `await win.fetch(signOutUrl, { method: 'POST' })` through the wrapper; the 303 is
    followed and sets the expired cookies. If the final response URL is the sign-in location, it runs
    `win.location.assign(signInUrl)`. Otherwise it doesn't navigate: the footer shows "Sign out failed. Reload and try
    again." in an `aria-live` region, so a failed sign-out is never shown as a success.
  - `viewer/js/components/sidebar.js: mountSidebar` appends the footer after the nav. It uses the Reality Reprojection
    ghost button (`btn btn--ghost btn--sm`): no accent rail, no hover motion, no shadow. **The builder runs the
    `reality-reprojection:apply` skill before this step**, per the user's design-system rule. In solo mode, nothing
    renders.

### 3.5 The bootstrap and operator token over the public path

It is refused at login (403, 002 D7 text, uncharged, WARNING log) and on every request of a session made elsewhere
(403). The reason is §2.4: Caddy's forwarding headers make `is_host_local` false. The new origin binding (§3.2)
refuses the loopback cookie on the public Host before the operator check runs. Both layers are tested (V2, Step 3).

The viewer never reads `Authorization`. A request with `Authorization: Bearer <any key>` and no cookie gets 303 or
401 like any other. Step 3 pins this, so nobody later adds a bearer path that bypasses CSRF.

### 3.6 Authentication behind one interface (D12, the portal hedge)

**Why.** The user's ruling (2026-11-09, "Portal proposal"): go-live ships standalone, and Taskmaster 8.0 adds a
"behind the portal" mode in which a shared Rust portal signs users in and asserts the principal to Taskmaster. The
hedge for 028 is that swapping cookie login for the portal's signed principal is a small, local change. Today the
cookie logic is spread through `ViewerGuard`: `_cookie_sids`, `_session`, `_has_session`, `_reauthenticate`, `login`,
`logout`, the `/login` and `/logout` branches of `admit`, the CSRF compare, `SessionStore`, `ViewerSession`,
`LOGIN_PAGE`, the cookie names and `CSRF_META`. A portal swap would touch most of `admit`.

**The interface.** A new module, `taskmaster/serve/viewer_auth.py`, holds it and its one 028 implementation:

```python
@dataclass(frozen=True)
class ViewerIdentity:
    principal: Principal        # serve.principals.Principal: the same type MCP authenticates to
    csrf: str | None            # the token writes must echo, or None when the adapter needs none
    ref: object                 # adapter-private (the cookie session id); the guard never reads it

class ViewerAuth(Protocol):
    PUBLIC_PATHS: tuple[str, ...]          # exact paths the adapter answers before sign-in; feeds route_allowed (§1.4)
    def identify(self, handler) -> ViewerIdentity | None: ...
        # Who is this request? Re-validated on every request: expiry, revocation, rotation, origin binding.
    def answer_public(self, handler, path: str, peer: str, reply: "Reply") -> None: ...
        # Answers the adapter's own PUBLIC_PATHS (the login page, the login POST, logout).
    def sign_in_location(self, handler) -> str: ...
        # Where an anonymous page request is sent (303), and what the page meta names.
    def check_forgery(self, handler, identity: ViewerIdentity) -> str | None: ...
        # The adapter's anti-forgery check on a write, after the guard's Origin and Sec-Fetch-Site checks.
        # Returns refusal text, or None to admit.
    def page_meta(self, identity: ViewerIdentity) -> str: ...
        # The <meta> tags index.html gets: the CSRF token, and the sign-in and sign-out locations (§3.4).
    def has_identity(self, handler) -> bool: ...
        # Cheap, no touch: decides whether a refusal is charged (CHARGED_WITHOUT_SESSION).
```

`Reply` is a small façade over the guard's own `_refuse`, `_redirect` and `_write`, so an adapter's answers are logged,
charged and given headers in one place, the guard's.

**Who calls it.** Only two places:

- `ViewerGuard.admit`: `answer_public` for the adapter's public paths; `identify` for everything else; a `None`
  identity gives 401 on `/api/*` and on writes, or 303 to `sign_in_location` on pages; `check_forgery` on writes;
  `has_identity` in `_refuse`'s charging rule.
- `GuardedViewerHandler._index_head_extra`: `page_meta`.

`route_allowed` and `caddy_matcher_forms` read `PUBLIC_PATHS` from the adapter class (§1.4).

**What stays outside it, unchanged by any adapter:** Host and framing checks, the route and static allow-lists, the
Origin and `Sec-Fetch-Site` check on writes, the operator host-local check on the identified principal, the per-key
rate limit and in-flight slot keyed on `principal.key_id`, `auth.viewer_principal(key_id)` around every handler,
response headers and CSP, the sanitizer, and the failure budget.

**028's implementation, `CookieLogin`.** It is the existing code, moved out of `serve/viewer.py` and not rewritten:
`SessionStore`, `ViewerSession`, `LOGIN_PAGE`, the cookie names, `CSRF_META`, `_cookie_sids`, the session lookup,
`_reauthenticate`, `login`, `logout` and `_key_from_body`. On top of the move come 028's own changes: the origin
binding (§3.2), the logout rules (§3.4), the failed-login alarm (§3.1) and the sign-in and sign-out meta.
`CookieLogin` gets the same `authenticator`, `principals` limiter and `inflight` counter the guard has today (they
are already shared instances), so a login still spends the key's rate token and in-flight slot. `serve/viewer.py`
re-exports the moved names that tests import, so the move doesn't rewrite test imports.

**What a portal adapter replaces (8.0, not built here).** A `PortalAssertion` class implementing `ViewerAuth`:

- `identify` verifies the portal's signed principal assertion header: its signature against the portal's key,
  audience (this instance), expiry and replay window, and that the TCP peer is the portal. It resolves the key id to
  a `Principal` through the principals registry. It holds no member key material.
- `PUBLIC_PATHS` is empty: the portal owns sign-in and sign-out.
- `sign_in_location` names the portal's sign-in URL, with a return path.
- `check_forgery` is whatever the portal's session needs (its own token, or None if the portal enforces it).
- `page_meta` names the portal's sign-out.

Swapping is one constructor argument in `daemon.py` where the guard is built, plus that class. The guard, the
handlers, the JS (which reads the locations from the meta) and the tests of everything in the "stays outside" list
don't change.

**What the hedge doesn't cover**, so nobody thinks the swap is the whole 8.0 job:

- **The path prefix.** Portal mode puts the viewer under a prefix. The viewer is root-relative throughout (§2.1), so
  that is its own change (URL rewriting in `index.html`, `api.js`, `/file/` links, the Caddy or portal routes).
- **A shared origin.** 028's safety leans on the viewer having its own origin: same-origin pages can read the CSRF
  meta from `/`, and `script-src {origin}/static/v3/` only narrows by path. If the portal serves Lightvault and
  Taskmaster on one origin, each app's pages can script the other. That is one of the clash points already sent to
  the Lightvault session, and it needs its own decision in 8.0. The interface doesn't decide it.
- **Principal lookup by key id.** `authenticate(key)` takes the whole key today; the portal adapter needs a
  `by_key_id` lookup in `serve/principals.py`.

**How the hedge is proved now (Step 3).** A test-only adapter, `HeaderAuth`, trusts an `X-Test-Principal` header
and has no public paths. With it, Step 1's route table, Step 4's header tests and the operator and limiter checks
pass unchanged through the guard. A source check asserts `serve/viewer.py` has no `Cookie`, `SimpleCookie`,
`Set-Cookie`, `__Host-` or `CSRF_META` after the move. If the guard grows cookie knowledge again, that test fails.

## 4. Output safety (settles item 4)

### 4.1 Sanitized markdown: one sanitizer, two loaders (D7, follow-up 017)

The app's markdown path is already safe: `viewer/js/components/markdown.js` runs `marked` (vendored v12.0.2), then an
allow-list sanitizer, and every `innerHTML` sink gets record text only through `renderMarkdown`. The others are
constant or escaped markup: `detail-page.js:82`, `right-rail.js:136`, `md-field.js:17`, `card.js:35`,
`task-detail-document.js:323`, `icon.js:50` and `sidebar.js:44`. One more sink is indirect (rev 2, M3):
`viewer/js/util/h.js:13`, where `h(tag, { html })` sets `innerHTML`. Its only caller today is `md-field.js:17`, which
passes `renderMarkdown` output. It joins the audit list, Step 5's source scan and §8 probe 6. The `/file/*.md` page is
not safe:

- `_MD_TEMPLATE` (`backlog_server.py:12800`) loads **`https://cdn.jsdelivr.net/npm/marked/marked.min.js`**. That URL is
  unpinned (any future marked) and remote (a supply-chain hop). The page assigns `marked.parse(raw)` straight to
  `innerHTML` with **no sanitizer**. 006's sandbox CSP (`serve/viewer.py:50`) only contains the damage.
- Its zoom reads `localStorage` before it adds listeners. A sandboxed page has an opaque origin, so `localStorage`
  throws `SecurityError` and the zoom buttons never work (follow-up 017).
- It carries an inline `<style>` and `<script>`, which a strict CSP forbids, and a blockquote accent rail, which the
  user's UI rules forbid.

The design:

1. **`viewer/js/lib/md-safe.js` (new).** A classic, dependency-free script with no `import` and no `export`. It sets
   `globalThis.TaskmasterMarkdown = Object.freeze({ render(src), isSafeUrl(url) })` and holds the whole current body
   of `markdown.js`: `ALLOWED_TAGS`, `DROPPED_TAGS`, `ALLOWED_ATTRS`, `sanitise`, the double-parse check and
   `markTasks`. A classic script is the only form a sandboxed, opaque-origin page can load from the viewer origin: a
   module script is a CORS fetch, and the viewer must never send CORS headers. It reads `window.marked` at call time.
2. **`viewer/js/components/markdown.js`** becomes the module face of the same code:
   `import '../lib/md-safe.js';`, then `renderMarkdown`, `mountMarkdown` and `isSafeUrl` delegate to
   `globalThis.TaskmasterMarkdown`. Its importers don't change. **Reuse, not a fork:** one sanitizer, and
   `tests/unit/markdown-safety.test.js` keeps testing it through `markdown.js`. A new test evaluates `md-safe.js`
   classically (`dom.window.eval`, as that test already does for `marked`) and checks the same corpus gives the same
   output.
3. **`_MD_TEMPLATE`** (one template, solo and server) is rewritten with no inline code. It loads:
   - `/static/v3/css/tokens.css`, `/static/v3/css/components/markdown.css` and the new
     `/static/v3/css/file-page.css`, which holds the old inline rules on Reality Reprojection tokens, with no
     blockquote rail and no hover motion;
   - `/static/v3/vendor/marked.min.js`, `/static/v3/js/lib/md-safe.js` and the new `/static/v3/js/file-page.js`.

   The content is `<main id="content" class="md-body" data-b64="{{B64CONTENT}}"></main>`. Base64 is attribute-safe.
   The placeholders become `{{TITLE}}`, `{{B64CONTENT}}` and `{{EDITOR_LINK}}`.
4. **`viewer/js/file-page.js` (new, classic).**
   - It decodes `data-b64` with `atob` and `TextDecoder('utf-8')`, then sets
     `content.innerHTML = TaskmasterMarkdown.render(text)`.
   - It wires the zoom with storage behind `try`/`catch`: read once, write on change, and keep the value in memory
     when storage throws. So zoom works in the sandbox, without persisting there.
   - If `TaskmasterMarkdown` or `marked` is missing, it shows the text in an escaped `<pre>`. That matches
     `markdown.js`'s fallback.
5. **The editor link.** A new hook, `ViewerHandler._md_editor_link(resolved) -> str`. The solo version returns
   `<a href="vscode://file/{html.escape(self._md_full_path(resolved), quote=True)}" class="open-editor">Open in VS
   Code</a>`, which keeps the solo behaviour. The escape is new (rev 2, M4): the solo `_md_full_path` returns the raw
   path (`backlog_server.py:11811-11812`), so on POSIX a tracked file named `a"><img src=x onerror=…>.md` would inject
   markup into the solo page, which has no CSP. The guarded override returns `""`: in server mode a `vscode://` link to a host path means nothing,
   and it would put a host path in the page. The guarded `_md_full_path` override (`serve/viewer.py:541`) becomes
   unused and is deleted.
6. **Solo also stops loading jsdelivr.** This touches the solo template on purpose (Q6). One template is better than
   two, and an unpinned CDN script is a risk in solo mode too.

**Two other script sinks get tightened at the same time, because they are in the viewer code this task touches:**

- `viewer/js/components/card.js:174`: `window.open(docs[0], '_blank', 'noopener')` opens a teammate-supplied
  `task.docs` value. A `javascript:` value would run in the opened window, and CSP blocks that only on the
  CSP-covered origin. The fix opens only when `isSafeUrl(docs[0])` is true; otherwise the button doesn't render.
- `viewer/js/components/budget-meter.js:15-21` builds `style="width:…"` and an unescaped `${label}` into `innerHTML`.
  It has no importer today, but under a strict `style-src` it would render broken, and `label` is a sink. It switches
  to `h()` with `textContent` and sets the width through CSSOM (`el.style.width`), which CSP allows.

**Style attributes set through `h()` (rev 2, M3).** `h()` sets every unknown key with `setAttribute`
(`util/h.js:14`), and a `style` attribute set that way is blocked by a strict `style-src`. Two live components do it:
`phase-strip.js:32` (`h('span', { style: \`width: ${pct}%\` })`, the current phase's bar) and `popover.js:30`
(`style: 'position: fixed'`). Both would break. The fix is in `h()` itself, one place: a `style` key is applied as
`el.style.cssText = v`, which is CSSOM and allowed. The two callers don't change, and no future caller of
`h({ style })` can reintroduce the problem.

**027 interaction.** 027 frames other people's text as untrusted data wherever an agent reads it. In the browser, the
same class of input means XSS. The two defences share an input but must not share an output:

- **Viewer payloads carry raw stored text, never 027's framing.** If 027 put framing inside shared functions that the
  viewer also calls, the viewer would show framing tags as literal text, and a teammate could forge a closing tag in a
  place the viewer echoes. The function concerned is `backlog_continuity_items`, which `/api/continuity` calls at
  `backlog_server.py:12085`. **The ask to 027:** frame at the MCP and hook boundary (tool results, hook output,
  `/read/v1/tools`), not in stored rows or shared readers. If 027 must frame inside `backlog_continuity_items`, the
  viewer route calls its unframed core. This is Q8; 028's Step 1 test pins `/api/continuity` bytes against the store
  either way.
- **The viewer never interprets framing or author tags as trusted.** Every record field still goes through the
  sanitizer, or through `textContent`.
- **015 M12's marker** (`TM-INJECT-7f3a…`) is also an XSS-corpus entry: the S13 and R4 probes seed it, and assert it
  shows as inert text.

### 4.2 CSP, one header per response (D6)

**Today:** `SECURITY_HEADERS` (`serve/viewer.py:46`) sends `Content-Security-Policy: frame-ancestors 'none'` on every
response, and `/file/` adds a second CSP header (`sandbox allow-scripts`). Browsers enforce both, but the policy is
hard to read and test.

**New:** `serve/viewer.py: response_headers(kind: str, origin: str) -> tuple[tuple[str, str], ...]` replaces
`SECURITY_HEADERS` and `FILE_CSP`. `end_headers` emits exactly one CSP from it.

- **`kind`** is set per request:
  - `"app"`, the default: `/`, `/login`, `/api/*`, `/logout`, refusals and stdlib error pages;
  - `"static"`: an allowed `/static/v3/` or `/favicon.ico` response;
  - `"file-md"`: a `/file/*.md` page;
  - `"file-raw"`: any other `/file/` answer, a 404 included.
- **`origin`** is the allowed origin whose host set holds the request's Host. It always exists, because any other
  Host got 421. A new `ViewerGuard.origin_for_host(host)` returns it.

| kind | Content-Security-Policy |
|---|---|
| app | `default-src 'none'; script-src {origin}/static/v3/; style-src {origin}/static/v3/; font-src {origin}/static/v3/; img-src 'self'; connect-src 'self'; manifest-src 'self'; form-action 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'` |
| static | `default-src 'none'; frame-ancestors 'none'` (a script or stylesheet is a subresource; this only covers direct navigation to an asset) |
| file-md | `sandbox allow-scripts; default-src 'none'; script-src {origin}/static/v3/; style-src {origin}/static/v3/; font-src {origin}/static/v3/; img-src 'none'; connect-src 'none'; form-action 'none'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'` |
| file-raw | `sandbox; default-src 'none'; frame-ancestors 'none'` |

- **Why the app policy names `/static/v3/` and not `'self'` (rev 2, M2).** `'self'` also covers `/file/*`, which is
  teammate-committed content on the same origin. Today a committed `docs/x.js` can't run as a script only because
  `_serve_repo_file` answers `text/plain` for it and every answer sends `nosniff` (`backlog_server.py:12232-12236`). If
  a later edit added `.js` or `.css` to that `ext_map`, `/file/` would become a script source for any HTML injection.
  Narrowing scripts, styles and fonts to the plugin's own directory removes that path whatever the MIME map says, and
  matches file-md. Step 4 also pins the MIME map: no `/file/` answer is a script or style type.
- **Why `{origin}` and not `'self'` in file-md.** A sandboxed document has an opaque origin. CSP3 matches `'self'`
  against the response URL's origin, but an explicit scheme-host-port source with a `/static/v3/` path means the same
  thing in every engine, and also narrows scripts to that directory. On the file page, fonts fail their CORS fetch and
  fall back to system fonts. That is accepted: there's no CORS, ever.
- **No `'unsafe-inline'` and no `'unsafe-eval'` anywhere** (Q3). That needs D8:
  - the theme pre-paint `<script>` in `viewer/index.html:6-14` moves to `viewer/js/theme-boot.js`, a classic file
    loaded synchronously in the `<head>`, so it still runs before paint; the "keep the two in agreement" comment moves
    with it;
  - `LOGIN_PAGE`'s `<style>` (`serve/viewer.py:149-160`) moves to `viewer/css/login.css`;
  - `_MD_TEMPLATE`'s `<style>` and `<script>` are covered by §4.1.

  CSSOM writes (`el.style.x = …`, `style.cssText`) are allowed under a strict `style-src`. Parsed `style="…"`
  attributes and `setAttribute('style', …)` are not. A source scan in Step 5 finds the remaining ones: the `*-demo.js`
  files, which are excluded from static by §1.2; `budget-meter.js`, fixed in §4.1; and the two `h({ style })` callers
  (`phase-strip.js`, `popover.js`), fixed by routing `style` through CSSOM inside `h()` (§4.1, rev 2 M3).
- **Trusted Types is out of scope.** The sink list is short and audited. It is a later hardening step.

### 4.3 Other headers

`response_headers` also emits:

| Header | Value | Kinds |
|---|---|---|
| `X-Content-Type-Options` | `nosniff` | all |
| `X-Frame-Options` | `DENY` (for old browsers; CSP's `frame-ancestors` is the real control) | all |
| `Referrer-Policy` | `same-origin` (not `no-referrer`: 006 C1, a form POST would carry `Origin: null`) | all |
| `Cross-Origin-Opener-Policy` | `same-origin` | app, file-md, file-raw |
| `Cross-Origin-Resource-Policy` | `same-origin` | app, file-raw. **Never `static`**: the sandboxed file page is a cross-origin requester, and `same-origin` would block its scripts and styles |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()` | app, file-md |
| `Cache-Control` | `no-store` | file-md, file-raw. The others already send it: `_send_json` `:12770`, index `:11851`, static `:11881`, `ViewerGuard._write` `:255` |
| `Strict-Transport-Security` | `max-age=31536000` | set by **Caddy** per site, not by the app |

- **The server banner.** The guarded handler sets `server_version = "taskmaster"` and `sys_version = ""`, so loopback
  answers carry no Python version either. Caddy strips `Server` on the public path.
- **No host paths in unauthenticated answers.** That covers the login page, every 4xx JSON, stdlib `send_error`
  pages, static files and 421/429. This is a hard rule, and Step 4's leak test pins it.
- **Host paths in authenticated pages.** Inside a member session, the user's Q2/Q3 ruling applies: host paths and host
  names aren't masked for members, on condition that 026 lands. Even so, 028's own templates add none (§4.1 point 5),
  `/api/identity` stays root-free, `/api/session` is gone (§1.3), and error text stays scrubbed by
  `_send_json` (`serve/viewer.py:569`).

## 5. Limits (settles item 5)

| Limit | Value | Shared with the daemon? | Where |
|---|---|---|---|
| Per-key rate | 240/min, burst 120 | **shared**: one `principal_limiter` instance for MCP, `/read/v1` and the viewer (006 D7) | `daemon.py:367`, `viewer.py:344` |
| Per-key in-flight | 32 | **shared** (`principal_inflight`) | `daemon.py:368`, `viewer.py:340` |
| Per-peer failure budget | 20, refill 20/min | **shared** (`failure_budget`): a viewer 401 and an MCP 401 from one peer spend one bucket | `daemon.py:369` |
| Failed-login alarm | 100/min, all peers | viewer only, log only (§3.1) | new |
| Body cap | `max_body_bytes` (2 MiB) | same value, enforced separately (the stdlib server doesn't linger) | `viewer.py:309` |
| Login body | 4 KiB | viewer only | `VIEWER_LOGIN_MAX_BYTES` |
| Body deadline | `--body-timeout` | same flag | `viewer.py:373` |
| Header deadline | Caddy `read_header 10s` on the public path; per-`recv` `header_timeout` on loopback | same values | Caddyfile global, `viewer.py:518` |
| Connections | 32, excess closed at accept | viewer only | `GuardedViewerServer` |
| Caddy upstream connections | **16 to the viewer**, further requests queue in Caddy (rev 2, I3) | viewer site only | §2.2 `max_conns_per_host` |
| Caddy proxy buffers | **512 KiB response, 64 KiB request** on the viewer site, so a stalled client never holds a slot (rev 2.1, N1) | viewer site only | §2.2 `response_buffers`, `request_buffers` |
| Caddy body cap | **1 MiB on the viewer site** (4 MiB stays on the MCP site) | per site | §2.2 |
| Sessions | 8 per key, 256 in all | viewer only | `SessionStore` |

- **No new anonymous per-peer request limit** (Q9).
  - Public paths, meaning the login page and static files, cost a file read each.
  - Several teammates behind one office NAT load about 200 module files per page view (index has no-store, and that
    doesn't change), so a per-address rate would trip on real use. That is 006 D6's argument.
  - The 32-connection cap and Caddy bound the rest. A flood big enough to matter is a network-level denial of service,
    which neither per-address limit stops.
- **The two connection limits work together (rev 2, I3).** On the public path Caddy holds at most 16 upstream
  connections and queues the rest, so the app's accept cap of 32 is never what a browser hits. The cap stays as the
  bound for direct loopback clients (`ssh -L`, mods). The trade-off is that a burst through Caddy now waits in Caddy's
  queue instead of being refused at the app. The queue drains because every slot is released as soon as the app has
  answered: Caddy buffers each viewer response and request in full (`response_buffers 512KiB`,
  `request_buffers 64KiB`, §2.2), so a client that stops reading or sends slowly holds Caddy memory, not a slot. The
  shipped config has no Caddy write timeout (it would cut the MCP site's SSE streams), so the buffers, not a timeout,
  are what bound the wait (rev 2.1, N1). The app stays responsive to loopback throughout.
- **The per-key in-flight count isn't spent by a page load's assets.** Static answers are admitted before sign-in
  (§1.4), so the ~165 asset requests of a page view take no in-flight slot and no rate token. Only `/`, `/api/*` and
  `/file/*` do, and through Caddy they are at most 16 at a time, under the shared 32.
- **Why the Caddy viewer cap is below the app's.** The daemon site keeps 4 MiB, because the daemon lingers and answers
  its own 413 (016 gate 7a). The stdlib viewer closes early on 413 without draining, so through Caddy a body between
  the caps would become a 502. A Caddy cap of 1 MiB makes Caddy's own 413 the first stop. Browsers' viewer writes are
  a few KiB.

## 6. Risks and rollback

- **R1, CSP breakage.** An unseen inline style or script breaks a screen under the strict CSP. The Step 10 browser
  sweep visits every sidebar screen and a task, an epic and a bug detail, with a `securitypolicyviolation` listener,
  and requires zero violations. If a real need for inline style appears, Q3's fallback (`style-src {origin}/static/v3/
  'unsafe-inline'`, scripts still strict) is the ruling to ask for, not a silent edit.
- **R2, sandbox and `'self'` engine differences.** These are avoided by the explicit `{origin}/static/v3/` source;
  Step 10 renders a file page in Chromium.
- **R3, a shared-file edit.** `backlog_server.py` gets one new hook (`_md_editor_link`) and a template rewrite. Both
  touch the solo viewer, so the existing `tests/test_viewer_serve_hooks.py` and the solo `/file/` tests guard drift.
- **R4, the 025 rebase.** 025 has merged (9193e12a). The builder rebases onto `feat/team-collab` before Step 1 and
  applies §2.3's per-site split to the merged `test_deploy_files.py`.
- **R6, the auth move (rev 2).** Step 3 moves the cookie code into `serve/viewer_auth.py`. The move is
  behaviour-preserving and lands as its own commit before any 028 change to that code, with the existing
  `tests/test_serve_viewer.py` green on both sides of it, so a regression shows up in the move and not in the hardening.
- **R7, the Caddy queue (rev 2).** If `max_conns_per_host` behaves differently on the deployed Caddy version, V9 fails
  in the WSL tier before go-live. The fallback is to raise `VIEWER_MAX_CONNECTIONS` and keep the Caddy cap below it.
  Making the app's accept loop wait for a slot instead of closing is not the fallback: it would let one slow client
  stall accepts for everyone. The Caddy queue would have the same flaw without the rev 2.1 buffers (N1), so if V10
  shows the buffers don't release the upstream on the deployed Caddy version, the cap is removed and I3 is reopened,
  not shipped as is.
- **R5, the real-Caddy tier is WSL only.** It is the place where the Caddy 413 versus 502 behaviour (§5) and the
  HTTP/1.0 upstream (V7) are proven. If Caddy answers 502 rather than 413 on an oversize viewer body, the builder
  reports it, and the ruling is to accept either refusal status. Neither reaches the app.
- **Rollback:**
  - revert the step commits; the commented Caddy block and the unit without `--viewer-origin` restore 016's
    loopback-only viewer;
  - there's no schema or data change, and sessions live in memory;
  - the solo `/file/` page keeps working on either side of the revert.

## 7. TDD plan

- **Rules for every step:**
  - Tests come first and must fail before the change; then the change; then the step's own test set, plus the files
    named, and nothing broader.
  - Use the repo's `.venv` Python (a bare `python` is C:\Python313, the wrong fastmcp).
  - Run serially in the foreground, with `--basetemp` under the C: session scratchpad (D: is full).
  - Run the JS checks with `npm run test:unit` from `viewer/`, after `npm ci` if `node_modules` is missing.
  - Don't run the full suite: memory is short, and another lane runs broad suites.
  - One commit per step, named `feat(team-server-028): Step N …` or `test(…)`.
- **Base:** rebase onto `feat/team-collab` before Step 1 (025 has merged there at 9193e12a). Steps 1 to 11 don't
  depend on 025; Steps 12 and 13 do.

**Step 1. The route allow-list and F3 (§1.3, §1.4).** `serve/viewer.py`: `PUBLIC_EXACT`, `SESSION_EXACT`,
`ROUTE_PREFIXES`, `REFUSED_EXACT`, `route_allowed`, `caddy_matcher_forms`; `admit` decodes the path once into
`handler.viewer_path` and refuses in the §1.4 order, including `//`; 404 joins `CHARGED_WITHOUT_SESSION`. Until Step 3
lands `CookieLogin`, `PUBLIC_EXACT` lists `/login` and `/logout` literally; Step 3 switches it to the class attribute.
Tests in `tests/test_serve_viewer.py`, written first:
1. `/backlog.yaml`, `/v3/dev/edit-demo`, `/v3/dev/edit-demo/`, `/v3`, `/v3/`, `/v3/index.html`, `/index.html`,
   `/api/session` and `/nope` all get 404, with and without a session. The bodies are identical, and none carries
   `backlog.yaml`'s bytes.
2. Without a session, 21 such 404s from one peer: the 21st gets 429. With a session, 30 404s are never charged.
3. Encoded forms: `/%62acklog.yaml`, `/api/%73ession`, `/api/../backlog.yaml`, `/static/v3/%2e%2e/dev/edit-demo.html`
   and `/file/..%2f.env` give 404 or 400, never 200. An absolute-form target (`GET http://x/ HTTP/1.1`) gets 400. A NUL
   or backslash gets 400. **Double-encoded forms (rev 2, I2)**, with and without a session, give 404 or 400 and never
   the file's bytes: `/static/v3/js/%255fdormant/flight-log.js`,
   `/static/v3/js/%252e%252e%252ftests%252fmock-api.js`, `/static/v3/%2564ev/edit-demo.js`,
   `/static/v3/js/components/edit/fields/md-field%252ddemo.js` and `/file/%252e%252e%252f.env`. **Leading `//`**:
   `//api/board`, `//login` and `//static/v3/js/main.js` give 400.
4. Every §1.1 route still answers as before with a session. Cover a sample of each row, reusing the existing fixtures
   `v`, `login` and `session_headers`. For each sampled route, a spy on the base handler records its `clean_path`, and
   the test asserts it equals `handler.viewer_path` (one decode, one value).
5. `/api/continuity` bytes equal `backlog_continuity_items(limit=0)` on the seeded store, with no framing markup. This
   is the 027 pin (§4.1).
6. **M13 markers (rev 2, M9).** Seed a `sessions` row whose fields carry `M13-SESSIONS-…`, a `linear_queue` error
   carrying `M13-LINEAR-…`, and a `projection` row carrying `M13-PROJ-…`. Signed in as a member, fetch every GET route
   in §1.1 (each list and one id of each detail). No body contains any marker.

**Step 2. The static allow-list (§1.2).** `static_asset_allowed`, called from `admit` for the static prefix; the base
hook `ViewerHandler._static_rel` (solo: today's `unquote`); the guarded override serving from `handler.viewer_path`.
Tests:
1. `css/tokens.css`, `js/main.js`, `js/lib/csrf.js`, `vendor/marked.min.js`, `vendor/fonts/DMSans-Variable.woff2` and
   `/favicon.ico` give 200.
2. `dev/edit-demo.html`, `brainstorm-phases.html`, `index.html`, `package.json`, `README.md`, `tests/mock-api.js`,
   `js/_dormant/flight-log.js`, `js/components/edit/fields/md-field-demo.js`, `tools/gen-tokens.mjs`,
   `node_modules/x.js` (a planted file), `css/.hidden.css` and `vendor/../index.html` all give 404, charged without a
   session. So do the double-encoded forms of each (`%255f`, `%252e%252e%252f`, `%2564ev`), and the test asserts the
   body isn't the excluded file's bytes.
3. A unit table for `static_asset_allowed` covers each rule, including `js/%5fdormant/flight-log.js` and
   `js/%2e%2e%2ftests%2fmock-api.js` (refused for the `%`).
4. **The file served is the file checked.** A spy on `static_asset_allowed` and on the guarded `_static_rel` records
   both values for each request in tests 1 to 3; they are equal every time. A planted file whose name literally
   contains `%` (`viewer/js/a%5fb.js`, in a temp viewer root) is refused, so the `%` rule holds even for a real file.
5. Solo is unchanged: on the solo handler, `/static/v3/js/main.js` gives 200 and `_static_rel` still decodes.

**Step 3a. The auth interface, as a pure move (§3.6, D12).** Its own commit, before Step 3.
- `serve/viewer_auth.py`: `ViewerIdentity`, `ViewerAuth`, `Reply`, and `CookieLogin` holding the moved code unchanged
  (`SessionStore`, `ViewerSession`, `LOGIN_PAGE`, cookie names, `CSRF_META`, the session lookup, `_reauthenticate`,
  `login`, `logout`, `_key_from_body`). `ViewerGuard` takes an `auth: ViewerAuth` and calls only the interface;
  `daemon.py` builds `CookieLogin` and passes it. `serve/viewer.py` re-exports the moved names its tests import.
  `PUBLIC_EXACT` reads `CookieLogin.PUBLIC_PATHS`.
- Tests, written first where new:
  1. The existing `tests/test_serve_viewer.py` passes unchanged before and after the move (run it on both commits).
  2. New `tests/test_serve_viewer_auth.py`: a test-only `HeaderAuth` adapter (trusts `X-Test-Principal`, no public
     paths, no forgery token) drives the guard. Step 1's route table and 404 charging, the operator host-local
     refusal, the per-key rate and in-flight limits, and one write under `viewer_principal` all behave the same as
     with `CookieLogin`. `/login` gives 404 under `HeaderAuth` (its paths come from the adapter), and an anonymous page
     request is sent to `HeaderAuth.sign_in_location`.
  3. A source check: `serve/viewer.py` contains none of `Cookie`, `SimpleCookie`, `Set-Cookie`, `__Host-`,
     `CSRF_META` or `compare_digest`.

**Step 3. Session and auth hardening (§3).** All of it lands in `CookieLogin`, except the `Sec-Fetch-Site` rule on
writes, which is the guard's.
- `ViewerSession.origin`; the origin check in `CookieLogin.identify`; `Sec-Fetch-Site` on writes, login and logout;
  logout with Origin and `Sec-Fetch-Site` only, no token; `Clear-Site-Data: "cache"`; the sign-in and sign-out meta;
  the login alarm (`LOGIN_ALARM_PER_MINUTE` in `config.py`, a `KeyedLimiter` in `CookieLogin`, injectable through the
  clock for tests).
- Tests:
  1. A cookie from a loopback login, sent with the public Host and `X-Forwarded-For` (the `CADDY` headers), is no
     session (303 or 401). A public login's cookie sent with the loopback Host is none either.
  2. A write with `Sec-Fetch-Site: same-site` or `none` gets 403. Absent passes. `cross-site` still gets 403. A login
     with `same-site` gets 403.
  3. `POST /logout` with no cookie and an allowed Origin gets 303 `/login`, both expired `Set-Cookie`s, and
     `Clear-Site-Data`, and isn't charged (20 of them leave the budget full). With a bad Origin, or
     `Sec-Fetch-Site: same-site`, it gets 403. **The `Clear-Site-Data` value is exactly `"cache"`**: the header
     contains neither `cookies` nor `*` (rev 2, I1), on every logout answer, with and without a session.
  3b. **Stale tab (rev 2, M1).** Log in twice with the same cookie jar (session A, then session B, so the cookie names
     B). `POST /logout` with session A's CSRF token, or with no token at all, and the allowed Origin: 303 `/login`,
     session B is dropped, and `/api/board` with the old cookie then gets 401.
  4. Replaying the cookie after logout gets 401 on `/api/board`.
  5. An operator token logs in on loopback, and its cookie is then sent on the public Host: 303 or 401 (the origin
     binding), and an operator token login through the public headers gets 403. Both keep the 002 text where 403
     applies.
  6. `Authorization: Bearer ka` with no cookie on `/api/board` gets 401, and on `/` gets 303.
  7. 101 failed logins across 101 peers within the clock minute: exactly one WARNING with the alarm text, and no
     refusal from the alarm. 200 more inside the same 60 s add no second line; after the clock advances 61 s and the
     bucket empties again, a second line appears.
- Update the existing `test_logout` if its expectation changes.

**Step 4. Headers and no host paths (§4.2, §4.3).**
- `response_headers(kind, origin)`, `ViewerGuard.origin_for_host`, the per-request `kind`, `server_version` and
  `sys_version`, and `no-store` on `/file/`. `SECURITY_HEADERS` and `FILE_CSP` are removed, and their importers in
  tests updated.
- Tests:
  1. For one response of each kind, on both loopback and the public Host, there's exactly one
     `Content-Security-Policy` header, equal to the table's with `{origin}` filled in for that Host, and each §4.3
     header is present or absent as the table says. CORP is absent on static. Assert
     `get_all("Content-Security-Policy")` has length 1.
  2. The CSP strings contain neither `unsafe-inline` nor `unsafe-eval`, and the app and file-md policies' `script-src`
     and `style-src` contain no `'self'` (rev 2, M2).
  2b. **`/file/` is never a script or style source (rev 2, M2).** Tracked files `docs/a.js`, `a.mjs`, `a.css`,
     `a.html`, `a.svg` and `a.xml` each answer with a `Content-Type` that is not a JavaScript, CSS, HTML, SVG or XML
     type (today: `text/plain`), with `nosniff`.
  3. The `Server` header is `taskmaster`, without "Python".
  4. **The leak test.** For every unauthenticated answer (login GET, a 401, a 403, a 404, a 421, a 429, a 400 and one
     static file), the body and headers contain none of these: `str(root)`, its POSIX form, `Path.home()`,
     `socket.gethostname()`, the Python version string.

**Step 5. No inline code in pages (D8).**
- `viewer/js/theme-boot.js`; `viewer/index.html` loads it in the head; `viewer/css/login.css`; `LOGIN_PAGE` links it.
- Tests:
  1. Python: the served `/`, `/login` and a `/file/x.md` page contain no `<script>` without `src`, no `<style>`, and
     no `style=` attribute (an HTML-parser walk, not a regex).
  2. JS unit: `tests/unit/theme.test.js` loads `theme-boot.js` in jsdom and checks it sets `data-theme` the same way
     `resolveTheme()` does, for `dark`, `light`, `system` and a throwing storage.
  3. A source scan in a new `tests/unit/csp-sources.test.js`: no `style=` inside a template literal assigned to
     `innerHTML`, and no `setAttribute('style'` in any `viewer/js` file that `static_asset_allowed` admits.
     `h(…, { style: … })` is allowed, because `h()` applies it through CSSOM (Step 8). Every `h(…, { html: … })` call
     and every `innerHTML`/`insertAdjacentHTML` assignment is listed against an allow-list of known sinks (file and
     line pattern, with the data source in a comment), so a new sink fails the scan until someone audits it. The file
     list comes from a shared JSON fixture written by the Step 2 Python test, or from the same rules restated in a
     comment that points at `static_asset_allowed`.

**Step 6. One sanitizer, two loaders (§4.1 points 1 and 2).**
- `viewer/js/lib/md-safe.js` holds the body of `markdown.js`; `markdown.js` imports it and delegates; `isSafeUrl` is
  exported.
- Tests, JS unit:
  1. A new `tests/unit/md-safe.test.js` evaluates `marked.min.js` and `md-safe.js` with `dom.window.eval`, and checks
     that `TaskmasterMarkdown.render` gives byte-identical output to `renderMarkdown` over every input of
     `markdown-safety.test.js`. The corpus is exported from that file, or moved to `tests/unit/fixtures/md-corpus.js`.
  2. `TaskmasterMarkdown` is frozen.
  3. `isSafeUrl` refuses `javascript:`, `\tjavascript:` and `data:`, and allows `/file/x`, `#a` and `https://x`.
  4. `markdown-safety.test.js` passes unchanged.

**Step 7. The `/file/` markdown page (§4.1 points 3 to 6).**
- `_MD_TEMPLATE`; `ViewerHandler._md_editor_link`, with the guarded override returning `""`; the guarded
  `_md_full_path` is deleted; `viewer/js/file-page.js`; `viewer/css/file-page.css`.
- Tests:
  1. Python, solo and guarded: the md page names no `jsdelivr` and no `https://`. It loads the three scripts and three
     stylesheets from `/static/v3/`. The `data-b64` round-trips the file's text.
  2. The solo page has the `vscode://file/` link; the guarded page has none, and carries no absolute host path.
     **Escaping (rev 2, M4):** on the solo handler, with `_md_full_path` stubbed to return
     `/r/a"><img src=x onerror=alert(1)>.md`, the page's markup has no `<img` element (an HTML-parser walk) and the
     `href` attribute holds the whole path. The stub keeps the test portable to Windows, where the name isn't legal.
  3. JS unit, `tests/unit/file-page.test.js` (jsdom):
     - it renders a heading from `data-b64`;
     - an `<img onerror>` / `<script>` payload is inert;
     - the zoom buttons change `font-size` and the label when `localStorage` throws `SecurityError` (a stubbed
       getter), and persist when it doesn't;
     - with no `TaskmasterMarkdown`, it falls back to a `<pre>`.
  4. Update `test_file_pages_are_sandboxed_and_the_path_is_escaped`: the sandbox is asserted through the file-md CSP,
     the escape assertion goes with `_md_full_path`'s override, and "no editor link in server mode" takes its place.

**Step 8. The two other sinks and `h()`'s style key (§4.1).**
- `card.js` checks the docs link with `isSafeUrl`; `budget-meter.js` uses `h()`, `textContent` and CSSOM; `util/h.js`
  applies a `style` key as `el.style.cssText` (rev 2, M3).
- Tests, JS unit:
  0. `h.test.js` (new or existing): `h('span', { style: 'width: 40%' })` sets `el.style.width === '40%'` without
     calling `setAttribute('style', …)` (a spy on `Element.prototype.setAttribute`). The phase strip renders its bar
     width, and the popover gets `position: fixed`, through the same path.
  1. `card.test.js`: a task with `docs: {spec: 'javascript:alert(1)'}` renders no docs button; `docs/x.md` and
     `https://x` render one.
  2. A new `budget-meter.test.js`: a `label` of `<img src=x onerror=…>` renders as text; the fill width is set
     through `style.width`, and there's no `style` attribute in the markup source.

**Step 9. The logout control (§3.4, 017).** Run `reality-reprojection:apply` first.
- `csrf.js` `serverMode` and `signOut`; the `sidebar.js` footer.
- Tests, JS unit:
  1. In `csrf.test.js`, `signOut` POSTs the sign-out meta's URL through the wrapped fetch, then assigns the sign-in
     meta's URL when the final response URL is that location; `serverMode` is false without the sign-out meta. With
     metas naming `/x-out` and `/x-in`, those are used (the JS has no hard-coded `/login` or `/logout`); a 401 on any
     same-origin fetch goes to the sign-in meta's URL.
  1b. **A failed sign-out is not shown as a success (rev 2, M1).** When the POST answers 403, or the final URL isn't
     the sign-in location, `signOut` doesn't navigate and the footer's live region says "Sign out failed. Reload and
     try again."
  2. `sidebar` tests (an existing file, or a new `sidebar.test.js`): the Sign out button exists only when the
     sign-out meta is present, and is a `button` with an accessible name.
  3. `style-rules.test.js` style checks pass for the new CSS: no left rail, no transform on hover, no box-shadow. If
     those rules aren't already covered, add them for the footer selector.

**Step 10. Browser proof (Chromium, the existing `BROWSER_LOGIN` runner pattern in `test_serve_viewer.py:741`).**
- New runners. They skip when node or Playwright is missing, as today.
- The run:
  1. **Sweep.** Sign in, then visit every sidebar route plus one task, epic and bug detail and one `/file/docs/a.md`,
     with a `securitypolicyviolation` listener and a `console` error listener. Zero CSP violations; no `pageerror`.
  2. **XSS.**
     - The XSS corpus: the `markdown-safety` payloads plus `"><svg onload=…>`, `javascript:` links,
       `<a href="jav&#x09;ascript:…">`, mXSS forms (`<noscript><p title="</noscript><img src=x onerror=…>">`) and the
       M12 marker.
     - Put them in each field the viewer renders: task title and description, a note, a handover quote, a bug summary,
       an idea body, a decision body, an epic name, tags, a branch name and `docs` links. Also commit a tracked
       `docs/x.md` with the same corpus.
     - **Prefs (rev 2, M8).** `PUT /api/viewer/prefs` as member `ka` with corpus strings in `theme`,
       `ui.last_task_id`, `ui.last_epic_id` and an unknown nested key. Prefs are project-wide, so this is the input
       one member writes and every member loads.
     - **The seeding route (rev 2, M8).** Seed each field through the guarded viewer's write API where it has one
       (ideas, notes, tasks, bugs, prefs), and over MCP as member `ka` where it doesn't (handover quotes, decision
       bodies, epic names, branch). Either way the author is a member, not the operator.
     - Each payload sets `window.__pwned = 1` if it runs. After visiting each view, `__pwned` is undefined, in the app
       and on the file page.
  3. **Zoom.** On the sandboxed file page, clicking + changes the label to 110%.
  4. **Sign out.** Clicking Sign out lands on `/login`. Replaying the old cookie with a raw request gets 401 on
     `/api/board`. Then the stale-tab case (M1): two tabs, sign in again in the second, click Sign out in the first;
     it lands on `/login`, and the second tab's next poll goes to `/login` too.
  5. **Links out of the file page (M5, recorded, not fixed; Q12).** From a `/file/docs/a.md` page, click "← Backlog"
     and a relative md link. Record where each lands (expected today: `/login`, because the sandboxed page's
     navigation is cross-site and the `SameSite=Strict` cookie is withheld), so Q12's follow-up starts from a measured
     baseline.

**Step 11. The config check (D11).** `config.validate` refuses a viewer-origin host equal to an `--allowed-host` host.
Tests in `tests/test_serve_config.py` (or the file holding the `--viewer-origin` tests today):
1. `--allowed-host tm.example.com --viewer-origin https://tm.example.com` gives `ServeRefused` with the text.
2. With `:443` on either side, it is refused too.
3. `https://viewer.tm.example.com` is accepted.
4. A loopback origin is accepted.

**Step 12. The Caddyfile, the unit and `tests/caddyfile.py` (§2.2, §2.3).** After 025 has merged.
- Add the viewer site block and the 028 comment; the unit's `--viewer-origin`; `tests/caddyfile.py`; and every
  `test_deploy_files.py` change in §2.3.
- Tests first:
  1. `tests/test_caddyfile_parser.py`: `sites()` on the shipped file returns the two names in order; `matcher()` reads
     both matchers; a nested brace in a `log` block doesn't confuse depth; a commented block is ignored.
  2. Mutations that must each fail a named test:
     - an extra `@viewer` form (`/v3/*`);
     - a missing form (`/file/*`);
     - `reverse_proxy 127.0.0.1:7471` inside the MCP site;
     - `/api/*` added to `@taskmaster`;
     - the viewer `max_size` raised to 4MiB;
     - HSTS removed from one site;
     - `--viewer-origin` dropped from the unit;
     - the viewer's `max_conns_per_host` removed, or raised to 32 (rev 2, I3);
     - the viewer's `keepalive off` removed;
     - the viewer's `response_buffers` removed, or set to `32KiB` (below the largest asset, rev 2.1, N1);
     - the viewer's `request_buffers` removed, or set to `1KiB` (below `VIEWER_LOGIN_MAX_BYTES`);
     - `response_buffers` added to the daemon site;
     - a planted allow-listed asset larger than `response_buffers` (a temp file under a copied `viewer/js/` tree passed
       to the walk), which must fail the size check;
     - the viewer log's CSRF filter removed (rev 2, M6).

     Each is checked by a parametrized test that edits the text in memory.
- Run `tests/test_deploy_files.py` and `tests/test_caddyfile_parser.py`.

**Step 13. Real Caddy (WSL deploy tier, `linux_lane.sh --tier deploy`; `tests/test_serve_deploy_smoke.py`).** These
cases are skipped without `TASKMASTER_CADDY`, as today. Each goes through `viewer.tm.localhost:<https_port>`:
- **V1.** A member key logs in. The cookie is `__Host-tm_session` with `Secure; HttpOnly; SameSite=Strict`. `/` then
  answers 200 with the CSRF meta, and `/api/board` answers 200.
- **V2.** The operator token's login gets 403 with 002's text. An operator cookie minted on loopback, then sent through
  Caddy, gets no session.
- **V3.** 25 bad logins with forged `X-Forwarded-For` values: 20 x 401 then 429, and the serve log names the real peer,
  never `203.0.113.*`. This is the viewer twin of 016 case 5.
- **V4.** `/backlog.yaml`, `/v3/dev/edit-demo`, `/static/v3/dev/edit-demo.html`, `/static/v3/package.json`,
  `/api/session`, `/mcp`, `/read/v1/meta`, `/v3/../backlog.yaml` and `/API/../backlog.yaml` all give 404. On the MCP
  host, `/login`, `/api/board` and `/static/v3/js/main.js` give 404 as well.
- **V5.** The headers through Caddy: HSTS is present, `Server` is absent, and the app's CSP for app, static, file-md
  and file-raw arrive unchanged.
- **V6.** A 2 MiB body to `/api/tasks` gets 413, or 502 per R5, and the viewer's log shows no handler ran.
- **V7.** Two sequential requests 6 s apart both succeed. The HTTP/1.0 upstream isn't reused stale. This is the twin
  of 016 case 11.
- **V8 (rev 2, M7).** An unknown Host header through Caddy's viewer site, with SNI `viewer.tm.localhost` and
  `Host: evil.example`. No site's host matcher matches, so Caddy answers on its own (possibly an empty 200) and the
  app is never reached. Assert what matters: the body has no app bytes (no login form, no JSON from the app, no
  `taskmaster` banner), and the viewer's log has no request line for it. Record Caddy's actual status in the evidence;
  don't assert it.
- **V9 (rev 2, I3). A page load over h2 doesn't over-dial.** Sign in (V1's cookie). Then, over one HTTP/2 connection
  to Caddy, fetch at once `/`, `/api/board`, and every file under `viewer/` that `static_asset_allowed` admits (about
  170 URLs, a superset of what a page load needs, module graph included). Use
  `curl --http2 --parallel --parallel-max 200` with the cookie and Caddy's local CA; skip with a reason if `curl -V`
  doesn't list `HTTP2` (`httpx` has no h2 support in the repo venv). Expect every answer 200 and zero 5xx, and Caddy's
  log shows no upstream EOF or dial errors for the viewer site. Run it three times. As a recorded control (not asserted, since it's
  timing-dependent), run once with `max_conns_per_host` removed from the rendered Caddyfile and record the 502 count,
  so the evidence shows the case can detect the failure.

- **V10 (rev 2.1, N1). A stalled reader holds no upstream slot.** Client A, with no cookie, opens one HTTP/2
  connection to Caddy, sends 20 concurrent `GET /static/v3/vendor/fonts/DMSans-Variable.woff2` streams, and never
  sends `WINDOW_UPDATE` (or, if the client library can't withhold it, stops reading the socket after the first
  frames). A raw `h2` client script in the WSL lane does this; skip with a reason if `h2` isn't installable there.
  While A holds its streams open (at least 15 s), client B's `GET /login` and a signed-in `GET /api/board` each answer
  200 within 5 s, three times in a row, and a slow upload (client C sends a 4 KiB `POST /login` body at 100 bytes/s)
  doesn't delay B either. Then A closes. Record, not assert, a control run with `response_buffers` and
  `request_buffers` removed from the rendered Caddyfile, and B's latency or failure there, so the evidence shows the
  case detects the stall.

Also run `test_the_shipped_caddyfile_validates_with_only_the_log_path_changed`, adjusted to replace both log paths.
`caddy validate` there also proves the `format filter` and `transport http` syntax on the deployed Caddy version.

**Step 14. Runbook and hand-offs.**
- `docs/runbooks/team-server-deploy.md`:
  - DNS: an A/AAAA record for `viewer.<domain>`.
  - The placeholder: replace `tm.example.com` in three places, the two site names and the unit, plus the
    `--viewer-origin`.
  - §9's "Checks from outside": `curl https://viewer.$DOMAIN/login` gives 200; `/backlog.yaml` gives 404;
    `/api/board` gives 401; `https://$DOMAIN/login` gives 404; check the certificate issuer of the viewer name.
  - Operator note: the operator signs in to the viewer only over `ssh -L 7471:127.0.0.1:7471`; through the public URL
    the operator token is refused by design.
  - Alarm line: what the failed-login WARNING means.
- `test_the_how_to_*` tests in `test_deploy_files.py` must pass, with the new blocks tagged `not-rehearsed` and a
  reason.
- Update the lane folder's `progress.json` row (stage) and the resume file `team-server-028.md`.

**Step 15. 028's security review (§8), then fixes.** Only Critical and Important findings block. Fix rounds are scoped
re-reviews, with at most two rounds before escalating (user review policy).

## 8. The security review for 028 (settles item 6)

- **Who:**
  - One fresh-context **Opus** reviewer who neither designed nor built 028;
  - **Codex** in parallel as a second model, since this is an internet-facing, security-sensitive change. It gets a
    lean, bounded brief: the files below, the probe list, and "inspect and report; do not implement".
  - The orchestrator adjudicates both.
- **When:** on the Step 14 commit, with Steps 10 and 13 green.
- **Sharing with 015:** the review shares 015's evidence pack (S13). A 015 PASS needs 028 merged and this review
  passed (015 B1).
- **On what:** the commit SHA, plus these files read in full:
  - `serve/viewer.py`, `serve/viewer_auth.py`, `serve/tracked.py`, `serve/config.py` (viewer parts),
    `serve/auth.py` `is_host_local`;
  - the `ViewerHandler` routes in `backlog_server.py` (`:11774-12800`), `_static_rel` and `_MD_TEMPLATE`;
  - `viewer/js/lib/md-safe.js`, `markdown.js`, `file-page.js`, `theme-boot.js`, `csrf.js`, `card.js`, `sidebar.js`,
    `util/h.js`;
  - `deploy/caddy/Caddyfile`, `deploy/systemd/taskmaster.service`.
- **The reviewer must probe** (each probe: request, expected, observed; direct to `127.0.0.1:7471` with the Caddy
  headers, and through real Caddy on WSL):
  1. **Route surface.** Every §1.1 route and every §1.3 path, with and without a session.
     - A path fuzz:
       - case variants (`/API/board`, `/Login`);
       - double slashes (`//api/board`, `/api//board`);
       - dot segments, raw and percent-encoded, and `%2f`;
       - a backslash, NUL, overlong UTF-8, `;` path parameters, query-only targets, absolute-form targets, and a
         `Host` that disagrees with the absolute URI.
     - Confirm Caddy's `@viewer` and `route_allowed` agree on every probe, and that nothing outside §1.1 returns 200.
  2. **Static.** Try to fetch any `.html`, `.json` or `.md` file, `dev`, `_dormant`, `*-demo.js` or `node_modules`
     content through `/static/v3/`, including by single, **double and triple percent-encoding** (`%255f`,
     `%252e%252e%252f`, `%25252e`), mixed case hex, and symlinks under `viewer/`, if any exist in the install. For
     each, confirm the value `static_asset_allowed` checked is the value the file was resolved from.
  3. **Authentication.**
     - Without a cookie, every `/api/*` gets 401 and every page a 303 to `/login`. An `Authorization` header is
       ignored.
     - Session fixation: a client-chosen sid is never adopted.
     - Origin binding, both directions.
     - Revocation and rotation end the session on its next request.
     - Idle and absolute expiry, using the injectable clock.
     - Logout invalidates server-side, including from a stale tab whose CSRF token belongs to an older session, and
       `Clear-Site-Data` carries `"cache"` only.
     - 8 sessions per key and the 256 cap, with eviction only of the key's own sessions.
     - The interface boundary: nothing in `serve/viewer.py` reads a cookie or compares a CSRF token; every auth
       decision goes through `ViewerAuth` (§3.6).
  4. **The operator token.** Refused at the public login, and its loopback session refused on the public Host. Look
     for any path where `is_host_local` is true through Caddy: `X-Forwarded-*` stripping, header case, duplicate Host,
     HTTP/2 `:authority`.
  5. **CSRF.** A write with each of:
     - no Origin;
     - `Origin: null`;
     - the MCP sibling origin;
     - a port variant;
     - `Sec-Fetch-Site` set to `same-site`, `cross-site` or `none`;
     - no token, a wrong token, or another session's token;
     - a `text/plain` simple POST;
     - method override headers;
     - HEAD, TRACE and unknown methods.

     Also check login CSRF and logout CSRF.
  6. **XSS.**
     - The Step 10 corpus plus the reviewer's own payloads, in every rendered field and on `/file/`, in Chromium, with
       the CSP report listener.
     - Re-audit every `innerHTML`/`insertAdjacentHTML` sink in the served JS (`rg innerHTML viewer/js`, minus the
       excluded files), every `h(…, { html })` caller (`rg "html:" viewer/js`), and each sink's data source.
     - Prefs: corpus strings in the project-wide prefs render inert for a second member.
     - Check `md-safe.js` against mXSS (double parse), `<template>`/namespace confusion, and URL schemes with control
       characters.
  7. **CSP and headers.** The header dump per response kind, direct and through Caddy:
     - exactly one CSP per response;
     - no `unsafe-*`;
     - CORP absent on static;
     - HSTS from Caddy;
     - no `Server` version;
     - `no-store` on every authenticated answer.
  8. **Brute force and limits.**
     - The 21st failure is refused.
     - Forged `X-Forwarded-For` through Caddy doesn't help.
     - IPv6 is keyed on its /64.
     - MCP and viewer failures share one bucket.
     - The alarm fires once.
     - The connection cap, a slow header (Caddy `read_header`), a slow body (408) and an oversize body (Caddy 413).
     - A full page load over h2 through Caddy, with two or three members loading at once: zero 5xx (V9), and the
       loopback viewer still answers an `ssh -L`-style direct request during the burst.
     - Slot exhaustion without a key (rev 2.1, N1): stalled h2 readers on the largest assets, slow login uploads, and
       a mix of both, from one peer and from several. Other clients keep answering within 5 s (V10). Look for any
       pre-login answer larger than `response_buffers`.
  9. **Leaks.** Unauthenticated bodies and headers carry no host path, host name or version. Every viewer body joins
     015's leak scan. Grep all logs and transcripts for the issued keys, session ids and CSRF tokens (the harness
     knows them), and expect none.
  10. **M13.** Compare `/api/sessions`, `/api/sessions/<id>`, `/api/threads` and `/api/continuity` with what 026
      lets a member read over MCP. Rows from the `sessions` table, `linear_queue` or `projection` in a viewer body
      are Important. Also compare write effects: viewer writes run under the daemon's `SESSION_ID` with the member
      named only by `viewer_principal` (§1.1). Check that a viewer status move claims, refuses (003 D11/E7, the 409)
      or releases exactly as the same member's MCP move does, and that no member can use the viewer to act under
      the daemon's own session on another member's claim.
  11. **F3.** `/backlog.yaml` and `/v3/dev/*` answer 404 with and without a login, direct and through Caddy (closed
      by removal).
  12. **Caddy.**
      - `caddy validate`;
      - exactly the two sites, with no shared upstream or path;
      - no `trusted_proxies`, `header_up Host` or `encode`;
      - port 80 closed;
      - the viewer certificate issued through TLS-ALPN.
  13. **027.** Viewer payloads carry raw stored text, never 027 framing, and the M12 marker renders inert.
  14. **At least five probes of the reviewer's own** beyond this list.
- **Evidence the review file must hold:**
  - the commit SHA;
  - the exact test commands and their pass counts: the viewer set, the deploy files, JS unit, the Step 10 browser
    runs, and the WSL deploy tier;
  - the probe table;
  - the header dump;
  - the CSP-violation and `__pwned` results per view;
  - the log-grep commands and their empty results;
  - the M13 comparison;
  - findings labelled Critical, Important or Minor, each with a reproduction;
  - a PASS/FAIL verdict.
- **Severity anchors:**
  - Critical: any XSS that runs on the viewer origin; any route that serves data or a non-allow-listed file before
    login; the operator token working through Caddy; a CSRF write.
    A static answer resolved from a different decode than the one checked counts here (rev 2, I2).
  - Important: a CSP with `unsafe-inline` for scripts; a host path in an unauthenticated answer; the `sessions` table
    rows through the viewer; the Caddy matcher wider than `caddy_matcher_forms()`; any 5xx on a normal page load
    through Caddy (rev 2, I3); a `Clear-Site-Data` that reaches beyond the viewer origin (rev 2, I1).
  - Minor: the rest.

## 9. Open questions, with the default this design builds

| # | Question | Default (recommended) |
|---|---|---|
| Q1 | Viewer routing: a subdomain, or a path prefix on `tm.example.com`? | **Subdomain `viewer.<domain>`** (§2.1). A prefix means rewriting every root-relative URL, and it mixes the cookie and bearer surfaces. |
| Q2 | F3 routes in server mode: 404 always, or behind login? | **404 always** (§1.3). Nothing in the viewer uses them. |
| Q3 | CSP `style-src`: strict (rev 2: `{origin}/static/v3/` only), or allow `'unsafe-inline'` for styles? | **Strict**, proved by the Step 10 sweep. The fallback, `'unsafe-inline'` for styles only with scripts still strict, needs a ruling. |
| Q4 | Login lockout: per-peer budget plus a global log-only alarm, or a per-key lockout too? | **No per-key lockout** (§3.1). With 256-bit keys a lockout buys nothing, and it is a denial-of-service lever. |
| Q5 | Session lifetimes for the public viewer: keep 8 h idle and 24 h absolute? | **Keep them.** The 3 s poll keeps an open tab alive until 24 h anyway. A shorter idle would only sign out closed tabs sooner. |
| Q6 | Should the solo `/file/` page also drop jsdelivr and use the sanitizer? | **Yes**: one template, and the CDN hop is a risk in solo too. Solo keeps its editor link. |
| Q7 | The viewer site's Caddy body cap? | **1 MiB**, below the app's 2 MiB, so Caddy's 413 is the first stop (§5). |
| Q8 | 027 framing and the viewer: where does framing live? | **At the agent-facing boundary only** (MCP results, hook output, `/read/v1/tools`). The viewer reads raw stored text; if 027 frames inside `backlog_continuity_items`, the viewer calls its unframed core (§4.1). This needs agreement with the 027 designer. |
| Q9 | An anonymous per-peer request limit on public paths? | **No** (§5). The office-NAT argument of 006 D6 holds, and the connection cap plus Caddy bound the rest. |
| Q10 | A Codex second review alongside the Opus reviewer? | **Yes** (§8). The change is internet-facing and security-sensitive. |
| Q11 (rev 2) | Static assets: keep `no-store`, or serve them `no-cache` with an `ETag` so repeat views revalidate? A remote teammate re-downloads ~165 files on every page view under `no-store`; with revalidation most become 304s. | **Keep `no-store` at go-live.** I3's queue makes a page load reliable, and a teammate count of a few makes the bandwidth small. Revalidation adds an ETag path to the static branch, and its stale-asset behaviour across a plugin upgrade needs its own test. File it as a follow-up and take it if teammates report slow loads. |
| Q12 (rev 2) | Links out of a sandboxed `/file/` page ("← Backlog", relative md links) land on `/login` while signed in, because the cookie is withheld on that cross-site navigation (M5). Fix in 028, or accept? | **Accept for go-live; follow-up.** 006 already behaves this way. The fix the review suggests (answer a cookie-less navigation to `/` with a same-origin bounce page) needs a loop guard and changes the redirect contract every unauthenticated client sees, so it deserves its own small design. Step 10 records the baseline. Opening `/file/` links from the app in a new tab already avoids it in the common path. |
| Q13 (rev 2) | The auth interface: move the cookie code into `serve/viewer_auth.py` now (Step 3a), or only define the interface and keep the code in `serve/viewer.py` until 8.0? | **Move it now**, as a behaviour-preserving commit before the hardening. 028 rewrites most of that code anyway (origin binding, logout, alarm, meta), so moving it first costs one commit and the hardening lands in its final home. Waiting means the 8.0 swap starts by untangling the guard, which is the cost the hedge exists to avoid. |

## 10. Review minors not fixed in 028 (rev 2)

The review's minors M1-M4 and M6-M9 are folded in (see "Rev 2 changes"). What is left, and why:

| Item | Decision | Reason |
|---|---|---|
| M5, links out of `/file/` land on `/login` | **Deferred** (Q12) | Pre-existing 006 behaviour; the fix changes the redirect contract and needs a loop guard. Step 10 records the baseline. |
| M8, per-principal prefs | **Deferred** (follow-up) | Project-wide prefs are accepted for go-live (§1.1): members are trusted to write, and the XSS corpus now covers prefs values. Per-principal prefs is a schema and API change. |
| I3's optional static revalidation | **Deferred** (Q11) | Not needed for correctness once Caddy queues; a performance follow-up. |
| Solo static's second decode (`backlog_server.py:11858`) | **Accepted** | Solo has no allow-list and keeps its traversal check, so a second decode reaches nothing solo doesn't already serve. Server mode no longer runs it (§1.2). |

Follow-ups to file when 028 merges: Q11 (static revalidation), Q12 (file-page link bounce), per-principal prefs.
