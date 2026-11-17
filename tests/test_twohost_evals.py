# User intent: give the security review one more kind of evidence - what a member's own coding agent actually does on a team server (which tools it picks, whether it relays refusals) - by pointing the agent tool-use eval's only door at a T1 host with a member key; the smoke test proves the door works and never leaks the key, the agent run is on demand because it bills a model and holds ~0.5 GB (team-server-015 Step 14; evidence, not a gate).
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from twohost.fixtures import team

pytestmark = [pytest.mark.twohost, pytest.mark.slow, pytest.mark.real_service_process,
              pytest.mark.xdist_group("heavy_processes"), pytest.mark.timeout(900)]

EVALS = Path(__file__).resolve().parents[1] / "evals" / "agent_tool_use"
KEY_VAR = "TM_EVAL_MEMBER_KEY"
SERVER_SCENARIOS = ("s48", "s49", "s50", "s51", "s52")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    try:
        with team(tmp_path_factory.mktemp("evals") / "run", patch) as the_team:
            yield the_team
    finally:
        patch.undo()


def tmcli(world, home: Path, run: str, *args: str, key: str | None = ...) -> subprocess.CompletedProcess:
    env = {**os.environ, "TM_EVAL_HOME": str(home), "PYTHONIOENCODING": "utf-8"}
    env.pop(KEY_VAR, None)
    if key is ...:
        env[KEY_VAR] = world.ann.key
    elif key:
        env[KEY_VAR] = key
    return subprocess.run([sys.executable, str(EVALS / "tmcli.py"), "--run", run, "--server-url", world.host.url,
                           "--key-env", KEY_VAR, *args], capture_output=True, text=True, env=env, timeout=120)


def test_the_eval_door_reaches_the_server_as_a_member_and_never_logs_the_key(world, tmp_path):
    home = tmp_path / "evalhome"
    names = tmcli(world, home, "t1-s48", "names")
    assert names.returncode == 0, (names.stdout, names.stderr)
    assert "backlog_pick_task" in names.stdout.split()
    described = tmcli(world, home, "t1-s48", "describe", "backlog_claim")
    assert described.returncode == 0 and "backlog_claim" in described.stdout
    called = tmcli(world, home, "t1-s48", "call", "backlog_claim", '{"action": "status", "task_id": "test-epic-001"}')
    assert called.returncode == 0, (called.stdout, called.stderr)
    log = (home / "logs" / "t1-s48.jsonl").read_text(encoding="utf-8")
    assert world.ann.key not in log and "Bearer" not in log
    assert [json.loads(line)["action"] for line in log.splitlines()] == ["names", "describe", "call"]
    assert not (home / "runs").exists()                      # server mode never copies a local backlog


def test_server_mode_without_a_key_in_the_named_variable_stops_before_connecting(world, tmp_path):
    done = tmcli(world, tmp_path / "evalhome", "t1-s49", "names", key=None)
    assert done.returncode != 0 and KEY_VAR in (done.stdout + done.stderr)


@pytest.mark.skipif(os.environ.get("TASKMASTER_TWOHOST_EVALS") != "1",
                    reason="on demand: bills a model and holds ~0.5 GB per claude process (set TASKMASTER_TWOHOST_EVALS=1)")
def test_the_five_server_scenarios_run_one_at_a_time_and_the_results_go_to_the_evidence_pack(world, tmp_path):
    import yaml
    from twohost.evidence import Evidence
    claude = shutil.which("claude")
    assert claude, "claude CLI not found"
    scenarios = {s["id"].split("-")[0]: s["request"]
                 for s in yaml.safe_load((EVALS / "scenarios.yaml").read_text(encoding="utf-8"))["scenarios"]}
    truth = yaml.safe_load((EVALS / "ground_truth.yaml").read_text(encoding="utf-8"))["ground_truth"]
    home = tmp_path / "evalhome"
    (home).mkdir(parents=True, exist_ok=True)
    (home / "ground_truth.json").write_text(json.dumps({"ground_truth": truth}), encoding="utf-8")
    template = (EVALS / "prompt_template.xml").read_text(encoding="utf-8")
    template = template[template.index("-->") + 3:]
    # state the scenarios rely on: bob holds Document the thing; ann's earlier session held Test the thing
    world.bob.call("backlog_pick_task", {"task_id": "test-epic-003"})
    world.ann.call("backlog_pick_task", {"task_id": "test-epic-002"})
    results = {}
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}        # the global key bills the API
    env.update({"TM_EVAL_HOME": str(home), "TM_EVAL_SERVER_URL": world.host.url, "TM_EVAL_KEY_ENV": KEY_VAR,
                KEY_VAR: world.ann.key})
    for short in SERVER_SCENARIOS:
        run = f"ev-{short}"
        briefing = (template.replace("{{CLI}}", str(EVALS / "tmcli.py")).replace("{{RUN_ID}}", run)
                    .replace("{{REQUEST}}", scenarios[short]))
        done = subprocess.run([claude, "-p", "--model", "haiku", "--allowedTools", "Bash"], input=briefing, text=True,
                              capture_output=True, env=env, timeout=600, cwd=str(home))   # a neutral cwd: the agent must not load this repo's CLAUDE.md or MCP config
        checked = subprocess.run([sys.executable, str(EVALS / "check.py"), run, "--home", str(home)], capture_output=True,
                                 text=True, timeout=60, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        results[short] = {"exit": done.returncode, "answer": done.stdout[-1500:], "check": checked.stdout}
    Evidence.from_env().write("evals.json", results)
    assert set(results) == set(SERVER_SCENARIOS)
