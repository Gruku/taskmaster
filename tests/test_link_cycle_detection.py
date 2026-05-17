from taskmaster_v3 import find_cycle, would_create_cycle


def test_find_cycle_self_edge():
    # T-001 depends_on T-001
    graph = {"T-001": ["T-001"]}
    assert find_cycle(graph) == ["T-001", "T-001"]


def test_find_cycle_two_node():
    # T-001 -> T-002 -> T-001
    graph = {"T-001": ["T-002"], "T-002": ["T-001"]}
    cycle = find_cycle(graph)
    assert cycle is not None
    # Must form a closed loop.
    assert cycle[0] == cycle[-1]
    assert set(cycle[:-1]) == {"T-001", "T-002"}


def test_find_cycle_three_node():
    # T-001 -> T-002 -> T-003 -> T-001
    graph = {"T-001": ["T-002"], "T-002": ["T-003"], "T-003": ["T-001"]}
    cycle = find_cycle(graph)
    assert cycle is not None
    assert cycle[0] == cycle[-1]
    assert {"T-001", "T-002", "T-003"}.issubset(set(cycle))


def test_find_cycle_dag_returns_none():
    graph = {"T-001": ["T-002"], "T-002": ["T-003"], "T-003": []}
    assert find_cycle(graph) is None


def test_find_cycle_disconnected_components():
    graph = {
        "T-001": ["T-002"],
        "T-002": [],
        "T-003": ["T-004"],
        "T-004": ["T-003"],   # cycle in second component
    }
    cycle = find_cycle(graph)
    assert cycle is not None
    assert set(cycle[:-1]) == {"T-003", "T-004"}


def test_find_cycle_five_node():
    # T-001 -> T-002 -> T-003 -> T-004 -> T-005 -> T-002 (cycle)
    graph = {
        "T-001": ["T-002"],
        "T-002": ["T-003"],
        "T-003": ["T-004"],
        "T-004": ["T-005"],
        "T-005": ["T-002"],
    }
    cycle = find_cycle(graph)
    assert cycle is not None
    assert cycle[0] == cycle[-1]
    # Cycle covers T-002..T-005.
    assert {"T-002", "T-003", "T-004", "T-005"}.issubset(set(cycle))


def test_find_cycle_disjoint_chains():
    # Two disjoint DAG chains, no cycle.
    graph = {
        "T-001": ["T-002"], "T-002": ["T-003"], "T-003": [],
        "T-100": ["T-200"], "T-200": ["T-300"], "T-300": [],
    }
    assert find_cycle(graph) is None


def test_would_create_cycle_blocks_self_edge():
    graph = {"T-001": []}
    assert would_create_cycle(graph, "T-001", "T-001") is True


def test_would_create_cycle_blocks_two_node_loop():
    # T-001 -> T-002 already; adding T-002 -> T-001 closes the loop.
    graph = {"T-001": ["T-002"], "T-002": []}
    assert would_create_cycle(graph, "T-002", "T-001") is True


def test_would_create_cycle_allows_chain_extension():
    graph = {"T-001": ["T-002"], "T-002": []}
    assert would_create_cycle(graph, "T-002", "T-003") is False


def test_would_create_cycle_blocks_three_node_loop():
    # T-001 -> T-002 -> T-003 already; adding T-003 -> T-001 closes.
    graph = {"T-001": ["T-002"], "T-002": ["T-003"], "T-003": []}
    assert would_create_cycle(graph, "T-003", "T-001") is True
