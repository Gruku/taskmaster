# User intent: prove the restore scanner reads only this repo's sessions and reports the transcript facts the restore pane
# classifies by (interrupt, end-session with or without prompts after, tm task and handover ids, running, cache), using
# small transcripts authored for these tests, never real ones.
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scan  # noqa: E402

FIXTURES = HERE / "fixtures"
SCRIPT = HERE / "scan.py"
IN_REPO = ["interrupted", "ended", "ended-then-more", "handed-off", "sdk-run"]


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "repo"
        self.worktree = self.root / ".worktrees" / "delta"
        self.sibling = tmp_path / "repo-sibling"
        for d in (self.worktree, self.sibling):
            d.mkdir(parents=True)
        self.cfg = tmp_path / "cfg"
        self.cache = tmp_path / "tmp" / "tm-restore-scan-cache.json"
        for name in IN_REPO:
            self.lay(name, self.root)
        self.lay("worktree", self.worktree)
        self.lay("elsewhere", self.sibling)

    def lay(self, name: str, cwd: Path) -> Path:
        text = (FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8")
        for token, path in (("{{ROOT}}", self.root), ("{{WORKTREE}}", self.worktree), ("{{SIBLING}}", self.sibling)):
            text = text.replace(token, json.dumps(str(path))[1:-1])
        text = text.replace("{{ROOT_IN_RESULT}}", self.root.as_posix())
        folder = self.cfg / "projects" / scan.encode_dir(str(cwd))
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / f"{name}.jsonl"
        out.write_text(text, encoding="utf-8")
        return out

    def transcript(self, name: str) -> Path:
        return next((self.cfg / "projects").glob(f"*/{name}.jsonl"))

    def scan(self, capsys, *extra: str) -> dict:
        assert scan.main(["scan", "--config-dir", str(self.cfg), "--root", str(self.root), *extra]) == 0
        return json.loads(capsys.readouterr().out)

    def by_id(self, capsys, *extra: str) -> dict:
        return {s["id"]: s for s in self.scan(capsys, *extra)["sessions"]}


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def test_only_this_repos_interactive_sessions_are_read(world, capsys):
    # sdk-run is a `claude -p` run; elsewhere lives in a sibling folder whose name starts like the repo's.
    assert set(world.by_id(capsys)) == {"interrupted", "ended", "ended-then-more", "handed-off", "worktree"}


def test_an_interrupt_is_the_last_event(world, capsys):
    s = world.by_id(capsys)["interrupted"]
    assert s["lastEvent"] == "interrupt"
    assert s["interrupted"] is True
    assert s["taskIds"] == ["alpha-001"]
    assert s["ctx"] == 125012
    assert s["cost"] == 1.23
    assert s["closedEpoch"] == (1791622790000 + 20000) / 1000
    assert s["hasClosed"] is True
    assert (s["title"], s["branch"], s["prompts"]) == ("Port the row counter", "feat/rows", 1)
    assert s["lastPrompt"] == "Port the row counter to the new store"


def test_end_session_with_nothing_after_it(world, capsys):
    s = world.by_id(capsys)["ended"]
    assert (s["endedSession"], s["promptsAfterEnd"], s["interrupted"]) == (True, 0, False)
    assert s["lastEvent"] == "end_turn"
    # Last touched last: beta-002 was read again after beta-001 was updated, so it is the bound task.
    assert s["taskIds"] == ["beta-001", "beta-002"]
    assert s["title"] == "Sign off the migration"
    assert s["lastReply"] == "Session closed out: beta-002 is in review."


def test_end_session_by_command_then_another_prompt(world, capsys):
    s = world.by_id(capsys)["ended-then-more"]
    assert (s["endedSession"], s["promptsAfterEnd"]) == (True, 1)
    assert s["prompts"] == 2  # the slash command itself is not a prompt
    assert s["hasClosed"] is False
    assert s["closedEpoch"] == scan.iso_to_epoch("2026-10-10T11:02:05.000Z")
    assert s["title"] == "Write the README section"


def test_handover_ids_come_from_create_results(world, capsys):
    sessions = world.by_id(capsys)
    gamma = sessions["handed-off"]
    assert gamma["handovers"] == [
        {
            "id": "2026-10-10-gamma-wrap",
            "path": f"{world.root.as_posix()}/.taskmaster/handovers/2026-10-10-gamma-wrap.md",
            "tldr": "Gamma parser half done",
            "next": "Finish the error rows, then run the suite",
        }
    ]
    assert gamma["taskIds"] == ["gamma-003"]  # the sidechain's side-999 is not this session's
    assert gamma["ctx"] == 452003
    # A plain-text receipt without a path still names the id; an errored call names nothing.
    delta = sessions["worktree"]
    assert [(h["id"], h["path"]) for h in delta["handovers"]] == [("2026-10-10-delta-wait", "")]
    assert delta["cwd"] == str(world.worktree)


def test_running_comes_from_a_live_pid_file(world, capsys):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    reg = world.cfg / "sessions"
    reg.mkdir(parents=True)
    (reg / f"{os.getpid()}.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "worktree"}), encoding="utf-8")
    (reg / f"{dead.pid}.json").write_text(json.dumps({"pid": dead.pid, "sessionId": "ended"}), encoding="utf-8")
    (reg / "junk.json").write_text("not json", encoding="utf-8")
    sessions = world.by_id(capsys)
    assert sessions["worktree"]["running"] is True
    assert sessions["ended"]["running"] is False


def test_the_days_window_and_exclude(world, capsys):
    old = time.time() - 10 * 86400
    os.utime(world.transcript("ended"), (old, old))
    ids = set(world.by_id(capsys, "--days", "4", "--exclude", "interrupted"))
    assert ids == {"ended-then-more", "handed-off", "worktree"}


def test_unchanged_files_come_from_the_cache(world, capsys, monkeypatch):
    calls: list[str] = []
    real = scan.scan_file

    def counting(path: Path) -> dict:
        calls.append(path.stem)
        return real(path)

    monkeypatch.setattr(scan, "scan_file", counting)
    first = world.by_id(capsys, "--cache", str(world.cache))
    assert len(calls) == 7  # every transcript in the repo's folders, kept or not
    calls.clear()
    assert world.by_id(capsys, "--cache", str(world.cache)) == first
    assert calls == []
    with world.transcript("ended").open("a", encoding="utf-8") as fh:
        fh.write("\n")
    world.by_id(capsys, "--cache", str(world.cache))
    assert calls == ["ended"]
    world.cache.write_text(json.dumps({"v": scan.CACHE_VERSION + 1, "files": {}}), encoding="utf-8")
    calls.clear()
    world.by_id(capsys, "--cache", str(world.cache))
    assert len(calls) == 7


def test_digest_keeps_prompts_prose_and_collapsed_tools(world, capsys):
    assert scan.main(["digest", "--config-dir", str(world.cfg), "--session", "ended"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["first"] == "USER: Pick up beta-002"
    assert out["tail"].splitlines() == [
        "USER: Pick up beta-002",
        "TOOL: mcp__plugin_taskmaster_tm__backlog_claim",
        "ASSISTANT: Session closed out: beta-002 is in review.",
    ]
    assert scan.main(["digest", "--config-dir", str(world.cfg), "--session", "nope"]) == 0
    assert json.loads(capsys.readouterr().out) == {"error": "session not found"}


def test_the_script_runs_isolated_and_needs_a_root(world):
    ok = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "scan", "--config-dir", str(world.cfg), "--root", str(world.root)],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert ok.returncode == 0, ok.stderr
    assert len(json.loads(ok.stdout)["sessions"]) == 5
    refused = subprocess.run([sys.executable, "-I", str(SCRIPT), "scan", "--config-dir", str(world.cfg)], capture_output=True, text=True, check=False)
    assert refused.returncode == 2


def test_a_truncated_last_line_and_bad_bytes_are_skipped(world, capsys):
    # A session killed mid-write leaves a cut last line; a bad byte must not sink the whole transcript.
    with world.transcript("interrupted").open("ab") as fh:
        fh.write(b'\xff\xfe not json\n{"type":"user","message":{"role":"us')
    s = world.by_id(capsys)["interrupted"]
    assert s["lastEvent"] == "interrupt"
    assert s["prompts"] == 1


def test_a_vanished_or_wrongly_typed_file_is_skipped_and_the_rest_still_report(world, capsys, monkeypatch):
    bad_cost = world.lay("ended", world.root).with_name("bad-cost.jsonl")
    bad_cost.write_text(
        json.dumps({"type": "user", "message": "just a string", "cwd": str(world.root), "timestamp": "2026-10-10T11:00:00.000Z"})
        + "\n"
        + json.dumps({"type": "result", "totalCostUSD": "lots", "cwd": str(world.root)})
        + "\n",
        encoding="utf-8",
    )
    ghost = bad_cost.with_name("ghost.jsonl")
    ghost.write_text("{}\n", encoding="utf-8")
    real_stat = Path.stat

    def stat(self, *a, **k):
        if self.name == "ghost.jsonl":
            raise FileNotFoundError(str(self))
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", stat)
    ids = set(world.by_id(capsys))
    assert {"interrupted", "ended", "ended-then-more", "handed-off", "worktree"} <= ids
    assert "ghost" not in ids


def test_digest_only_reads_sessions_under_the_given_root(world, capsys):
    assert scan.main(["digest", "--config-dir", str(world.cfg), "--session", "elsewhere", "--root", str(world.root)]) == 0
    assert json.loads(capsys.readouterr().out) == {"error": "session not found"}
    assert scan.main(["digest", "--config-dir", str(world.cfg), "--session", "ended", "--root", str(world.root)]) == 0
    assert json.loads(capsys.readouterr().out)["first"] == "USER: Pick up beta-002"


def write_session(world: World, name: str, events: list[dict]) -> Path:
    """A transcript of this repo's own (the current session): `events` are (kind, ...) dicts, one second apart."""
    base = scan.iso_to_epoch("2026-10-14T09:00:00.000Z")
    folder = world.cfg / "projects" / scan.encode_dir(str(world.root))
    folder.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, ev in enumerate(events):
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(base + i))
        common = {"isSidechain": False, "timestamp": stamp, "cwd": str(world.root), "gitBranch": "feat/long", "entrypoint": "cli", "sessionId": name}
        if ev["kind"] == "prompt":
            lines.append({"type": "user", **common, "message": {"role": "user", "content": ev["text"]}})
        elif ev["kind"] == "tool":
            use = {"type": "tool_use", "id": f"tu-{i}", "name": scan.TM_TOOL + ev["name"], "input": ev["input"]}
            lines.append({"type": "assistant", **common, "message": {"role": "assistant", "content": [use], "stop_reason": "tool_use"}})
        elif ev["kind"] == "result":
            body = json.dumps({"result": ev["text"]})
            lines.append({"type": "user", **common, "toolUseResult": {"result": "x"}, "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"tu-{i - 1}", "content": body}]}})
    out = folder / f"{name}.jsonl"
    out.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return out


def tool(name: str, **inp) -> dict:
    return {"kind": "tool", "name": name, "input": inp}


def session_facts(world: World, capsys, name: str, *extra: str) -> dict:
    assert scan.main(["session", "--config-dir", str(world.cfg), "--session", name, "--root", str(world.root), *extra]) == 0
    return json.loads(capsys.readouterr().out)


def test_session_mode_lists_every_task_with_when_and_whether_it_wrote(world, capsys):
    events = [{"kind": "prompt", "text": "Work through the batch"}]
    events += [tool("backlog_get_task", task_id=f"t-{n:03d}") for n in range(1, 13)]  # 12 reads: past restore's cap of 8
    events += [tool("backlog_update_task", task_id="t-002", field="status", value="done"), tool("backlog_get_task", task_id="t-002")]
    events += [tool("backlog_batch_update", task_ids=["t-020", "t-021"])]
    write_session(world, "long", events)
    out = session_facts(world, capsys, "long")
    assert out["found"] is True and out["branch"] == "feat/long"
    ids = [t["id"] for t in out["tasks"]]
    assert len(ids) == 14 and ids[-3:] == ["t-002", "t-020", "t-021"]  # last-touched last, t-002 moved up by its re-read
    by_id = {t["id"]: t for t in out["tasks"]}
    assert by_id["t-001"]["wrote"] is False
    assert by_id["t-002"]["wrote"] is True  # one write among its touches is enough
    assert by_id["t-021"]["wrote"] is True
    assert by_id["t-002"]["at"] == scan.iso_to_epoch("2026-10-14T09:00:14.000Z")
    # Restore keeps its cap of 8 and its rows carry no times.
    capped = scan.scan_file(world.transcript("long"))
    assert len(capped["taskIds"]) == scan.MAX_TASK_IDS and set(capped["taskTouch"]) == set(capped["taskIds"])


def test_session_mode_returns_every_handover_in_write_order_with_its_time(world, capsys):
    events = [{"kind": "prompt", "text": "go"}]
    for n in (1, 2, 3):
        events.append(tool("backlog_handover_create", tldr=f"Part {n} done", next_action=f"Start part {n + 1}", task_ids=["t-001"]))
        events.append({"kind": "result", "text": f"Handover written: 2026-10-14-part-{n}\n- Path: C:/x/part-{n}.md"})
    write_session(world, "long", events)
    out = session_facts(world, capsys, "long")
    assert [h["id"] for h in out["handovers"]] == [f"2026-10-14-part-{n}" for n in (1, 2, 3)]
    assert out["handovers"][2] == {"id": "2026-10-14-part-3", "path": "C:/x/part-3.md", "tldr": "Part 3 done", "next": "Start part 4", "at": scan.iso_to_epoch("2026-10-14T09:00:05.000Z")}
    # The restore scan's rows stay as they were: no write time.
    assert "at" not in scan.facts(scan.scan_file(world.transcript("long")), 0.0, set())["handovers"][0]


def test_session_mode_unknown_foreign_and_unsafe_sessions(world, capsys):
    assert session_facts(world, capsys, "nope")["found"] is False
    assert session_facts(world, capsys, "elsewhere")["found"] is False  # a sibling repo's folder shares the prefix
    assert session_facts(world, capsys, "../x") == {"error": "unusable session id"}
    assert session_facts(world, capsys, "ended")["found"] is True


def test_session_mode_caches_by_size_and_mtime(world, capsys, monkeypatch):
    write_session(world, "long", [{"kind": "prompt", "text": "go"}, tool("backlog_get_task", task_id="t-001")])
    calls: list[int] = []
    real = scan.scan_file

    def counting(path: Path, cap: int = scan.MAX_TASK_IDS) -> dict:
        calls.append(cap)
        return real(path, cap)

    monkeypatch.setattr(scan, "scan_file", counting)
    first = session_facts(world, capsys, "long", "--cache", str(world.cache))
    again = session_facts(world, capsys, "long", "--cache", str(world.cache))
    assert calls == [scan.SESSION_TASK_IDS] and [t["id"] for t in again["tasks"]] == ["t-001"] and first["tasks"] == again["tasks"]
    with world.transcript("long").open("a", encoding="utf-8") as fh:
        fh.write("\n")
    session_facts(world, capsys, "long", "--cache", str(world.cache))
    assert len(calls) == 2


def test_write_tools_are_told_from_reads():
    for name in ("backlog_update_task", "backlog_claim", "backlog_pick_task", "backlog_complete_task", "backlog_handover_update_status", "backlog_batch_update"):
        assert scan.is_write_tool(scan.TM_TOOL + name), name
    for name in ("backlog_get_task", "backlog_list_tasks", "backlog_query", "backlog_search", "backlog_context", "backlog_task_pipeline", "backlog_handover_get", "backlog_batch_preview"):
        assert not scan.is_write_tool(scan.TM_TOOL + name), name
