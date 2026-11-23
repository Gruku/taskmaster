# User intent: build, from the code, the list of everything the internet or a member can reach on the team server (routes, tools with their class, command ops, coordinator methods, restricted tables, headers, the Caddyfile's public matcher), so the security reviewer signs off against a fixture and a later change cannot silently widen the surface (team-server-015 B2.4).
"""Surface inventory builder. `python tests/surface_inventory.py --write` regenerates the fixture, keeping reviewed classes."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "serve_surface.json"
CADDYFILE = REPO / "deploy" / "caddy" / "Caddyfile"
UNIT = REPO / "deploy" / "systemd" / "taskmaster.service"
CLASSES = ("member", "operator-only", "serve-refused", "refused-native", "noop-initialized")
UNCLASSIFIED = "UNCLASSIFIED"
OP_CLASSES = ("batch-allowed", "internal")

# Reviewed classes for every tool that is not a plain member tool; anything unlisted is `member`. The gate that enforces
# an operator-only tool is named so the reviewer can read it. `note` says what stays member-callable.
TOOL_OVERRIDES = {
    "backlog_init": ("refused-native", "native_routing/registry.py GUIDANCE"),
    "backlog_migrate_v3": ("refused-native", "native_routing/registry.py GUIDANCE"),
    "backlog_migrate_v4": ("refused-native", "native_routing/registry.py GUIDANCE"),
    "backlog_canonicalize_layout": ("refused-native", "native_routing/registry.py GUIDANCE"),
    "backlog_backfill_lanes": ("refused-native", "native_routing/registry.py GUIDANCE"),
    "backlog_project_init": ("noop-initialized", "native_routing/overview.py: create-only, refuses once initialized"),
    "backlog_sync": ("operator-only", "coordinator allowlist (sync is operator-only)"),
    "backlog_handover_resync": ("operator-only", "native_routing/resync.py via served_action_refusal"),
    "backlog_issue_resync": ("operator-only", "native_routing/resync.py via served_action_refusal"),
}
# Member tools with an operator-gated mode: the class stays `member`, the gated mode is recorded.
TOOL_NOTES = {
    "backlog_store_status": "deep=True (a full integrity_check) is operator-only on a team server (backlog_server._integrity_section)",
    "backlog_blast_radius": "mode=evidence is operator-only (served_action_refusal)",
    "backlog_linear": "probe and bootstrap are operator-only (overview.served_linear_guard); retry is member (caller_scope, 004)",
    "backlog_document_import": "source other than upload and restore=True are operator-only (allowlist HOST_IMPORT / RESTORE)",
    "backlog_query": "SQL; members read only READ_ENDPOINT_TABLES, failing closed (team-server-026); command_receipts is not a table",
    "backlog_batch_update": "commands= op names bounded by INTERNAL_OPERATIONS (classified in `ops`)",
    "backlog_open_viewer": "host action; in serve mode only reports the viewer URL",
}

_LIST_TOOLS = (
    "import asyncio,hashlib,json;from taskmaster.backlog_server import mcp;"
    "print(json.dumps({t.name: hashlib.sha256(json.dumps(t.parameters, sort_keys=True).encode()).hexdigest() "
    "for t in asyncio.run(mcp.list_tools())}))")


def tool_hashes() -> dict[str, str]:
    """name -> sha256 of the input JSON schema, from a child process so no test mock leaks into the registration."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="tm-surface-root-") as name:      # private per call; parallel runs must not share it
        scratch = Path(name)
        env = dict(os.environ, TASKMASTER_ROOT=str(scratch), PYTHONPATH=str(REPO))
        done = subprocess.run([sys.executable, "-c", _LIST_TOOLS], cwd=scratch, env=env, capture_output=True, text=True,
                              timeout=120, check=True)
    return json.loads(done.stdout.strip().splitlines()[-1])


HANDSHAKE = REPO / "tm_bridge_handshake.json"
_HANDSHAKE_CODE = (
    "import asyncio,json;from taskmaster.backlog_server import mcp;"
    "o=mcp._mcp_server.create_initialization_options();"
    "print(json.dumps({'initialize': {'capabilities': o.capabilities.model_dump(mode='json', exclude_none=True),"
    " 'serverInfo': {'name': o.server_name, 'version': o.server_version}, 'instructions': o.instructions},"
    " 'tools': [t.to_mcp_tool().model_dump(mode='json', exclude_none=True, by_alias=True) for t in asyncio.run(mcp.list_tools())]},"
    " sort_keys=True))")


def handshake_snapshot() -> dict:
    """What a connecting client is told before any tool call (initialize result minus protocolVersion, and tools/list), from a child
    process so no test mock leaks in. tm_bridge.py serves this file when it has no cache of a live connect (first-ever run)."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="tm-handshake-root-") as name:
        scratch = Path(name)
        env = dict(os.environ, TASKMASTER_ROOT=str(scratch), PYTHONPATH=str(REPO))
        done = subprocess.run([sys.executable, "-c", _HANDSHAKE_CODE], cwd=scratch, env=env, capture_output=True, text=True,
                              timeout=120, check=True)
    return json.loads(done.stdout.strip().splitlines()[-1])


class _Dummy:
    def __getattr__(self, name):
        return lambda *a, **k: None


def routes_of(api_class) -> list[str]:
    return sorted(f"{','.join(sorted(r.methods - {'HEAD'}))} {r.path}" for r in api_class.routes(_Dummy()))


def viewer_api_paths() -> list[str]:
    text = (REPO / "taskmaster" / "backlog_server.py").read_text(encoding="utf-8")
    return sorted(set(re.findall(r"[\"'](/api/[A-Za-z0-9/_-]*)[\"']", text)))


def ops_table() -> dict[str, str]:
    from taskmaster.native.batch_commands import INTERNAL_OPERATIONS
    from taskmaster.native.workflow import OPERATIONS
    return {op: ("internal" if op in INTERNAL_OPERATIONS else "batch-allowed")
            for op in sorted(set(OPERATIONS) | set(INTERNAL_OPERATIONS))}


def active_text(path: Path) -> str:
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("#"))


def caddy_matcher(text: str | None = None) -> list[str]:
    """The paths of the `@taskmaster path ...` matcher, from the Caddyfile's non-comment lines."""
    text = active_text(CADDYFILE) if text is None else text
    found = [line.split()[2:] for line in text.splitlines() if re.match(r"\s*@taskmaster\s+path\s", line)]
    if len(found) != 1:
        raise AssertionError(f"expected exactly one `@taskmaster path` matcher, found {len(found)}")
    return found[0]


def public_matcher() -> list[str]:
    from taskmaster.serve.config import PUBLIC_PREFIXES
    return sorted(p for prefix in PUBLIC_PREFIXES for p in (prefix, prefix + "/*"))


def matcher_problems(matcher: list[str], expected: list[str]) -> list[str]:
    problems = [f"too narrow: {p} is missing from @taskmaster" for p in expected if p not in matcher]
    problems += [f"too wide: {p} is routed by @taskmaster but is not a public prefix" for p in matcher if p not in expected]
    return problems


def viewer_route_problems(text: str | None = None, *, viewer_port: int = 7471) -> list[str]:
    """What is wrong with the Caddyfile's route to the viewer. Empty means exactly one site block proxies to the
    viewer port, with that upstream and nothing wider (team-server-028)."""
    text = active_text(CADDYFILE) if text is None else text
    pattern = rf"reverse_proxy\s+(\S+):{viewer_port}\b"
    hits = re.findall(pattern, text)
    if not hits:
        return ["no route to the viewer"]
    problems = [f"viewer upstream is {h}:{viewer_port}, not 127.0.0.1:{viewer_port}" for h in hits if h != "127.0.0.1"]
    if len(hits) != 1:
        problems.append(f"{len(hits)} routes to the viewer, expected one")
    if re.search(r"path\s+/\*\s*$", text, re.M):
        problems.append("a matcher routes every path")
    return problems


def unit_flags() -> list[str]:
    """The 016 unit's ExecStart flags minus the placement flags the harness replaces (S10 load probe, R1)."""
    import shlex
    text = UNIT.read_text(encoding="utf-8").replace("\\\n", " ")
    line = next(l for l in text.splitlines() if l.startswith("ExecStart="))
    tokens = shlex.split(line[len("ExecStart="):])[1:]
    drop = {"--root", "--host", "--port", "--viewer-port", "--viewer-origin", "--allowed-host", "--export-push"}
    flags, skip = [], False
    for token in tokens:
        if skip:
            skip = False
        elif token in drop:
            skip = True
        elif token.split("=", 1)[0] in drop:
            continue
        else:
            flags.append(token)
    return flags


def build_surface(previous: dict | None = None) -> dict:
    from taskmaster import query_guard
    from taskmaster.coordinator import allowlist
    from taskmaster.serve import config as serve_config
    from taskmaster.serve import viewer, viewer_auth
    from taskmaster.serve.read_api import READ_TOOLS, ReadApi
    from taskmaster.serve.write_api import WriteApi

    old_tools = (previous or {}).get("tools", {})
    tools = {}
    for name, digest in sorted(tool_hashes().items()):
        klass, gate = TOOL_OVERRIDES.get(name, ("member", None))
        entry = {"schema_sha256": digest, "class": klass}
        if gate:
            entry["gate"] = gate
        if name in TOOL_NOTES:
            entry["note"] = TOOL_NOTES[name]
        # a tool the reviewer has not classed yet stays UNCLASSIFIED until someone sets its class on purpose
        if name not in old_tools and previous is not None and name not in TOOL_OVERRIDES:
            entry["class"] = UNCLASSIFIED
        elif name in old_tools and name not in TOOL_OVERRIDES:
            entry["class"] = old_tools[name]["class"]
        tools[name] = entry
    return {
        "public_prefixes": list(serve_config.PUBLIC_PREFIXES),
        "caddy_public_matcher": public_matcher(),
        "routes": sorted(set(routes_of(ReadApi)) | set(routes_of(WriteApi))),
        "viewer": {
            "public_exact": list(viewer.PUBLIC_EXACT),
            "session_exact": list(viewer.SESSION_EXACT),
            "route_prefixes": list(viewer.ROUTE_PREFIXES),
            "refused_exact": sorted(viewer.REFUSED_EXACT),
            "caddy_matcher": viewer.caddy_matcher_forms(),
            "api_paths": viewer_api_paths(),
            "headers_by_kind": {kind: [list(h) for h in viewer.response_headers(kind, "https://VIEWER_ORIGIN")]
                                for kind in viewer.KINDS},
            "cookie_names": [viewer_auth.COOKIE_SECURE, viewer_auth.COOKIE_PLAIN],
            "csrf_header": viewer_auth.CSRF_HEADER,
        },
        "coordinator": {"METHODS": sorted(allowlist.METHODS), "REMOTE": sorted(allowlist.REMOTE),
                        "SERVER_REFUSED": sorted(allowlist.SERVER_REFUSED)},
        "restricted_tables": {"READ_ENDPOINT_TABLES": sorted(query_guard.READ_ENDPOINT_TABLES),
                              "HOST_PRIVATE_TABLES": sorted(query_guard.HOST_PRIVATE_TABLES)},
        "read_tools": list(READ_TOOLS),
        "ops": ops_table(),
        "tools": tools,
    }


def surface_problems(fixture: dict, built: dict) -> list[str]:
    """Every difference between the reviewed fixture and the surface the code builds now, each naming the item."""
    problems = []
    old, new = fixture["tools"], built["tools"]
    problems += [f"tool added: {n}" for n in sorted(set(new) - set(old))]
    problems += [f"tool removed: {n}" for n in sorted(set(old) - set(new))]
    for name in sorted(set(old) & set(new)):
        if old[name]["schema_sha256"] != new[name]["schema_sha256"]:
            problems.append(f"tool schema changed: {name}")
        if old[name]["class"] != new[name]["class"]:
            problems.append(f"tool class changed: {name} {old[name]['class']} -> {new[name]['class']}")
    problems += [f"op unclassified or added: {o}" for o in sorted(set(built["ops"]) - set(fixture["ops"]))]
    problems += [f"op removed: {o}" for o in sorted(set(fixture["ops"]) - set(built["ops"]))]
    problems += [f"op class changed: {o}" for o in sorted(set(built["ops"]) & set(fixture["ops"]))
                 if built["ops"][o] != fixture["ops"][o]]
    problems += [f"route added: {r}" for r in sorted(set(built["routes"]) - set(fixture["routes"]))]
    problems += [f"route removed: {r}" for r in sorted(set(fixture["routes"]) - set(built["routes"]))]
    for key in ("public_prefixes", "caddy_public_matcher", "viewer", "coordinator", "restricted_tables", "read_tools"):
        if fixture[key] != built[key]:
            problems.append(f"{key} changed: {fixture[key]!r} -> {built[key]!r}")
    return problems


def fixture_sha256() -> str:
    return hashlib.sha256(FIXTURE.read_bytes()).hexdigest()


if __name__ == "__main__":
    if "--write-handshake" in sys.argv:
        HANDSHAKE.write_text(json.dumps(handshake_snapshot(), sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        print(f"wrote {HANDSHAKE}")
        sys.exit(0)
    if "--write" not in sys.argv:
        sys.exit("usage: python tests/surface_inventory.py --write   (review the diff: classes decide what members may call)")
    old = json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.exists() else None
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(build_surface(old), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {FIXTURE} sha256 {fixture_sha256()}")
