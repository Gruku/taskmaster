from __future__ import annotations

"""
blast_radius.py — Pure utility module for blast radius analysis.
No side effects, safe to import for testing.
"""

import re
import subprocess
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path


# ---------------------------------------------------------------------------
# Task 1: Config helpers
# ---------------------------------------------------------------------------

@dataclass
class BlastRadiusConfig:
    fan_out_threshold: int = 5
    max_file_scan: int = 1000
    shared_dirs: list[str] = field(default_factory=list)


def load_config(meta: dict) -> BlastRadiusConfig:
    """Load BlastRadiusConfig from a backlog meta dict."""
    raw = meta.get("blast_radius", {})
    return BlastRadiusConfig(
        fan_out_threshold=raw.get("fan_out_threshold", 5),
        max_file_scan=raw.get("max_file_scan", 1000),
        shared_dirs=raw.get("shared_dirs", []),
    )


# ---------------------------------------------------------------------------
# Task 2: Git diff helper
# ---------------------------------------------------------------------------

def get_changed_files(branch: str, base: str, cwd: Path) -> list[str]:
    """Return list of files changed between base and branch via git diff."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...{branch}"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=cwd,
        )
        if result.returncode != 0:
            return []
        lines = result.stdout.strip().splitlines()
        return [line for line in lines if line]
    except (subprocess.TimeoutExpired, OSError, Exception):
        return []


# ---------------------------------------------------------------------------
# Task 3: Import parsers
# ---------------------------------------------------------------------------

def parse_imports_python(source: str) -> set[str]:
    """Extract imported module names from Python source."""
    imports: set[str] = set()
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # from X import Y
        m = re.match(r"^from\s+([\w.]+)\s+import\s+", stripped)
        if m:
            imports.add(m.group(1))
            continue
        # import X (possibly dotted, possibly aliased)
        m = re.match(r"^import\s+([\w.]+)", stripped)
        if m:
            imports.add(m.group(1))
    return imports


def parse_imports_js(source: str) -> set[str]:
    """Extract imported paths from JS/TS source."""
    imports: set[str] = set()
    # import ... from 'path' or "path"
    for m in re.finditer(r"""import\s+[^'"]*from\s+['"]([^'"]+)['"]""", source):
        imports.add(m.group(1))
    # import 'path' or import "path" (side-effect import)
    for m in re.finditer(r"""^import\s+['"]([^'"]+)['"]""", source, re.MULTILINE):
        imports.add(m.group(1))
    # require('path') or require("path")
    for m in re.finditer(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""", source):
        imports.add(m.group(1))
    # import('path') dynamic
    for m in re.finditer(r"""import\s*\(\s*['"]([^'"]+)['"]\s*\)""", source):
        imports.add(m.group(1))
    return imports


def parse_imports_go(source: str) -> set[str]:
    """Extract imported paths from Go source."""
    imports: set[str] = set()
    # Single import: import "path"
    for m in re.finditer(r'^import\s+"([^"]+)"', source, re.MULTILINE):
        imports.add(m.group(1))
    # Import block: import ( "a" "b" )
    block_m = re.search(r'import\s*\((.*?)\)', source, re.DOTALL)
    if block_m:
        block = block_m.group(1)
        for m in re.finditer(r'"([^"]+)"', block):
            imports.add(m.group(1))
    return imports


_PARSER_MAP: dict[str, callable] = {
    ".py": parse_imports_python,
    ".js": parse_imports_js,
    ".ts": parse_imports_js,
    ".tsx": parse_imports_js,
    ".jsx": parse_imports_js,
    ".mjs": parse_imports_js,
    ".go": parse_imports_go,
}

SUPPORTED_EXTENSIONS: set[str] = set(_PARSER_MAP.keys())


def parse_imports(source: str, extension: str) -> set[str]:
    """Dispatch to the correct import parser based on file extension."""
    parser = _PARSER_MAP.get(extension)
    if parser is None:
        return set()
    return parser(source)


# ---------------------------------------------------------------------------
# Task 4: Import graph tracing
# ---------------------------------------------------------------------------

def _resolve_import_to_file(import_name: str, target_rel: str) -> bool:
    """
    Heuristic: does import_name refer to the file at target_rel?
    Converts file path to dotted module path, then checks suffix match.
    """
    # Strip extension from target path
    p = Path(target_rel)
    # Convert path separators to dots and strip extension
    without_ext = p.with_suffix("")
    dotted = str(without_ext).replace("\\", "/").replace("/", ".")

    # Check if import_name is a suffix of the dotted path or exact match
    # e.g. "foo" matches "src.foo", "bar.baz" matches "lib.bar.baz"
    if dotted == import_name:
        return True
    if dotted.endswith("." + import_name):
        return True
    # Also check: last segment match (simple "import foo" for "src/foo.py")
    stem = p.stem
    if import_name == stem:
        return True
    # Check if import_name as dotted path is suffix of dotted
    if dotted.endswith(import_name.replace("/", ".")):
        return True
    return False


def find_importers(
    target_rel: str,
    project_root: Path,
    config: BlastRadiusConfig,
    sub_repo: Path | None = None,
) -> list[str]:
    """Find all project files that import target_rel."""
    search_root = sub_repo if sub_repo else project_root
    importers: list[str] = []
    count = 0

    for ext in SUPPORTED_EXTENSIONS:
        for fpath in search_root.rglob(f"*{ext}"):
            if count >= config.max_file_scan:
                break
            count += 1
            rel = str(fpath.relative_to(project_root)).replace("\\", "/")
            if rel == target_rel:
                continue
            try:
                source = fpath.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            imported_names = parse_imports(source, ext)
            for name in imported_names:
                if _resolve_import_to_file(name, target_rel):
                    importers.append(rel)
                    break

    return importers


def find_importers_with_limit(
    target_rel: str,
    project_root: Path,
    config: BlastRadiusConfig,
    sub_repo: Path | None = None,
) -> tuple[list[str], bool]:
    """Find importers, returning (list, truncated_flag)."""
    search_root = sub_repo if sub_repo else project_root
    importers: list[str] = []
    count = 0
    truncated = False

    for ext in SUPPORTED_EXTENSIONS:
        for fpath in search_root.rglob(f"*{ext}"):
            if count >= config.max_file_scan:
                truncated = True
                break
            count += 1
            rel = str(fpath.relative_to(project_root)).replace("\\", "/")
            if rel == target_rel:
                continue
            try:
                source = fpath.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            imported_names = parse_imports(source, ext)
            for name in imported_names:
                if _resolve_import_to_file(name, target_rel):
                    importers.append(rel)
                    break
            if len(importers) >= config.fan_out_threshold * 10:
                truncated = True
                break

    return importers, truncated


def trace_dependency_graph(
    changed_files: list[str],
    depths: dict[str, int],
    project_root: Path,
    config: BlastRadiusConfig,
    sub_repo: Path | None = None,
) -> dict[str, list[str]]:
    """
    BFS import graph tracing up to specified depths per file.
    Returns {file: [direct_importers]} for all reachable nodes.
    """
    graph: dict[str, list[str]] = {}
    queue: list[tuple[str, int]] = []

    for f in changed_files:
        depth = depths.get(f, 1)
        if depth > 0:
            queue.append((f, depth))
        else:
            graph[f] = []

    visited: set[str] = set(changed_files)

    while queue:
        current, remaining_depth = queue.pop(0)
        if current in graph:
            continue
        importers = find_importers(current, project_root, config, sub_repo)
        graph[current] = importers

        if remaining_depth > 1:
            for imp in importers:
                if imp not in visited:
                    visited.add(imp)
                    queue.append((imp, remaining_depth - 1))

    return graph


# ---------------------------------------------------------------------------
# Task 5: Export change detection
# ---------------------------------------------------------------------------

def _extract_exports(source: str, extension: str) -> set[str]:
    """Regex-based export extraction per language."""
    exports: set[str] = set()

    if extension == ".py":
        # Module-scope def and class (not indented, not starting with _)
        for m in re.finditer(r"^(def|class)\s+([a-zA-Z]\w*)", source, re.MULTILINE):
            exports.add(m.group(2))

    elif extension in (".js", ".ts", ".tsx", ".jsx", ".mjs"):
        # export function/class/const/let/var/default
        for m in re.finditer(
            r"^export\s+(?:default\s+)?(?:function|class|const|let|var)\s+([a-zA-Z_$][\w$]*)",
            source,
            re.MULTILINE,
        ):
            exports.add(m.group(1))
        # export { name, name2 }
        for m in re.finditer(r"^export\s*\{([^}]+)\}", source, re.MULTILINE):
            for name in re.findall(r"\b([a-zA-Z_$][\w$]*)\b", m.group(1)):
                exports.add(name)

    elif extension == ".go":
        # Capitalized function names = public
        for m in re.finditer(r"^func\s+(?:\([^)]+\)\s+)?([A-Z]\w*)\s*\(", source, re.MULTILINE):
            exports.add(m.group(1))

    return exports


def detect_export_changes(old_source: str, new_source: str, extension: str) -> bool:
    """Return True if the set of exports changed between old and new."""
    old_exports = _extract_exports(old_source, extension)
    new_exports = _extract_exports(new_source, extension)
    return old_exports != new_exports


def has_export_changes(file_rel: str, base_branch: str, project_root: Path) -> bool:
    """
    Git-based: compare exports in base_branch vs current disk version.
    Returns False if file doesn't exist in git or on error.
    """
    extension = Path(file_rel).suffix
    try:
        result = subprocess.run(
            ["git", "show", f"{base_branch}:{file_rel}"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=project_root,
        )
        if result.returncode != 0:
            return False
        old_source = result.stdout
    except (subprocess.TimeoutExpired, OSError):
        return False

    file_path = project_root / file_rel
    try:
        new_source = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False

    return detect_export_changes(old_source, new_source, extension)


# ---------------------------------------------------------------------------
# Task 6: Adaptive depth heuristic
# ---------------------------------------------------------------------------

# Leaf directories that suggest shallow blast radius
_LEAF_DIR_PREFIXES = ("plugins/", "components/", "pages/", "views/", "screens/")


def _is_shared_dir(file_rel: str, config: BlastRadiusConfig) -> bool:
    """Return True if file lives in a configured shared directory."""
    normalized = file_rel.replace("\\", "/")
    for shared in config.shared_dirs:
        prefix = shared.rstrip("/") + "/"
        if normalized.startswith(prefix) or normalized == shared:
            return True
    return False


def _is_leaf_dir(file_rel: str) -> bool:
    """Return True if file lives in a known leaf directory."""
    normalized = file_rel.replace("\\", "/")
    for prefix in _LEAF_DIR_PREFIXES:
        if normalized.startswith(prefix):
            return True
    return False


def compute_blast_depth(
    file_rel: str,
    fan_out: int,
    has_export_change: bool,
    priority: str,
    config: BlastRadiusConfig,
    depth_override: str | None,
) -> int:
    """
    Voting system to compute blast radius depth.
    Each signal votes 0 (shallow), 1 (normal), or 2 (deep). Max vote wins.
    """
    # Manual overrides take precedence
    if depth_override == "shallow":
        return 1
    if depth_override == "deep":
        return 2

    votes: list[int] = []

    # Signal 1: File location
    if _is_shared_dir(file_rel, config):
        votes.append(2)
    elif _is_leaf_dir(file_rel):
        votes.append(0)
    else:
        votes.append(1)

    # Signal 2: Fan-out
    if fan_out <= 3:
        votes.append(0)
    elif fan_out <= 10:
        votes.append(1)
    else:
        votes.append(2)

    # Signal 3: Export change
    votes.append(2 if has_export_change else 0)

    # Signal 4: Priority
    if priority in ("P0", "P1"):
        votes.append(2)
    elif priority == "P2":
        votes.append(1)
    else:  # P3 or unknown
        votes.append(0)

    return max(votes)


# ---------------------------------------------------------------------------
# Task 7: Anchor cross-referencing
# ---------------------------------------------------------------------------

def _anchor_matches_path(anchor: str, file_path: str) -> bool:
    """
    Return True if anchor refers to file_path.
    Skips URL anchors (http/https/localhost).
    """
    # Skip URLs
    if anchor.startswith(("http://", "https://", "localhost")):
        return False

    # Exact match
    if anchor == file_path:
        return True

    # Glob match
    if fnmatch(file_path, anchor):
        return True

    # Prefix match (anchor ends with /)
    if anchor.endswith("/") and file_path.startswith(anchor):
        return True

    # Prefix without trailing slash
    if file_path.startswith(anchor.rstrip("/") + "/"):
        return True

    return False


def find_overlapping_tasks(
    affected_paths: list[str],
    all_tasks: list[dict],
    exclude_task_id: str,
) -> list[dict]:
    """
    Find tasks that share anchored paths with affected_paths.
    Returns [{task_id, title, status, shared_paths}].
    Skips archived tasks and self (exclude_task_id).
    """
    results: list[dict] = []

    for task in all_tasks:
        task_id = task.get("id", "")
        if task_id == exclude_task_id:
            continue
        status = task.get("status", "")
        if status == "archived":
            continue

        anchors = task.get("anchors", [])
        if not anchors:
            continue

        shared: list[str] = []
        for path in affected_paths:
            for anchor in anchors:
                if _anchor_matches_path(anchor, path):
                    shared.append(path)
                    break

        if shared:
            results.append({
                "task_id": task_id,
                "title": task.get("title", ""),
                "status": status,
                "shared_paths": shared,
            })

    return results


# ---------------------------------------------------------------------------
# Task 8: Fan-out computation
# ---------------------------------------------------------------------------

def compute_fan_out_scores(
    changed_files: list[str],
    project_root: Path,
    config: BlastRadiusConfig,
    sub_repo: Path | None = None,
) -> dict[str, int]:
    """Return {file: importer_count} for each changed file."""
    scores: dict[str, int] = {}
    for f in changed_files:
        importers = find_importers(f, project_root, config, sub_repo)
        scores[f] = len(importers)
    return scores


# ---------------------------------------------------------------------------
# Task 9: Predictive analysis
# ---------------------------------------------------------------------------

def analyze_predictive(task: dict, all_tasks: list[dict]) -> dict:
    """
    Analyze blast radius based on task anchors alone (no git required).
    Returns {task_summary, anchored_areas, overlapping_tasks}.
    """
    task_id = task.get("id", "")
    title = task.get("title", "")
    anchors = task.get("anchors", [])

    # Filter out HTTP/localhost anchors for file-based analysis
    file_anchors = [
        a for a in anchors
        if not a.startswith(("http://", "https://", "localhost"))
    ]

    overlapping = find_overlapping_tasks(file_anchors, all_tasks, exclude_task_id=task_id)

    return {
        "task_summary": f"{task_id}: {title}",
        "anchored_areas": file_anchors,
        "overlapping_tasks": overlapping,
    }


# ---------------------------------------------------------------------------
# Task 10: Evidence analysis
# ---------------------------------------------------------------------------

def analyze_evidence(
    task: dict,
    all_tasks: list[dict],
    project_root: Path,
    config: BlastRadiusConfig,
    base_branch: str = "main",
    depth_override: str | None = None,
) -> dict:
    """
    Full evidence-based blast radius analysis using git diff.

    Pipeline:
    1. get_changed_files
    2. compute_fan_out_scores
    3. has_export_changes + compute_blast_depth per file
    4. trace_dependency_graph
    5. Collect all affected paths (changed + dependents)
    6. find_overlapping_tasks against all affected
    7. Compute summary stats

    Returns {changed_files, dependency_graph, fan_out_scores, depth_used,
             overlapping_tasks, summary_stats, truncated}.
    """
    task_id = task.get("id", "")
    priority = task.get("priority", "P2")

    # Step 1: Get changed files
    branch = task.get("branch", "HEAD")
    changed_files = get_changed_files(branch, base_branch, project_root)

    # Step 2: Fan-out scores
    fan_out_scores = compute_fan_out_scores(changed_files, project_root, config)

    # Step 3: Export changes + depth per file
    depth_used: dict[str, int] = {}
    for f in changed_files:
        ext = Path(f).suffix
        export_changed = False
        if ext in SUPPORTED_EXTENSIONS:
            export_changed = has_export_changes(f, base_branch, project_root)
        fan_out = fan_out_scores.get(f, 0)
        depth = compute_blast_depth(
            file_rel=f,
            fan_out=fan_out,
            has_export_change=export_changed,
            priority=priority,
            config=config,
            depth_override=depth_override,
        )
        depth_used[f] = depth

    # Step 4: Trace dependency graph
    dependency_graph = trace_dependency_graph(
        changed_files, depth_used, project_root, config
    )

    # Step 5: Collect all affected paths
    all_affected: set[str] = set(changed_files)
    truncated = False
    for dependents in dependency_graph.values():
        all_affected.update(dependents)

    # Step 6: Find overlapping tasks
    overlapping_tasks = find_overlapping_tasks(
        list(all_affected), all_tasks, exclude_task_id=task_id
    )

    # Step 7: Summary stats
    summary_stats = {
        "total_changed": len(changed_files),
        "total_affected": len(all_affected),
        "total_dependents": len(all_affected) - len(changed_files),
        "overlapping_task_count": len(overlapping_tasks),
        "max_fan_out": max(fan_out_scores.values(), default=0),
    }

    return {
        "changed_files": changed_files,
        "dependency_graph": {k: v for k, v in dependency_graph.items()},
        "fan_out_scores": fan_out_scores,
        "depth_used": depth_used,
        "overlapping_tasks": overlapping_tasks,
        "summary_stats": summary_stats,
        "truncated": truncated,
    }
