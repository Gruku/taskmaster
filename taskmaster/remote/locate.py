# User intent: turn a clone's server marker into the one canonical server identity the key file, the cache and
# the read URL all hang off, so `https://Host:443/mcp` and `https://host/mcp` are one server (team-server-007 D8).
"""Canonical origin, read base and server id for the server a clone's marker names."""
from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

from taskmaster import server_marker

READ_PREFIX = "/read/v1/"
WRITE_PREFIX = "/write/v1/"
DEFAULT_PORTS = {"https": 443, "http": 80}


@dataclass(frozen=True)
class ServerRef:
    url: str            # the MCP URL as the marker names it
    origin: str         # canonical: scheme://host[:port]
    read_base: str      # origin + /read/v1/
    server_id: str      # sha256(origin)[:16]

    @property
    def loopback(self) -> bool:
        return is_loopback_origin(self.origin)

    @property
    def write_base(self) -> str:
        return self.origin + WRITE_PREFIX


def _host_of(origin: str) -> str:
    host = urlsplit(origin).hostname or ""
    return host


def is_loopback_literal(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_loopback_origin(origin: str) -> bool:
    return is_loopback_literal(_host_of(origin))


def canonical_origin(url: str) -> str:
    """Lowercase scheme and host, IDNA host, default port dropped, no path. ValueError when unusable."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    port = parts.port
    if scheme not in DEFAULT_PORTS or not host:
        raise ValueError(f"not an http(s) URL: {url!r}")
    if parts.username or parts.password:
        raise ValueError("the URL may not carry a user or password")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is None:
        try:
            host = host.encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise ValueError(f"not a valid host name: {host!r}") from None
    if scheme == "http" and not (literal is not None and literal.is_loopback):
        raise ValueError("plain http is allowed only to a loopback address (127.0.0.0/8, ::1)")
    shown = f"[{literal.compressed}]" if literal is not None and literal.version == 6 else \
        (literal.compressed if literal is not None else host)
    return f"{scheme}://{shown}" + ("" if port in (None, DEFAULT_PORTS[scheme]) else f":{port}")


def server_id(origin: str) -> str:
    return hashlib.sha256(origin.encode("ascii")).hexdigest()[:16]


def server_ref(url: str) -> ServerRef:
    origin = canonical_origin(url)
    return ServerRef(url, origin, origin + READ_PREFIX, server_id(origin))


def locate(root) -> ServerRef | None:
    """The server this clone belongs to (009's `server_mode`: marker, else latch), or None in a solo clone.

    Raises `server_marker.ServerConfigError` for a marker or latch that can't be read or used.
    """
    marker = server_marker.server_mode(root)
    if marker is None:
        return None
    try:
        return server_ref(marker.url)
    except ValueError as exc:
        raise server_marker.ServerConfigError(server_marker.marker_path(root), str(exc)) from None
