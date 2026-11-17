# User intent: remote callers never see host paths, SQL or tracebacks in an error; the operator finds the detail in serve.log by its ref.
"""Error sanitizing at the tool chokepoint (SanitizeErrors) and the coordinator chokepoint (design D9)."""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from fastmcp.exceptions import NotFoundError, ToolError, ValidationError
from fastmcp.server.middleware import Middleware
from mcp.types import TextContent

from taskmaster.admission import UnsupportedStoreError
from taskmaster.coordinator.protocol import CoordinatorStopping, HandshakeError, ServiceUnavailable
from taskmaster.native import contracts
from taskmaster.team_identity import IdentityRefused

from .app import REQUEST_ID_KEY

SQL_MARKERS = ("sqlite", "constraint failed", "no such table", "no such column", "syntax error",
               "database is locked", "database disk image", "disk i/o error", "datatype mismatch",
               "readonly database", "unable to open database", "traceback (most recent call last)")
SQL_EXEMPT_TOOLS = frozenset({"backlog_query"})
LOG = logging.getLogger("taskmaster.serve")

_WINDOWS_PATH = re.compile(r"(?<![\w])[A-Za-z]:[\\/][^\s'\"`,;)\]]*")
# A directory segment may hold single spaces ("/home/u/My Project/x"); it must still end in "/", so a space
# before plain words after a path is not swallowed.
_SEGMENT = r"[^\s'\"`,;)\]/]+(?: [^\s'\"`,;)\]/]+)*"
_POSIX_PATH = re.compile(r"(?<![\w:/.<>])/(?:" + _SEGMENT + r"/)+[^\s'\"`,;)\]]*")


@dataclass(frozen=True)
class Scrubbed:
    text: str
    changed: bool
    internal: bool


def new_ref() -> str:
    return secrets.token_hex(6)


def _root_forms(root: Path) -> list[str]:
    forms = {str(root)}
    try:
        forms.add(str(root.resolve()))
    except OSError:
        pass
    forms |= {form.replace("\\", "/") for form in forms}
    forms |= {form.replace("/", "\\") for form in forms}
    return sorted((form.rstrip("/\\") for form in forms if len(form.rstrip("/\\")) > 1), key=len, reverse=True)


def scrub(text: str, root: Path) -> Scrubbed:
    original = text
    flags = re.IGNORECASE if os.name == "nt" else 0
    for form in _root_forms(Path(root)):
        text = re.sub(re.escape(form), "<root>", text, flags=flags)
    text = _WINDOWS_PATH.sub("<path>", text)
    text = _POSIX_PATH.sub("<path>", text)
    lowered = original.lower()
    return Scrubbed(text, text != original, any(marker in lowered for marker in SQL_MARKERS))


def internal_message(what: str, ref: str) -> str:
    return f"internal error in {what} (ref {ref}); the operator can look it up in the server log"


def _principal_handle() -> str:
    from .app import log_safe
    from .auth import current_principal
    try:
        principal = current_principal()
    except Exception:  # noqa: BLE001 - a log label only
        principal = None
    return log_safe(principal.handle if principal else "-")


class SanitizeErrors(Middleware):
    """Over HTTP only: a tool's exception becomes an internal error with a ref; error text and JSON error
    fields lose host paths, and SQL or traceback text becomes an internal error. Solo stdio and in-memory
    calls pass through untouched."""

    def __init__(self, root: Path):
        self.root = Path(root)

    async def on_call_tool(self, context, call_next):
        from fastmcp.server.dependencies import get_http_request
        try:
            request = get_http_request()
        except RuntimeError:
            return await call_next(context)
        ref = request.scope.get(REQUEST_ID_KEY) or new_ref()
        tool = getattr(context.message, "name", "?")
        try:
            result = await call_next(context)
        except (ValidationError, NotFoundError) as exc:
            # Bad arguments or an unknown tool: the text echoes only the caller's own input.
            raise type(exc)(self._error_text(str(exc), tool, ref, prefix="")) from None
        except ToolError as exc:
            if exc.__cause__ is None:
                raise ToolError(self._error_text(str(exc), tool, ref, prefix="Error: ")) from None
            self._log_exception(exc.__cause__, tool, ref)
            raise ToolError("Error: " + internal_message(tool, ref)) from None
        except Exception as exc:
            self._log_exception(exc, tool, ref)
            raise ToolError("Error: " + internal_message(tool, ref)) from None
        return self._scrub_result(result, tool, ref)

    def _log_exception(self, exc, tool, ref) -> None:
        from .app import log_safe
        LOG.error("tool error ref=%s principal=%s tool=%s type=%s", ref, _principal_handle(), log_safe(tool),
                  type(exc).__name__, exc_info=exc)

    def _error_text(self, text: str, tool: str, ref: str, *, prefix: str) -> str:
        scrubbed = scrub(text, self.root)
        if scrubbed.internal and tool not in SQL_EXEMPT_TOOLS:
            out = prefix + internal_message(tool, ref)
        else:
            out = scrubbed.text
        if out != text:
            from .app import log_safe
            LOG.info("scrubbed tool error ref=%s tool=%s original=%r", ref, log_safe(tool), text)
        return out

    def _scrub_text(self, text, tool, ref):
        if not isinstance(text, str):
            return text
        if text.startswith("Error:"):
            return self._error_text(text, tool, ref, prefix="Error: ")
        if not text.lstrip().startswith("{"):
            return text
        try:
            obj = json.loads(text)
        except ValueError:
            return text
        if not isinstance(obj, dict) or not isinstance(obj.get("error"), str):
            return text
        error = self._error_text(obj["error"], tool, ref, prefix="")
        if error == obj["error"]:
            return text
        return json.dumps({**obj, "error": error}, ensure_ascii=False)

    def _scrub_result(self, result, tool, ref):
        content = getattr(result, "content", None)
        if isinstance(content, list):
            for i, block in enumerate(content):
                if isinstance(block, TextContent):
                    text = self._scrub_text(block.text, tool, ref)
                    if text != block.text:
                        content[i] = block.model_copy(update={"text": text})
        structured = getattr(result, "structured_content", None)
        if isinstance(structured, dict) and isinstance(structured.get("result"), str):
            text = self._scrub_text(structured["result"], tool, ref)
            if text != structured["result"]:
                result.structured_content = {**structured, "result": text}
        elif isinstance(structured, dict) and isinstance(structured.get("error"), str):
            # A dict-returning tool's own error field (006 review M3).
            error = self._error_text(structured["error"], tool, ref, prefix="")
            if error != structured["error"]:
                result.structured_content = {**structured, "error": error}
        return result


DOMAIN_ERRORS: tuple[type, ...] = (contracts.Conflict, contracts.CancelledBeforeExecution, ValueError, KeyError,
                                   HandshakeError, UnsupportedStoreError, CoordinatorStopping, IdentityRefused,
                                   ServiceUnavailable)
_DOMAIN_NAMES = frozenset(t.__name__ for t in DOMAIN_ERRORS)


def coordinator_public_error(exc, root, log) -> dict:
    """The coordinator's IPC error answer in server mode: domain errors keep their type, path-scrubbed;
    anything else (or SQL text) is logged with a ref and answered as ServiceUnavailable."""
    text = str(exc)
    scrubbed = scrub(text, root)
    if type(exc).__name__ in _DOMAIN_NAMES and not scrubbed.internal:
        return {"error": scrubbed.text, "type": type(exc).__name__}
    ref = "c" + new_ref()
    log.error("coordinator error ref=%s type=%s", ref, type(exc).__name__, exc_info=exc)
    return {"error": internal_message("the coordinator", ref), "type": "ServiceUnavailable"}
