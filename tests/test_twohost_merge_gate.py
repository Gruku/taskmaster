# User intent: in a member's clone, with the real daemon up, down and the key revoked, show the merge hook never lets a gated merge through on a missing server answer, that the user's "Approve" (via the approval hook) is the only bypass and lasts 60 s, that naming the approval file is refused, and record the accepted limit that a directly written approval file is honoured (team-server-015 S5, Step 12; P-008 is already closed by team-server-008).
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from twohost.fixtures import team
from twohost.member import make_clone, run_hook

pytestmark = [pytest.mark.twohost, pytest.mark.slow, pytest.mark.real_service_process,
              pytest.mark.xdist_group("heavy_processes"), pytest.mark.timeout(240)]

SESSION = "sess-ann"
COMMAND = "git merge feature/x"
QUESTION = "[merge-gate feature/x] The server is down; merge anyway?"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    try:
        with team(tmp_path_factory.mktemp("s5") / "run", patch) as the_team:
            for member in (the_team.ann, the_team.bob):
                make_clone(member, the_team.host.url)
                done = member.set_key()
                assert done.returncode == 0, done.stderr
            yield the_team
    finally:
        patch.undo()


def gate(member, command=COMMAND, session=SESSION):
    return run_hook(member, "merge_gate.py", {"session_id": session, "cwd": str(member.machine.root), "tool_name": "Bash",
                                              "tool_input": {"command": command}})


def approve(member, answer="Approve", question=QUESTION, session=SESSION):
    return run_hook(member, "taskmaster_merge_approve.py", {
        "session_id": session, "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": question}]},
        "tool_response": {"answers": {question: answer}}})


def approval_file(member, session=SESSION) -> Path:
    return Path(member.machine.env["HOME"]) / ".claude" / f"taskmaster-merge-approve-{session}"


def test_with_the_server_up_a_merge_with_no_gated_task_is_allowed(world):
    done = gate(world.ann)
    assert done.returncode == 0, (done.stdout, done.stderr)


NAMING_COMMAND = "echo '{\"branch\":\"feature/x\"}' >> ~/.claude/taskmaster-merge-approve-sess-ann"


def test_a_command_naming_the_approval_file_is_refused_with_the_server_up_too(world):
    done = gate(world.ann, NAMING_COMMAND)
    assert done.returncode == 2 and "approval" in done.stderr.lower(), (done.stdout, done.stderr)
    assert not approval_file(world.ann).exists()


def test_a_revoked_key_blocks_with_the_refused_text(world):
    revoked = world.operator.cli("serve", "revoke-user", "bob", "--root", str(world.root))
    assert revoked.returncode == 0, revoked.stderr
    done = gate(world.bob)
    assert done.returncode == 2 and "merge-gate blocked this merge" in done.stderr, (done.stdout, done.stderr)
    assert "refus" in done.stderr.lower() or "key" in done.stderr.lower(), done.stderr


def test_with_the_server_down_the_merge_blocks_and_only_the_users_approval_lets_it_through(world):
    world.host.stop()
    blocked = gate(world.ann)
    assert blocked.returncode == 2 and "unreachable" in blocked.stderr, (blocked.stdout, blocked.stderr)
    assert "[merge-gate feature/x]" in blocked.stderr                     # the question the agent must put to the user
    assert not approval_file(world.ann).exists()

    # an "Approve" to some other question writes nothing; a "Deny" writes nothing
    approve(world.ann, question="Delete the repository?")
    approve(world.ann, answer="Deny")
    assert not approval_file(world.ann).exists()
    assert gate(world.ann).returncode == 2

    # the user's "Approve" to the gate's own question, in a real session, lets the same command through
    assert approve(world.ann).returncode == 0
    entry = json.loads(approval_file(world.ann).read_text(encoding="utf-8").splitlines()[-1])
    assert entry["branch"] == "feature/x"
    assert gate(world.ann).returncode == 0
    assert gate(world.ann, "git merge other-branch").returncode == 2          # the approval names its branch only
    assert gate(world.ann, session="sess-other").returncode == 2              # nor can another session reuse it

    # the window is 60 s: the same approval, 61 s old, no longer holds (backdated, so the test does not wait)
    # each bound alone: an old stamp inside a fresh file, then an old file holding a fresh stamp, then both
    for stamp_old, file_old in ((True, False), (False, True), (True, True)):
        line = {**entry, "at": entry["at"] - 61} if stamp_old else {**entry, "at": time.time()}
        approval_file(world.ann).write_text(json.dumps(line) + "\n", encoding="utf-8")
        age = time.time() - (61 if file_old else 0)
        os.utime(approval_file(world.ann), (age, age))
        assert gate(world.ann).returncode == 2, (stamp_old, file_old)
    approval_file(world.ann).unlink(missing_ok=True)      # an expired line may already have been pruned


def test_a_command_naming_the_approval_file_is_refused_in_any_mode(world):
    done = gate(world.ann, NAMING_COMMAND)
    assert done.returncode == 2 and "approval" in done.stderr.lower(), done.stderr
    assert not approval_file(world.ann).exists()


def test_the_host_runs_no_git_for_a_member_merge(world):
    log = world.host.log_text()
    assert log.strip(), "the host log is empty, so this check would pass without looking"
    assert "git merge" not in log and "feature/x" not in log, "the host log names the member's merge"


def test_accepted_limit_g8_an_approval_file_written_directly_is_honoured(world):
    """Not a pin and not expected to close: the gate cannot tell who wrote the file. That is why 027 frames every channel
    a teammate's text can reach an agent through (the reviewer's evidence for G8)."""
    approval_file(world.ann).parent.mkdir(parents=True, exist_ok=True)
    approval_file(world.ann).write_text(json.dumps({"branch": "feature/x", "at": time.time()}) + "\n", encoding="utf-8")
    assert gate(world.ann).returncode == 0
