# User intent: pin the internal caller_scope vocabulary (team-server-004 D1) so a remote member can never name one and
# no new scope literal drifts back into the code.
import ast
from pathlib import Path

from taskmaster.coordinator import scopes
from taskmaster.native import claims
from taskmaster.serve import sessions

ANN = "ann@" + "a" * 16
ROOT = Path(__file__).resolve().parents[1] / "taskmaster"


def test_no_internal_scope_parses_as_a_remote_session():
    samples = list(scopes.INTERNAL) + ["sync-" + "0" * 64, "git:ann@" + "a" * 16, "git:status"]
    for scope in samples:
        assert claims.remote_handle(scope) is None, scope
        assert scopes.is_internal(scope), scope
    for scope in (ANN, "ann@viewer", "host-1-aaaaaaaa", None, 7):
        assert not scopes.is_internal(scope), scope


def test_named_sessions():
    batch = {"operation": "batch", "caller_scope": "host-1-aaaaaaaa", "arguments": {"commands": [
        {"operation": "task.pick", "arguments": {"id": "t-1", "session": ANN}},
        {"operation": "task.update", "arguments": {"id": "t-1"}}]}}
    assert scopes.named_sessions("execute", {"envelope": batch}) == ["host-1-aaaaaaaa", ANN]
    assert scopes.named_sessions("linear_retry", {"caller_scope": "x"}) == ["x"]
    assert scopes.named_sessions("status", {}) == []
    assert scopes.named_sessions("execute", {"envelope": "junk"}) == []


def test_sessions_reexports_the_one_walk():
    assert sessions.claim_operations is scopes.claim_operations
    assert sessions.CLAIM_OPERATIONS is scopes.CLAIM_OPERATIONS


def test_log_session_prints_only_remote_shapes():
    assert scopes.log_session(ANN) == ANN
    assert scopes.log_session("evil\nline") == "invalid"
    assert scopes.log_session(None) == "invalid"


def _literals(path):
    return ast.walk(ast.parse(path.read_text(encoding="utf-8")))


def test_no_scope_literals_outside_the_vocabulary():
    internal = set(scopes.INTERNAL)
    found = []
    for path in ROOT.rglob("*.py"):
        if path.name == "scopes.py" and path.parent.name == "coordinator":
            continue
        for node in _literals(path):
            if isinstance(node, ast.keyword) and node.arg == "caller_scope" and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                found.append((path.name, node.lineno, node.value.value))
            elif isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value == "caller_scope" and isinstance(value, ast.Constant) \
                            and isinstance(value.value, str):
                        found.append((path.name, node.lineno, value.value))
            elif isinstance(node, ast.arguments):
                names = [a.arg for a in node.args + node.kwonlyargs]
                defaults = node.defaults + [d for d in node.kw_defaults if d is not None]
                # a signature default for a caller_scope parameter must reference the constants
                for arg, default in zip(node.kwonlyargs, node.kw_defaults):
                    if arg.arg == "caller_scope" and isinstance(default, ast.Constant) and isinstance(default.value, str):
                        found.append((path.name, default.lineno, default.value))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                if value in internal or (value in (scopes.SYNC_PREFIX, scopes.GIT_PREFIX)):
                    found.append((path.name, node.lineno, value))
    assert not found, found
