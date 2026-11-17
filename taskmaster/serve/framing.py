# User intent: the one choke point where a team-server tool result that carries a teammate's stored text is wrapped in
# an untrusted-data frame naming its authors, so another member's agent reads planted instructions as data (027 D1).
"""`FrameUntrusted`: a FastMCP middleware, installed by the daemon only, outermost after the client-info capture."""
from __future__ import annotations

import logging

import anyio
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
from mcp.types import TextContent

from taskmaster import untrusted
from taskmaster.native import provenance

LOG = logging.getLogger("taskmaster.serve")

# Tools whose stored-text reads the ledger accounts for (design D1.5). A TRACKED call is unframed only when the
# ledger noted its entities, the read guard saw nothing unaccounted, no refusal came from the coordinator and every
# noted entity's prose is the reader's own. Membership is decided by the marker sweep (tests/test_serve_untrusted_sweep).
TRACKED = frozenset("""
backlog_get_task backlog_list_tasks backlog_handover_get backlog_handover_list backlog_thread_resume
backlog_thread_list backlog_phase_status backlog_decision backlog_bug_get backlog_bug_list backlog_idea_get
backlog_idea_list backlog_issue_get backlog_note backlog_record_gate backlog_skip_gate backlog_clear_gate
backlog_set_spec_review backlog_clear_spec_review backlog_record_merge backlog_store_status backlog_index_status
backlog_open_viewer
""".split())
# Sweep result (Step 10): add_task, update_task, handover_create, dependencies, epic_status, issue_list,
# next_available and task_pipeline showed no marker-observing row, so they stay in the default (store-wide) class.


def string_wrapper(schema) -> bool:
    """FastMCP's output schema for a tool returning text: {"result": string} with the wrap marker."""
    return (isinstance(schema, dict) and schema.get("x-fastmcp-wrap-result") is True
            and (schema.get("properties") or {}).get("result", {}).get("type") == "string")


async def drop_non_string_schemas(server) -> frozenset:
    """Clear the output schema of every registered tool that isn't a string wrapper (D1.2); returns their names."""
    names = set()
    for tool in await server.list_tools():
        schema = getattr(tool, "output_schema", None)
        if schema is not None and not string_wrapper(schema):
            tool.output_schema = None
        if not string_wrapper(getattr(tool, "output_schema", None)):
            names.add(tool.name)
    return frozenset(names)


def drop_non_string_schemas_sync(server) -> frozenset:
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(drop_non_string_schemas(server))
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, drop_non_string_schemas(server)).result()


def _own_refusal(text: str) -> bool:
    return text.startswith("Error:") or text.lstrip().startswith('{"error"')


class FrameUntrusted(Middleware):
    """Over HTTP only: binds a provenance ledger around the call, resolves who wrote the stored text the call
    read, and frames every text block with those authors. Calls without an HTTP request pass through untouched."""

    def __init__(self, coordinator, *, pre_team=lambda: (None, None), non_string=frozenset(), tracked=TRACKED,
                 observer=None, authors_timeout: float = 30.0):
        self.coordinator = coordinator
        self.pre_team = pre_team
        self.non_string = frozenset(non_string)
        self.tracked = frozenset(tracked)
        self.observer = observer                    # tests: (ledger, tokens) after each resolution
        self.timeout = authors_timeout
        self._store = provenance.StoreAuthors()

    async def on_call_tool(self, context, call_next):
        from fastmcp.server.dependencies import get_http_request
        try:
            get_http_request()
        except RuntimeError:
            return await call_next(context)
        from .auth import current_principal
        principal = current_principal()
        tool = getattr(context.message, "name", "?")
        ledger = provenance.Ledger(reader=principal.handle if principal else "", tool=tool)
        token = provenance.bind(ledger)
        failure = None
        try:
            result = await call_next(context)
        except ToolError as exc:
            result, failure = None, exc
        finally:
            provenance.unbind(token)
        text = str(failure) if failure is not None else self._first_text(result)
        tokens = await self._tokens(ledger, text)
        if self.observer is not None:
            self.observer(ledger, tokens)
        if failure is not None:
            if tokens:
                raise ToolError(untrusted.frame(str(failure), tokens)) from None
            raise failure
        return self._apply(result, tool, tokens)

    @staticmethod
    def _first_text(result) -> str:
        for block in getattr(result, "content", None) or []:
            if isinstance(block, TextContent):
                return block.text
        return ""

    def _apply(self, result, tool, tokens):
        content = getattr(result, "content", None)
        structured = getattr(result, "structured_content", None)
        if tokens and isinstance(content, list):
            first = None
            for i, block in enumerate(content):
                if isinstance(block, TextContent):
                    framed = untrusted.frame(block.text, tokens)
                    first = framed if first is None else first
                    content[i] = block.model_copy(update={"text": framed})
            if tool in self.non_string:
                result.structured_content = None
            elif isinstance(structured, dict) and set(structured) == {"result"} and isinstance(structured["result"], str):
                result.structured_content = {"result": first if first is not None else structured["result"]}
            elif structured is not None:
                result.structured_content = None
        elif tool in self.non_string and structured is not None:
            result.structured_content = None             # a raw dict never leaves the server (D1.2)
        return result

    async def _tokens(self, ledger, text) -> set:
        tokens = set(ledger.external)
        if not ledger.opened and not ledger.entities:
            return tokens                                # the call read nothing from the store
        if ledger.unaccounted:
            LOG.info("provenance: unaccounted read tool=%s columns=%s", ledger.tool, sorted(set(ledger.unaccounted)))
        try:
            with anyio.fail_after(self.timeout):
                return tokens | await anyio.to_thread.run_sync(self._resolve, ledger, text)
        except Exception as exc:  # noqa: BLE001 - failure is safe: frame as unattributed, never leave text bare
            LOG.warning("provenance: authors unresolved tool=%s (%s); framing as unattributed", ledger.tool,
                        type(exc).__name__)
            return tokens | {provenance.UNATTRIBUTED}

    def _resolve(self, ledger, text) -> set:
        from contextlib import closing
        with closing(self.coordinator._connect(readonly=True)) as connection:
            owner_cutoff = self.pre_team()
            if not ledger.opened:
                # No native read opened (an adoption notice's own snapshot noted entities): only those count.
                return provenance.authors_of(connection, ledger.entities, reader=ledger.reader, pre_team=owner_cutoff)
            if ledger.tool in self.tracked:
                clean = (not ledger.unaccounted and not ledger.refused
                         and (bool(ledger.entities) or _own_refusal(text)))
                if clean:
                    return provenance.authors_of(connection, ledger.entities, reader=ledger.reader,
                                                 pre_team=owner_cutoff)
            store_id = connection.execute("SELECT value FROM native_manifest WHERE key='store_id'").fetchone()
            return self._store.authors(connection, store_id=store_id[0] if store_id else "", reader=ledger.reader,
                                       pre_team=owner_cutoff)
