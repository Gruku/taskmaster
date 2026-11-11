"""User intent: the local viewer must always land on a port a browser will open. Firefox
refused "This address is restricted" on :6566 because the fallback took any OS-assigned
port, and Windows' dynamic range here starts at 1024.
"""
from __future__ import annotations

import socket

import pytest

from taskmaster import backlog_server as bs


class _Fake:
    def __init__(self, port):
        self.server_address = ("127.0.0.1", port)
        self.closed = False

    def server_close(self):
        self.closed = True


def _maker(taken=(), os_ports=()):
    """A bind stand-in: ports in `taken` raise; port 0 hands out `os_ports` in order."""
    handed = iter(os_ports)
    made = []

    def make(port):
        if port in taken:
            raise OSError("in use")
        server = _Fake(next(handed) if port == 0 else port)
        made.append(server)
        return server
    return make, made


@pytest.fixture(autouse=True)
def _no_identity(monkeypatch):
    monkeypatch.setattr(bs, "_check_identity", lambda port: False)


def test_free_target_is_used():
    make, _ = _maker()
    server, port = bs._bind_viewer(6850, make)
    assert port == 6850 and server.server_address[1] == 6850


def test_taken_target_moves_along_the_band_before_asking_the_os():
    make, _ = _maker(taken={6898, 6899, 6800})
    _, port = bs._bind_viewer(6898, make)
    assert port == 6801                      # wraps from 6899 to 6800, never port 0


def test_same_project_viewer_on_a_band_port_is_reused(monkeypatch):
    monkeypatch.setattr(bs, "_check_identity", lambda port: port == 6851)
    make, made = _maker(taken={6850, 6851})
    server, port = bs._bind_viewer(6850, make)
    assert (server, port) == (None, 6851) and made == []


def test_os_fallback_skips_browser_blocked_ports_and_closes_them():
    make, made = _maker(taken=set(bs._PORT_BAND), os_ports=(6566, 6000, 51234))
    server, port = bs._bind_viewer(6820, make)
    assert port == 51234 and server.server_address[1] == 51234
    assert [s.closed for s in made] == [True, True, False]


def test_no_band_port_is_browser_blocked():
    assert not set(bs._PORT_BAND) & bs.BROWSER_BLOCKED_PORTS
    assert {6566, 6000, 6665, 6669, 10080} <= bs.BROWSER_BLOCKED_PORTS


def test_real_bind_skips_a_port_held_by_another_process():
    holder = socket.socket()
    for target in bs._PORT_BAND:
        try:
            holder.bind(("127.0.0.1", target))
            break
        except OSError:
            continue
    else:
        holder.close()
        pytest.skip("every viewer band port is held on this machine")
    holder.listen()
    try:
        server, port = bs._bind_viewer(target)
        try:
            assert server is not None and port != target
            assert port not in bs.BROWSER_BLOCKED_PORTS
        finally:
            if server is not None:
                server.server_close()
    finally:
        holder.close()
