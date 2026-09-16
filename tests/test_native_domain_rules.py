"""User intent: prove the task/epic/phase rules exist exactly once, so the MCP
tools, the viewer HTTP path and the native command core cannot drift apart.
"""
import ast
from pathlib import Path

import pytest

from taskmaster import backlog_server
from taskmaster.native import domain

REPO = Path(__file__).resolve().parents[1]
SHARED_NAMES = (
    "ALLOWED_FIELDS", "VALID_STATUSES", "LEGAL_STATUS_TRANSITIONS", "VALID_PRIORITIES",
    "VALID_DOC_KEYS", "VALID_ARCHIVE_REASONS", "ALLOWED_AREA_FIELDS", "VALID_EPIC_STATUSES",
    "ALLOWED_EPIC_FIELDS", "VALID_DESIGN_STATUSES", "VALID_PHASE_STATUSES", "ALLOWED_PHASE_FIELDS",
)


def _assigned_names(relative):
    tree = ast.parse((REPO / relative).read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return names


@pytest.mark.parametrize("name", SHARED_NAMES)
def test_rule_tables_are_defined_once_and_shared(name):
    assert name not in _assigned_names("taskmaster/backlog_server.py")
    assert getattr(backlog_server, name) is getattr(domain, name)


@pytest.mark.parametrize("name", ["illegal_transition_message", "_completion_block_reason",
                                  "_validate_components", "_normalize_priority", "_now", "_today"])
def test_rule_functions_come_from_the_shared_layer(name):
    assert name not in _assigned_names("taskmaster/backlog_server.py")
    assert getattr(backlog_server, name).__module__ == "taskmaster.native.domain"


def test_shared_layer_never_imports_the_server_or_the_filesystem():
    tree = ast.parse((REPO / "taskmaster/native/domain.py").read_text(encoding="utf-8"))
    modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    modules |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names}
    assert modules == {"datetime", "re", "taskmaster.taskmaster_v3"}
