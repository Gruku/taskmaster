"""User intent: writing `project.yaml` is a store write like any other, so it
must report the commit that produced it and initialization must not be able to
overwrite a manifest that already exists.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store


def _project_yaml(root: Path) -> Path:
    return root / ".taskmaster" / "project.yaml"


def test_project_set_reports_the_sequence_it_committed(tm_epic_phase):
    """Spec 3.8: a write result ends with `[seq N]`. Without it a caller
    cannot tie the manifest write to its commit."""
    result = bs.backlog_project_set(
        yaml_content=(
            "schema_version: 1\n"
            "meta:\n  name: Demo\n  slug: demo\n  kind: app\n"
        )
    )
    assert re.search(r"\[seq \d+\]$", result.strip()), result
    assert _project_yaml(tm_epic_phase).exists()


def test_project_init_reports_the_sequence_it_committed(tm_epic_phase):
    result = bs.backlog_project_init(name="Demo")
    assert re.search(r"\[seq \d+\]$", result.strip()), result


def test_project_init_refuses_when_the_row_already_exists(tm_epic_phase):
    """The file check happens outside the transaction, so two initializers can
    both pass it and the second silently replaces the first's committed row.
    The authoritative check has to be on the row, inside the transaction."""
    bs.backlog_project_set(
        yaml_content=(
            "schema_version: 1\n"
            "meta:\n  name: Real Project\n  slug: real\n  kind: app\n"
        )
    )
    # The projection is gone but the row is not — the state a failed first
    # export, or a racing initializer, leaves behind.
    _project_yaml(tm_epic_phase).unlink()

    with pytest.raises(ValueError):
        bs.backlog_project_init(name="Impostor")

    store.reset_for_tests()
    with store.transaction(backlog_path=tm_epic_phase / ".taskmaster" / "backlog.yaml",
                           tool="test-read") as tx:
        doc = tx.get("project", "__project__")
    assert doc["meta"]["name"] == "Real Project"
