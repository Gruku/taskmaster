# User intent: N17 - the plugin ships as `uv run <plugin>/backlog_server.py` (plus hooks and
# CLIs), not as this dev venv, so the packaged runtime's declarations, versions and entry
# points are pinned by tests that do not depend on what happens to be installed here.
from __future__ import annotations

import ast
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Scripts `uv run` executes in script mode: each carries its own PEP 723 header.
SCRIPT_ENTRIES = ("backlog_server.py", "taskmaster/backlog_server.py", "taskmaster_cli.py",
                  "hooks/merge_recorder_stamp.py")
# Import name -> distribution name, where they differ.
DISTRIBUTION = {"yaml": "pyyaml"}


def _script_metadata(path: Path) -> dict:
    match = re.search(r"(?m)^# /// script\s*$\n((?:^#.*$\n)+?)^# ///\s*$", path.read_text(encoding="utf-8"))
    assert match, f"{path.name} has no PEP 723 `# /// script` block"
    body = "".join(line[2:] if line.startswith("# ") else line[1:] for line in match.group(1).splitlines(True))
    return tomllib.loads(body)


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _name(requirement: str) -> str:
    return re.split(r"[<>=!~;\[ ]", requirement, maxsplit=1)[0].strip().lower()


def _load(relative: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── dependency declarations ────────────────────────────────────────────────


@pytest.mark.parametrize("entry", SCRIPT_ENTRIES)
def test_script_headers_declare_exactly_the_pyproject_dependencies(entry):
    header = _script_metadata(ROOT / entry)
    project = _pyproject()["project"]
    assert sorted(header["dependencies"]) == sorted(project["dependencies"])
    assert header["requires-python"] == project["requires-python"]


def test_every_third_party_import_is_declared():
    """httpx and pydantic were imported directly but arrived only through fastmcp (B-091)."""
    declared = {_name(r) for r in _pyproject()["project"]["dependencies"]}
    imported: dict[str, set[str]] = {}
    for path in [*ROOT.joinpath("taskmaster").rglob("*.py"), ROOT / "backlog_server.py"]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top not in sys.stdlib_module_names and top != "taskmaster":
                    imported.setdefault(DISTRIBUTION.get(top, top), set()).add(path.name)
    missing = {dist: sorted(files) for dist, files in imported.items() if dist not in declared}
    assert not missing, f"imported but not declared: {missing}"


def test_fastmcp_is_held_below_the_next_major():
    """An unpinned header let a fresh `uv run` resolve FastMCP 4.x, a major nobody tested."""
    spec = next(r for r in _pyproject()["project"]["dependencies"] if _name(r) == "fastmcp")
    assert re.search(r"<\s*4(\b|\.)", spec), spec


def test_dev_extra_can_collect_and_run_the_suite():
    """B-091: `uv pip install -e ".[dev]"` must produce a venv that runs the suite."""
    dev = {_name(r) for r in _pyproject()["project"]["optional-dependencies"]["dev"]}
    assert {"pytest", "pytest-xdist", "pytest-timeout"} <= dev


# ── version alignment ──────────────────────────────────────────────────────


def test_repository_versions_are_aligned():
    bump = _load("scripts/bump_version.py", "bump_version_t")
    assert bump.check(ROOT) == []


def test_bump_rewrites_every_version_string_and_nothing_else(tmp_path):
    bump = _load("scripts/bump_version.py", "bump_version_t")
    for relative in bump.VERSIONED_FILES + ("CHANGELOG.md",):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, tmp_path / relative)
    before = {rel: (tmp_path / rel).read_bytes() for rel in bump.VERSIONED_FILES}
    old = bump.versions(tmp_path)[".claude-plugin/plugin.json"]

    changed = bump.bump(tmp_path, "9.8.7")

    assert sorted(changed) == sorted(bump.VERSIONED_FILES)
    assert set(bump.versions(tmp_path).values()) == {"9.8.7"}
    for rel in bump.VERSIONED_FILES:
        # Only the version token moved, line endings kept: uv.lock's own `version = 1`
        # header and pyyaml's 6.0.3 wheel lines are not the plugin's version.
        old_lines, new_lines = before[rel].splitlines(True), (tmp_path / rel).read_bytes().splitlines(True)
        assert len(old_lines) == len(new_lines), rel
        diff = [(a, b) for a, b in zip(old_lines, new_lines) if a != b]
        assert len(diff) == 1, (rel, diff)
        assert diff[0][1] == diff[0][0].replace(old.encode(), b"9.8.7"), (rel, diff)
    assert bump.check(tmp_path) == ["CHANGELOG.md has no '## 9.8.7' heading"]
    with (tmp_path / "CHANGELOG.md").open("a", encoding="utf-8") as changelog:
        changelog.write("\n## 9.8.7\n")
    assert bump.check(tmp_path) == []


def test_bump_carries_a_release_candidate_into_the_badge(tmp_path):
    """shields.io reads `-` as a separator: a release candidate's badge needs `--`."""
    bump = _load("scripts/bump_version.py", "bump_version_t")
    for relative in bump.VERSIONED_FILES + ("CHANGELOG.md",):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, tmp_path / relative)
    bump.bump(tmp_path, "6.9.0-rc.1")
    assert set(bump.versions(tmp_path).values()) == {"6.9.0-rc.1"}
    assert "badge/version-6.9.0--rc.1-" in (tmp_path / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("version", ["6.1", "6.1.0-rehearsal", "v6.1.0"])
def test_bump_refuses_a_version_pip_and_uv_cannot_read(tmp_path, version):
    bump = _load("scripts/bump_version.py", "bump_version_t")
    with pytest.raises(ValueError):
        bump.bump(tmp_path, version)


def test_version_falls_back_to_the_codex_manifest(tmp_path):
    """The Codex distribution ships `.codex-plugin/` only; its viewer reported 0.0.0."""
    from taskmaster import backlog_server as bs

    assert bs._plugin_version(tmp_path) == "0.0.0"
    (tmp_path / ".codex-plugin").mkdir()
    (tmp_path / ".codex-plugin" / "plugin.json").write_text('{"version": "1.2.3"}', encoding="utf-8")
    assert bs._plugin_version(tmp_path) == "1.2.3"
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"version": "1.2.4"}', encoding="utf-8")
    assert bs._plugin_version(tmp_path) == "1.2.4"


def test_mcp_server_reports_the_plugin_version():
    """serverInfo.version was FastMCP's own version (4.0.10 in a fresh packaged env)."""
    from taskmaster import backlog_server as bs

    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert bs.VERSION == manifest["version"]
    assert bs.mcp.version == bs.VERSION


# ── packaged CLI entry ─────────────────────────────────────────────────────


def _cli(*args, code=None):
    argv = [sys.executable, str(ROOT / "taskmaster_cli.py"), *args] if code is None else [sys.executable, "-c", code]
    return subprocess.run(argv, capture_output=True, text=True, timeout=120, cwd=str(ROOT.parent),
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


@pytest.mark.parametrize("command, module_usage", [
    ("cutover", "--dry-run"),
    ("git", "checkout"),
    ("git-hook", "pre-commit"),
])
def test_cli_dispatches_to_each_module(command, module_usage):
    result = _cli(command, "--help")
    assert result.returncode == 0, result.stderr
    assert module_usage in result.stdout


def test_cli_rejects_an_unknown_command():
    result = _cli("nope")
    assert result.returncode == 2
    assert "cutover" in result.stderr and "git-hook" in result.stderr


def test_cli_never_imports_the_server():
    """Importing taskmaster.backlog_server starts the viewer; a CLI run must not."""
    shim = (ROOT / "taskmaster_cli.py").as_posix()
    code = ("import runpy, sys\n"
            f"sys.argv = [{shim!r}, 'cutover', '--help']\n"
            "try:\n"
            f"    runpy.run_path({shim!r}, run_name='__main__')\n"
            "except SystemExit:\n"
            "    pass\n"
            "print('SERVER' if 'taskmaster.backlog_server' in sys.modules else 'CLEAN')\n")
    result = _cli(code=code)
    assert result.stdout.strip().splitlines()[-1] == "CLEAN", result.stdout + result.stderr


def test_cli_launch_is_not_mistaken_for_a_store_client():
    """The cutover's process scan excludes only its own pid; its `uv run` parent and venv
    launcher carry the same command line and must not match the launcher inventory."""
    from taskmaster.native import quiesce

    root = "C:/work/project"
    for command in (f"uv run C:/p/plugins/taskmaster/taskmaster_cli.py cutover --root {root} --dry-run",
                    f"C:/cache/env/Scripts/python.exe C:/p/plugins/taskmaster/taskmaster_cli.py cutover --root {root}"):
        assert quiesce._classify({"pid": 7, "command_line": command}, [root.lower()]) is None, command


# ── merge-recorder interpreter ─────────────────────────────────────────────


def test_stamp_runs_on_the_hook_interpreter_when_it_has_the_dependencies():
    recorder = _load("hooks/merge_recorder.py", "merge_recorder_t")
    argv = recorder.stamp_command(Path("s.py"), ["feat", "main", "abc"], in_process=True, uv="C:/bin/uv.exe")
    assert argv == [sys.executable or "python", "s.py", "feat", "main", "abc"]


def test_stamp_logs_when_the_server_cannot_be_imported(tmp_path):
    """Without fastmcp the stamp used to return silently: the merge went unrecorded and
    nothing said why."""
    from test_merge_recorder_hook import _init_git_repo, _seed

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    _seed(repo)
    stamp = (ROOT / "hooks" / "merge_recorder_stamp.py").as_posix()
    code = ("import runpy, sys\n"
            "sys.modules['fastmcp'] = None\n"  # the hook interpreter has no fastmcp
            f"sys.argv = [{stamp!r}, 'feature/x', 'master', 'abc1234', {str(repo)!r}]\n"
            f"runpy.run_path({stamp!r}, run_name='__main__')\n")
    subprocess.run([sys.executable, "-c", code], cwd=str(repo), capture_output=True, text=True, timeout=120,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log = (repo / ".taskmaster" / "local" / "hook.log").read_text(encoding="utf-8")
    assert "cannot import the Taskmaster server" in log, log


def test_stamp_runs_under_uv_when_the_hook_interpreter_lacks_them():
    """Hooks run on whatever system Python exists; the stamp imports the server (fastmcp,
    pydantic, pyyaml), which the plugin installs only into its uv script environment."""
    recorder = _load("hooks/merge_recorder.py", "merge_recorder_t")
    argv = recorder.stamp_command(Path("s.py"), ["feat", "main", "abc"], in_process=False, uv="C:/bin/uv.exe")
    assert argv == ["C:/bin/uv.exe", "run", "--script", "s.py", "feat", "main", "abc"]
    assert recorder.stamp_command(Path("s.py"), ["feat"], in_process=False, uv=None) is None


def test_a_uv_stamp_does_not_hold_the_hook_past_its_timeout(tmp_path):
    """hooks.json gives the recorder 10 s; building the stamp's uv environment the first
    time takes about 4 s with warm wheels and more with a cold index, before the stamp's
    own work. The uv path is started detached; the in-interpreter path still waits."""
    import time

    recorder = _load("hooks/merge_recorder.py", "merge_recorder_t")
    marker = tmp_path / "done"
    slow = [sys.executable, "-c", f"import time, pathlib; time.sleep(3); pathlib.Path({str(marker)!r}).touch()"]

    started = time.monotonic()
    recorder.run_stamp(slow, detach=True)
    assert time.monotonic() - started < 2
    assert not marker.exists()
    deadline = time.monotonic() + 30
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert marker.exists(), "the detached stamp must still run to completion"

    marker.unlink()
    recorder.run_stamp(slow, detach=False)
    assert marker.exists()


def test_pre_commit_guidance_names_a_command_an_install_can_run():
    """`python -m taskmaster.coordinator.git_cli` only works from a checkout with the package
    on the path; an installed plugin has no such interpreter."""
    from taskmaster.coordinator import git_hook

    cli = f'uv run "{(ROOT / "taskmaster_cli.py").as_posix()}"'
    assert f'{cli} git commit -m "<message>"' in git_hook.GUIDANCE
    assert f"{cli} git status" in git_hook.GUIDANCE
    assert "python -m" not in git_hook.GUIDANCE


def test_bump_writes_nothing_when_any_file_cannot_be_bumped(tmp_path):
    """A half-applied bump leaves the repository misaligned; compute first, then write."""
    bump = _load("scripts/bump_version.py", "bump_version_t")
    for relative in bump.VERSIONED_FILES + ("CHANGELOG.md",):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, tmp_path / relative)
    (tmp_path / "README.md").write_text("no badge here\n", encoding="utf-8")
    before = {rel: (tmp_path / rel).read_bytes() for rel in bump.VERSIONED_FILES}
    with pytest.raises(ValueError):
        bump.bump(tmp_path, "9.8.7")
    assert {rel: (tmp_path / rel).read_bytes() for rel in bump.VERSIONED_FILES} == before


def test_a_release_candidate_heading_does_not_satisfy_the_final_version(tmp_path):
    bump = _load("scripts/bump_version.py", "bump_version_t")
    for relative in bump.VERSIONED_FILES:
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, tmp_path / relative)
    # Its own changelog: the repository's real headings must not decide this test.
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## 9.9.0-rc.1\n\nnotes\n\n## 9.8.0\n", encoding="utf-8")
    bump.bump(tmp_path, "9.9.0")
    assert bump.check(tmp_path) == ["CHANGELOG.md has no '## 9.9.0' heading"]
    bump.bump(tmp_path, "9.9.0-rc.1")
    assert bump.check(tmp_path) == []
    changelog.write_text("# Changelog\n\n## 9.9.0 - final\n\n## 9.9.0-rc.1\n", encoding="utf-8")
    bump.bump(tmp_path, "9.9.0")
    assert bump.check(tmp_path) == []


def test_the_build_is_fixed_when_the_package_is_imported():
    """A process reports the code it loaded: the digest is taken at import, not at the first
    Client(), so an in-place upgrade after import cannot change what it claims to run."""
    code = ("from taskmaster.coordinator import protocol\n"
            "def unread(*a, **k):\n"
            "    raise SystemExit('digest read after import')\n"
            "protocol.package_digest = unread\n"
            "protocol.declared_version = unread\n"
            "print(protocol.build_identity()['digest'])\n")
    result = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    assert result.returncode == 0 and len(result.stdout.strip()) == 32, result.stdout + result.stderr


def test_check_accepts_uv_s_normalized_pre_release_in_the_lock(tmp_path):
    """uv writes a pre-release as 7.0.0rc1 in uv.lock; that is 7.0.0-rc.1."""
    bump = _load("scripts/bump_version.py", "bump_version_t")
    for relative in bump.VERSIONED_FILES:
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, tmp_path / relative)
    (tmp_path / "CHANGELOG.md").write_text("## 9.9.0-rc.1\n", encoding="utf-8")
    bump.bump(tmp_path, "9.9.0-rc.1")
    lock = (tmp_path / "uv.lock").read_bytes().decode("utf-8")
    assert 'name = "taskmaster"\nversion = "9.9.0rc1"' in lock.replace("\r\n", "\n"), "bump writes uv's form"
    assert bump.check(tmp_path) == []
    for spelled, ok in (('"9.9.0-rc.1"', True), ('"9.9.0rc2"', False)):
        (tmp_path / "uv.lock").write_bytes(lock.replace('"9.9.0rc1"', spelled).encode("utf-8"))
        assert (bump.check(tmp_path) == []) is ok, spelled
