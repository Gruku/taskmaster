# User intent: a teammate's merge hook sends its merge stamp to the team server as a command, over one small authenticated
# write route behind every 001/006 layer. The server only compares and records; whether a commit is older than another
# is the teammate's clone's answer, and the host never runs a teammate's Git (team-server-008 D5, D6, D9).
"""`POST /write/v1/merge-stamp` for `build_app(routes=...)`, beside the read API."""
from __future__ import annotations

import json
from datetime import datetime
import re

from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

from taskmaster import untrusted
from taskmaster.native_routing import hook_reads

from .config import WRITE_PREFIX
from .read_api import ReadApi, Refused, _text_of, valid_clone, valid_ref
from .sanitize import scrub
from .sessions import HOOK_CHANNEL_KEY, HOOK_CLONE_KEY

PREFIX = WRITE_PREFIX
VIEWS = ("merge-stamp",)
WRITE_MAX_BYTES = 4 * 1024
STAMP_KEYS = frozenset({"branch", "target", "sha", "merged_at", "clone", "checked"})
SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
MERGED_AT_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}")
AGAINST_RE = re.compile(r"[!-~]{1,64}")
RECORDED_PREFIX = "Recorded merge for rung"


def _sha(name: str, value) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise Refused(400, "bad-request", f"{name} is not a commit id")
    return value


def _real_minute(text: str) -> bool:
    try:
        datetime.strptime(text, "%Y-%m-%dT%H:%M")
    except ValueError:
        return False
    return True


def _against(value) -> str:
    """`checked.against` echoes the stored record, which may be any text a member recorded; it is only compared."""
    if not isinstance(value, str) or not AGAINST_RE.fullmatch(value):
        raise Refused(400, "bad-request", "checked.against is not a recorded commit")
    return value


def _shown(current):
    """A stored merge record can hold any text a member recorded (record_merge does not shape `sha`); only a hex commit and
    a UTC minute leave in a response an agent reads (027 D8). The compare-and-set uses the stored value, not this copy."""
    if current is None:
        return None
    sha, at = current.get("merge_commit", ""), current.get("merged_at", "")
    return {"merge_commit": sha if re.fullmatch(r"[0-9a-fA-F]{7,64}", sha) else "(not a commit id)",
            "merged_at": at if MERGED_AT_RE.fullmatch(at) else ""}


class WriteApi(ReadApi):
    def routes(self) -> list:
        return [
            Route(f"{PREFIX}/merge-stamp", self.merge_stamp, methods=["POST"]),
            Route("/write/{rest:path}", self.unknown, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
        ]

    # -- the body ---------------------------------------------------------------------------------

    async def _stamp_body(self, request) -> dict:
        length = request.headers.get("content-length")
        if request.headers.get("transfer-encoding") or length is None:
            raise Refused(411, "bad-request", "a merge stamp needs a Content-Length")
        try:
            size = int(length)
        except ValueError:
            raise Refused(400, "bad-request", "Content-Length is not a number") from None
        if size > WRITE_MAX_BYTES:
            raise Refused(413, "bad-request", f"a merge stamp is at most {WRITE_MAX_BYTES} bytes")
        try:
            body = json.loads((await request.body()).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise Refused(400, "bad-request", "the body is not JSON") from None
        if not isinstance(body, dict):
            raise Refused(400, "bad-request", "the body must be a JSON object")
        if set(body) - STAMP_KEYS:
            raise Refused(400, "bad-request", "the body holds a key a merge stamp does not take")
        merged_at = body.get("merged_at")
        if not isinstance(merged_at, str) or not MERGED_AT_RE.fullmatch(merged_at) or not _real_minute(merged_at):
            raise Refused(400, "bad-request", "merged_at is not a UTC minute (YYYY-MM-DDTHH:MM)")
        checked = body.get("checked")
        if checked is not None and (not isinstance(checked, dict) or set(checked) != {"against"}):
            raise Refused(400, "bad-request", "checked must be {\"against\": <commit>}")
        return {"branch": valid_ref("branch", body.get("branch")), "target": valid_ref("target", body.get("target")),
                "sha": _sha("sha", body.get("sha")), "merged_at": merged_at,
                "clone": valid_clone(body.get("clone")),
                "against": _against(checked["against"]) if checked is not None else None}

    # -- applying it ------------------------------------------------------------------------------

    async def _plan(self, principal, stamp):
        def read(connection, deadline):
            return hook_reads.merge_stamp_plan(connection, stamp["branch"], stamp["target"], principal,
                                               clone=stamp["clone"] or None)
        return await run_in_threadpool(self._read_store, read)

    async def _apply(self, request, stamp, plan, expect_commit: str) -> str:
        """The tool call: `backlog_record_merge` with the compare-and-set, as `<handle>@hook`.

        Through the middleware stack (005's tool gate, 003's session, 004's scoping, 006's sanitizer) exactly as over MCP.
        Returns the tool's text; a refusal raised as an exception is its text too."""
        from fastmcp.exceptions import ToolError
        request.scope[HOOK_CHANNEL_KEY] = True
        request.scope[HOOK_CLONE_KEY] = stamp["clone"]
        arguments = {"task_id": plan["task_id"], "rung": plan["rung"], "sha": stamp["sha"],
                     "merged_at": stamp["merged_at"], "expect_commit": expect_commit}
        try:
            result = await self.server.call_tool("backlog_record_merge", arguments, run_middleware=True)
            return _text_of(result)
        except ToolError as exc:
            return str(exc)
        except Exception as exc:  # noqa: BLE001 - validation errors and the like, already scrubbed
            return f"Error: {scrub(str(exc), self.root).text}"
        finally:
            request.scope.pop(HOOK_CHANNEL_KEY, None)
            request.scope.pop(HOOK_CLONE_KEY, None)

    async def _outcome(self, request, principal, stamp):
        """`(data, revision, store_id)`: the server compares; it never decides ancestry and never runs Git (D6, D9)."""
        plan, revision, store_id = await self._plan(principal, stamp)
        if plan["ambiguous"]:
            return {"outcome": "ambiguous", "candidates": plan["candidates"]}, revision, store_id
        if plan["task_id"] is None:
            return {"outcome": "no-task"}, revision, store_id
        current = plan["current"]
        where = {"task_id": plan["task_id"], "rung": plan["rung"]}
        if current is not None and current["merge_commit"] == stamp["sha"]:
            return {"outcome": "already", **where}, revision, store_id
        if current is not None and stamp["against"] != current["merge_commit"]:
            return {"outcome": "check", **where, "current": _shown(current)}, revision, store_id
        text = await self._apply(request, stamp, plan, current["merge_commit"] if current else "")
        if untrusted.unframe(text).startswith(RECORDED_PREFIX):  # the tool text arrives framed (027); the refusal below stays framed
            return {"outcome": "recorded", **where}, revision, store_id
        # Not recorded. Either a stamp landed between the plan and the write (the compare-and-set refused it), or the
        # tool refused for its own reason: read the plan once more and tell the two apart.
        again, revision, store_id = await self._plan(principal, stamp)
        if again["current"] is not None and again["current"] != current and not again["ambiguous"]                 and again["task_id"] == plan["task_id"]:
            return {"outcome": "check", "task_id": again["task_id"], "rung": again["rung"],
                    "current": _shown(again["current"])}, revision, store_id
        return {"outcome": "refused", "message": scrub(text, self.root).text}, revision, store_id

    async def merge_stamp(self, request):
        async def handler():
            principal = self._principal(request)
            stamp = await self._stamp_body(request)
            data, revision, store_id = await self._outcome(request, principal, stamp)
            return self._envelope("merge-stamp", data, revision, store_id)
        return await self._guarded(request, "merge-stamp", handler)

    async def unknown(self, request):
        rest = request.path_params.get("rest", "")
        version = rest.split("/", 1)[0]
        if version != "v1":
            return self._error(request, 404, "unsupported-version", "write API is not served", supported=[1])
        view = rest.split("/", 1)[1] if "/" in rest else ""
        if view in VIEWS:
            return self._error(request, 405, "method-not-allowed", f"{view} takes POST", {"Allow": "POST"})
        return self._error(request, 404, "unknown-view", "no such write view", views=list(VIEWS))
