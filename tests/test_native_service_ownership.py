"""N12 ownership must never trust or signal the discovery PID."""
import json
import os

import pytest

from taskmaster.coordinator.ownership import Ownership, OwnershipUnavailable, verify_private


def root_at(tmp_path):
    (tmp_path / '.taskmaster/local').mkdir(parents=True)
    return tmp_path


def test_only_one_owner_and_reacquire_after_release(tmp_path, monkeypatch):
    root = root_at(tmp_path)
    def forbidden(*a, **kw):
        pytest.fail('ownership used a destructive PID probe')
    monkeypatch.setattr(os, 'kill', forbidden)
    with Ownership(root) as first:
        first.publish({'pid': os.getpid(), 'nonce': 'stale'})
        verify_private(first.directory)
        verify_private(first.directory / 'discovery.json')
        with pytest.raises(OwnershipUnavailable):
            with Ownership(root):
                pass
    with Ownership(root) as replacement:
        replacement.publish({'pid': 0xffffffff, 'nonce': 'new'})
        record = json.loads((replacement.directory / 'discovery.json').read_text())
        assert record['nonce'] == 'new'


def test_discovery_requires_held_ownership(tmp_path):
    owner = Ownership(root_at(tmp_path))
    with pytest.raises(RuntimeError, match='ownership'):
        owner.publish({'nonce': 'invalid'})


@pytest.mark.skipif(os.name == 'nt', reason='POSIX permission assertion')
def test_private_modes(tmp_path):
    with Ownership(root_at(tmp_path)) as owner:
        owner.publish({'token': 'fixture-only'})
        assert owner.directory.stat().st_mode & 0o777 == 0o700
        assert (owner.directory / 'discovery.json').stat().st_mode & 0o777 == 0o600
