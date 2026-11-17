# User intent: one stdlib-only reader and writer for the git-tracked server marker, so every hook, CLI and
# server process agrees on whether a project lives on a Taskmaster server and never reads a stray local store
# by accident; a clone that has once seen the marker stays in server mode through old checkouts (team-server-009).
"""The server-mode marker (`.taskmaster-server.json` at the repo root) and the clone's latch.

Standard library only: hooks import this under the system interpreter, without the venv
(the same rule as `taskmaster.root`). Design: docs/specs/2026-11-01-team-server-009-split-brain-fence-design.md
§4-§5.

Public API (shared with team-server-007): `read_marker(root) -> Marker | None`, `ServerConfigError`,
`server_mode(root) -> Marker | None`.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

MARKER_NAME = ".taskmaster-server.json"
LATCH_NAME = "taskmaster-server.json"          # inside <repo root>/.git/
FORMAT = 1
MAX_BYTES = 4096
MAX_URL = 2048
_KEYS = frozenset({"format", "host_id", "mode", "url"})
_HOST_ID = re.compile(r"[0-9a-f]{32}")


class ServerConfigError(Exception):
    """A marker (or latch) exists but can't be used. Callers fail closed: never solo by accident."""

    def __init__(self, path, reason: str):
        self.path = Path(path)
        self.reason = reason
        super().__init__(f"the server marker at {self.path} can't be used ({reason})")


@dataclass(frozen=True)
class Marker:
    url: str
    host_id: str
    mode: str = "server"
    format: int = FORMAT
    source: str = field(default="marker", compare=False)     # "marker" or "latch": where it was read


def marker_path(root) -> Path:
    return Path(root) / MARKER_NAME


def latch_path(root) -> Path:
    return Path(root) / ".git" / LATCH_NAME


def new_host_id() -> str:
    return secrets.token_hex(16)


# -- reading ---------------------------------------------------------------------------------------

def read_bounded(path) -> bytes:
    """The bytes of a regular file of at most MAX_BYTES, never following a link and never blocking.

    A symlink (git tracks them), FIFO, directory or device is refused without being opened, so a
    committed link to /dev/zero or a huge file can't hang or bloat a tool call. FileNotFoundError
    passes through for a path that vanished; everything else is ServerConfigError.
    """
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ServerConfigError(path, f"cannot stat it: {exc.strerror or exc}") from None
    if not stat.S_ISREG(info.st_mode):
        raise ServerConfigError(path, "not a regular file")
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_BINARY", 0))
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ServerConfigError(path, f"cannot open it: {exc.strerror or exc}") from None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ServerConfigError(path, "not a regular file")
        chunks, total = [], 0
        while total <= MAX_BYTES:
            chunk = os.read(descriptor, MAX_BYTES + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    except OSError as exc:
        raise ServerConfigError(path, f"cannot read it: {exc.strerror or exc}") from None
    finally:
        os.close(descriptor)
    if total > MAX_BYTES:
        raise ServerConfigError(path, f"larger than {MAX_BYTES} bytes")
    return b"".join(chunks)


def url_problem(url) -> str | None:
    """Why `url` can't be a server URL, or None."""
    if not isinstance(url, str) or not url:
        return "url must be a non-empty string"
    if len(url) > MAX_URL:
        return f"url is longer than {MAX_URL} characters"
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        return "url contains whitespace or control characters"
    if "?" in url or "#" in url:
        return "url must not carry a query or fragment"
    try:
        parts = urlsplit(url)
        parts.port
    except ValueError as exc:
        return f"url is not valid ({exc})"
    if parts.scheme not in ("https", "http"):
        return "url must start with https:// (or http:// to a loopback address)"
    if "@" in parts.netloc:
        return "url must not carry userinfo"
    host = parts.hostname
    if not host:
        return "url has no host"
    if parts.scheme == "http":
        try:
            loopback = ipaddress.ip_address(host).is_loopback      # a literal only, never the name localhost
        except ValueError:
            loopback = False
        if not loopback:
            return "url must be https://, or http:// to a loopback literal address"
    return None


def parse(raw: bytes, path=MARKER_NAME, *, latch: bool = False) -> Marker:
    """The marker in `raw`, validated; ServerConfigError naming the field otherwise.

    A latch is the marker's own keys plus `first_seen` (design section 5)."""
    if len(raw) > MAX_BYTES:
        raise ServerConfigError(path, f"larger than {MAX_BYTES} bytes")
    try:
        document = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise ServerConfigError(path, "not UTF-8") from None
    except ValueError as exc:
        raise ServerConfigError(path, f"not JSON: {exc}") from None
    if not isinstance(document, dict):
        raise ServerConfigError(path, "not a JSON object")
    version = document.get("format")
    if type(version) is not int:
        raise ServerConfigError(path, "format must be an integer")
    if version > FORMAT:
        raise ServerConfigError(path, f"format {version} is newer than this build understands; "
                                      "update the taskmaster plugin")
    if version < FORMAT:
        raise ServerConfigError(path, f"format {version} is not a known marker format")
    extra = set(document) - _KEYS - ({"first_seen"} if latch else set())
    missing = _KEYS - set(document)
    if extra or missing:
        detail = [f"unknown key: {name}" for name in sorted(map(str, extra))]
        detail += [f"missing key: {name}" for name in sorted(missing)]
        raise ServerConfigError(path, f"keys must be exactly {', '.join(sorted(_KEYS))} ({'; '.join(detail)})")
    if document["mode"] != "server":
        raise ServerConfigError(path, "mode must be \"server\"")
    problem = url_problem(document["url"])
    if problem:
        raise ServerConfigError(path, problem)
    host_id = document["host_id"]
    if not isinstance(host_id, str) or not _HOST_ID.fullmatch(host_id):
        raise ServerConfigError(path, "host_id must be 32 lowercase hex characters")
    return Marker(url=document["url"], host_id=host_id, source="latch" if latch else "marker")


def render(marker: Marker) -> str:
    """The canonical text: sorted keys, two-space indent, LF, trailing newline."""
    return json.dumps({"format": marker.format, "host_id": marker.host_id, "mode": marker.mode,
                       "url": marker.url}, sort_keys=True, indent=2) + "\n"


def read_marker(root) -> Marker | None:
    """The marker at `root`, None only when nothing at all exists at its path."""
    path = marker_path(root)
    if not os.path.lexists(path):
        return None
    try:
        raw = read_bounded(path)
    except FileNotFoundError:
        return None
    return parse(raw, path)


def read_latch(root) -> Marker | None:
    """The marker this clone has latched at `root`, None when there is no latch."""
    path = latch_path(root)
    if not os.path.lexists(path):
        return None
    try:
        raw = read_bounded(path)
    except FileNotFoundError:
        return None
    return parse(raw, path, latch=True)


def server_mode(root) -> Marker | None:
    """The marker, or the latched one when the marker is absent (an older checkout); None in solo."""
    return read_marker(root) or read_latch(root)


# -- writing ---------------------------------------------------------------------------------------

def _publish(path: Path, text: str, *, exclusive: bool) -> bool:
    """Write `text` to `path` through a temp file; `exclusive` never replaces an existing file."""
    temp = path.with_name(f"{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        with open(temp, "x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            try:
                os.link(temp, path)
            except FileExistsError:
                return False
        else:
            os.replace(temp, path)
        return True
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def write_marker(root, url: str, host_id: str) -> Path:
    """Validate and atomically write the canonical marker at `root`. Never commits."""
    marker = Marker(url=url, host_id=host_id)
    text = render(marker)
    parse(text.encode("utf-8"), marker_path(root))
    path = marker_path(root)
    _publish(path, text, exclusive=False)
    return path


def record_latch(root, marker: Marker) -> bool:
    """Latch `marker` in `<root>/.git/` once; best effort, never raises. True when written now."""
    try:
        if not (Path(root) / ".git").is_dir():
            return False
        path = latch_path(root)
        if os.path.lexists(path):
            return False
        body = json.loads(render(marker))
        body["first_seen"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        text = json.dumps(body, sort_keys=True, indent=2) + "\n"
        return _publish(path, text, exclusive=True)
    except Exception:  # noqa: BLE001 -- a latch is a safety net; failing to write it never fails a call
        return False


def clear_latch(root) -> bool:
    """Remove the latch; True when one was removed."""
    try:
        latch_path(root).unlink()
    except FileNotFoundError:
        return False
    return True


def publish_marker(root, url: str, host_id: str) -> Path:
    """Write the marker at `root` and make this clone's latch agree with it (replacing a stale latch)."""
    path = write_marker(root, url, host_id)
    marker = read_marker(root)
    try:
        latched = read_latch(root)
    except ServerConfigError:
        latched = None
    if latched != marker:
        clear_latch(root)
        record_latch(root, marker)
    return path
