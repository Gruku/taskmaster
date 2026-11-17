# User intent: cut off clients that send headers too slowly, and pin uvicorn's connection limits, because uvicorn alone never times out a slow header; and when a request is refused before its body is read, let the client read the refusal instead of a reset, without letting it hold the connection.
"""The connection layer (design D2, D3a, D5): a header deadline on every request of a connection, a bounded
lingering close after early answers, plus the uvicorn options the daemon pins so no caller can loosen them."""
from __future__ import annotations

import logging

import h11
from uvicorn.protocols.http.h11_impl import H11Protocol

from .config import (DEFAULT_HEADER_TIMEOUT, DEFAULT_LINGER_BYTES, DEFAULT_LINGER_SECONDS, KEEP_ALIVE_SECONDS,
                     MAX_HEADER_BYTES, TRUSTED_PROXIES)

LOG = logging.getLogger("taskmaster.serve")


class DeadlineH11Protocol(H11Protocol):
    """uvicorn's h11 protocol with a deadline from connection open (or from the end of the last response)
    to complete request headers. uvicorn arms no timer on a fresh connection, and every trickled byte cancels
    its keep-alive timer, so without this a client sending one header line a second is never cut off."""

    header_timeout: float = DEFAULT_HEADER_TIMEOUT
    linger_seconds: float = DEFAULT_LINGER_SECONDS
    linger_bytes: int = DEFAULT_LINGER_BYTES
    _header_timer = None
    _raw_transport = None
    _lingering = False
    _linger_timer = None
    _lingered = 0

    def connection_made(self, transport) -> None:
        self._raw_transport = transport
        super().connection_made(_LingeringTransport(transport, self))
        self._arm()

    def data_received(self, data: bytes) -> None:
        if self._lingering:
            # Discarded: never parsed, never buffered, never handed to the app (D3a still holds).
            self._lingered += len(data)
            if self._lingered > self.linger_bytes:
                self._end_linger()
            return
        super().data_received(data)
        if self.conn.their_state is not h11.IDLE:
            self._disarm()

    def _close_requested(self) -> None:
        """uvicorn closes after an answer that must close. If the request body is still arriving (an early 401,
        413 or 429, D3a), closing now would make the stack reset the late bytes, and the reset can reach the
        client before it reads the answer. So: half-close, discard what still arrives up to linger_bytes or
        linger_seconds or EOF, then close."""
        transport = self._raw_transport
        if (self._lingering or transport.is_closing() or self.linger_seconds <= 0
                or self.conn.their_state is not h11.SEND_BODY or not transport.can_write_eof()):
            self._end_linger()
            return
        try:
            transport.write_eof()            # flushes the answer first, then FIN
            transport.resume_reading()       # uvicorn may have paused reading on a full body buffer
        except (OSError, RuntimeError):
            self._end_linger()
            return
        self._lingering = True
        self._lingered = 0
        self._linger_timer = self.loop.call_later(self.linger_seconds, self._end_linger)

    def _end_linger(self) -> None:
        if self._linger_timer is not None:
            self._linger_timer.cancel()
            self._linger_timer = None
        self._raw_transport.close()

    def on_response_complete(self) -> None:
        super().on_response_complete()
        if (self._header_timer is None and not self.transport.is_closing()
                and self.conn.their_state is h11.IDLE):
            self._arm()

    def connection_lost(self, exc) -> None:
        super().connection_lost(exc)
        self._disarm()
        if self._linger_timer is not None:
            self._linger_timer.cancel()
            self._linger_timer = None

    def _arm(self) -> None:
        self._disarm()
        self._header_timer = self.loop.call_later(self.header_timeout, self._expire)

    def _disarm(self) -> None:
        if self._header_timer is not None:
            self._header_timer.cancel()
            self._header_timer = None

    def _expire(self) -> None:
        self._header_timer = None
        if self.conn.their_state is h11.IDLE and not self.transport.is_closing():
            from .app import log_safe
            LOG.info("closed a connection from %s that sent no complete request headers within %ss",
                     log_safe(self.client[0] if self.client else "-"), self.header_timeout)
            self.transport.close()


class _LingeringTransport:
    """The transport uvicorn sees: close() goes through the protocol's lingering close; all else is the real one."""

    def __init__(self, transport, protocol: DeadlineH11Protocol):
        self._transport = transport
        self._protocol = protocol

    def close(self) -> None:
        self._protocol._close_requested()

    def is_closing(self) -> bool:
        return self._protocol._lingering or self._transport.is_closing()

    def __getattr__(self, name):
        return getattr(self._transport, name)


def deadline_protocol(header_timeout: float, linger_seconds: float = DEFAULT_LINGER_SECONDS,
                      linger_bytes: int = DEFAULT_LINGER_BYTES) -> type[DeadlineH11Protocol]:
    return type("DeadlineH11Protocol", (DeadlineH11Protocol,), {
        "header_timeout": float(header_timeout), "linger_seconds": float(linger_seconds),
        "linger_bytes": int(linger_bytes)})


HARDENED_KEYS = frozenset({"http", "ws", "limit_concurrency", "h11_max_incomplete_event_size", "proxy_headers",
                           "forwarded_allow_ips", "timeout_keep_alive"})


def hardened_uvicorn_options(config) -> dict:
    return {
        "http": deadline_protocol(config.header_timeout, config.linger_seconds, config.linger_bytes),
        "ws": "none",                                       # websockets is installed; never accept an upgrade
        "limit_concurrency": config.max_connections,
        "h11_max_incomplete_event_size": MAX_HEADER_BYTES,
        "proxy_headers": True,
        "forwarded_allow_ips": list(TRUSTED_PROXIES),       # explicit, so FORWARDED_ALLOW_IPS cannot widen it
        "timeout_keep_alive": KEEP_ALIVE_SECONDS,
    }
