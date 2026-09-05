"""User intent: `[seq N]` on every mutating public tool is the contract that
lets a caller tie an answer to the commit that produced it. A sample cannot
enforce it — the tool that loses the suffix is the one nobody sampled — so this
enumerates all 80-odd registered tools and checks the whole set.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from taskmaster import backlog_server as bs

SOURCE = Path(bs.__file__)

# A read tool may still commit a one-shot legacy backfill. That write is
# maintenance, not the tool's answer, so it does not make the tool a mutating
# one and its result carries no suffix. Each entry is a helper that owns its own
# transaction; the list is short and explicit so a new undecorated writer cannot
# hide behind it.
BACKFILL_HELPERS = {
    "_ensure_handover_status_backfilled",
    "_threads_data",
}

# Reaching this is what makes a tool a writer: it is the single latch that
# commits a transaction's work.
LATCH = "_mutate_and_save"


def _module_functions() -> dict[str, ast.AST]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _decorators(node) -> list[str]:
    names = []
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, ast.Attribute):
            names.append(f"{getattr(target.value, 'id', '?')}.{target.attr}")
    return names


def _callees(node) -> set[str]:
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _reaches(graph: dict[str, set[str]], start: str, target: str, stop: set[str]) -> bool:
    seen: set[str] = set()
    stack = [start]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        for callee in graph.get(current, ()):
            if callee == target:
                return True
            if callee in stop:
                continue  # it stamps its own result, or is an exempt backfill
            stack.append(callee)
    return False


def test_every_mutating_public_tool_is_wrapped_by_transactional():
    """Plan 3.4 asks for the enumeration, not a sample: this is the check that
    catches a tool added — or auto-merged — without the wrapper that gives it a
    transaction and a `[seq N]`."""
    functions = _module_functions()
    decorated = {
        name: _decorators(node) for name, node in functions.items()
    }
    graph = {name: _callees(node) for name, node in functions.items()}
    stamped = {name for name, decs in decorated.items() if "_transactional" in decs}
    stop = stamped | BACKFILL_HELPERS

    tools = [name for name, decs in decorated.items() if "mcp.tool" in decs]
    assert len(tools) > 50, f"the tool enumeration found only {len(tools)}"

    unstamped = sorted(
        name
        for name in tools
        if name not in stamped and _reaches(graph, name, LATCH, stop)
    )
    assert unstamped == [], (
        "these tools commit through the store but are not wrapped by "
        f"_transactional, so their results carry no [seq N]: {unstamped}"
    )


def test_the_exempt_backfill_helpers_still_exist():
    """The exemption list is only honest while it names real functions; a
    renamed helper would silently widen it."""
    functions = _module_functions()
    missing = sorted(name for name in BACKFILL_HELPERS if name not in functions)
    assert missing == [], missing


def test_every_transactional_tool_stamps_its_string_result():
    """The wrapper is the only thing that appends the suffix, so a tool that
    carries it must not be able to return a bare string."""
    functions = _module_functions()
    decorated = {name: _decorators(node) for name, node in functions.items()}
    stamped = {name for name, decs in decorated.items() if "_transactional" in decs}
    tools = {name for name, decs in decorated.items() if "mcp.tool" in decs}
    assert stamped & tools, "no tool carries @_transactional at all"
    source = SOURCE.read_text(encoding="utf-8")
    assert "return _with_seq(result, committed_frame)" in source
