import json, pathlib
HJ = pathlib.Path(__file__).parents[1] / "hooks" / "hooks.json"


def test_merge_hooks_registered():
    d = json.loads(HJ.read_text())["hooks"]
    pre = " ".join(h["command"] for m in d["PreToolUse"] for h in m["hooks"])
    post = " ".join(h["command"] for m in d["PostToolUse"] for h in m["hooks"])
    assert "merge-gate.sh" in pre
    assert "merge-recorder.sh" in post
    assert "taskmaster-merge-approve.sh" in post
    assert "worktree-submodule-init.sh" in post   # existing preserved


def test_approval_writer_matches_ask_user_question():
    d = json.loads(HJ.read_text())["hooks"]
    approve_matchers = [m["matcher"] for m in d["PostToolUse"]
                        for h in m["hooks"] if "taskmaster-merge-approve.sh" in h["command"]]
    assert approve_matchers and all("AskUserQuestion" in m for m in approve_matchers)
