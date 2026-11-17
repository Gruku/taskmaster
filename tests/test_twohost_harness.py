# User intent: prove the two-host harness's own machinery (opt-in marker, waits, launcher safety, machine environments, evidence recorder, gap table, orphan watchdog and pid sweep) without starting a daemon; runs in the normal set (team-server-015 plan Step 1).
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import conftest
from twohost import evidence, gaps, host, launch_host, procid, waits, world

TWOHOST_DIR = Path(__file__).resolve().parent / "twohost"
TESTS = Path(__file__).resolve().parent


# 1. the opt-in marker -------------------------------------------------------------------------------------------

class FakeItem:
    def __init__(self, *markers):
        self.markers = set(markers)

    def get_closest_marker(self, name):
        return name if name in self.markers else None


def run_hook(expression, items):
    deselected = []
    config = SimpleNamespace(option=SimpleNamespace(markexpr=expression),
                             hook=SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items)))
    conftest.pytest_collection_modifyitems(config, items)
    return deselected


def test_twohost_items_are_deselected_unless_the_expression_names_them():
    plain, two, scale = FakeItem(), FakeItem("twohost"), FakeItem("scale")
    items = [plain, two, scale]
    gone = run_hook("", items)
    assert items == [plain] and set(map(id, gone)) == {id(two), id(scale)}
    items = [plain, two, scale]
    run_hook("twohost", items)
    assert items == [plain, two]            # scale stays deselected, as before
    items = [plain, two, scale]
    run_hook("scale", items)
    assert items == [plain, scale]
    items = [plain, two, scale]
    run_hook("twohost or scale", items)
    assert items == [plain, two, scale]


# 2. waits ---------------------------------------------------------------------------------------------------------

def test_until_fails_at_its_bound_with_the_description_and_last_value():
    seen = iter(range(1000))
    with pytest.raises(waits.WaitFailed) as exc:
        waits.until(lambda: next(seen) < 0, timeout=0.3, what="the moon to rise")
    assert "the moon to rise" in str(exc.value) and "last value:" in str(exc.value)
    assert waits.until(lambda: "yes", timeout=1) == "yes"
    with pytest.raises(AssertionError):
        waits.until(lambda: True, timeout=31, what="too long")


# 3. no bare sleeps ------------------------------------------------------------------------------------------------

def test_no_sleep_outside_waits():
    offenders = [f"{path.name}:{number}" for path in TWOHOST_DIR.glob("*.py") if path.name != "waits.py"
                 for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                 if "time.sleep(" in line and not line.lstrip().startswith("#")]
    offenders += [f"{path.name}:{number}" for path in TESTS.glob("test_twohost_*.py") if path.name != Path(__file__).name
                  for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                  if "time.sleep(" in line]
    assert offenders == []


# 4. launcher safety -----------------------------------------------------------------------------------------------

def test_the_launcher_refuses_without_the_opt_in_or_outside_the_base(tmp_path):
    inside = tmp_path / "run" / "H"
    inside.mkdir(parents=True)
    with pytest.raises(SystemExit) as exc:                                   # M2: a base with no sentinel is not a run base
        launch_host.check_launch({"TASKMASTER_TWOHOST": "1"}, ["--root", str(inside)], tmp_path / "run")
    assert "not a harness run base" in str(exc.value)
    (tmp_path / "run" / launch_host.SENTINEL).write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:                                   # M1: two --root, serve honours the last
        launch_host.check_launch({"TASKMASTER_TWOHOST": "1"}, ["--root", str(inside), "--root", str(tmp_path)], tmp_path / "run")
    assert "more than one --root" in str(exc.value)
    with pytest.raises(SystemExit) as exc:
        launch_host.check_launch({}, ["--root", str(inside)], tmp_path / "run")
    assert exc.value.code == 2 and "TASKMASTER_TWOHOST=1" in str(exc.value)
    with pytest.raises(SystemExit) as exc:
        launch_host.check_launch({"TASKMASTER_TWOHOST": "1"}, ["--root", str(tmp_path / "elsewhere")], tmp_path / "run")
    assert exc.value.code == 2 and "not under" in str(exc.value)
    with pytest.raises(SystemExit):
        launch_host.check_launch({"TASKMASTER_TWOHOST": "1"}, ["--port", "0"], tmp_path / "run")
    assert launch_host.check_launch({"TASKMASTER_TWOHOST": "1"}, ["--root", str(inside)], tmp_path / "run") == inside.resolve()
    assert launch_host.check_launch({"TASKMASTER_TWOHOST": "1"}, [f"--root={inside}"], tmp_path / "run") == inside.resolve()


def test_the_launcher_is_never_packaged():
    text = (TESTS.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert "launch_host" not in text and "twohost" not in text


# 5. per-machine environment ---------------------------------------------------------------------------------------

def test_a_machine_env_drops_every_server_variable_and_points_home_at_the_machine(tmp_path):
    parent = {"PATH": os.environ.get("PATH", ""), "TASKMASTER_ROOT": "r", "TASKMASTER_SERVER_URL": "u",
              "TASKMASTER_LINEAR_API_KEY": "k", "TASKMASTER_MEMBER_KEY": "k", "ANTHROPIC_API_KEY": "a",
              "GIT_DIR": "x", "GIT_AUTHOR_NAME": "n", "HOME": "REAL", "USERPROFILE": "REAL", "TASKMASTER_KEEP": "1"}
    env = world.machine_env(tmp_path, "ann", parent=parent)
    for name in ("TASKMASTER_ROOT", "TASKMASTER_SERVER_URL", "TASKMASTER_LINEAR_API_KEY", "TASKMASTER_MEMBER_KEY",
                 "ANTHROPIC_API_KEY", "GIT_DIR", "GIT_AUTHOR_NAME"):
        assert name not in env, name
    assert env["TASKMASTER_KEEP"] == "1" and env["PATH"] == parent["PATH"]
    home = tmp_path / "machines" / "ann" / "home"
    for name in ("HOME", "USERPROFILE"):
        assert Path(env[name]) == home
    assert Path(env["APPDATA"]).is_relative_to(home) and Path(env["LOCALAPPDATA"]).is_relative_to(home)
    assert Path(env["TASKMASTER_CONFIG_DIR"]).is_relative_to(home) and Path(env["TASKMASTER_CACHE_DIR"]).is_relative_to(home)
    assert Path(env["GIT_CONFIG_GLOBAL"]).read_text(encoding="utf-8").count("ann@twohost.invalid") == 1
    assert (home / ".claude").is_dir()           # the merge approval's home (merge_gate.py)
    other = world.machine_env(tmp_path, "bob", parent=parent)
    assert other["HOME"] != env["HOME"]


# 6. evidence ------------------------------------------------------------------------------------------------------

def test_key_id_names_a_real_minted_key_and_the_operator_token():
    from taskmaster.serve.principals import mint_key
    key, _digest = mint_key(set())
    assert evidence.key_id(f"Bearer {key}") == key.split("_")[2]
    assert evidence.key_id("Bearer tm_op_" + "A" * 43) == "operator"
    assert evidence.key_id("Bearer nonsense") == "unknown"


def test_the_pack_keeps_every_world_s_transcripts_and_redacts_a_login_body(tmp_path):
    first, second = evidence.Evidence(tmp_path, name="one"), evidence.Evidence(tmp_path, name="two")
    first.transcript(who="ann", method="GET", target="/a", status=200)
    second.transcript(who="bob", method="POST", target="/login", request="token=tm_u_" + "ab" * 8 + "_" + "S" * 43, status=303)
    first.flush()
    second.flush()
    lines = [json.loads(line) for name in ("one", "two") for line in
             (tmp_path / f"transcripts-{name}.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [line["who"] for line in lines] == ["ann", "bob"]
    assert "S" * 10 not in json.dumps(lines) and "token=<redacted>" in lines[1]["request"]
    first.mcp(who="ann", tool="backlog_status", arguments={"x": 1}, key="tm_op_" + "A" * 43, answer="ok")
    assert first.lines[-1]["key_id"] == "operator" and first.lines[-1]["method"] == "MCP"


def test_two_worlds_with_the_same_name_both_keep_their_transcripts(tmp_path):
    # Nine modules build their world with team()'s default name; a later flush must not overwrite an earlier one.
    first, second = evidence.Evidence(tmp_path, name="host"), evidence.Evidence(tmp_path, name="host")
    first.transcript(who="ann", method="GET", target="/a", status=200)
    second.transcript(who="bob", method="GET", target="/b", status=200)
    first.flush()
    second.flush()
    who = sorted(json.loads(line)["who"] for path in tmp_path.glob("transcripts-*.jsonl")
                 for line in path.read_text(encoding="utf-8").splitlines())
    assert who == ["ann", "bob"]


def test_the_recorder_reduces_authorization_to_a_key_id_and_caps_bodies():
    secret = "tm_u_" + "ab12cd34ef56ab78" + "_" + "S" * 43
    recorder = evidence.Evidence()
    big = "x" * 5000
    line = recorder.transcript(who="ann", method="POST", target="/mcp", authorization=f"Bearer {secret}",
                               request=big, status=200, response="ok")
    entry = recorder.lines[line]
    assert entry["key_id"] == "ab12cd34ef56ab78"
    assert secret not in json.dumps(recorder.lines) and "S" * 10 not in json.dumps(recorder.lines)
    assert len(entry["request"]) < evidence.BODY_CAP + 40 and "more chars" in entry["request"]
    assert evidence.key_id(None) == "none"


def test_the_scan_finds_a_needle_in_every_spelling():
    recorder = evidence.Evidence()
    recorder.record("mcp", "tool_a", "see C:\\Srv\\Host\\proj for details")
    recorder.record("mcp", "tool_b", json.dumps({"path": "C:\\Srv\\Host\\proj"}))
    recorder.record("mcp", "tool_c", "c:/srv/host/proj lower-case forward slashes")
    recorder.record("mcp", "tool_d", "nothing to see")
    found = recorder.scan({"machine-root": ["C:\\Srv\\Host\\proj"]})
    assert sorted(found) == ["tool_a", "tool_b", "tool_c"]
    assert found["tool_a"]["machine-root"]["excerpt"].startswith("see ")


def test_the_pack_is_written_only_when_asked(tmp_path):
    assert evidence.Evidence().write("x.json", {"a": 1}) is None
    out = evidence.Evidence(tmp_path / "pack")
    out.transcript(who="ann", method="GET", target="/", status=200)
    out.flush()
    assert out.write("run.json", {"commit": "c"}).exists()
    assert (tmp_path / "pack" / "transcripts-world.jsonl").read_text(encoding="utf-8").count("\n") == 1


# 7. gap markers ---------------------------------------------------------------------------------------------------

def test_every_gap_marker_is_listed_and_no_listed_gap_is_orphaned():
    used = set()
    for path in [*TESTS.glob("test_twohost_*.py"), TESTS / "test_serve_surface_inventory.py"]:
        if path.name == Path(__file__).name or not path.exists():
            continue
        used |= set(re.findall(r'gap\(\s*"([^"]+)"\s*\)', path.read_text(encoding="utf-8")))
    used -= {"team-server-0NN", "task_id"}
    assert used <= set(gaps.GAPS), f"gap markers missing from tests/twohost/gaps.py: {sorted(used - set(gaps.GAPS))}"
    assert all(gaps.GAPS.values())      # each row says what it pins; the closing lane deletes the row


# 8. watchdog and sweep --------------------------------------------------------------------------------------------

PY = sys.executable
SLEEP = "import time; time.sleep(120)"


def spawn(*args):
    return subprocess.Popen([PY, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_a_child_with_the_watchdog_exits_within_two_seconds_of_its_parent(tmp_path):
    parent = spawn("-c", SLEEP)
    script = ("import sys,time; sys.path.insert(0, sys.argv[2]); from twohost import launch_host; "
              "launch_host.start_parent_watchdog(int(sys.argv[1]), interval=0.2); time.sleep(120)")
    child = spawn("-c", script, str(parent.pid), str(TESTS))
    try:
        waits.until(lambda: procid.alive(child.pid), 10, "the stub child to start")
        waits.until(lambda: child.poll() is None, 1, "the stub child to stay up while its parent lives")
        assert child.poll() is None
        parent.kill()
        parent.wait(10)
        started = time.monotonic()
        waits.until(lambda: child.poll() is not None, 5, "the stub child to exit after its parent")
        assert time.monotonic() - started < 2.5
    finally:
        for proc in (parent, child):
            if proc.poll() is None:
                proc.kill()


def test_the_sweep_kills_only_an_exact_match(tmp_path):
    stub = spawn("-c", SLEEP, str(tmp_path / "H"))            # the root appears in its command line
    other = spawn("-c", SLEEP, str(tmp_path / "H"))
    try:
        waits.until(lambda: procid.identity(stub.pid), 30, "the stub's identity")
        host.register(tmp_path, stub.pid, tmp_path / "H")
        entries = json.loads((tmp_path / host.PID_FILE).read_text(encoding="utf-8"))
        assert entries[0]["pid"] == stub.pid and entries[0]["create_time"] and entries[0]["cmdline"]
        # the stub's command line names neither launch_host.py nor serve, so the sweep treats it as not ours
        assert host.sweep_leftovers(tmp_path, kill=lambda pid: pytest.fail("killed a process that is not ours")) == []
        assert json.loads((tmp_path / host.PID_FILE).read_text(encoding="utf-8")) == []
    finally:
        for proc in (stub, other):
            proc.kill()


def entry_for(pid, root, **changes):
    found = procid.identity(pid)
    return {"pid": pid, "root": str(root), **found, **changes}


def test_the_sweep_matches_on_pid_creation_time_and_command_line(tmp_path, monkeypatch):
    root = tmp_path / "H"
    stub = spawn("-c", SLEEP + "  # launch_host.py", str(root))
    try:
        waits.until(lambda: procid.identity(stub.pid), 30, "the stub's identity")
        good = entry_for(stub.pid, root)
        killed = []

        def sweep(entry):
            (tmp_path / host.PID_FILE).write_text(json.dumps([entry]), encoding="utf-8")
            return host.sweep_leftovers(tmp_path, kill=killed.append)

        # the pid is reused by another process: creation time differs, or the command line differs
        assert sweep({**good, "create_time": "1999-01-01"}) == [] and killed == []
        assert sweep({**good, "cmdline": good["cmdline"] + " other"}) == [] and killed == []
        assert sweep({**good, "create_time": ""}) == [] and killed == []          # unreadable at record time
        assert sweep({**good, "cmdline": ""}) == [] and killed == []
        assert sweep({**good, "root": str(tmp_path / "elsewhere")}) == [] and killed == []
        assert sweep(good) == [stub.pid] and killed == [stub.pid]                 # all three fields match
        # an entry whose process is gone is dropped and kills nothing
        stub.kill()
        stub.wait(10)
        waits.until(lambda: not procid.alive(stub.pid), 10, "the stub to be gone")
        assert sweep(good) == []
        assert json.loads((tmp_path / host.PID_FILE).read_text(encoding="utf-8")) == []
    finally:
        if stub.poll() is None:
            stub.kill()


def test_the_sweep_leaves_a_live_process_whose_pid_matches_but_whose_identity_differs(tmp_path):
    """Simulated pid reuse: the entry is rewritten against this test's own pid (a live pytest, not a daemon)."""
    root = tmp_path / "H"
    mine = entry_for(os.getpid(), root)
    (tmp_path / host.PID_FILE).write_text(json.dumps([{**mine, "cmdline": "python launch_host.py --root " + str(root)}]),
                                          encoding="utf-8")
    assert host.sweep_leftovers(tmp_path, kill=lambda pid: pytest.fail("killed a live pytest")) == []


# 9. the revoked-session wait must not pass on a throttle ----------------------------------------------------------

def test_the_ended_session_probe_counts_only_an_auth_refusal_as_ended():
    import httpx
    from test_twohost_principals import session_ended

    class Raises:
        def __init__(self, exc):
            self.exc = exc

        def call(self, *args, **kwargs):
            raise self.exc

    def status_error(code):
        request = httpx.Request("POST", "http://x/mcp")
        return httpx.HTTPStatusError(f"{code}", request=request, response=httpx.Response(code, request=request))

    assert session_ended(Raises(status_error(401))) is True
    with pytest.raises(httpx.HTTPStatusError):
        session_ended(Raises(status_error(429)))              # the old probe read this as "session ended"
