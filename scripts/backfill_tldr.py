"""One-shot backfill: add tldr fields to legacy tasks/issues/lessons/ideas.

Idempotent — re-running only touches entities still missing a tldr. Skips
handovers (they already require tldr at write time).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from plugins.taskmaster import taskmaster_v3 as tm


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Parse '---\\nYAML\\n---\\nBODY' into (frontmatter, body)."""
    import yaml
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    fm = yaml.safe_load(parts[1]) or {}
    body = parts[2].lstrip("\n")
    return fm, body


def _join_frontmatter(fm: dict, body: str) -> str:
    import yaml
    fm_yaml = yaml.dump(fm, sort_keys=False, allow_unicode=True)
    return f"---\n{fm_yaml}---\n{body}"


def _backfill_dir(directory: Path) -> int:
    """Backfill all .md files under directory. Returns count of changes."""
    if not directory.exists():
        return 0
    changed = 0
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        fm, body = _split_frontmatter(text)
        new_fm, did_change = tm.backfill_tldr(fm, body)
        if did_change:
            path.write_text(_join_frontmatter(new_fm, body), encoding="utf-8")
            changed += 1
            print(f"  backfilled {path.name}: {new_fm['tldr']!r}")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="Project root containing .taskmaster/")
    args = parser.parse_args()

    base = Path(args.root) / ".taskmaster"
    if not base.exists():
        print(f"No .taskmaster/ at {args.root}", file=sys.stderr)
        return 2

    total = 0
    for subdir in ("tasks", "issues", "lessons", "ideas"):
        d = base / subdir
        print(f"Scanning {d}...")
        total += _backfill_dir(d)

    print(f"\nDone. {total} entities backfilled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
