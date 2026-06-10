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


def test_ported_hooks_route_through_launcher_with_timeout():
    """The four ported hooks SOURCE run_hook.sh (fail-open launcher: a
    missing script or interpreter must never become an accidental exit-2
    deny). Sourcing (`. file`) instead of `bash file` is load-bearing: MSYS
    bash process creation costs seconds under load, and the env-assignment
    prefix is the POSIX-portable way to pass the script name into a sourced
    file. session-start.sh is unchanged; snapshot.py also routes through
    the launcher."""
    d = json.loads(HJ.read_text())["hooks"]
    ported = []
    for event in ("PreToolUse", "PostToolUse"):
        for m in d[event]:
            for h in m["hooks"]:
                if "merge_gate.py" in h["command"] or "merge_recorder.py" in h["command"] \
                        or "worktree_submodule_init.py" in h["command"] \
                        or "taskmaster_merge_approve.py" in h["command"]:
                    ported.append(h)
    assert len(ported) == 4
    for h in ported:
        script = h["command"].split("=", 1)[1].split(" ", 1)[0]
        assert h["command"] == (
            'CLAUDE_HOOK_SCRIPT=%s . "${CLAUDE_PLUGIN_ROOT}/hooks/run_hook.sh"'
            % script)
        assert h["timeout"] == 10
    # session-start unchanged; PreCompact snapshot routed through launcher too
    ss = " ".join(h["command"] for m in d["SessionStart"] for h in m["hooks"])
    pc = " ".join(h["command"] for m in d["PreCompact"] for h in m["hooks"])
    assert "session-start.sh" in ss
    assert "CLAUDE_HOOK_SCRIPT=snapshot.py . " in pc
