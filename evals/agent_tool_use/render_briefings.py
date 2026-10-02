# User intent: turn the scenario list into one ready-to-hand-out briefing file per test agent, so
# a batch is launched with "your briefing is in <file>" instead of filling the template by hand.
"""Render one briefing per scenario for a run-id prefix.

    uv run evals/agent_tool_use/render_briefings.py --prefix b2 [--only s15-s47] [--out DIR] [--home DIR]

Writes `<out>/<prefix>-sNN.md` (run id `<prefix>-sNN`) from prompt_template.xml with the header
comment stripped and {{CLI}}, {{RUN_ID}}, {{REQUEST}} filled from `<home>/scenarios.json`.
The default output directory is a sibling of the eval home, not inside it: the home holds the
ground truth, and a briefing must not lead an agent there.
--only takes scenario numbers or ranges, comma-separated: `s15-s47`, `s03,s18,s20-s22`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evalpaths  # noqa: E402


def wanted_numbers(spec: str | None) -> set[int] | None:
    if not spec:
        return None
    numbers: set[int] = set()
    for part in spec.split(","):
        match = re.fullmatch(r"\s*s?(\d+)(?:\s*-\s*s?(\d+))?\s*", part)
        if not match:
            raise SystemExit(f"cannot read --only part {part!r}")
        low, high = int(match.group(1)), int(match.group(2) or match.group(1))
        numbers.update(range(low, high + 1))
    return numbers


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prefix", required=True, help="run-id prefix, e.g. b2 -> run ids b2-s15, b2-s16, ...")
    parser.add_argument("--only", help="scenario numbers/ranges, e.g. s15-s47 or s03,s18")
    parser.add_argument("--out", help="output directory (default: <eval home>-briefings/<prefix>)")
    parser.add_argument("--home")
    ns = parser.parse_args()
    evalpaths.check_run_id(ns.prefix)
    home = evalpaths.home(ns.home)
    rendered = json.loads((home / "scenarios.json").read_text(encoding="utf-8"))
    if rendered.get("seeded_on") != date.today().isoformat():
        print(f"WARNING: backlog was seeded on {rendered.get('seeded_on')}; reseed with --force before running agents.")
    template = (evalpaths.EVAL_DIR / "prompt_template.xml").read_text(encoding="utf-8")
    template = re.sub(r"\A\s*<!--.*?-->\s*", "", template, flags=re.S)
    out = Path(ns.out).resolve() if ns.out else home.with_name(home.name + "-briefings") / ns.prefix
    if out == home or home in out.parents or out == evalpaths.REPO_ROOT or evalpaths.REPO_ROOT in out.parents:
        raise SystemExit("briefings must be written outside the eval home and outside the repo")
    out.mkdir(parents=True, exist_ok=True)
    only = wanted_numbers(ns.only)
    cli = str(evalpaths.EVAL_DIR / "tmcli.py")
    count = 0
    for scenario in rendered["scenarios"]:
        short = scenario["id"].split("-")[0]
        if only is not None and int(short[1:]) not in only:
            continue
        run = f"{ns.prefix}-{short}"
        text = template.replace("{{CLI}}", cli).replace("{{RUN_ID}}", run).replace("{{REQUEST}}", scenario["request"])
        path = out / f"{run}.md"
        path.write_text(text, encoding="utf-8")
        print(f"{run}\t{scenario['id']}\t{path}")
        count += 1
    print(f"{count} briefing(s) in {out}")
    return 0 if count else 1


if __name__ == "__main__":
    sys.exit(main())
