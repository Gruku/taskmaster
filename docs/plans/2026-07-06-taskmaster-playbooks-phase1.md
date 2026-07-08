# Taskmaster Playbook Extraction (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Repo rule:** before editing any file under `plugins/taskmaster/skills/`, invoke `plugin-dev:skill-development` (project CLAUDE.md requirement — frontmatter rules live there).

**Goal:** Move all taskmaster workflow knowledge out of Claude-Code-specific SKILL.md bodies into assistant-neutral `playbooks/`, leaving skills as thin trigger wrappers — in-place in claude-tools, behavior-neutral, fully lint-tested.

**Architecture:** Content moves once into `plugins/taskmaster/playbooks/<name>/playbook.md` (+ its `references/` and `templates/`); each `skills/<name>/SKILL.md` keeps its verbatim `description` frontmatter and gets a ~5-line pointer body. A new `check_adapter_coverage.py` enforces 1:1 mapping and assistant-neutrality. Existing per-skill lint tests are re-pointed at playbooks.

**Tech Stack:** Python 3 + pytest (existing suite at `plugins/taskmaster/tests/`), markdown, git.

**Spec:** `docs/superpowers/specs/2026-07-06-taskmaster-multi-assistant-design.md` (Phase 1 of 5).

## Global Constraints

- `description` frontmatter of every skill is **byte-for-byte unchanged** (it's the trigger surface). Verify with `git diff` after every conversion.
- Playbook files must contain none of the banned CC tokens: `AskUserQuestion`, `CLAUDE_PLUGIN_ROOT`, `subagent_type`, `model: opus|sonnet|haiku` — except inside `<!-- cc-only:start -->` … `<!-- cc-only:end -->` sections.
- Neutral phrasings (from the spec): "ask the user (use your structured-question tool if available)" replaces AskUserQuestion; "delegate to a sub-agent if your tool supports it; otherwise do it inline" replaces Agent-tool specifics. `backlog_*` MCP tool names stay as-is (shared vocabulary).
- Wrapper SKILL.md body: ≤ 12 non-empty lines, must contain the literal relative path `../../playbooks/<name>/playbook.md`.
- Body token budgets in `skill_budget_helper.py` now cover **wrapper + playbook combined**; budget numbers unchanged.
- Use `git mv` for all moves (history preservation matters for the later filter-repo extraction).
- Commit after each task with explicit pathspecs (no `git add -A`); never push.
- Full test suite (`python -m pytest plugins/taskmaster/tests/ -q`) green at the end of every task.
- Version bump to **3.22.0** (minor — additive `playbooks/` surface) happens once, in Task 7, in all three places (plugin.json, marketplace.json, CHANGELOG.md).

## File Structure (end state)

```
plugins/taskmaster/
├── playbooks/
│   ├── CONVENTIONS.md               ← authoring rules for playbooks (new)
│   └── <name>/                      ← one per skill (17 total)
│       ├── playbook.md              ← former SKILL.md body, neutralized
│       ├── references/              ← moved from skills/<name>/references/
│       └── templates/               ← moved from skills/<name>/templates/ (where present)
├── skills/<name>/SKILL.md           ← frontmatter (verbatim) + pointer body
├── scripts/check_adapter_coverage.py  (new)
└── tests/
    ├── test_adapter_coverage.py       (new)
    ├── skill_budget_helper.py         (modified: combined budget)
    └── test_<name>_skill_lint.py      (modified: content asserts → playbook)
```

---

### Task 1: Coverage checker + playbook conventions (TDD)

**Files:**
- Create: `plugins/taskmaster/playbooks/CONVENTIONS.md`
- Create: `plugins/taskmaster/scripts/check_adapter_coverage.py`
- Test: `plugins/taskmaster/tests/test_adapter_coverage.py`

**Interfaces:**
- Produces: `check_adapter_coverage.py` CLI — `python plugins/taskmaster/scripts/check_adapter_coverage.py [--strict] [--root PATH]`. Exit 0 = pass, 1 = violations (printed one per line as `ERROR: <path>: <message>`). Default mode validates only *converted* skills (those with `playbooks/<name>/playbook.md`); `--strict` additionally requires every `skills/<name>/` to be converted. `--root` overrides the plugin root (for tests).
- Produces: `CONVENTIONS.md` — the normative transformation rules Tasks 2–6 apply.

- [ ] **Step 1: Write CONVENTIONS.md**

```markdown
# Playbook Authoring Conventions

Playbooks are assistant-neutral workflow documents. Any coding agent that can
read files and call the `backlog_*` MCP tools must be able to follow one.

## Layout

- `playbooks/<name>/playbook.md` — the workflow. One per skill wrapper.
- `playbooks/<name>/references/`, `templates/` — supporting files, referenced
  from the playbook by paths relative to the playbook's own directory
  (`references/foo.md`, never absolute, never `${CLAUDE_PLUGIN_ROOT}`).

## Neutrality rules

1. **Ask, don't name the tool.** Write "ask the user (use your
   structured-question tool if available)" — never `AskUserQuestion`.
2. **Delegation is optional.** Write "delegate to a sub-agent if your tool
   supports it; otherwise do it inline" — never Agent-tool call syntax,
   `subagent_type`, or model names (opus/sonnet/haiku).
3. **MCP names are the shared vocabulary.** Refer to `backlog_*` tools
   directly; every supported assistant reaches them via MCP.
4. **Cross-playbook references** point at the playbook path first, with the
   native invocation as a hint: "follow the bug playbook
   (`../bug/playbook.md`; on Claude Code/ZCode: `taskmaster:bug`)".
5. **Assistant-specific content** (e.g. Codex-subagent dispatch snippets)
   is allowed only between `<!-- cc-only:start -->` and `<!-- cc-only:end -->`
   markers, with a one-line neutral fallback outside the markers.
6. **No `${CLAUDE_PLUGIN_ROOT}`** anywhere in playbooks. Wrappers (SKILL.md)
   may be CC-flavored; playbooks may not.

## Wrapper contract (skills/<name>/SKILL.md)

- Frontmatter: `name` + `description` **verbatim from before the conversion**
  — the description is the trigger surface and must not change.
- Body: ≤ 12 non-empty lines containing the literal path
  `../../playbooks/<name>/playbook.md` (relative to the skill's base dir).

Enforced by `scripts/check_adapter_coverage.py` (run with `--strict` in CI
rituals; default mode during incremental conversion).
```

- [ ] **Step 2: Write the failing tests**

`plugins/taskmaster/tests/test_adapter_coverage.py`:

```python
"""Tests for scripts/check_adapter_coverage.py."""
from pathlib import Path
import subprocess
import sys
import textwrap

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_adapter_coverage.py"

WRAPPER = textwrap.dedent("""\
    ---
    name: demo
    description: "Demo skill."
    ---

    Follow the playbook at `../../playbooks/demo/playbook.md`. Read it in
    full and execute it exactly; `references/` paths inside it resolve
    relative to the playbook's directory.
    """)


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        capture_output=True, text=True,
    )


def make_pair(root: Path, name: str = "demo", playbook_text: str = "# Demo\n\nAsk the user.\n"):
    (root / "skills" / name).mkdir(parents=True)
    (root / "skills" / name / "SKILL.md").write_text(
        WRAPPER.replace("demo", name), encoding="utf-8")
    (root / "playbooks" / name).mkdir(parents=True)
    (root / "playbooks" / name / "playbook.md").write_text(playbook_text, encoding="utf-8")


def test_clean_pair_passes(tmp_path):
    make_pair(tmp_path)
    r = run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_playbook_without_wrapper_fails(tmp_path):
    make_pair(tmp_path)
    (tmp_path / "playbooks" / "orphan").mkdir()
    (tmp_path / "playbooks" / "orphan" / "playbook.md").write_text("# X\n", encoding="utf-8")
    r = run(tmp_path)
    assert r.returncode == 1
    assert "orphan" in r.stdout


def test_banned_token_in_playbook_fails(tmp_path):
    make_pair(tmp_path, playbook_text="# Demo\n\nCall AskUserQuestion here.\n")
    r = run(tmp_path)
    assert r.returncode == 1
    assert "AskUserQuestion" in r.stdout


def test_banned_token_inside_cc_only_passes(tmp_path):
    make_pair(tmp_path, playbook_text=(
        "# Demo\n\nAsk the user.\n\n"
        "<!-- cc-only:start -->\nAskUserQuestion({...})\n<!-- cc-only:end -->\n"))
    r = run(tmp_path)
    assert r.returncode == 0, r.stdout


def test_banned_token_in_reference_file_fails(tmp_path):
    make_pair(tmp_path)
    refs = tmp_path / "playbooks" / "demo" / "references"
    refs.mkdir()
    (refs / "flow.md").write_text("Use subagent_type here.\n", encoding="utf-8")
    r = run(tmp_path)
    assert r.returncode == 1
    assert "subagent_type" in r.stdout


def test_wrapper_missing_pointer_fails(tmp_path):
    make_pair(tmp_path)
    (tmp_path / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: \"Demo skill.\"\n---\n\nNo pointer here.\n",
        encoding="utf-8")
    r = run(tmp_path)
    assert r.returncode == 1


def test_wrapper_body_too_long_fails(tmp_path):
    make_pair(tmp_path)
    long_body = WRAPPER + "\n".join(f"extra line {i}" for i in range(15)) + "\n"
    (tmp_path / "skills" / "demo" / "SKILL.md").write_text(long_body, encoding="utf-8")
    r = run(tmp_path)
    assert r.returncode == 1


def test_strict_requires_all_skills_converted(tmp_path):
    make_pair(tmp_path)
    (tmp_path / "skills" / "unconverted").mkdir()
    (tmp_path / "skills" / "unconverted" / "SKILL.md").write_text(
        "---\nname: unconverted\ndescription: \"Old style.\"\n---\n\nFull body here.\n",
        encoding="utf-8")
    assert run(tmp_path).returncode == 0          # default mode: OK
    r = run(tmp_path, "--strict")
    assert r.returncode == 1
    assert "unconverted" in r.stdout
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest plugins/taskmaster/tests/test_adapter_coverage.py -q`
Expected: errors/failures for every test (script doesn't exist yet).

- [ ] **Step 4: Implement the checker**

`plugins/taskmaster/scripts/check_adapter_coverage.py`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest plugins/taskmaster/tests/test_adapter_coverage.py -q`
Expected: all 8 PASS.

- [ ] **Step 6: Run against the real plugin (no playbooks yet — must pass trivially)**

Run: `python plugins/taskmaster/scripts/check_adapter_coverage.py`
Expected: `check_adapter_coverage: OK (0 error(s))`

- [ ] **Step 7: Commit**

```bash
git add plugins/taskmaster/playbooks/CONVENTIONS.md plugins/taskmaster/scripts/check_adapter_coverage.py plugins/taskmaster/tests/test_adapter_coverage.py
git commit -m "feat(taskmaster): playbook conventions + adapter coverage checker (multi-assistant phase 1)"
```

---

### Task 2: Pilot conversion — end-session (+ budget helper update)

**Files:**
- Create: `plugins/taskmaster/playbooks/end-session/playbook.md`
- Move: `plugins/taskmaster/skills/end-session/references/` → `plugins/taskmaster/playbooks/end-session/references/` (3 files, `git mv`)
- Modify: `plugins/taskmaster/skills/end-session/SKILL.md` (body → pointer; frontmatter untouched)
- Modify: `plugins/taskmaster/tests/skill_budget_helper.py`
- Modify: `plugins/taskmaster/tests/test_end_session_skill_lint.py`

**Interfaces:**
- Consumes: Task 1's checker + CONVENTIONS.md.
- Produces: `playbook_md_path(skill_name) -> Path` and combined-budget `body_token_count()` in `skill_budget_helper.py` — all later lint-test edits use these; the wrapper text pattern all later tasks copy.

- [ ] **Step 1: Update skill_budget_helper.py**

Add after `SKILLS_ROOT`:

```python
PLAYBOOKS_ROOT = Path(__file__).resolve().parents[1] / "playbooks"


def playbook_md_path(skill_name: str) -> Path:
    return PLAYBOOKS_ROOT / skill_name / "playbook.md"
```

Replace `body_token_count` with:

```python
def body_token_count(skill_name: str) -> int:
    """Approximate invocation-time token cost: wrapper SKILL.md + playbook.md."""
    text = skill_md_path(skill_name).read_text(encoding="utf-8")
    pb = playbook_md_path(skill_name)
    if pb.exists():
        text += pb.read_text(encoding="utf-8")
    return len(text) // CHARS_PER_TOKEN
```

- [ ] **Step 2: Update the lint test to expect the converted layout (failing first)**

Rewrite `plugins/taskmaster/tests/test_end_session_skill_lint.py`:

```python
"""Lint checks for the taskmaster:end-session skill (wrapper + playbook)."""
from pathlib import Path
import re
import yaml
from skill_budget_helper import body_token_count, description_word_count

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "end-session"
PLAYBOOK_DIR = Path(__file__).resolve().parents[1] / "playbooks" / "end-session"


def _read_frontmatter() -> dict:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    return yaml.safe_load(m.group(1)) or {} if m else {}


def test_skill_md_exists():
    assert (SKILL_DIR / "SKILL.md").exists()


def test_playbook_exists():
    assert (PLAYBOOK_DIR / "playbook.md").exists()


def test_wrapper_points_at_playbook():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "../../playbooks/end-session/playbook.md" in text


def test_frontmatter_required_fields():
    fm = _read_frontmatter()
    assert fm.get("name") == "end-session"
    assert "description" in fm


def test_body_within_budget():
    actual = body_token_count("end-session")
    assert actual <= 1_500, f"wrapper+playbook is {actual} tokens (budget: 1500)"


def test_description_within_word_budget():
    count = description_word_count("end-session")
    assert count <= 60, f"description is {count} words (budget: 60)"


def test_description_contains_key_trigger_phrases():
    fm = _read_frontmatter()
    desc = fm["description"].lower()
    must_have = ["end session", "wrap up", "mark this task done", "save progress"]
    missing = [p for p in must_have if p not in desc]
    assert not missing, f"description missing: {missing}"


def test_references_exist():
    for ref in ("v3-pre-steps.md", "summary-modes.md"):
        assert (PLAYBOOK_DIR / "references" / ref).exists(), f"missing references/{ref}"


def test_references_not_stubs():
    freshly_created = {"v3-pre-steps.md", "summary-modes.md"}
    for ref in (PLAYBOOK_DIR / "references").iterdir():
        if ref.name not in freshly_created:
            continue
        non_blank = [ln for ln in ref.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(non_blank) > 20, f"reference stub: {ref}"


def test_playbook_links_resolve():
    text = (PLAYBOOK_DIR / "playbook.md").read_text(encoding="utf-8")
    refs = re.findall(r"`(references/[A-Za-z0-9_-]+\.md)`", text)
    assert refs, "playbook.md must reference at least one references/ file"
    missing = [r for r in refs if not (PLAYBOOK_DIR / r).exists()]
    assert not missing, f"unresolved: {missing}"


def test_playbook_contains_canonical_sentence():
    text = (PLAYBOOK_DIR / "playbook.md").read_text(encoding="utf-8")
    assert "ONLY" in text and "backlog_complete_task" in text, (
        "Missing canonical 'ONLY correct way' sentence"
    )
```

- [ ] **Step 3: Run to verify the new expectations fail**

Run: `python -m pytest plugins/taskmaster/tests/test_end_session_skill_lint.py -q`
Expected: FAIL on `test_playbook_exists`, `test_wrapper_points_at_playbook`, `test_references_exist` (playbook dir doesn't exist yet).

- [ ] **Step 4: Create the playbook and move references**

```bash
mkdir -p plugins/taskmaster/playbooks/end-session
git mv plugins/taskmaster/skills/end-session/references plugins/taskmaster/playbooks/end-session/references
```

Create `plugins/taskmaster/playbooks/end-session/playbook.md` with the **entire current body** of `skills/end-session/SKILL.md` (everything below the frontmatter, i.e. from `# End Session` through the `## Additional Resources` list, verbatim) with exactly these neutralization edits:

- In step **v3-pre-1b**, "ask to carry forward / resolve now / drop" is already neutral — keep.
- No `AskUserQuestion` occurrences exist in this body — no other body edits.
- In `references/v3-pre-steps.md`: replace the phrase ``ask via `AskUserQuestion`:`` with `ask the user (use your structured-question tool if available):` — it appears once (decision sweep section).
- Scan the other two moved references (`summary-modes.md`, `edge-cases.md`) for banned tokens: `grep -n "AskUserQuestion\|subagent_type\|CLAUDE_PLUGIN_ROOT" plugins/taskmaster/playbooks/end-session/references/*.md` — fix any hit with the same neutral phrasing.

- [ ] **Step 5: Replace the SKILL.md body with the wrapper**

`plugins/taskmaster/skills/end-session/SKILL.md` becomes exactly (frontmatter lines 1–4 byte-identical to before):

```markdown
---
name: end-session
description: "Close out a work session. Invoke when the user says 'end session', 'I'm done for today', 'let's wrap up', 'mark this task done', or 'save progress'. This is the ONLY correct way to mark tasks done or in-review with a session record."
---

# End Session

Follow the playbook at `../../playbooks/end-session/playbook.md` (relative to
this skill's base directory). Read it in full and execute it exactly.
`references/` paths inside the playbook resolve relative to the playbook's
own directory.
```

- [ ] **Step 6: Verify frontmatter unchanged and tests pass**

Run: `git diff plugins/taskmaster/skills/end-session/SKILL.md | head -20` — confirm no `-description:` line in the diff.
Run: `python -m pytest plugins/taskmaster/tests/test_end_session_skill_lint.py plugins/taskmaster/tests/test_adapter_coverage.py plugins/taskmaster/tests/test_skill_body_budgets.py plugins/taskmaster/tests/test_skill_description_budgets.py plugins/taskmaster/tests/test_skill_catalog_smoke.py -q`
Expected: all PASS.
Run: `python plugins/taskmaster/scripts/check_adapter_coverage.py`
Expected: `OK (0 error(s))`.

- [ ] **Step 7: Live smoke test (manual checkpoint)**

Ask the user to run `/taskmaster:end-session` (or invoke the skill) in a scratch session and confirm it reads the playbook and behaves identically. **Pause here for user confirmation before batch-converting.**

- [ ] **Step 8: Commit**

```bash
git add plugins/taskmaster/playbooks/end-session plugins/taskmaster/skills/end-session/SKILL.md plugins/taskmaster/tests/skill_budget_helper.py plugins/taskmaster/tests/test_end_session_skill_lint.py
git commit -m "refactor(taskmaster): end-session -> playbook + thin wrapper (pilot)"
```

---

### Task 3: Convert session-flow skills — taskmaster, start-session, pick-task, handover

**Files (per skill `<name>` in: `taskmaster`, `start-session`, `pick-task`, `handover`):**
- Create: `plugins/taskmaster/playbooks/<name>/playbook.md`
- Move (`git mv`, where the dir exists): `skills/<name>/references/` → `playbooks/<name>/references/`; `skills/<name>/templates/` → `playbooks/<name>/templates/`
- Modify: `skills/<name>/SKILL.md` → wrapper (same pattern as Task 2 Step 5, with `<name>` substituted; frontmatter byte-identical)
- Modify: `tests/test_taskmaster_skill_lint.py`, `tests/test_start_session_skill_lint.py`, `tests/test_pick_task_skill_lint.py`, `tests/test_handover_skill_lint.py` — apply the same re-pointing as Task 2 Step 2: add `PLAYBOOK_DIR`, add `test_playbook_exists` + `test_wrapper_points_at_playbook`, and change every content/reference assertion that reads `SKILL_DIR / "SKILL.md"` body text or `SKILL_DIR / "references"` to read `PLAYBOOK_DIR / "playbook.md"` / `PLAYBOOK_DIR / "references"`. Frontmatter/description tests keep reading SKILL.md.

**Interfaces:**
- Consumes: wrapper text pattern (Task 2 Step 5), `playbook_md_path` (Task 2 Step 1), CONVENTIONS.md rules (Task 1).

**Known CC-isms to neutralize (from repo grep; re-grep to confirm):**
- `handover/SKILL.md` body: ``Ask with `AskUserQuestion` if unsure.`` → `Ask the user if unsure (use your structured-question tool if available).`
- `taskmaster` (router) body: routes by naming `taskmaster:*` skills — apply CONVENTIONS.md rule 4 (playbook path first, native invocation as hint).
- `start-session`/`pick-task`: no banned tokens in bodies; check their references files with the grep from Task 2 Step 4.

- [ ] **Step 1:** For each of the 4 skills, update its lint test first (Task 2 Step 2 pattern), run it, verify it FAILS on the playbook-existence assertions.
- [ ] **Step 2:** For each skill: `mkdir -p playbooks/<name>`, `git mv` references/templates, create `playbook.md` = old body verbatim + the neutralization edits listed above, replace SKILL.md body with the wrapper.
- [ ] **Step 3:** Grep the four playbook dirs for banned tokens (`grep -rn "AskUserQuestion\|subagent_type\|CLAUDE_PLUGIN_ROOT\|model: opus\|model: sonnet" plugins/taskmaster/playbooks/{taskmaster,start-session,pick-task,handover}/`) — fix hits per CONVENTIONS.md.
- [ ] **Step 4:** Run: `python -m pytest plugins/taskmaster/tests/ -q -k "skill or adapter or catalog or budget"` — all PASS. Run the coverage checker — OK. `git diff` each SKILL.md — description lines unchanged.
- [ ] **Step 5: Commit**

```bash
git add plugins/taskmaster/playbooks plugins/taskmaster/skills plugins/taskmaster/tests
git commit -m "refactor(taskmaster): session-flow skills -> playbooks (taskmaster, start-session, pick-task, handover)"
```

---

### Task 4: Convert review skills — review-gate, spec-review, plan-review

Same mechanics as Task 3, for `review-gate`, `spec-review`, `plan-review`. (`plan-review` has no references dir and no dedicated lint file beyond the shared budget/catalog tests — verify with `ls plugins/taskmaster/tests/ | grep plan_review`; if absent, only the shared tests apply.)

**Known CC-isms (these two files are the cc-only showcase):**
- `review-gate/references/codex-integration.md` and `spec-review/references/codex-integration.md`: contain `subagent_type: "codex:codex-rescue"` dispatch snippets. Wrap each snippet in `<!-- cc-only:start -->` … `<!-- cc-only:end -->` and add this neutral fallback line immediately before the marker: `If your tool cannot dispatch sub-agents, run the review inline against the same checklist.`
- `review-gate/references/gate-details.md`: "If the `superpowers:code-reviewer` subagent is available, dispatch it … If not available, perform an inline review" — already has the fallback; rephrase the first clause to `Delegate the review to a sub-agent if your tool supports it (on Claude Code: the superpowers code-reviewer)` and wrap only the CC-specific name if the checker still flags `subagent`. Note: the banned-token list contains `subagent_type`, not the word `subagent` — plain-English "sub-agent" phrasing is fine.

- [ ] **Step 1:** Update lint tests first (`test_review_gate_skill_lint.py`, `test_spec_review_skill_lint.py`), verify FAIL.
- [ ] **Step 2:** Convert all three skills (mkdir, git mv, playbook.md, wrapper).
- [ ] **Step 3:** Apply the cc-only wrapping above; grep for banned tokens; fix.
- [ ] **Step 4:** Run: `python -m pytest plugins/taskmaster/tests/ -q -k "skill or adapter or catalog or budget"` — PASS; checker OK; `git diff` descriptions unchanged.
- [ ] **Step 5: Commit**

```bash
git add plugins/taskmaster/playbooks plugins/taskmaster/skills plugins/taskmaster/tests
git commit -m "refactor(taskmaster): review skills -> playbooks (review-gate, spec-review, plan-review)"
```

---

### Task 5: Convert entity skills — bug, issue, lesson, decision, add-idea, check-todos

Same mechanics, for `bug`, `issue`, `lesson`, `decision`, `add-idea`, `check-todos`. Four of these have `templates/` dirs — `git mv` those alongside `references/`.

**Known CC-isms:**
- `decision/references/auto-resolution.md`: ``it asks (via `AskUserQuestion`):`` → `it asks the user (use your structured-question tool if available):`
- `lesson/references/reinforce-flows.md`: one real `AskUserQuestion({` call snippet → rewrite as neutral prose ("ask the user which lesson applies, offering:") OR wrap in cc-only markers. **Careful:** the nearby example lesson label `"L-014 [pattern] Use AskUserQuestion for ambiguous intents"` is *data* (an example lesson title), not an instruction — reword the example label to `"L-014 [pattern] Ask structured questions for ambiguous intents"` so the banned-token scan passes without a misleading cc-only wrap.
- `bug`, `issue`, `add-idea`, `check-todos`: no known hits; grep to confirm.

- [ ] **Step 1:** Update the six lint tests first, verify FAIL.
- [ ] **Step 2:** Convert all six (mkdir, git mv references + templates, playbook.md, wrapper).
- [ ] **Step 3:** Apply neutralizations; grep banned tokens; fix.
- [ ] **Step 4:** Run: `python -m pytest plugins/taskmaster/tests/ -q -k "skill or adapter or catalog or budget"` — PASS; checker OK; descriptions unchanged.
- [ ] **Step 5: Commit**

```bash
git add plugins/taskmaster/playbooks plugins/taskmaster/skills plugins/taskmaster/tests
git commit -m "refactor(taskmaster): entity skills -> playbooks (bug, issue, lesson, decision, add-idea, check-todos)"
```

---

### Task 6: Convert setup skills — init-taskmaster, migrate-v3, linear

Same mechanics, for `init-taskmaster`, `migrate-v3`, `linear` (`linear` has no references dir).

**Known CC-isms (heaviest of the batch — these two SKILL.md bodies embed full `AskUserQuestion({...})` call blocks):**
- `init-taskmaster` body: two `AskUserQuestion({...})` JSON blocks + the instruction ``Use `AskUserQuestion` with both questions in a single call``. Rewrite: keep the *questions and options* as a neutral markdown list ("Ask the user, in one round if your tool supports multi-question prompts: 1. <question> — options: A / B …"), and put the original `AskUserQuestion({...})` blocks inside `<!-- cc-only:start -->` markers as the CC-native form. Same for `references/analysis-mode.md` (one mention → neutral phrasing).
- `migrate-v3` body: one `AskUserQuestion({...})` block (the mandatory opt-in confirm) + heading "(AskUserQuestion — MANDATORY)". Same treatment: heading → "(confirm with the user — MANDATORY)"; block → neutral option list + cc-only-wrapped original. `references/migration-steps.md`: one mention → neutral phrasing.
- `linear`: grep to confirm; no known hits.

- [ ] **Step 1:** Update lint tests (`test_init_taskmaster_skill_lint.py`, `test_migrate_v3_skill_lint.py`; linear has none — confirm) first, verify FAIL.
- [ ] **Step 2:** Convert all three.
- [ ] **Step 3:** Apply the rewrites above; grep banned tokens across the whole `playbooks/` tree now: `grep -rn "AskUserQuestion\|subagent_type\|CLAUDE_PLUGIN_ROOT" plugins/taskmaster/playbooks/ | grep -v cc-only` — every remaining hit must sit inside cc-only markers (checker verifies authoritatively).
- [ ] **Step 4:** Run: `python -m pytest plugins/taskmaster/tests/ -q -k "skill or adapter or catalog or budget"` — PASS; checker OK; descriptions unchanged.
- [ ] **Step 5: Commit**

```bash
git add plugins/taskmaster/playbooks plugins/taskmaster/skills plugins/taskmaster/tests
git commit -m "refactor(taskmaster): setup skills -> playbooks (init-taskmaster, migrate-v3, linear)"
```

---

### Task 7: Strict coverage, commands audit, version bump, changelog

**Files:**
- Modify: `plugins/taskmaster/.claude-plugin/plugin.json` (version `3.21.1` → `3.22.0`)
- Modify: `.claude-plugin/marketplace.json` (taskmaster entry → `3.22.0`)
- Modify: `plugins/taskmaster/CHANGELOG.md` (new `## 3.22.0` section)
- Possibly modify: `plugins/taskmaster/commands/*.md` (8 files — audit only)

**Interfaces:**
- Consumes: all 17 conversions complete (Tasks 2–6).

- [ ] **Step 1: Strict coverage gate**

Run: `python plugins/taskmaster/scripts/check_adapter_coverage.py --strict`
Expected: `OK (0 error(s))` — all 17 skills converted. Fix any stragglers before proceeding.

- [ ] **Step 2: Commands audit**

Read each of the 8 files in `plugins/taskmaster/commands/`. They should be thin entry points (per the "commands merged into skills" convention). For any command whose body duplicates workflow content now living in a playbook, replace that content with the one-line pointer `Follow the playbook at playbooks/<name>/playbook.md (plugin root).` Leave already-thin commands untouched. Record which files changed in the commit message.

- [ ] **Step 3: Full test suite**

Run: `python -m pytest plugins/taskmaster/tests/ -q`
Expected: PASS (same count as pre-Phase-1 baseline plus the new adapter-coverage tests; capture the baseline count with `git stash`-free comparison only if failures appear — the suite was green at 1514 on 3.21.1).

- [ ] **Step 4: Version bump (all three parts)**

- `plugins/taskmaster/.claude-plugin/plugin.json`: `"version": "3.22.0"`
- `.claude-plugin/marketplace.json`: taskmaster `version` → `"3.22.0"`
- `plugins/taskmaster/CHANGELOG.md`, new top section:

```markdown
## 3.22.0

Multi-assistant generalization, Phase 1 (spec: 2026-07-06-taskmaster-multi-assistant-design.md).

- All 17 skills' workflow content extracted to assistant-neutral `playbooks/<name>/playbook.md` (+ references/templates); SKILL.md files are now thin trigger wrappers — behavior on Claude Code unchanged.
- New `playbooks/CONVENTIONS.md` (authoring/neutrality rules) and `scripts/check_adapter_coverage.py` (1:1 wrapper↔playbook mapping + banned-CC-token scan; `--strict` gate).
- Skill lint tests re-pointed at playbooks; body token budgets now cover wrapper+playbook combined.
```

- [ ] **Step 5: Version check script**

Run: `python scripts/check_plugin_version_bump.py --base origin/master`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add plugins/taskmaster/.claude-plugin/plugin.json .claude-plugin/marketplace.json plugins/taskmaster/CHANGELOG.md plugins/taskmaster/commands
git commit -m "chore(taskmaster): bump to 3.22.0 - playbook extraction phase 1 complete"
```

- [ ] **Step 7: Final live verification (manual)**

Restart the MCP server / reload the plugin (known gotcha: plugin changes need a fresh session). Ask the user to exercise 2–3 skills (`start-session`, `bug`, `end-session`) in a real session and confirm identical behavior. Phase 1 is done when the user signs off; Phases 2–5 (extraction, adapters) get their own plans.

---

## Verification checklist (whole phase)

- `check_adapter_coverage.py --strict` exits 0.
- `python -m pytest plugins/taskmaster/tests/ -q` green.
- `git log --oneline` shows one commit per task, no stray files swept in (subagent commit hygiene: explicit pathspecs only).
- Every SKILL.md `description` byte-identical to pre-phase (`git diff <baseline>..HEAD -- 'plugins/taskmaster/skills/*/SKILL.md' | grep '^[-+]description:'` outputs nothing).
- No push — everything stays local pending user approval.
