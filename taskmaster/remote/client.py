# User intent: one way for a teammate's hooks and mods to read the team server: HTTPS only, the key sent only to
# its own origin and never through a redirect, a whole-request deadline that DNS, TLS or a dripping peer can't
# outlast, and every failure reported as one named kind the caller decides on (team-server-007 D10, review I2).
"""`Client.read(view, params)` against `/read/v1/`, through the cache."""
from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .cache import CACHE_POLICY, Cache
from .keys import Key
from .locate import ServerRef

VIEWS = frozenset(CACHE_POLICY)
WRITE_VIEWS = ("merge-stamp",)
BUDGETS = {"paths": 2.0, "merge-gate": 5.0, "merge-target": 4.0, "tools": 10.0, "meta": 3.0, "merge-stamp": 6.0}
HOOK_HARD_CAP = 8.0             # a hook answers before the host's 10 s kill (D10)
REPLY_CAP = {"tools": 4 * 1024 * 1024}
VIEW_REPLY_CAP = 256 * 1024
CHUNK = 64 * 1024
BUSY_RETRY_MAX = 2.0
KINDS = ("config", "no-key", "unreachable", "refused", "busy", "version", "server", "bad-response", "bad-request")


class ReadUnavailable(Exception):
    def __init__(self, kind: str, message: str, retry_after: float | None = None):
        super().__init__(f"{kind}: {message}")
        self.kind, self.message, self.retry_after = kind, message, retry_after


@dataclass(frozen=True)
class ReadResult:
    data: dict
    revision: int
    store_id: str
    source: str          # "server", "cache" or "stale"
    age_s: float


def _plugin_version() -> str:
    try:
        manifest = Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"
        return str(json.loads(manifest.read_text(encoding="utf-8")).get("version", "0"))
    except (OSError, ValueError):
        return "0"


USER_AGENT = f"taskmaster-remote/{_plugin_version()}"


def hook_deadline(budget: float, *, started: float, monotonic=time.monotonic) -> float:
    """An absolute monotonic deadline: the view's budget, but never past the hook's own hard cap."""
    return min(monotonic() + budget, started + HOOK_HARD_CAP)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib copies Authorization onto a redirected request, even to another host (Step 0 spike c)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener(loopback: bool):
    import ssl
    handlers = [_NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context())]
    if loopback:
        handlers.insert(0, urllib.request.ProxyHandler({}))    # never carry the key to a proxy in plaintext
    return urllib.request.build_opener(*handlers)


def _status_kind(status: int) -> str:
    if 300 <= status < 400:
        return "bad-response"
    if status in (401, 403):
        return "refused"
    if status == 404:
        return "version"
    if status == 408:
        return "unreachable"
    if status == 429:
        return "busy"
    if status in (400, 411, 413, 421, 431):
        return "bad-request"
    if status >= 500:
        return "server"
    return "bad-response"


class Client:
    def __init__(self, server: ServerRef, key: Key, cache: Cache | None = None, *, log=None,
                 monotonic=time.monotonic, sleep=time.sleep):
        self.server, self.key, self.cache = server, key, cache
        self._log = log or (lambda text: None)
        self._monotonic, self._sleep = monotonic, sleep

    # -- public -----------------------------------------------------------------------------------

    def read(self, view: str, params: dict, *, deadline: float | None = None, fresh: bool = False,
             retry_busy: bool = False) -> ReadResult:
        if view not in VIEWS:
            raise ValueError(f"unknown view {view!r}")
        policy = CACHE_POLICY[view]
        deadline = deadline if deadline is not None else self._monotonic() + BUDGETS[view]
        cache = self.cache if self.cache is not None and self.cache.enabled else None
        if cache is not None and not fresh:
            hit = cache.fresh(view, params)
            if hit is not None:
                return ReadResult(hit.data, hit.revision, hit.store_id, "cache", hit.age)
        if cache is not None and policy.negative:
            if cache.refused(self.key.marker):
                raise ReadUnavailable("refused", "the server refused this key moments ago; not asking again yet")
            since = cache.down_since()
            if since is not None:
                return self._stale_or_raise(view, params, ReadUnavailable(
                    "unreachable", f"server unreachable {max(0, int(time.time() - since))} s ago"))
        try:
            envelope = self._fetch(view, params, deadline)
        except ReadUnavailable as failure:
            if failure.kind == "busy" and retry_busy and failure.retry_after is not None and \
                    failure.retry_after <= BUSY_RETRY_MAX and self._monotonic() + failure.retry_after < deadline:
                self._sleep(failure.retry_after)
                try:
                    envelope = self._fetch(view, params, deadline)
                except ReadUnavailable as again:
                    failure = again
                else:
                    return self._stored(view, params, envelope, cache)
            if cache is not None:
                if failure.kind == "unreachable":
                    cache.mark_down()
                elif failure.kind == "refused" and cache.note_refused(self.key.marker):
                    failure = ReadUnavailable("refused", failure.message + " (refused twice; pausing reads)")
            return self._stale_or_raise(view, params, failure)
        return self._stored(view, params, envelope, cache)

    def write(self, view: str, body: dict, *, deadline: float | None = None) -> ReadResult:
        """POST `body` to `/write/v1/<view>`. Never touches the cache or its negative entries (a write is not a read)."""
        if view not in WRITE_VIEWS:
            raise ValueError(f"unknown write view {view!r}")
        deadline = deadline if deadline is not None else self._monotonic() + BUDGETS[view]
        envelope = self._fetch(view, body, deadline)
        return ReadResult(envelope["data"], envelope["revision"], envelope["store_id"], "server", 0.0)

    # -- internals --------------------------------------------------------------------------------

    def _stored(self, view, params, envelope, cache) -> ReadResult:
        if cache is not None:
            cache.note_success()
            cache.put(view, params, envelope)
        return ReadResult(envelope["data"], envelope["revision"], envelope["store_id"], "server", 0.0)

    def _stale_or_raise(self, view, params, failure: ReadUnavailable) -> ReadResult:
        cache = self.cache if self.cache is not None and self.cache.enabled else None
        entry = cache.stale(view, params, failure.kind) if cache is not None else None
        if entry is None:
            raise failure
        self._log(f"serving a {int(entry.age)} s old {view} reply: {failure}")
        return ReadResult(entry.data, entry.revision, entry.store_id, "stale", entry.age)

    def _request(self, view: str, params: dict) -> urllib.request.Request:
        headers = {"Authorization": f"Bearer {self.key.value}", "Accept": "application/json",
                   "User-Agent": USER_AGENT}
        if view in WRITE_VIEWS:
            body = json.dumps(params, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
            return urllib.request.Request(self.server.write_base + view, data=body, headers=headers, method="POST")
        if view == "tools":
            body = json.dumps({"calls": params.get("calls", [])}, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
            return urllib.request.Request(self.server.read_base + view, data=body, headers=headers, method="POST")
        query = urllib.parse.urlencode(params)
        url = self.server.read_base + view + (f"?{query}" if query else "")
        return urllib.request.Request(url, headers=headers, method="GET")

    def _fetch(self, view: str, params: dict, deadline: float) -> dict:
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise ReadUnavailable("unreachable", "no time left to ask the server")
        request = self._request(view, params)
        cap = REPLY_CAP.get(view, VIEW_REPLY_CAP)
        outcome: dict = {}

        def exchange():
            try:
                outcome["value"] = self._exchange(request, cap, deadline)
            except BaseException as exc:  # noqa: BLE001 - handed to the caller's thread
                outcome["error"] = exc

        worker = threading.Thread(target=exchange, name="taskmaster-remote-read", daemon=True)
        worker.start()
        worker.join(max(0.0, deadline - self._monotonic()))
        if worker.is_alive():
            # DNS, TLS and a dripping peer all end here; the thread is abandoned (the process exits soon).
            raise ReadUnavailable("unreachable", f"no answer within {remaining:.1f} s")
        if "error" in outcome:
            error = outcome["error"]
            if isinstance(error, ReadUnavailable):
                raise error
            raise ReadUnavailable("unreachable", f"{type(error).__name__}: {error}")
        status, headers, body = outcome["value"]
        return self._envelope(view, status, headers, body)

    def _exchange(self, request, cap: int, deadline: float):
        timeout = max(0.05, deadline - self._monotonic())
        try:
            response = _opener(self.server.loopback).open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ReadUnavailable("unreachable", str(reason)) from None
        try:
            status = response.status if hasattr(response, "status") else response.code
            headers = {k.lower(): v for k, v in response.headers.items()}
            chunks, size = [], 0
            while True:
                if self._monotonic() > deadline:
                    raise ReadUnavailable("unreachable", "the reply did not finish in time")
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > cap:
                    raise ReadUnavailable("bad-response", f"reply larger than {cap} bytes")
                chunks.append(chunk)
            return status, headers, b"".join(chunks)
        except (OSError, ValueError) as exc:
            raise ReadUnavailable("unreachable", f"reading the reply failed: {exc}") from None
        finally:
            try:
                response.close()
            except Exception:  # noqa: BLE001
                pass

    def _envelope(self, view: str, status: int, headers: dict, body: bytes) -> dict:
        is_json = headers.get("content-type", "").split(";")[0].strip().lower() == "application/json"
        parsed = None
        if is_json:
            try:
                parsed = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                parsed = None
        if status != 200:
            kind = _status_kind(status)
            message = f"HTTP {status}"
            error = parsed.get("error") if isinstance(parsed, dict) else None
            if isinstance(error, dict):
                message = f"{message}: {error.get('message') or error.get('code')}"
                if error.get("ref"):
                    message += f" (ref {error['ref']})"
            elif isinstance(error, str):
                message = f"{message}: {error}"
            if status == 404 and not (isinstance(error, dict) and error.get("code") in ("unsupported-version",
                                                                                         "unknown-view")):
                message = "the server has no read API v1 (older than team-server-007?); update the server"
            retry = None
            if kind == "busy":
                try:
                    retry = float(headers.get("retry-after", ""))
                except ValueError:
                    retry = None
                if retry is not None and not math.isfinite(retry):
                    retry = None
                elif retry is not None:
                    retry = max(0.0, retry)       # a negative Retry-After would make time.sleep raise (review M1)
            raise ReadUnavailable(kind, message, retry)
        if not is_json or not isinstance(parsed, dict):
            raise ReadUnavailable("bad-response", "the reply is not JSON")
        if parsed.get("v") != 1 or parsed.get("view") != view or not isinstance(parsed.get("data"), dict) \
                or not isinstance(parsed.get("revision"), int) or not isinstance(parsed.get("store_id"), str):
            raise ReadUnavailable("bad-response", "the reply is not a v1 envelope for this view")
        return parsed
