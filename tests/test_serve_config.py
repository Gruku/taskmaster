# User intent: the bind policy refuses plaintext off-host binds unless explicitly opted in, before anything is touched.
import pytest

from taskmaster.serve.config import (
    EXIT_COORDINATOR_LOST, BindRefused, ServeConfig, ServeRefused, ServeUnavailable,
    bind_host, check_bind, validate,
)


@pytest.mark.parametrize("h", ["127.0.0.1", "127.0.0.5", "::1", "localhost"])
def test_loopback_hosts_need_no_flag(h):
    assert check_bind(h, insecure_bind=False) is None


def test_bind_host_normalizes():
    assert bind_host("localhost") == "127.0.0.1"
    assert bind_host("0:0:0:0:0:0:0:1") == "::1"


@pytest.mark.parametrize("h", ["0.0.0.0", "::", "192.168.1.10", "10.0.0.1", "203.0.113.5"])
def test_non_loopback_bind_is_refused_without_the_flag(h):
    with pytest.raises(BindRefused) as exc:
        check_bind(h, insecure_bind=False)
    text = str(exc.value)
    assert "--insecure-bind" in text and "plaintext" in text and h in text


def test_insecure_bind_returns_the_risk_warning():
    text = check_bind("0.0.0.0", insecure_bind=True)
    assert "unencrypted" in text and "replay the token" in text and "TLS reverse proxy" in text


@pytest.mark.parametrize("h", ["example.com", "my-host"])
def test_hostnames_are_refused_even_with_the_flag(h):
    with pytest.raises(BindRefused, match="IP address or localhost"):
        bind_host(h)


@pytest.mark.parametrize("kw", [
    {"port": -1}, {"port": 65536}, {"path": "mcp"}, {"path": "/rpc"}, {"path": "/a b"},
    {"allowed_hosts": ("https://x",)}, {"allowed_hosts": ("x/y",)}, {"log_level": "trace"},
    {"session_idle_seconds": 0}, {"session_idle_seconds": 86401},
])
def test_validate_rejects_bad_settings(tmp_path, kw):
    with pytest.raises(ServeRefused):
        validate(ServeConfig(root=tmp_path, **kw))


def test_validate_accepts_defaults_and_port_zero(tmp_path):
    cfg = ServeConfig(root=tmp_path, port=0, allowed_hosts=("tm.example.com",))
    assert validate(cfg) is cfg
    assert validate(ServeConfig(root=tmp_path)).port == 7470


def test_exit_codes():
    assert ServeRefused.exit_code == 2 and BindRefused.exit_code == 2
    assert ServeUnavailable.exit_code == 69 and EXIT_COORDINATOR_LOST == 75


# --- team-server-006: hardening settings (plan Step 0) ---

from taskmaster.serve.config import allowed_viewer_origins


def test_hardening_defaults(tmp_path):
    cfg = ServeConfig(root=tmp_path)
    assert (cfg.max_body_bytes, cfg.header_timeout, cfg.body_timeout, cfg.max_connections) == (2097152, 10.0, 30.0, 128)
    assert (cfg.rate_per_minute, cfg.rate_burst, cfg.max_inflight) == (240, 120, 32)
    assert cfg.viewer_port is None and cfg.viewer_origins == ()


RANGES = [
    ("max_body_bytes", "--max-body-bytes", 65536, 67108864, 65535, 67108865),
    ("header_timeout", "--header-timeout", 1, 120, 0.5, 121),
    ("body_timeout", "--body-timeout", 1, 600, 0.5, 601),
    ("linger_seconds", "--linger-seconds", 0, 10, -0.1, 10.5),
    ("linger_bytes", "--linger-bytes", 0, 16777216, -1, 16777217),
    ("max_connections", "--max-connections", 33, 10000, 7, 10001),
    ("rate_per_minute", "--rate-limit", 1, 100000, 0, 100001),
    ("rate_burst", "--rate-burst", 1, 100000, 0, 100001),
    ("max_inflight", "--max-inflight", 1, 127, 0, 10001),
    ("viewer_port", "--viewer-port", 0, 65535, -1, 65536),
]


@pytest.mark.parametrize("field, flag, low, high, below, above", RANGES)
def test_hardening_ranges(tmp_path, field, flag, low, high, below, above):
    for good in (low, high):
        validate(ServeConfig(root=tmp_path, **{field: good}))
    for bad in (below, above):
        with pytest.raises(ServeRefused, match=flag):
            validate(ServeConfig(root=tmp_path, **{field: bad}))
    if field == "max_connections":
        validate(ServeConfig(root=tmp_path, max_connections=8, max_inflight=7))


def test_max_inflight_must_be_below_max_connections(tmp_path):
    with pytest.raises(ServeRefused) as exc:
        validate(ServeConfig(root=tmp_path, max_inflight=128, max_connections=128))
    assert "--max-inflight" in str(exc.value) and "--max-connections" in str(exc.value)
    validate(ServeConfig(root=tmp_path, max_inflight=127, max_connections=128))


@pytest.mark.parametrize("origin, ok", [
    ("http://tm.example.com", False), ("https://viewer.tm.example.com", True), ("http://localhost:9000", True),
    ("http://127.0.0.1:7471", True), ("http://[::1]:7471", True), ("https://x.com/path", False), ("ftp://x", False),
    ("https://x.com/", False), ("https://", False),
])
def test_viewer_origin_must_be_https_unless_loopback(tmp_path, origin, ok):
    cfg = ServeConfig(root=tmp_path, viewer_origins=(origin,))
    if ok:
        validate(cfg)
    else:
        with pytest.raises(ServeRefused, match="--viewer-origin"):
            validate(cfg)


def test_allowed_viewer_origins_adds_loopback(tmp_path):
    cfg = ServeConfig(root=tmp_path, viewer_origins=("https://v.example",))
    assert allowed_viewer_origins(cfg, 7471) == ("https://v.example", "http://127.0.0.1:7471", "http://localhost:7471")
    dup = ServeConfig(root=tmp_path, viewer_origins=("http://127.0.0.1:7471",))
    assert allowed_viewer_origins(dup, 7471) == ("http://127.0.0.1:7471", "http://localhost:7471")


# --- team-server-028 D11: the viewer is its own host, never the MCP host ---------------------------------

@pytest.mark.parametrize("allowed", ["tm.example.com", "tm.example.com:443", "TM.example.com"])
@pytest.mark.parametrize("origin", ["https://tm.example.com", "https://tm.example.com:443", "https://TM.EXAMPLE.COM"])
def test_a_viewer_origin_on_an_allowed_host_is_refused(tmp_path, allowed, origin):
    cfg = ServeConfig(root=tmp_path, allowed_hosts=(allowed,), viewer_origins=(origin,))
    with pytest.raises(ServeRefused) as exc:
        validate(cfg)
    assert str(exc.value) == (f"--viewer-origin {origin} must be its own host (e.g. viewer.<domain>), "
                              "not an --allowed-host")


def test_a_separate_viewer_host_and_a_loopback_origin_are_accepted(tmp_path):
    validate(ServeConfig(root=tmp_path, allowed_hosts=("tm.example.com",), viewer_origins=("https://viewer.tm.example.com",)))
    validate(ServeConfig(root=tmp_path, allowed_hosts=("localhost",), viewer_origins=("http://localhost:9000",)))
    validate(ServeConfig(root=tmp_path, allowed_hosts=("127.0.0.1:7470",), viewer_origins=("http://127.0.0.1:7471",)))
