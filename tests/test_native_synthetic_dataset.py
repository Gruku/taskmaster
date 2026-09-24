# User intent: the N16 synthetic datasets must be reproducible by seed, keep the measured CodeMaestro
# shape at every scale, and never carry authored content from the source copy into the committed
# statistics or the generated projects.
"""Unit tests for scripts/native_synthetic_dataset.py (statistics only; no store, no processes)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import native_synthetic_dataset as synth  # noqa: E402


@pytest.fixture(scope="module")
def stats():
    return synth.load_stats(synth.DEFAULT_STATS)


def as_entities(generated: dict) -> dict:
    """Generated rows in `native_twins.committed`'s shape, the input `compute_stats` reads."""
    return {(kind, doc["id"]): (doc, body, bool(doc.get("archived")))
            for kind, rows in generated.items() for doc, body in rows}


def mean(hist: dict) -> float:
    return synth.Hist(hist).mean()


def near(actual, expected, *, rel, floor=0.0):
    return abs(actual - expected) <= max(rel * abs(expected), floor)


def test_committed_statistics_are_anonymous_and_complete(stats):
    synth.assert_anonymous(stats)
    assert stats["format"] == synth.FORMAT
    assert stats["counts"]["task"] > 1000 and stats["paths"]["distinct"] > 100
    for key in ("epic_fanout", "phase_fanout", "depends_on", "paths", "handover_task_ids", "bodies", "links", "archived"):
        assert stats[key], key


@pytest.mark.allow_projection_bypass  # the generator seeds a legacy project's files, as a user would
def test_generation_is_deterministic_by_seed(stats, tmp_path):
    first = synth.generate_entities(stats, 0.1, 7)
    again = synth.generate_entities(stats, 0.1, 7)
    other = synth.generate_entities(stats, 0.1, 8)
    assert json.dumps(first, sort_keys=True) == json.dumps(again, sort_keys=True)
    assert json.dumps(first, sort_keys=True) != json.dumps(other, sort_keys=True)
    # The projection bytes too: two writes of one seed are byte-identical trees.
    trees = []
    for name in ("a", "b"):
        root = tmp_path / name
        synth.write_projection(root, synth.generate_entities(stats, 0.1, 7), project="synthetic-7")
        trees.append({p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted(root.rglob("*")) if p.is_file()})
    assert trees[0] == trees[1] and len(trees[0]) > 300


@pytest.mark.parametrize("scale", [1.0, 10.0])
def test_distributions_follow_the_statistics(stats, scale):
    generated = synth.generate_entities(stats, scale, 16)
    got = synth.compute_stats(as_entities(generated))
    for kind, count in stats["counts"].items():
        assert near(got["counts"][kind], count * scale, rel=0.01, floor=1), kind
    for kind in ("task", "handover", "epic"):
        assert near(got["archived"][kind], stats["archived"][kind], rel=0.35, floor=0.02), kind
    assert near(mean(got["epic_fanout"]), mean(stats["epic_fanout"]), rel=0.1)
    assert near(got["phase_share"], stats["phase_share"], rel=0.02)
    assert near(mean(got["depends_on"]["degree"]), mean(stats["depends_on"]["degree"]), rel=0.15)
    assert near(got["depends_on"]["same_epic_share"], stats["depends_on"]["same_epic_share"], rel=0.05)
    assert near(mean(got["paths"]["per_entity"]["task"]), mean(stats["paths"]["per_entity"]["task"]), rel=0.1)
    assert near(mean(got["handover_task_ids"]), mean(stats["handover_task_ids"]), rel=0.1)
    paths, source = got["paths"], stats["paths"]
    assert near(paths["distinct"], source["distinct"] * scale, rel=0.15)
    assert near(paths["glob_share"], source["glob_share"], rel=0.25)
    assert near(mean(paths["depth"]), mean(source["depth"]), rel=0.05)
    assert near(mean(paths["refs_per_path"]), mean(source["refs_per_path"]), rel=0.15)
    for depth in ("2", "3", "4"):  # literal-prefix sharing below the top level
        assert near(paths["prefix_ratio"][depth], source["prefix_ratio"][depth], rel=0.35), depth
    # The top level keeps its measured layout: the head of the rank curve holds at 10x too.
    assert near(paths["top_rank_share"][0], source["top_rank_share"][0], rel=0.15)
    assert near(paths["line_suffix_by_kind"]["bug"], source["line_suffix_by_kind"]["bug"], rel=0.15)
    assert near(mean(got["links"]["task"]["per_entity"]), mean(stats["links"]["task"]["per_entity"]), rel=0.3)
    notes, source_notes = got["text"]["task"]["notes"], stats["text"]["task"]["notes"]
    assert near(notes["present"], source_notes["present"], rel=0.05)
    assert near(notes["length"][10], source_notes["length"][10], rel=0.15)  # median length
    assert near(got["bodies"]["handover"][10], stats["bodies"]["handover"][10], rel=0.15)


SENTINELS = ("Zorblax", "secret-repo", "Quuxington", "ACME-PRIVATE", "fizzbin.cs", "wibble-epic")


def authored_source() -> dict:
    """A tiny source project whose every authored string carries a sentinel."""
    entities = {("epic", "wibble-epic"): ({"id": "wibble-epic", "name": "Zorblax epic", "status": "active",
                                           "description": "Quuxington plans"}, "", False),
                ("phase", "ACME-PRIVATE-phase"): ({"id": "ACME-PRIVATE-phase", "name": "Zorblax phase",
                                                   "status": "active"}, "", False)}
    for n in range(12):
        ident = f"wibble-epic-{n:03d}"
        entities[("task", ident)] = ({
            "id": ident, "title": f"Zorblax task {n}", "status": "todo" if n % 3 else "Quuxington-status",
            "priority": "high", "epic": "wibble-epic", "phase": "ACME-PRIVATE-phase",
            "notes": "Quuxington notes " * n, "depends_on": [f"wibble-epic-{n - 1:03d}"] if n else [],
            "anchors": [f"secret-repo/src/Zorblax/fizzbin.cs", f"secret-repo/src/Quuxington/*.cs"],
            "links": [{"target": "ISS-001", "type": "relates_to", "note": "Zorblax"}],
        }, "ACME-PRIVATE body", False)
    entities[("issue", "ISS-001")] = ({"id": "ISS-001", "title": "Zorblax issue", "status": "open", "severity": "P1",
                                       "components": ["ACME-PRIVATE"], "location": ["secret-repo/fizzbin.cs:12"],
                                       "impact": "Quuxington", "links": []}, "Zorblax", False)
    entities[("handover", "2026-01-01-zorblax")] = ({"id": "2026-01-01-zorblax", "tldr": "Zorblax handover",
                                                     "task_ids": ["wibble-epic-001"], "status": "open",
                                                     "session_kind": "continuity"}, "Quuxington", True)
    return entities


def test_statistics_carry_no_authored_content(stats):
    derived = synth.compute_stats(authored_source())
    synth.assert_anonymous(derived)
    text = json.dumps(derived)
    for sentinel in SENTINELS:
        assert sentinel not in text
    assert derived["enums"]["task.status"]["other"] > 0  # an unknown enum value is counted, never named
    generated = json.dumps(synth.generate_entities(derived, 3.0, 1))
    for sentinel in SENTINELS:
        assert sentinel not in generated


@pytest.mark.parametrize("poison", [
    {"counts": {"Zorblax": 1}},
    {"text": {"task": {"zorblax": {"present": 1.0, "length": [1]}}}},  # a lowercase name is not structural
    {"enums": {"task.status": {"Quuxington-status": 1.0}}},
    {"paths": {"top_rank_share": ["secret-repo"]}},
])
def test_assert_anonymous_refuses_content(stats, poison):
    document = json.loads(json.dumps(stats))
    for key, value in poison.items():
        document[key] = {**document.get(key, {}), **value}
    with pytest.raises(ValueError):
        synth.assert_anonymous(document)


def test_stats_refuses_an_unmarked_source(tmp_path):
    (tmp_path / ".taskmaster" / "local").mkdir(parents=True)
    with pytest.raises(SystemExit, match="benchmark-copy"):
        synth.entities_from_copy(tmp_path)
