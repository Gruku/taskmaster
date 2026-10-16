# User intent: make the prompt-modernization style stick. Prompts shouldn't regress to caps-lock emphasis,
# alarm emoji, or documentation of features that don't exist, because that makes the model rigid and wrong.
"""Style lint over taskmaster's prompt surfaces (playbooks, skill descriptions, session hook, adapters)."""
import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

CAPS_WORDS = re.compile(
    r"\b(?:MUST|NEVER|ALWAYS|CRITICAL|MANDATORY|REQUIRED|IMPORTANT|EXTREMELY|ONLY)\b|\bNOT\b"
)
ALARM = ("⛔",)  # no-entry emoji
# Slash names must match as whole tokens so `/init-taskmaster` and `playbooks/init-taskmaster` don't trip `/init`.
DEAD_NAMES = (
    re.compile(r"_get_session_bundle"),
    re.compile(r"Resolves: DEC"),
    re.compile(r"(?<![\w/.-])/decide(?![\w-])"),
    re.compile(r"(?<![\w/.-])/advance-phase(?![\w-])"),
    re.compile(r"(?<![\w/.-])/init(?![\w-])"),
    re.compile(r"execute it exactly"),
)

# (path suffix, exact stripped line) pairs for quoted literals that legitimately hold a caps word, such as literal
# user text. Keep it short; instructions never belong here. Literal TODO/FIXME/HACK/XXX and severity names are not
# in CAPS_WORDS, so they need no entry.
ALLOWED_LINES: set[tuple[str, str]] = set()


def _prompt_files() -> list[Path]:
    files = sorted((ROOT / "playbooks").rglob("*.md"))
    files += sorted((ROOT / "adapters").rglob("*.md"))
    files.append(ROOT / "hooks" / "session-start.sh")
    files.append(ROOT / "references" / "task-lifecycle.md")
    return files


def _hook_strings() -> list[tuple[str, int, str]]:
    """String literals (not docstrings) in the hook scripts: these are what the model reads on a block or a nudge."""
    out = []
    for f in sorted((ROOT / "hooks").glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        docs = {
            id(n.body[0].value)
            for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and ast.get_docstring(n)
        }
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
                out.append((str(f.relative_to(ROOT)), n.lineno, n.value))
    return out


def _description(skill_md: Path) -> str:
    text = skill_md.read_text(encoding="utf-8")
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    if not m:
        return ""
    import yaml

    return str((yaml.safe_load(m.group(1)) or {}).get("description", ""))


def _scan(rel: str, text: str) -> list[str]:
    problems = []
    for n, line in enumerate(text.splitlines(), 1):
        norm = rel.replace("\\", "/")
        if any(norm.endswith(suffix) and line.strip() == allowed for suffix, allowed in ALLOWED_LINES):
            continue
        for m in CAPS_WORDS.finditer(line):
            problems.append(f"{rel}:{n}: caps emphasis '{m.group(0)}'")
        for a in ALARM:
            if a in line:
                problems.append(f"{rel}:{n}: alarm emoji")
        for d in DEAD_NAMES:
            if d.search(line):
                problems.append(f"{rel}:{n}: dead name '{d.pattern}'")
    return problems


def test_prompt_files_have_no_caps_emphasis_or_dead_names():
    problems = []
    for f in _prompt_files():
        problems += _scan(str(f.relative_to(ROOT)), f.read_text(encoding="utf-8"))
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("skill_md", sorted((ROOT / "skills").glob("*/SKILL.md")), ids=lambda p: p.parent.name)
def test_skill_description_has_no_caps_emphasis(skill_md):
    problems = _scan(str(skill_md.relative_to(ROOT)), _description(skill_md))
    assert not problems, "\n".join(problems)


def test_hook_messages_have_no_caps_emphasis():
    problems = []
    for rel, lineno, text in _hook_strings():
        problems += [p.replace(":1:", f":{lineno}:", 1) for p in _scan(rel, text)]
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("skill_md", sorted((ROOT / "skills").glob("*/SKILL.md")), ids=lambda p: p.parent.name)
def test_skill_body_has_no_caps_emphasis(skill_md):
    text = skill_md.read_text(encoding="utf-8")
    body = re.sub(r"\A---\r?\n.*?\r?\n---\r?\n", "", text, count=1, flags=re.DOTALL)
    problems = _scan(str(skill_md.relative_to(ROOT)), body)
    assert not problems, "\n".join(problems)


def test_lint_catches_what_it_claims():
    sample = "You MUST do this. Do NOT skip. ⛔ See /decide and _get_session_bundle()."
    found = _scan("x.md", sample)
    assert len(found) >= 5
