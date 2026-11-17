# User intent: claims belong to a session, a person may adopt their own sessions that are gone, and a remote session can never be judged by the host's process table.
from __future__ import annotations

import random
import socket

import pytest

from taskmaster import team_identity
from taskmaster.native import blockers, claims

A_OLD, A_NEW, A_LIVE = "ann@" + "a" * 16, "ann@" + "b" * 16, "ann@" + "c" * 16
BOB = "bob@" + "d" * 16
HOST = f"{socket.gethostname()}-1234-deadbeef"


@pytest.fixture
def live(monkeypatch):
    """The set of remote sessions whose connection is open; drives claims.CONNECTED."""
    sessions: set[str] = set()
    monkeypatch.setattr(claims, "CONNECTED", lambda session: session in sessions)
    return sessions


@pytest.mark.parametrize("session, handle", [
    ("ann@" + "a" * 16, "ann"), ("box-1234@" + "0" * 16, "box-1234"), ("ann@viewer", "ann")])
def test_remote_handle_parses_the_remote_shape(session, handle):
    assert claims.remote_handle(session) == handle


@pytest.mark.parametrize("session", [
    HOST, "ann-1234-deadbeef", "Ann@" + "a" * 16, "ann@" + "a" * 15, "ann@" + "A" * 16, "ann@other",
    "@" + "a" * 16, "coordinator-identity", "explicit-sync", "sync-" + "a" * 64, "git:x", "", None, 5,
    "ann@" + "a" * 16 + "\n"])
def test_remote_handle_rejects_every_other_shape(session):
    assert claims.remote_handle(session) is None


def test_handle_pattern_is_team_identitys():
    assert claims.REMOTE_HANDLE == team_identity.HANDLE_RE.pattern


def test_remote_ids_never_match_session_shape():
    rng = random.Random(3)
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    for _ in range(200):
        head = rng.choice(alphabet) + "".join(rng.choice(alphabet + "-") for _ in range(rng.randint(0, 12)))
        handle = head.rstrip("-") + f"-{rng.randint(1, 99999)}" if rng.random() < 0.5 else head.rstrip("-") or "x"
        handle = handle[:32].rstrip("-")
        assert team_identity.HANDLE_RE.fullmatch(handle), handle
        session = f"{handle}@{rng.getrandbits(64):016x}"
        assert claims.SESSION_SHAPE.fullmatch(session) is None, session
        assert claims.identity(session) is None, session
        assert claims.remote_handle(session) == handle


def test_remote_holder_is_never_judged_by_host_pids(monkeypatch):
    # A handle shaped like this host's name and a pid, plus a sessions row that names this host:
    # without the remote-first rule the row path would ask the OS about pid 1.
    holder = "box-1@" + "a" * 16

    def boom(*_):
        raise AssertionError("a remote holder was judged by the host's process table")

    row = {"session": holder, "pid": 1, "host": "box", "last_seen": ""}
    for hook, expected in ((None, None), (lambda s: True, True), (lambda s: False, None)):
        monkeypatch.setattr(claims, "CONNECTED", hook)
        assert claims.liveness(holder, row, hostname="box", pid_alive=boom) is expected


@pytest.mark.parametrize("holder, session, open_, expected", [
    (A_OLD, A_NEW, set(), True),              # same person, connection gone: theirs to adopt
    (A_LIVE, A_NEW, {A_LIVE}, False),         # same person, still open: protected
    (BOB, A_NEW, set(), False),               # another person
    (A_NEW, A_NEW, set(), False),             # the session itself is not "adoption"
    (HOST, A_NEW, set(), False),              # host-local holder
    (A_OLD, HOST, set(), False),              # host-local session
    (A_OLD, "ann@viewer", set(), True),       # the person's viewer
    (A_LIVE, "ann@viewer", {A_LIVE}, True),   # the viewer sees live sessions as its own too
    (A_OLD, "bob@viewer", set(), False),
    ("ann@viewer", A_NEW, set(), True),       # a channel never counts as connected
    ("ann@viewer", BOB, set(), False),
])
def test_adoptable_needs_same_handle_and_a_gone_holder(live, holder, session, open_, expected):
    live.update(open_)
    assert claims.adoptable(holder, session) is expected


def test_connected_is_false_without_a_server_and_for_channels(monkeypatch):
    monkeypatch.setattr(claims, "CONNECTED", None)
    assert claims.connected(A_OLD) is False
    monkeypatch.setattr(claims, "CONNECTED", lambda s: True)
    assert claims.connected(A_OLD) is True
    assert claims.connected("ann@viewer") is False
    assert claims.connected(HOST) is False


@pytest.mark.parametrize("holder, open_, mine", [
    (A_NEW, set(), True), (A_OLD, set(), True), (A_LIVE, {A_LIVE}, False), (BOB, set(), False)])
def test_foreign_holder_and_mine_follow_adoption(live, holder, open_, mine):
    live.update(open_)
    task = {"id": "t-1", "status": "in-progress", "locked_by": holder}
    assert (claims.foreign_holder(task, A_NEW) == "") is mine
    state = claims.read(task, task_id="t-1", session=A_NEW, connection=None)
    assert state.mine is mine
    assert state.as_blocker().mine is mine
    found = blockers._claim_blockers(state.as_blocker(), A_NEW, "t-1")
    assert (found == []) is (mine or state.expired)


# ── Connection-bound claims (team-server-010 D4) ───────────────────────────────

def test_connected_remote_holder_never_expires_by_its_stamp(live):
    live.add(A_LIVE)
    task = {"id": "t-1", "status": "in-progress", "locked_by": A_LIVE, "claim_expires": "2000-01-01T00:00:00Z",
            "claim_expires_for": A_LIVE}
    state = claims.read(task, task_id="t-1", session=BOB, connection=None)
    assert state.live is True and state.expired is False


def test_disconnected_remote_holder_expires_by_its_stamp(live):
    task = {"id": "t-1", "status": "in-progress", "locked_by": A_OLD, "claim_expires": "2000-01-01T00:00:00Z",
            "claim_expires_for": A_OLD}
    assert claims.read(task, task_id="t-1", session=BOB, connection=None).expired is True


def test_a_live_host_holder_still_expires_by_its_stamp(monkeypatch):
    monkeypatch.setattr(claims, "CONNECTED", None)
    monkeypatch.setattr(claims, "_local", lambda name: {"_local_host": socket.gethostname,
                                                         "_local_pid_alive": lambda pid: True}[name])
    task = {"id": "t-1", "status": "in-progress", "locked_by": HOST, "claim_expires": "2000-01-01T00:00:00Z",
            "claim_expires_for": HOST}
    state = claims.read(task, task_id="t-1", session=BOB, connection=None)
    assert state.live is True and state.expired is True


def test_keeper_ttl_constants():
    assert claims.DISCONNECTED_TTL_SECONDS == 900 and claims.RENEW_BELOW_SECONDS == 3600
    for value in (claims.DISCONNECTED_TTL_SECONDS, claims.RENEW_BELOW_SECONDS):
        assert claims.MIN_TTL_SECONDS <= value <= claims.MAX_TTL_SECONDS


# ── Solo mode is unchanged: fixed expectations, computed on the base commit ────

LOCAL, PEER_LOCAL = f"{socket.gethostname()}-111-aaaaaaaa", f"{socket.gethostname()}-222-bbbbbbbb"
REMOTE_BOX = "other-box-333-cccccccc"
FUTURE, PAST = "2999-01-01T00:00:00Z", "2000-01-01T00:00:00Z"
SOLO_CASES = [
    # (task fields, session) -> (foreign_holder, mine, live, expired, survives a move to blocked)
    ({"status": "in-progress", "locked_by": LOCAL}, LOCAL, ("", True, True, False, False)),
    ({"status": "in-progress", "locked_by": PEER_LOCAL}, LOCAL, (PEER_LOCAL, False, True, False, True)),
    ({"status": "in-progress", "locked_by": REMOTE_BOX}, LOCAL, (REMOTE_BOX, False, None, False, True)),
    ({"status": "in-progress", "locked_by": REMOTE_BOX, "claim_expires": PAST, "claim_expires_for": REMOTE_BOX},
     LOCAL, (REMOTE_BOX, False, None, True, False)),
    ({"status": "in-progress", "locked_by": REMOTE_BOX, "claim_expires": FUTURE, "claim_expires_for": "x"},
     LOCAL, (REMOTE_BOX, False, None, False, True)),
    ({"status": "done", "locked_by": PEER_LOCAL}, LOCAL, (PEER_LOCAL, False, True, True, True)),
    ({"status": "todo"}, LOCAL, ("", False, None, False, False)),
    ({"status": "in-progress", "locked_by": "sess-peer"}, "sess-alpha", ("sess-peer", False, None, False, True)),
]


@pytest.mark.parametrize("fields, session, expected", SOLO_CASES)
def test_solo_behaviour_is_identical(monkeypatch, fields, session, expected):
    monkeypatch.setattr(claims, "CONNECTED", None, raising=False)
    monkeypatch.setattr(claims, "_local", lambda name: {
        "_local_host": socket.gethostname, "_local_pid_alive": lambda pid: pid in (111, 222)}[name])
    task = dict(fields, id="t-1")
    state = claims.read(task, task_id="t-1", session=session, connection=None)
    moved = dict(task, status="blocked")
    got = (claims.foreign_holder(task, session), state.mine, state.live, state.expired,
           claims.survives_status_change(moved, session=session, connection=None))
    assert got == expected
    assert (claims.blocked_by([state], release=False) is None) is (state.mine or not state.holder)


def test_the_hook_channel_is_a_never_connected_pseudo_session():
    """team-server-008: a teammate's hook write runs as `<handle>@hook`, like the read and viewer channels."""
    assert claims.remote_handle("ann@hook") == "ann"
    assert "hook" in claims.CHANNELS
    assert claims.connected("ann@hook") is False
