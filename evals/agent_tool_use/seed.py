# /// script
# requires-python = ">=3.11"
# dependencies = ["fastmcp>=3.4,<4", "httpx", "pydantic>=2", "pyyaml"]
# ///
# User intent: build a realistic, backdated scratch backlog (outside the repo) that the agent
# tool-use eval runs against, and render the scenarios + ground truth for the day it was seeded —
# so "yesterday" in a request is really yesterday in the data and the answers are computed, not typed.
"""Seed the agent tool-use eval backlog.

    uv run evals/agent_tool_use/seed.py [--home DIR] [--force]

Writes `<home>/seed/` (a taskmaster project), `<home>/scenarios.json`, `<home>/ground_truth.json`
and `<home>/seed_manifest.json`. Everything is written through the server's own public tool
functions, in process, under a fake clock: the MCP tools cannot backdate, and the clock is the
only way to keep a handover's id date, `date`, `created`, the thread registry and the store's
change log all telling the same story. Nothing in the taskmaster package is modified on disk.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evalpaths  # noqa: E402

REAL_START = _dt.datetime.now(_dt.timezone.utc)
TODAY = _dt.date.today()

# ── fake clock ────────────────────────────────────────────────────────────
CLOCK = [REAL_START]


class _DTMeta(type(_dt.datetime)):
    def __instancecheck__(cls, obj):
        return isinstance(obj, _dt.datetime)


class FakeDatetime(_dt.datetime, metaclass=_DTMeta):
    @classmethod
    def now(cls, tz=None):
        CLOCK[0] += _dt.timedelta(seconds=1)  # every stamp is distinct and ordered
        return CLOCK[0].astimezone(tz) if tz else CLOCK[0].astimezone().replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return cls.now(_dt.timezone.utc).replace(tzinfo=None)


class _DMeta(type(_dt.date)):
    def __instancecheck__(cls, obj):
        return isinstance(obj, _dt.date)


class FakeDate(_dt.date, metaclass=_DMeta):
    @classmethod
    def today(cls):
        return CLOCK[0].astimezone().date()


def patch_clock() -> None:
    """Point every loaded taskmaster module's `datetime`/`date` at the fake clock.

    Re-run before each write because the server imports some modules lazily.
    """
    for name, mod in list(sys.modules.items()):
        if mod is None or not (name == "taskmaster" or name.startswith("taskmaster.")):
            continue
        if getattr(mod, "datetime", None) is _dt.datetime:
            mod.datetime = FakeDatetime
        if getattr(mod, "date", None) is _dt.date:
            mod.date = FakeDate


def day(offset: int) -> _dt.date:
    return TODAY + _dt.timedelta(days=offset)


def at(offset: int, hhmm: str) -> None:
    """Move the clock to `hhmm` UTC on today+offset. Day 0 is clamped to stay in the past."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    moment = _dt.datetime.combine(day(offset), _dt.time(hour, minute), _dt.timezone.utc)
    if offset == 0:
        moment = REAL_START - _dt.timedelta(minutes=20)
        if moment.astimezone().date() != TODAY:
            moment = REAL_START - _dt.timedelta(minutes=1)
    if moment <= CLOCK[0] and CLOCK[0] is not REAL_START:
        raise SystemExit(f"timeline goes backwards at day {offset} {hhmm}")
    CLOCK[0] = moment


# ── content helpers ───────────────────────────────────────────────────────
def body(decisions=(), blockers=(), start=(), open_threads=(), notes=()) -> str:
    parts = []
    for title, items in (("Decisions", decisions), ("Notes", notes), ("Blockers", blockers),
                         ("Where I'd start", start), ("Open threads", open_threads)):
        if items:
            parts.append(f"## {title}\n" + "\n".join(f"- {item}" for item in items))
    return "\n\n".join(parts) + "\n"


HANDOVERS: dict[str, str] = {}   # key -> id
BS = None                        # taskmaster.backlog_server, imported after TASKMASTER_ROOT is set


def ok(result: str) -> str:
    if re.match(r"\s*(Error|Cannot|No backlog|Handover not found)", result or ""):
        raise SystemExit(f"seed step failed: {result}")
    return result


def call(tool: str, /, **kwargs) -> str:
    patch_clock()
    return ok(getattr(BS, tool)(**kwargs))


def handover(key: str, *, tldr: str, next_action: str = "", text: str = "", thread: str = "",
             tasks=(), kind: str = "end-of-day", branch: str = "", tip: str = "") -> str:
    options = {k: v for k, v in (("branch", branch), ("tip_commit", tip)) if v}
    result = call("backlog_handover_create", tldr=tldr, next_action=next_action, body=text,
                  task_ids=list(tasks), session_kind=kind, thread=thread, options=options or None)
    hid = re.match(r"Handover written: (\S+)", result).group(1)
    if not hid.startswith(FakeDate.today().isoformat()):
        raise SystemExit(f"{key}: id {hid} does not carry the clock's date")
    HANDOVERS[key] = hid
    return hid


def auto_stage(key: str, thread: str, task: str, branch: str, files: int, what: str) -> None:
    handover(key, kind="auto-stage", thread=thread, tasks=[task], branch=branch,
             tldr=f"auto-stage: {files} files staged on {branch} ({what})",
             text=f"Checkpoint written by the pre-compaction hook. {files} files staged on `{branch}`: {what}.\n")


def task(title: str, epic: str, phase: str, priority: str = "medium", notes: str = "", depends_on: str = "",
         **options) -> str:
    result = call("backlog_add_task", title=title, epic=epic, phase=phase, priority=priority, notes=notes,
                  depends_on=depends_on, options=options or None)
    return re.search(rf"\b{re.escape(epic)}-\d{{3}}\b", result).group(0)


def bug(title: str, found_in: str, severity: str, text: str, components=(), location=()) -> str:
    result = call("backlog_bug_create", title=title, found_in=found_in, severity=severity, body=text,
                  components=list(components), location=list(location))
    return re.search(r"\bB-\d+\b", result).group(0)


def created_id(prefix: str, result: str) -> str:
    return re.search(rf"\b{prefix}-\d+\b", result).group(0)


def issue(title: str, severity: str, **fields) -> str:
    return created_id("ISS", call("backlog_issue_create", title=title, severity=severity, **fields))


def decision(title: str, options: list, **fields) -> str:
    return created_id("DEC", call("backlog_decision_create", title=title, options=options, **fields))


def idea(title: str, text: str, tags: list, status: str = "parking-lot", **fields) -> str:
    return created_id("IDEA", call("backlog_idea_create", title=title, body=text, tags=tags, status=status, **fields))


def note(text: str, pinned: bool = False) -> str:
    return created_id("NOTE", call("backlog_note", action="create", text=text, pinned=pinned))


def start_task(task_id: str) -> None:
    call("backlog_update_task", task_id=task_id, field="status", value="in-progress")


def finish_task(task_id: str, **summary) -> None:
    """Pass the review gates the default lane requires, then complete with a session summary."""
    for gate in ("spec-review", "plan-review", "design-review", "review-gate"):
        call("backlog_record_gate", task_id=task_id, gate=gate, verdict="pass")
    call("backlog_complete_task", task_id=task_id, **summary)


# ── the timeline ──────────────────────────────────────────────────────────
def build() -> dict:
    T: dict[str, str] = {}
    B: dict[str, str] = {}

    # Two weeks ago: project set up, the work planned.
    at(-13, "07:05")
    call("backlog_init", project_name="Kilnworks")
    call("backlog_add_phase", phase_id="alpha", name="Alpha — internal content pipeline")
    call("backlog_add_phase", phase_id="beta", name="Beta — external studios")
    call("backlog_area_create", area_id="pipeline", name="Asset pipeline",
         description="Generation, validation, caching and delivery code.", anchors=["pipeline/**", "agents/generation/**"])
    call("backlog_area_create", area_id="brief-tool", name="Brief tooling",
         description="Everything that reads a game design doc and produces a brief or an export.", anchors=["brief/**"])
    call("backlog_area_create", area_id="ci", name="CI and test infrastructure",
         description="Workflows, runners and shared test fixtures.", anchors=[".github/workflows/**", "tests/fixtures/**"])
    call("backlog_add_epic", epic_id="asset-pipeline", name="Asset Pipeline", status="active", area="pipeline",
         done_when="A generated asset goes from prompt to in-engine preview without a human fixing metadata",
         description="Generation, validation, caching and delivery of game assets.")
    call("backlog_add_epic", epic_id="design-docs", name="Design Docs Tooling", status="active", area="brief-tool",
         done_when="A designer can get a one-page brief and a PDF out of any game design doc",
         description="Tools that read the game design document (GDD) and produce briefs and exports.")
    call("backlog_add_epic", epic_id="ci-health", name="CI Health", status="active", area="ci",
         done_when="Main is green for a week without reruns and a cold CI run is under two minutes")

    T["contract"] = task("Expose the asset contract as a callable tool for the generation agent", "asset-pipeline",
                         "alpha", "high", "The generation agent should ask for the contract of an asset type "
                         "(required fields, limits, naming) instead of carrying it in its prompt.")
    T["thumbs"] = task("Thumbnail cache v2: content-addressed previews", "asset-pipeline", "alpha", "high",
                       "Preview thumbnails are regenerated on every re-import. Key them by content hash.")
    T["fallback"] = task("Fail over to the secondary image provider on timeout", "asset-pipeline", "alpha", "medium",
                         "Primary provider times out under batch load; route to the secondary instead of failing the job.",
                         anchors="pipeline/providers/**")
    T["atlas"] = task("Texture atlas memory budget per scene", "asset-pipeline", "beta", "medium",
                      "Decide and enforce how much atlas memory a scene may use.")
    T["brief"] = task("GDD brief generator tool", "design-docs", "alpha", "high",
                      "Turn a game design doc into a one-page brief: pillars, loops, scope, open questions.")
    T["linking"] = task("Link GDD sections to backlog tasks", "design-docs", "beta", "low",
                        "A section of the design doc should know which tasks implement it.")
    T["pdf"] = task("Export the brief to PDF", "design-docs", "alpha", "medium", "Designers send briefs to publishers as PDF.")
    T["flakes"] = task("Quarantine the flaky importer tests", "ci-health", "alpha", "high",
                       "test_importer_roundtrip fails about one run in five on the Windows runners.")
    T["uvcache"] = task("Cache uv environments in CI", "ci-health", "alpha", "medium",
                        "Cold runs spend most of their time resolving and installing.")

    start_task(T["flakes"])
    I: dict[str, str] = {}
    D: dict[str, str] = {}
    N: dict[str, str] = {}
    IDEAS: dict[str, str] = {}
    I["flakes"] = issue("Importer tests fail intermittently on the Windows runners", "P1",
                        evidence="Recurring: test_importer_roundtrip failed 9 of the last 45 runs on main, Windows runners only.",
                        impact="Main cannot be trusted; every merge needs a rerun.", components=["ci"],
                        related_tasks=[T["flakes"]], discovered_by="CI history",
                        body="## Repro\n- Run `pytest tests/importer -p no:randomly --count 40` on a Windows runner.\n"
                             "- Expect 5 to 9 failures in test_importer_roundtrip.\n")
    at(-13, "15:40")
    handover("e1", thread="ci-flake-hunt", tasks=[T["flakes"]], branch="fix/importer-flakes", tip="3f9a1c2",
             tldr="Importer tests flake on Windows runners: reproduced locally, three suspects",
             next_action="Bisect the three suspects by running test_importer_roundtrip 200x with each fixture disabled",
             text=body(
                 notes=["Reproduced the failure 7 times in 40 local runs with `pytest -p no:randomly -x --count 40`.",
                        "Suspects: tmp dir cleanup racing the watcher thread, a shared module-level cache, clock skew in mtime comparisons."],
                 blockers=["None — just slow to reproduce."],
                 start=["Disable the watcher fixture first; it is the only one that spawns a thread."],
                 open_threads=["The Linux runners never fail. Do not spend time there."]))

    at(-12, "08:20")
    start_task(T["atlas"])
    auto_stage("x1", "ci-flake-hunt", T["flakes"], "fix/importer-flakes", 3, "watcher fixture join + two test edits")
    at(-12, "11:10")
    handover("f1", thread="atlas-budget", tasks=[T["atlas"]], branch="spike/atlas-budget", tip="77c0e4d",
             tldr="Texture atlas budget: measured the five heaviest scenes and settled the cap",
             next_action="Write the import-time check that fails a scene whose atlases exceed the cap",
             text=body(
                 decisions=["Atlas memory is capped at 96 MB per scene. The heaviest shipped scene (harbor_night) sits at 81 MB, so the cap leaves headroom without letting the forest scenes keep growing.",
                            "Atlas pages are limited to 2048x2048 on mobile targets and 4096x4096 on desktop.",
                            "The check runs at import, not at build: a build-time failure is found a day too late."],
                 notes=["Measured with the engine's memory report, compressed formats, mips included.",
                        "forest_dawn is at 118 MB today and will fail the check until its foliage atlas is split."],
                 blockers=["Parked until beta — nobody is free to split the forest atlases this phase."],
                 start=["`pipeline/atlas/budget.py` has the measuring code; the check itself is not written."],
                 open_threads=["Should UI atlases count against the scene? Left out for now."]))
    at(-12, "15:55")
    handover("e2", thread="ci-flake-hunt", tasks=[T["flakes"]], branch="fix/importer-flakes", tip="9b2d7e0",
             tldr="Flake hunt: tmp dir cleanup races the watcher thread in the importer fixtures",
             next_action="Join the watcher thread in fixture teardown, then rerun the 200x loop",
             text=body(
                 decisions=["The race is real: with the watcher fixture disabled the test passed 200 of 200.",
                            "Fix the fixture rather than add a retry — a retry hides the same race in production code."],
                 start=["`tests/fixtures/importer.py::watcher` — teardown returns before the thread exits."],
                 open_threads=["Two other tests share the fixture and are probably flaky for the same reason."]))

    at(-11, "09:00")
    start_task(T["thumbs"])
    at(-11, "10:30")
    handover("c1", thread="thumb-cache-v2", tasks=[T["thumbs"]], branch="feature/thumb-cache-v2", tip="1e44b9a",
             tldr="Thumbnail cache v2: design settled on content-addressed keys",
             next_action="Implement key derivation in pipeline/thumbs/keys.py and a read-through wrapper around the old cache",
             text=body(
                 decisions=["A thumbnail is keyed by sha256(source bytes + render settings), not by asset path. Renames and re-imports of identical content then cost nothing.",
                            "The old path-keyed cache stays readable during migration; nothing is deleted until v2 has served a full week."],
                 notes=["Today a re-import of an unchanged asset regenerates its thumbnail: 40% of preview time on a full sync."],
                 start=["Write keys.py first; everything else hangs off the key."],
                 open_threads=["Render settings need a stable serialisation before they can be hashed."]))
    at(-11, "14:45")
    handover("e3", thread="ci-flake-hunt", tasks=[T["flakes"]], branch="fix/importer-flakes", tip="c5a8f13",
             tldr="Flaky importer tests fixed at the fixture; main green 12 runs in a row",
             next_action="Nothing further — reopen only if test_importer_roundtrip fails again",
             text=body(
                 decisions=["Watcher fixture now joins its thread in teardown. No test is quarantined after all; the fixture was the bug."],
                 notes=["200 of 200 locally, 12 consecutive green runs on the Windows runners."],
                 open_threads=["None."]))
    finish_task(T["flakes"], session_title="CI: importer flakes fixed at the fixture",
         done="Found the tmp-dir/watcher race in the importer fixtures\nFixture joins its thread on teardown\n12 green Windows runs in a row",
         decisions="Fix the fixture instead of adding retries", issues="None", tasks_touched=T["flakes"])
    call("backlog_issue_update", issue_id=I["flakes"], field="fixed_in_task", value=T["flakes"])
    call("backlog_issue_update", issue_id=I["flakes"], field="status", value="fixed")
    call("backlog_handover_update_status", handover_id=HANDOVERS["e3"], status="closed", reason="flakes fixed, task done")

    at(-10, "08:15")
    start_task(T["contract"])
    start_task(T["brief"])
    at(-10, "12:00")
    auto_stage("x2", "thumb-cache-v2", T["thumbs"], "feature/thumb-cache-v2", 5, "keys.py and its tests")
    at(-10, "15:20")
    handover("a1", thread="contract-as-tool", tasks=[T["contract"]], branch="feature/contract-tool", tip="2aa90d1",
             tldr="Contract tool spike: registry serves an asset type's contract, agent can call it",
             next_action="Replace the hard-coded sprite contract with a registry lookup for every asset type",
             text=body(
                 decisions=["The asset contract (required fields, size limits, naming rules) becomes a tool the generation agent calls, instead of 900 tokens pasted into every prompt.",
                            "One tool, `get_asset_contract(asset_type)`, returning JSON. No per-type tools."],
                 notes=["Spike only covers `sprite`. The agent called it unprompted in 9 of 10 trial generations."],
                 start=["`pipeline/contract/registry.py` — the registry exists, only sprite is registered."],
                 open_threads=["What should the tool say for an asset type it does not know?"]))
    at(-10, "16:05")
    handover("b1", thread="gdd-brief-tool", tasks=[T["brief"]], branch="feature/gdd-brief", tip="a07c3be",
             tldr="GDD brief: outline extraction works on the sample design doc",
             next_action="Feed the extracted outline into the brief template and see what is missing",
             text=body(
                 decisions=["The brief is built from the design doc's heading outline plus the first paragraph under each heading — no summarising model call for the first version."],
                 notes=["Works on docs/samples/emberfall_gdd.md (14 sections). Headings deeper than level 3 are ignored."],
                 start=["`brief/outline.py` is done; `brief/template.py` is an empty stub."],
                 open_threads=["Design docs written as tables rather than prose will need separate handling."]))

    at(-9, "09:30")
    start_task(T["pdf"])
    D["naming"] = decision(
        "Naming scheme for generated asset files",
        ["<name>_<type>_<variant>.<ext>, no version in the name",
         "<type>_<name>_<variant>_v###.<ext>, version suffix kept in the name",
         "content hash as the file name, readable names only in metadata"],
        recommendation=2, task_id=T["contract"],
        body="Generated files currently get whatever name the prompt produced. The contract needs one rule.\n"
             "Constraints: the asset browser sorts by file name; artists re-generate the same asset many times.")
    at(-9, "13:10")
    auto_stage("x3", "contract-as-tool", T["contract"], "feature/contract-tool", 4, "registry entries for mesh and audio")
    at(-9, "15:00")
    handover("g1", tasks=[T["pdf"]], branch="feature/brief-pdf", tip="5d1f0aa",
             tldr="PDF export of the brief: chose the HTML-to-PDF route over a layout library",
             next_action="Wire the brief HTML through the headless renderer and check page breaks on a long brief",
             text=body(
                 decisions=["Render the brief to HTML and print it to PDF headlessly. A layout library would mean maintaining a second template."],
                 blockers=["Fonts: the renderer falls back to a system font on the CI image."],
                 start=["`brief/export/pdf.py` — the function signature is there, the body is not."]))

    at(-8, "10:00")
    start_task(T["fallback"])
    call("backlog_decision", action="resolve", decision_id=D["naming"], resolved_with=2,
         rationale="Type first so the asset browser groups sprites, meshes and audio together; the version suffix "
                   "keeps earlier generations side by side instead of overwriting them.")
    handover("c2", thread="thumb-cache-v2", tasks=[T["thumbs"]], branch="feature/thumb-cache-v2", tip="8c3e5f7",
             tldr="Thumb cache v2: key derivation done, read-through wrapper serves old and new entries",
             next_action="Write the migration that re-keys existing thumbnails without regenerating them",
             text=body(
                 decisions=["Render settings are serialised as sorted-key JSON before hashing, so two equal settings objects always hash the same.",
                            "The wrapper looks in v2 first and falls back to the path-keyed cache, copying the thumbnail forward on a hit."],
                 notes=["Full-sync preview time on the sample project: 212 s before, 131 s with the wrapper."],
                 start=["`pipeline/thumbs/migrate.py` is not started."],
                 open_threads=["Copy-forward doubles disk use until the old cache is dropped."]))
    at(-8, "14:30")
    auto_stage("x4", "gdd-brief-tool", T["brief"], "feature/gdd-brief", 2, "brief template first pass")
    at(-8, "16:10")
    handover("d1", thread="provider-fallback", tasks=[T["fallback"]], branch="feature/provider-fallback", tip="e19b6c4",
             tldr="Provider failover: timeout budget and retry policy drafted",
             next_action="Implement the router: one attempt on the primary, then the secondary, never back",
             text=body(
                 decisions=["A generation job gets 30 s on the primary image provider, then moves to the secondary. It never returns to the primary inside the same job.",
                            "No retry on the primary: its timeouts come in bursts, so a retry mostly waits a second time."],
                 start=["`pipeline/providers/router.py` — new file."],
                 open_threads=["The two providers do not accept the same parameters; a mapping layer is needed."]))

    at(-7, "07:40")
    handover("b2", thread="gdd-brief-tool", tasks=[T["brief"]], branch="feature/gdd-brief", tip="b6e2d90", kind="context-handoff",
             tldr="GDD brief: template renders pillars and core loop; scope section still empty",
             next_action="Fill the scope section from the doc's 'Features' and 'Out of scope' headings",
             text=body(
                 notes=["Morning checkpoint before a long refactor of the template.",
                        "Pillars and core loop render correctly for the sample doc."],
                 start=["`brief/template.py::render_scope` raises NotImplementedError."]))
    at(-7, "13:00")
    handover("g2", tasks=[T["pdf"]], branch="feature/brief-pdf", tip="0f7a2c8",
             tldr="PDF export of the brief shipped: fonts bundled, page breaks respected",
             next_action="Nothing further on export; revisit only if briefs grow past two pages",
             text=body(
                 decisions=["Fonts are bundled with the tool instead of relying on the CI image."],
                 notes=["A three-page brief breaks cleanly between sections."]))
    finish_task(T["pdf"], session_title="Design docs: brief exports to PDF",
         done="HTML-to-PDF export wired through the headless renderer\nFonts bundled\nPage breaks fall between sections",
         decisions="HTML-to-PDF instead of a layout library", issues="CI image lacked the fonts", tasks_touched=T["pdf"],
         patchnote="Briefs can be exported to PDF.")
    at(-7, "15:50")
    handover("b3", thread="gdd-brief-tool", tasks=[T["brief"]], branch="feature/gdd-brief", tip="d41c9b3",
             tldr="GDD brief: scope section filled from Features / Out of scope; brief is end-to-end on the sample",
             next_action="Run the brief on the three real design docs and list what breaks",
             text=body(
                 decisions=["'Out of scope' is printed even when empty — an empty list is itself information for a publisher."],
                 notes=["End-to-end on the sample doc in 0.4 s."],
                 start=["The three real docs are in docs/gdd/; none has been tried yet."],
                 open_threads=["One of them is mostly tables."]))

    at(-6, "09:15")
    handover("a2", thread="contract-as-tool", tasks=[T["contract"]], branch="feature/contract-tool", tip="6be07a5",
             tldr="Contract tool: every asset type registered; validation endpoint returns structured errors",
             next_action="Decide the error taxonomy so the agent can tell a fixable error from a fatal one",
             text=body(
                 decisions=["Validation is a second tool, `validate_asset(asset_type, metadata)`. It returns a list of `{field, rule, got, expected}` rather than a sentence, so the agent can repair field by field."],
                 notes=["Registered: sprite, mesh, audio, material, animation."],
                 start=["`pipeline/contract/validate.py`"],
                 open_threads=["Unknown asset types currently return an empty contract with HTTP 200 — that is wrong, see the bug list."]))
    B["empty_schema"] = bug("Contract tool returns 200 with an empty schema for an unknown asset type", T["contract"], "P2",
                            "Repro: `get_asset_contract('vfx')`. Expected a 404-style error naming the known types; got `{}`. "
                            "The agent then generates metadata with no constraints at all.", ["contract"])
    I["timestamps"] = issue("Job log timestamps are written in local time", "P3",
                            evidence="Systemic: every job log line; reports from two time zones cannot be lined up.",
                            impact="Cosmetic until logs from two machines are compared.", components=["pipeline"],
                            discovered_by="manual QA")
    N["publisher"] = note("Publisher check-in is every other Thursday. The demo recording has to stay under 8 minutes.", pinned=True)
    at(-6, "11:00")
    call("backlog_add_epic", epic_id="legacy-retag", name="Legacy Asset Re-tagging", status="planned", area="pipeline",
         done_when="Every legacy asset pack carries contract-conformant tags",
         description="Hand-made packs from before the contract existed need their metadata brought in line.")
    packs = ("harbor", "forest", "caverns", "desert", "tundra", "citadel", "swamp", "ruins")
    for number in range(1, 49):
        T[f"retag{number:02d}"] = task(f"Re-tag legacy asset pack {number:02d} ({packs[number % 8]} set {1 + number // 8})",
                                       "legacy-retag", "beta", "low")
    at(-6, "12:30")
    auto_stage("x5", "provider-fallback", T["fallback"], "feature/provider-fallback", 6, "router skeleton and parameter mapping")
    at(-6, "16:20")
    handover("c3", thread="thumb-cache-v2", tasks=[T["thumbs"]], branch="feature/thumb-cache-v2", tip="47d0b2e",
             tldr="Thumb cache v2: migration re-keys 18k existing thumbnails in 50 s without regenerating",
             next_action="Put v2 behind a flag and run a full sync with it on",
             text=body(
                 decisions=["Migration hashes the source file and hard-links the existing thumbnail under its new key. No pixels are re-rendered."],
                 notes=["18,204 thumbnails re-keyed in 50 s on the sample project. 37 could not be migrated because their source file is gone; they are left in the old cache."],
                 start=["The flag is `THUMBS_V2`; nothing reads it yet."],
                 open_threads=["Hard links do not work across volumes — fall back to copy there."]))

    at(-5, "08:50")
    handover("h1", kind="milestone", tldr="Alpha pipeline demo recorded for the publisher check-in",
             next_action="Send the recording and the two briefs to the publisher contact",
             text=body(
                 notes=["Six-minute recording: prompt to in-engine preview for a sprite and a mesh, plus a generated brief.",
                        "The demo used the primary provider only; failover was switched off to keep it predictable."],
                 open_threads=["They will ask about turnaround time per asset — have the numbers ready."]))
    at(-5, "11:25")
    handover("h2", tldr="Failover timeout notes: primary provider p95 hits 28 s under batch load",
             next_action="Feed these numbers into the router's timeout budget",
             text=body(
                 notes=["Loose notes, not tied to a task. Measured 400 batch jobs against the primary provider: p50 6 s, p95 28 s, p99 44 s.",
                        "With a 30 s budget roughly 4% of jobs would fail over."],
                 open_threads=["Measure again at a different time of day; this was a single afternoon."]))
    I["timeouts"] = issue("Primary image provider times out under batch load", "P1",
                          evidence="Recurring: seen on three separate afternoons; p95 28 s and p99 44 s across 400 batch jobs.",
                          impact="About 4% of batch jobs fail outright until failover is on for everyone.",
                          components=["providers"], related_tasks=[T["fallback"]], discovered_by="load measurement",
                          body="## Repro\n- Queue 400 generation jobs in one batch against the primary provider between 14:00 and 17:00 UTC.\n"
                               "- Watch `pipeline/providers/primary.py` request durations: roughly 1 in 25 exceeds the 30 s budget.\n\n"
                               "## Investigation\n- Not reproducible with fewer than about 150 concurrent jobs.\n"
                               "- The provider's status page shows nothing during the slow windows.\n")
    call("backlog_issue_update", issue_id=I["timeouts"], field="status", value="investigating")
    at(-5, "15:35")
    handover("d2", thread="provider-fallback", tasks=[T["fallback"]], branch="feature/provider-fallback", tip="91ce3d6",
             tldr="Provider failover: router implemented, parameter mapping covers size, seed and style",
             next_action="Map negative prompts — the secondary provider calls the field something else",
             text=body(
                 decisions=["The mapping layer is a plain table per provider, not a class hierarchy."],
                 notes=["Size, seed and style map cleanly. 12 router tests pass."],
                 start=["`pipeline/providers/mapping.py::SECONDARY` has no entry for negative prompts."],
                 open_threads=["Cost per image differs between providers; nobody has decided who is told when a job fails over."]))
    D["notify"] = decision(
        "Who is told when a job fails over to the secondary provider?",
        ["Nobody: the job log records it and that is all",
         "The job owner sees a notice on the finished job",
         "The job owner and whoever pays the provider bill"],
        task_id=T["fallback"], branch="feature/provider-fallback",
        body="The secondary provider costs about 1.6x per image. Someone will eventually ask why the bill moved.")
    auto_stage("x6", "thumb-cache-v2", T["thumbs"], "feature/thumb-cache-v2", 3, "THUMBS_V2 flag plumbing")

    at(-4, "09:40")
    start_task(T["linking"])
    handover("g3", tasks=[T["linking"]], branch="spike/gdd-task-links",
             tldr="Section-to-task linking: data model sketched, nothing built",
             next_action="Decide whether a link lives on the task or in the design doc's front matter",
             text=body(
                 notes=["Two candidate models: an `implements:` list on each task, or anchors in the design doc that name task ids.",
                        "The first survives a doc rewrite; the second survives a backlog reshuffle."],
                 blockers=["Needs a decision from design before any code."],
                 open_threads=["Beta-phase work; do not start before the brief tool is done."]))
    D["linking"] = decision(
        "Where does a design-doc-section-to-task link live?",
        ["An `implements:` list on each task", "Anchors in the design doc that name task ids"],
        recommendation=1, task_id=T["linking"], raised_in=HANDOVERS["g3"], branch="spike/gdd-task-links",
        body="The list on the task survives a rewrite of the design doc; anchors in the doc survive a backlog reshuffle.")
    T["metrics"] = task("Emit failover metrics to the job dashboard", "asset-pipeline", "alpha", "high",
                        "Acceptance criteria:\n- One counter per provider: jobs started, jobs failed over, jobs failed.\n"
                        "- The dashboard shows the failover rate over the last 24 hours.\n"
                        "- An alert fires when the failover rate stays above 15% for 30 minutes.\n"
                        "Out of scope: per-image cost.", anchors="pipeline/providers/**,dashboards/jobs/**", area="pipeline")
    T["retry"] = task("Retry budget for contract repair rounds", "asset-pipeline", "alpha", "medium",
                      "Make the two-round repair limit configurable per asset type.", depends_on=T["contract"])
    T["batchgen"] = task("Contract-aware batch generation", "asset-pipeline", "alpha", "medium",
                         "Fetch each asset type's contract once per batch instead of once per asset.", depends_on=T["retry"])
    T["glossary"] = task("Glossary section in the brief", "design-docs", "alpha", "medium",
                         "Pull defined terms out of the design doc into a short glossary at the end of the brief.",
                         area="brief-tool")
    T["upload"] = task("Raise the upload size limit in the pipeline config", "asset-pipeline", "alpha", "low",
                       "Meshes above 64 MB are rejected at upload. Raise the limit to 256 MB; config only.")
    start_task(T["upload"])
    T["secrets"] = task("Secondary provider API key in the CI secrets", "asset-pipeline", "alpha", "medium",
                        "The failover tests need a real key for the secondary provider on CI.")
    start_task(T["secrets"])
    call("backlog_complete_task", task_id=T["secrets"], target_status="in-review",
         human_action="add PROVIDER_SECONDARY_KEY to the CI secrets (only the account owner can)")
    at(-4, "12:15")
    handover("b4", thread="gdd-brief-tool", tasks=[T["brief"]], branch="feature/gdd-brief", tip="3c8e1f6",
             tldr="GDD brief: two of three real design docs produce a usable brief; the table-heavy one loses content",
             next_action="Teach the outline extractor to read tables as content, starting with simple ones",
             text=body(
                 notes=["emberfall and tidewake produce good briefs. glasswright is written mostly as tables and its brief is nearly empty."],
                 start=["`brief/outline.py` skips every table node. Start there."],
                 open_threads=["Tables with merged cells are probably a separate problem."]))
    B["merged_cells"] = bug("GDD brief drops tables with merged cells", T["brief"], "P2",
                            "Repro: run the brief on tests/fixtures/gdd_merged.md. The 'Economy' table (merged header cells) is missing "
                            "from the brief entirely; simple tables render.", ["brief"], ["brief/tables.py:112"])
    I["no_headings"] = issue("Design docs without headings produce an empty brief", "P2",
                             evidence="Systemic: any doc written as one long page hits it; two of the five studio docs sampled do.",
                             impact="The brief tool silently returns a blank page for those docs.", components=["brief"],
                             related_tasks=[T["brief"]], discovered_by="studio sample docs",
                             body="## Repro\n- Run the brief on a markdown file that has no `#` headings.\n- The outline is empty, so the brief is too.\n")
    at(-4, "16:00")
    handover("a3", thread="contract-as-tool", tasks=[T["contract"]], branch="feature/contract-tool", tip="f2b7d48",
             tldr="Contract tool: error taxonomy settled — fixable, fatal, unknown-type",
             next_action="Wire both tools into the generation agent and run the 50-asset regression set",
             text=body(
                 decisions=["Three error classes. `fixable`: the agent should repair and resubmit. `fatal`: stop and report. `unknown_type`: the asset type is not registered.",
                            "At most two repair rounds per asset; after that the job fails with the last error list attached."],
                 start=["`agents/generation/tools.py` — neither tool is registered with the agent yet."],
                 open_threads=["The agent's system prompt still contains the old pasted contract."]))
    auto_stage("x7", "contract-as-tool", T["contract"], "feature/contract-tool", 7, "error classes and their tests")

    at(-3, "07:30")
    start_task(T["uvcache"])
    handover("c4", thread="thumb-cache-v2", tasks=[T["thumbs"]], branch="feature/thumb-cache-v2", tip="a9d3c10", kind="context-handoff",
             tldr="Thumb cache v2: full sync with the flag on is 2.4x faster; one stale preview seen",
             next_action="Find out why hero_idle.png showed its old thumbnail after a re-import",
             text=body(
                 notes=["Morning checkpoint. Full sync: 212 s with the flag off, 88 s with it on.",
                        "One asset (hero_idle.png) showed the previous thumbnail after its source was edited and re-imported."],
                 start=["Suspect the read-through wrapper: it may be serving the path-keyed entry before checking the new hash."]))
    at(-3, "11:45")
    handover("i1", tasks=[T["uvcache"]], branch="ci/uv-cache", tip="6a0e9f2",
             tldr="CI uv cache: cold run down from 4m10s to 1m35s with the environment cached",
             next_action="Key the cache on uv.lock's hash and check that a dependency bump invalidates it",
             text=body(
                 decisions=["Cache the resolved environment, not the wheel downloads — the install step, not the download, was the slow part."],
                 notes=["Measured over five cold runs each."],
                 open_threads=["Cache is currently keyed on the branch name, which is wrong."]))
    at(-3, "14:20")
    handover("d3", thread="provider-fallback", tasks=[T["fallback"]], branch="feature/provider-fallback", tip="5f3b8a1",
             tldr="Provider failover: negative prompts mapped; failover exercised with injected timeouts",
             next_action="Turn failover on for 10% of jobs and watch the error rate for a day",
             text=body(
                 decisions=["Failover is announced in the job log but not to the user. Cost attribution is deferred."],
                 notes=["With timeouts injected on the primary, 100 of 100 jobs completed on the secondary."],
                 start=["`PROVIDER_FAILOVER_PERCENT` in the pipeline config; it is 0 today."],
                 open_threads=["Images from the secondary provider come back slightly larger; previews are regenerated for them."]))
    at(-3, "16:40")
    handover("c5", thread="thumb-cache-v2", tasks=[T["thumbs"]], branch="feature/thumb-cache-v2", tip="d07e4b9",
             tldr="Thumb cache v2: stale preview traced to the read-through wrapper's fallback order",
             next_action="Make the wrapper compare the source hash before trusting a path-keyed hit, then re-test hero_idle.png",
             text=body(
                 decisions=["The path-keyed cache may only answer when the stored source hash matches the current one. A path match alone is not enough."],
                 notes=["Root cause: the wrapper falls back to the old cache by path, and the old cache has no idea the source changed."],
                 start=["`pipeline/thumbs/wrapper.py::get` — the fallback branch."],
                 open_threads=["Old entries have no stored hash; they need one computed at migration time."]))
    B["stale_preview"] = bug("Thumbnail cache serves a stale preview after re-import", T["thumbs"], "P1",
                             "Repro: edit hero_idle.png, re-import with THUMBS_V2 on. The preview still shows the old image. "
                             "The read-through wrapper answers from the path-keyed cache without checking the source hash.",
                             ["thumbs"], ["pipeline/thumbs/wrapper.py:40"])
    call("backlog_idea_create", title="Pre-warm thumbnails at import instead of on first view",
         body="With content-addressed keys a thumbnail could be rendered as part of import, so the first person to open the "
              "asset browser does not wait. Costs import time; worth measuring once thumbnail cache v2 is on by default.",
         tags=["thumbs", "performance"], status="parking-lot", related_tasks=[T["thumbs"]])
    IDEAS["batch_fetch"] = idea("Fetch asset contracts once per job instead of once per asset",
                                "A 200-asset job asks for the same five contracts 200 times. Cache them for the life of the job.",
                                ["contract", "performance"], "candidate")
    IDEAS["warm_up"] = idea("Warm up the secondary provider connection before a batch starts",
                            "The first request after failover pays for a cold connection and a token exchange. "
                            "Opening the connection when the batch is queued would hide that from the job.",
                            ["providers", "performance"])
    IDEAS["token_cache"] = idea("Cache provider auth tokens between jobs",
                                "Tokens are valid for an hour and are requested per job.", ["providers", "performance"])
    call("backlog_idea_update", idea_id=IDEAS["token_cache"], field="promoted_to", value=T["fallback"])
    call("backlog_idea_update", idea_id=IDEAS["token_cache"], field="archived", value="true")

    at(-2, "07:20")
    N["windows"] = note("Second Windows runner: infra said to ask again once beta has a date.")
    at(-2, "07:45")
    handover("c6", thread="thumb-cache-v2", tasks=[T["thumbs"]], branch="feature/thumb-cache-v2", tip="e3c21f8",
             tldr="Thumb cache v2 merged behind THUMBS_V2; stale-preview fix is written but not verified on Windows",
             next_action="Verify the stale-preview fix on a Windows checkout, then flip THUMBS_V2 on by default",
             text=body(
                 decisions=["Merged with the flag off by default. It goes on by default only after the stale thumbnail bug is confirmed fixed on both platforms."],
                 notes=["The wrapper now compares source hashes. hero_idle.png refreshes correctly on Linux."],
                 blockers=["No Windows machine was free today."],
                 start=["Re-run the repro from the stale-preview bug on Windows."],
                 open_threads=["Dropping the old path-keyed cache is a separate change, a week after the flag flips."]))
    at(-2, "10:30")
    finish_task(T["uvcache"], session_title="CI: uv environment cached, cold runs under two minutes",
         done="Environment cache keyed on the uv.lock hash\nCold run 4m10s -> 1m35s\nA dependency bump invalidates the cache",
         decisions="Cache the environment, not the downloads", issues="None", tasks_touched=T["uvcache"])
    at(-2, "13:05")
    auto_stage("x8", "gdd-brief-tool", T["brief"], "feature/gdd-brief", 4, "table nodes in the outline extractor")
    at(-2, "16:30")
    handover("b5", thread="gdd-brief-tool", tasks=[T["brief"]], branch="feature/gdd-brief", tip="7b90c5d",
             tldr="GDD brief: simple tables now reach the brief; tables with merged cells are still dropped",
             next_action="Handle merged cells in brief/tables.py — the repro is tests/fixtures/gdd_merged.md",
             text=body(
                 decisions=["A table becomes a bullet list in the brief: one bullet per row, header cells as labels. A one-page brief has no room for real tables."],
                 notes=["glasswright's brief went from 4 lines to a full page."],
                 blockers=["Merged cells: the parser reports them as a single cell with a span, and the row-to-bullet code assumes one cell per column."],
                 start=["`brief/tables.py::row_to_bullet` — expand spans before labelling."],
                 open_threads=["After merged cells, the brief tool is done apart from the open bug on it."]))

    at(-1, "07:35")
    handover("a5", thread="contract-as-tool", tasks=[T["contract"]], branch="feature/contract-tool", tip="0c4d7e2", kind="context-handoff",
             tldr="Contract tool: both tools registered with the generation agent; two validators fail on the regression set",
             next_action="Fix the mesh triangle-count and audio sample-rate validators, then rerun the 50-asset set",
             text=body(
                 notes=["Morning checkpoint. The agent now fetches the asset contract through the tool; the pasted contract is gone from its system prompt.",
                        "Regression set: 44 of 50 pass. All six failures come from two validators."],
                 start=["`pipeline/contract/validate.py::check_mesh` compares against the LOD0 limit for every LOD.",
                        "`check_audio` rejects 44.1 kHz although the contract allows it."]))
    at(-1, "10:10")
    T["messages"] = task("Human-readable messages for contract validation errors", "asset-pipeline", "beta", "low",
                         "The structured errors are right for the agent; a person reading the job log needs a sentence.")
    call("backlog_bug_update", bug_id=B["empty_schema"], field="fix_commit", value="4e1b9d7")
    call("backlog_bug_update", bug_id=B["empty_schema"], field="status", value="fixed")
    at(-1, "14:00")
    handover("d4", thread="provider-fallback", tasks=[T["fallback"]], branch="feature/provider-fallback", tip="2d8a6f0",
             tldr="Provider failover live for 10% of jobs; the secondary ignores negative prompts",
             next_action="Find out why the mapped negative prompt never reaches the secondary provider's request",
             text=body(
                 notes=["A day at 10%: 31 jobs failed over, all completed. Error rate unchanged.",
                        "Seven of the 31 images contain things their negative prompt excluded."],
                 blockers=["Do not raise the percentage until negative prompts work."],
                 start=["Log the outgoing request body in `pipeline/providers/secondary.py`."]))
    B["negative_prompt"] = bug("Secondary image provider ignores the negative prompt after failover", T["fallback"], "P1",
                               "Seven of 31 failed-over jobs produced images containing excluded content. The mapping table has the "
                               "field, but the request builder drops keys it does not recognise.", ["providers"],
                               ["pipeline/providers/request.py:61"])
    B["seed_dropped"] = bug("Secondary image provider ignores the seed after failover", T["fallback"], "P2",
                            "Two failed-over jobs with a fixed seed produced different images on rerun.", ["providers"],
                            ["pipeline/providers/request.py:61", "pipeline/providers/mapping.py:23"])
    B["style_dropped"] = bug("Secondary image provider ignores the style preset after failover", T["fallback"], "P2",
                             "Failed-over sprites come back photorealistic although the job asked for the pixel-art preset.",
                             ["providers"], ["pipeline/providers/request.py:61"])
    B["footer"] = bug("Brief PDF footer shows the page count of the previous export", T["brief"], "P3",
                      "Export two briefs in a row; the second one's footer says 'of 3' although it has two pages.",
                      ["brief"], ["brief/export/pdf.py:77"])
    call("backlog_bug_update", bug_id=B["footer"], field="status", value="shelved")
    at(-1, "16:45")
    handover("a6", thread="contract-as-tool", tasks=[T["contract"]], branch="feature/contract-tool", tip="b81f3a6",
             tldr="Contract tool wired into the generation agent; validators green on the 50-asset regression set",
             next_action="Open the PR for feature/contract-tool and ask for review of the version handling first",
             text=body(
                 decisions=["The contract's schema version travels in the tool response envelope as `contract_version`; it is not written into the agent's system prompt. A prompt-pinned version went stale the moment the registry changed.",
                            "The validator accepts metadata built against the current version or the one before it, and rejects anything older as `fatal`."],
                 notes=["50 of 50 on the regression set. Mesh limits are now per LOD; 44.1 kHz audio is accepted.",
                        "Average repair rounds per asset: 0.3."],
                 start=["The branch is rebased on main and ready for a PR."],
                 open_threads=["Human-readable error messages are split out into their own task.",
                               "Nothing announces a contract version bump to running agents yet."]))

    at(0, "00:00")
    handover("d5", thread="provider-fallback", tasks=[T["fallback"]], branch="feature/provider-fallback", tip="c60a1e9",
             tldr="Provider failover: negative-prompt passthrough fixed in the request builder; fix is in review",
             next_action="After review lands, rerun the 31 failed-over prompts and raise failover to 50%",
             text=body(
                 decisions=["The request builder now passes through every key the mapping table produces, instead of keeping its own allow-list."],
                 notes=["Unit test added with a negative prompt; the outgoing body contains it."],
                 blockers=["Waiting on review."],
                 open_threads=["The loose timeout notes from last week are folded into the router's budget and can be retired."]))
    call("backlog_update_task", task_id=T["fallback"], next_step="Rerun the 31 failed-over prompts once the request-builder fix is reviewed")
    call("backlog_pick_task", task_id=T["retag03"])
    return {"tasks": T, "bugs": B, "issues": I, "decisions": D, "ideas": IDEAS, "notes": N}


# ── ground truth ──────────────────────────────────────────────────────────
def compute(seed_root: Path, ids: dict) -> dict:
    """Everything the ground truth quotes, read back from the seeded store."""
    db = seed_root / ".taskmaster" / "local" / "store.db"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = {r["id"]: json.loads(r["doc"]) | {"_archived": r["archived"]}
            for r in con.execute("SELECT id, doc, archived FROM entities WHERE kind='handover' AND deleted=0")}
    patch_clock()
    indexed = [e["id"] for e in BS._load().get("handovers") or []]
    key_of = {v: k for k, v in HANDOVERS.items()}

    def newest_first(idents):
        return sorted(idents, key=lambda i: (i[:10], str(rows[i].get("created") or ""), i), reverse=True)

    def brief(i):
        d = rows[i]
        return {"id": i, "key": key_of.get(i, ""), "date": d.get("date"), "thread": d.get("thread", ""),
                "status": d.get("status"), "session_kind": d.get("session_kind"), "tldr": d.get("tldr"),
                "next_action": d.get("next_action", ""), "task_ids": d.get("task_ids") or [],
                "superseded_by": d.get("superseded_by", ""), "in_index": i in indexed}

    all_ids = newest_first(rows)
    open_ids = [i for i in all_ids if rows[i].get("status") == "open"]
    latest_open = {}
    for i in open_ids:
        latest_open.setdefault(rows[i].get("thread") or i, i)
    anchor = HANDOVERS["c6"]
    anchor_seq = con.execute("SELECT seq FROM changes WHERE kind='handover' AND id=? AND op='create'", (anchor,)).fetchone()[0]
    after = {}
    for r in con.execute("SELECT seq,ts,tool,kind,id,op FROM changes WHERE seq>? AND kind!='backlog' ORDER BY seq", (anchor_seq,)):
        entry = after.setdefault(f"{r['kind']}/{r['id']}", {"ops": [], "tools": [], "first_seq": r["seq"]})
        entry["ops"].append(r["op"])
        if r["tool"] not in entry["tools"]:
            entry["tools"].append(r["tool"])
    stamps = [r[0] for r in con.execute("SELECT ts FROM changes")]
    leaked = [s for s in stamps if _dt.datetime.fromisoformat(s) >= REAL_START]
    con.close()
    if leaked:
        raise SystemExit(f"{len(leaked)} change rows carry the real clock, not the seed clock")
    for i, d in rows.items():
        if not (i[:10] == str(d.get("date")) == str(d.get("created"))[:10]):
            raise SystemExit(f"handover {i}: id date, `date` and `created` disagree: {d.get('date')} {d.get('created')}")

    def probe(query, kinds=None):
        patch_clock()
        return BS.backlog_search(query=query, kinds=kinds)

    def on_day(offset):
        return [brief(i) for i in all_ids if rows[i].get("date") == day(offset).isoformat()]

    t = ids["tasks"]
    con = sqlite3.connect(db)
    todo = con.execute("SELECT count(*) FROM entities WHERE kind='task' AND status='todo' AND archived=0 AND deleted=0").fetchone()[0]
    total_tasks = con.execute("SELECT count(*) FROM entities WHERE kind='task' AND deleted=0").fetchone()[0]
    design_docs = [r[0] + " (" + r[1] + ")" for r in con.execute(
        "SELECT id, status FROM entities WHERE kind='task' AND epic='design-docs' AND deleted=0 ORDER BY id")]
    con.close()

    def read(tool, **kwargs):
        patch_clock()
        return getattr(BS, tool)(**kwargs)

    patch_clock()
    return {
        "todo_tasks": todo,
        "total_tasks": total_tasks,
        "design_docs_tasks": design_docs,
        "rb_next_available": read("backlog_next_available"),
        "rb_list_todo_default": read("backlog_list_tasks", status="todo")[:400],
        "rb_deps_contract_depth1": read("backlog_dependencies", task_id=t["contract"]),
        "rb_deps_contract_depth3": read("backlog_dependencies", task_id=t["contract"], depth=3),
        "rb_pipeline_thumbs": read("backlog_task_pipeline", task_id=t["thumbs"]),
        "rb_context_thumbs": read("backlog_context", focus=t["thumbs"], scope="task"),
        "rb_pipeline_upload": read("backlog_task_pipeline", task_id=t["upload"]),
        "rb_metrics_notes": read("backlog_get_task", task_id=t["metrics"], sections=["notes"]),
        "rb_metrics_slim": read("backlog_get_task", task_id=t["metrics"]),
        "rb_blast_metrics": read("backlog_blast_radius", task_id=t["metrics"]),
        "rb_phase_status": read("backlog_phase_status"),
        "rb_bugs_open": read("backlog_bug_list", status="open"),
        "rb_bugs_default": read("backlog_bug_list"),
        "rb_bug_patterns": read("backlog_bug_pattern_scan"),
        "rb_issues_default": read("backlog_issue_list"),
        "rb_issues_status_open": read("backlog_issue_list", status="open"),
        "rb_issue_timeouts": read("backlog_issue_get", issue_id=ids["issues"]["timeouts"], verbose=True),
        "rb_decisions_open": read("backlog_decision", action="list"),
        "rb_decision_naming": read("backlog_decision", action="get", decision_id=ids["decisions"]["naming"]),
        "rb_search_naming": read("backlog_search", query="naming scheme"),
        "rb_area_list": read("backlog_area_list"),
        "rb_area_brief": read("backlog_area_get", area_id="brief-tool"),
        "rb_list_tasks_area_brief": read("backlog_list_tasks", area="brief-tool"),
        "rb_in_review": read("backlog_list_tasks", status="in-review"),
        "rb_validate": read("backlog_validate"),
        "rb_paths_providers": read("backlog_query", sql="SELECT kind,id,path,source FROM entity_paths "
                                                        "WHERE path LIKE 'pipeline/providers%' ORDER BY kind,id"),
        "rb_batch_preview_ci": read("backlog_batch_preview", operations=f"archive {t['flakes']}\narchive {t['uvcache']}"),
        "total_handovers": len(all_ids),
        "indexed_handovers": len(indexed),
        "archived_handovers": [brief(i) for i in all_ids if i not in indexed],
        "open_handovers": [brief(i) for i in open_ids],
        "open_latest_per_thread": [brief(i) for i in latest_open.values()],
        "thread_board": BS.backlog_thread_list(),
        "contract_thread": [brief(i) for i in all_ids if rows[i].get("thread") == "contract-as-tool"],
        "gdd_thread": [brief(i) for i in all_ids if rows[i].get("thread") == "gdd-brief-tool"],
        "day_minus_3": on_day(-3),
        "day_minus_1": on_day(-1),
        "thumbs_task_handovers": [brief(i) for i in all_ids if t["thumbs"] in (rows[i].get("task_ids") or [])],
        "changes_after_anchor": {"anchor": anchor, "anchor_seq": anchor_seq, "entities": after},
        "last_session": BS.backlog_last_session(),
        "search_asset_contract_handovers": probe("asset contract", ["handover"]),
        "search_contract_handovers": probe("contract", ["handover"]),
        "search_thumbnail": probe("thumbnail"),
        "search_thumbnails": probe("thumbnails"),
        "search_thumbnail_caching": probe("thumbnail caching"),
        "search_atlas_budget": probe("atlas budget"),
        "search_texture_atlas_memory_budget": probe("texture atlas memory budget"),
    }


def render(value, ids: dict, computed: dict):
    """Fill {h:key} {t:key} {b:key} {i:key} {d:key} {idea:key} {n:key} {iso:-N} {nice:-N} in strings;
    a string that is exactly {c:name} becomes that computed value."""
    if isinstance(value, dict):
        return {k: render(v, ids, computed) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ids, computed) for v in value]
    if not isinstance(value, str):
        return value
    whole = re.fullmatch(r"\{c:(\w+)\}", value)
    kinds = {"t": "tasks", "b": "bugs", "i": "issues", "d": "decisions", "idea": "ideas", "n": "notes"}
    if whole:
        return computed[whole.group(1)]

    def sub(match):
        kind, arg = match.group(1), match.group(2)
        if kind == "h":
            return HANDOVERS[arg]
        if kind in kinds:
            return ids[kinds[kind]][arg]
        d = day(int(arg))
        return d.isoformat() if kind == "iso" else f"{d.strftime('%A')} {d.day} {d.strftime('%B')}"

    return re.sub(r"\{(h|t|b|i|d|idea|n|iso|nice):([\w+-]+)\}", sub, value)


def check(computed: dict) -> None:
    """The seed must actually have the shape the scenarios rely on."""
    def keys(name):
        return {h["key"] for h in computed[name]}

    assert computed["total_handovers"] > 30 and computed["indexed_handovers"] == 30, computed["total_handovers"]
    archived = keys("archived_handovers")
    assert "f1" in archived, "the atlas-budget handover must be outside the index"
    assert {h["key"] for h in computed["day_minus_1"] if h["thread"] == "contract-as-tool"} == {"a5", "a6"}
    assert keys("day_minus_3") == {"c4", "i1", "d3", "c5"}, keys("day_minus_3")
    status = {h["key"]: h for name in ("contract_thread", "gdd_thread", "thumbs_task_handovers", "archived_handovers",
                                       "day_minus_1", "day_minus_3", "open_handovers") for h in computed[name]}
    assert status["a5"]["status"] == "superseded" and status["a5"]["superseded_by"] == HANDOVERS["a6"]
    assert status["c4"]["superseded_by"] == HANDOVERS["c5"] and status["c5"]["superseded_by"] == HANDOVERS["c6"]
    assert status["a6"]["status"] == status["b5"]["status"] == status["f1"]["status"] == "open"
    assert status["h2"]["status"] == "open" and status["h2"]["thread"] != "provider-fallback"
    assert "c1" in archived and "c1" in keys("thumbs_task_handovers")


def main() -> None:
    global BS
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", help="eval home (default: TM_EVAL_HOME or <temp>/tm-agent-tool-use-eval)")
    parser.add_argument("--force", action="store_true", help="rebuild an existing seed (runs and logs are kept)")
    args = parser.parse_args()
    home = evalpaths.home(args.home)
    root = evalpaths.seed_dir(home)
    if root.exists():
        if not args.force:
            raise SystemExit(f"{root} already exists; pass --force to rebuild it")
        if not (home / evalpaths.MARKER).exists():
            raise SystemExit(f"{home} is not an eval home (no {evalpaths.MARKER}); refusing to delete {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    (home / evalpaths.MARKER).write_text("agent tool-use eval home\n", encoding="utf-8")

    os.environ["TASKMASTER_ROOT"] = str(root)
    os.chdir(root)
    sys.path.insert(0, str(evalpaths.REPO_ROOT))
    from taskmaster import backlog_server  # noqa: PLC0415 - must follow TASKMASTER_ROOT
    BS = backlog_server

    ids = build()
    computed = compute(root, ids)
    check(computed)

    con = sqlite3.connect(root / ".taskmaster" / "local" / "store.db")
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()

    scenarios = yaml.safe_load((evalpaths.EVAL_DIR / "scenarios.yaml").read_text(encoding="utf-8"))
    truth = yaml.safe_load((evalpaths.EVAL_DIR / "ground_truth.yaml").read_text(encoding="utf-8"))
    if [s["id"] for s in scenarios["scenarios"]] != [g["id"] for g in truth["ground_truth"]]:
        raise SystemExit("scenarios.yaml and ground_truth.yaml list different scenario ids")
    meta = {"seeded_on": TODAY.isoformat(), "home": str(home)}
    (home / "scenarios.json").write_text(json.dumps(
        {**meta, "scenarios": render(scenarios["scenarios"], ids, computed)}, indent=2, ensure_ascii=False), encoding="utf-8")
    (home / "ground_truth.json").write_text(json.dumps(
        {**meta, "ground_truth": render(truth["ground_truth"], ids, computed)}, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    (home / "seed_manifest.json").write_text(json.dumps(
        {**meta, "handovers": HANDOVERS, **ids, "total_handovers": computed["total_handovers"]}, indent=2), encoding="utf-8")
    print(f"seeded {computed['total_handovers']} handovers ({computed['indexed_handovers']} indexed, "
          f"{len(computed['archived_handovers'])} archived) for {TODAY.isoformat()}")
    print(f"backlog:      {root}")
    print(f"scenarios:    {home / 'scenarios.json'}")
    print(f"ground truth: {home / 'ground_truth.json'}")
    print("Relative dates in the scenarios are only true today: reseed with --force on any later day.")


if __name__ == "__main__":
    main()
