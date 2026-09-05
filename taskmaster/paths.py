# User intent: keep the path-shaping rules the derived tables depend on — anchor and
# location normalization, prose path extraction, repo inference — in one small module
# the store can import, now that `index.py` and its separate database are gone.
from __future__ import annotations

import re

# Re-exported so every consumer of the derived link tables has one import for the
# path helpers and the link inverse map. The map itself stays defined beside the
# link validators in `taskmaster_v3`, which is what enforces it at the boundary.
from taskmaster.taskmaster_v3 import REVERSE_TYPE  # noqa: F401

_CODE_EXTS = (
    "py", "ts", "tsx", "js", "jsx", "cs", "csproj", "md", "yaml", "yml", "json", "toml",
    "html", "css", "scss", "sql", "sh", "ps1", "cpp", "h", "hpp", "c", "rs", "go",
    "java", "kt", "swift", "uasset", "ini", "cfg", "txt",
)
# Longest alternative first plus a trailing word guard, so `page.tsx` cannot be
# truncated to `page.ts`, `x.csproj` to `x.cs`, or `abc.jsonl` to `abc.json`.
_EXT_ALT = "|".join(sorted(_CODE_EXTS, key=lambda e: (-len(e), e)))
_PROSE_PATH_RE = re.compile(
    r"(?<![\w:/\\])((?:[A-Za-z0-9_.-]+/){1,}[A-Za-z0-9_.-]+\.(?:" + _EXT_ALT
    + r"))(?![A-Za-z0-9])(?::\d+)?"
)
# Transcript paths are never project sources. Dropped explicitly rather than by
# relying on `jsonl` being absent from the extension list.
_EXCLUDED_PATH_SUFFIXES = (".jsonl",)
_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S*")
_LINE_SUFFIX_RE = re.compile(r":\d+$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:$")


def as_list(value: object) -> list:
    """Coerce a field that should be a list. A bare string becomes one element.

    Hand-edited frontmatter routinely writes `location: api/src/x.py` instead of
    a YAML list; iterating that string would index it character by character.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def normalize_task_anchor(anchor: str, sub_repo: str | None) -> tuple[str, str]:
    """Normalize a task anchor to (project-root-relative path, 'exact'|'glob')."""
    p = str(anchor).replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    if sub_repo and not p.startswith(f"{sub_repo}/"):
        p = f"{sub_repo}/{p}"
    if p.endswith("/"):
        return p + "**", "glob"
    if "*" in p or "?" in p:
        return p, "glob"
    return p, "exact"


def normalize_location(loc: str) -> str:
    """Normalize a bug/issue `location` entry: drop `:LINE`, `\\`→`/`, leading `./`."""
    p = str(loc).replace("\\", "/")
    p = _LINE_SUFFIX_RE.sub("", p)
    while p.startswith("./"):
        p = p[2:]
    return p


def extract_prose_paths(text: str) -> list[str]:
    """Repo-relative code paths mentioned in prose, unique, first-appearance order.

    URLs are stripped before matching so a host+path (`x.y/a/b.py`) can never be
    mistaken for a repo path, and absolute Windows paths are dropped.
    """
    if not text:
        return []
    stripped = _URL_RE.sub(" ", text)
    out: list[str] = []
    seen: set[str] = set()
    for m in _PROSE_PATH_RE.finditer(stripped):
        p = m.group(1)
        if "://" in p or p.endswith(_EXCLUDED_PATH_SUFFIXES):
            continue
        first = p.split("/", 1)[0]
        if _WINDOWS_DRIVE_RE.match(first) or first == "Users":
            continue
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def infer_repo(paths: list[str], repos: list[tuple[str, str]]) -> str | None:
    """Name of the repo whose (longest) path prefix contains one of `paths`.

    No caller remains: the store's `entities` table has no `repo` column, so
    nothing infers one any more. Kept because R9 names it -- a repo dimension is
    a plausible addition to the derived tables and this is the rule it would use.
    """
    best_name: str | None = None
    best_len = -1
    for name, prefix in repos:
        if not prefix:
            continue
        if any(p.startswith(prefix + "/") for p in paths) and len(prefix) > best_len:
            best_name, best_len = name, len(prefix)
    return best_name
