# User intent: talk to the public viewer the way a browser behind Caddy would (off-host peer, the public Host and Origin, a cookie jar and the CSRF token), so the S13 scenario can show what the internet and a logged-in member get (team-server-015 S13). Same shapes as tests/test_serve_viewer.py's helpers, which are fixture-bound there.
"""A tiny viewer client over http.client."""
from __future__ import annotations

import http.client
import re
from types import SimpleNamespace

PUBLIC_HOST = "viewer.twohost.test"
PUBLIC_ORIGIN = f"https://{PUBLIC_HOST}"


class Viewer:
    def __init__(self, url: str, *, peer: str = "203.0.113.7"):
        self.port = int(url.rstrip("/").rsplit(":", 1)[1])
        self.peer = peer
        self.cookie: str | None = None
        self.csrf: str | None = None

    def request(self, method: str, path: str, *, headers=None, body=None, origin: bool = False, cookie: bool = True,
                csrf: bool = False):
        merged = {"Host": PUBLIC_HOST, "X-Forwarded-For": self.peer}
        if origin:
            merged["Origin"] = PUBLIC_ORIGIN
        if cookie and self.cookie:
            merged["Cookie"] = self.cookie
        if csrf and self.csrf:
            merged["X-Taskmaster-CSRF"] = self.csrf
        merged.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            data = body.encode() if isinstance(body, str) else body
            connection.request(method, path, body=data, headers=merged)
            response = connection.getresponse()
            payload = response.read()
        finally:
            connection.close()
        return SimpleNamespace(status=response.status, headers=response.headers, body=payload,
                               text=payload.decode("utf-8", "replace"))

    def login(self, key: str):
        response = self.request("POST", "/login", origin=True, cookie=False,
                                headers={"Content-Type": "application/x-www-form-urlencoded"}, body=f"token={key}")
        if response.status == 303 and response.headers.get("Set-Cookie"):
            self.cookie = response.headers["Set-Cookie"].split(";", 1)[0]
            page = self.request("GET", "/")
            found = re.search(r'<meta name="taskmaster-csrf" content="([^"]+)">', page.text)
            self.csrf = found.group(1) if found else None
        return response
