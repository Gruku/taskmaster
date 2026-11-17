# User intent: on a team server a teammate imports a document from their own machine by sending its text, credited to them; a replace of someone else's copy is visible or guarded; a host path the server won't read points them at content=; and the restore path never appears on the tool surface (team-server-012 D2, D4, D5, D6, D10).
from __future__ import annotations

import json
import sys

import pytest

from serve_helpers import call_tool
from taskmaster import backlog_server as bs
from taskmaster.coordinator import allowlist
from taskmaster.coordinator import client as client_module
from taskmaster.coordinator.client import Client
from native_twins import native_connection
from test_native_service_build import root  # noqa: F401  (fixture)
from test_serve_identity import _value, no_host_identity, scope_of, team_on, world  # noqa: F401  (fixtures)
from test_serve_principals_e2e import launch  # noqa: F401  (fixture)
from test_session_scope import fake as fake_session

TASK = "test-epic-001"
HINT = '; to import the file from your machine, pass its text: content={"spec": "..."}'


def _declare(root_, **docs):
    from taskmaster.native.commands import _write_field  # test-only: the task declares its documents
    with native_connection(root_) as connection:
        key = connection.execute("SELECT entity_key FROM entity_core WHERE kind='task' AND public_id=?",
                                 (TASK,)).fetchone()[0]
        _write_field(connection, key, "task", "docs", docs)


@pytest.fixture
def served(world, no_host_identity, monkeypatch):  # noqa: F811
    _declare(world.root, spec="docs/specs/only-on-my-machine.md")
    who = {"key": world.ann}
    monkeypatch.setattr(client_module, "ORIGIN_PROVIDER", lambda: who["key"])
    # The daemon pairs the key with its connection's session (team-server-003 D3/D5); in process, so does this.
    monkeypatch.setattr(bs, "SESSION_PROVIDER", lambda: fake_session(scope_of(world, who["key"]), agent="claude"))
    world.who = who
    return world


def _import(**kwargs):
    return bs.backlog_document_import(kind="task", entity_id=TASK, **kwargs)


def _actors(root_):
    return [json.loads(row[0]) for row in _value(root_, "SELECT actor FROM domain_events WHERE op='document.import' "
                                                        "ORDER BY seq")]


def _spec_provenance():
    return bs.backlog_document(kind="task", entity_id=TASK, sections=["spec"], provenance=True)


def test_a_member_imports_a_document_the_host_does_not_have(served):
    assert not (served.root / "docs/specs/only-on-my-machine.md").exists()
    answer = _import(content={"spec": "Ann's spec.\n"})
    assert "- spec: imported from your upload as docs/specs/only-on-my-machine.md" in answer, answer
    assert _actors(served.root)[-1] == {"handle": "ann", "agent": "claude", "clone_id": served.ann}   # the session's agent
    detail = _spec_provenance()
    assert "Ann's spec." in detail and "origin: upload" in detail and "imported_by: ann" in detail


def test_a_replace_by_someone_else_is_named_or_guarded(served):
    _import(content={"spec": "Ann's spec.\n"})
    served.who["key"] = served.bob
    stale = _import(content={"spec": "Bob's spec.\n"}, expected_hash={"spec": "0" * 64})
    assert "FAILED — the stored copy changed" in stale and "imported by ann at seq" in stale
    replaced = _import(content={"spec": "Bob's spec.\n"})
    assert "- spec: imported from your upload" in replaced and "— replaced the copy ann imported at seq" in replaced
    again = _import(content={"spec": "Bob's spec, v2.\n"})
    assert "replaced the copy" not in again                       # his own copy: nothing to name


def test_a_refused_or_missing_host_path_points_at_content(served):
    _declare(served.root, spec="docs/spec.md")
    missing = _import(sections=["spec"])
    assert HINT in missing and "nothing imported" in missing, missing
    (served.root / "docs").mkdir(exist_ok=True)
    (served.root / "docs" / "spec.md").write_text("Untracked host file.\n", encoding="utf-8")
    refused = _import(sections=["spec"])
    assert "not a git-tracked project file" in refused and HINT in refused, refused
    assert "Untracked host file." not in refused


def test_the_tool_schema_has_no_restore_parameters():
    import asyncio
    tool = asyncio.run(bs.mcp._inner.get_tool("backlog_document_import"))
    properties = set(tool.parameters["properties"])
    assert {"content", "expected_hash"} <= properties
    assert not properties & {"restore", "imported_by", "original_seq", "source"}


def test_only_documents_py_builds_a_document_import():
    from pathlib import Path
    package = Path(__file__).resolve().parents[1] / "taskmaster"
    hits = sorted(path.relative_to(package).as_posix() for path in package.rglob("*.py")
                  if 'execute("document.import"' in path.read_text(encoding="utf-8")
                  or "execute('document.import'" in path.read_text(encoding="utf-8"))
    assert hits == ["native_routing/documents.py"], hits


def test_a_batch_cannot_carry_a_document_import(served):
    answer = bs.backlog_batch_update(commands=[{"operation": "document.import", "arguments": {
        "kind": "task", "id": TASK, "section": "spec", "path": "docs/specs/only-on-my-machine.md", "body": "x",
        "restore": True, "source": "upload", "imported_by": "bob"}}])
    assert "backlog_document_import" in answer and not _value(served.root, "SELECT 1 FROM external_documents")


# ── over HTTP ────────────────────────────────────────────────────────────────

TOOL = "t012_restore"


@pytest.fixture
def remote(launch):  # noqa: F811
    served_ = launch()
    _declare(served_.root, spec="docs/specs/only-on-my-machine.md")
    inner = sys.modules["taskmaster.backlog_server"].mcp._inner

    def send_restore() -> str:
        """Test-only: a tool that builds a restore envelope itself."""
        client = Client(served_.root, autostart=False)
        value = {"protocol": 2, "store_id": client.identity["store_id"], "caller_scope": "t012", "request_id": "r1",
                 "operation": "document.import", "expected_revisions": [],
                 "arguments": {"kind": "task", "id": TASK, "section": "spec", "path": "docs/specs/only-on-my-machine.md",
                               "body": "forged", "restore": True, "source": "upload", "imported_by": "bob"}}
        try:
            client.execute(value)
            return "passed"
        except ValueError as exc:
            return "refused" if str(exc) == allowlist.RESTORE_REFUSED else f"other:{exc}"
    inner.tool(send_restore, name=TOOL)
    yield served_
    inner.local_provider.remove_tool(TOOL)


def test_over_http_a_member_uploads_and_cannot_restore(remote):
    ann = remote.keys["ann"].key
    answer = call_tool(remote.url, ann, "backlog_document_import",
                       {"kind": "task", "entity_id": TASK, "content": {"spec": "Ann's spec.\n"}})
    assert "imported from your upload" in answer, answer
    assert call_tool(remote.url, ann, TOOL) == "refused"
    assert "forged" not in call_tool(remote.url, ann, "backlog_get_task", {"task_id": TASK, "sections": ["spec"]})


def test_over_http_the_schema_is_the_same_for_everyone(remote):
    import asyncio

    from fastmcp import Client as McpClient

    async def schema(token):
        async with McpClient(remote.url, auth=token) as client:
            tool = next(t for t in await client.list_tools() if t.name == "backlog_document_import")
            return set(tool.inputSchema["properties"])
    for token in (remote.keys["ann"].key, remote.operator):
        properties = asyncio.run(schema(token))
        assert "content" in properties and not properties & {"restore", "imported_by", "original_seq", "source"}


def test_over_http_a_body_over_the_cap_imports_nothing(remote):
    """A 006 regression guard, not a red test: the 2 MiB body cap answers before the tool runs."""
    ann = remote.keys["ann"].key
    with pytest.raises(Exception):
        call_tool(remote.url, ann, "backlog_document_import",
                  {"kind": "task", "entity_id": TASK, "content": {"spec": "x" * (2 * 1024 * 1024 + 1)}})
    assert not _value(remote.root, "SELECT 1 FROM external_documents")
