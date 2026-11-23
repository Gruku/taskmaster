"""User intent: any web page the user visits must not be able to write to, or read from, the solo viewer on
127.0.0.1 (CSRF and DNS rebinding); only the viewer's own pages may (local-server-006).
"""
from __future__ import annotations

import http.client
import threading

import pytest

from taskmaster import backlog_server as bs


@pytest.fixture()
def viewer(tm_epic_phase):
    srv, port = bs._make_server(host="127.0.0.1", port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield port
    srv.shutdown()
    srv.server_close()


def _send(port, method, path, body=None, headers=None, host=True):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    hdrs = dict(headers or {})
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    if host is True:
        conn.putheader("Host", f"127.0.0.1:{port}")
    elif host:
        conn.putheader("Host", host)
    data = body.encode() if body is not None else None
    if data is not None:
        hdrs["Content-Length"] = str(len(data))
    for k, v in hdrs.items():
        conn.putheader(k, v)
    conn.endheaders(data)
    resp = conn.getresponse()
    out = (resp.status, dict(resp.getheaders()), resp.read())
    conn.close()
    return out


JSON = {"Content-Type": "application/json"}
IDEA = '{"title": "csrf probe"}'


def test_cross_origin_post_refused(viewer):
    status, headers, _ = _send(viewer, "POST", "/api/ideas", IDEA, {**JSON, "Origin": "https://evil.example"})
    assert status == 403
    assert "Access-Control-Allow-Origin" not in headers


def test_wrong_port_origin_refused(viewer):
    status, *_ = _send(viewer, "POST", "/api/ideas", IDEA, {**JSON, "Origin": f"http://127.0.0.1:{viewer + 1}"})
    assert status == 403


def test_cross_site_referer_refused_without_origin(viewer):
    status, *_ = _send(viewer, "POST", "/api/ideas", IDEA, {**JSON, "Referer": "https://evil.example/x"})
    assert status == 403


def test_text_plain_post_without_origin_refused(viewer):
    status, *_ = _send(viewer, "POST", "/api/ideas", IDEA, {"Content-Type": "text/plain"})
    assert status == 403


def test_missing_content_type_refused(viewer):
    status, *_ = _send(viewer, "POST", "/api/ideas", IDEA)
    assert status == 403


@pytest.mark.parametrize("host", ["evil.example", "evil.example:%PORT%", "127.0.0.1", "127.0.0.1:1", None])
def test_bad_host_refused_on_read_and_write(viewer, host):
    if host is not None:
        host = host.replace("%PORT%", str(viewer))
    for method, path, body, hdrs in (("GET", "/api/identity", None, {}), ("POST", "/api/ideas", IDEA, JSON)):
        status, *_ = _send(viewer, method, path, body, hdrs, host=host)
        assert status == 403, (method, host)


@pytest.mark.parametrize("origin", [None, "http://127.0.0.1:%PORT%", "http://localhost:%PORT%"])
def test_same_origin_json_write_accepted(viewer, origin):
    headers = dict(JSON)
    if origin:
        headers["Origin"] = origin.replace("%PORT%", str(viewer))
    status, resp_headers, body = _send(viewer, "POST", "/api/ideas", IDEA, headers)
    assert status in (200, 201), body
    assert "Access-Control-Allow-Origin" not in resp_headers


def test_localhost_host_accepted_for_reads(viewer):
    status, *_ = _send(viewer, "GET", "/api/identity", host=f"localhost:{viewer}")
    assert status == 200


def test_pages_still_served_without_cors(viewer):
    status, headers, body = _send(viewer, "GET", "/")
    assert status == 200 and b"<html" in body.lower()
    assert "Access-Control-Allow-Origin" not in headers


def test_preflight_grants_nothing(viewer):
    status, headers, _ = _send(viewer, "OPTIONS", "/api/ideas", None, {"Origin": "https://evil.example"})
    assert not any(k.lower().startswith("access-control-") for k in headers)


def test_preflight_status_is_not_a_grant(viewer):
    status, *_ = _send(viewer, "OPTIONS", "/api/ideas", None, {"Origin": "https://evil.example"})
    assert status in (200, 204, 501)  # whatever it is, test_preflight_grants_nothing shows no grant header


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_put_patch_delete_are_gated_like_post(viewer, method):
    status, *_ = _send(viewer, method, "/api/ideas", IDEA, {**JSON, "Origin": "https://evil.example"})
    assert status == 403


def test_origin_null_refused(viewer):
    status, *_ = _send(viewer, "POST", "/api/ideas", IDEA, {**JSON, "Origin": "null"})
    assert status == 403


@pytest.mark.parametrize("ctype", ["application/x-www-form-urlencoded", "multipart/form-data; boundary=x"])
def test_form_content_types_refused(viewer, ctype):
    status, *_ = _send(viewer, "POST", "/api/ideas", IDEA, {"Content-Type": ctype})
    assert status == 403


def test_ipv6_loopback_host_is_a_known_name_and_others_are_not(viewer):
    from email.message import Message
    for host, ok in ((f"[::1]:{viewer}", True), (f"[::1]:{viewer + 1}", False), ("[::2]:%d" % viewer, False)):
        msg = Message()
        msg["Host"] = host
        assert (bs._solo_refusal("GET", msg, viewer) is None) is ok, host


def test_solo_pages_refuse_framing(viewer):
    _, headers, _ = _send(viewer, "GET", "/api/identity")
    assert headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_file_route_refuses_a_sibling_directory_sharing_the_root_prefix(viewer):
    root = bs.ROOT.resolve()
    sibling = root.parent / (root.name + "-other")
    sibling.mkdir(exist_ok=True)
    (sibling / "secret.txt").write_text("sibling secret", encoding="utf-8")
    status, _, body = _send(viewer, "GET", f"/file/../{sibling.name}/secret.txt")
    assert status == 403 and b"sibling secret" not in body
