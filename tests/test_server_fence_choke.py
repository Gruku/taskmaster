# User intent: prove every store choke point (G1-G4) refuses in a server-mode project and creates nothing, is
# open to the lease holder, and that a ratchet keeps new store-opening call sites from bypassing the fence
# (team-server-009 Step 3).
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

from taskmaster import backlog_server as bs
from taskmaster import server_fence as fence
from taskmaster import server_marker as sm
from taskmaster import store
from taskmaster.coordinator import protocol
from taskmaster.coordinator.adapter import Client
from taskmaster.coordinator.service import Coordinator
from taskmaster.native import cutover
from taskmaster.native_routing import gate, registry

from test_server_fence import HOST_ID, URL, git, mark, project, repo  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def _fresh_fence():
    fence.reset_for_tests()
    yield
    leaked = fence.reset_for_tests()
    assert not leaked


def tree(path: Path) -> list[str]:
    return sorted(str(p.relative_to(path)) for p in path.rglob("*") if ".git" not in p.parts)


@pytest.fixture
def teammate(repo, monkeypatch):
    mark(repo)
    monkeypatch.setattr(bs, "ROOT", repo)
    return repo


def test_g1a_every_tool_is_refused_and_nothing_is_created(teammate):
    before = tree(teammate)
    for tool in (bs.backlog_init, bs.backlog_project_init, bs.backlog_status, bs.backlog_list_tasks):
        answer = tool() if tool is not bs.backlog_project_init else tool(name="x")
        assert "Taskmaster server" in answer and "Nothing was changed" in answer, answer
    json_tool = bs.backlog_claim(task_id="t-1")
    assert "Taskmaster server" in json.loads(json_tool)["error"]
    assert not (teammate / ".taskmaster").exists()
    assert [p for p in tree(teammate) if p != ".git" and not p.startswith(".git")] == [
        p for p in before if not p.startswith(".git")]


def test_g1a_a_root_layout_backlog_yaml_cannot_dodge_the_fence(teammate):
    (teammate / "backlog.yaml").write_text("tasks: []\n", encoding="utf-8")
    assert "Taskmaster server" in bs.backlog_status()


def test_g1b_native_database_refuses_for_the_store_root_and_a_subdirectory(teammate):
    sub = teammate / "sub"
    for backlog in (teammate / ".taskmaster" / "backlog.yaml", sub / ".taskmaster" / "backlog.yaml",
                    teammate / "backlog.yaml"):
        with pytest.raises(gate.ServerModeUnavailable):
            gate.native_database(backlog)
    assert registry.route("backlog_status", None, teammate / ".taskmaster" / "backlog.yaml", "s", (), {}) \
        .startswith("Error: ")
    answer = registry.route("backlog_claim", None, teammate / ".taskmaster" / "backlog.yaml", "s", (), {})
    assert "error" in json.loads(answer)
    assert not (sub / ".taskmaster").exists()


def test_g2_open_store_and_read_only_status_refuse_and_create_nothing(teammate):
    for target in (teammate / ".taskmaster" / "backlog.yaml", teammate / "sub" / ".taskmaster" / "backlog.yaml"):
        with pytest.raises(fence.ServerModeRefused):
            store.open_store(target)
        with pytest.raises(fence.ServerModeRefused):
            store.read_only_status(target)
    assert not (teammate / ".taskmaster").exists() and not (teammate / "sub" / ".taskmaster").exists()


def test_g3_connect_client_and_coordinator_refuse(teammate, monkeypatch):
    root = project(teammate)
    with pytest.raises(fence.ServerModeRefused):
        protocol.connect(root, readonly=True)
    monkeypatch.setattr("taskmaster.coordinator.client._launch",
                        lambda *a, **k: pytest.fail("launched a coordinator in a server-mode clone"))
    with pytest.raises(fence.ServerModeRefused):
        Client(root)
    with pytest.raises(fence.ServerModeRefused):
        Coordinator(root)
    assert not (root / ".taskmaster" / "local" / "coordinator" / "owner.lock").exists()
    with pytest.raises(fence.ServerModeRefused):
        protocol.connect(teammate / "sub", readonly=True)
    assert not (teammate / "sub" / ".taskmaster").exists()


def test_g4_cutover_is_refused_with_json_and_exit_2(teammate, capsys):
    root = project(teammate)
    code = cutover.main(["--root", str(root), "--dry-run", "--json"])
    assert code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["state"] == "refused" and "Taskmaster server" in report["detail"]
    for call in (cutover.dry_run, cutover.cutover, cutover.rollback, cutover.fence_state):
        with pytest.raises(fence.ServerModeRefused):
            call(root)


def test_the_lease_holder_passes_every_choke_point(repo, monkeypatch):
    root = project(repo)
    mark(repo)
    with fence.claim_host(root):
        fence.check(root)
        assert fence.refusal(root / "sub") is None
        # G3 gets past the fence and fails later for want of a store, not with ServerModeRefused.
        with pytest.raises(sqlite3.OperationalError):
            protocol.connect(root, readonly=True)


def test_m10_a_coordinator_started_before_the_marker_stops_on_the_next_message(repo):
    # A dispatch-level check: the marker appears after construction.
    class Stub:
        root = repo
        stopped = False

        def stop(self):
            self.stopped = True

    stub = Stub()
    mark(repo)
    with pytest.raises(fence.ServerModeRefused):
        Coordinator.dispatch(stub, {"method": "status", "identity": {}})
    assert stub.stopped


# -- the ratchet (design 7.2, M3) ----------------------------------------------------------------

_SITE = re.compile(r"sqlite3\.connect\(|(?<![A-Za-z_.])Store\(|Ownership\(|private_directory\(")
_SKIP_LINE = re.compile(r"^\s*#|def private_directory|class Ownership|class Store\b")
_BENCH = "operator script on synthetic or scratch data; not an entry point of the plugin"
# file -> (call-site count, the choke point that covers it, or why it is exempt)
_ALLOWED = {
    "taskmaster/store.py": (7, "G2 open_store/read_only_status; internal connects sit behind it"),
    "taskmaster/coordinator/protocol.py": (1, "G3 connect itself"),
    "taskmaster/coordinator/service.py": (1, "G3 via identify; Coordinator.__init__ builds its Ownership after it"),
    "taskmaster/coordinator/ownership.py": (1, "defines the lock class; callers are listed here"),
    "taskmaster/native_routing/gate.py": (2, "G1b native_database fences first; the connect follows"),
    "taskmaster/native/cutover.py": (5, "G4 every entry point checks first"),
    "taskmaster/native/fresh.py": (2, "reached only from backlog_init (G1a) and cutover (G4)"),
    "taskmaster/native/quiesce.py": (1, "called only from cutover (G4)"),
    "taskmaster/native/compatibility.py": (1, "a new snapshot file; never the store"),
    "taskmaster/native/query_snapshots.py": (1, "re-opens, read-only, the store file the caller's already fenced connection reads"),
    "taskmaster/native/query_worker.py": (1, "opens a published snapshot file read-only, never the store"),
    "taskmaster/store_integrity.py": (1, "fingerprint: a mode=ro snapshot read of meta/domain_events; never writes the store"),
    "taskmaster/integrity.py": (1, "only from inside an opened Store (G2)"),
    "taskmaster/coordinator/git_hook.py": (1, "the pre-commit validator; server mode answers before it (section 7.3)"),
    "taskmaster/native_routing/hook_reads.py": (1, "read-only hook read; unchanged by 009 (D7), 007 reroutes"),
    "taskmaster/serve/auth.py": (1, "serve/ files: principals and operator token, not the store"),
    "taskmaster/serve/untrack.py": (1, "private_directory for serve/init (journals, hook-free dir); never opens the store"),
    "taskmaster/server_fence.py": (2, "the lease and host-id directory themselves"),
    "taskmaster/localserve.py": (2, "local server mode: a read-only mode=ro admission probe (is_native_store) and the owner-only serve/ dir for local.json; a local server refuses marker/latch roots first (server_mode_reason)"),
    "hooks/merge_recorder_stamp.py": (3, "read-only hook reads and its stamp queue; unchanged by 009 (D7)"),
    "evals/agent_tool_use/check.py": (1, _BENCH),
    "evals/agent_tool_use/seed.py": (3, _BENCH),
    "scripts/benchmark_native_core.py": (3, _BENCH),
    "scripts/benchmark_performance_audit.py": (2, _BENCH),
    "scripts/benchmark_store.py": (3, _BENCH),
    "scripts/native_backfill_rehearsal.py": (3, _BENCH),
    "scripts/native_n08_parity.py": (2, _BENCH),
    "scripts/native_n09_journeys.py": (1, _BENCH),
    "scripts/native_n10_viewer_bench.py": (1, _BENCH),
    "scripts/native_n11_progress_parity.py": (1, _BENCH),
    "scripts/native_n13_rehearsal.py": (1, _BENCH),
    "scripts/native_n14_rehearsal.py": (2, _BENCH),
    "scripts/native_n15_rehearsal.py": (3, _BENCH),
    "scripts/native_n16_acceptance.py": (8, _BENCH),
    "scripts/native_n16_perf.py": (3, _BENCH),
    "scripts/native_synthetic_dataset.py": (1, _BENCH),
    "scripts/reproduce_fts_integrity.py": (4, _BENCH),
    "scripts/viewer_fixture_server.py": (1, _BENCH),
}


def test_ratchet_every_store_opening_call_site_is_accounted_for():
    base = Path(__file__).resolve().parents[1]
    found: dict[str, int] = {}
    for top in ("taskmaster", "hooks", "scripts", "evals"):
        for path in (base / top).rglob("*.py"):
            count = sum(1 for line in path.read_text(encoding="utf-8").splitlines()
                        if _SITE.search(line) and not _SKIP_LINE.search(line))
            if count:
                found[path.relative_to(base).as_posix()] = count
    unlisted = {name: n for name, n in found.items() if name not in _ALLOWED}
    grown = {name: (n, _ALLOWED[name][0]) for name, n in found.items() if name in _ALLOWED and n > _ALLOWED[name][0]}
    assert not unlisted, f"new store-opening call sites need a fence choke point or an exemption here: {unlisted}"
    assert not grown, f"more call sites than allowed (found, allowed): {grown}"
