# User intent: the shipped systemd unit and Caddyfile repeat numbers and flags the server code owns; these tests keep
# them in step with the code and refuse the settings that would expose keys or the host-local operator path
# (team-server-016 design §5.4, §6).
from __future__ import annotations

import functools
import ipaddress
import re
import shlex
from pathlib import Path
from urllib.parse import urlparse

import pytest

from taskmaster.serve import config, viewer
from taskmaster.serve.cli import build_parser
from caddyfile import block, directive, matcher, sites, subdirective, upstreams

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "deploy" / "systemd" / "taskmaster.service"
CADDYFILE = ROOT / "deploy" / "caddy" / "Caddyfile"
ENV_EXAMPLE = ROOT / "deploy" / "taskmaster.env.example"

FORBIDDEN_CADDY = [
    r"\btrusted_proxies\b",
    r"\bclient_ip_headers\b",
    r"\blog_credentials\b",
    r"^\s*encode\b",
    r"\b(header_up|request_header)\s+[-+]?(host|x-forwarded-[a-z]+|forwarded|x-real-ip)\b",
]


def parse_unit(text: str) -> dict[str, list[str]]:
    """systemd unit lines as `Section.Key -> [values]`, with comments dropped and `\\` continuations joined."""
    values: dict[str, list[str]] = {}
    section, pending = "", ""
    for raw in text.splitlines():
        line = raw.strip()
        if not pending and (not line or line[0] in "#;"):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        line, pending = pending + line, ""
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        key, _, value = line.partition("=")
        values.setdefault(f"{section}.{key.strip()}", []).append(value.strip())
    return values


def caddy_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def caddy_size(value: str) -> int:
    match = re.fullmatch(r"(\d+)\s*(B|KB|KiB|MB|MiB|GB|GiB)?", value)
    assert match, value
    factor = {None: 1, "B": 1, "KB": 1000, "KiB": 1024, "MB": 10**6, "MiB": 2**20, "GB": 10**9, "GiB": 2**30}
    return int(match.group(1)) * factor[match.group(2)]


def caddy_duration(value: str) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h)", value)
    assert match, value
    return float(match.group(1)) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[match.group(2)]


def one(pattern: str, text: str) -> str:
    found = re.findall(pattern, text, re.MULTILINE)
    assert len(found) == 1, (pattern, found)
    return found[0]


@pytest.fixture(scope="module")
def unit() -> dict[str, list[str]]:
    return parse_unit(UNIT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def serve_args(unit):
    [exec_start] = unit["Service.ExecStart"]
    program, *argv = shlex.split(exec_start)
    assert program == "/opt/taskmaster/current/.venv/bin/taskmaster-serve", program
    return build_parser().parse_args(argv)     # an unknown or renamed flag exits here


@pytest.fixture(scope="module")
def caddy() -> str:
    return "\n".join(caddy_lines(CADDYFILE.read_text(encoding="utf-8")))


def site_texts(text: str) -> dict[str, str]:
    """Each site block's text, so the single-site `one(...)` rules apply to one site at a time."""
    out, name, depth, lines = {}, None, 0, []
    for line in caddy_lines(text):
        stripped = line.strip()
        if name is None:
            if stripped.endswith("{") and stripped != "{":
                name, depth, lines = stripped[:-1].strip(), 1, []
            elif stripped == "{":
                name, depth, lines = "", 1, []
            continue
        depth += stripped.count("{") - stripped.count("}")
        if depth == 0:
            if name:
                out[name] = "\n".join(lines)
            name = None
        else:
            lines.append(line)
    return out


ALLOWED_HOST = "tm.example.com"
VIEWER_HOST = "viewer.tm.example.com"


@pytest.fixture(scope="module")
def daemon_site(caddy) -> str:
    return site_texts(caddy)[ALLOWED_HOST]


@pytest.fixture(scope="module")
def viewer_site(caddy) -> str:
    return site_texts(caddy)[VIEWER_HOST]


@pytest.fixture(scope="module")
def site_nodes():
    return sites(CADDYFILE.read_text(encoding="utf-8"))


# -- the unit -----------------------------------------------------------------------------------------------------


def test_exec_start_binds_loopback_on_the_default_ports(serve_args):
    assert ipaddress.ip_address(config.bind_host(serve_args.host)).is_loopback
    assert serve_args.insecure_bind is False
    assert serve_args.port == config.DEFAULT_PORT
    assert serve_args.viewer_port == config.DEFAULT_VIEWER_PORT and serve_args.no_viewer is False
    assert serve_args.viewer_origin == ["https://viewer.tm.example.com"]
    assert len(serve_args.allowed_host) == 1
    assert serve_args.export_push == "backup" and serve_args.export_branch == "taskmaster-state", "013's backup remote"


def test_the_unit_drains_a_refused_body_as_far_as_caddy_can_send(serve_args, daemon_site):
    """Design §6.2: with the 64 KiB default, Caddy hits a broken pipe on a declared oversize body or a revoked key's
    large POST and answers 502 instead of the daemon's 413/401. The drain must cover Caddy's cap, for long enough."""
    assert serve_args.linger_bytes >= caddy_size(one(r"^\s*max_size\s+(\S+)\s*$", daemon_site))
    assert serve_args.linger_seconds >= config.DEFAULT_LINGER_SECONDS > 0


def test_the_unit_writes_only_the_served_root(unit, serve_args):
    root = str(serve_args.root.as_posix())
    assert unit["Service.ReadWritePaths"] == [root]
    assert unit["Service.WorkingDirectory"] == [root]
    assert unit["Service.UMask"] == ["0077"]
    assert unit["Service.ProtectSystem"] == ["strict"]
    assert unit["Service.ProtectHome"] == ["yes"]


def test_restart_policy_matches_the_exit_codes(unit):
    assert unit["Service.Restart"] == ["on-failure"]
    assert unit["Service.RestartPreventExitStatus"] == [str(config.EXIT_REFUSED)]
    assert unit["Unit.StartLimitBurst"] and unit["Unit.StartLimitIntervalSec"]
    assert unit["Service.KillMode"] == ["mixed"] and unit["Service.KillSignal"] == ["SIGTERM"]
    assert int(unit["Service.TimeoutStopSec"][0]) > config.GRACEFUL_SECONDS + 15


def test_secrets_never_sit_in_the_unit(unit):
    assert not any(key.endswith(".Environment") for key in unit), "use EnvironmentFile= (root, 0600)"
    assert unit["Service.EnvironmentFile"] == ["-/etc/taskmaster/taskmaster.env"]
    assert "FORWARDED_ALLOW_IPS" not in UNIT.read_text(encoding="utf-8")
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        assert not line.strip() or line.lstrip().startswith("#"), f"the example must not set a value: {line!r}"


def test_the_sandbox_is_on(unit):
    expected = {"User": "taskmaster", "NoNewPrivileges": "yes", "CapabilityBoundingSet": "", "AmbientCapabilities": "",
                "PrivateTmp": "yes", "PrivateDevices": "yes", "ProtectProc": "invisible", "RestrictNamespaces": "yes",
                "LockPersonality": "yes", "MemoryDenyWriteExecute": "yes", "SystemCallFilter": "@system-service",
                "SystemCallArchitectures": "native", "RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6",
                "MemoryMax": "1G", "OOMPolicy": "continue"}
    for key, value in expected.items():
        assert unit[f"Service.{key}"] == [value], key


# -- the Caddyfile ------------------------------------------------------------------------------------------------


def test_caddy_body_cap_sits_above_the_daemons(daemon_site):
    """Design §6.2 (review I2): the daemon's 413 is the primary limit; Caddy's wrap is defence in depth."""
    assert caddy_size(one(r"^\s*max_size\s+(\S+)\s*$", daemon_site)) > config.DEFAULT_MAX_BODY_BYTES


def test_the_viewer_body_cap_sits_at_or_below_the_daemons(viewer_site):
    """Caddy refuses an oversize viewer body before the stdlib server; that early 413 isn't lingered (006 deviation 11)."""
    assert caddy_size(one(r"^\s*max_size\s+(\S+)\s*$", viewer_site)) <= config.DEFAULT_MAX_BODY_BYTES


def test_caddy_timeouts_match_the_daemon(caddy, daemon_site):
    assert caddy_duration(one(r"^\s*read_header\s+(\S+)\s*$", caddy)) == config.DEFAULT_HEADER_TIMEOUT
    assert caddy_duration(one(r"^\s*keepalive\s+(\S+)\s*$", daemon_site)) < config.KEEP_ALIVE_SECONDS
    for name in ("read_body", "write", "read_timeout", "write_timeout", "stream_timeout"):
        assert not re.search(rf"^\s*{name}\b", caddy, re.MULTILINE), f"{name} can cut SSE streams (006 §9)"


def test_caddy_proxies_only_the_public_prefixes_to_the_daemon(daemon_site):
    caddy = daemon_site
    assert one(r"^\s*reverse_proxy\s+(\S+)", caddy) == f"127.0.0.1:{config.DEFAULT_PORT}"
    # team-server-025: the matcher is exactly the app's public prefixes, each as itself and as a subtree. Too wide
    # exposes a path the app never meant to publish; too narrow 404s a production route at the proxy.
    matcher = one(r"^\s*@taskmaster\s+path\s+(.+?)\s*$", caddy).split()
    expected = [form for prefix in config.PUBLIC_PREFIXES for form in (prefix, prefix + "/*")]
    assert sorted(matcher) == sorted(expected)
    assert set(config.PUBLIC_PREFIXES) == {"/mcp", "/read/v1", "/write/v1"}
    assert re.search(r"handle\s*\{\s*respond 404\s*\}", caddy)


# -- the viewer site (team-server-028 section 2.3) ----------------------------------------------------------------


def test_the_viewer_site_proxies_only_the_viewer_routes(site_nodes):
    body = site_nodes[VIEWER_HOST]
    assert upstreams(body) == [f"127.0.0.1:{config.DEFAULT_VIEWER_PORT}"]
    assert sorted(matcher(body, "viewer")) == sorted(viewer.caddy_matcher_forms())
    assert directive(body, "respond") == ["404"]


def test_the_viewer_upstream_queues_below_the_apps_connection_cap(site_nodes):
    body = site_nodes[VIEWER_HOST]
    [cap] = subdirective(body, "reverse_proxy/transport/max_conns_per_host")
    assert 0 < int(cap) <= config.VIEWER_MAX_CONNECTIONS // 2
    assert subdirective(body, "reverse_proxy/transport/keepalive") == ["off"]


def largest_served_asset(root: Path) -> int:
    sizes = [path.stat().st_size for path in root.rglob("*") if path.is_file()
             and viewer.static_asset_allowed(path.relative_to(root).as_posix())]
    return max(sizes)


def test_the_viewer_proxy_buffers_every_static_asset_and_the_login_body(site_nodes):
    body = site_nodes[VIEWER_HOST]
    [response] = subdirective(body, "reverse_proxy/response_buffers")
    [request] = subdirective(body, "reverse_proxy/request_buffers")
    assets = ROOT / "viewer"
    for known in ("vendor/marked.min.js", "vendor/fonts/JetBrainsMono-Variable.woff2", "vendor/fonts/DMSans-Variable.woff2"):
        assert viewer.static_asset_allowed(known) and (assets / known).is_file(), known
    assert caddy_size(response) >= largest_served_asset(assets)
    assert caddy_size(request) >= config.VIEWER_LOGIN_MAX_BYTES
    assert not subdirective(site_nodes[ALLOWED_HOST], "reverse_proxy/response_buffers"), "the MCP site streams SSE"
    assert not subdirective(site_nodes[ALLOWED_HOST], "reverse_proxy/request_buffers")


def test_a_planted_asset_larger_than_response_buffers_fails_the_size_check(site_nodes, tmp_path):
    shutil_tree = tmp_path / "viewer"
    (shutil_tree / "js").mkdir(parents=True)
    (shutil_tree / "js" / "big.js").write_bytes(b"x" * (caddy_size(subdirective(site_nodes[VIEWER_HOST],
                                                                              "reverse_proxy/response_buffers")[0]) + 1))
    assert caddy_size(subdirective(site_nodes[VIEWER_HOST], "reverse_proxy/response_buffers")[0]) < largest_served_asset(shutil_tree)


def test_the_viewer_log_drops_the_csrf_header(site_nodes):
    assert subdirective(site_nodes[VIEWER_HOST], "log/format/request>headers>X-Taskmaster-Csrf") == ["delete"]
    assert subdirective(site_nodes[VIEWER_HOST], "log/format") == ["filter"]


@pytest.mark.parametrize("host", [ALLOWED_HOST, VIEWER_HOST])
def test_each_site_has_tls_alpn_hsts_and_no_server_header(site_nodes, host):
    body = site_nodes[host]
    assert subdirective(body, "tls/issuer/disable_http_challenge") == [""]
    assert any(v.startswith('Strict-Transport-Security "max-age=') for v in directive(body, "header"))
    assert "-Server" in directive(body, "header")


def test_the_two_sites_share_no_upstream_and_no_path(site_nodes):
    assert upstreams(site_nodes[ALLOWED_HOST]) == [f"127.0.0.1:{config.DEFAULT_PORT}"]
    assert upstreams(site_nodes[VIEWER_HOST]) == [f"127.0.0.1:{config.DEFAULT_VIEWER_PORT}"]
    daemon_forms = matcher(site_nodes[ALLOWED_HOST], "taskmaster")
    viewer_forms = matcher(site_nodes[VIEWER_HOST], "viewer")
    for form in viewer_forms:
        assert not any(form == prefix or form.startswith(prefix + "/") for prefix in config.PUBLIC_PREFIXES), form
    for form in daemon_forms:
        assert not viewer.route_allowed(form.replace("/*", "/x")), form


def caddy_path_matches(pattern: str, path: str) -> bool:
    """Caddy's path matcher: an exact path, or a trailing /* that takes the subtree."""
    if pattern.endswith("/*"):
        return path.startswith(pattern[:-1]) and len(path) > len(pattern) - 1
    return path == pattern


@pytest.mark.parametrize("path", ["/", "/login", "/logout", "/favicon.ico", "/api/board", "/api/prefs",
                                  "/file/docs/a.md", "/static/v3/js/main.js", "/static/v3/css/login.css"])
def test_every_viewer_route_sits_under_the_matcher(path):
    assert viewer.route_allowed(path)
    assert any(caddy_path_matches(form, path) for form in viewer.caddy_matcher_forms()), path


@pytest.mark.parametrize("path", ["/backlog.yaml", "/v3/dev/x.html", "/v3/index.html", "/index.html", "/api/session",
                                  "/mcp", "/read/v1/tasks", "/write/v1/x"])
def test_every_removed_path_fails_both(path):
    assert not viewer.route_allowed(path)
    if path == "/api/session":
        return      # Caddy's /api/* cannot carve one path out; the app answers 404 (design 028 deviation, recorded in the resume file)
    assert not any(caddy_path_matches(form, path) for form in viewer.caddy_matcher_forms()), path


# -- public static assets come from Caddy's file_server, never the buffered proxy (028 gate, Codex-I) ------------
# Through the proxy, an unauthenticated HTTP/2 stream that never opens its window pins a response buffer holding the
# whole asset (the ~63 KB font), and its upstream slot is freed for the next one. From disk, Go's HTTP/2 server holds
# no more for such a stream than for any other.

RELEASE_VIEWER = "/opt/taskmaster/current/viewer"
STATIC_CACHE_CONTROL = "no-cache, no-store, must-revalidate"     # what ViewerHandler sends with a static asset
# Paths around the edges of static_asset_allowed: Caddy may refuse more than the app (the app then answers 404), never less.
STATIC_EDGE_CASES = ["js/.hidden.js", "js/_dormant/x.js", "js/a-DEMO.js", "js/a-demo.JS", "vendor/x.JS", "css/../x.css",
                     "js/./x.js", "js//x.js", "js/a%2e.js", "dev/x.js", "tests/x.js", "js/tests/x.js",
                     "js/node_modules/x/y.js", "js/dev/x.js", "js/x.txt", "js/x.html", "x.js", "js/", "js", "",
                     "js/a b.js", "js/ü.js", "js/x.js/", "vendor/fonts/OFL-DMSans.txt", "js/x.mjs", "js/.js",
                     "js/x.", "js/x.js.map", "js\\x.js", "js/x.js\x00", "JS/x.js", "css/_dormant.css", "js/dev.js"]


def static_regexes(body) -> tuple[str, str]:
    matcher_node = block(body, "@static")
    assert [n.name for n in matcher_node.children] == ["path_regexp", "not"], matcher_node
    allow, deny = matcher_node.children
    assert len(allow.args) == 1 and deny.args[0] == "path_regexp" and len(deny.args) == 2, matcher_node
    return allow.args[0], deny.args[1]


def caddy_serves_static(body, path: str) -> bool:
    """Would the viewer site's @static send this decoded, cleaned path to file_server? (Go RE2 and Python re agree on
    these patterns: no backreferences or lookaround.)"""
    allow, deny = static_regexes(body)
    return bool(re.search(allow, path)) and not re.search(deny, path)


@functools.lru_cache(maxsize=1)
def viewer_files() -> tuple[str, ...]:
    assets = ROOT / "viewer"
    return tuple(path.relative_to(assets).as_posix() for path in assets.rglob("*") if path.is_file())


def test_caddy_serves_every_public_asset_itself_and_nothing_the_app_refuses(site_nodes):
    body = site_nodes[VIEWER_HOST]
    assets = ROOT / "viewer"
    files = viewer_files()
    assert len(files) > 100
    for rel in files:
        assert caddy_serves_static(body, "/static/v3/" + rel) == viewer.static_asset_allowed(rel), rel
    for rel in STATIC_EDGE_CASES:
        if caddy_serves_static(body, "/static/v3/" + rel):
            assert viewer.static_asset_allowed(rel), f"Caddy serves {rel!r} from disk but the app refuses it"
    for refused in ("js/_dormant/auto-mode.js", "js/components/edit/fields/chip-input-demo.js",
                    "vendor/fonts/OFL-DMSans.txt"):
        assert (assets / refused).is_file() and not caddy_serves_static(body, "/static/v3/" + refused), refused


def test_static_answers_carry_the_apps_static_headers(site_nodes):
    handle = block(site_nodes[VIEWER_HOST], "handle", "@static")
    sent = {}
    for node in handle.children:
        if node.name == "header":
            name, value = node.args[0], " ".join(node.args[1:])
            sent[name] = value[1:-1] if value.startswith('"') and value.endswith('"') else value
    expected = dict(viewer.response_headers("static", "https://" + VIEWER_HOST))
    assert sent == {**expected, "Cache-Control": STATIC_CACHE_CONTROL}


def test_static_assets_come_from_the_release_and_before_the_proxy(site_nodes):
    body = site_nodes[VIEWER_HOST]
    handle = block(body, "handle", "@static")
    assert [(n.name, n.args) for n in handle.children if n.name != "header"] == [
        ("uri", ["strip_prefix", "/static/v3"]), ("root", ["*", RELEASE_VIEWER]), ("file_server", [])]
    order = [(n.name, n.args[:1]) for n in body if n.name in ("rewrite", "handle")]
    assert order == [("rewrite", ["/favicon.ico"]), ("handle", ["@static"]), ("handle", ["@viewer"]), ("handle", [])]
    [rewrite] = [n for n in body if n.name == "rewrite"]
    target = rewrite.args[1]
    assert target.startswith(viewer.STATIC_PREFIX) and caddy_serves_static(body, target)
    assert (ROOT / "viewer" / target[len(viewer.STATIC_PREFIX):]).is_file()


def test_the_release_holds_the_viewer_caddy_serves(unit):
    [exec_start] = unit["Service.ExecStart"]
    release = shlex.split(exec_start)[0].split("/.venv/", 1)[0]
    assert RELEASE_VIEWER == f"{release}/viewer"

# -- each mutation of the shipped files must fail a named check --------------------------------------------------

MUTATIONS = {
    "extra_viewer_form": ("@viewer path /favicon.ico", "@viewer path /v3/* /favicon.ico"),
    "missing_viewer_form": ("/api/* /file/*", "/api/*"),
    "viewer_upstream_in_mcp_site": ("reverse_proxy 127.0.0.1:7470", "reverse_proxy 127.0.0.1:7471"),
    "api_in_taskmaster": ("@taskmaster path /mcp", "@taskmaster path /api/* /mcp"),
    "viewer_max_size_4mib": ("max_size 1MiB", "max_size 4MiB"),
    "viewer_conns_removed": ("\t\t\t\tmax_conns_per_host 16\n", ""),
    "viewer_conns_32": ("max_conns_per_host 16", "max_conns_per_host 32"),
    "viewer_keepalive_removed": ("\t\t\t\tkeepalive off\n", ""),
    "response_buffers_removed": ("\t\t\tresponse_buffers 512KiB\n", ""),
    "response_buffers_32k": ("response_buffers 512KiB", "response_buffers 32KiB"),
    "request_buffers_removed": ("\t\t\trequest_buffers 64KiB\n", ""),
    "request_buffers_1k": ("request_buffers 64KiB", "request_buffers 1KiB"),
    "csrf_filter_removed": ("\t\t\trequest>headers>X-Taskmaster-Csrf delete\n", ""),
    "static_deny_removed": ("\t\tnot path_regexp (?i:-demo\\.js)$|/(_dormant|dev|node_modules|tests)/\n", ""),
    "static_dotfiles_allowed": ("(/[A-Za-z0-9_][A-Za-z0-9_.-]*)+", "(/[^/]+)+"),
    "static_txt_allowed": ("|svg|ico|png)$", "|svg|ico|png|txt)$"),
    "static_csp_dropped": ("\t\theader Content-Security-Policy \"default-src 'none'; frame-ancestors 'none'\"\n", ""),
    "static_cache_control_changed": ('header Cache-Control "no-cache, no-store, must-revalidate"',
                                     'header Cache-Control "max-age=3600"'),
    "static_root_elsewhere": ("root * /opt/taskmaster/current/viewer", "root * /opt/taskmaster/current"),
    "static_browse": ("\t\tfile_server\n", "\t\tfile_server browse\n"),
    "favicon_rewrite_removed": ("\trewrite /favicon.ico /static/v3/vendor/favicon.ico\n", ""),
}
VIEWER_CHECKS = ("test_the_viewer_site_proxies_only_the_viewer_routes", "test_the_viewer_upstream_queues_below_the_apps_connection_cap",
                 "test_the_viewer_proxy_buffers_every_static_asset_and_the_login_body", "test_the_viewer_log_drops_the_csrf_header",
                 "test_the_viewer_body_cap_sits_at_or_below_the_daemons", "test_the_two_sites_share_no_upstream_and_no_path",
                 "test_caddy_proxies_only_the_public_prefixes_to_the_daemon",
                 "test_caddy_serves_every_public_asset_itself_and_nothing_the_app_refuses",
                 "test_static_answers_carry_the_apps_static_headers",
                 "test_static_assets_come_from_the_release_and_before_the_proxy")


def failing_checks(text: str) -> list[str]:
    """Run every site-level check against `text` in memory; the names of the ones that fail."""
    nodes = sites(text)
    daemon = site_texts("\n".join(caddy_lines(text)))[ALLOWED_HOST]
    viewer_text = site_texts("\n".join(caddy_lines(text)))[VIEWER_HOST]
    failed = []
    for name in VIEWER_CHECKS:
        fn = globals()[name]
        params = {"site_nodes": nodes, "daemon_site": daemon, "viewer_site": viewer_text}
        names = fn.__code__.co_varnames[:fn.__code__.co_argcount]
        try:
            fn(*[params[n] for n in names])
        except (AssertionError, KeyError, ValueError):
            failed.append(name)
    return failed


def test_the_shipped_caddyfile_passes_every_mutated_check():
    assert failing_checks(CADDYFILE.read_text(encoding="utf-8").replace("\r\n", "\n")) == []


@pytest.mark.parametrize("label", sorted(MUTATIONS))
def test_each_caddyfile_mutation_fails_a_named_check(label):
    old, new = MUTATIONS[label]
    text = CADDYFILE.read_text(encoding="utf-8").replace("\r\n", "\n")
    if label == "viewer_upstream_in_mcp_site":
        mutated = text.replace(old, new, 1)
    elif label == "api_in_taskmaster":
        mutated = text.replace(old, new, 1)
    else:
        assert old in text, label
        mutated = text.replace(old, new, 1)
    assert mutated != text, label
    assert failing_checks(mutated), f"{label} was not caught"


def test_response_buffers_on_the_daemon_site_fails_the_size_check():
    text = CADDYFILE.read_text(encoding="utf-8").replace("\r\n", "\n")
    mutated = text.replace("reverse_proxy 127.0.0.1:7470 {", "reverse_proxy 127.0.0.1:7470 {\n\t\t\tresponse_buffers 512KiB", 1)
    assert "test_the_viewer_proxy_buffers_every_static_asset_and_the_login_body" in failing_checks(mutated)


def test_the_unit_mutations_are_caught(tmp_path):
    text = UNIT.read_text(encoding="utf-8")
    dropped = text.replace("--viewer-origin https://viewer.tm.example.com ", "")
    assert dropped != text
    assert parse_unit(dropped) and "--viewer-origin" not in dropped
    args = shlex.split(" ".join(parse_unit(dropped)["Service.ExecStart"]))[1:]
    assert build_parser().parse_args(args).viewer_origin != ["https://viewer.tm.example.com"]


def test_every_viewer_route_sample_is_in_the_inventory():
    assert viewer.caddy_matcher_forms()


def test_every_route_the_read_api_mounts_sits_under_a_public_prefix():
    from taskmaster.serve.read_api import ReadApi
    paths = [route.path for route in ReadApi(root=ROOT, server=None, limiter=None).routes() if "{" not in route.path]
    assert paths and all(any(p == pre or p.startswith(pre + "/") for pre in config.PUBLIC_PREFIXES) for p in paths), paths


@pytest.mark.parametrize("required", [r"^\s*admin off\s*$", r"^\s*protocols h1 h2\s*$",
                                      r"^\s*auto_https disable_redirects\s*$", r"^\s*disable_http_challenge\s*$",
                                      r"^\s*header -Server\s*$", r'^\s*header Strict-Transport-Security "max-age=\d+"',
                                      r"^\s*roll_interval\s+\S+"])
def test_caddy_required_settings(caddy, required):
    assert re.search(required, caddy, re.MULTILINE), required


@pytest.mark.parametrize("pattern", FORBIDDEN_CADDY)
def test_caddy_forbidden_settings(caddy, pattern):
    assert not re.search(pattern, caddy, re.MULTILINE | re.IGNORECASE), pattern


@pytest.mark.parametrize("line", ["\theader_up -X-Forwarded-For", "\trequest_header Host 127.0.0.1",
                                  "\theader_up X-Real-IP {remote}", "\ttrusted_proxies static private_ranges",
                                  "\tencode gzip", "\tlog_credentials"])
def test_the_forbidden_list_catches_each_dangerous_form(line):
    assert any(re.search(p, line, re.MULTILINE | re.IGNORECASE) for p in FORBIDDEN_CADDY), line


def test_the_unit_and_the_caddyfile_name_the_same_domain(site_nodes, serve_args):
    """Review M3: the placeholders change together, or every request gets 421."""
    assert list(site_nodes) == [*serve_args.allowed_host, *(urlparse(o).hostname for o in serve_args.viewer_origin)]


# -- the how-to, the rehearsal and the workflow ----------------------------------------------------------------------

HOWTO = ROOT / "docs" / "runbooks" / "team-server-deploy.md"
REHEARSAL = ROOT / "scripts" / "linux_rehearsal.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "linux-serve.yml"
FENCE = re.compile(r"^```(\w+)([^\n]*)\n(.*?)^```$", re.DOTALL | re.MULTILINE)
SERVE_SUBCOMMANDS = {"add-user", "rotate-user", "revoke-user", "list-users", "set-user", "token", "export", "marker", "init", "--help"}


def howto_text() -> str:
    return HOWTO.read_text(encoding="utf-8").replace("\r\n", "\n")


def howto_blocks() -> list[tuple[str, str, str]]:
    """(language, tag, body) for every fenced block of the how-to."""
    return [(m.group(1), m.group(2).strip(), m.group(3)) for m in FENCE.finditer(howto_text())]


def test_every_command_block_is_tagged_rehearsed_or_not():
    blocks = [b for b in howto_blocks() if b[0] == "bash"]
    assert blocks
    ids = []
    for _, tag, body in blocks:
        assert re.fullmatch(r"rehearsed:[a-z0-9-]+|not-rehearsed", tag), f"untagged command block: {body[:60]!r}"
        if tag.startswith("rehearsed:"):
            ids.append(tag.split(":", 1)[1])
    assert len(ids) == len(set(ids)), "rehearsed ids must be unique"


def test_every_not_rehearsed_block_says_why():
    text = howto_text()
    for m in FENCE.finditer(text):
        if m.group(1) == "bash" and m.group(2).strip() == "not-rehearsed":
            assert "Why not rehearsed" in text[m.end():m.end() + 400] or "Why not rehearsed" in text[m.start() - 400:m.start()], \
                m.group(3)[:80]


def test_the_how_to_names_only_deploy_files_that_exist():
    for name in set(re.findall(r"\bdeploy/[A-Za-z0-9_./-]+", howto_text())):
        assert (ROOT / name.rstrip(".,)")).exists(), name


def test_the_how_to_serve_subcommands_exist_and_run_forms_parse():
    subcommands = set()
    for _, _, body in howto_blocks():
        for line in body.splitlines():
            if line.lstrip().startswith("#") or '"$SERVE"' not in line:
                continue
            word = re.search(r'"\$SERVE"\s+(\S+)', line).group(1)
            subcommands.add(word)
            if word not in SERVE_SUBCOMMANDS:     # the daemon's own run form
                argv = shlex.split(line.replace('"$SERVE"', "taskmaster-serve").replace("$ROOT", "/srv/taskmaster/project"))
                build_parser().parse_args(argv[argv.index("taskmaster-serve") + 1:])
    assert subcommands <= SERVE_SUBCOMMANDS, subcommands - SERVE_SUBCOMMANDS


def test_the_how_to_variables_are_the_ones_the_rehearsal_sets():
    [vars_block] = [body for _, tag, body in howto_blocks() if tag == "not-rehearsed" and "export DOMAIN=" in body]
    names = re.findall(r"^export (\w+)=", vars_block, re.MULTILINE)
    assert names == ["DOMAIN", "SHA", "TASKMASTER_URL", "REPO_URL", "BACKUP_URL"]
    script = REHEARSAL.read_text(encoding="utf-8")
    for name in names:
        assert re.search(rf"\b{name}=", script), f"linux_rehearsal.sh does not set {name}"


def test_rehearsal_hooks_attach_to_real_steps():
    ids = {tag.split(":", 1)[1] for _, tag, _ in howto_blocks() if tag.startswith("rehearsed:")}
    script = REHEARSAL.read_text(encoding="utf-8")
    hooked = set(re.findall(r"^\s*(?:before|after)_([a-z0-9-]+)\(\)", script, re.MULTILINE))
    assert hooked and hooked <= ids, hooked - ids



REHEARSAL_DOMAIN = "tm.localhost"


def rehearsal_caddy_edit() -> str:
    """The Python the rehearsal runs on the installed Caddyfile (its after_install-files hook), verbatim."""
    script = REHEARSAL.read_text(encoding="utf-8").replace("\r\n", "\n")
    hook = re.search(r"^  after_install-files\(\) \{\n(.*?)^  \}$", script, re.DOTALL | re.MULTILINE).group(1)
    [code] = re.findall(r"python3 - <<'PY'\n(.*?)^PY$", hook, re.DOTALL | re.MULTILINE)
    return code


def rehearsal_edited_caddyfile(tmp_path: Path) -> Path:
    """The Caddyfile the rehearsal runs: the shipped one after the how-to's domain sed and the rehearsal's own edit."""
    [install] = [body for _, tag, body in howto_blocks() if tag == "rehearsed:install-files"]
    sed = re.search(r'sed -i "s/([^/]+)/\$DOMAIN/g" [^\n]*/etc/caddy/Caddyfile', install)
    assert sed, install
    installed = re.sub(sed.group(1), REHEARSAL_DOMAIN, CADDYFILE.read_text(encoding="utf-8").replace("\r\n", "\n"))
    target = tmp_path / "Caddyfile"
    target.write_text(installed, encoding="utf-8")
    code = rehearsal_caddy_edit()
    assert code.count('"/etc/caddy/Caddyfile"') == 1
    exec(compile(code.replace('"/etc/caddy/Caddyfile"', repr(str(target))), "rehearsal-edit", "exec"), {})
    return target


def test_the_rehearsal_converts_every_shipped_site_to_its_own_ca(tmp_path):
    """028 gate I2: the rehearsal's edit must certify every shipped site from Caddy's CA; a new site with its own acme
    block must not abort the rehearsal. The deploy tier also validates the result with the real Caddy."""
    edited = rehearsal_edited_caddyfile(tmp_path).read_text(encoding="utf-8")
    nodes = sites(edited)
    assert list(nodes) == [REHEARSAL_DOMAIN, f"viewer.{REHEARSAL_DOMAIN}"]
    for host, body in nodes.items():
        assert directive(body, "tls") == ["internal"], host
    assert "issuer acme" not in edited and "skip_install_trust" in edited


def test_the_rehearsal_checks_the_viewer_site_through_caddy():
    script = REHEARSAL.read_text(encoding="utf-8")
    assert re.search(r"viewer\.\$DOMAIN", script), "the rehearsal never checks the viewer site"
    for path in ("/login", "/backlog.yaml", "/static/v3/js/main.js"):
        assert path in script, path

def test_the_how_to_covers_the_operator_topics():
    text = howto_text().lower()
    for needle in ("team-server-migration.md", "rollout rule", "modify/delete", "uncommitted edits", "submodule",
                   "--force-upgrade", "apt-mark hold caddy", "port 80", "restart, never reload", "rotate-user",
                   "revoke-user", "backup", "rehearsed", "tm.example.com", "--no-editable"):
        assert needle in text, needle


def test_the_workflow_is_read_only_pinned_and_secretless():
    text = WORKFLOW.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert re.search(r"^permissions:\n  contents: read\n(?=\S)", text, re.MULTILINE), \
        "top-level permissions must be exactly contents: read"
    uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)", text, re.MULTILINE)
    assert uses and all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", ref) for ref in uses), uses
    active = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    for forbidden in ("secrets.", "pull_request_target", "write", "GITHUB_TOKEN"):
        assert forbidden not in active, forbidden
    assert "ubuntu-24.04" in text and "--tier serve --tier regression --tier deploy" in text
    assert "scripts/linux_lane.sh" in text


def test_actionlint_accepts_the_workflow_when_installed():
    import shutil
    import subprocess
    tool = shutil.which("actionlint")
    if tool is None:
        pytest.skip("actionlint is not installed")
    done = subprocess.run([tool, str(WORKFLOW)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_how_to_names_no_taskmaster_command_that_does_not_ship():
    """The package ships `taskmaster-serve` and taskmaster_cli.py; a bare `taskmaster <word>` command does not exist."""
    text = howto_text()
    assert not re.search(r"(?<![\w/.-])taskmaster (serve|remote)\b", text)
    assert (ROOT / "taskmaster_cli.py").is_file()


def test_the_firewall_section_comes_before_the_service_is_started():
    text = HOWTO.read_text(encoding="utf-8")
    assert text.index("## 6. Firewall") < text.index("## 7. Start it")
    assert "TCP 443" in text.split("## 1. Packages")[0]
