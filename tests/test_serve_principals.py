# User intent: per-person keys are minted once, stored only as owner-only hashes, and rotated or revoked by the operator on the host.
from __future__ import annotations

import hashlib
import os
import stat
import threading

import pytest

from taskmaster.coordinator import ownership
from taskmaster.native import profiles
from taskmaster.serve import principals
from taskmaster.serve.principals import (KEY_RE, PrincipalsBusy, PrincipalsRefused, StoreCheck, add_user, inspect_store,
                                         list_users, load, locked, mint_key, principals_path, revoke_user, rotate_user)
from taskmaster.team_identity import RESERVED_HANDLES, choose_handle
from test_native_service_build import root  # noqa: F401

POSIX = os.name != "nt"


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".taskmaster" / "local").mkdir(parents=True)
    return tmp_path


def file_bytes(project):
    return principals_path(project).read_bytes()


def test_operator_is_a_reserved_handle():
    assert "operator" in RESERVED_HANDLES
    with pytest.raises(ValueError):
        profiles.validate("profile.create", {"handle": "operator", "name": "Op Erator", "emails": [],
                                              "created": "2000-01-01T00:00:00Z"})
    assert choose_handle("operator", "", {})[0] != "operator"


def test_minted_key_has_the_documented_shape():
    keys = set()
    for _ in range(50):
        key, key_id = mint_key(set())
        match = KEY_RE.fullmatch(key)
        assert match and len(key) == 65 and match.group(1) == key_id
        keys.add(key)
    assert len(keys) == 50


def test_mint_redraws_a_taken_id(monkeypatch):
    draws = iter(["a" * 16, "b" * 16])
    monkeypatch.setattr(principals.secrets, "token_hex", lambda n: next(draws))
    assert mint_key({"a" * 16})[1] == "b" * 16


def test_add_user_stores_only_the_hash(project):
    key, record, reinstated = add_user(project, "ann", name="Ann Lee", emails=["ann@example.com"])
    assert reinstated is False and record.active
    assert record.sha256 == hashlib.sha256(key.encode()).hexdigest()
    raw = file_bytes(project)
    secret = KEY_RE.fullmatch(key).group(2).encode()
    assert key.encode() not in raw and secret not in raw and record.sha256.encode() in raw
    assert load(principals_path(project))[record.key_id] == record


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_principals_file_is_never_readable_by_others(project):
    path = principals_path(project)

    def check():
        ownership.verify_private(path)
        if POSIX:
            assert _mode(path) == 0o600
            assert _mode(path.with_name(principals.LOCK_FILE)) == 0o600
            assert _mode(path.parent) == 0o700
        assert not list(path.parent.glob("*.tmp"))

    old = os.umask(0o022) if POSIX else None
    try:
        add_user(project, "ann")
        check()
        rotate_user(project, "ann")
        check()
        revoke_user(project, "ann")
        check()
    finally:
        if old is not None:
            os.umask(old)


@pytest.mark.skipif(not POSIX, reason="chmod modes")
def test_loose_permissions_refuse(project):
    add_user(project, "ann")
    path = principals_path(project)
    path.chmod(0o644)
    with pytest.raises(PrincipalsRefused, match="owner-only") as exc:
        load(path)
    assert exc.value.exit_code == 2
    before = path.read_bytes()
    with pytest.raises(PrincipalsRefused, match="owner-only"):
        add_user(project, "bob")
    assert path.read_bytes() == before


def test_unverifiable_permissions_refuse(project, monkeypatch):
    add_user(project, "ann")

    def deny(path):
        raise PermissionError("x")
    monkeypatch.setattr(principals.ownership, "verify_private", deny)
    with pytest.raises(PrincipalsRefused, match="owner-only"):
        load(principals_path(project))


def test_a_link_in_place_of_the_file_refuses(project, tmp_path_factory):
    add_user(project, "ann")
    path = principals_path(project)
    other = tmp_path_factory.mktemp("elsewhere") / "p.json"
    other.write_bytes(path.read_bytes())
    if POSIX:
        other.chmod(0o600)
    path.unlink()
    try:
        os.symlink(other, path)
    except OSError:
        pytest.skip("symlinks need a privilege here")
    with pytest.raises(PrincipalsRefused):
        load(path)


def _valid_doc(**over):
    record = {"key_id": "0123456789abcdef", "handle": "ann", "role": "member", "name": "ann", "emails": [],
              "created": "2026-10-31T12:00:00Z", "sha256": "a" * 64, "revoked": None, "revoked_reason": None}
    record.update(over)
    return record


def _doc(*records, version=1):
    import json
    return json.dumps({"version": version, "keys": list(records)}).encode()


MALFORMED = {
    "not json": b"{",
    "version 2": _doc(_valid_doc(), version=2),
    "bad key id": _doc(_valid_doc(key_id="XYZ")),
    "duplicate key id": _doc(_valid_doc(), _valid_doc(handle="bob")),
    "reserved handle": _doc(_valid_doc(handle="taskmaster")),
    "role admin": _doc(_valid_doc(role="admin")),
    "active without hash": _doc(_valid_doc(sha256=None)),
    "revoked with hash": _doc(_valid_doc(revoked="2026-11-01T00:00:00Z", revoked_reason="revoked")),
    "two active for one handle": _doc(_valid_doc(), _valid_doc(key_id="fedcba9876543210")),
}


@pytest.mark.parametrize("raw", MALFORMED.values(), ids=MALFORMED.keys())
def test_malformed_file_refuses(project, raw):
    path = principals_path(project)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
    with pytest.raises(PrincipalsRefused, match="malformed") as exc:
        load(path)
    import re
    assert not re.search(r"[0-9a-f]{64}", str(exc.value))


def test_one_active_key_per_handle(project):
    add_user(project, "ann")
    before = file_bytes(project)
    with pytest.raises(PrincipalsRefused, match="rotate-user"):
        add_user(project, "ann")
    assert file_bytes(project) == before


@pytest.mark.parametrize("kwargs", [
    {"handle": "Ann"}, {"handle": "a_b"}, {"handle": "taskmaster"}, {"handle": "operator"}, {"handle": "admin"},
    {"handle": "ann", "name": "your name"}, {"handle": "ann", "emails": ["not-an-email"]},
    {"handle": "ann", "role": "root"}])
def test_bad_people_refuse(project, kwargs):
    with pytest.raises(PrincipalsRefused):
        add_user(project, **kwargs)
    assert not principals_path(project).exists()


def test_rotate_replaces_the_key_in_one_write(project):
    key1, first, _ = add_user(project, "ann", name="Ann Lee", emails=["ann@example.com"])
    key2, new, old = rotate_user(project, "ann")
    assert old.revoked_reason == "rotated" and old.sha256 is None
    assert (new.handle, new.role, new.name, new.emails) == ("ann", "member", "Ann Lee", ("ann@example.com",))
    assert new.key_id != first.key_id
    raw = file_bytes(project)
    assert key1.encode() not in raw and key2.encode() not in raw
    assert [r.handle for r in list_users(project) if r.active] == ["ann"]


def test_revoke_keeps_the_record_without_its_hash(project):
    _, record, _ = add_user(project, "ann")
    revoked = revoke_user(project, "ann")
    assert revoked.revoked_reason == "revoked" and revoked.sha256 is None
    assert [r.key_id for r in list_users(project)] == [record.key_id]


def test_revoke_and_rotate_need_an_active_key(project):
    add_user(project, "ann")
    revoke_user(project, "ann")
    for call in (revoke_user, rotate_user):
        for handle in ("ann", "nobody"):
            with pytest.raises(PrincipalsRefused):
                call(project, handle)


def test_add_after_revoke_reinstates(project):
    _, first, _ = add_user(project, "ann")
    revoke_user(project, "ann")
    _, second, reinstated = add_user(project, "ann")
    assert reinstated is True and second.key_id != first.key_id
    records = load(principals_path(project))
    assert not records[first.key_id].active and records[second.key_id].active


def test_concurrent_mutations_serialise(project):
    barrier, errors = threading.Barrier(8), []

    def work(i):
        try:
            barrier.wait()
            add_user(project, f"user{i}")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    assert not errors
    assert len(load(principals_path(project))) == 8


def test_held_lock_times_out_with_exit_69(project, monkeypatch):
    monkeypatch.setattr(principals, "LOCK_SECONDS", 0.2)
    held, release = threading.Event(), threading.Event()

    def hold():
        with locked(project):
            held.set()
            release.wait(30)
    thread = threading.Thread(target=hold)
    thread.start()
    assert held.wait(10)
    try:
        with pytest.raises(PrincipalsBusy) as exc:
            add_user(project, "ann")
        assert exc.value.exit_code == 69
    finally:
        release.set()
        thread.join(10)


def test_replace_contention_is_retried(project, monkeypatch):
    real, calls = os.replace, []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) <= 2:
            raise PermissionError("busy")
        return real(src, dst)
    monkeypatch.setattr(principals.os, "replace", flaky)
    monkeypatch.setattr(principals, "REPLACE_PAUSE", 0)
    add_user(project, "ann")
    assert len(calls) == 3
    monkeypatch.setattr(principals.os, "replace", lambda src, dst: (_ for _ in ()).throw(PermissionError("busy")))
    with pytest.raises(PrincipalsBusy):
        add_user(project, "bob")
    assert not list(principals_path(project).parent.glob("*.tmp"))


class _Spy:
    def __init__(self, inner):
        self.inner, self.closed = inner, False

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def close(self):
        self.closed = True
        self.inner.close()


def _spy_connect(monkeypatch):
    spies, real = [], principals.protocol.connect

    def connect(*args, **kwargs):
        spies.append(_Spy(real(*args, **kwargs)))
        return spies[-1]
    monkeypatch.setattr(principals.protocol, "connect", connect)
    return spies


def test_inspect_store(root, tmp_path_factory, monkeypatch):  # noqa: F811
    from taskmaster.coordinator import identity_gate
    spies = _spy_connect(monkeypatch)
    assert inspect_store(root, "ann") == StoreCheck("absent", None, False)
    monkeypatch.setattr(identity_gate, "team_mode_enabled", lambda root, *, project_doc=None: True)
    assert inspect_store(root, "ann").team_mode is True
    bare = tmp_path_factory.mktemp("bare")
    (bare / ".taskmaster" / "local").mkdir(parents=True)
    assert inspect_store(bare, "ann") == StoreCheck("unknown", None, None)
    assert spies and all(s.closed for s in spies)


def test_key_file_is_owner_only_and_exclusive(project, tmp_path_factory, monkeypatch):
    kf = tmp_path_factory.mktemp("keys") / "ann.key"
    old = os.umask(0o022) if POSIX else None
    try:
        key, record, _ = add_user(project, "ann", key_file=kf)
    finally:
        if old is not None:
            os.umask(old)
    assert kf.read_text() == key + "\n"
    ownership.verify_private(kf)
    if POSIX:
        assert _mode(kf) == 0o600
    before = file_bytes(project)
    with pytest.raises(PrincipalsRefused, match="already exists"):
        add_user(project, "bob", key_file=kf)
    assert file_bytes(project) == before and kf.read_text() == key + "\n"

    real_fsync = os.fsync
    kf2 = kf.with_name("bob.key")
    state = {"armed": True}

    def failing(fd):
        if state["armed"]:
            raise OSError("disk")
        return real_fsync(fd)
    monkeypatch.setattr(principals.os, "fsync", failing)
    with pytest.raises(PrincipalsRefused) as exc:
        add_user(project, "bob", key_file=kf2)
    assert not kf2.exists() and "tm_u_" not in str(exc.value)
    state["armed"] = False
    assert "bob" not in {r.handle for r in list_users(project)}
    state["armed"] = True
    kf3 = kf.with_name("ann2.key")
    with pytest.raises(PrincipalsRefused):
        rotate_user(project, "ann", key_file=kf3)
    state["armed"] = False
    assert load(principals_path(project))[record.key_id].active and not kf3.exists()


def test_lock_rewinds_before_locking(project, monkeypatch):
    events, real = [], os.lseek
    monkeypatch.setattr(principals.os, "lseek", lambda fd, pos, how: (events.append(("lseek", pos, how)), real(fd, pos, how))[1])
    if os.name == "nt":
        import msvcrt
        real_locking = msvcrt.locking
        monkeypatch.setattr(msvcrt, "locking", lambda fd, mode, n: (events.append(("lock", mode)), real_locking(fd, mode, n))[1])
    else:
        import fcntl
        real_flock = fcntl.flock
        monkeypatch.setattr(fcntl, "flock", lambda fd, op: (events.append(("lock", op)), real_flock(fd, op))[1])
    with locked(project):
        pass
    kinds = [e[0] for e in events]
    assert kinds == ["lseek", "lock", "lseek", "lock"]
    assert events[0][1:] == (0, os.SEEK_SET) and events[2][1:] == (0, os.SEEK_SET)


# -- team-server-027 D6: the pre-team owner, the cutoff and the wider reserved set --------------------------------

def _journal(project, **values):
    import json
    path = principals_path(project).parent / "init" / "init.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"stage": "done", **values}), encoding="utf-8")


@pytest.mark.parametrize("handle", ["system", "you", "assistant", "human", "anthropic"])
def test_027_added_reserved_handles_are_refused(project, handle):
    assert handle in RESERVED_HANDLES
    with pytest.raises(PrincipalsRefused):
        add_user(project, handle)


def test_027_pre_team_reads_cutoff_and_active_owner(project):
    assert principals.pre_team(project) == (None, None)             # no migration recorded
    _journal(project, pre_team_through_seq=42)
    assert principals.pre_team(project) == (None, 42)
    add_user(project, "ann")
    add_user(project, "bob")
    principals.set_pre_team_owner(project, "ann")
    assert principals.pre_team(project) == ("ann", 42)
    with pytest.raises(PrincipalsRefused, match="already owns"):
        principals.set_pre_team_owner(project, "bob")
    principals.set_pre_team_owner(project, "ann")                   # the holder again: fine
    revoke_user(project, "ann")
    assert principals.pre_team(project) == (None, 42)               # a revoked owner owns nothing


def test_027_set_owner_needs_a_migration_and_an_active_key(project):
    add_user(project, "ann")
    with pytest.raises(PrincipalsRefused, match="no recorded migration"):
        principals.set_pre_team_owner(project, "ann")
    _journal(project, pre_team_through_seq=1)
    with pytest.raises(PrincipalsRefused, match="no active key"):
        principals.set_pre_team_owner(project, "ghost")


def test_027_old_principals_file_without_the_flag_parses_unchanged(project):
    add_user(project, "ann")
    before = file_bytes(project)
    _journal(project, pre_team_through_seq=3)
    principals.set_pre_team_owner(project, "ann")
    assert file_bytes(project) == before                             # the owner lives in the init journal
    assert set(load(principals_path(project)))


def test_027_registry_loads_an_existing_newly_reserved_handle_with_a_warning(project, caplog):
    import json
    add_user(project, "ann")
    path = principals_path(project)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["keys"][0]["handle"] = "system"                  # a member named before 027 reserved the word
    path.write_text(json.dumps(doc), encoding="utf-8")
    registry = principals.PrincipalRegistry(path)
    with caplog.at_level("WARNING", logger="taskmaster.serve"):
        registry.refresh()
    assert any("system" in r.getMessage() and "reserved" in r.getMessage() for r in caplog.records)
    assert registry.active_ids()
    with pytest.raises(PrincipalsRefused):
        add_user(project, "system")


def test_027_set_user_verb_and_list_users(project, capsys):
    from taskmaster.serve import cli
    add_user(project, "ann")
    _journal(project, pre_team_through_seq=5)
    assert cli.main(["set-user", "ann", "--owns-pre-team-history", "--root", str(project)]) == 0
    assert "ann owns the pre-team history" in capsys.readouterr().out
    assert cli.main(["list-users", "--root", str(project)]) == 0
    assert "(owns pre-team history)" in capsys.readouterr().out
