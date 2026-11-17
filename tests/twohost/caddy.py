# User intent: put the real Caddy (the shipped Caddyfile, `tls internal`) in front of the harness's real daemon, so S13 is judged on what a stranger on the internet reaches (team-server-015 Step 15); reuses 028's deploy-tier rendering instead of a parallel Caddy setup.
"""Caddy in front of a twohost host."""
from __future__ import annotations

import contextlib
import os
import signal
import socket
import ssl
import subprocess
from pathlib import Path

import httpx

from . import waits
from test_serve_deploy_smoke import HOST, VIEWER_HOST, _SniClient, free_port, own_address, render_caddyfile

CADDY = os.environ.get("TASKMASTER_CADDY")


def _die_with_parent() -> None:
    """Linux: Caddy gets SIGTERM when pytest dies, so a killed run does not leave it holding its ports (015 gate M4)."""
    import ctypes
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)       # PR_SET_PDEATHSIG


class Caddy:
    """Reserve ports first (the daemon needs the public origin on its command line), start Caddy once the daemon is up."""

    def __init__(self, base: Path):
        self.base = Path(base)
        self.https_port, self.http_port = free_port(), free_port()
        self.address = own_address()
        self.proc = None
        self.log = None
        self.ca = self.base / "data" / "caddy" / "pki" / "authorities" / "local" / "root.crt"

    @property
    def viewer_origin(self) -> str:
        return f"https://{VIEWER_HOST}:{self.https_port}"

    @property
    def daemon_flags(self) -> list[str]:
        return ["--viewer-origin", self.viewer_origin, "--allowed-host", f"{HOST}:{self.https_port}"]

    def start(self, host) -> None:
        for name in ("data", "config", "home"):
            (self.base / name).mkdir(parents=True, exist_ok=True)
        daemon_port = int(host.url.split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)[1])
        viewer_port = int(host.viewer_url.rstrip("/").rsplit(":", 1)[1])
        caddyfile = self.base / "Caddyfile"
        caddyfile.write_text(render_caddyfile(log_path=self.base / "access.log", https_port=self.https_port,
                                              http_port=self.http_port, upstream_port=daemon_port, viewer_port=viewer_port),
                             encoding="utf-8")
        env = {**os.environ, "HOME": str(self.base / "home"), "XDG_DATA_HOME": str(self.base / "data"),
               "XDG_CONFIG_HOME": str(self.base / "config")}
        self.log = open(self.base / "caddy.log", "wb")
        self.proc = subprocess.Popen([CADDY, "run", "--config", str(caddyfile), "--adapter", "caddyfile"],
                                     stdout=self.log, stderr=subprocess.STDOUT, env=env, start_new_session=True,
                                     preexec_fn=_die_with_parent)
        def up():
            assert self.proc.poll() is None, (self.base / "caddy.log").read_text(errors="replace")[-3000:]
            if not self.ca.exists():
                return False
            with self.tls(VIEWER_HOST):
                return True

        waits.until(up, 30, "Caddy to serve the viewer site", interval=0.5)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.log:
            self.log.close()

    def tls(self, sni: str, timeout: float = 15.0) -> ssl.SSLSocket:
        context = ssl.create_default_context(cafile=str(self.ca))
        raw = socket.create_connection((self.address, self.https_port), timeout=timeout)
        return context.wrap_socket(raw, server_hostname=sni)

    def viewer(self) -> httpx.Client:
        return _SniClient(base_url=f"https://{self.address}:{self.https_port}", verify=str(self.ca),
                          headers={"Host": f"{VIEWER_HOST}:{self.https_port}"}, timeout=30)

    def raw(self, sni: str, request: bytes, *, read_for: float = 5.0) -> bytes:
        with self.tls(sni) as sock:
            sock.sendall(request)
            sock.settimeout(read_for)
            data = b""
            with contextlib.suppress(OSError, ssl.SSLError):
                while chunk := sock.recv(65536):
                    data += chunk
            return data
