"""User intent: make a Taskmaster release bump one command, so every version string the
package ships moves together and a mismatch is caught before the claude-tools release.

    python scripts/bump_version.py 6.1.0     # rewrite every in-repo version string
    python scripts/bump_version.py --check   # report misaligned strings; exit 1 if any

Write the CHANGELOG's `## <version>` entry yourself: `--check` fails until it exists.
Outside this repository the same version lives in claude-tools'
`.claude-plugin/marketplace.json` and the generated `codex-plugins/taskmaster/` snapshot;
see docs/runbooks/release-packaging.md. Standard library only.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?")

# Each pattern's group 2 is the version; groups 1 and 3 are kept byte for byte.
PATTERNS = {
    ".claude-plugin/plugin.json": r'(\n\s*"version":\s*")([^"]+)(")',
    ".codex-plugin/plugin.json": r'(\n\s*"version":\s*")([^"]+)(")',
    "pyproject.toml": r'(\[project\][^\[]*?\nversion = ")([^"]+)(")',
    "uv.lock": r'(\[\[package\]\]\r?\nname = "taskmaster"\r?\nversion = ")([^"]+)(")',
    "README.md": r"(img\.shields\.io/badge/version-)([^-]+)(-)",
}
VERSIONED_FILES = tuple(PATTERNS)


def _read(root: Path, rel: str) -> str:
    with open(root / rel, encoding="utf-8", newline="") as stream:
        return stream.read()


def versions(root: Path = ROOT) -> dict[str, str | None]:
    """The version each file carries, or None where its pattern does not match once."""
    found = {}
    for rel, pattern in PATTERNS.items():
        matches = re.findall(pattern, _read(root, rel))
        found[rel] = matches[0][1] if len(matches) == 1 else None
    return found


def check(root: Path = ROOT) -> list[str]:
    """Problems, empty when every version string agrees and the CHANGELOG has its entry."""
    found = versions(root)
    problems = [f"{rel}: version not found exactly once" for rel, v in found.items() if v is None]
    distinct = sorted({v for v in found.values() if v is not None})
    if len(distinct) > 1:
        problems.append("versions disagree: " + ", ".join(f"{rel}={v}" for rel, v in found.items()))
    if len(distinct) == 1 and not re.search(rf"(?m)^##\s+{re.escape(distinct[0])}\b", _read(root, "CHANGELOG.md")):
        problems.append(f"CHANGELOG.md has no '## {distinct[0]}' heading")
    return problems


def bump(root: Path, new: str) -> list[str]:
    """Rewrite every version string to `new`; returns the files written."""
    if not SEMVER.fullmatch(new):
        raise ValueError(f"not a semantic version: {new!r}")
    changed = []
    for rel, pattern in PATTERNS.items():
        text = _read(root, rel)
        updated, count = re.subn(pattern, lambda m: m.group(1) + new + m.group(3), text)
        if count != 1:
            raise ValueError(f"{rel}: expected one version string, found {count}")
        if updated != text:
            with open(root / rel, "w", encoding="utf-8", newline="") as stream:
                stream.write(updated)
            changed.append(rel)
    return changed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version", nargs="?")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        for rel in bump(ROOT, args.version):
            print(f"updated {rel}")
    elif not args.check:
        parser.error("give a version or --check")
    problems = check(ROOT)
    for problem in problems:
        print(f"problem: {problem}")
    if not problems:
        print(f"versions aligned: {versions(ROOT)['.claude-plugin/plugin.json']}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
