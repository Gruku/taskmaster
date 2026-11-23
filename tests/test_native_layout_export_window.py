# User intent: B-111 follow-up - while the native exporter has backlog.yaml moved aside, routed native adapters must still see
# the project (store.db is the stable marker) instead of answering "no backlog found / run backlog_init".
from __future__ import annotations

import pytest

from taskmaster import backlog_server as bs
from taskmaster.native_routing import documents, viewer
from test_native_service_build import root  # noqa: F401

NO_BACKLOG = ("no backlog found", "run `backlog_init`")


@pytest.fixture
def window(root, monkeypatch):  # noqa: F811
    """The export window: backlog.yaml is gone, the store database is not."""
    from taskmaster.coordinator import adapter
    from tests.native_coordinator_helpers import compatibility_client
    monkeypatch.setattr(adapter, "Client", compatibility_client)
    backlog = root / ".taskmaster" / "backlog.yaml"
    backlog.rename(backlog.with_name("backlog.yaml.aside"))
    assert not backlog.exists() and (root / ".taskmaster" / "local" / "store.db").is_file()
    return root


def _says_no_backlog(text) -> bool:
    return any(phrase in str(text).lower() for phrase in NO_BACKLOG)


def test_records_adapter_lists_bugs_in_the_window(window):
    assert not _says_no_backlog(bs.backlog_bug_list())


def test_records_adapter_creates_in_the_window(window):
    out = bs.backlog_idea_create(title="window idea", body="b")
    assert not _says_no_backlog(out)


def test_notes_adapter_in_the_window(window):
    assert not _says_no_backlog(bs.backlog_note(action="list"))
    assert not _says_no_backlog(bs.backlog_note(action="create", text="n"))


def test_handovers_adapter_in_the_window(window):
    assert not _says_no_backlog(bs.backlog_thread_list())


def test_links_areas_adapter_in_the_window(window):
    assert not _says_no_backlog(bs.backlog_area_list())


def test_overview_store_status_in_the_window(window):
    assert not _says_no_backlog(bs.backlog_store_status())


def test_viewer_snapshot_and_documents_in_the_window(window):
    assert viewer.snapshot is not None
    assert documents.project_root() == window
