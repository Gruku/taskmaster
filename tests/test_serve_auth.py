# User intent: the operator token is owner-only, compared in constant time, rotatable without a restart, and never leaks into the access token.
import asyncio
import logging
import os
import threading

import pytest

from taskmaster.coordinator import ownership
from taskmaster.serve import auth
from taskmaster.serve.auth import (
    OPERATOR, OperatorTokenAuthenticator, Principal, ServeTokenVerifier, TOKEN_RE, ensure_operator_token,
    principal_from_access_token, read_operator_token, rotate_operator_token, serve_directory, token_path,
)
from taskmaster.serve.config import ServeRefused


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".taskmaster" / "local").mkdir(parents=True)
    return tmp_path


def bump(path):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns + 1_000_000, st.st_mtime_ns + 1_000_000))


def test_ensure_creates_an_owner_only_token(project):
    token, created = ensure_operator_token(project)
    assert created is True and token.startswith("tm_op_") and TOKEN_RE.fullmatch(token)
    ownership.verify_private(token_path(project))
    if os.name != "nt":
        assert token_path(project).stat().st_mode & 0o777 == 0o600
        assert serve_directory(project).stat().st_mode & 0o777 == 0o700
    assert not list(serve_directory(project).glob("*.tmp"))


def test_ensure_is_idempotent(project):
    token, _ = ensure_operator_token(project)
    assert ensure_operator_token(project) == (token, False)


def test_rotate_replaces_the_token(project):
    token, _ = ensure_operator_token(project)
    new = rotate_operator_token(project)
    assert new != token and read_operator_token(token_path(project)) == new


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_loose_permissions_refuse(project):
    ensure_operator_token(project)
    token_path(project).chmod(0o644)
    with pytest.raises(ServeRefused, match="owner-only"):
        read_operator_token(token_path(project))


def test_unverifiable_permissions_refuse(project, monkeypatch):
    ensure_operator_token(project)

    def boom(path):
        raise PermissionError("x")
    monkeypatch.setattr(auth.ownership, "verify_private", boom)
    with pytest.raises(ServeRefused, match="owner-only"):
        read_operator_token(token_path(project))


@pytest.mark.parametrize("content", ["", "short", "has space " * 8])
def test_malformed_token_refuses(project, content):
    ensure_operator_token(project)
    token_path(project).write_text(content)
    with pytest.raises(ServeRefused, match="--rotate"):
        read_operator_token(token_path(project))


def test_authenticator_accepts_only_the_current_token(project):
    token, _ = ensure_operator_token(project)
    authn = OperatorTokenAuthenticator(token_path(project))
    assert authn.authenticate(token) == OPERATOR
    for bad in ("", "wrong", token[:-1], token + "x", token.upper()):
        assert authn.authenticate(bad) is None


def test_authenticator_uses_constant_time_compare(project, monkeypatch):
    token, _ = ensure_operator_token(project)
    authn = OperatorTokenAuthenticator(token_path(project))
    calls = []
    real = auth.hmac.compare_digest

    def rec(a, b):
        calls.append((a, b))
        return real(a, b)
    monkeypatch.setattr(auth.hmac, "compare_digest", rec)
    authn.authenticate(token)
    assert len(calls) == 1 and all(len(x) == 32 for x in calls[0])


def test_rotation_takes_effect_without_restart(project):
    token, _ = ensure_operator_token(project)
    authn = OperatorTokenAuthenticator(token_path(project))
    assert authn.authenticate(token) == OPERATOR
    new = rotate_operator_token(project)
    bump(token_path(project))
    assert authn.authenticate(token) is None
    assert authn.authenticate(new) == OPERATOR


def test_authenticator_fails_closed_when_the_file_breaks(project, caplog):
    token, _ = ensure_operator_token(project)
    authn = OperatorTokenAuthenticator(token_path(project))
    assert authn.authenticate(token) == OPERATOR
    token_path(project).write_text("bad")
    bump(token_path(project))
    with caplog.at_level(logging.ERROR, logger="taskmaster.serve"):
        assert authn.authenticate(token) is None
        assert authn.authenticate("bad") is None
    errors = [r for r in caplog.records if r.levelno == logging.ERROR and r.name == "taskmaster.serve"]
    assert len(errors) == 1 and token not in errors[0].getMessage()


@pytest.mark.parametrize("where", ["read", "stat"])
def test_a_transient_os_error_refuses_once_and_is_not_cached(project, monkeypatch, where):
    # A one-off EMFILE or sharing violation must not lock the operator out until the file changes.
    token, _ = ensure_operator_token(project)
    authn = OperatorTokenAuthenticator(token_path(project))
    bump(token_path(project))  # force a re-read on the next call
    failed = []
    if where == "read":
        real = auth.read_operator_token

        def flaky(path):
            if not failed:
                failed.append(1)
                raise OSError(24, "Too many open files")
            return real(path)
        monkeypatch.setattr(auth, "read_operator_token", flaky)
        assert authn.authenticate(token) is None
    else:
        target = str(token_path(project))
        real_stat = type(authn.path).stat

        def flaky_stat(self, *a, **kw):
            if not failed and str(self) == target:
                failed.append(1)
                raise OSError(32, "sharing violation")
            return real_stat(self, *a, **kw)
        monkeypatch.setattr(type(authn.path), "stat", flaky_stat)
        assert authn.authenticate(token) is None
        monkeypatch.undo()
    assert failed and authn.authenticate(token) == OPERATOR  # file unchanged, next request succeeds


def test_verifier_access_token_carries_the_principal_not_the_secret(project):
    token, _ = ensure_operator_token(project)
    verifier = ServeTokenVerifier(OperatorTokenAuthenticator(token_path(project)))
    access = asyncio.run(verifier.verify_token(token))
    assert access.client_id == "operator"
    assert access.token == "key:operator" and access.subject == "operator"
    assert access.claims == {"taskmaster_principal": "operator", "taskmaster_role": "operator",
                             "taskmaster_key_id": "operator"}
    assert token not in repr(access)
    assert asyncio.run(verifier.verify_token("nope")) is None


def test_principal_from_access_token(project):
    from fastmcp.server.auth import AccessToken
    token, _ = ensure_operator_token(project)
    verifier = ServeTokenVerifier(OperatorTokenAuthenticator(token_path(project)))
    access = asyncio.run(verifier.verify_token(token))
    assert principal_from_access_token(access) == Principal("operator", "operator", "operator")
    assert principal_from_access_token(None) is None
    assert principal_from_access_token(AccessToken(token="t", client_id="c", scopes=[])) is None


def test_concurrent_ensure_yields_one_token(project):
    barrier = threading.Barrier(8)
    results = []

    def go():
        barrier.wait()
        results.append(ensure_operator_token(project))
    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert len(results) == 8 and len({t for t, _ in results}) == 1
    assert sum(1 for _, c in results if c) == 1
    assert not list(serve_directory(project).glob("*.tmp"))


def test_ensure_never_replaces_an_existing_file(project, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("os.replace used by ensure")
    monkeypatch.setattr(auth.os, "replace", boom)
    token, created = ensure_operator_token(project)
    assert created and read_operator_token(token_path(project)) == token


def test_unsafe_serve_directory_refuses_with_exit_2(project, monkeypatch):
    def boom(root, name="coordinator"):
        raise PermissionError("not owner")
    monkeypatch.setattr(auth.ownership, "private_directory", boom)
    with pytest.raises(ServeRefused, match="not owner") as exc:
        serve_directory(project)
    assert exc.value.exit_code == 2


def test_private_directory_names(tmp_path):
    (tmp_path / ".taskmaster" / "local").mkdir(parents=True)
    assert ownership.private_directory(tmp_path, "serve").parts[-2:] == ("local", "serve")
    with pytest.raises(ValueError):
        ownership.private_directory(tmp_path, "../x")
