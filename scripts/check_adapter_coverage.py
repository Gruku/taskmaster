#!/usr/bin/env python3
"""Verify skills/ wrappers and playbooks/ stay 1:1 and playbooks stay assistant-neutral.

Default mode validates converted skills only (those with a playbook).
--strict additionally requires every skill to be converted.
Exit 0 = pass, 1 = violations (one `ERROR: <path>: <msg>` line each).
"""
import argparse
import re
import sys
from pathlib import Path

BANNED = [
    "AskUserQuestion",
    "CLAUDE_PLUGIN_ROOT",
    "subagent_type",
    "model: opus",
    "model: sonnet",
    "model: haiku",
]
CC_ONLY = re.compile(r"<!-- cc-only:start -->.*?<!-- cc-only:end -->", re.DOTALL)
MAX_WRAPPER_BODY_LINES = 12


def strip_cc_only(text: str) -> str:
    return CC_ONLY.sub("", text)


def wrapper_body(text: str) -> str:
    m = re.match(r"^---\n.*?\n---\n", text, re.DOTALL)
    return text[m.end():] if m else text


def check(root: Path, strict: bool) -> list[str]:
    errors: list[str] = []
    skills = root / "skills"
    playbooks = root / "playbooks"

    converted = sorted(
        d for d in (playbooks.iterdir() if playbooks.is_dir() else [])
        if d.is_dir() and (d / "playbook.md").is_file()
    )

    for pb_dir in converted:
        name = pb_dir.name
        skill_md = skills / name / "SKILL.md"
        if not skill_md.is_file():
            errors.append(f"{pb_dir}: playbook '{name}' has no skills/{name}/SKILL.md wrapper (orphan)")
            continue
        text = skill_md.read_text(encoding="utf-8")
        pointer = f"../../playbooks/{name}/playbook.md"
        if pointer not in text:
            errors.append(f"{skill_md}: wrapper body missing pointer `{pointer}`")
        body_lines = [ln for ln in wrapper_body(text).splitlines() if ln.strip()]
        if len(body_lines) > MAX_WRAPPER_BODY_LINES:
            errors.append(
                f"{skill_md}: wrapper body has {len(body_lines)} non-empty lines "
                f"(max {MAX_WRAPPER_BODY_LINES}) — content belongs in the playbook")
        for md in sorted(pb_dir.rglob("*.md")):
            content = strip_cc_only(md.read_text(encoding="utf-8"))
            for token in BANNED:
                if token in content:
                    errors.append(f"{md}: banned assistant-specific token '{token}'")

    if strict and skills.is_dir():
        converted_names = {d.name for d in converted}
        for skill_dir in sorted(skills.iterdir()):
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file() \
                    and skill_dir.name not in converted_names:
                errors.append(
                    f"{skill_dir}: skill '{skill_dir.name}' unconverted — "
                    f"no playbooks/{skill_dir.name}/playbook.md")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--root", type=Path,
                    default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()
    errors = check(args.root, args.strict)
    for e in errors:
        print(f"ERROR: {e}")
    print(f"check_adapter_coverage: {'FAIL' if errors else 'OK'} "
          f"({len(errors)} error(s))")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
