# User intent: N16 needs small and 10x CodeMaestro-shaped projects without copying any of CodeMaestro's
# authored content: derive anonymous SHAPE statistics from a marked copy, commit them, and regenerate
# deterministic legacy projects (optionally cut over to native by the production cutover) from them.
"""Synthetic CodeMaestro-shaped datasets for the N16 acceptance harness.

    # 1. Shape statistics from a MARKED copy (never the live project). The store is
    #    file-copied to a scratch directory first; the copy is never opened for writing.
    python scripts/native_synthetic_dataset.py stats --source-copy COPY --out docs/reports/data/n16-shape-stats.json

    # 2. A project from statistics only, deterministic by seed.
    python scripts/native_synthetic_dataset.py generate --stats docs/reports/data/n16-shape-stats.json \
        --scale 0.1 --seed 16 --out C:/.../Temp/n16/small [--native]

`--scale` multiplies every count (0.1 = "small", 1 = CodeMaestro-sized, 10 = "10x") while the
per-entity distributions (epic/phase fan-out, depends_on degree, anchors per task, path depth,
literal-prefix sharing, glob share, handover task_ids sizes, body sizes, archive ratio, links) are
kept. The statistics file holds numbers, field names and allow-listed enum values only: no IDs,
titles, prose, paths or names (`assert_anonymous` enforces it before a file is written).

`generate` writes a legacy project: projection files (`backlog.yaml`, per-entity markdown) in a
fresh `git init` repository (no remote, hooks outside, marked `.benchmark-copy`), then adopts them
into a legacy store the way a fresh bridge client does (in a subprocess), and checks the adopted
entity counts. `--native` then runs the production cutover (`python -m taskmaster.native.cutover
--json --confirm-stopped`) in a subprocess.
"""
from __future__ import annotations

import argparse
import bisect
from collections import Counter, defaultdict
from contextlib import closing
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FORMAT = "taskmaster-n16-shape-stats/1"
PRESETS = {"small": 0.1, "1x": 1.0, "10x": 10.0}
KINDS = ("epic", "phase", "task", "bug", "issue", "handover", "decision", "idea", "note")
# Enum values that may appear in the statistics. Anything else is counted as "other" and never named.
ENUMS = {
    "task.status": ("todo", "in-progress", "in-review", "done", "archived", "blocked"),
    "task.priority": ("critical", "high", "medium", "low"),
    "task.lane": ("full", "standard", "express"),
    "task.estimate": ("XS", "S", "M", "L", "XL"),
    "task.archive_reason": ("done", "deprecated", "duplicate", "wont-fix", "superseded"),
    "epic.status": ("active", "planned", "done", "archived"),
    "phase.status": ("planned", "active", "done", "archived"),
    "bug.status": ("open", "fixed", "shelved", "adopted", "promoted"),
    "bug.severity": ("P0", "P1", "P2", "P3"),
    "issue.status": ("open", "investigating", "fixed", "wontfix", "duplicate"),
    "issue.severity": ("P0", "P1", "P2", "P3"),
    "handover.status": ("open", "closed", "superseded"),
    "handover.session_kind": ("continuity", "deep-context", "milestone", "auto-stage", "task-complete"),
    "decision.status": ("open", "resolved", "dropped"),
    "idea.status": ("", "parking-lot", "exploring", "promoted", "dropped", "archived"),
    "link.type": ("depends_on", "blocks", "fixes", "fixed_in_task", "relates_to", "supersedes",
                  "superseded_by", "duplicate_of", "duplicates", "references", "referenced_by"),
    "kind": KINDS,
}
# Free-text fields whose LENGTH is recorded (per kind); the text itself never is.
TEXT_FIELDS = {
    "task": ("title", "tldr", "notes", "next_step"),
    "epic": ("name", "description", "done_when"),
    "phase": ("name", "description"),
    "bug": ("title",),
    "issue": ("title", "impact", "evidence", "tldr"),
    "handover": ("tldr", "next_action"),
    "decision": ("title",),
    "idea": ("title", "tldr"),
    "note": (),
}
QUANTILES = [i / 20 for i in range(21)]
PATH_SOURCES = {"task": "anchors", "bug": "location", "issue": "location"}
GLOB = re.compile(r"[*?\[]")
LINE_SUFFIX = re.compile(r":\d+(-\d+)?$")
BASE_DATE = dt.datetime(2026, 1, 5, 9, 0, 0, tzinfo=dt.timezone.utc)
PYTHON = sys.executable


# ── statistics ───────────────────────────────────────────────────────────────
def histogram(values) -> dict:
    return {str(k): v for k, v in sorted(Counter(int(x) for x in values).items())}


def quantiles(values) -> list:
    ordered = sorted(values)
    if not ordered:
        return [0] * len(QUANTILES)
    return [ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))] for q in QUANTILES]


def shares(counter: Counter, allowed) -> dict:
    total = sum(counter.values())
    out = {}
    for key, count in counter.items():
        name = key if key in allowed else "other"
        out[name] = out.get(name, 0) + count
    return {k: round(v / total, 6) for k, v in sorted(out.items())} if total else {}


def _split_path(ref: str):
    """(segments, is_glob, has_line_suffix) of one anchor/location reference."""
    ref = str(ref).strip().replace("\\", "/")
    line = bool(LINE_SUFFIX.search(ref))
    ref = LINE_SUFFIX.sub("", ref)
    segments = [s for s in ref.split("/") if s]
    return segments, bool(GLOB.search(ref)), line


def _glob_form(segments) -> str:
    leaf = segments[-1] if segments else ""
    if leaf == "**" or leaf == "*":
        return "dir_star"
    if leaf.startswith("*."):
        return "star_ext"
    return "other"


def path_stats(references) -> dict:
    """Distinct paths, reuse, depth, literal-prefix sharing and glob share, without the paths."""
    refs = defaultdict(int)
    line_refs = 0
    for ref in references:
        segments, _, line = _split_path(ref)
        if not segments:
            continue
        refs["/".join(segments)] += 1
        line_refs += line
    distinct = list(refs)
    parsed = {p: p.split("/") for p in distinct}
    depth = histogram(len(s) for s in parsed.values())
    prefix_ratio = {}
    for d in range(1, 7):
        deeper = [s for s in parsed.values() if len(s) > d]
        if deeper:
            prefix_ratio[str(d)] = round(len({tuple(s[:d]) for s in deeper}) / len(deeper), 6)
    top = Counter(s[0] for s in parsed.values() if len(s) > 1)
    total_top = sum(top.values())
    globs = [p for p in distinct if GLOB.search(p)]
    glob_forms = Counter(_glob_form(parsed[p]) for p in globs)
    extensions = Counter(s[-1].rsplit(".", 1)[-1] for s in parsed.values() if "." in s[-1] and not GLOB.search(s[-1]))
    return {
        "distinct": len(distinct), "references": sum(refs.values()),
        "refs_per_path": histogram(refs.values()), "depth": depth, "prefix_ratio": prefix_ratio,
        "top_rank_share": [round(c / total_top, 6) for _, c in top.most_common(200)] if total_top else [],
        "glob_share": round(len(globs) / len(distinct), 6) if distinct else 0.0,
        "glob_forms": {k: round(v / len(globs), 6) for k, v in sorted(glob_forms.items())} if globs else {},
        "line_suffix_share": round(line_refs / max(1, sum(refs.values())), 6),
        "extension_rank_share": [round(c / max(1, sum(extensions.values())), 6)
                                 for _, c in extensions.most_common(20)],
    }


def compute_stats(entities: dict) -> dict:
    """`entities`: {(kind, id): (doc, body, archived)} from either authority (tests.native_twins.committed)."""
    by_kind = defaultdict(list)
    for (kind, ident), (doc, body, archived) in entities.items():
        if kind in KINDS:
            by_kind[kind].append((ident, doc or {}, body or "", bool(archived)))
    ids = {kind: {ident for ident, *_ in rows} for kind, rows in by_kind.items()}
    stats = {"format": FORMAT, "counts": {k: len(by_kind.get(k, ())) for k in KINDS},
             "archived": {}, "enums": {}, "text": {}, "bodies": {}, "links": {}}
    for kind in KINDS:
        rows = by_kind.get(kind, [])
        stats["archived"][kind] = round(sum(a for *_, a in rows) / len(rows), 6) if rows else 0.0
        stats["bodies"][kind] = quantiles(len(b) for _, _, b, _ in rows)
        stats["text"][kind] = {}
        for field in TEXT_FIELDS[kind]:
            lengths = [len(str(d.get(field))) for _, d, _, _ in rows if d.get(field) not in (None, "")]
            stats["text"][kind][field] = {"present": round(len(lengths) / len(rows), 6) if rows else 0.0,
                                          "length": quantiles(lengths)}
        for key, allowed in ENUMS.items():
            owner, _, field = key.partition(".")
            if owner == kind:
                stats["enums"][key] = shares(Counter(str(d.get(field) or "") for _, d, _, _ in rows
                                                     if field in d), allowed)
        # links: per-entity count, type and target-kind shares
        counts, types, targets = [], Counter(), Counter()
        for _, doc, _, _ in rows:
            links = doc.get("links") if isinstance(doc.get("links"), list) else []
            counts.append(len(links))
            for link in links:
                if isinstance(link, dict):
                    types[str(link.get("type") or "")] += 1
                    target = str(link.get("target") or "")
                    targets[next((k for k in KINDS if target in ids.get(k, ())), "missing")] += 1
        stats["links"][kind] = {"per_entity": histogram(counts), "types": shares(types, ENUMS["link.type"]),
                                "target_kinds": shares(targets, KINDS + ("missing",))}
    tasks = by_kind.get("task", [])
    epic_ids, phase_ids, task_ids = ids.get("epic", set()), ids.get("phase", set()), ids.get("task", set())
    task_epic = {i: d.get("epic") for i, d, _, _ in tasks}
    stats["epic_fanout"] = histogram(Counter(task_epic[i] for i in task_epic if task_epic[i] in epic_ids).get(e, 0)
                                     for e in epic_ids)
    stats["orphan_task_share"] = round(sum(1 for e in task_epic.values() if e not in epic_ids) / max(1, len(tasks)), 6)
    phased = Counter(d.get("phase") for _, d, _, _ in tasks if d.get("phase") in phase_ids)
    stats["phase_share"] = round(sum(phased.values()) / max(1, len(tasks)), 6)
    stats["phase_fanout"] = histogram(phased.get(p, 0) for p in phase_ids)
    degrees, same, dangling, total = [], 0, 0, 0
    for ident, doc, _, _ in tasks:
        deps = doc.get("depends_on") or []
        deps = [deps] if isinstance(deps, str) else [d for d in deps if isinstance(d, str)]
        degrees.append(len(deps))
        for dep in deps:
            total += 1
            dangling += dep not in task_ids
            same += dep in task_ids and task_epic.get(dep) == doc.get("epic")
    stats["depends_on"] = {"degree": histogram(degrees), "same_epic_share": round(same / max(1, total), 6),
                           "dangling_share": round(dangling / max(1, total), 6)}
    references, per_entity = [], {}
    for kind, field in PATH_SOURCES.items():
        sizes = []
        for _, doc, _, _ in by_kind.get(kind, []):
            values = doc.get(field) or []
            values = [values] if isinstance(values, str) else [v for v in values if isinstance(v, str)]
            sizes.append(len(values))
            references.extend(values)
        per_entity[kind] = histogram(sizes)
    stats["paths"] = path_stats(references)
    stats["paths"]["per_entity"] = per_entity
    stats["paths"]["line_suffix_by_kind"] = {}
    for kind, field in PATH_SOURCES.items():
        refs = [v for _, d, _, _ in by_kind.get(kind, []) for v in (d.get(field) or []) if isinstance(v, str)]
        stats["paths"]["line_suffix_by_kind"][kind] = round(sum(bool(LINE_SUFFIX.search(v)) for v in refs) / max(1, len(refs)), 6)
    components = [d.get("components") or [] for k in ("bug", "issue") for _, d, _, _ in by_kind.get(k, [])]
    stats["components"] = {"per_entity": histogram(len(c) for c in components),
                           "distinct": len({str(x) for c in components for x in c})}
    handovers = by_kind.get("handover", [])
    stats["handover_task_ids"] = histogram(len(d.get("task_ids") or []) for _, d, _, _ in handovers)
    stats["handover_task_ids_resolving"] = round(
        sum(t in task_ids for _, d, _, _ in handovers for t in (d.get("task_ids") or []))
        / max(1, sum(len(d.get("task_ids") or []) for _, d, _, _ in handovers)), 6)
    stats["decision_options"] = histogram(len(d.get("options") or []) for _, d, _, _ in by_kind.get("decision", []))
    return stats


# Every structural key `compute_stats` writes; anything else in a statistics file is refused.
STRUCTURAL_KEYS = frozenset({
    "bodies", "components", "counts", "dangling_share", "decision_options", "degree", "depth", "distinct",
    "enums", "epic_fanout", "extension_rank_share", "format", "glob_forms", "glob_share", "handover_task_ids",
    "handover_task_ids_resolving", "length", "line_suffix_by_kind", "line_suffix_share", "links",
    "orphan_task_share", "paths", "per_entity", "phase_fanout", "phase_share", "prefix_ratio", "present",
    "references", "refs_per_path", "same_epic_share", "target_kinds", "text", "top_rank_share", "types", "archived"})


def assert_anonymous(stats) -> None:
    """Refuse a statistics document that could carry authored content: every string must be a
    known structural key, an allow-listed enum value or a numeric histogram key."""
    allowed = set(KINDS) | {v for values in ENUMS.values() for v in values} | {"other", "missing", FORMAT}
    allowed |= {f for fields in TEXT_FIELDS.values() for f in fields} | set(ENUMS) | {"dir_star", "star_ext"}

    def walk(value, where):
        if isinstance(value, dict):
            for key, item in value.items():
                if not (key in allowed or key in STRUCTURAL_KEYS or key.isdigit()):
                    raise ValueError(f"statistics key {key!r} at {where} is not structural")
                walk(item, f"{where}.{key}")
        elif isinstance(value, list):
            for item in value:
                walk(item, where)
        elif isinstance(value, str):
            if value not in allowed:
                raise ValueError(f"statistics string {value!r} at {where} is not allow-listed")
        elif not (value is None or isinstance(value, (int, float, bool))):
            raise ValueError(f"unexpected value type at {where}")
    walk(stats, "stats")


def _require_marked_copy(source: Path) -> Path:
    source = source.resolve()
    if not (source / ".benchmark-copy").is_file():
        raise SystemExit(f"refusing {source}: no .benchmark-copy marker (copies only, never a live project)")
    return source


def entities_from_copy(source: Path) -> dict:
    source = _require_marked_copy(source)
    local = source / ".taskmaster" / "local"
    if not (local / "store.db").is_file():
        raise SystemExit(f"{local / 'store.db'} not found")
    scratch = Path(tempfile.mkdtemp(prefix="n16-stats-"))
    try:
        target = scratch / ".taskmaster" / "local"
        target.mkdir(parents=True)
        for name in ("store.db", "store.db-wal"):
            if (local / name).is_file():
                shutil.copy2(local / name, target / name)
        sys.path.insert(0, str(REPO / "tests"))
        from native_twins import committed
        return committed(scratch)
    finally:
        from taskmaster import store
        store.reset_for_tests()
        shutil.rmtree(scratch, ignore_errors=True)


def cmd_stats(args) -> int:
    stats = compute_stats(entities_from_copy(args.source_copy))
    assert_anonymous(stats)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(stats, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out), "counts": stats["counts"]}))
    return 0


# ── sampling helpers ────────────────────────────────────────────────────────
class Hist:
    def __init__(self, hist: dict):
        items = sorted((int(k), v) for k, v in (hist or {"0": 1}).items() if v > 0) or [(0, 1)]
        self.values = [k for k, _ in items]
        self.cumulative, total = [], 0
        for _, v in items:
            total += v
            self.cumulative.append(total)
        self.total = total

    def sample(self, rng) -> int:
        return self.values[bisect.bisect_right(self.cumulative, rng.random() * self.total)
                           if self.cumulative[-1] else 0]

    def mean(self) -> float:
        previous, acc = 0, 0.0
        for value, cum in zip(self.values, self.cumulative):
            acc += value * (cum - previous)
            previous = cum
        return acc / self.total


def from_quantiles(points, rng) -> int:
    """Inverse-CDF sample from a 21-point quantile table, linearly interpolated."""
    u = rng.random() * (len(points) - 1)
    low = int(u)
    high = min(low + 1, len(points) - 1)
    return int(round(points[low] + (points[high] - points[low]) * (u - low)))


def pick_share(table: dict, rng, default):
    items = [(k, v) for k, v in (table or {}).items() if k != "other" and v > 0]
    if not items:
        return default
    total = sum(v for _, v in items)
    roll, acc = rng.random() * total, 0.0
    for key, value in items:
        acc += value
        if roll < acc:
            return key
    return items[-1][0]


def scaled(count: float, scale: float) -> int:
    return max(0, int(round(count * scale)))


# A fixed, seed-independent pseudo-word vocabulary: generated text never borrows real words.
_SYLLABLES = ["ka", "lo", "mi", "ne", "su", "ta", "ri", "vo", "de", "pa", "zu", "fi", "go", "ha", "ju", "we"]
VOCABULARY = sorted({a + b + c for a in _SYLLABLES for b in _SYLLABLES for c in ("n", "r", "s", "x", "")})[:1200]


def text(rng, length: int) -> str:
    if length <= 0:
        return ""
    words, size = [], 0
    while size < length:
        word = VOCABULARY[int(len(VOCABULARY) * rng.random() ** 2.5)]
        words.append(word)
        size += len(word) + 1
    return " ".join(words)[:max(1, length)].strip() or "x"


def title(rng, length: int) -> str:
    return (text(rng, max(8, length)) or "item").capitalize()


def stamp(offset_minutes: int) -> str:
    return (BASE_DATE + dt.timedelta(minutes=offset_minutes)).strftime("%Y-%m-%dT%H:%M")


# ── path universe ───────────────────────────────────────────────────────────
def build_paths(stats: dict, references: int, rng) -> list[str]:
    """A multiset of `references` path references whose distinct-path reuse, depth, literal-prefix
    sharing and glob share follow `stats['paths']`. Every name is synthetic."""
    p = stats["paths"]
    if references <= 0:
        return []
    reuse = Hist(p["refs_per_path"])
    counts = []
    while sum(counts) < references:
        counts.append(max(1, reuse.sample(rng)))
    depth = Hist(p["depth"])
    depths = [max(1, depth.sample(rng)) for _ in counts]
    n = len(counts)
    # Level-by-level prefix tree: at depth d, `ratio_d * (paths deeper than d)` distinct prefixes,
    # the first level weighted by the measured (anonymous) top-level rank shares.
    assignment = [[] for _ in range(n)]
    parents = {(): list(range(n))}
    rank = p.get("top_rank_share") or [1.0]
    for d in range(1, max(depths)):
        ratio = float(p["prefix_ratio"].get(str(d), p["prefix_ratio"].get(str(d - 1), 0.5)) if p["prefix_ratio"] else 0.5)
        children = {}
        deeper_total = sum(1 for x in depths if x > d)
        wanted_total = max(1, round(ratio * deeper_total))
        if d == 1:  # a larger project keeps the measured top-level layout and grows beneath it
            wanted_total = min(wanted_total, len(rank))
        for parent, members in sorted(parents.items()):
            deeper = [m for m in members if depths[m] > d]
            if not deeper:
                continue
            wanted = max(1, min(len(deeper), round(wanted_total * len(deeper) / deeper_total)))
            if d == 1:
                weights = [rank[min(len(rank) - 1, int(i * len(rank) / wanted))] or 1e-6 for i in range(wanted)]
            else:
                weights = [1.0 / (i + 1) for i in range(wanted)]
            # Deep paths share long prefixes: the shallowest members seed one child each (the lightest
            # slots first) and the deeper ones crowd into the heaviest slots, so few prefixes carry
            # members to the next level, as measured.
            rng.shuffle(deeper)
            deeper.sort(key=lambda m: depths[m])
            seeds = list(range(wanted - 1, -1, -1))
            rest = sorted(rng.choices(range(wanted), weights=weights, k=len(deeper) - wanted), reverse=True)
            slots = seeds + rest
            for member, slot in zip(deeper, slots):
                children.setdefault(parent + (slot,), []).append(member)
        for key, members in children.items():
            for m in members:
                assignment[m] = list(key)
        parents = children
    glob_share = float(p.get("glob_share", 0.0))
    forms = p.get("glob_forms") or {"dir_star": 1.0}
    exts = p.get("extension_rank_share") or [1.0]
    paths, dir_globs = [], set()
    for index in range(n):
        dirs = [("t" if level == 0 else "d") + f"{slot:03d}" for level, slot in enumerate(assignment[index])]
        ext = f"x{rng.choices(range(len(exts)), weights=exts)[0]}"
        parent = "/".join(dirs[:max(0, depths[index] - 1)])
        if rng.random() < glob_share:
            form = pick_share(forms, rng, "dir_star")
            if form == "dir_star" and parent not in dir_globs:
                dir_globs.add(parent)  # one `dir/**` per directory; a repeat would merge into it
                leaf = "**"
            elif form == "star_ext":
                leaf = f"*.{ext}"
            else:
                leaf = f"f{index:05d}*.{ext}"
        else:
            leaf = f"f{index:05d}.{ext}"
        paths.append("/".join(filter(None, (parent, leaf))))
    multiset = [path for path, count in zip(paths, counts) for _ in range(count)]
    rng.shuffle(multiset)
    return multiset[:references]


# ── generation ──────────────────────────────────────────────────────────────
def spread(total: int, groups: int, fanout: Hist, rng) -> list[int]:
    """`total` items over `groups` groups with sizes drawn from `fanout`, rescaled to the total."""
    if groups <= 0:
        return []
    draws = [fanout.sample(rng) for _ in range(groups)]
    weight = sum(draws) or groups
    sizes = [int(total * d / weight) for d in draws] if sum(draws) else [total // groups] * groups
    for index in rng.sample(range(groups), groups):
        if sum(sizes) >= total:
            break
        if draws[index] or not sum(draws):
            sizes[index] += 1
    while sum(sizes) < total:
        sizes[rng.randrange(groups)] += 1
    return sizes


def generate_entities(stats: dict, scale: float, seed: int) -> dict:
    """{kind: [(doc, body)]} in a deterministic order, from statistics only."""
    rng = random.Random(seed)
    counts = {k: scaled(stats["counts"].get(k, 0), scale) for k in KINDS}
    counts["epic"] = max(1, counts["epic"])
    counts["phase"] = max(1, counts["phase"])
    enums = stats["enums"]
    txt = stats["text"]

    def field_text(kind, field, fn=text):
        spec = txt.get(kind, {}).get(field)
        if not spec or rng.random() >= spec["present"]:
            return None
        return fn(rng, from_quantiles(spec["length"], rng))

    def body(kind):
        return text(rng, from_quantiles(stats["bodies"].get(kind, [0]), rng))

    def archived(kind):
        return rng.random() < stats["archived"].get(kind, 0.0)

    def archive_given_status(kind):
        """P(archived flag | status archived): the measured flag ratio over the archived-status share."""
        share = (enums.get(f"{kind}.status") or {}).get("archived", 0.0)
        return min(1.0, stats["archived"].get(kind, 0.0) / share) if share else 0.0

    out = {k: [] for k in KINDS}
    minute = 0
    # phases
    for index in range(counts["phase"]):
        status = pick_share(enums.get("phase.status"), rng, "planned")
        doc = {"id": f"ph{index + 1:03d}", "name": title(rng, 30), "status": status, "order": index + 1,
               "created": stamp(minute)}
        description = field_text("phase", "description")
        if description:
            doc["description"] = description
        out["phase"].append((doc, ""))
        minute += 7
    # epics
    for index in range(counts["epic"]):
        status = pick_share(enums.get("epic.status"), rng, "active")
        doc = {"id": f"ep{index + 1:04d}", "name": field_text("epic", "name", title) or title(rng, 30),
               "status": status, "created": stamp(minute)}
        for field in ("description", "done_when"):
            value = field_text("epic", field)
            if value:
                doc[field] = value
        if status == "archived" and rng.random() < archive_given_status("epic"):
            doc.update(archived=stamp(minute + 1), archive_reason="done")
        out["epic"].append((doc, ""))
        minute += 11
    # tasks: epic fan-out, phase fan-out, anchors, depends_on
    epic_ids = [d["id"] for d, _ in out["epic"]]
    phase_ids = [d["id"] for d, _ in out["phase"]]
    sizes = spread(counts["task"], len(epic_ids), Hist(stats["epic_fanout"]), rng)
    phase_sizes = spread(int(round(counts["task"] * stats.get("phase_share", 0.0))), len(phase_ids),
                         Hist(stats["phase_fanout"]), rng)
    phase_pool = [p for p, n in zip(phase_ids, phase_sizes) for _ in range(n)]
    rng.shuffle(phase_pool)
    anchors_hist = Hist(stats["paths"]["per_entity"].get("task", {"0": 1}))
    tasks = []
    for epic, size in zip(epic_ids, sizes):
        for number in range(1, size + 1):
            tasks.append({"id": f"{epic}-{number:03d}", "epic": epic, "order": float(number)})
    rng.shuffle(tasks)  # phases and paths are spread across epics, as measured
    for task in tasks:
        task["phase"] = phase_pool.pop() if phase_pool else None
    tasks.sort(key=lambda t: t["id"])
    anchor_counts = [anchors_hist.sample(rng) for _ in tasks]
    location_hist = {k: Hist(stats["paths"]["per_entity"].get(k, {"0": 1})) for k in ("bug", "issue")}
    location_counts = {k: [location_hist[k].sample(rng) for _ in range(counts[k])] for k in ("bug", "issue")}
    refs = build_paths(stats, sum(anchor_counts) + sum(map(sum, location_counts.values())), rng)
    line_share = stats["paths"].get("line_suffix_by_kind", {})
    literal_share = max(0.05, 1.0 - float(stats["paths"].get("glob_share", 0.0)))  # globs take no line suffix

    def take(n, kind):
        chosen = []
        while n > 0 and refs:
            path = refs.pop()
            if kind != "task" and "*" not in path and rng.random() < line_share.get(kind, 0.0) / literal_share:
                path = f"{path}:{rng.randint(1, 900)}"
            if path not in chosen:
                chosen.append(path)
            n -= 1
        return chosen

    by_epic = defaultdict(list)
    for task in tasks:
        by_epic[task["epic"]].append(task["id"])
    degree = Hist(stats["depends_on"]["degree"])
    same_epic = stats["depends_on"]["same_epic_share"]
    index_of = {t["id"]: i for i, t in enumerate(tasks)}
    carry = {}
    for position, (task, anchor_count) in enumerate(zip(tasks, anchor_counts)):
        status = pick_share(enums.get("task.status"), rng, "todo")
        doc = {"id": task["id"], "title": field_text("task", "title", title) or title(rng, 40), "status": status,
               "priority": pick_share(enums.get("task.priority"), rng, "medium"), "epic": task["epic"],
               "order": task["order"], "created": stamp(minute)}
        if task["phase"]:
            doc["phase"] = task["phase"]
        for field in ("tldr", "notes", "next_step"):
            value = field_text("task", field)
            if value:
                doc[field] = value
        lane = pick_share(enums.get("task.lane"), rng, None)
        if lane:
            doc["lane"] = lane
        estimate = pick_share(enums.get("task.estimate"), rng, None)
        if estimate:
            doc["estimate"] = estimate
        anchors = take(anchor_count, "task")
        if anchors:
            doc["anchors"] = anchors
        # depends_on: earlier tasks only (a DAG), same epic with the measured share
        # A same-epic draw with no earlier task in the epic is carried to the epic's next task,
        # so the same-epic share holds instead of leaking into cross-epic edges.
        deps = []
        for _ in range(degree.sample(rng) + carry.pop(task["epic"], 0)):
            if rng.random() < same_epic:
                candidates = [t for t in by_epic[task["epic"]] if index_of[t] < position]
                if not candidates:
                    carry[task["epic"]] = carry.get(task["epic"], 0) + 1
                    continue
            else:
                candidates = [tasks[rng.randrange(position)]["id"]] if position else []
            if candidates:
                dep = rng.choice(candidates)
                if dep not in deps:
                    deps.append(dep)
        if deps:
            doc["depends_on"] = deps
        if status == "archived":
            doc["archive_reason"] = pick_share(enums.get("task.archive_reason"), rng, "done")
            if rng.random() < archive_given_status("task"):  # in tasks/archive/, as measured
                doc["archived"] = stamp(minute + 3)
        elif status in ("done",):
            doc["completed"] = stamp(minute + 2)
        out["task"].append((doc, body("task")))
        minute += 3
    # bugs, issues
    for kind, prefix in (("bug", "B-"), ("issue", "ISS-")):
        components = Hist(stats["components"]["per_entity"])
        vocabulary = [f"comp{n:02d}" for n in range(max(1, int(stats["components"]["distinct"] * min(scale, 1) or 1)))]
        for index in range(counts[kind]):
            doc = {"id": f"{prefix}{index + 1:03d}", "title": field_text(kind, "title", title) or title(rng, 40),
                   "status": pick_share(enums.get(f"{kind}.status"), rng, "open"),
                   "severity": pick_share(enums.get(f"{kind}.severity"), rng, "P2"),
                   "components": sorted(set(rng.choice(vocabulary) for _ in range(components.sample(rng)))),
                   "location": take(location_counts[kind][index], kind),
                   "discovered": stamp(minute), "discovered_by": "user"}
            if kind == "bug":
                doc.update(found_in=None, fix_commit=None, adopted_into=None, promoted_to=None, links=[])
            else:
                impact = field_text("issue", "impact") or "impact"
                doc.update(impact=impact, evidence=field_text("issue", "evidence") or impact, resolved=None,
                           related_tasks=[], fixed_in_task=None, duplicate_of=None, promoted_from=[],
                           tldr=field_text("issue", "tldr") or "", tracker_id=None)
            if archived(kind):
                doc["archived"] = True
            out[kind].append((doc, body(kind)))
            minute += 13
    # handovers
    sizes_hist = Hist(stats["handover_task_ids"])
    task_ids = [d["id"] for d, _ in out["task"]]
    for index in range(counts["handover"]):
        day = (BASE_DATE + dt.timedelta(hours=7 * index)).date().isoformat()
        tldr = field_text("handover", "tldr") or text(rng, 40)
        size = sizes_hist.sample(rng)
        doc = {"id": f"{day}-h{index + 1:05d}", "date": day, "created": stamp(minute) + ":00+00:00", "tldr": tldr,
               "next_action": field_text("handover", "next_action") or "", "session_kind":
               pick_share(enums.get("handover.session_kind"), rng, "continuity"),
               "task_ids": sorted(set(rng.sample(task_ids, min(size, len(task_ids))))) if task_ids else [],
               "status": pick_share(enums.get("handover.status"), rng, "open"),
               "status_changed": stamp(minute) + ":00+00:00", "status_user_set": False}
        if archived("handover"):
            doc["archived"] = True
        out["handover"].append((doc, body("handover")))
        minute += 17
    # decisions, ideas, notes
    options_hist = Hist(stats.get("decision_options") or {"2": 1})
    for index in range(counts["decision"]):
        status = pick_share(enums.get("decision.status"), rng, "open")
        options = [text(rng, 30) for _ in range(max(2, options_hist.sample(rng)))]
        doc = {"id": f"DEC-{index + 1:03d}", "title": field_text("decision", "title", title) or title(rng, 40),
               "status": status, "options": options, "recommendation": 1, "task_id": None, "related_issues": [],
               "branch": None, "resolved_with": 1 if status == "resolved" else None,
               "resolved_rationale": text(rng, 40) if status == "resolved" else None,
               "dropped_reason": text(rng, 20) if status == "dropped" else None,
               "created_at": stamp(minute) + ":00+00:00",
               "resolved_at": stamp(minute + 5) + ":00+00:00" if status == "resolved" else None,
               "raised_in": None, "referenced_in": [], "resolved_in": None}
        out["decision"].append((doc, body("decision")))
        minute += 19
    for index in range(counts["idea"]):
        doc = {"id": f"IDEA-{index + 1:03d}", "title": field_text("idea", "title", title) or title(rng, 40),
               "created": stamp(minute) + ":00Z", "created_by": "Claude",
               "status": pick_share(enums.get("idea.status"), rng, ""), "tags": [], "related_tasks": [],
               "related_issues": [], "promoted_to": None, "archived": archived("idea"),
               "tldr": field_text("idea", "tldr") or ""}
        out["idea"].append((doc, body("idea")))
        minute += 23
    for index in range(counts["note"]):
        doc = {"id": f"NOTE-{index + 1:03d}", "author": "claude", "created": stamp(minute) + ":00Z",
               "updated": stamp(minute) + ":00Z", "pinned": False, "archived": archived("note"), "archived_at": None}
        if doc["archived"]:
            doc["archived_at"] = stamp(minute + 1) + ":00Z"
        out["note"].append((doc, body("note") or text(rng, 20)))
        minute += 29
    _add_links(out, stats, rng)
    return out


def _add_links(out: dict, stats: dict, rng) -> None:
    """Typed links with their inverses. The measured per-entity counts include both directions, so
    about half are drawn as forward links and the inverse supplies the other half."""
    from taskmaster.taskmaster_v3 import REVERSE_TYPE
    docs = {kind: {doc["id"]: doc for doc, _ in rows} for kind, rows in out.items()}
    ids = {kind: sorted(d) for kind, d in docs.items()}
    for kind in ("task", "bug", "issue", "handover", "idea"):
        spec = stats["links"].get(kind) or {}
        hist = Hist(spec.get("per_entity") or {"0": 1})
        for source in ids[kind]:
            wanted = hist.sample(rng)
            forward = wanted // 2 + (1 if wanted % 2 and rng.random() < 0.5 else 0)
            for _ in range(forward):
                target_kind = pick_share(spec.get("target_kinds"), rng, "task")
                if target_kind not in ids or not ids[target_kind]:
                    continue
                target = rng.choice(ids[target_kind])
                if target == source:
                    continue
                link_type = pick_share(spec.get("types"), rng, "relates_to")
                src, dst = docs[kind][source], docs[target_kind][target]
                if any(l.get("target") == target for l in src.get("links", [])):
                    continue
                src.setdefault("links", []).append({"target": target, "type": link_type})
                if target_kind in ("task", "bug", "issue", "handover", "idea"):
                    dst.setdefault("links", []).append({"target": source, "type": REVERSE_TYPE[link_type]})


# ── writing a legacy project ────────────────────────────────────────────────
def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_") and k != "TASKMASTER_ROOT"}
    env.update(extra)
    return env


def git(root, *args):
    done = subprocess.run(["git", "-C", str(root), *args], env=clean_env(), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if done.returncode:
        raise RuntimeError(f"git {args} failed: {done.stderr[-800:]}")
    return done.stdout


def write_projection(root: Path, entities: dict, project: str) -> int:
    from taskmaster.store import render_backlog_file, render_entity_file
    backlog = root / ".taskmaster"
    written = 0

    def put(rel: str, content: bytes):
        nonlocal written
        path = backlog / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        written += 1

    epics = [dict(doc) for doc, _ in entities["epic"]]
    phases = [dict(doc) for doc, _ in entities["phase"]]
    content, _ = render_backlog_file({"version": 4, "project": project, "meta": {"schema_version": 4},
                                      "epics": [dict(e) for e in epics], "phases": [dict(p) for p in phases]})
    put("backlog.yaml", content)
    for kind, directory in (("epic", "epics"), ("phase", "phases")):
        for doc, body in entities[kind]:
            content, _ = render_entity_file(kind, doc, body)
            if content is not None:
                put(f"{directory}/{doc['id']}.md", content)
    for doc, body in entities["task"]:
        content, _ = render_entity_file("task", doc, body)
        put(("tasks/archive/" if doc.get("archived") else "tasks/") + f"{doc['id']}.md", content)
    for kind, directory, archive in (("bug", "bugs", "archive/"), ("issue", "issues", "archive/"),
                                     ("decision", "decisions", ""), ("idea", "ideas", ""),
                                     ("note", "notes", "_archive/")):
        for doc, body in entities[kind]:
            content, _ = render_entity_file(kind, doc, body)
            put(f"{directory}/{archive if doc.get('archived') else ''}{doc['id']}.md", content)
    for doc, body in entities["handover"]:
        content, _ = render_entity_file("handover", doc, body)
        rel = f"handovers/_archive/{doc['id'][:4]}/{doc['id']}.md" if doc.get("archived") else f"handovers/{doc['id']}.md"
        put(rel, content)
    for sub in ("local", "local/cache"):
        (backlog / sub).mkdir(parents=True, exist_ok=True)
    (backlog / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    return written


ADOPT = r"""
import json, os, sys
from taskmaster import backlog_server as bs
bs.backlog_status()
bs.backlog_handover_list()
from taskmaster import store
store.reset_for_tests()
import sqlite3
con = sqlite3.connect(os.path.join(sys.argv[1], '.taskmaster', 'local', 'store.db'))
print(json.dumps(dict(con.execute("SELECT kind, COUNT(*) FROM entities WHERE deleted=0 GROUP BY kind").fetchall())))
"""


def adopt(root: Path) -> tuple[dict, float]:
    """Legacy adoption in a fresh process, as a new bridge client (handover latch unset) does it."""
    started = time.perf_counter()
    done = subprocess.run([PYTHON, "-c", ADOPT, str(root)], cwd=root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=7200,
                          env=clean_env(PYTHONPATH=str(REPO), TASKMASTER_ROOT=str(root),
                                        TASKMASTER_SERVICE_IDLE_SECONDS="5"))
    if done.returncode:
        raise RuntimeError(f"adoption failed: {done.stderr[-3000:]}")
    return json.loads(done.stdout.strip().splitlines()[-1]), round(time.perf_counter() - started, 2)


def cutover(root: Path) -> tuple[dict, float]:
    started = time.perf_counter()
    done = subprocess.run([PYTHON, "-m", "taskmaster.native.cutover", "--root", str(root), "--json", "--confirm-stopped"],
                          cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=7200,
                          env=clean_env(PYTHONPATH=str(REPO)))
    seconds = round(time.perf_counter() - started, 2)
    try:
        report = json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        report = {"ok": False, "stdout_tail": done.stdout[-1500:], "stderr_tail": done.stderr[-1500:]}
    if done.returncode or not report.get("ok"):
        raise RuntimeError(f"cutover failed (exit {done.returncode}): {json.dumps(report)[:3000]}")
    return {k: report.get(k) for k in ("ok", "mode", "completed_stages", "warnings")}, seconds


def init_repository(out: Path, hooks: Path) -> None:
    out.mkdir(parents=True)
    git(out, "init", "-q")
    git(out, "config", "user.email", "n16-synthetic@example.invalid")
    git(out, "config", "user.name", "n16 synthetic")
    git(out, "config", "core.hooksPath", str(hooks))
    git(out, "config", "core.autocrlf", "false")
    (out / ".benchmark-copy").write_text("n16 synthetic dataset\n", encoding="utf-8")
    (out / ".gitignore").write_text(".benchmark-copy\n.synthetic-dataset.json\n.taskmaster/local/\n", encoding="utf-8")


def generate(stats: dict, *, scale: float, seed: int, out: Path, native: bool = False, adopt_store: bool = True) -> dict:
    out = out.resolve()
    if out.exists():
        raise SystemExit(f"{out} exists; generated datasets are single-use")
    hooks = out.parent / ".n16-empty-hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    entities = generate_entities(stats, scale, seed)
    init_repository(out, hooks)
    files = write_projection(out, entities, project=f"synthetic-{seed}")
    generated_s = round(time.perf_counter() - started, 2)
    git(out, "add", "-A")
    git(out, "commit", "-q", "-m", "synthetic projection")
    counts = {k: len(v) for k, v in entities.items()}
    report = {"seed": seed, "scale": scale, "stats_sha256": hashlib.sha256(json.dumps(stats, sort_keys=True).encode()).hexdigest(),
              "counts": counts, "files": files, "generate_s": generated_s, "authority": "files"}
    if adopt_store:
        adopted, adopt_s = adopt(out)
        missing = {k: (counts[k], adopted.get(k, 0)) for k in KINDS if adopted.get(k, 0) != counts[k]}
        if missing:
            raise RuntimeError(f"adoption lost entities (generated, adopted): {missing}")
        report.update(adopted=adopted, adopt_s=adopt_s, authority="legacy")
        if git(out, "status", "--porcelain"):
            git(out, "add", "-A")
            git(out, "commit", "-q", "-m", "adopted projection")
    if native:
        cut, cut_s = cutover(out)
        report.update(cutover=cut, cutover_s=cut_s, authority="native")
        if git(out, "status", "--porcelain"):
            git(out, "add", "-A")
            git(out, "commit", "-q", "-m", "cut-over projection")
    (out / ".synthetic-dataset.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    return report


def load_stats(path: Path) -> dict:
    stats = json.loads(path.read_text(encoding="utf-8"))
    if stats.get("format") != FORMAT:
        raise SystemExit(f"{path} is not a {FORMAT} statistics file")
    assert_anonymous(stats)
    return stats


def parse_scale(value: str) -> float:
    if value in PRESETS:
        return PRESETS[value]
    scale = float(value.rstrip("xX"))
    if not 0 < scale <= 50:
        raise argparse.ArgumentTypeError("scale must be in (0, 50]")
    return scale


DEFAULT_STATS = REPO / "docs" / "reports" / "data" / "n16-shape-stats.json"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("stats", help="derive anonymous shape statistics from a marked copy")
    s.add_argument("--source-copy", type=Path, required=True)
    s.add_argument("--out", type=Path, required=True)
    g = sub.add_parser("generate", help="write a synthetic project from statistics")
    g.add_argument("--stats", type=Path, default=DEFAULT_STATS)
    g.add_argument("--scale", type=parse_scale, default=1.0, help="factor or preset: small (0.1), 1x, 10x")
    g.add_argument("--seed", type=int, default=16)
    g.add_argument("--out", type=Path, required=True)
    g.add_argument("--native", action="store_true", help="cut over to native through the production cutover")
    g.add_argument("--no-adopt", action="store_true", help="write projection files only")
    args = parser.parse_args(argv)
    if args.command == "stats":
        return cmd_stats(args)
    report = generate(load_stats(args.stats), scale=args.scale, seed=args.seed, out=args.out,
                      native=args.native, adopt_store=not args.no_adopt)
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
