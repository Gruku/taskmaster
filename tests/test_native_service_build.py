"""User intent: a client never has its commands run by a coordinator from a different
taskmaster build; a newer client retires an idle older owner gracefully, an older client
refuses. Builds are simulated by overriding the per-instance build identity."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from taskmaster import backlog_server as bs
from taskmaster.coordinator import client as client_module
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.ownership import OwnershipUnavailable, ownership_held
from taskmaster.coordinator.protocol import HandshakeError, ServiceUnavailable, build_identity, check_handshake
from taskmaster.coordinator.service import Coordinator
from native_twins import make_twins

OLD = {'version': '7.0.0', 'digest': 'a' * 32}
NEW = {'version': '7.0.1', 'digest': 'b' * 32}


@pytest.fixture
def root(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, lambda: bs.backlog_add_task(title='Build task', epic='test-epic', phase='dev'),
                       visibility=None)
    with twins.at(twins.native):
        yield twins.native


def request(client, key='one', title='Changed'):
    return {'protocol': 2, 'store_id': client.identity['store_id'], 'caller_scope': 'build-tests',
            'request_id': key, 'operation': 'task.patch', 'arguments': {'id': 'test-epic-001', 'set': {'title': title}},
            'expected_revisions': []}


class _Child:
    def __init__(self, code):
        self.code = code

    def poll(self):
        return self.code


@pytest.fixture
def owners():
    """In-process owners served like `service.main`: stop -> close -> release the lock."""
    started = []

    def serve(build, **kwargs):
        owner = Coordinator(kwargs.pop('root'), **kwargs)
        owner.build = build
        owner.start()
        started.append(owner)
        threading.Thread(target=lambda: (owner.stopping.wait(), owner.close()), daemon=True).start()
        return owner

    yield serve, started
    for owner in started:
        owner.stop()
    for owner in started:
        owner.close()


def launcher(monkeypatch, serve, build, launched):
    """Replace the process launch with an in-process owner of `build`; the kernel lock elects."""
    def launch(path):
        try:
            launched.append(serve(build, root=path))
        except OwnershipUnavailable:
            return _Child(0)  # a startup loser exits cleanly, like service.main
        return _Child(None)
    monkeypatch.setattr(client_module, '_launch', launch)


def forbid_launch(monkeypatch):
    def forbidden(*args):
        raise AssertionError('this client must not start a coordinator')
    monkeypatch.setattr(client_module, '_launch', forbidden)


def wait_released(root, timeout=10):
    deadline = time.monotonic() + timeout
    while ownership_held(root):
        assert time.monotonic() < deadline, 'retired owner never released the ownership lock'
        time.sleep(0.02)


def test_build_identity_names_version_and_source_digest():
    build = build_identity()
    assert set(build) == {'version', 'digest'}
    assert build['version'] and len(build['digest']) == 32
    assert build_identity() == build


def test_same_build_handshake_is_unchanged(root, monkeypatch, owners):
    serve, _ = owners
    owner = serve(build_identity(), root=root)
    forbid_launch(monkeypatch)
    client = Client(root)
    assert client.build == owner.build
    assert client.execute(request(client))['receipt']['affected']
    status = client.status()
    assert status['nonce'] == owner.nonce and status['build'] == owner.build
    assert not owner.stopping.is_set()


def test_different_build_retires_idle_owner_and_new_owner_serves(root, monkeypatch, owners):
    serve, _ = owners
    old = serve(OLD, root=root)
    launched = []
    launcher(monkeypatch, serve, NEW, launched)
    client = Client(root)
    client.build = NEW
    receipt = client.execute(request(client))['receipt']
    assert receipt['affected']
    assert old.stopping.is_set() and old.ownership.descriptor is None
    assert len(launched) == 1
    status = client.status()
    assert status['nonce'] == launched[0].nonce and status['build'] == NEW
    assert client.receipt('build-tests', 'one')['receipt'] == receipt


def test_no_command_runs_on_a_coordinator_of_another_build(root, monkeypatch, owners):
    serve, _ = owners
    owner = serve(OLD, root=root)
    forbid_launch(monkeypatch)
    # Without autostart the client may not retire: it refuses before sending anything.
    client = Client(root, autostart=False)
    client.build = NEW
    with pytest.raises(HandshakeError, match='build') as refused:
        client.execute(request(client))
    assert refused.value.may_have_committed is False
    # The coordinator enforces it too: a command stamped with another build is refused.
    record = client._discovery()
    for method, arguments in (('execute', {'envelope': request(client), 'visibility': 'native'}),
                              ('status', {}), ('shutdown', {})):
        with pytest.raises(HandshakeError, match='build'):
            client._send(record, method, **arguments)
    assert not owner.stopping.is_set()
    same = Client(root, autostart=False)
    same.build = OLD
    assert same.receipt('build-tests', 'one') == {'state': 'unknown'}


def test_busy_old_owner_is_not_retired_mid_command_and_client_refuses(root, monkeypatch, owners):
    serve, _ = owners
    entered, release = threading.Event(), threading.Event()

    def checkpoint(stage):
        if stage == 'admitted':
            entered.set()
            assert release.wait(20)
    old = serve(OLD, root=root, checkpoint=checkpoint)
    forbid_launch(monkeypatch)
    old_client = Client(root, autostart=False)
    old_client.build = OLD
    with ThreadPoolExecutor(max_workers=1) as pool:
        in_flight = pool.submit(old_client.execute, request(old_client, key='in-flight'))
        try:
            assert entered.wait(10)
            client = Client(root, timeout=1)
            client.build = NEW
            with pytest.raises(ServiceUnavailable, match='busy') as refused:
                client.execute(request(client, key='new'))
            assert refused.value.may_have_committed is False
            assert not old.stopping.is_set()
        finally:
            release.set()
        assert in_flight.result(timeout=10)['receipt']['affected']
    assert not old.stopping.is_set()


def test_client_waits_for_busy_old_owner_then_retires_it(root, monkeypatch, owners):
    serve, _ = owners
    entered, release = threading.Event(), threading.Event()

    def checkpoint(stage):
        if stage == 'admitted' and not release.is_set():
            entered.set()
            assert release.wait(20)
    old = serve(OLD, root=root, checkpoint=checkpoint)
    launched = []
    launcher(monkeypatch, serve, NEW, launched)
    old_client = Client(root, autostart=False)
    old_client.build = OLD
    with ThreadPoolExecutor(max_workers=2) as pool:
        in_flight = pool.submit(old_client.execute, request(old_client, key='in-flight'))
        assert entered.wait(10)
        client = Client(root, timeout=20)
        client.build = NEW
        newer = pool.submit(client.execute, request(client, key='new', title='Newer'))
        time.sleep(0.5)
        assert not newer.done() and not old.stopping.is_set()
        release.set()
        committed = in_flight.result(timeout=10)['receipt']
        assert newer.result(timeout=20)['receipt']['affected']
    assert committed['affected']
    assert old.stopping.is_set() and len(launched) == 1
    assert client.receipt('build-tests', 'in-flight')['receipt'] == committed


@pytest.mark.parametrize('work', ['sync', 'git', 'linear'])
def test_owner_with_sync_git_or_linear_work_answers_busy(root, owners, work):
    serve, _ = owners
    owner = serve(OLD, root=root)
    if work == 'sync':
        owner.active_syncs += 1
    elif work == 'git':
        owner.git_active = {'phase': 'launched'}  # a contained Git child may be running
    else:
        owner.linear.jobs[('scope', 'id')] = None
    try:
        assert owner.retire(NEW) == {'state': 'busy', 'build': OLD}
        assert not owner.stopping.is_set()
    finally:
        owner.active_syncs, owner.git_active = 0, None
        owner.linear.jobs.clear()
    assert owner.retire(NEW)['state'] == 'retiring' and owner.stopping.is_set()


def test_older_client_refuses_a_newer_owner_without_downgrading_it(root, monkeypatch, owners):
    serve, _ = owners
    owner = serve(NEW, root=root)
    forbid_launch(monkeypatch)
    client = Client(root)
    client.build = OLD
    with pytest.raises(HandshakeError, match='newer') as refused:
        client.execute(request(client))
    assert refused.value.may_have_committed is False
    # Even a direct retire request from the older build is refused by the owner.
    assert client._send(client._discovery(), 'retire')['state'] == 'refused'
    assert not owner.stopping.is_set()
    current = Client(root, autostart=False)
    current.build = NEW
    assert current.receipt('build-tests', 'one') == {'state': 'unknown'}


def test_two_new_clients_racing_start_exactly_one_new_owner(root, monkeypatch, owners):
    serve, _ = owners
    old = serve(OLD, root=root)
    launched = []
    launcher(monkeypatch, serve, NEW, launched)
    clients = [Client(root) for _ in range(4)]
    for client in clients:
        client.build = NEW
    barrier = threading.Barrier(len(clients))

    def run(pair):
        index, client = pair
        barrier.wait()
        return client.execute(request(client, key=f'race-{index}', title=f'Race {index}'))
    with ThreadPoolExecutor(max_workers=len(clients)) as pool:
        outcomes = list(pool.map(run, enumerate(clients)))
    assert all(item['receipt']['affected'] for item in outcomes)
    assert old.stopping.is_set()
    assert len(launched) == 1
    assert {client.status()['nonce'] for client in clients} == {launched[0].nonce}


def test_client_dying_after_retire_leaves_nothing_wedged(root, monkeypatch, owners):
    serve, _ = owners
    old = serve(OLD, root=root)
    abandoned = Client(root)
    abandoned.build = NEW
    # The negotiation's only effect is the owner's own graceful stop; the client vanishes here.
    assert abandoned._send(abandoned._discovery(), 'retire')['state'] == 'retiring'
    wait_released(root)
    assert old.stopping.is_set()
    launched = []
    launcher(monkeypatch, serve, NEW, launched)
    client = Client(root)
    client.build = NEW
    assert client.execute(request(client))['receipt']['affected']
    assert len(launched) == 1


def test_stale_discovery_of_another_build_just_starts_an_owner(root, monkeypatch, owners):
    serve, _ = owners
    old = serve(OLD, root=root)
    old.stop()
    wait_released(root)  # the old owner is gone; its discovery record remains
    launched = []
    launcher(monkeypatch, serve, NEW, launched)
    client = Client(root)
    client.build = NEW
    assert client.status()['build'] == NEW and len(launched) == 1


@pytest.mark.real_service_process
@pytest.mark.xdist_group("heavy_processes")
def test_real_old_process_retires_and_a_real_new_process_serves(root):
    """End to end across real processes: the old owner exits through `main`'s own close."""
    import os
    from pathlib import Path
    import subprocess
    import sys
    repo = Path(__file__).resolve().parents[1]
    ANCIENT = {'version': '0.0.1', 'digest': 'c' * 32}  # older than the real build under test
    script = f"""
import sys
from pathlib import Path
from taskmaster.coordinator.service import Coordinator
owner = Coordinator(Path(sys.argv[1]))
owner.build = {ANCIENT!r}
with owner:
    while not owner.stopping.wait(0.25):
        pass
"""
    environment = dict(os.environ, PYTHONPATH=str(repo), TASKMASTER_ROOT=str(root))
    old = subprocess.Popen([sys.executable, '-c', script, str(root)], cwd=repo, env=environment,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        deadline = time.monotonic() + 20
        discovery = root / '.taskmaster/local/coordinator/discovery.json'
        while not (discovery.exists() and Client(root, autostart=False)._discovery().get('build') == ANCIENT):
            assert time.monotonic() < deadline and old.poll() is None, 'old owner failed to start'
            time.sleep(0.05)
        client = Client(root)
        assert client.execute(request(client))['receipt']['affected']
        assert old.wait(timeout=15) == 0  # a graceful exit, never a kill
        assert client.status()['build'] == build_identity()
    finally:
        try:
            Client(root, autostart=False, timeout=2).shutdown()
        except (ServiceUnavailable, FileNotFoundError):
            pass
        if old.poll() is None:
            old.terminate()  # only this test-created handle
            old.wait(timeout=10)


def test_handshake_names_a_build_mismatch():
    identity = {'root': 'r', 'store_id': 's', 'schema': 1, 'protocol': 2, 'service_protocol': 4}
    check_handshake(dict(identity, nonce='n', build=OLD), identity, 'n', OLD)
    with pytest.raises(HandshakeError, match='build'):
        check_handshake(dict(identity, nonce='n', build=NEW), identity, 'n', OLD)
    with pytest.raises(HandshakeError, match='build'):
        check_handshake(dict(identity, nonce='n'), identity, 'n', OLD)
    check_handshake(dict(identity, nonce='n', build=NEW), identity, 'n', OLD, any_build=True)
    with pytest.raises(HandshakeError, match='mismatch'):
        check_handshake(dict(identity, nonce='x', build=NEW), identity, 'n', OLD, any_build=True)
