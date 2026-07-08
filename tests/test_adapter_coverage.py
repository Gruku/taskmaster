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


def test_stale_old_layout_path_fails(tmp_path):
    make_pair(tmp_path, playbook_text="# Demo\n\nsee skills/other/references/foo.md\n")
    r = run(tmp_path)
    assert r.returncode == 1
    assert "stale old-layout path" in r.stdout
    assert "skills/other/references/" in r.stdout


def test_clean_relative_playbook_reference_passes(tmp_path):
    make_pair(tmp_path, playbook_text="# Demo\n\nsee ../other/playbook.md\n")
    r = run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


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


# --- adapter (codex + agents-md) coverage ----------------------------------

def make_adapters(root: Path, name: str = "demo"):
    """Minimal healthy adapters for a single playbook `name`."""
    prompts = root / "adapters" / "codex" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / f"{name}.md").write_text(
        f"Read `{{{{TASKMASTER_HOME}}}}/playbooks/{name}/playbook.md` and execute it.\n",
        encoding="utf-8")
    agents = root / "adapters" / "agents-md"
    agents.mkdir(parents=True)
    (agents / "AGENTS.md").write_text(
        f"| intent | `playbooks/{name}/playbook.md` |\n", encoding="utf-8")


def test_adapters_absent_entirely_still_passes(tmp_path):
    # repos without adapters/ (pre-phase-3 layouts) stay valid
    make_pair(tmp_path)
    assert run(tmp_path).returncode == 0


def test_healthy_adapters_pass(tmp_path):
    make_pair(tmp_path)
    make_adapters(tmp_path)
    r = run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_missing_codex_prompt_fails(tmp_path):
    make_pair(tmp_path)
    make_adapters(tmp_path)
    (tmp_path / "adapters" / "codex" / "prompts" / "demo.md").unlink()
    r = run(tmp_path)
    assert r.returncode == 1
    assert "no codex prompt wrapper" in r.stdout


def test_codex_prompt_missing_pointer_fails(tmp_path):
    make_pair(tmp_path)
    make_adapters(tmp_path)
    (tmp_path / "adapters" / "codex" / "prompts" / "demo.md").write_text(
        "Do taskmaster things.\n", encoding="utf-8")
    r = run(tmp_path)
    assert r.returncode == 1
    assert "missing pointer" in r.stdout


def test_agents_md_missing_reference_fails(tmp_path):
    make_pair(tmp_path)
    make_adapters(tmp_path)
    (tmp_path / "adapters" / "agents-md" / "AGENTS.md").write_text(
        "# Rules with no table\n", encoding="utf-8")
    r = run(tmp_path)
    assert r.returncode == 1
    assert "missing reference" in r.stdout


def test_banned_token_in_adapter_fails(tmp_path):
    make_pair(tmp_path)
    make_adapters(tmp_path)
    (tmp_path / "adapters" / "codex" / "AGENTS.md").write_text(
        "Use CLAUDE_PLUGIN_ROOT to find things.\n", encoding="utf-8")
    r = run(tmp_path)
    assert r.returncode == 1
    assert "CLAUDE_PLUGIN_ROOT" in r.stdout
