# User intent: a presented key resolves to its person by id with one constant-time compare, revocation applies from the next request, and the secret never reaches the access token.
from __future__ import annotations

import asyncio
import errno
import hashlib
import logging
import os
import pathlib
import re
import threading

import pytest

from taskmaster.serve import app as app_module
from taskmaster.serve import auth, principals
from taskmaster.serve.auth import (OPERATOR, KeyAuthenticator, OperatorTokenAuthenticator, Principal, ServeTokenVerifier,
                                   current_key_id, ensure_operator_token, is_host_local, principal_from_access_token,
                                   request_key_id, token_path, viewer_principal)
from taskmaster.serve.principals import (KEY_RE, PrincipalRegistry, RegistryUnavailable, add_user, principals_path,
                                         revoke_user, rotate_user)


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".taskmaster" / "local").mkdir(parents=True)
    ensure_operator_token(tmp_path)
    return tmp_path


def build(project):
    registry = PrincipalRegistry(principals_path(project))
    op = OperatorTokenAuthenticator(token_path(project))
    return KeyAuthenticator(op, registry), registry, op


def operator_token(project):
    return ensure_operator_token(project)[0]


def test_a_minted_key_authenticates_as_its_person(project):
    key, record, _ = add_user(project, "ann")
    chief, chief_record, _ = add_user(project, "chief", role="operator")
    authn, _, _ = build(project)
    assert authn.authenticate(key) == Principal("ann", "member", record.key_id)
    assert authn.authenticate(chief) == Principal("chief", "operator", chief_record.key_id)


def test_an_unknown_key_is_refused(project):
    key, record, _ = add_user(project, "ann")
    authn, _, _ = build(project)
    secret = KEY_RE.fullmatch(key).group(2)
    other = "tm_u_" + "f" * 16 + "_" + secret
    wrong = f"tm_u_{record.key_id}_" + ("A" if secret[0] != "A" else "B") + secret[1:]
    changed = key[:-1] + ("A" if key[-1] != "A" else "B")
    for token in (other, wrong, changed, key + "x", key[:-1], "", "tm_u_", key.upper()):
        assert authn.authenticate(token) is None, token


def test_the_operator_token_still_authenticates(project, monkeypatch):
    key, _, _ = add_user(project, "ann")
    authn, _, op = build(project)
    assert authn.authenticate(operator_token(project)) == OPERATOR
    calls = []
    real = op.authenticate
    monkeypatch.setattr(op, "authenticate", lambda t: (calls.append(t), real(t))[1])
    assert authn.authenticate(key).handle == "ann"
    assert calls == []


def test_lookup_is_by_id_with_one_constant_time_compare(project, monkeypatch):
    keys = {}
    for i in range(50):
        keys[i] = add_user(project, f"user{i}")
    authn, registry, _ = build(project)
    first = keys[0][0]
    assert authn.authenticate(first) is not None

    class Opaque(dict):
        def __iter__(self):
            raise AssertionError("iterated the registry")

        def values(self):
            raise AssertionError("scanned the registry")

        def items(self):
            raise AssertionError("scanned the registry")
    registry._records = Opaque(registry._records)
    calls = []
    real = auth.hmac.compare_digest
    monkeypatch.setattr(auth.hmac, "compare_digest", lambda a, b: (calls.append((a, b)), real(a, b))[1])
    revoked_key, revoked_record, _ = keys[1]
    # a revoked record is simulated by a non-active replacement under the same id
    registry._records[revoked_record.key_id] = principals.Record(
        revoked_record.key_id, revoked_record.handle, "member", "x", (), revoked_record.created, None,
        "2026-11-01T00:00:00Z", "revoked")
    unknown = "tm_u_" + "e" * 16 + "_" + "A" * 43
    for token, ok in ((first, True), (unknown, False), (revoked_key, False)):
        calls.clear()
        assert (authn.authenticate(token) is not None) is ok
        assert len(calls) == 1 and all(isinstance(x, bytes) and len(x) == 32 for x in calls[0])


def test_a_revoked_key_is_refused_from_the_next_call(project):
    key, _, _ = add_user(project, "ann")
    authn, _, _ = build(project)
    assert authn.authenticate(key) is not None
    revoke_user(project, "ann")
    assert authn.authenticate(key) is None
    key2, _, _ = add_user(project, "ann")
    assert authn.authenticate(key2) is not None
    new_key, _, _ = rotate_user(project, "ann")
    assert authn.authenticate(key2) is None and authn.authenticate(new_key) is not None


def test_registry_fails_closed_and_keeps_the_operator(project, caplog):
    key, _, _ = add_user(project, "ann")
    authn, _, _ = build(project)
    assert authn.authenticate(key) is not None
    path = principals_path(project)
    path.write_bytes(b"{")
    stamp = path.stat().st_mtime_ns + 5_000_000_000
    os.utime(path, ns=(stamp, stamp))
    with caplog.at_level(logging.DEBUG, logger="taskmaster.serve"):
        assert authn.authenticate(key) is None
        assert authn.authenticate(key) is None
        assert authn.authenticate(operator_token(project)) == OPERATOR
    errors = [r for r in caplog.records if r.levelno == logging.ERROR and "refusing every member key" in r.getMessage()]
    assert len(errors) == 1
    # repair
    path.unlink()
    key2, _, _ = add_user(project, "ann")
    assert authn.authenticate(key2) is not None


def _stat_key(path):
    info = os.stat(path)
    return info.st_mtime_ns, info.st_size, info.st_ino


def test_a_transient_read_error_is_not_cached(project, caplog, monkeypatch):
    ann_key, ann, _ = add_user(project, "ann")
    add_user(project, "bob")
    authn, registry, _ = build(project)
    assert authn.authenticate(ann_key) is not None
    path = principals_path(project)

    def fail_once(exc):
        state = {"left": 1}
        real = principals.load

        def load(p):
            if state["left"]:
                state["left"] -= 1
                raise exc
            return real(p)
        return load

    revoke_user(project, "bob")
    monkeypatch.setattr(principals, "load", fail_once(OSError(errno.EMFILE, "Too many open files")))
    with caplog.at_level(logging.DEBUG, logger="taskmaster.serve"):
        assert authn.authenticate(ann_key) is None
        before = _stat_key(path)
        assert authn.authenticate(ann_key) is not None
        assert _stat_key(path) == before
    messages = [r.getMessage() for r in caplog.records]
    assert any(r.levelno == logging.WARNING and "could not be read" in r.getMessage() for r in caplog.records)
    assert f"refused key {ann.key_id}: key list unavailable" in messages
    monkeypatch.undo()

    # the Windows sharing-violation shape: the bytes cannot be read
    add_user(project, "cy")
    real_read = pathlib.Path.read_bytes
    state = {"left": 1}

    def read_bytes(self):
        if state["left"] and self.name == principals.PRINCIPALS_FILE:
            state["left"] -= 1
            raise PermissionError(13, "sharing violation")
        return real_read(self)
    monkeypatch.setattr(pathlib.Path, "read_bytes", read_bytes)
    assert authn.authenticate(ann_key) is None
    assert authn.authenticate(ann_key) is not None
    monkeypatch.undo()

    add_user(project, "dee")
    real_stat = os.stat
    with monkeypatch.context() as m:
        def stat(p, *a, **k):
            if pathlib.Path(p).name == principals.PRINCIPALS_FILE:
                raise PermissionError(13, "denied")
            return real_stat(p, *a, **k)
        m.setattr(principals.os, "stat", stat)
        with pytest.raises(RegistryUnavailable):
            registry.active_ids()
    assert ann.key_id in registry.active_ids()


def test_a_transient_error_keeps_the_previous_records(project, monkeypatch):
    ann_key, ann, _ = add_user(project, "ann")
    add_user(project, "bob")
    authn, registry, _ = build(project)
    assert authn.authenticate(ann_key) is not None
    old = dict(registry._records)
    real = principals.load
    monkeypatch.setattr(principals, "load", lambda p: (_ for _ in ()).throw(OSError(errno.EIO, "io")))
    # change the file by writing directly: the mutators call load() too
    records = real(principals_path(project))
    bob = next(r for r in records.values() if r.handle == "bob")
    records[bob.key_id] = principals.Record(bob.key_id, "bob", "member", "bob", (), bob.created, None,
                                            "2026-11-01T00:00:00Z", "revoked")
    principals.write(principals_path(project), records)
    for _ in range(3):
        with pytest.raises(RegistryUnavailable):
            registry.lookup(ann.key_id)
        assert authn.authenticate(ann_key) is None
    assert registry._records == old
    monkeypatch.undo()
    assert authn.authenticate(ann_key) is not None
    assert not registry._records[bob.key_id].active


def test_registry_without_a_file_is_empty(project, caplog):
    with caplog.at_level(logging.DEBUG, logger="taskmaster.serve"):
        assert PrincipalRegistry(principals_path(project)).active_ids() == frozenset()
        assert PrincipalRegistry(None).active_ids() == frozenset()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_refusals_and_first_use_are_logged_by_key_id_only(project, caplog):
    key, record, _ = add_user(project, "ann")
    key_b, rec_b, _ = add_user(project, "bob")
    revoke_user(project, "bob")
    authn, _, _ = build(project)
    secret = KEY_RE.fullmatch(key).group(2)
    wrong = f"tm_u_{record.key_id}_" + ("A" if secret[0] != "A" else "B") + secret[1:]
    unknown = "tm_u_" + "d" * 16 + "_" + "A" * 43
    with caplog.at_level(logging.DEBUG, logger="taskmaster.serve"):
        authn.authenticate(key)
        authn.authenticate(key)
        authn.authenticate(wrong)
        authn.authenticate(key_b)
        authn.authenticate(unknown)
    messages = [r.getMessage() for r in caplog.records]
    assert messages.count(f"key {record.key_id} (ann) authenticated") == 1
    assert f"refused key {record.key_id}: wrong secret" in messages
    assert f"refused key {rec_b.key_id}: revoked" in messages
    assert f"refused key {'d' * 16}: unknown" in messages
    text = "\n".join(messages)
    for secret_text in (key, secret, hashlib.sha256(key.encode()).hexdigest(), wrong):
        assert secret_text not in text


def test_logged_request_text_goes_through_log_safe(project, caplog, monkeypatch):
    key, record, _ = add_user(project, "ann")
    authn, _, _ = build(project)
    seen = []
    real = app_module.log_safe
    monkeypatch.setattr(app_module, "log_safe", lambda v, *a, **k: (seen.append(v), real(v, *a, **k))[1])
    secret = KEY_RE.fullmatch(key).group(2)
    wrong = f"tm_u_{record.key_id}_" + ("A" if secret[0] != "A" else "B") + secret[1:]
    with caplog.at_level(logging.DEBUG, logger="taskmaster.serve"):
        authn.authenticate(key)
        authn.authenticate(wrong)
    assert record.key_id in seen and "ann" in seen
    assert all("\r" not in r.getMessage() and "\n" not in r.getMessage() for r in caplog.records)


def test_verifier_access_token_names_the_key_not_the_secret(project):
    key, record, _ = add_user(project, "ann")
    authn, _, _ = build(project)
    verifier = ServeTokenVerifier(authn)
    access = asyncio.run(verifier.verify_token(key))
    assert access.token == f"key:{record.key_id}" and access.client_id == "ann" and access.subject == record.key_id
    assert access.claims == {"taskmaster_principal": "ann", "taskmaster_role": "member",
                             "taskmaster_key_id": record.key_id}
    secret = KEY_RE.fullmatch(key).group(2)
    for text in (repr(access), access.model_dump_json()):
        assert key not in text and secret not in text
    assert asyncio.run(verifier.verify_token("nope")) is None


def test_subject_is_the_key_id_in_a_stable_form(project):
    key, record, _ = add_user(project, "ann")

    def subject(authn, token):
        return asyncio.run(ServeTokenVerifier(authn).verify_token(token)).subject
    authn, _, _ = build(project)
    first = subject(authn, key)
    assert re.fullmatch(r"[0-9a-f]{16}", first) and first == record.key_id
    assert subject(authn, key) == first
    assert subject(build(project)[0], key) == first
    add_user(project, "bob")
    assert subject(authn, key) == first
    new_key, new, _ = rotate_user(project, "ann")
    assert subject(authn, new_key) == new.key_id != first
    assert subject(authn, operator_token(project)) == "operator"


def scope(client=("127.0.0.1", 5), headers=((b"host", b"127.0.0.1:7470"),)):
    value = {"type": "http", "headers": list(headers)}
    if client is not None:
        value["client"] = client
    return value


FWD = ((b"host", b"127.0.0.1:7470"),)


@pytest.mark.parametrize("given,expected", [
    (scope(), True),
    (scope(("::1", 5), ((b"host", b"[::1]:7470"),)), True),
    (scope(("127.0.0.1", 5), ((b"host", b"localhost"),)), True),
    (scope(("127.0.0.1", 5), ((b"host", b"LOCALHOST:7470"),)), True),
    (scope(("203.0.113.9", 5)), False),
    (scope(None), False),
    (scope(("not-an-ip", 5)), False),
    (scope(headers=((b"host", b"tm.example.com"),)), False),
    (scope(headers=()), False),
    (scope(headers=((b"host", b"127.0.0.1"), (b"host", b"127.0.0.1"))), False),
    *[(scope(headers=FWD + ((name, value),)), False)
      for name in (b"x-forwarded-for", b"forwarded", b"x-real-ip", b"X-Forwarded-For", b"FORWARDED", b"X-Real-IP")
      for value in (b"203.0.113.9", b"")]])
def test_is_host_local(given, expected):
    assert is_host_local(given) is expected


def test_is_host_local_never_raises_on_malformed_input():
    for bad in ({}, {"client": 5}, {"client": ("1.1.1.1",), "headers": [(b"host",)]}, {"headers": None}, scope(("::1", 1), ((b"host", b"[::1"),))):
        assert is_host_local(bad) is False


def test_principal_from_access_token_reads_three_claims():
    from fastmcp.server.auth import AccessToken
    claims = {"taskmaster_principal": "ann", "taskmaster_role": "member", "taskmaster_key_id": "0123456789abcdef"}
    assert principal_from_access_token(AccessToken(token="t", client_id="ann", scopes=[], claims=claims)) \
        == Principal("ann", "member", "0123456789abcdef")
    two_op = {"taskmaster_principal": "operator", "taskmaster_role": "operator"}
    assert principal_from_access_token(AccessToken(token="t", client_id="c", scopes=[], claims=two_op)) == OPERATOR
    two_ann = {"taskmaster_principal": "ann", "taskmaster_role": "member"}
    assert principal_from_access_token(AccessToken(token="t", client_id="c", scopes=[], claims=two_ann)) is None


def test_current_key_id_outside_a_request_is_none():
    out = []
    thread = threading.Thread(target=lambda: out.append(current_key_id()))
    thread.start()
    thread.join(10)
    assert out == [None]


def test_request_key_id_chains_the_per_request_sources():
    ann, bob = "a" * 16, "b" * 16
    assert request_key_id() is None
    with viewer_principal(ann):
        assert request_key_id() == ann
        with viewer_principal(bob):
            assert request_key_id() == bob
        assert request_key_id() == ann
    assert request_key_id() is None
    with pytest.raises(ValueError):
        with viewer_principal("bad"):
            pass
    barrier, seen = threading.Barrier(2), {}

    def work(name, key_id):
        with viewer_principal(key_id):
            barrier.wait(10)
            seen[name] = request_key_id()
            barrier.wait(10)
    threads = [threading.Thread(target=work, args=("a", ann)), threading.Thread(target=work, args=("b", bob))]
    [t.start() for t in threads]
    [t.join(20) for t in threads]
    assert seen == {"a": ann, "b": bob}
