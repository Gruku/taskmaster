"""One-shot backfill: add tldr fields to legacy tasks/issues/ideas.

Idempotent — re-running only touches entities still missing a tldr. Skips
handovers (they already require tldr at write time).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from taskmaster.taskmaster_v3 import (
    backfill_tldr,
    parse_frontmatter,
    render_frontmatter,
)


def _backfill_dir(directory: Path) -> int:
    """Backfill all .md files under directory. Returns count of changes."""
    if not directory.exists():
        return 0
    changed = 0
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)
        new_fm, did_change = backfill_tldr(fm, body)
        if did_change:
            path.write_text(render_frontmatter(new_fm, body), encoding="utf-8")
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
    for subdir in ("tasks", "issues", "ideas"):
        d = base / subdir
        print(f"Scanning {d}...")
        total += _backfill_dir(d)

    print(f"\nDone. {total} entities backfilled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
