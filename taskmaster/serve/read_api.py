# User intent: one versioned, authenticated read endpoint on the team server for teammates' hooks and mods, giving
# a key exactly what its MCP read tools give it, minus the host's private bookkeeping, behind every 001/006 layer
# (team-server-007 D1-D7).
"""`/read/v1/{meta,paths,merge-gate,merge-target,tools}` routes for `build_app(routes=...)`."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from taskmaster import query_guard
from taskmaster.admission import UnsupportedStoreError
from taskmaster.coordinator import protocol
from taskmaster.native_routing import hook_reads

from . import auth
from .app import REQUEST_ID_KEY, log_safe
from .config import READ_PREFIX
from .limits import retry_after
from .sanitize import new_ref, scrub
from .sessions import READ_CHANNEL_KEY

LOG = logging.getLogger("taskmaster.serve")
PREFIX = READ_PREFIX
VERSION = 1
READ_TOOLS = ("backlog_list_tasks", "backlog_issue_list", "backlog_continuity_items", "backlog_handover_list",
              "backlog_handover_get", "backlog_get_task", "backlog_task_pipeline", "backlog_query")
VIEWS = ("meta", "paths", "merge-gate", "merge-target", "tools")
READ_BATCH_MAX_CALLS = 16
READ_BATCH_MAX_BYTES = 64 * 1024
READ_ARGS_MAX_BYTES = 16 * 1024
BATCH_BUDGET_SECONDS = 9.0
VIEW_TIMEOUT_S = query_guard.QUERY_TIMEOUT_S      # one view's read transaction, SQL and Python alike
BATCH_TEXT_CAP = 3 * 1024 * 1024
NOT_RUN_BUDGET = "Error: not run: batch time budget"
NOT_RUN_SIZE = "Error: not run: reply size budget"
CLONE_RE = re.compile(r"c-[0-9a-f]{16}")
NO_STORE = {"Cache-Control": "no-store"}


class Refused(Exception):
    def __init__(self, status: int, code: str, message: str, headers=None):
        super().__init__(message)
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}


def valid_ref(name: str, value) -> str:
    """A branch name, from a Git ref-name subset (D5); the rule is `hook_reads.ref_problem`'s."""
    if hook_reads.ref_problem(value) is not None:
        raise Refused(400, "bad-request", f"{name} is not a usable branch name")
    return value


def valid_clone(value) -> str:
    """The optional `clone` wire parameter (008 section 11): `c-` and 16 hex digits, or empty."""
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not CLONE_RE.fullmatch(value):
        raise Refused(400, "bad-request", "clone is not a clone id")
    return value


def valid_path(value) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024 or "\\" in value or "\x00" in value \
            or value.startswith("/") or re.match(r"[A-Za-z]:", value) \
            or any(part in ("", ".", "..") for part in value.split("/")):
        raise Refused(400, "bad-request", "path must be a relative, /-separated repository path")
    return value


def _text_of(result) -> str:
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict) and isinstance(structured.get("result"), str):
        return structured["result"]
    parts = [getattr(block, "text", "") for block in getattr(result, "content", None) or []]
    return "".join(part for part in parts if isinstance(part, str))


class ReadApi:
    def __init__(self, *, root: Path, server, limiter, build: str = "", monotonic=time.monotonic):
        self.root, self.server, self.limiter, self.build = Path(root), server, limiter, build
        self._monotonic = monotonic

    def routes(self) -> list:
        return [
            Route(f"{PREFIX}/meta", self.meta, methods=["GET"]),
            Route(f"{PREFIX}/paths", self.paths, methods=["GET"]),
            Route(f"{PREFIX}/merge-gate", self.merge_gate, methods=["GET"]),
            Route(f"{PREFIX}/merge-target", self.merge_target, methods=["GET"]),
            Route(f"{PREFIX}/tools", self.tools, methods=["POST"]),
            Route("/read/{rest:path}", self.unknown, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
        ]

    # -- plumbing ---------------------------------------------------------------------------------

    def _ref(self, request) -> str:
        return request.scope.get(REQUEST_ID_KEY) or new_ref()

    def _error(self, request, status, code, message, headers=None, **extra) -> JSONResponse:
        text = scrub(message, self.root).text
        body = {"v": VERSION, "error": {"code": code, "message": text, "ref": self._ref(request), **extra}}
        return JSONResponse(body, status_code=status, headers={**NO_STORE, **(headers or {})})

    def _principal(self, request):
        user = request.scope.get("user")
        principal = auth.principal_from_access_token(getattr(user, "access_token", None))
        if principal is None:      # RequirePrincipalMiddleware already refused; an invariant, not a path
            raise Refused(500, "internal", "authenticated principal missing")
        return principal

    @staticmethod
    def _no_body(request) -> None:
        headers = request.headers
        length = headers.get("content-length") or "0"
        if headers.get("transfer-encoding") or not length.isdigit() or int(length) > 0:
            raise Refused(400, "bad-request", "a GET read takes no body")

    def _read_store(self, fn):
        """`(data, revision, store_id)` from one read transaction on the host store (D4).

        Bounded to VIEW_TIMEOUT_S: a progress handler stops SQLite, and `fn(connection, deadline)` checks the same
        deadline in its own Python loops (review I1), so no view can hold the store or the GIL past it."""
        deadline = query_guard.Deadline(VIEW_TIMEOUT_S)
        try:
            # A lock wait is SQLite's, not Python's: the busy timeout may not outlast the view deadline.
            with closing(protocol.connect(self.root, readonly=True, busy_seconds=max(0.0, deadline.expires_at - time.monotonic()))) as connection:
                connection.set_progress_handler(deadline, query_guard.PROGRESS_INSTRUCTIONS)
                connection.execute("BEGIN")
                identity = hook_reads.admit(connection)
                if identity is None:
                    raise UnsupportedStoreError("the team store is not a native store")
                revision = hook_reads.revision(connection)
                data = fn(connection, deadline) if fn is not None else None
                if deadline.expired or deadline():
                    raise hook_reads.ViewTimeout("the read ran past its deadline")
                return data, revision, str(identity["store_id"])
        except hook_reads.ViewTimeout:
            raise Refused(503, "timeout", f"the read took longer than {VIEW_TIMEOUT_S:g} s") from None
        except sqlite3.OperationalError as exc:
            if deadline.expired:
                raise Refused(503, "timeout", f"the read took longer than {VIEW_TIMEOUT_S:g} s") from None
            raise Refused(503, "store-unavailable", f"the team store cannot be read: {exc}") from None
        except UnsupportedStoreError as exc:
            raise Refused(503, "store-unavailable", f"the team store cannot be read: {exc}") from None
        except (OSError, sqlite3.Error) as exc:
            raise Refused(503, "store-unavailable", f"the team store cannot be read: {exc}") from None

    def _envelope(self, view, data, revision, store_id) -> JSONResponse:
        body = {"v": VERSION, "view": view, "store_id": store_id, "revision": revision,
                "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "data": data}
        return JSONResponse(body, headers=NO_STORE)

    async def _guarded(self, request, view, handler):
        try:
            return await handler()
        except Refused as refused:
            return self._error(request, refused.status, refused.code, refused.message, refused.headers)
        except Exception as exc:  # noqa: BLE001 - logged with its ref, answered without detail
            ref = self._ref(request)
            LOG.error("read error ref=%s view=%s type=%s", ref, view, type(exc).__name__, exc_info=exc)
            return self._error(request, 500, "internal", f"internal error in {view} (ref {ref}); "
                                                          "the operator can look it up in the server log")

    async def _view(self, request, view, fn):
        async def handler():
            self._no_body(request)
            data, revision, store_id = await run_in_threadpool(self._read_store, fn)
            return self._envelope(view, data, revision, store_id)
        return await self._guarded(request, view, handler)

    # -- views ------------------------------------------------------------------------------------

    async def meta(self, request):
        data = {"supported": [VERSION], "build": self.build, "views": list(VIEWS), "tools": list(READ_TOOLS)}
        return await self._view(request, "meta", lambda connection, deadline: data)

    async def paths(self, request):
        async def handler():
            self._principal(request)
            path = valid_path(request.query_params.get("path"))
            return await self._view(request, "paths", lambda connection, deadline: hook_reads.paths_view(connection, path, deadline))
        return await self._guarded(request, "paths", handler)

    async def merge_gate(self, request):
        async def handler():
            principal = self._principal(request)
            branch = valid_ref("branch", request.query_params.get("branch"))
            clone = valid_clone(request.query_params.get("clone")) or None
            return await self._view(request, "merge-gate", lambda connection, deadline: hook_reads.merge_gate_view(
                connection, branch, principal, clone=clone))
        return await self._guarded(request, "merge-gate", handler)

    async def merge_target(self, request):
        async def handler():
            principal = self._principal(request)
            branch = valid_ref("branch", request.query_params.get("branch"))
            target = valid_ref("target", request.query_params.get("target"))
            clone = valid_clone(request.query_params.get("clone")) or None
            return await self._view(request, "merge-target", lambda connection, deadline: hook_reads.merge_target_view(
                connection, branch, target, principal, clone=clone))
        return await self._guarded(request, "merge-target", handler)

    async def unknown(self, request):
        rest = request.path_params.get("rest", "")
        version = rest.split("/", 1)[0]
        if version != f"v{VERSION}":
            return self._error(request, 404, "unsupported-version", f"read API {log_safe(version, 16)} is not served",
                               supported=[VERSION])
        view = rest.split("/", 1)[1] if "/" in rest else ""
        if view in VIEWS:          # a known view asked with the wrong method (review M5)
            allowed = "POST" if view == "tools" else "GET"
            return self._error(request, 405, "method-not-allowed", f"{view} takes {allowed}", {"Allow": allowed})
        return self._error(request, 404, "unknown-view", "no such read view", views=list(VIEWS))

    # -- tools ------------------------------------------------------------------------------------

    async def _calls(self, request) -> list:
        length = request.headers.get("content-length")
        if request.headers.get("transfer-encoding") or length is None:
            raise Refused(411, "bad-request", "a tools batch needs a Content-Length")
        try:
            size = int(length)
        except ValueError:
            raise Refused(400, "bad-request", "Content-Length is not a number") from None
        if size > READ_BATCH_MAX_BYTES:
            raise Refused(413, "bad-request", f"a tools batch is at most {READ_BATCH_MAX_BYTES} bytes")
        try:
            body = json.loads((await request.body()).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise Refused(400, "bad-request", "the body is not JSON") from None
        calls = body.get("calls") if isinstance(body, dict) else None
        if not isinstance(calls, list) or not 1 <= len(calls) <= READ_BATCH_MAX_CALLS:
            raise Refused(400, "bad-request", f"calls must be a list of 1 to {READ_BATCH_MAX_CALLS} tool calls")
        checked = []
        for call in calls:
            tool = call.get("tool") if isinstance(call, dict) else None
            args = call.get("args", {}) if isinstance(call, dict) else None
            if tool not in READ_TOOLS:
                raise Refused(400, "bad-request", f"tool not readable here: {log_safe(tool, 64)}")
            if not isinstance(args, dict) or len(json.dumps(args)) > READ_ARGS_MAX_BYTES:
                raise Refused(400, "bad-request", f"args of {tool} must be a JSON object of at most "
                                                  f"{READ_ARGS_MAX_BYTES} bytes")
            checked.append((tool, args))
        return checked

    def _charge(self, principal, count: int) -> None:
        """One token per call (D6): PrincipalLimits took one for the request."""
        if count > self.limiter.capacity:
            raise Refused(400, "bad-request", f"a batch of {count} calls exceeds the rate burst "
                                              f"{int(self.limiter.capacity)}; ask the operator")
        if count > 1:
            wait = self.limiter.take(principal.key_id, cost=count - 1)
            if wait:
                raise Refused(429, "busy", "rate limited", {"Retry-After": str(retry_after(wait))})

    async def tools(self, request):
        async def handler():
            principal = self._principal(request)
            calls = await self._calls(request)
            self._charge(principal, len(calls))
            # The stamp is read before any call runs, so it is never newer than the data (M12).
            _, revision, store_id = await run_in_threadpool(self._read_store, None)
            results = await self._run(request, calls)
            return self._envelope("tools", {"results": results}, revision, store_id)
        return await self._guarded(request, "tools", handler)

    async def _run(self, request, calls) -> list:
        from fastmcp.exceptions import ToolError
        results, started, total = [], self._monotonic(), 0
        request.scope[READ_CHANNEL_KEY] = True
        try:
            with query_guard.restricted(query_guard.READ_ENDPOINT_TABLES):
                for index, (tool, args) in enumerate(calls):
                    if self._monotonic() - started > BATCH_BUDGET_SECONDS:
                        results.append({"tool": tool, "text": NOT_RUN_BUDGET, "is_error": True})
                        continue
                    if total > BATCH_TEXT_CAP:
                        results.append({"tool": tool, "text": NOT_RUN_SIZE, "is_error": True})
                        continue
                    if index and await request.is_disconnected():
                        LOG.info("read batch stopped: the client went away ref=%s", self._ref(request))
                        break
                    try:
                        result = await self.server.call_tool(tool, args, run_middleware=True)
                        text, is_error = _text_of(result), False
                    except ToolError as exc:
                        text, is_error = str(exc), True
                    except Exception as exc:  # noqa: BLE001 - validation errors and the like, already scrubbed
                        text, is_error = f"Error: {scrub(str(exc), self.root).text}", True
                    total += len(text)
                    if total > BATCH_TEXT_CAP:
                        results.append({"tool": tool, "text": NOT_RUN_SIZE, "is_error": True})
                        continue
                    results.append({"tool": tool, "text": text, "is_error": is_error})
        finally:
            request.scope.pop(READ_CHANNEL_KEY, None)
        return results
