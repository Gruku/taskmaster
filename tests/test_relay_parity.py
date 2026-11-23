# User intent: keep the two relays (Python Bridge, Rust relay) from drifting. Every behaviour the spec lists as duplicated must be covered by a
# black-box test run against both relays, and a change to the Python copy must be ported to relay/ before the pin moves.
# Re-pin after porting: python tests/test_relay_parity.py --write
from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PARITY = PLUGIN_ROOT / "tests" / "fixtures" / "relay_parity.json"
SPEC = PLUGIN_ROOT / "docs" / "specs" / "2026-11-23-local-server-011-rust-relay.md"
BLACKBOX = PLUGIN_ROOT / "tests" / "test_relay_blackbox.py"
BRIDGE = PLUGIN_ROOT / "tm_bridge.py"
# Everything in class Bridge is pinned except these Python-only members (the handshake cache and the spawn path); Log, Endpoint and
# the compiled-in module constants are pinned whole.
PYTHON_ONLY_BRIDGE_MEMBERS = ("cache_path", "_cache_key", "load_cache", "save_cache", "start")
PINNED_CLASSES = ("Log", "Endpoint")
PINNED_CONSTANTS = ("LOOPBACK_HOSTS", "DRAINING_TEXT", "SERVER_NAME")
PINNED_FUNCTIONS = ("may_have_committed_text",)


def duplicated_rows():
    """Row numbers of the spec's section 2 table whose Where column names Rust: the behaviours that exist in both relays."""
    rows = {}
    for line in SPEC.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\|\s*(\d+)\s*\|", line)
        if not match:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows[int(cells[0])] = cells[2]
    return rows, sorted(number for number, where in rows.items() if "Rust" in where)


def _member_name(item):
    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return item.name
    if isinstance(item, ast.Assign) and len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
        return item.targets[0].id
    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
        return item.target.id
    return None


def pinned_nodes():
    """name -> ast node for everything the pin covers; the Bridge's members are listed one by one so the pin names what moved."""
    tree = ast.parse(BRIDGE.read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Bridge":
            for item in node.body:
                name = _member_name(item)
                if name and name not in PYTHON_ONLY_BRIDGE_MEMBERS:
                    found["Bridge." + name] = item
        elif isinstance(node, ast.ClassDef) and node.name in PINNED_CLASSES:
            found[node.name] = node
        elif isinstance(node, ast.FunctionDef) and node.name in PINNED_FUNCTIONS:
            found[node.name] = node
        elif _member_name(node) in PINNED_CONSTANTS:
            found[_member_name(node)] = node
    missing = (set(PINNED_CLASSES) | set(PINNED_FUNCTIONS) | set(PINNED_CONSTANTS) | {"Bridge.__init__", "Bridge.start_lazy",
               "Bridge._shipped_handshake", "Bridge.MIN_INIT", "Bridge.handle", "Bridge.reconnect"}) - set(found)
    assert not missing, f"pinned members missing from tm_bridge.py: {sorted(missing)}"
    return found


def pinned_names():
    return sorted(pinned_nodes())


def compute_pin():
    """sha256 over the ast dump of every pinned member (comments and layout do not count)."""
    found = pinned_nodes()
    digest = hashlib.sha256()
    for name in sorted(found):
        digest.update(name.encode() + b"\x00" + ast.dump(found[name], annotate_fields=False).encode() + b"\x00")
    return digest.hexdigest()


def blackbox_tests():
    """name -> True when the black-box suite defines it with a 'relay' parameter (so it runs against both relays)."""
    tree = ast.parse(BLACKBOX.read_text(encoding="utf-8"))
    return {node.name: "relay" in [arg.arg for arg in node.args.args] for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


def load():
    return json.loads(PARITY.read_text(encoding="utf-8"))


def test_the_spec_table_still_has_the_rows_the_plan_names():
    rows, duplicated = duplicated_rows()
    assert len(rows) == 35
    assert duplicated == [5, *range(16, 29), 30, 32, 33], "the spec's duplicated rows changed: update tests/fixtures/relay_parity.json"


def test_every_duplicated_row_has_black_box_tests_that_run_against_both_relays():
    _, duplicated = duplicated_rows()
    data, tests = load(), blackbox_tests()
    missing = [row for row in duplicated if not data["rows"].get(str(row))]
    assert not missing, f"duplicated rows with no black-box test in relay_parity.json: {missing}"
    stale = {row: name for row, names in data["rows"].items() for name in names if name not in tests}
    assert not stale, f"relay_parity.json names tests that test_relay_blackbox.py does not define: {stale}"
    single = {row: name for row, names in data["rows"].items() for name in names if not tests[name]}
    assert not single, f"these tests are not parametrized over both relays (no 'relay' parameter): {single}"
    extra = [row for row in data["rows"] if int(row) not in duplicated]
    assert not extra, f"relay_parity.json maps rows that are not duplicated: {extra}"


def test_the_python_bridges_duplicated_behaviour_matches_the_pin():
    data = load()
    assert data["functions"] == pinned_names()
    assert compute_pin() == data["pin"], (
        "A duplicated function of the Python Bridge in tm_bridge.py changed. The Rust relay implements the same behaviour: port the fix "
        "to relay/ (Rust), add or extend the black-box test in tests/test_relay_blackbox.py that covers it, rebuild the binaries, then "
        "re-pin with: python tests/test_relay_parity.py --write")


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        data = load()
        data["pin"] = compute_pin()
        data["functions"] = pinned_names()
        PARITY.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8", newline="\n")
        print("pinned", data["pin"])
    else:
        sys.exit("usage: python tests/test_relay_parity.py --write")
