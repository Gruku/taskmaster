"""Owned in-process services for compatibility tests using the real HTTP client.

In-process ownership preserves fake-clock and fault-injection seams; there is no
direct-engine shortcut. A per-test finalizer closes every owned service before
fixture clocks are restored. Actual process boundaries have a separate suite.
"""
from pathlib import Path
import threading

from taskmaster.coordinator.client import Client
from taskmaster.coordinator.service import Coordinator

_owners = {}
_guard = threading.Lock()


def compatibility_client(root, *, visibility='legacy', **kwargs):
    root = Path(root).resolve()
    with _guard:
        if root not in _owners:
            _owners[root] = Coordinator(root).start()
    kwargs.pop('autostart', None)  # the in-process owner above is always running
    return Client(root, autostart=False, visibility='legacy', **kwargs)


def close_owned():
    with _guard:
        owners = list(_owners.values())
        _owners.clear()
    for owner in owners:
        owner.close()
