"""API drift must be reviewed explicitly before switching storage backends."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


def test_native_inventory_matches_registered_source_contracts():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("native_contract_inventory", root / "scripts/native_contract_inventory.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.inventory() == json.loads(module.FIXTURE.read_text(encoding="utf-8"))


def test_sql_compatibility_snapshot_matches_live_guard():
    from taskmaster import query_guard
    path = Path(__file__).parent / "fixtures/native_contracts.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    assert list(query_guard.TABLES) == contract["sql_tables"]
    assert query_guard.SCHEMA_SUMMARY == contract["query_schema"]
    assert query_guard.QUERY_TIMEOUT_S == contract["sql_timeout_seconds"]


def test_frozen_tools_match_real_mcp_registration(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, TASKMASTER_ROOT=str(tmp_path), PYTHONPATH=str(root))
    result = subprocess.run([sys.executable, "-c",
        "import asyncio,json;from taskmaster.backlog_server import mcp;"
        "print(json.dumps(sorted(t.name for t in asyncio.run(mcp.list_tools()))))"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, check=True)
    contract = json.loads((root / "tests/fixtures/native_contracts.json").read_text(encoding="utf-8"))
    assert json.loads(result.stdout.strip().splitlines()[-1]) == sorted(contract["tools"])
