# User intent: generate the taskmaster-mods prototype artboards (.dc.html) as exact monospace cell grids from ONE
# shared component vocabulary, so every screen uses identical parts, every row is width-checked (no clipping),
# and everything stays portable back to Ink Box/Text.
import html, os, sys

OUT = sys.argv[1]
W = 116
IW = W - 4          # inside the pane frame
CWD = IW            # card width
CI = CWD - 4        # inside a card
CW, CH = 8.4, 18

CSS = """body{margin:0}
.t{font-family:'JetBrains Mono',monospace;font-size:14px;line-height:18px;letter-spacing:0;font-variant-ligatures:none}
.r{white-space:pre;height:18px;overflow:hidden}
.k{all:unset;white-space:pre;cursor:pointer;font:inherit}
.band{background:#1d1d1b}.ov{background:#272725}
.fb{color:#f5f3ed;font-weight:700}.fd{color:#d2cfc8}.fs{color:#a09c95}.fh{color:#7d7b76}.fx{color:#66645f}.fz{color:#4d4c48}
.bd{color:#343331}.bs{color:#4d4c48}
.sg{color:#5e79e6}.sv{color:#8a9eeb;font-weight:700}.svn{color:#8a9eeb}
.cr{color:#e06060;font-weight:700}.wa{color:#d69a2e;font-weight:700}.in{color:#7aa6d6;font-weight:700}.ok{color:#4fb071;font-weight:700}
.wan{color:#d69a2e}.crn{color:#e06060}.okn{color:#4fb071}
.pri{background:#5e79e6;color:#f5f3ed;font-weight:700}
.chip{background:rgba(94,121,230,0.20);color:#b5c2ee;font-weight:700}
.sel{background:#272725;color:#f5f3ed;font-weight:700}
.fld{background:#272725;color:#f5f3ed}
.hl{background:rgba(94,121,230,0.22);color:#f5f3ed;font-weight:700}
.k:hover{color:#f5f3ed}
.k:focus-visible{outline:1px solid #5e79e6}"""

# ---------- segments -> cells ----------
_bid = [0]
def T(t, c="fd"): return [(t, c, False)]
def BT(t, c):     return [(t, c, True)]
def L(*ps):
    o = []
    for p in ps: o += p
    return o
def ln(s): return sum(len(x[0]) for x in s)
def sp(n): return T(" " * n)
def fit(l, w, r=None, fc="fd"):
    r = r or []
    g = w - ln(l) - ln(r)
    assert g >= 0, (w, ln(l), ln(r), "".join(x[0] for x in l + r))
    return l + [(" " * g, fc, False)] + r
def trunc(text, n):
    return text if len(text) <= n else text[: n - 1] + "…"
def cells(segs):
    out = []
    for t, c, b in segs:
        bid = None
        if b:
            _bid[0] += 1; bid = _bid[0]
        out += [(ch, c, bid) for ch in t]
    return out

# ---------- the vocabulary ----------
# keycap rungs
def KP(k, lab): return BT(f" {k} {lab} ", "pri")                     # 1 primary
def KS(k, lab): return BT(k, "sv") + T(" " + lab)                     # 2 secondary
def KD(k, lab): return BT(f"{k} {lab}", "fz")                         # 4 disabled
def KX(k, lab): return BT(k, "wa") + T(" " + lab)                     # 5 destructive
def hint(pairs):                                                      # 3 nav / meta -> footer
    o = []
    for i, (k, w) in enumerate(pairs):
        if i: o += T(" · ", "fz")
        o += T(k, "fs") + T(" " + w, "fx")
    return o
def actions(*groups):        # action row: primary first, 3-space gaps, destructive last
    o = sp(2)
    for i, g in enumerate(groups):
        if i: o += sp(3)
        o += g
    return o

BADGE_W = 15
BADGES = {
    "BLOCKED": ("▲", "cr"), "ACTION": ("◆", "wa"), "IN REVIEW": ("●", "in"), "IN PROGRESS": ("●", "in"),
    "DONE": ("✓", "ok"), "INTERRUPTED": ("▲", "cr"), "WIP": ("◆", "wa"), "HANDED OFF": ("●", "in"),
    "OPEN": ("○", "fs"), "CLOSED": ("✓", "fx"), "RESUMED FROM": ("↳", "svn"), "WRITTEN HERE": ("↳", "svn"),
    "RUNNING": ("○", "okn"),
}
def badge(word, pad=True, dim=False):
    sym, c = BADGES[word]
    s = f"{sym} {word}"
    return T(s.ljust(BADGE_W) if pad else s, "fz" if dim else c)
def mark(state):             # selection mark: True picked, False not, None not selectable
    return T("■ ", "sg") if state else T("□ ", "fx") if state is False else T("  ")

def card_head(state, word, ident, title, meta, age, w, dim=False):
    left = L(mark(state), badge(word, dim=dim))
    if ident: left += T(ident + "  ", "fz" if dim else "fb")
    right = (T(meta + "   ", "fz" if dim else "fh") if meta else []) + T(age.rjust(5), "fz" if dim else "fs")
    room = w - ln(left) - ln(right) - 2
    left += T(trunc(title, room), "fz" if dim else ("fb" if not ident else "fd"))
    return fit(left, w, right)

def row(state, word, ident, title, meta, age, dim=False):   # card, collapsed (list row)
    return sp(2) + card_head(state, word, ident, title, meta, age, CWD - 4, dim) + sp(2)

def box(rows, w, bc, title=None, right=None, fill=None):
    iw = w - 4
    tl = [("╭─", bc, False)] + ((title + T(" ")) if title else [])
    tr = ((T(" ") + right) if right else []) + [("─╮", bc, False)]
    out = [tl + [("─" * (w - ln(tl) - ln(tr)), bc, False)] + tr]
    for r in rows:
        l, rt = r if isinstance(r, tuple) else (r, None)
        out.append([("│ ", bc, False)] + fit(l, iw, rt) + [(" │", bc, False)])
    out.append([("╰" + "─" * (w - 2) + "╯", bc, False)])
    return out

def card(state, word, ident, title, meta, age, body, acts, focused=True):   # card, expanded
    rows = [card_head(state, word, ident, title, meta, age, CI)] + body
    if acts is not None: rows += [[], acts]
    return box(rows, CWD, "sg" if focused else "bd")

def section(label, extra=""):
    return L(T(label, "fs"), T(("  " + extra) if extra else "", "fx"))

def phase_sel(name, count, opened=False):
    return L(T("‹ ", "fs"), BT(f" {name}  {count} {'▴' if opened else '▾'} ", "fld"), T(" ›", "fs"))

def toolbar(left, toggles=(), pager=True):
    r = []
    for t, on in toggles: r += BT(t, "svn" if on else "fx") + sp(3)
    if pager: r += T("‹ ", "fs") + T("1 / 1", "fs") + T(" ›", "fs") + sp(3)
    r += T("u ", "fs") + T("refresh", "fx")
    return fit(left, IW, r)

def bulk(caps, scopes):
    l = []
    for i, c in enumerate(caps):
        if i: l += sp(3)
        l += c
    r = []
    for i, s in enumerate(scopes):
        if i: r += T(" · ", "fz")
        r += BT(s, "fs")
    return fit(l, IW, r)

def footer(pairs): return fit(hint(pairs), IW)

TABS = [("1", "Review", "5"), ("2", "Handovers", "6"), ("3", "Session", ""), ("4", "Restore", "3")]
def tab_items(active=None, keycls="fz", labcls="fs", gap=3, dimzero=False):
    o = []
    for i, (k, n, c) in enumerate(TABS):
        if i: o += sp(gap)
        lab = n + (f" {c}" if c else "")
        if n == active: o += BT(f" {k} {lab} ", "sel")
        else: o += T(k + " ", keycls) + BT(lab, labcls)
    return o
def tabrow(active):
    return fit(sp(1) + tab_items(active), W, T("esc ", "fs") + T("close ", "fx"))

FRAME_TITLE = lambda name: T("◆ ", "sg") + T("TASKMASTER · ", "fs") + T(name, "fb")
def pane(name, inner, status=T("● ", "okn") + T("tm ok", "fs")):
    rows = []
    return rows + box(inner, W, "bd", FRAME_TITLE(name), status)

# ---------- render ----------
def render(rows, w=W):
    out = []
    for r in rows:
        cs = r if (r and isinstance(r[0], tuple) and len(r[0]) == 3 and isinstance(r[0][0], str) and len(r[0][0]) == 1 and (r[0][2] is None or isinstance(r[0][2], int))) and getattr(r, "_cells", False) else None
        out.append(r)
    return out

class Grid:
    def __init__(self): self.rows = []
    def add(self, segs, w=W, extra=""):
        segs = fit(segs, w) if ln(segs) < w else segs
        assert ln(segs) == w, (ln(segs), "".join(x[0] for x in segs))
        c = cells(segs)
        if extra: c = [(ch, cl + " " + extra if extra not in cl else cl, b) for ch, cl, b in c]
        self.rows.append(c)
    def addall(self, rows, **kw):
        for r in rows: self.add(r, **kw)
    def overlay(self, top, col, segs_rows, extra="ov"):
        for i, segs in enumerate(segs_rows):
            c = cells(segs)
            c = [(ch, cl + " " + extra, b) for ch, cl, b in c]
            r = self.rows[top + i]
            assert col + len(c) <= len(r)
            self.rows[top + i] = r[:col] + c + r[col + len(c):]
    def html(self):
        lines = []
        for r in self.rows:
            h, i = [], 0
            while i < len(r):
                ch, cl, b = r[i]; j = i
                while j < len(r) and r[j][1] == cl and r[j][2] == b: j += 1
                e = html.escape("".join(x[0] for x in r[i:j]))
                h.append(f'<button class="k {cl}" type="button">{e}</button>' if b else f'<span class="{cl}">{e}</span>')
                i = j
            lines.append('<div class="r">' + "".join(h) + "</div>")
        return "\n".join(lines)

def write(name, title, g, note):
    pad = 24
    gw = round(W * CW)
    width = gw + 2 * pad
    height = len(g.rows) * CH + 2 * pad + 80
    s = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700;800&amp;display=swap">
<style>
{CSS}
</style>
</helmet>
<div style="width: {width}px; height: {height}px; box-sizing: border-box; background: #151514; padding: {pad}px; overflow: hidden;">
<div class="t" style="color: #7d7b76; margin-bottom: 20px; font-size: 12px; line-height: 18px;">{html.escape(note)}</div>
<div class="t" style="width: {gw}px;">
{g.html()}
</div>
</div>
</x-dc>
<script type="text/x-dc" data-dc-script data-props='{{"$preview":{{"width":{width},"height":{height}}}}}'>
class Component extends DCLogic {{
renderVals() {{ return {{}}; }}
}}
</script>
</body>
</html>
"""
    with open(os.path.join(OUT, name), "w", encoding="utf-8", newline="\n") as f: f.write(s)
    print(name, width, height, title)


# ================= round 3 screens =================
# Layout rule: col 0-1 selection mark, col 2-16 label column (badge / section / field label), content from col 19.
# Collapsed items: 2-3 lines, no action keys, separated by a dim ┄ rule. Focused item: periwinkle box + action row.
# Actions are verb + object and sit next to their subject. Navigation lives in the sidebar (m).
PH = ("Patch 1.9 · active", "3")
LBL = 17   # mark(2) + badge(15)
GAP = 2
CC = LBL + GAP

def ctxcls(k):
    return "crn" if k >= 700 else "wan" if k >= 400 else "fs"
def ctx(k): return T(f"{k}k ctx", ctxcls(k))

def lab(text, cls="fs"):            # field label inside the content column
    return T(text + "  ", cls)

def item(state, word, ident, title, age, lines, w, dim=False):
    head = card_head(state, word, ident, title, "", age, w, dim)
    out = [head]
    for l in lines:
        out.append(sp(CC) + l)
    return out

def sep(w): return sp(2) + T("┄" * (w - 2), "bd")

def listing(items, w=IW):
    """items: list of row-lists (collapsed) or ('card', rows) focused. Separators between collapsed neighbours."""
    out = []
    for i, it in enumerate(items):
        if i and not (isinstance(items[i - 1], tuple) or isinstance(it, tuple)):
            out.append(sep(w))
        out += it[1] if isinstance(it, tuple) else it
    return out

def focused(state, word, ident, title, age, lines, acts):
    rows = item(state, word, ident, title, age, lines, CI) + [[]] + [sp(2) + a for a in acts]
    return ("card", box(rows, CWD, "sg"))

def section_row(label, note=""):
    return L(sp(2), T(label, "fs"), T(("  " + note) if note else "", "fx"))

def acts(*groups):
    o = []
    for i, gp in enumerate(groups):
        if i: o += sp(3)
        o += gp
    return o

FOOT_HEAD = [("j/k", "move"), ("s", "select")]
FOOT_TAIL = [("h/l", "phase"), ("f", "phases"), ("p/n", "page"), ("u", "refresh"), ("m", "sidebar"), ("esc", "close")]

# ================= SESSION (round-1 style, label column) =================
def srow(label, segs): return L(sp(2), T(label.ljust(LBL - 2 + GAP), "fs"), segs)
sess = [
    toolbar(L(T("this session", "fs"), T(" · feat/mods-ux · started 3h ago", "fx")), pager=False),
    [],
    srow("TASK", L(badge("IN REVIEW", False), T("  unified-chat-022  ", "fb"), T("Unified chat pre-build gets the full supervisor toolset; cook…"))),
    srow("", L(T("full · patch-1-9 · review-gate pass ", "fs"), T("✓", "okn"), T(" · ", "fs"), T("ready for done", "okn"))),
    srow("", L(lab("human", "svn"), T("Live check on dev (needs unifiedChatGenerate + unifiedChatBuild granted)"))),
    [],
    srow("", acts(KP("d", "mark task done"), KS("a", "send task to agent"))),
    sep(IW),
    srow("HANDOVER", L(badge("RESUMED FROM", False), T("  2026-10-04-audit-fixes-landed", "fd"), T(" · 20h ago", "fh"))),
    srow("", T("Audit fixes landed on the native branch", "fb")),
    srow("", L(lab("next", "svn"), T("Record the merge for tm-audit-030"))),
    [],
    srow("", acts(KS("c", "copy handover"), KS("h", "show in handovers"), KS("w", "resume in new tab"), KS("i", "handover decisions·blockers ▸"))),
    sep(IW),
    srow("SESSION", L(T("resumed from ", "fs"), T("2026-10-04-audit-fixes-landed"), T(" · part of the 16:14 burst · 3 others open", "fs"))),
    [],
    srow("", acts(KS("4", "show burst in restore"))),
    [],
    footer([("u", "refresh"), ("m", "sidebar"), ("esc", "close")]),
]
g = Grid(); g.addall(pane("SESSION", sess))
write("Session.dc.html", "Session tab", g,
      "SESSION — round-1 layout kept as the house style: left label column (TASK / HANDOVER / SESSION), content beside it, actions under the thing they act on, dim ┄ rules between sections. No tab row: panes are reached through the sidebar (m).")

# ================= REVIEW =================
rev = [
    toolbar(phase_sel(*PH)),
    L(T("5 waiting on you · newest 1h ago", "fs"), sp(4), badge("BLOCKED", False), T(" 1", "crn"), sp(3), badge("ACTION", False), T(" 4", "wan"), sp(3), badge("IN REVIEW", False, dim=True), T(" 0", "fz")),
    bulk([KD("b", "mark 0 tasks done"), KD("g", "send 0 tasks to agent")], ["all", "none"]),
    [],
] + listing([
    focused(False, "BLOCKED", "unified-chat-022", "Unified chat pre-build gets the full supervisor toolset; cookbook owns recipes", "1h", [
        L(T("patch-1-9 · lane full · ", "fs"), T("●", "okn"), T(" review-gate pass", "fs")),
        L(lab("human", "svn"), T("Live check on dev — needs unifiedChatGenerate + unifiedChatBuild")),
        L(T("1 ", "fx"), T("☐ first unified message stays on intent/brief and does not build")),
        L(T("2 ", "fx"), T("☐ second message builds with the full toolset")),
        L(sp(2), KS("t", "tick check 1-9"), sp(3), KS("c", "copy live check")),
    ], [acts(KP("d", "mark task done"), KS("a", "send task to agent"), KS("o", "open task"), KS("v", "task in viewer"), KS("i", "task detail ▸"))]),
    item(False, "ACTION", "tm-audit-031", "Handover auto-supersede keeps the newest per thread", "5h", [
        T("patch-1-9 · lane full · in review for 5h", "fs"),
        L(lab("human", "svn"), T("Confirm the newest handover survives on a 3-thread repo")),
    ], IW),
    item(False, "ACTION", "docs-012", "README mentions the review queue", "30h", [
        T("patch-1-9 · lane light · in review for 30h", "fs"),
        L(lab("human", "svn"), T("Proof-read the review-queue paragraph")),
    ], IW),
]) + [
    [],
    footer(FOOT_HEAD + [("t 1-9", "tick")] + FOOT_TAIL),
]
# pager 1 / 2 (3 items per page)
def toolbar(left, toggles=(), pager="1 / 1", refresh="refresh"):
    r = []
    for t, on in toggles: r += BT(t, "svn" if on else "fx") + sp(3)
    if pager: r += T("‹ ", "fs") + T(pager, "fs") + T(" ›", "fs") + sp(3)
    r += T("u ", "fs") + T(refresh, "fx")
    return fit(left, IW, r)
rev[0] = toolbar(phase_sel(*PH), pager="1 / 2")
g = Grid(); g.addall(pane("REVIEW", rev))
DW = 44
dd_items = [("0", "All", "5", False, False), ("1", "Patch 1.9 · active", "3", True, False), ("2", "Release 2.0", "2", False, False),
            ("3", "Patch 1.8 ✓", "0", False, True), ("4", "Patch 1.7 ✓", "0", False, True)]
dd = []
for k, n, c, on, dim in dd_items:
    seg = fit(L(T(k + "  ", "fx"), T(n, "fz" if dim else ("fb" if on else "fd"))), DW - 6, T(c, "fz" if dim else "fs"))
    dd.append(L(BT("".join(x[0] for x in seg), "hl")) if on else seg)
dd += [[], fit(L(BT("◐ show done phases", "svn")), DW - 6, T("on", "fs")),
       fit(hint([("↑↓", "move"), ("enter", "pick"), ("esc", "close")]), DW - 6)]
g.rows[1] = cells([("│ ", "bd", False)] + toolbar(phase_sel(*PH, opened=True), pager="1 / 2") + [(" │", "bd", False)])
g.overlay(2, 2, box(dd, DW - 2, "bs"))
write("Review.dc.html", "Review pane", g,
      "REVIEW — phase dropdown shown OPEN (f). Items are 3-line: badge + id + title / dim context / the human action. Focused item boxed with actions; checks carry their own t / c keys right under them. 3 per page.")

# ================= HANDOVERS =================
ho = [
    toolbar(phase_sel(*PH), toggles=[("◐ show closed", False)], pager="1 / 2"),
    L(T("5 open · newest 2h ago", "fs")),
    bulk([KD("b", "close 0 handovers")], ["all", "none"]),
    [],
] + listing([
    focused(False, "OPEN", "", "Shipped unified-chat-022 to dev", "2h", [
        T("2026-10-05-shipped-unified-chat-022 · main · 1 linked task", "fs"),
        L(lab("next", "svn"), T("Live check, then sign off in the review queue")),
        L(lab("task", "svn"), badge("IN REVIEW", False), T("  unified-chat-022  ", "fb"), T("Unified chat pre-build gets the full…"), T("  patch-1-9", "fs")),
    ], [acts(KP("r", "resume handover here"), KS("w", "resume in new tab"), KS("c", "copy handover"), KS("i", "decisions·blockers"), KX("x", "close handover"))]),
    item(False, "OPEN", "", "Audit fixes landed on the native branch", "20h", [
        T("2026-10-04-audit-fixes-landed · feat/database-native-foundation · 2 linked tasks", "fs"),
        L(lab("next", "svn"), T("Record the merge for tm-audit-030")),
    ], IW),
    item(False, "OPEN", "", "Viewer RR re-skin plan 2a closed", "44h", [
        T("2026-10-03-viewer-reskin-2a-closed · feat/viewer-reskin · no linked task", "fs"),
        L(lab("next", "svn"), T("Start the plan 2b token pass")),
    ], IW),
]) + [
    [],
    footer(FOOT_HEAD + [("b", "close selected")] + FOOT_TAIL),
]
g = Grid(); g.addall(pane("HANDOVERS", ho))
write("Handovers.dc.html", "Handovers pane", g,
      "HANDOVERS — same item form: title / slug · branch · linked tasks / next step. Focused card lists each linked task with its live status. r resume handover here is the one primary; x close handover is the destructive key, always last.")

# ================= RESTORE =================
def ritem(state, word, title, when, proj, branch, cwd, k, task_line, dim=False):
    l2 = L(T(f"{proj} · {branch} · {cwd} · ", "fz" if dim else "fs"), T(f"{k}k ctx", "fz" if dim else ctxcls(k)))
    return item(state, word, "", title, when, [l2] + ([task_line] if task_line else []), IW, dim)
def tl(status, tid, rest, dim=False):
    return L(badge(status, False, dim=dim), T(f"  {tid}", "fz" if dim else "fb"), T(" · " + rest, "fz" if dim else "fs"))
rs = [
    toolbar(phase_sel("All", "7"), toggles=[("◐ compact", False)], pager="1 / 3", refresh="rescan"),
    L(T("4 closed together · today 16:14–16:31 · 12m ago · 1 closed since", "fs"), sp(4), badge("INTERRUPTED", False), T(" 1", "crn"), sp(3), badge("WIP", False), T(" 2", "wan"), sp(3), badge("HANDED OFF", False), T(" 1", "in")),
    bulk([KS("b", "resume 3 sessions"), KS("g", "brief 3 sessions")], ["all", "unfinished", "none"]),
    [],
    section_row("UNFINISHED", "3 · interrupted or mid-work, no handover"),
] + listing([
    ritem(True, "INTERRUPTED", "Port row counter to native store", "16:29", "taskmaster", "feat/store-native", ".worktrees/store-native", 512,
          tl("IN PROGRESS", "tm-audit-033", 'last prompt "run the row-count probe again"')),
    ritem(True, "WIP", "Viewer RR re-skin plan 2b tokens", "16:22", "taskmaster", "feat/viewer-reskin", ".worktrees/viewer-reskin", 164,
          tl("IN PROGRESS", "viewer-rr-014", 'last reply "tokens.css split, 3 files left"')),
    ritem(False, "WIP", "Scanner cache version bump", "16:14", "taskmaster", "main", "repo root", 731,
          L(T("no task touched", "fx"), T(' · last prompt "bump the cache version and rerun"', "fs"))),
]) + [
    [],
    section_row("HANDED OFF", "1 · an open handover carries the next step"),
] + listing([
    focused(True, "HANDED OFF", "", "U5 brief approval", "16:31", [
        L(T("taskmaster · master · repo root · ", "fs"), ctx(378)),
        L(lab("task", "svn"), badge("IN PROGRESS", False), T("  unified-chat-027", "fb"), T(" · phase-b", "fs")),
        L(lab("handover", "svn"), T("H-0412", "fb"), T(" · next: run end-session to close 027, then Phase B of 009")),
        L(lab("via", "svn"), BT(" fresh from H-0412 ", "fld"), T("  --resume", "fx"), sp(3), KS("v", "switch resume mode")),
    ], [acts(KP("r", "resume session"), KS("i", "brief session"), KS("c", "copy resume command"))]),
]) + [
    [],
    section_row("IDLE", "1 · done or ended cleanly"),
] + listing([
    ritem(None, "DONE", "Release notes for 7.2.0", "16:41", "taskmaster", "master", "repo root", 88,
          tl("DONE", "docs-020", "end-session ran · closed after the burst", dim=True), dim=True),
]) + [
    [],
    section_row("RUNNING NOW", "2 · open in other terminals, not resumable here"),
] + listing([
    ritem(None, "RUNNING", "Mods UX stage 4 handovers pane", "3m", "taskmaster", "feat/mods-ux", ".worktrees/mods-ux", 221, None),
    ritem(None, "RUNNING", "Backlog validate after migration", "9m", "taskmaster", "main", "repo root", 47, None),
]) + [
    [],
    footer(FOOT_HEAD[:1] + [("s", "tick"), ("v", "resume mode")] + FOOT_TAIL[:3] + [("u", "rescan"), ("m", "sidebar"), ("esc", "close")]),
]
g = Grid(); g.addall(pane("RESTORE", rs))
write("Restore.dc.html", "Restore pane", g,
      "RESTORE — grouped UNFINISHED / HANDED OFF / IDLE / RUNNING NOW. Each item: title + closed time / project · branch · cwd · ctx (≥400k warning, ≥700k critical) / bound task + status or last prompt. ■ default ticks on unfinished + resumable.")

# ================= MAIN =================
g = Grid()
def label(t, sub=""): return L(T(t, "fb"), T(("   " + sub) if sub else "", "fs"))
CHIP = BT(" ◆ TASKMASTER ", "chip")
SIDEBTN = KS("m", "open sidebar")
RCW = 34   # fixed right action column: keys left-aligned inside it on every band line
def rcol(*groups):
    o = []
    for i, gp in enumerate(groups):
        if i: o += sp(3)
        o += gp
    return fit(o, RCW)
def band(l1_left, title, r1, l2=None, r2=None):
    room = W - ln(l1_left) - RCW - 4
    g.add(fit(l1_left + T(trunc(title, room), "fb"), W, rcol(*r1) + sp(1), "band"), extra="band")
    if l2 is not None:
        g.add(fit(l2, W, rcol(*(r2 or [])) + sp(1), "band"), extra="band")
def prompt():
    for r in [T("─" * W, "bs"), T("❯ ", "fs") + T("▏", "sv"), T("─" * W, "bs")]: g.add(r)
TASK_L = L(sp(1), CHIP, sp(2), badge("IN REVIEW", False), sp(2), T("unified-chat-022  ", "fh"))
TTL = "Unified chat pre-build gets the full supervisor toolset; cookbook owns recipes"

g.add(label("BAND", "subject left, keys in one right column · waiting counts live in the sidebar, not here"))
g.add([])
band(TASK_L, TTL, [KS("d", "mark task done"), SIDEBTN], L(sp(3), T("↳ resumed from ", "fs"), T("2026-10-04-audit-fixes-landed", "svn"), T(" · 20h · ", "fs"), T("Audit fixes landed…")), [KS("c", "copy handover")])
g.add([]); prompt(); g.add([])
g.add(label("BAND · STATES", "no handover · nothing bound (one line) · tm offline"))
g.add([])
band(L(sp(1), CHIP, sp(2), badge("IN PROGRESS", False), sp(2), T("tm-audit-031  ", "fh")), "Handover auto-supersede keeps the newest per thread", [SIDEBTN],
     L(sp(3), T("↳ no handover yet for this task", "fx")), [])
g.add([])
g.add(fit(L(sp(1), CHIP, sp(2), T("no task bound to this session", "fx")), W, rcol(SIDEBTN) + sp(1), "band"), extra="band")
g.add([])
g.add(fit(L(sp(1), CHIP, sp(2), T("▲ tm offline", "cr"), T(" · last ok 2m ago · counts paused", "fs")), W, rcol(KS("r", "retry tm connection")) + sp(1), "band"), extra="band")
g.add([])
g.add(T("─" * W, "bd"))

# sidebar open, docked left over a terminal mock
g.add(label("SIDEBAR · open (m)", "docked left, overlays the conversation; the only navigation surface for all panes"))
g.add([])
convo = [
    T(" "*36 + "● Read hooks/handovers.tsx", "fx"),
    T(" "*36 + "● Handovers now group by linked-task phase; wiring the close toast.", "fs"),
    [], T(" "*36 + "● Update(hooks/handovers.tsx)", "fx"), T(" "*38 + "⎿  Updated with 14 additions and 3 removals", "fz"), [],
    T(" "*36 + "● Ran the mods test suite: 212 passed.", "fs"), [], [], [], [], [],
]
top = len(g.rows)
for r in convo: g.add(r)
band(TASK_L, TTL, [KS("d", "mark task done"), SIDEBTN], L(sp(3), T("↳ resumed from ", "fs"), T("2026-10-04-audit-fixes-landed", "svn"), T(" · 20h · ", "fs"), T("Audit fixes landed…")), [KS("c", "copy handover")])
prompt()
SBW = 34
def sbrow(k, name, count, cur=False, note=""):
    left = L(T(k + "  ", "fx" if not cur else "fb"), T(name, "fb" if cur else "fd"))
    right = (T(note + " ", "fx") if note else []) + T(count.rjust(2), "fs" if not cur else "fb")
    s = fit(left, SBW - 6, right)
    return L(BT("".join(x[0] for x in s), "hl")) if cur else s
sb = box([
    [],
    sbrow("1", "Review", "5", note="▲1 ◆4"),
    sbrow("2", "Handovers", "6", note="newest 2h"),
    sbrow("3", "Session", "", cur=True),
    sbrow("4", "Restore", "3", note="burst 16:14"),
    [],
    T("unified-chat-022 · in review", "fx"),
    [],
    fit(hint([("↑↓", "move"), ("enter", "open")]), SBW - 6),
    fit(hint([("1-4", "jump"), ("esc", "close")]), SBW - 6),
], SBW - 2, "bs", T("◆ ", "sg") + T("TASKMASTER", "fb"))
g.overlay(top, 0, sb)
g.add([])
g.add(T("─" * W, "bd"))

# component sheet
g.add(label("COMPONENT SHEET", "each element once, in the only form a screen may use"))
g.add([])
def sheet(name, demo_rows, rule):
    demo_rows = demo_rows if (demo_rows and isinstance(demo_rows[0], list)) else [demo_rows]
    DWID = 64
    for i, d in enumerate(demo_rows):
        left = T((name if i == 0 else "").ljust(12), "fb") + fit(d, DWID)
        rr = rule if i == 0 else ""
        if len(rr) > W - ln(left) - 2: print("LONG", len(rr), ascii(rr))
        g.add(L(left, sp(2), T(trunc(rr, W - ln(left) - 2), "fs")))
sheet("FRAME", L(T("╭─ ", "bd"), FRAME_TITLE("REVIEW"), T(" ─────────── ", "bd"), T("● ", "okn"), T("tm ok", "fs"), T(" ─╮", "bd")),
      "no tab row; pane named in title")
sheet("BAND", [fit(L(CHIP, T(" ● uc-022 Unified chat…", "fb")), 64, L(KS("d", "mark task done"), sp(2), KS("m", "sidebar"))),
               fit(L(sp(2), T("↳ ", "svn"), T("audit-fixes-landed · 20h", "fs")), 64, fit(KS("c", "copy handover"), 27))],
      "subject left; keys in a right column")
sheet("SIDEBAR", [sbrow("1", "Review", "5", note="▲1 ◆4"), sbrow("3", "Session", "", cur=True)], "m opens; key · pane · note · count")
sheet("TOOLBAR", fit(phase_sel(*PH), 64, L(BT("◐ show closed", "fx"), sp(2), T("‹ 1 / 2 ›", "fs"), sp(2), T("u", "fs"))),
      "row 1: phase left; toggle, pager, u")
sheet("SUMMARY", L(T("5 waiting on you · newest 1h ago", "fs"), sp(3), badge("BLOCKED", False), T(" 1", "crn")), "row 2: sentence, then tallies")
sheet("BULK", fit(L(KD("b", "mark 0 tasks done"), sp(3), KS("g", "send 2 tasks to agent")), 64, L(BT("all", "fs"), T(" · ", "fz"), BT("none", "fs"))),
      "row 3: count in the label; dim at 0")
sheet("SECTION", section_row("HANDED OFF", "1 · open handover"), "label column, dim note")
sheet("BADGE", L(badge("BLOCKED", False), sp(2), badge("ACTION", False), sp(2), badge("IN REVIEW", False)), "symbol + word, semantic fg")
sheet("ITEM", [card_head(False, "ACTION", "docs-012", "README mentions the review queue", "", "30h", 64),
               sp(CC) + T("patch-1-9 · lane light", "fs"),
               sp(CC) + L(lab("human", "svn"), T("Proof-read the paragraph")),
               sep(64)],
      "title / context / detail; ┄; no keys")
sheet("CARD", box([card_head(False, "BLOCKED", "uc-022", "Unified chat pre-build", "", "1h", 56),
                   sp(CC) + T("patch-1-9 · lane full", "fs"), [],
                   sp(2) + acts(KP("d", "mark task done"), KS("a", "send to agent"))], 60, "sg"),
      "focused only: box + action row")
sheet("FIELD", L(sp(CC), lab("next", "svn"), T("Record the merge")), "lowercase label beside its value")
sheet("SELECTION", L(T("■ ", "sg"), T("picked   ", "fd"), T("□ ", "fx"), T("not", "fs")), "mark column only")
sheet("KEY 1", KP("d", "mark task done"), "primary: one fill per context")
sheet("KEY 2", KS("c", "copy handover"), "secondary: lit letter, plain label")
sheet("KEY 3", hint([("j/k", "move"), ("p/n", "page")]), "nav/meta: footer only, never capped")
sheet("KEY 4", KD("b", "close 0 handovers"), "disabled: whole cap dim")
sheet("KEY 5", KX("x", "close handover"), "destructive: warning letter, y/n, last")
sheet("LABELS", L(KS("c", "copy handover"), T("  not  ", "fx"), T("c copy", "fz")), "verb + object, beside its subject")
sheet("FOOTER", hint([("j/k", "move"), ("s", "select"), ("h/l", "phase"), ("m", "sidebar")]), "last row; same order everywhere")
sheet("EMPTY", L(T("Nothing waiting in Patch 1.8 · done 10-01   ", "fs"), KS("0", "show all phases")), "sentence + one way out")
sheet("OFFLINE", L(T("▲ tm offline", "cr"), T(" · last ok 2m ago   ", "fs"), KS("r", "retry tm connection")), "critical word + retry")
write("Main.dc.html", "Band and components", g,
      "BAND (direction C, navigation removed: one m key opens the sidebar) and its states; the SIDEBAR open over a terminal; the COMPONENT SHEET with the 3-line list item and the verb + object labelling rule.")
