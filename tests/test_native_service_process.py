"""Actual coordinator process boundaries; cleanup targets only owned Popen handles."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from taskmaster.coordinator.client import Client, _launch
from taskmaster.coordinator.protocol import ServiceUnavailable
from test_native_service import root, request  # noqa: F401

# The process boundary is what this module proves (conftest refuses unmarked launches).
pytestmark = pytest.mark.real_service_process


def ready(root, *, timeout=20):
    client = Client(root, autostart=False, timeout=2)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            client.status()
            return client
        except ServiceUnavailable:
            time.sleep(0.05)
    raise AssertionError('owned coordinator failed to become ready')


def stop_owned(processes, root):
    try:
        Client(root, autostart=False, timeout=2).shutdown()
    except ServiceUnavailable:
        pass
    for process in processes:
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            # This exact handle was created by this test, never from discovery.
            process.terminate()
            process.wait(timeout=10)


@pytest.mark.xdist_group("heavy_processes")  # conftest: one multi-process test at a time
def test_concurrent_process_start_elects_exactly_one_owner(root):
    with ThreadPoolExecutor(max_workers=4) as pool:
        processes = list(pool.map(lambda _: _launch(root), range(4)))
    try:
        client = ready(root)
        generation = client.status()['nonce']
        with ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(pool.map(lambda _: Client(root, autostart=False).execute(request(client)), range(4)))
        assert all(value['receipt'] == receipts[0]['receipt'] for value in receipts)
        assert client.status()['nonce'] == generation
        deadline = time.monotonic() + 10
        while sum(p.poll() is None for p in processes) > 1 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert sum(p.poll() is None for p in processes) == 1
    finally:
        stop_owned(processes, root)


def test_owned_process_crash_releases_lock_and_retry_recovers_receipt(root):
    first = _launch(root)
    processes = [first]
    try:
        client = ready(root)
        nonce = client.status()['nonce']
        envelope = request(client)
        committed = client.execute(envelope)['receipt']
        first.kill()  # crash injection into our owned child, not any discovered PID
        first.wait(timeout=10)
        processes.append(_launch(root))
        restarted = ready(root)
        assert restarted.status()['nonce'] != nonce
        assert restarted.execute(envelope)['receipt'] == committed
    finally:
        stop_owned(processes, root)


def test_restart_exports_a_committed_task_and_progress_after_owner_dies_before_export(root):
    repo = Path(__file__).resolve().parents[1]
    script = """
import sys, threading, time
from pathlib import Path
from taskmaster.coordinator.service import Coordinator
root = Path(sys.argv[1])
def blocked(*args, **kwargs):
    (root / 'export-blocked').touch()
    threading.Event().wait(60)
    return []
with Coordinator(root, exporter=blocked):
    while True:
        time.sleep(0.1)
"""
    environment = dict(os.environ, PYTHONPATH=str(repo), TASKMASTER_ROOT=str(root))
    first = subprocess.Popen([sys.executable, '-c', script, str(root)], cwd=repo, env=environment,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    processes = [first]
    try:
        client = ready(root)
        deadline = time.monotonic() + 5
        while not (root / 'export-blocked').exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (root / 'export-blocked').exists()
        for index, (operation, arguments) in enumerate((
            ('task.update', {'id': 'test-epic-001', 'field': 'lane', 'value': 'express'}),
            ('task.gate', {'id': 'test-epic-001', 'gate': 'review-gate', 'verdict': 'pass'}),
            ('task.pick', {'id': 'test-epic-001', 'session': 'service-tests'}),
        )):
            envelope = request(client, key=f'prepare-{index}')
            envelope.update(operation=operation, arguments=arguments)
            client.execute(envelope)
        envelope = request(client, key='completion-after-restart')
        envelope.update(operation='task.complete', arguments={
            'id': 'test-epic-001', 'changelog': '### Coordinator crash recovery once\n- acknowledged before export'})
        committed = client.execute(envelope)['receipt']
        progress = root / '.taskmaster/local/PROGRESS.md'
        assert 'Coordinator crash recovery once' not in progress.read_text(encoding='utf-8')
        first.kill()  # only this test-created handle, after a confirmed commit
        first.wait(timeout=10)
        processes.append(_launch(root))
        restarted = ready(root)
        # Prove startup itself recovers the durable outboxes. A replay would
        # wake the exporter and flush would drain them, masking a missing
        # startup-recovery signal. Observe files only until both are published.
        task_path = root / '.taskmaster/tasks/test-epic-001.md'
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if ('Coordinator crash recovery once' in progress.read_text(encoding='utf-8')
                    and 'status: done' in task_path.read_text(encoding='utf-8')):
                break
            time.sleep(0.05)
        assert progress.read_text(encoding='utf-8').count('Coordinator crash recovery once') == 1
        assert 'status: done' in task_path.read_text(encoding='utf-8')
        assert restarted.execute(envelope)['receipt'] == committed
        assert restarted.flush(committed['commit_seq'])['state'] == 'exported'
        assert progress.read_text(encoding='utf-8').count('Coordinator crash recovery once') == 1
        task = task_path.read_text(encoding='utf-8')
        assert 'status: done' in task
    finally:
        stop_owned(processes, root)
