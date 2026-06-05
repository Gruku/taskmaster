import json, pathlib
HJ = pathlib.Path(__file__).parents[1] / "hooks" / "hooks.json"


def test_merge_hooks_registered():
    d = json.loads(HJ.read_text())["hooks"]
    pre = " ".join(h["command"] for m in d["PreToolUse"] for h in m["hooks"])
    post = " ".join(h["command"] for m in d["PostToolUse"] for h in m["hooks"])
    assert "merge_gate.py" in pre
    assert "merge_recorder.py" in post
    assert "taskmaster_merge_approve.py" in post
    assert "worktree_submodule_init.py" in post   # existing preserved


def test_approval_writer_matches_ask_user_question():
    d = json.loads(HJ.read_text())["hooks"]
    approve_matchers = [m["matcher"] for m in d["PostToolUse"]
                        for h in m["hooks"] if "taskmaster_merge_approve.py" in h["command"]]
    assert approve_matchers and all("AskUserQuestion" in m for m in approve_matchers)


def test_ported_hooks_invoke_python_with_timeout():
    """The four ported hooks run via python with a 10s timeout; the
    session-start.sh and snapshot.py registrations are unchanged."""
    d = json.loads(HJ.read_text())["hooks"]
    ported = []
    for event in ("PreToolUse", "PostToolUse"):
        for m in d[event]:
            for h in m["hooks"]:
                if "hooks/merge_" in h["command"] or "hooks/worktree_" in h["command"] \
                        or "hooks/taskmaster_" in h["command"]:
                    ported.append(h)
    assert len(ported) == 4
    for h in ported:
        assert h["command"].startswith('python "${CLAUDE_PLUGIN_ROOT}/hooks/')
        assert h["timeout"] == 10
    # untouched entries
    ss = " ".join(h["command"] for m in d["SessionStart"] for h in m["hooks"])
    pc = " ".join(h["command"] for m in d["PreCompact"] for h in m["hooks"])
    assert "session-start.sh" in ss
    assert "snapshot.py" in pc
