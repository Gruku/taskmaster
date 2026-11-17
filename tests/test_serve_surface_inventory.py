# User intent: ratchet the team server's internet-facing surface against a reviewed fixture (routes, tools and their class, command ops, coordinator methods, restricted tables, the Caddyfile's public matcher and viewer route), so a new or changed entry fails until someone re-confirms it; the security reviewer signs off against the fixture's sha256 (team-server-015 B2.4, Step 9).
from __future__ import annotations

import copy
import json

import pytest

import surface_inventory as inv


@pytest.fixture(scope="module")
def fixture():
    return json.loads(inv.FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def built(fixture):
    return inv.build_surface(fixture)


def test_the_built_surface_equals_the_reviewed_fixture(fixture, built):
    assert inv.surface_problems(fixture, built) == [], (
        "the serve surface changed; review it, then `python tests/surface_inventory.py --write` and re-confirm classes")
    assert built == fixture


def test_a_removed_tool_a_changed_schema_an_unclassified_op_and_an_added_route_each_fail_naming_the_item(fixture):
    removed = copy.deepcopy(fixture)
    removed["tools"].pop("backlog_status")
    assert inv.surface_problems(fixture, removed) == ["tool removed: backlog_status"]
    changed = copy.deepcopy(fixture)
    changed["tools"]["backlog_status"]["schema_sha256"] = "0" * 64
    assert inv.surface_problems(fixture, changed) == ["tool schema changed: backlog_status"]
    unclassified = copy.deepcopy(fixture)
    unclassified["ops"]["task.brand_new"] = "batch-allowed"
    assert inv.surface_problems(fixture, unclassified) == ["op unclassified or added: task.brand_new"]
    routed = copy.deepcopy(fixture)
    routed["routes"].append("GET /read/v1/new")
    assert inv.surface_problems(fixture, routed) == ["route added: GET /read/v1/new"]
    reclassed = copy.deepcopy(fixture)
    reclassed["tools"]["backlog_sync"]["class"] = "member"
    assert inv.surface_problems(fixture, reclassed) == ["tool class changed: backlog_sync operator-only -> member"]


def test_every_tool_and_op_is_classified(fixture):
    assert {t["class"] for t in fixture["tools"].values()} <= set(inv.CLASSES)
    assert set(fixture["ops"].values()) <= set(inv.OP_CLASSES)
    for name in fixture["read_tools"]:
        assert fixture["tools"][name]["class"] == "member", name
    for name, entry in fixture["tools"].items():
        if entry["class"] in ("operator-only", "refused-native", "serve-refused", "noop-initialized"):
            assert entry.get("gate"), f"{name} names no gate"


def test_the_internal_ops_are_exactly_the_denylist(fixture):
    from taskmaster.native.batch_commands import INTERNAL_OPERATIONS
    assert sorted(op for op, klass in fixture["ops"].items() if klass == "internal") == sorted(INTERNAL_OPERATIONS)


def test_the_new_member_callable_tools_must_not_arrive_unclassified(fixture, built):
    assert inv.UNCLASSIFIED not in {t["class"] for t in built["tools"].values()}


# the Caddyfile check (review I2) ------------------------------------------------------------------------------------

def test_the_matcher_parser_reads_the_shipped_forms():
    assert inv.caddy_matcher("\t@taskmaster path /mcp /mcp/* /read/v1 /read/v1/*\n") == ["/mcp", "/mcp/*", "/read/v1", "/read/v1/*"]
    assert inv.caddy_matcher("# @taskmaster path /x\n@taskmaster path /y\n".replace("# @taskmaster path /x\n", "")) == ["/y"]
    with pytest.raises(AssertionError):
        inv.caddy_matcher("handle {\n}\n")


def test_the_shipped_matcher_is_exactly_the_public_prefixes():
    # team-server-025 is already in the 016 Caddyfile on feat/team-collab: there is no P-025 pin to keep
    assert inv.matcher_problems(inv.caddy_matcher(), inv.public_matcher()) == []


def test_a_narrow_matcher_names_the_missing_prefixes_and_a_wide_one_the_extra():
    expected = inv.public_matcher()
    narrow = ["/mcp", "/mcp/*"]
    assert inv.matcher_problems(narrow, expected) == [f"too narrow: {p} is missing from @taskmaster" for p in
                                                      ("/read/v1", "/read/v1/*", "/write/v1", "/write/v1/*")]
    assert inv.matcher_problems([*expected, "/api/*"], expected) == [
        "too wide: /api/* is routed by @taskmaster but is not a public prefix"]
    assert inv.matcher_problems(["/*"], expected)[-1].startswith("too wide: /*")


def test_the_unit_flags_the_load_probe_runs_with_are_the_shipped_ones():
    assert inv.unit_flags() == ["--linger-bytes", "4194304"]


# the viewer route (P-028 closed by 028) -------------------------------------------------------------------------------------------

def test_the_shipped_caddyfile_routes_to_the_viewer_through_one_loopback_upstream():
    assert inv.viewer_route_problems() == []        # P-028 closed: 028 ships the viewer site block


def test_the_viewer_route_check_accepts_exactly_one_loopback_upstream():
    block = "viewer.tm.example.com {\n\treverse_proxy 127.0.0.1:7471\n}\n"
    assert inv.viewer_route_problems(block) == []
    assert inv.viewer_route_problems(block.replace("127.0.0.1", "10.0.0.5")) == [
        "viewer upstream is 10.0.0.5:7471, not 127.0.0.1:7471"]
    assert inv.viewer_route_problems(block + block) == ["2 routes to the viewer, expected one"]
    assert "a matcher routes every path" in inv.viewer_route_problems(block + "\t@x path /*\n")


def test_the_fixture_has_a_stable_sha256():
    assert len(inv.fixture_sha256()) == 64
