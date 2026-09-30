<!-- User intent: the executable, task-by-task plan for cutting the taskmaster test suite and its verification cycle from hours to minutes, per the approved speed spec. -->

# Test Suite Speed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the full suite from ~30 min to a `merge` run of as few minutes as the measured levers allow, verify ordinary changes in under 2 minutes, and replace "a full run every review round" with one `merge` run per merge plus one `release` run per release. No test is lost.

**Architecture:** Step 1 removes waste while keeping the same tests. It has three parts:
- per-session temp dirs with out-of-band cleanup;
- an in-process durability switch that tests turn off;
- per-worker seeded-project templates that tests copy instead of rebuilding.

Step 2 changes when things run:
- a `release` marker absorbs `scale`, and the heavy tail moves into it with representatives left in `merge`;
- one runner, `scripts/run_tests.py {changed,merge,release}`, adds a shared single-flight lock, a RAM preflight and a detached mode;
- policy docs teach agents to use `changed` in review rounds.

**Tech Stack:** Python 3.12 (uv-managed), pytest 9.1.1, pytest-xdist 3.8.0, SQLite 3.47.1, Windows 11 (primary), stdlib and ctypes only.

**Spec:** `docs/specs/2026-09-29-test-suite-speed-design.md` (commit `7546e0a`). Read it before any task. The plan argues from it.

## Global Constraints

- Merge suite target: **≤ 10 min wall at `-n 3`**. `changed` target: **< 2 min** in the typical case (spec §2 G1/G2). See "Honest estimate" below: G1 is at risk.
- **No silent coverage loss.** `merge` ∪ `release` collect exactly today's test IDs, plus only the listed reduced variants (spec §7).
- **Durability switch: in-process only.** No environment variable, CLI flag or config key may disable durability (spec §3.2).
- **Templates are per worker and in-session only, never persisted across runs.**
  - Every template has an explicit `template_key`; never key on `seed.__code__` alone.
  - `TASKMASTER_TWINS_VERIFY` is part of every key.
  - `TASKMASTER_TEMPLATE_VERIFY=1` turns on the fresh-vs-copy diff (spec §3.3).
- **`release` absorbs `scale`.** Both are deselected unless the `-m` expression names them, and `-m scale` keeps working. "Everything" is `-m "release or not release"` (spec §4.2, Task 8).
- **Runner RAM preflight:** refuse below **2 GB** free (spec §4.1).
- **Temp dirs:** no pytest session deletes old runs inline. A one-test session run right after a full run takes **< 10 s** wall (spec §3.1).
- **No new third-party dependencies.**
- **Headers and commits:**
  - New files open with a 1–3 line user-intent header.
  - Commit messages end with `Co-Authored-By: Claude <noreply@anthropic.com>`.
- **Branch and worktree:** branch `feat/test-speed` in worktree `.worktrees/test-speed`, created off `feat/database-native-foundation` by Task 1. Merge back only after Task 12. **Never push** without the user's explicit permission.
- **Heavy runs:** full-suite runs happen only in Tasks 1, 2 and 12. Run them detached, one at a time, on a quiet machine. Every other verification step names specific files.

## Execution order

| Wave | Tasks | Notes |
|---|---|---|
| 0 | 1 | Worktree, analyzer, **quiet-machine baseline**. Every later number compares against this baseline. |
| 1 | 2, 3, 4, 8 | These are independent, **but 2, 3, 4 and 8 all edit `tests/conftest.py`.** Run them in parallel worktrees only if each merges into `feat/test-speed` one at a time, rebasing on the previous one. Otherwise run them in sequence. |
| 2 | 5, 6 (6a → 6b → 6c), 9 | 5 and 6 need 4; 9 needs 8. |
| 3 | 7, 10 | 7 measures after Step 1 lands. 10 needs 1 and 8. |
| 4 | 11 | Needs 3, 4, 8, 9 and 10. |
| 5 | 12 | Final acceptance. |

## Honest estimate (read before executing)

The spec estimated about 9–10 min. The drafting measurements came in lower than the spec assumed:

| Lever | Spec assumed | Drafts measured or derived |
|---|---|---|
| Templates (Tasks 4–6) | ~1,500 worker-s | **~915 worker-s**. `build_project`, the crash-child files and `tm_epic_phase` must stay fresh. |
| Moving the heavy tail to `release` (Task 9) | 1,300–1,600 worker-s | **~650 worker-s net** under the spec's own rule. Unique scenarios stay in `merge`. |
| Durability (Task 3) | ~800 worker-s | **Unconfirmed.** The only probe was noisy. Task 3 measures it. |
| Temp churn (Task 2) | — | Minutes of wall time at session end, and one-test sessions become fast. The per-test gain is unmeasured. |
| Shutdown latency (Task 7) | — | Likely dropped. Teardown was 3.3% in the noisy profile, under the 5% rule. |

From the 5,365 worker-s profile (402c17c; N16 has since added 269 tests), the result is **~3,200–3,800 worker-s, which is ~18–21 min at `-n 3`.** That is roughly half of today's time, but **G1 is likely missed**.

Task 12 measures the real number. It also records a `merge` run at higher worker counts with the minimum free RAM seen, so you can choose between:
- more workers;
- a wider `release` move;
- pruning the legacy half of the twins (Step 3).

None of these is applied automatically.

## Spec deviations resolved in this plan

The drafters found these and resolved them as stated. Each resolution is visible in the owning task.

1. **§4.1 `changed` rule 2** applies to *every* changed Python module. The spec's list missed test modules imported by other tests (e.g. `from test_native_service import root`, 17 importers), plus `agent_journeys.py`, `cutover_stubs.py`, `skill_budget_helper.py` and `scripts/*.py`. **Rule 3** also matches `.py` hooks and scripts that are loaded by path (Task 10).
2. **§3.1 cleanup** also skips any run whose controller pid is alive, so it never deletes a suite running in another worktree (Task 2).
3. **§4.1 lock** lives in the *main* checkout's `test-results/`, shared by all worktrees. A per-worktree lock would not give "one suite at a time". The lock also stays held while the pytest child lives, even if the runner wrapper was killed (Task 10).
4. **§4.1 `release` acceptance scripts:** no runbook names them, so the runner prints a reminder naming `scripts/native_n16_acceptance.py` and `scripts/native_n15_rehearsal.py`. It does not run them (Task 10).
5. **§4.1 "absorbs `run_detached_check.py`"** is kept as it is. Its docstring points to `run_tests.py --detach` (Task 10).
6. **§3.2 site counts:**
   - The spec's "22 fsync + 5 synchronous" were raw grep line counts. The real sites are **15 fsync and 4 `synchronous=FULL`**.
   - **16 `sqlite3.connect` sites** exist. The 5 that write get `durability.relax()`, which does nothing in production. `Store.connection` routes its explicit pragma. 10 never commit and go on an allowlist that an AST guard enforces (Task 3).
   - A third test needs the `durability` marker: `test_store_recovery.py:615`.
   - `test_native_projection_drain.py:57` doesn't actually observe fsync. It is marked anyway, per the spec.
7. **§3.3 must stay fresh:**
   - `build_project`, because it stores wall-clock timestamps.
   - The crash-child files `native_progress`, `projection_faults`, `projection_review` and `projection_review2`.
   - `tm_epic_phase`, because a copy would split the store session.
   - Templates also skip the sync-fingerprint cache file that activation writes only when a build runs slowly. Verify mode failed on it twice.
   - `root` has 16 importers, not 12, and 10 of them opt out with `ROOT_MUST_STAY_FRESH` (Tasks 4–6).
8. **§4.2 moves:**
   - Paging and direct related-seed tests loop internally, so their IDs can't be kept. The full tests move to `release` and gain reduced `merge` variants.
   - Cutover crash cases and the end-to-end related-seed cases each take under 5 s. They move only because §4.2 names them (Task 9).
9. **§3.4 denominator:** Task 7 divides by the worker-seconds of the same measurement run, taken after Step 1. **Gate edge case:** `-m release` pulls in `scale` because the gate also treats `scale` items as `release`, not by substring (Task 8).
10. **G2 measurement** uses `changed --base HEAD`, because on `feat/test-speed` itself `conftest.py` has changed, so rule 4 selects everything (Tasks 11–12).

11. **Slow-test warnings live in two places on purpose.** The conftest `pytest_terminal_summary` warning (Task 8) knows markers and serves bare `pytest`. The runner's `--warn-over 10` summary (Task 10) is what an agent sees, since pytest output goes to `pytest.log`. It is skipped for `release`, where it can't tell tiers apart.

## Review Focus

Failure modes the spec implies that the original task tests did not cover. Each now has a test in its owning task.

1. **A `release`/`scale` test run by explicit node ID must run,** not be silently deselected with exit 5 (Task 8).
2. **`changed` with an unresolvable `--base` must fail non-zero.** It must never report "no tests selected" and exit 0, because that is a false green (Task 10).
3. **The suite lock must stay held while the pytest child is alive, even after the runner wrapper is killed by the memory reaper.** Otherwise a second suite starts in parallel and exhausts RAM (Task 10).
4. **A detached run that dies before starting must be reported,** not announced as "Started PID" with exit 0 (Task 10).
5. **Process-global state after a templated build must equal a fresh build's.** This covers module globals beyond `CLOCK`, `_session_task` and `_session_bundle`, which the file/DB diff can't see. Verify mode checks it as well (Task 4).

---

### Task 1: Worktree, baseline profile and durations analyzer

**Files:**
- Create: `scripts/analyze_test_durations.py`
- Test: `tests/test_analyze_test_durations.py`
- Create: `docs/reports/2026-09-29-test-speed-baseline.md`
- Create: `docs/reports/data/2026-09-29-test-speed-baseline-ids.txt`

**Interfaces:**
- Consumes: `scripts/run_detached_check.py --name NAME -- COMMAND…` (existing, unchanged).
- Produces:
  - CLI `python scripts/analyze_test_durations.py --junit PATH [--durations-log PATH] [--top N] [--warn-over SECONDS] [--basetemp DIR]`; `main(argv: list[str] | None = None) -> int`.
  - `Case(file, nodeid, name, seconds, outcome)`, `Footprint(bytes, files, dirs)`, `load_cases(junit: Path) -> list[Case]`, `phase_totals(log_text) -> dict[str, float]`, `session_summary(log_text) -> str | None`, `temp_footprint(basetemp, cases)`, `footprint_outliers(by_file, factor=10.0)`.
  - Worktree `.worktrees/test-speed` on `feat/test-speed` with its own `.venv`.
  - `docs/reports/data/2026-09-29-test-speed-baseline-ids.txt`: every collected test ID at baseline, all tiers (the §7 "same IDs" reference for every later task).
  - `docs/reports/2026-09-29-test-speed-baseline.md` with a `## Per-step measurements` table that Tasks 2+ append to.

Why the analyzer exists: the throwaway `analyze_profile.py` hard-coded file names and missed the slowest-tests list, phase shares and the temp footprint the spec's §3.1, §3.4 and §4.2 decisions need. Layout facts it relies on, read at HEAD in the venv: `_pytest/tmpdir.py:282-287` names each `tmp_path` `re.sub(r"[\W]", "_", node.name)[:30]` and numbers it through `make_numbered_dir` (`_pytest/pathlib.py:224-242`, which also drops a `<prefix>current` symlink, observed on this machine); `xdist/workermanage.py:337-341` gives each worker `<basetemp>/popen-<gw id>` and `xdist/remote.py:392-400` applies it as the worker's `--basetemp`. So a test dir is `<basetemp>/popen-gwN/<first 30 sanitized chars of the test name><n>/`. The junit `name` carries xdist's `@<group>` suffix under `loadgroup`; `node.name` does not, so the analyzer strips it. Session- and module-scoped `tmp_path_factory.mktemp()` dirs match no test name and are reported as "unattributed".

- [ ] **Step 1: Create the worktree and its own venv**

From the main checkout root (`C:\Users\gruku\Files\Claude\taskmaster`), PowerShell:

```powershell
git worktree add .worktrees/test-speed -b feat/test-speed feat/database-native-foundation
New-Item -ItemType Directory -Force .worktrees\test-speed\test-results | Out-Null
uv pip freeze --python .venv\Scripts\python.exe --exclude-editable |
  Where-Object { $_ -notmatch '^taskmaster==' } |
  Set-Content -Encoding utf8NoBOM .worktrees\test-speed\test-results\venv-requirements.txt
uv venv .worktrees\test-speed\.venv --python "$env:APPDATA\uv\python\cpython-3.12.9-windows-x86_64-none\python.exe"
uv pip install --python .worktrees\test-speed\.venv\Scripts\python.exe -r .worktrees\test-speed\test-results\venv-requirements.txt
uv pip install --python .worktrees\test-speed\.venv\Scripts\python.exe --no-deps -e .worktrees\test-speed
```

Why a venv per worktree (earlier worktrees ran with "the worktree's own `.venv`", memory `database-native-tracking.md:21`): the main venv's editable install points `taskmaster` at the main checkout. In-process imports would still find the worktree first, but subprocesses the suite starts with another cwd (coordinator service, hooks, crash children) would run the main checkout's code. `freeze` lists a stale `taskmaster==5.1.0` dist-info, hence the filter. The requirements file sits in the git-ignored `test-results/`, so it never shows as an untracked change.

**From here on every command runs with `.worktrees/test-speed` as the current directory.**

```powershell
.\.venv\Scripts\python.exe -c "import taskmaster, pytest, xdist; print(taskmaster.__file__, pytest.__version__, xdist.__version__)"
.\.venv\Scripts\python.exe -c "import subprocess, sys; print(subprocess.run([sys.executable, '-c', 'import taskmaster; print(taskmaster.__file__)'], cwd='C:\\', capture_output=True, text=True).stdout)"
.\.venv\Scripts\python.exe -m pytest tests/test_hooks_json.py -q -p no:cacheprovider
```

Expected: both `taskmaster` paths end in `.worktrees\test-speed\taskmaster\__init__.py`; versions `9.1.1 3.8.0`; `test_hooks_json.py` passes.

- [ ] **Step 2: Write the failing analyzer tests**

Create `tests/test_analyze_test_durations.py`:

```python
# User intent: the suite-speed numbers (baseline, per-step re-measures, runner summaries) all come
# from the durations analyzer, so its parsing and attribution are pinned here.
from __future__ import annotations

import pytest

from scripts import analyze_test_durations as analyzer

JUNIT = """<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests"><testsuite name="pytest" errors="0" failures="1" skipped="1" tests="5" time="20.0">
<testcase classname="tests.test_alpha" name="test_fast" time="0.010" />
<testcase classname="tests.test_alpha" name="test_seed_3[7]@heavy_processes" time="12.000" />
<testcase classname="tests.test_beta.TestGroup" name="test_method" time="1.500"><failure message="boom">boom</failure></testcase>
<testcase classname="tests.test_beta" name="test_skipped" time="0.001"><skipped type="pytest.skip" message="no">no</skipped></testcase>
<testcase classname="tests.test_beta" name="test_a_really_long_name_that_pytest_truncates" time="6.489" />
</testsuite></testsuites>
"""

DURATIONS = """============================= slowest durations ==============================
11.50s call     tests/test_alpha.py::test_seed_3[7]@heavy_processes
6.00s call     tests/test_beta.py::test_a_really_long_name_that_pytest_truncates
1.20s setup    tests/test_beta.py::TestGroup::test_method
0.50s teardown tests/test_alpha.py::test_seed_3[7]@heavy_processes
0.30s teardown tests/test_beta.py::TestGroup::test_method

(12 durations < 0.005s hidden.  Use -vv to show these durations.)
1 failed, 3 passed, 1 skipped in 21.07s (0:00:21)
"""


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_file_of_maps_junit_classnames_to_test_files():
    assert analyzer.file_of("tests.test_alpha") == "tests/test_alpha.py"
    assert analyzer.file_of("tests.test_beta.TestGroup") == "tests/test_beta.py"


def test_load_cases_reads_seconds_outcomes_and_drops_the_xdist_group_suffix(tmp_path):
    cases = analyzer.load_cases(_write(tmp_path, "junit.xml", JUNIT))
    assert [case.outcome for case in cases] == ["passed", "passed", "failed", "skipped", "passed"]
    assert cases[1].nodeid == "tests/test_alpha.py::test_seed_3[7]"
    assert cases[2].nodeid == "tests/test_beta.py::TestGroup::test_method"
    assert sum(case.seconds for case in cases) == pytest.approx(20.0)


def test_phase_totals_and_session_summary_come_from_the_durations_log():
    assert analyzer.phase_totals(DURATIONS) == pytest.approx({"setup": 1.2, "call": 17.5, "teardown": 0.8})
    assert analyzer.session_summary(DURATIONS) == "1 failed, 3 passed, 1 skipped in 21.07s (0:00:21)"
    assert analyzer.session_summary("===== 2 passed in 0.50s =====\n") == "2 passed in 0.50s"


def test_histogram_buckets_each_test_by_its_upper_edge():
    cases = [analyzer.Case("f", "f::t", "t", seconds, "passed") for seconds in (0.01, 0.05, 0.3, 12.0)]
    rows = {label: (count, seconds) for label, count, seconds in analyzer.histogram(cases)}
    assert rows["<= 0.05s"] == (2, pytest.approx(0.06))
    assert rows["<= 0.5s"][0] == 1
    assert rows["> 10s"][0] == 1


def test_main_prints_totals_top_files_slowest_tests_phases_and_warnings(tmp_path, capsys):
    junit = _write(tmp_path, "junit.xml", JUNIT)
    log = _write(tmp_path, "pytest.log", DURATIONS)
    assert analyzer.main(["--junit", str(junit), "--durations-log", str(log), "--top", "2", "--warn-over", "10"]) == 0
    out = capsys.readouterr().out
    assert "tests=5 worker-seconds=20.0 mean=4.000s passed=3 failed=1 error=0 skipped=1" in out
    assert "session: 1 failed, 3 passed, 1 skipped in 21.07s (0:00:21)" in out
    assert "teardown 0.8s (4.0%)" in out
    top_files = out.split("Top 2 files by worker-seconds:")[1].split("\n\n")[0]
    assert top_files.index("tests/test_alpha.py") < top_files.index("tests/test_beta.py")
    assert "1 test(s) over 10s" in out
    assert "WARNING slow test 12.00s tests/test_alpha.py::test_seed_3[7]" in out


def test_temp_footprint_attributes_tmp_dirs_to_test_files(tmp_path):
    cases = analyzer.load_cases(_write(tmp_path, "junit.xml", JUNIT))
    base = tmp_path / "run"
    seeded = base / "popen-gw0" / "test_seed_3_7_0"  # `test_seed_3[7]` sanitized, then numbered
    seeded.mkdir(parents=True)
    (seeded / "a.bin").write_bytes(b"x" * 1000)
    long_name = base / "popen-gw1" / "test_a_really_long_name_that_p0"  # truncated to 30 chars
    (long_name / "sub").mkdir(parents=True)
    (long_name / "sub" / "b.bin").write_bytes(b"y" * 10)
    (base / "popen-gw1" / "twins_template3").mkdir()
    by_file, other = analyzer.temp_footprint(base, cases)
    assert by_file == {
        "tests/test_alpha.py": analyzer.Footprint(1000, 1, 1),
        "tests/test_beta.py": analyzer.Footprint(10, 1, 2),
    }
    assert set(other) == {"twins_template"}


def test_footprint_outliers_flag_files_over_ten_times_the_median():
    by_file = {f"tests/test_{i}.py": analyzer.Footprint(100, 2, 1) for i in range(5)}
    by_file["tests/test_big.py"] = analyzer.Footprint(5_000, 2, 1)
    by_file["tests/test_many.py"] = analyzer.Footprint(100, 50, 1)
    assert analyzer.footprint_outliers(by_file) == (100, 2, ["tests/test_big.py", "tests/test_many.py"])
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_analyze_test_durations.py -q -p no:cacheprovider`
Expected: collection error, `ImportError: cannot import name 'analyze_test_durations' from 'scripts'`.

- [ ] **Step 4: Implement the analyzer**

Create `scripts/analyze_test_durations.py`:

```python
# User intent: turn a suite run's junit and durations log into a cost map (per file, per phase,
# per time bucket, temp footprint) so test-speed work is steered by measurements, not guesses.
"""Summarize where a pytest run spent its time.

    python scripts/analyze_test_durations.py --junit PATH [--durations-log PATH] [--top N]
        [--warn-over SECONDS] [--basetemp DIR]

A junit `time` is setup + call + teardown, so the sum over tests is worker-seconds. The
durations log is pytest output from `--durations=0 --durations-min=0`, which splits those
seconds by phase. `--basetemp` sizes each test file's tmp_path trees under that run's basetemp.
Stdlib only.
"""
from __future__ import annotations

import argparse
import os
import re
import statistics
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import NamedTuple

HISTOGRAM_EDGES = (0.05, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0)
OUTLIER_FACTOR = 10.0
_PHASE_LINE = re.compile(r"^(\d+(?:\.\d+)?)s (setup|call|teardown)\s+\S", re.M)
_SUMMARY_LINE = re.compile(r"^=*\s*(\d+ \w+.*? in \d+(?:\.\d+)?s(?: \([\d:]+\))?)\s*=*$", re.M)
_GROUP_SUFFIX = re.compile(r"@[\w-]+$")  # xdist loadgroup appends `@<group>` to junit names
_NON_WORD = re.compile(r"\W")
_TMP_NAME_MAX = 30  # _pytest/tmpdir.py `_mk_tmp`: re.sub(r"[\W]", "_", node.name)[:30]


class Case(NamedTuple):
    file: str
    nodeid: str
    name: str
    seconds: float
    outcome: str


class Footprint(NamedTuple):
    bytes: int
    files: int
    dirs: int


def file_of(classname: str) -> str:
    """`tests.test_x.TestY` -> `tests/test_x.py`; xunit2 junit carries no file attribute."""
    parts = classname.split(".")
    for index, part in enumerate(parts):
        if part.startswith("test_") or part.endswith("_test"):
            return "/".join(parts[: index + 1]) + ".py"
    return "/".join(parts) + ".py"


def load_cases(junit: Path) -> list[Case]:
    cases = []
    for element in ET.parse(junit).getroot().iter("testcase"):
        classname = element.get("classname", "")
        name = _GROUP_SUFFIX.sub("", element.get("name", ""))
        file = file_of(classname)
        classes = classname.split(".")[len(file[:-3].split("/")):]
        outcome = next((tag for tag in ("failure", "error", "skipped") if element.find(tag) is not None), "passed")
        cases.append(Case(file, "::".join([file, *classes, name]), name, float(element.get("time") or 0),
                          "failed" if outcome == "failure" else outcome))
    return cases


def phase_totals(log_text: str) -> dict[str, float]:
    totals = {"setup": 0.0, "call": 0.0, "teardown": 0.0}
    for match in _PHASE_LINE.finditer(log_text):
        totals[match.group(2)] += float(match.group(1))
    return totals


def session_summary(log_text: str) -> str | None:
    """pytest's final `N passed ... in Xs` line, with or without its `=` rule."""
    matches = _SUMMARY_LINE.findall(log_text)
    return matches[-1] if matches else None


def per_file(cases: list[Case]) -> list[tuple[str, int, float]]:
    totals: dict[str, list] = defaultdict(lambda: [0, 0.0])
    for case in cases:
        totals[case.file][0] += 1
        totals[case.file][1] += case.seconds
    return sorted(((file, count, seconds) for file, (count, seconds) in totals.items()),
                  key=lambda row: (-row[2], row[0]))


def histogram(cases: list[Case], edges: tuple[float, ...] = HISTOGRAM_EDGES) -> list[tuple[str, int, float]]:
    bounds = [*edges, float("inf")]
    rows = [[0, 0.0] for _ in bounds]
    for case in cases:
        index = next(i for i, bound in enumerate(bounds) if case.seconds <= bound)
        rows[index][0] += 1
        rows[index][1] += case.seconds
    labels = [f"<= {edge:g}s" for edge in edges] + [f"> {edges[-1]:g}s"]
    return [(label, count, seconds) for label, (count, seconds) in zip(labels, rows)]


def tree_size(path: Path) -> Footprint:
    size = files = dirs = 0
    for top, dirnames, filenames in os.walk(path, followlinks=False):
        dirs += len(dirnames)
        for name in filenames:
            try:
                size += os.lstat(os.path.join(top, name)).st_size
            except OSError:
                continue
            files += 1
    return Footprint(size, files, dirs)


def _owner(dirname: str, prefixes: dict[str, set[str]]) -> str | None:
    """The test file whose tmp_path is `dirname` (`<name prefix><n>`); longest prefix wins."""
    stem = dirname
    while stem and stem[-1].isdigit():
        stem = stem[:-1]
        files = prefixes.get(stem)
        if files:
            return " | ".join(sorted(files))
    return None


def temp_footprint(basetemp: Path, cases: list[Case]) -> tuple[dict[str, Footprint], dict[str, Footprint]]:
    """Bytes, files and dirs per test file, and per unattributed dir name, over every worker.

    Layout (xdist): `<basetemp>/popen-gwN/<test name[:30]><n>/`; without xdist the test dirs
    sit in `<basetemp>` itself. Dirs no test name explains are fixtures' `mktemp` dirs.
    """
    prefixes: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        prefixes[_NON_WORD.sub("_", case.name)[:_TMP_NAME_MAX]].add(case.file)
    workers = [p for p in basetemp.iterdir() if p.name.startswith("popen-gw") and p.is_dir() and not p.is_symlink()]
    by_file: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    other: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for root in workers or [basetemp]:
        for entry in root.iterdir():
            if entry.is_symlink() or not entry.is_dir():
                continue  # pytest's `<prefix>current` links point at dirs counted already
            owner = _owner(entry.name, prefixes)
            bucket = by_file[owner] if owner else other[entry.name.rstrip("0123456789")]
            size = tree_size(entry)
            bucket[0] += size.bytes
            bucket[1] += size.files
            bucket[2] += size.dirs + 1
    return ({name: Footprint(*row) for name, row in by_file.items()},
            {name: Footprint(*row) for name, row in other.items()})


def footprint_outliers(by_file: dict[str, Footprint], factor: float = OUTLIER_FACTOR) -> tuple[float, float, list[str]]:
    """Median bytes, median files, and the files over `factor` times either median."""
    if not by_file:
        return 0.0, 0.0, []
    median_bytes = statistics.median(f.bytes for f in by_file.values())
    median_files = statistics.median(f.files for f in by_file.values())
    outliers = sorted(name for name, f in by_file.items()
                      if f.bytes > factor * max(median_bytes, 1) or f.files > factor * max(median_files, 1))
    return median_bytes, median_files, outliers


def _print_footprint(basetemp: Path, cases: list[Case], top: int) -> None:
    by_file, other = temp_footprint(basetemp, cases)
    rows = [*by_file.values(), *other.values()]
    print(f"\nTemp footprint under {basetemp}: {sum(r.bytes for r in rows) / 1024**2:.1f} MB, "
          f"{sum(r.files for r in rows)} files, {sum(r.dirs for r in rows)} dirs")
    print(f"{'MB':>9} {'files':>7} {'dirs':>6}  test file")
    for name, f in sorted(by_file.items(), key=lambda kv: (-kv[1].bytes, kv[0]))[:top]:
        print(f"{f.bytes / 1024**2:9.2f} {f.files:7d} {f.dirs:6d}  {name}")
    median_bytes, median_files, outliers = footprint_outliers(by_file)
    print(f"\nOver {OUTLIER_FACTOR:g}x the median test file ({median_bytes / 1024**2:.2f} MB, "
          f"{median_files:g} files): {len(outliers)}")
    for name in outliers:
        f = by_file[name]
        print(f"{f.bytes / 1024**2:9.2f} {f.files:7d} {f.dirs:6d}  {name}")
    if other:
        print(f"\nUnattributed dirs (fixture mktemp names), top {top} by size:")
        for name, f in sorted(other.items(), key=lambda kv: (-kv[1].bytes, kv[0]))[:top]:
            print(f"{f.bytes / 1024**2:9.2f} {f.files:7d} {f.dirs:6d}  {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--durations-log", type=Path)
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--warn-over", type=float, metavar="SECONDS", help="list every test slower than this")
    parser.add_argument("--basetemp", type=Path, help="the run's basetemp, to size tmp_path use per test file")
    args = parser.parse_args(argv)
    cases = load_cases(args.junit)
    total = sum(case.seconds for case in cases)
    outcomes = Counter(case.outcome for case in cases)
    print(f"tests={len(cases)} worker-seconds={total:.1f} mean={total / max(len(cases), 1):.3f}s "
          + " ".join(f"{key}={outcomes[key]}" for key in ("passed", "failed", "error", "skipped")))
    if args.durations_log:
        text = args.durations_log.read_text(encoding="utf-8", errors="replace")
        summary = session_summary(text)
        if summary:
            print(f"session: {summary}")
        print("phases: " + ", ".join(f"{phase} {seconds:.1f}s ({100 * seconds / max(total, 1e-9):.1f}%)"
                                     for phase, seconds in phase_totals(text).items()))
    print(f"\nTop {args.top} files by worker-seconds:")
    print(f"{'seconds':>9} {'tests':>6} {'s/test':>7}  file")
    for file, count, seconds in per_file(cases)[: args.top]:
        print(f"{seconds:9.1f} {count:6d} {seconds / count:7.2f}  {file}")
    print(f"\nTop {args.top} slowest tests:")
    for case in sorted(cases, key=lambda c: (-c.seconds, c.nodeid))[: args.top]:
        print(f"{case.seconds:9.2f}  {case.nodeid}")
    print("\nPer-test time histogram:")
    for label, count, seconds in histogram(cases):
        print(f"{label:>9}: {count:6d} tests {seconds:9.1f}s")
    if args.warn_over is not None:
        slow = sorted((c for c in cases if c.seconds > args.warn_over), key=lambda c: (-c.seconds, c.nodeid))
        print(f"\n{len(slow)} test(s) over {args.warn_over:g}s")
        for case in slow:
            print(f"WARNING slow test {case.seconds:.2f}s {case.nodeid}")
    if args.basetemp:
        _print_footprint(args.basetemp, cases, args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run to verify they pass, then smoke the CLI**

Run: `.venv/Scripts/python.exe -m pytest tests/test_analyze_test_durations.py -q -p no:cacheprovider`
Expected: `7 passed`.

Run: `.venv/Scripts/python.exe scripts/analyze_test_durations.py --help`
Expected: usage text listing `--junit`, `--durations-log`, `--top`, `--warn-over`, `--basetemp`; exit 0.

- [ ] **Step 6: Commit**

```bash
git add scripts/analyze_test_durations.py tests/test_analyze_test_durations.py
git commit -m "feat(tests): durations analyzer for suite-speed measurements

Per-file worker-seconds, slowest tests, setup/call/teardown shares,
a time histogram and per-file tmp_path footprint from a junit file,
a --durations=0 log and the run's basetemp.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

- [ ] **Step 7: Record the baseline test IDs (all tiers)**

```powershell
.\.venv\Scripts\python.exe -m pytest tests --collect-only -q -p no:cacheprovider -m "scale or not scale" |
  Select-String '^tests/.*::' | ForEach-Object Line |
  Set-Content -Encoding utf8NoBOM docs\reports\data\2026-09-29-test-speed-baseline-ids.txt
(Get-Content docs\reports\data\2026-09-29-test-speed-baseline-ids.txt).Count
```

`-m "scale or not scale"` names `scale`, so the conftest keeps the scale profiles (`tests/conftest.py:81-88`) and the expression keeps everything. Expected count: about 4,627 (at `a12d3aa`: 4,617 passed + 2 skipped + 1 deselected `scale` profile, plus the 7 analyzer tests); record the exact number.

- [ ] **Step 8: Pre-flight: the machine must be quiet**

```powershell
Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
  Where-Object { $_.CommandLine -match 'pytest|acceptance|rehearsal|benchmark|measure_test_memory' } |
  Select-Object ProcessId, ParentProcessId, @{n='MB';e={[math]::Round($_.WorkingSetSize/1MB)}}, CommandLine |
  Format-Table -AutoSize -Wrap
$os = Get-CimInstance Win32_OperatingSystem
'{0:N2} GB free of {1:N2} GB' -f ($os.FreePhysicalMemory/1MB), ($os.TotalVisibleMemorySize/1MB)
Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" | Measure-Object WorkingSetSize -Sum |
  ForEach-Object { '{0} python processes, {1:N0} MB' -f $_.Count, ($_.Sum/1MB) }
```

Expected: the first command prints nothing, and at least **4.00 GB** is free. The unfiltered count is for the report: MCP servers (backlog_server, blender, godot) are always running and are not suite work. If a pytest, acceptance, rehearsal or benchmark process is listed, or less than 4 GB is free, wait and re-check. Do not start a noisy baseline: every later number is compared against it.

- [ ] **Step 9: Launch the baseline detached**

```powershell
$tmp = & .\.venv\Scripts\python.exe -c "import tempfile; print(tempfile.gettempdir())"
New-Item -ItemType Directory -Force (Join-Path $tmp 'taskmaster-tests') | Out-Null
.\.venv\Scripts\python.exe scripts\run_detached_check.py --name speed-baseline -- "$PWD\.venv\Scripts\python.exe" -m pytest tests -n 3 -p no:cacheprovider --durations=0 --durations-min=0 --junitxml=test-results/speed-baseline/junit.xml "--basetemp=$(Join-Path $tmp 'taskmaster-tests\run-baseline')"
```

Expected: `Started PID <PID>; logs test-results/speed-baseline.{out,err}.log`. Note the PID and the start time.

Why these flags: `run_detached_check.py` keeps the run out of the shell reaper's reach. An explicit `--basetemp` makes pytest skip its numbered-dir rotation and inline delete of an old run (`_pytest/tmpdir.py:154-159, 325-333`), so neither pollutes the wall time, and it fixes the tree the footprint step walks. `--durations-min=0` keeps the sub-5 ms phases the default hides (5,429 of them in the 2026-09-29 profile), so the phase totals add up to worker-seconds. The dir lives under `taskmaster-tests/` and is named `run-*`, so Task 2's pruner reclaims its ~1.3 GB later. If this step is ever re-run, use a new `--name` and a new basetemp name (`run-baseline-2`): pytest deletes an existing given basetemp inline at start.

- [ ] **Step 10: Wait for it to finish**

Every few minutes:

```powershell
Get-Process -Id <PID> -ErrorAction SilentlyContinue
$os = Get-CimInstance Win32_OperatingSystem; '{0:N2} GB free' -f ($os.FreePhysicalMemory/1MB)
```

Note the lowest free-RAM value seen. When `Get-Process` prints nothing, the run is over:

```powershell
Get-Content test-results\speed-baseline.out.log -Tail 3
Get-Content test-results\speed-baseline.err.log -Tail 20
Get-Item test-results\speed-baseline.out.log | Select-Object CreationTime, LastWriteTime
```

Expected: the last line reads like `4624 passed, 2 skipped in 1500.00s (0:25:00)`, and the err log holds no traceback. On any failure, stop and report it; this task measures, it does not fix.

- [ ] **Step 11: Analyze, including the per-file temp footprint**

```powershell
.\.venv\Scripts\python.exe scripts\analyze_test_durations.py --junit test-results\speed-baseline\junit.xml --durations-log test-results\speed-baseline.out.log --top 25 --warn-over 5 --basetemp (Join-Path $tmp 'taskmaster-tests\run-baseline') |
  Tee-Object -FilePath test-results\speed-baseline\analysis.txt
```

Expected: the totals line; `session:` with the wall time; `phases:` with each phase's share of worker-seconds; top 25 files; top 25 tests; the histogram; every test over 5 s (`WARNING slow test …` lines, input to the §4.2 `release` list); then the temp footprint, the files over 10× the median and the unattributed fixture dirs. The footprint walk over ~61k files takes up to a minute.

- [ ] **Step 12: Write the baseline report**

Create `docs/reports/2026-09-29-test-speed-baseline.md`. Fill every `‹…›` from the named source; paste the `analysis.txt` blocks verbatim inside the `text` fences.

````markdown
<!-- User intent: fix the quiet-machine starting point for the test-speed work, so every later step
     (temp dirs, durability, templates, tiers) is judged against measured numbers, not the noisy first profile. -->

# Test-suite speed: baseline

Status: **measured** · Run date: ‹date of Step 9› · Commit: `‹git rev-parse --short HEAD›` on `feat/test-speed`
(branched from `feat/database-native-foundation` `7546e0a`) · Spec:
[`2026-09-29-test-suite-speed-design.md`](../specs/2026-09-29-test-suite-speed-design.md) §7

## How it was measured

- Command: `pytest tests -n 3 -p no:cacheprovider --durations=0 --durations-min=0 --junitxml=… --basetemp=<temp>/taskmaster-tests/run-baseline`,
  started detached through `scripts/run_detached_check.py`.
- The explicit `--basetemp` skips pytest's inline rotation of old basetemps (spec §3.1). Wall time is pytest's own session time.
- Machine: Windows 11, Python 3.12.9, pytest 9.1.1, pytest-xdist 3.8.0, ‹total› GB RAM, worktree venv.
- Quiet check at start: no pytest, acceptance, rehearsal or benchmark processes; ‹N› python processes (MCP servers) using
  ‹MB› MB; ‹X.XX› GB free. Lowest free RAM seen during the run: ‹Y.YY› GB.
- Collected IDs, all tiers (`-m "scale or not scale"`): ‹count›, in
  [`data/2026-09-29-test-speed-baseline-ids.txt`](data/2026-09-29-test-speed-baseline-ids.txt).

## Headline

| Measure | Baseline | 2026-09-29 profile (`402c17c`, machine shared with an N16 acceptance run) |
|---|---|---|
| Passed / skipped / failed | ‹from the `tests=` line› | 4,346 / 2 / 0 |
| Wall at `-n 3` | ‹from `session:`› | 1,804.7 s (30 min 04 s) |
| Worker-seconds | ‹› | 5,364.9 |
| Mean per test | ‹› | 1.234 s |
| Setup / call / teardown | ‹from `phases:`› | 2,161.5 s (40.3 %) / 3,020.6 s (56.3 %) / 176.3 s (3.3 %), sub-5 ms phases hidden |

## §3.4 decision input: teardown share

Teardown is ‹T› s, ‹P› % of worker-seconds. The rule (spec §3.4): at 5 % or more, add a poll-interval constructor
parameter that tests set to 0.02 s; below 5 %, drop §3.4. **Decision: ‹implement | drop›.**

## Where the time goes

### Top 25 files by worker-seconds

```text
‹block "Top 25 files by worker-seconds" from analysis.txt›
```

### Top 25 slowest tests

```text
‹block "Top 25 slowest tests"›
```

### Per-test time histogram

```text
‹block "Per-test time histogram"›
```

### Tests over 5 s (input to the §4.2 `release` list)

```text
‹the "N test(s) over 5s" line and every WARNING line›
```

## Temp-dir footprint (spec §3.1)

```text
‹from "Temp footprint under" to the end of analysis.txt›
```

Files writing more than 10× the median are follow-ups, not fixed in this work:

| Test file | MB | Files | Follow-up |
|---|---|---|---|
| ‹one row per file in the "Over 10x the median" block› | | | ‹what it writes, if obvious from the test› |

## Per-step measurements

Each later step appends one row: same command as above but without `--basetemp`, a quiet machine, and the analyzer's
numbers. "Same IDs" means the collect-only list equals the baseline file plus the step's new tests.

| Step | Commit | Passed / skipped | Worker-seconds | Wall `-n 3` | Same IDs | Notes |
|---|---|---|---|---|---|---|
| Baseline | `‹sha›` | ‹› | ‹› | ‹› | reference | quiet machine |

## Deviations

‹anything unusual: a re-run and why, a test that failed or was flaky, a noisy moment during the run; "None" otherwise›
````

- [ ] **Step 13: Commit the baseline**

```bash
git add docs/reports/2026-09-29-test-speed-baseline.md docs/reports/data/2026-09-29-test-speed-baseline-ids.txt
git commit -m "docs(tests): quiet-machine baseline for the test-speed work

Wall, worker-seconds, phase shares, heavy tail and per-file tmp_path
footprint at -n 3, plus every collected test ID as the reference for
the no-coverage-loss checks.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

---

### Task 2: Temp-dir hygiene

**Files:**
- Create: `tests/_temp_cleanup.py`
- Modify: `tests/conftest.py:16-20` (import), `tests/conftest.py:22-29` (new function before `pytest_configure`, one call inside it)
- Test: `tests/test_temp_cleanup.py`
- Modify: `docs/reports/2026-09-29-test-speed-baseline.md` (one `## Per-step measurements` row)

**Interfaces:**
- Consumes: `scripts/analyze_test_durations.py` (Task 1); the baseline IDs file and report table (Task 1).
- Produces:
  - `tests._temp_cleanup`: `ROOT_NAME = "taskmaster-tests"`, `RUN_NAME` (regex `^run-\d{8}-\d{6}-(\d+)$`), `temp_root() -> Path` (= `Path(tempfile.gettempdir()) / "taskmaster-tests"`), `run_dir_name(when=None, pid=None) -> str`, `pid_alive(pid: int) -> bool`, `select_stale(runs, keep, is_alive=pid_alive, keep_newest=2) -> list[str]`, `list_runs(root)`, `remove_tree(path)`, `launch_cleanup(root, keep) -> subprocess.Popen`, CLI `python tests/_temp_cleanup.py ROOT --keep NAME`.
  - Every pytest controller session without `--basetemp` runs in `<temp>/taskmaster-tests/run-<YYYYmmdd-HHMMSS>-<pid>/`; xdist workers in `popen-gwN/` beneath it.

How it fits the existing conftest, read at HEAD:

- **Order.** Initial conftests register before `pytest_configure` fires, and pluggy calls later-registered implementations first, so `tests/conftest.py`'s `pytest_configure` sets `config.option.basetemp` before `_pytest/tmpdir.py:244-254` builds `TempPathFactory` from it (confirmed: the factory did not exist yet when the conftest hook ran). `test_this_session_runs_in_its_own_run_dir` pins it.
- **Workers.** xdist's controller calls `self.config._tmp_path_factory.getbasetemp()` and sends `str(basetemp / "popen-gwN")` (`xdist/workermanage.py:337-341`); the worker sets it as `config.option.basetemp` (`xdist/remote.py:392-400`) before its own `pytest_configure`, and `config.workerinput` exists there, so the conftest returns early on workers.
- **Given basetemp.** pytest creates it with `mkdir` and no parents (`_pytest/tmpdir.py:154-159`), so the root is created first. A given basetemp gets no numbered-dir rotation and no end-of-session delete (`_pytest/tmpdir.py:325-333` requires `_given_basetemp is None`). If a given basetemp already exists, pytest still deletes it inline at start; that is pytest's contract and unchanged.
- **Child-process guard.** The pruner is launched from `pytest_configure`, outside any test. `_WATCH.spawned` is `None` there (`tests/conftest.py:117`; set to a list only inside `_child_process_guard`, `:214`), so the Popen wrapper does not record it (`:185-187`), and it can never be a "leftover Python child" failure. It is not a coordinator service, and `_WATCH.allow_service` is `True` outside tests anyway (`:115`). It is also launched before `_windowless_test_children()` installs the wrapper (`:65`).
- **Windows flags.** `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`, as `scripts/run_detached_check.py:25-26` uses: its docstring notes that a console-less (`DETACHED_PROCESS`) parent can make console children allocate visible windows. All three std handles are `DEVNULL`, so a caller capturing pytest's output (the runner, a shell) never waits on the pruner.
- **Concurrent suites.** Other worktrees may be mid-run under the same root. The run-dir name carries the controller pid, and the pruner skips any run whose pid is alive.
- **Old `pytest-of-<user>` dirs** under `%TEMP%` are no longer rotated by this branch's sessions. Other worktrees on older branches still use and rotate them, so this task leaves them alone.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_temp_cleanup.py`:

```python
# User intent: the basetemp pruner deletes directories in the user's temp dir while other suites may
# be running; which runs it deletes, and that its standalone entry point works, are pinned here.
from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from tests import _temp_cleanup as cleanup


def _finished_pid() -> int:
    done = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True)
    return int(done.stdout)


def test_temp_root_is_under_the_system_temp_dir():
    assert cleanup.temp_root() == Path(tempfile.gettempdir()) / "taskmaster-tests"


def test_run_dir_name_is_timestamped_and_pid_suffixed():
    when = time.mktime((2026, 9, 29, 13, 5, 9, 0, 0, -1))
    assert cleanup.run_dir_name(when=when, pid=4242) == "run-20260929-130509-4242"
    assert cleanup.RUN_NAME.match(cleanup.run_dir_name()).group(1) == str(os.getpid())


def test_select_stale_keeps_the_current_run_the_newest_two_and_live_sessions():
    runs = [
        ("run-20260101-000001-11", 1.0),
        ("run-20260101-000002-12", 2.0),
        ("run-20260101-000003-13", 3.0),
        ("run-20260101-000004-14", 4.0),
        ("run-20260101-000005-15", 0.1),
        ("run-baseline", 0.5),
        ("pytest-of-someone", 0.2),
    ]
    stale = cleanup.select_stale(runs, keep="run-20260101-000005-15", is_alive=lambda pid: pid == 11)
    assert stale == ["run-20260101-000002-12", "run-baseline"]


def test_select_stale_deletes_nothing_while_three_or_fewer_runs_exist():
    runs = [("run-20260101-000001-1", 1.0), ("run-20260101-000002-2", 2.0), ("run-20260101-000003-3", 3.0)]
    assert cleanup.select_stale(runs, keep="run-20260101-000003-3", is_alive=lambda pid: False) == []


def test_pid_alive_tells_this_process_from_a_finished_one():
    assert cleanup.pid_alive(os.getpid())
    assert not cleanup.pid_alive(_finished_pid())
    assert not cleanup.pid_alive(0)


def _make_run(root: Path, name: str, mtime: float, read_only: bool = False) -> None:
    leaf = root / name / "popen-gw0" / "test_x0"
    leaf.mkdir(parents=True)
    data = leaf / "data.txt"
    data.write_text("x", encoding="utf-8")
    if read_only:
        os.chmod(data, stat.S_IREAD)
    os.utime(root / name, (mtime, mtime))


def test_the_pruner_script_deletes_only_stale_runs(tmp_path):
    dead, now = _finished_pid(), time.time()
    root = tmp_path / "taskmaster-tests"
    _make_run(root, f"run-20260101-000001-{dead}", now - 500, read_only=True)  # stale
    _make_run(root, f"run-20260101-000002-{os.getpid()}", now - 400)  # stale by age, but its session is alive
    _make_run(root, f"run-20260101-000003-{dead}", now - 300)  # newest two others
    _make_run(root, f"run-20260101-000004-{dead}", now - 200)
    _make_run(root, f"run-20260101-000005-{dead}", now - 900)  # the starting session's own dir
    (root / "not-a-run").mkdir()
    subprocess.run([sys.executable, str(Path(cleanup.__file__).resolve()), str(root),
                    "--keep", f"run-20260101-000005-{dead}"], check=True, timeout=120)
    assert sorted(path.name for path in root.iterdir()) == [
        "not-a-run",
        f"run-20260101-000002-{os.getpid()}",
        f"run-20260101-000003-{dead}",
        f"run-20260101-000004-{dead}",
        f"run-20260101-000005-{dead}",
    ]


def test_this_session_runs_in_its_own_run_dir(tmp_path_factory, request):
    if any(str(arg).startswith("--basetemp") for arg in request.config.invocation_params.args):
        pytest.skip("an explicit --basetemp is used as given")
    base = tmp_path_factory.getbasetemp()
    run = base.parent if base.name.startswith("popen-gw") else base
    assert run.parent == cleanup.temp_root().resolve()
    assert cleanup.RUN_NAME.match(run.name)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_temp_cleanup.py -q -p no:cacheprovider`
Expected: collection error, `ImportError: cannot import name '_temp_cleanup' from 'tests'`.

- [ ] **Step 3: Implement the pruner module**

Create `tests/_temp_cleanup.py`:

```python
# User intent: a full test run leaves ~1.3 GB / 61k temp files, and pytest deleted them inline in a
# later session (229 s inside a one-test run); each session gets its own basetemp and old ones are
# pruned out of band, so no pytest session ever waits on deletion.
"""Per-session pytest basetemps under one root, pruned by a detached process (spec §3.1).

`tests/conftest.py` names each controller session's basetemp `<temp>/taskmaster-tests/run-<stamp>-<pid>`
and starts `python tests/_temp_cleanup.py <root> --keep <that name>`. The pruner deletes the other
`run-*` dirs except the newest two and any whose session pid is still alive, so a suite running in
another worktree keeps its files. Stdlib only: it runs detached, outside pytest, and must not
import the suite or taskmaster.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from pathlib import Path

ROOT_NAME = "taskmaster-tests"
RUN_PREFIX = "run-"
KEEP_NEWEST = 2
RUN_NAME = re.compile(r"^run-\d{8}-\d{6}-(\d+)$")


def temp_root() -> Path:
    """Where every session's basetemp lives."""
    return Path(tempfile.gettempdir()) / ROOT_NAME


def run_dir_name(when: float | None = None, pid: int | None = None) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(when))
    return f"{RUN_PREFIX}{stamp}-{os.getpid() if pid is None else pid}"


def pid_alive(pid: int) -> bool:
    """Whether `pid` is running; a process that cannot be inspected counts as alive.

    Mirrors `taskmaster.store._local_pid_alive` (store.py:829-869), which a detached stdlib-only
    script cannot import. On Windows `os.kill(pid, 0)` terminates the process, so it waits on a
    SYNCHRONIZE handle instead; that also tells a live process from one that exited with 259.
    """
    if not 0 < pid <= 0xFFFFFFFF:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except PermissionError:
            return True
        except OSError:
            return False
        return True
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: no such pid
    try:
        return kernel.WaitForSingleObject(handle, 0) != 0  # only WAIT_OBJECT_0 proves exit
    finally:
        kernel.CloseHandle(handle)


def select_stale(runs: Iterable[tuple[str, float]], keep: str, is_alive: Callable[[int], bool] = pid_alive,
                 keep_newest: int = KEEP_NEWEST) -> list[str]:
    """Run dirs to delete, newest first: not `keep`, not the newest `keep_newest` others, not live."""
    others = sorted(((name, mtime) for name, mtime in runs if name.startswith(RUN_PREFIX) and name != keep),
                    key=lambda run: run[1], reverse=True)
    stale = []
    for name, _ in others[keep_newest:]:
        match = RUN_NAME.match(name)
        if match and is_alive(int(match.group(1))):
            continue  # another session, e.g. a suite in a second worktree, is still using it
        stale.append(name)
    return stale


def list_runs(root: Path) -> list[tuple[str, float]]:
    try:
        entries = list(os.scandir(root))
    except OSError:
        return []
    runs = []
    for entry in entries:
        try:
            if entry.name.startswith(RUN_PREFIX) and entry.is_dir(follow_symlinks=False):
                runs.append((entry.name, entry.stat(follow_symlinks=False).st_mtime))
        except OSError:
            continue
    return runs


def remove_tree(path: Path) -> None:
    """Delete what can be deleted: read-only entries are made writable, locked ones are left."""
    def retry_writable(function, target, _error):
        try:
            os.chmod(target, stat.S_IWRITE)
            function(target)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry_writable)
    else:
        shutil.rmtree(path, onerror=retry_writable)


def launch_cleanup(root: Path, keep: str) -> subprocess.Popen:
    """Start the pruner windowless in its own process group; nothing waits for it.

    The flags match scripts/run_detached_check.py: CREATE_NO_WINDOW rather than DETACHED_PROCESS,
    since a console-less parent can make console grandchildren allocate visible windows. All three
    standard handles are DEVNULL so a caller capturing pytest's output never waits on this child.
    """
    kwargs = ({"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
              if os.name == "nt" else {"start_new_session": True})
    return subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(root), "--keep", keep],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            **kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Delete old taskmaster pytest basetemps.")
    parser.add_argument("root", type=Path)
    parser.add_argument("--keep", default="", help="the starting session's run dir name")
    args = parser.parse_args(argv)
    for name in select_stale(list_runs(args.root), args.keep):
        remove_tree(args.root / name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run: six pass, the session check still fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_temp_cleanup.py -q -p no:cacheprovider`
Expected: `1 failed, 6 passed`; the failure is `test_this_session_runs_in_its_own_run_dir`, with the basetemp still under `pytest-of-<user>`.

- [ ] **Step 5: Wire it into the conftest**

`tests/conftest.py:16-20` before:

```python
# Make `import backlog_server` and `from taskmaster_v3 import ...` work
# exactly the same way the existing hermetic tests do.
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))
```

after:

```python
# Make `import backlog_server` and `from taskmaster_v3 import ...` work
# exactly the same way the existing hermetic tests do.
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from tests._temp_cleanup import launch_cleanup, run_dir_name, temp_root  # noqa: E402 - needs PLUGIN_ROOT on sys.path
```

`tests/conftest.py:22-29` before:

```python
# Every twins activation runs the full N15 carry-over oracle (snapshot + verify_carryover)
# through the production activation core. Measured 2026-09-24 on the 20-file twins sample:
# 258 s off vs 264 s on (+2.3%). Opt out with TASKMASTER_TWINS_VERIFY=0.
os.environ.setdefault("TASKMASTER_TWINS_VERIFY", "1")


def pytest_configure(config):
    """Register custom markers (avoids PytestUnknownMarkWarning)."""
```

after:

```python
# Every twins activation runs the full N15 carry-over oracle (snapshot + verify_carryover)
# through the production activation core. Measured 2026-09-24 on the 20-file twins sample:
# 258 s off vs 264 s on (+2.3%). Opt out with TASKMASTER_TWINS_VERIFY=0.
os.environ.setdefault("TASKMASTER_TWINS_VERIFY", "1")


def _assign_session_basetemp(config) -> None:
    """Give this session its own basetemp and prune old ones out of band (spec §3.1).

    With no `--basetemp`, pytest rotates numbered basetemps and deletes the oldest inline at
    session end: 229 s inside a one-test session after a full run. A given basetemp gets
    neither (_pytest/tmpdir.py:154-159, 325-333). This hook runs before the builtin tmpdir
    plugin's `pytest_configure` reads the option (tests/test_temp_cleanup.py pins that). Only
    the controller decides; xdist hands each worker `<basetemp>/popen-gwN`
    (xdist/workermanage.py:337-341, xdist/remote.py:392-400). The pruner starts outside any
    test, so the child-process guard neither records nor judges it.
    """
    if hasattr(config, "workerinput"):
        return
    root = temp_root()
    root.mkdir(parents=True, exist_ok=True)  # pytest creates a given basetemp without parents
    if config.option.basetemp is None:
        config.option.basetemp = str(root / run_dir_name())
    # Held on the config so Popen.__del__ never warns about a still-running child mid-session.
    config._taskmaster_temp_cleanup = launch_cleanup(root, keep=Path(config.option.basetemp).name)


def pytest_configure(config):
    """Register custom markers (avoids PytestUnknownMarkWarning)."""
    _assign_session_basetemp(config)
```

(The rest of `pytest_configure`, from `config.addinivalue_line(` at the old line 30 on, is unchanged.)

- [ ] **Step 6: Run to verify they pass, serially and under xdist; regressions**

Run: `.venv/Scripts/python.exe -m pytest tests/test_temp_cleanup.py -q -p no:cacheprovider`
Expected: `7 passed`.

Run: `.venv/Scripts/python.exe -m pytest tests/test_temp_cleanup.py -q -p no:cacheprovider -n 1`
Expected: `7 passed` (the session check sees `…/run-…/popen-gw0`).

Run: `.venv/Scripts/python.exe -m pytest tests/test_child_process_guard.py tests/test_hooks_json.py -q -p no:cacheprovider`
Expected: all pass; the guard still refuses an unmarked service launch and allows ordinary Python children.

Check the root (PowerShell):

```powershell
$root = & .\.venv\Scripts\python.exe -c "from tests._temp_cleanup import temp_root; print(temp_root())"
Get-ChildItem $root -Directory | Sort-Object LastWriteTime | Format-Table Name, LastWriteTime
```

Expected: `run-baseline` (Task 1) and `run-<stamp>-<pid>` dirs; after these three sessions, at most the current one plus the newest two others remain besides any whose pid is alive. `run-baseline` is the oldest, so it is being deleted in the background (about 4 minutes for its ~1.3 GB).

- [ ] **Step 7: Commit**

```bash
git add tests/_temp_cleanup.py tests/test_temp_cleanup.py tests/conftest.py
git commit -m "feat(tests): per-session basetemp with detached pruning of old runs

Each controller session gets <temp>/taskmaster-tests/run-<stamp>-<pid>;
a windowless pruner deletes older runs except the newest two and any
whose session is still alive. No pytest session deletes old runs inline.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

- [ ] **Step 8: Re-measure (spec §7) and check acceptance**

Run the quiet-machine pre-flight first (the three commands of Task 1 Step 8: no pytest/acceptance/rehearsal/benchmark process, at least 4 GB free). Then:

```powershell
.\.venv\Scripts\python.exe scripts\run_detached_check.py --name speed-t2 -- "$PWD\.venv\Scripts\python.exe" -m pytest tests -n 3 -p no:cacheprovider --durations=0 --durations-min=0 --junitxml=test-results/speed-t2/junit.xml
```

Wait as in Task 1 Step 10 (`Get-Process -Id <PID>`). **The moment it finishes**, time a one-test session:

```powershell
(Measure-Command { & .\.venv\Scripts\python.exe -m pytest "tests/test_temp_cleanup.py::test_run_dir_name_is_timestamped_and_pid_suffixed" -q -p no:cacheprovider | Out-Host }).TotalSeconds
```

Expected: **< 10** (acceptance, spec §3.1). Before this change the same session could spend 229 s deleting an old basetemp inline.

Then compare with the baseline:

```powershell
.\.venv\Scripts\python.exe scripts\analyze_test_durations.py --junit test-results\speed-t2\junit.xml --durations-log test-results\speed-t2.out.log --top 10 |
  Tee-Object -FilePath test-results\speed-t2\analysis.txt
.\.venv\Scripts\python.exe -m pytest tests --collect-only -q -p no:cacheprovider -m "scale or not scale" |
  Select-String '^tests/.*::' | ForEach-Object Line | Set-Content -Encoding utf8NoBOM test-results\speed-t2\ids.txt
Compare-Object (Get-Content docs\reports\data\2026-09-29-test-speed-baseline-ids.txt) (Get-Content test-results\speed-t2\ids.txt) | Format-Table -AutoSize
Get-ChildItem $root -Directory | Sort-Object LastWriteTime | Format-Table Name, LastWriteTime
```

Expected:
- `Compare-Object` prints only `=>` rows, exactly the 7 `tests/test_temp_cleanup.py::…` IDs.
- Passed = baseline passed + 7; skipped equal to the baseline; failed 0. A failure mentioning a path longer than 260 characters means the longer basetemp prefix hit a limit: stop and report it.
- A few minutes later the root holds the one-test session's dir, the `speed-t2` run and one other; `run-baseline` is gone.

- [ ] **Step 9: Record and commit**

Append to the `## Per-step measurements` table in `docs/reports/2026-09-29-test-speed-baseline.md`:

```markdown
| §3.1 temp dirs | `‹git rev-parse --short HEAD›` | ‹passed› / ‹skipped› | ‹worker-seconds› | ‹wall from `session:`› | yes (+7 `test_temp_cleanup`) | one-test session right after the full run: ‹seconds› s |
```

```bash
git add docs/reports/2026-09-29-test-speed-baseline.md
git commit -m "docs(tests): record temp-dir hygiene measurements

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

---

### Task 3: Test-mode durability switch

Spec §3.2. A new module, `taskmaster/durability.py`, owns every fsync and every SQLite `synchronous` level in the package. The switch is ON in every fresh interpreter. The only way to turn it off is an in-process `set_enabled(False)`, and only `tests/conftest.py` calls it, for unmarked tests. There is no environment variable, CLI flag or config key.

The switch works at three kinds of site:
1. **fsync calls** go through `durability.fsync`.
2. **The 4 explicit `PRAGMA synchronous=FULL` sites** run `durability.synchronous_pragma()`. They must go through the switch too, or a later explicit FULL would undo `relax` on the same connection.
3. **Connections that rely on SQLite's default level get `durability.relax(connection)`.** SQLite 3.47.1 in `.venv` is compiled with `DEFAULT_SYNCHRONOUS=2` and `DEFAULT_WAL_SYNCHRONOUS=2`, so the default is FULL. While the switch is ON, `relax` returns before touching the connection, so production connections are unchanged.

The task is split into two commits, and a reviewer can accept or reject each one:
- **Part A (Steps 1–7): production routing that changes no behaviour.** The switch is ON by default, so production and every test behave exactly as today.
- **Part B (Steps 8–14): the test-suite switch.** An autouse fixture turns durability off for unmarked tests, and the tests that observe durability get the `durability` marker.

**Line numbers** are from HEAD `a12d3aa`, before any edit. Inserting imports shifts the lines below them, so apply each edit by matching its text. Every site is listed below, and Step 4's failing scans print the same lists.

**Files:**
- Create: `taskmaster/durability.py`
- Create: `tests/test_durability.py`
- Modify: `taskmaster/store.py:37,1003-1004,1376,1927,1991,2008,2208-2209,2741,2765,5455,6045,6816`
- Modify: `taskmaster/native/commands.py:9-11,285`
- Modify: `taskmaster/native/cutover.py:44,143-146,481,487,534-535`
- Modify: `taskmaster/native/projection.py:31,661,1079,1174`
- Modify: `taskmaster/native_routing/progress.py:41,245`
- Modify: `taskmaster/coordinator/checkouts.py:23,363`
- Modify: `taskmaster/coordinator/git.py:26,275`
- Modify: `taskmaster/coordinator/ownership.py:9-10,197`
- Modify: `taskmaster/coordinator/protocol.py:7,59-66`
- Modify: `tests/conftest.py:46-49` (register the marker) and `tests/conftest.py:237-240` (new autouse fixture between `_child_process_guard` and `_store_isolation`)
- Modify: `tests/test_store_schema.py:209`, `tests/test_native_projection_drain.py:57` and `tests/test_store_recovery.py:615` (add the marker)
- Test: `tests/test_durability.py`

**Interfaces:**
- Consumes: none. This task is independent of the §3.1 temp-dir task and the §3.3 template task. It edits `pytest_configure` in `tests/conftest.py`, which other tasks may also edit to register markers (for example `release`), so conflicts there are textual only.
- Produces:
  - `taskmaster.durability.enabled() -> bool`
  - `taskmaster.durability.set_enabled(value: bool) -> None`: tests only; raises `TypeError` for a non-bool.
  - `taskmaster.durability.fsync(fd: int) -> None`: `os.fsync(fd)` while enabled, a no-op while disabled.
  - `taskmaster.durability.synchronous_pragma() -> str`: `"PRAGMA synchronous=FULL"` while enabled, `"PRAGMA synchronous=OFF"` while disabled.
  - `taskmaster.durability.relax(connection: sqlite3.Connection) -> None`: does nothing while enabled. While disabled it:
    - raises `RuntimeError` if the connection is inside a transaction (misuse);
    - otherwise runs `PRAGMA synchronous=OFF`, ignoring any `sqlite3.Error`.
  - The pytest marker `durability`, registered in `tests/conftest.py::pytest_configure`. The `tests/README.md` task (spec §4.3) documents it.
  - The autouse fixture `tests/conftest.py::_durability_switch`.

#### Inventory 1: fsync and explicit synchronous sites (verified by grep at `a12d3aa`)

There are **15 `os.fsync` calls and 4 `PRAGMA synchronous=FULL` executions, for 19 edits in total.**

Nothing else needs routing:
- `taskmaster/` and `backlog_server.py` contain no `os.fdatasync`, no `from os import fsync`, no `FlushFileBuffers` via ctypes, and no `PRAGMA fullfsync` or `checkpoint_fullfsync`.
- No site sets `PRAGMA synchronous` to anything other than FULL.
- No site combines this pragma with other pragmas in one string.
- No production code reads `PRAGMA synchronous` back. The only reader is `tests/test_store_schema.py:228`.
- `backlog_server.py` at the repo root is an import shim with no sites.

| # | Site (HEAD line) | Current code | Replacement |
|---|---|---|---|
| 1 | `taskmaster/store.py:1376` (`Store.connection`) | `connection.execute("PRAGMA synchronous=FULL")` | `connection.execute(durability.synchronous_pragma())` |
| 2 | `taskmaster/store.py:1927` (id-reservation sidecar) | `os.fsync(handle.fileno())` | `durability.fsync(handle.fileno())` |
| 3 | `taskmaster/store.py:1991` (export-intent recovery restore) | `os.fsync(handle.fileno())` | `durability.fsync(handle.fileno())` |
| 4 | `taskmaster/store.py:2008` (`_persist_export_intent`) | `os.fsync(handle.fileno())` | `durability.fsync(handle.fileno())` |
| 5 | `taskmaster/store.py:2741` (config read-modify-write) | `os.fsync(handle.fileno())` | `durability.fsync(handle.fileno())` |
| 6 | `taskmaster/store.py:2765` (`write_local_cache`) | `os.fsync(handle.fileno())` | `durability.fsync(handle.fileno())` |
| 7 | `taskmaster/store.py:5455` (PROGRESS.md export) | `os.fsync(handle.fileno())` | `durability.fsync(handle.fileno())` |
| 8 | `taskmaster/store.py:6045` (projection export temp) | `os.fsync(handle.fileno())` | `durability.fsync(handle.fileno())` |
| 9 | `taskmaster/store.py:6816` (`_restore_replaced`) | `os.fsync(handle.fileno())` | `durability.fsync(handle.fileno())` |
| 10 | `taskmaster/native/commands.py:285` (`execute`) | `connection.execute("PRAGMA synchronous=FULL")` | `connection.execute(durability.synchronous_pragma())` |
| 11 | `taskmaster/native/cutover.py:481` (`_fsync`, file) | `os.fsync(stream.fileno())` | `durability.fsync(stream.fileno())` |
| 12 | `taskmaster/native/cutover.py:487` (`_fsync`, directory descriptor) | `os.fsync(descriptor)` | `durability.fsync(descriptor)` |
| 13 | `taskmaster/native/projection.py:661` (`_write_temp`) | `os.fsync(handle.fileno())` | `durability.fsync(handle.fileno())` |
| 14 | `taskmaster/native/projection.py:1079` (restore/install temp) | `os.fsync(handle.fileno())` | `durability.fsync(handle.fileno())` |
| 15 | `taskmaster/native/projection.py:1174` (config write temp) | `os.fsync(handle.fileno())` | `durability.fsync(handle.fileno())` |
| 16 | `taskmaster/native_routing/progress.py:245` (`render`) | `os.fsync(handle.fileno())` | `durability.fsync(handle.fileno())` |
| 17 | `taskmaster/coordinator/checkouts.py:363` (`write`) | `connection.execute('PRAGMA synchronous=FULL')` | `connection.execute(durability.synchronous_pragma())` |
| 18 | `taskmaster/coordinator/git.py:275` (`write_state`) | `connection.execute('PRAGMA synchronous=FULL')` | `connection.execute(durability.synchronous_pragma())` |
| 19 | `taskmaster/coordinator/ownership.py:197` (`publish`) | `os.fsync(stream.fileno())` | `durability.fsync(stream.fileno())` |

**Decision on `cutover._fsync(path)` (`cutover.py:478-491`):** keep the helper and change only its two inner calls (rows 11–12). Do not add an early return when durability is off. That keeps its `open(path, "rb+")` and directory `os.open` running, along with any error they raise today, and opening a file costs little next to an fsync. Its four callers (`cutover.py:537,547,559,1279`) and `scripts/native_n16_perf.py:136` stay as they are and go through the switch automatically. The docstrings at `checkouts.py:361` and `git.py:273` say "(synchronous=FULL)", which is still true in production, so leave them. The scan matches `PRAGMA synchronous`, not the bare word.

#### Inventory 2: every `sqlite3.connect` site (verified by grep and AST at `a12d3aa`)

There is **no single connect factory**. The three shared helpers are `coordinator/protocol.py::connect` (every coordinator service connection, via `service.py:121`), `native/cutover.py::_connect` (every cutover transaction) and `store.py::Store.connection` (the legacy store's registry connection). The other sites are ad hoc.

There are **16 sites in total: 5 get `relax`, 1 already routes its explicit pragma, and 10 never commit**, so they have nothing to sync.

| # | Site (HEAD line) | Enclosing function | Mode | Coverage |
|---|---|---|---|---|
| 1 | `taskmaster/coordinator/protocol.py:62` | `connect` | rw or ro | **relax** (rw branch). Shared helper for all coordinator service connections. |
| 2 | `taskmaster/native/cutover.py:144` | `_connect` | rw | **relax**. Shared helper for all cutover transactions. |
| 3 | `taskmaster/native/cutover.py:534` | `write_backup` (backup destination) | rw, new file | **relax** on `destination` before `reader.backup(...)`. |
| 4 | `taskmaster/store.py:995` | `checkpoint_all` | rw | **relax** after `assert_compatible`, before `wal_checkpoint(TRUNCATE)`. |
| 5 | `taskmaster/store.py:2200` | `Store._try_register_session` (session activity) | rw | **relax** before `BEGIN IMMEDIATE`. |
| 6 | `taskmaster/store.py:1356` | `Store.connection` (registry) | rw, or ro on network | Already covered: `synchronous_pragma()` (inventory 1, row 1). |
| 7 | `taskmaster/coordinator/git_hook.py:104` | `check` | `mode=ro` | Never commits |
| 8 | `taskmaster/integrity.py:25` | `check_database` | `mode=ro` | Never commits |
| 9 | `taskmaster/native/compatibility.py:22` | `query` | `:memory:` | Never commits (nothing on disk) |
| 10 | `taskmaster/native/cutover.py:150` | `_connect_readonly` | `mode=ro` | Never commits |
| 11 | `taskmaster/native/cutover.py:1320` | `_legacy_objects` | `:memory:` | Never commits (nothing on disk) |
| 12 | `taskmaster/native/quiesce.py:99` | `open_writers` | rw, zero-timeout lock probe | Never commits: only `BEGIN`/`ROLLBACK`. Left alone because the probe's timing is deliberate. |
| 13 | `taskmaster/native_routing/gate.py:38` | `_runtime_error` | `:memory:` | Never commits (nothing on disk) |
| 14 | `taskmaster/native_routing/gate.py:70` | `native_database` | `mode=ro` | Never commits |
| 15 | `taskmaster/store.py:3887` | `Store.read_only_status` | `mode=ro` | Never commits |
| 16 | `taskmaster/store.py:3945` | `Store._busy_diagnostic` | rw, reads only | Never commits: two `SELECT`s |

The database is never `ATTACH`ed anywhere, so the unqualified pragma, which applies to `main`, covers the whole connection.

**Why `relax` swallows errors and refuses transactions.** Both behaviours were measured in drafting:
- `PRAGMA synchronous=OFF` reads the schema first. On a file that is not a database it raises `DatabaseError: file is not a database`, while `PRAGMA busy_timeout` does not.
- Inside a transaction it raises `Safety level may not be changed inside a transaction`.

Swallowing `sqlite3.Error` means that on a corrupt, busy or odd file the connection keeps its default, and its next statement raises exactly what it raises today. The corruption and recovery tests therefore take the same paths. Raising `RuntimeError` on `in_transaction` turns a misplaced call into a loud test failure instead of a silent no-op. Neither check runs while durability is ON.

A backup written to a relaxed destination was byte-identical to one written without the relax (drafting experiment).

**Tests that observe durability and get `@pytest.mark.durability`.** Spec §3.2 names two. This task finds a third.
- `tests/test_store_schema.py:209`: asserts `PRAGMA synchronous == 2` at line 228.
- `tests/test_native_projection_drain.py:57`: named by the spec. See Note 3.
- **`tests/test_store_recovery.py:615`** (`test_truncate_checkpoint_failure_is_best_effort`, new): pins the exact statement list that `checkpoint_all` runs, `["PRAGMA busy_timeout=0", "SELECT 1 FROM sqlite_schema …", "PRAGMA wal_checkpoint(TRUNCATE)", "close"]` (lines 641–646), on a fake connection. When unmarked, `relax` would add `PRAGMA synchronous=OFF` to that list. The marker makes it pin the production sequence, which is what it is for.

The other tests that patch `sqlite3.connect` were checked and are unaffected:
- `tests/test_store_read_path.py:500,517` and `tests/test_store_recovery.py:506,573` go through `_busy_diagnostic`, which is exempt.
- `tests/test_store_root.py:333` forbids connecting at all.
- `tests/test_store_schema.py:222,295,342,367` count connections, not statements.
- `tests/test_native_cutover.py:460` replaces `cutover._connect` with a plain connect, which keeps FULL and is only slower.
- The `set_trace_callback` tests (`test_native_commands.py`, `test_native_lifecycle.py`, `test_native_queries.py`, `test_native_relations.py`, `test_native_graph_repair.py`, `test_native_dashboard_reads.py`, `test_backlog_search_fts.py`) trace connections they own. None of them asserts on a synchronous statement.

---

- [ ] **Step 0: Record the "before" numbers (unmodified worktree)**

These are the baseline for Step 13. First confirm that no other pytest run is using the machine. This PowerShell command must print `0`:

```powershell
(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object CommandLine -match 'pytest').Count
```

Then run:

```bash
mkdir -p test-results/task3-durability
.venv/Scripts/python.exe -m pytest tests --collect-only -q -p no:cacheprovider > test-results/task3-durability/collect-before.txt
.venv/Scripts/python.exe -m pytest tests/test_native_projection.py -q -p no:cacheprovider
.venv/Scripts/python.exe -m pytest tests/test_native_projection.py -q -p no:cacheprovider
.venv/Scripts/python.exe -m pytest tests/test_native_projection.py -q -p no:cacheprovider
```

Record the three `N passed in X s` lines and their median. This is variant **A (all durability on)**. `test-results/` is gitignored.

For reference, drafting measured this file at `a12d3aa` on a shared machine:
- 40–48 s per run, with about ±20% noise.
- 680 in-process `os.fsync` calls totalling 2.8 s.
- 2,147 SQLite commits. 1,818 ran on connections with an explicit `synchronous=FULL`. The remaining 329 ran on the default level: 224 from `store.py:2200` and the rest from test-owned connections.

## Part A: route every durability site through one module (no behaviour change)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_durability.py`:

```python
# User intent: tests must never pay real fsync/SQLite-sync costs, but production must never be able
# to turn durability off: the switch is in-process only, on by default, and every site uses it.
"""The test-mode durability switch (`taskmaster.durability`, spec §3.2)."""
from __future__ import annotations

import ast
import collections
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from taskmaster import durability

REPO = Path(__file__).resolve().parents[1]
DURABILITY_FILE = REPO / "taskmaster" / "durability.py"
# Any of these outside `taskmaster/durability.py` would sync (or switch) behind the switch's back.
_BYPASS = re.compile(
    r"\bos\.(?:fsync|fdatasync)\b"
    r"|\bfrom\s+os\s+import\b[^#]*\b(?:fsync|fdatasync)\b"
    r"|\bfrom\s+sqlite3\s+import\b[^#]*\bconnect\b"
    r"|\bFlushFileBuffers\b"
    r"|\bPRAGMA\s+(?:synchronous|fullfsync|checkpoint_fullfsync)\b"
    r"|\bset_enabled\(",
    re.IGNORECASE,
)
# `sqlite3.connect` sites that never commit, so SQLite never syncs on them: read-only URIs,
# in-memory databases, a read-only diagnostic and a lock probe. Every other connect site must
# sit in a function that calls `durability.relax` (or runs `durability.synchronous_pragma()`).
_NEVER_COMMITS = {
    ("taskmaster/coordinator/git_hook.py", "check"): "mode=ro",
    ("taskmaster/integrity.py", "check_database"): "mode=ro",
    ("taskmaster/native/compatibility.py", "query"): ":memory:",
    ("taskmaster/native/cutover.py", "_connect_readonly"): "mode=ro",
    ("taskmaster/native/cutover.py", "_legacy_objects"): ":memory:",
    ("taskmaster/native/quiesce.py", "open_writers"): "zero-timeout BEGIN/ROLLBACK lock probe",
    ("taskmaster/native_routing/gate.py", "_runtime_error"): ":memory:",
    ("taskmaster/native_routing/gate.py", "native_database"): "mode=ro",
    ("taskmaster/store.py", "read_only_status"): "mode=ro",
    ("taskmaster/store.py", "_busy_diagnostic"): "two SELECTs, never writes",
}


def _bypasses(text: str) -> list[int]:
    return [number for number, line in enumerate(text.splitlines(), 1) if _BYPASS.search(line)]


def _production_sources():
    yield REPO / "backlog_server.py"
    yield from sorted((REPO / "taskmaster").rglob("*.py"))


def _connect_references(node, rel, function):
    """(file, line, innermost enclosing def or None) for every `sqlite3.connect` reference."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield from _connect_references(child, rel, child)
            continue
        if (isinstance(child, ast.Attribute) and child.attr == "connect"
                and isinstance(child.value, ast.Name) and child.value.id == "sqlite3"):
            yield rel, child.lineno, function
        yield from _connect_references(child, rel, function)


def _routes_through_durability(function) -> bool:
    return function is not None and any(
        isinstance(node, ast.Attribute) and node.attr in ("relax", "synchronous_pragma")
        and isinstance(node.value, ast.Name) and node.value.id == "durability"
        for node in ast.walk(function)
    )


def _unrouted_connects(sites) -> tuple[list[str], list[str]]:
    uncovered = collections.defaultdict(list)
    for rel, line, function in sites:
        if not _routes_through_durability(function):
            uncovered[(rel, function.name if function else "<module>")].append(line)
    unlisted = sorted(f"{rel}:{','.join(map(str, lines))} in {name}"
                      for (rel, name), lines in uncovered.items()
                      if (rel, name) not in _NEVER_COMMITS or len(lines) > 1)
    stale = sorted(f"{rel} {name}" for rel, name in _NEVER_COMMITS if (rel, name) not in uncovered)
    return unlisted, stale


@pytest.fixture
def restore_switch():
    before = durability.enabled()
    yield
    durability.set_enabled(before)


def test_fsync_calls_os_fsync_only_while_enabled(monkeypatch, restore_switch):
    calls = []
    monkeypatch.setattr(os, "fsync", calls.append)
    durability.set_enabled(True)
    durability.fsync(7)
    durability.set_enabled(False)
    durability.fsync(8)
    assert calls == [7]


def test_synchronous_pragma_is_full_while_enabled_and_off_while_disabled(restore_switch):
    durability.set_enabled(True)
    assert durability.synchronous_pragma() == "PRAGMA synchronous=FULL"
    durability.set_enabled(False)
    assert durability.synchronous_pragma() == "PRAGMA synchronous=OFF"


@pytest.mark.parametrize("value", ["0", 0, None])
def test_set_enabled_takes_only_a_bool(restore_switch, value):
    with pytest.raises(TypeError):
        durability.set_enabled(value)


def test_relax_touches_nothing_while_enabled(restore_switch):
    class Untouchable:  # no `in_transaction`, no working `execute`: any access fails the test
        def __getattr__(self, name):
            raise AssertionError(f"relax touched the connection ({name}) while durability is on")

    durability.set_enabled(True)
    durability.relax(Untouchable())


def test_relax_turns_a_default_connection_off_while_disabled(tmp_path, restore_switch):
    durability.set_enabled(False)
    connection = sqlite3.connect(tmp_path / "relax.db", isolation_level=None)
    try:
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2  # SQLite's default here
        durability.relax(connection)
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 0
    finally:
        connection.close()


def test_relax_refuses_a_connection_inside_a_transaction(tmp_path, restore_switch):
    durability.set_enabled(False)
    connection = sqlite3.connect(tmp_path / "relax.db", isolation_level=None)
    try:
        connection.execute("BEGIN")
        with pytest.raises(RuntimeError, match="before the connection's first transaction"):
            durability.relax(connection)
    finally:
        connection.close()


def test_relax_leaves_a_broken_file_to_fail_on_the_next_statement(tmp_path, restore_switch):
    durability.set_enabled(False)
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database at all" * 100)
    connection = sqlite3.connect(junk, isolation_level=None)
    try:
        durability.relax(connection)  # the pragma's own error is swallowed ...
        with pytest.raises(sqlite3.DatabaseError, match="file is not a database"):
            connection.execute("SELECT 1 FROM sqlite_schema")  # ... and surfaces here, as today
    finally:
        connection.close()


def test_a_fresh_interpreter_starts_with_durability_on():
    # The venv's editable install points at the main checkout; cwd + PYTHONPATH make the
    # child import this tree's module, and the printed path proves it did.
    result = subprocess.run(
        [sys.executable, "-c", "import taskmaster.durability as d; print(d.enabled()); print(d.__file__)"],
        cwd=REPO, env=dict(os.environ, PYTHONPATH=str(REPO)), capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    enabled, origin = result.stdout.splitlines()
    assert enabled == "True"
    assert Path(origin).resolve() == DURABILITY_FILE.resolve()


def test_the_switch_has_no_environment_or_command_line_path():
    tree = ast.parse(DURABILITY_FILE.read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imported <= {"__future__", "os", "sqlite3"}
    names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not names & {"environ", "environb", "getenv", "getenvb", "argv"}


@pytest.mark.parametrize("line", [
    "os.fsync(fd)",
    "sync = os.fsync",
    "from os import replace, fsync",
    "os.fdatasync(handle.fileno())",
    "from sqlite3 import connect",
    "connection.execute('PRAGMA synchronous=NORMAL')",
    'connection.execute("pragma  fullfsync=1")',
    "kernel32.FlushFileBuffers(handle)",
    "durability.set_enabled(False)",
])
def test_the_scan_sees_each_way_around_the_switch(line):
    assert _bypasses(line) == [1]


@pytest.mark.parametrize("line", [
    '"""One durable (synchronous=FULL) coordinator transaction over checkout rows."""',
    "durability.fsync(handle.fileno())",
    "connection.execute(durability.synchronous_pragma())",
    "return  # Windows: directories cannot be opened for fsync; NTFS journals the rename.",
])
def test_the_scan_leaves_routed_calls_and_prose_alone(line):
    assert _bypasses(line) == []


def test_no_production_module_bypasses_the_durability_switch():
    offenders = [
        f"{path.relative_to(REPO).as_posix()}:{number}"
        for path in _production_sources() if path != DURABILITY_FILE
        for number in _bypasses(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_the_connect_scan_sees_an_unrelaxed_connection():
    sample = ast.parse(
        "def fresh(path):\n"
        "    return sqlite3.connect(path)\n"
        "def relaxed(path):\n"
        "    connection = sqlite3.connect(path)\n"
        "    durability.relax(connection)\n"
        "    return connection\n"
        "opener = sqlite3.connect\n"
    )
    unlisted, _ = _unrouted_connects(_connect_references(sample, "sample.py", None))
    assert unlisted == ["sample.py:2 in fresh", "sample.py:7 in <module>"]


def test_every_sqlite_connection_is_relaxed_or_never_commits():
    sites = [site for path in _production_sources()
             for site in _connect_references(ast.parse(path.read_text(encoding="utf-8")),
                                             path.relative_to(REPO).as_posix(), None)]
    unlisted, stale = _unrouted_connects(sites)
    assert unlisted == [], "call durability.relax(connection) right after connecting, or list a never-committing site"
    assert stale == [], "remove _NEVER_COMMITS entries whose connect is gone"
```

About the fresh-interpreter test:
- `subprocess.run` waits for the child, so it exits long before `_child_process_guard` checks at teardown.
- The conftest `Popen` wrapper adds `CREATE_NO_WINDOW`.
- The child is not `taskmaster.coordinator.service`, so no `real_service_process` marker is needed. The precedent is `tests/test_child_process_guard.py:20-22`.
- The `cwd` and `PYTHONPATH` setup follows `tests/test_native_cutover_crash.py:64-66`.

The connect scan fails in three cases:
- a new connect site in a function that neither relaxes nor is listed;
- a second connect inside a listed never-commit function;
- a stale listed entry.

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_durability.py -q -p no:cacheprovider`
Expected: collection error `ModuleNotFoundError: No module named 'taskmaster.durability'`.

- [ ] **Step 3: Implement the module**

Create `taskmaster/durability.py`:

```python
# User intent: tests must never pay real fsync/SQLite-sync costs, but production must never be able
# to turn durability off; every fsync and SQLite synchronous level in the package goes through here.
"""Process-wide durability switch for every fsync and SQLite `synchronous` level in taskmaster.

On in every interpreter at import. Only an in-process `set_enabled(False)` turns it off,
and only the test suite's conftest makes that call: no variable, flag or config key
reaches it, so no deployment can be configured out of durability. Off is safe for tests
because fsync and synchronous=FULL guard only against OS crash or power loss, which no
test simulates; an application crash loses nothing at synchronous=OFF.
"""
from __future__ import annotations

import os
import sqlite3

_enabled = True


def enabled() -> bool:
    """Whether fsync and `synchronous=FULL` are in force (always, outside the test suite)."""
    return _enabled


def set_enabled(value: bool) -> None:
    """Tests only: switch durability for this whole process (every thread) until switched back."""
    global _enabled
    if not isinstance(value, bool):
        raise TypeError(f"set_enabled takes a bool, not {type(value).__name__}")
    _enabled = value


def fsync(fd: int) -> None:
    """`os.fsync(fd)`, or nothing while durability is off."""
    if _enabled:
        os.fsync(fd)


def synchronous_pragma() -> str:
    """The statement a durable connection runs: FULL, or OFF while durability is off."""
    return "PRAGMA synchronous=FULL" if _enabled else "PRAGMA synchronous=OFF"


def relax(connection: sqlite3.Connection) -> None:
    """While durability is off, drop a default-level connection to `synchronous=OFF`.

    While it is on (always, outside the test suite) this returns without touching the
    connection. Call it right after connecting, before the connection's first
    transaction. The pragma reads the schema, so on a file that is not a database, or a
    busy one, it fails; that error is left for the connection's next statement to raise
    exactly as it does today, and the connection keeps its default level.
    """
    if _enabled:
        return
    if connection.in_transaction:
        raise RuntimeError("durability.relax must run before the connection's first transaction")
    try:
        connection.execute("PRAGMA synchronous=OFF")
    except sqlite3.Error:
        pass
```

The switch is a plain module global, not thread-local or a contextvar. In-process coordinator threads must see the value the test process set.

- [ ] **Step 4: Run to see only the two scans fail, naming exactly the sites to route**

Run: `.venv/Scripts/python.exe -m pytest tests/test_durability.py -q -p no:cacheprovider`
Expected: every test passes except these two.

`test_no_production_module_bypasses_the_durability_switch` fails with exactly the 19 lines of inventory 1:

```
taskmaster/coordinator/checkouts.py:363, taskmaster/coordinator/git.py:275,
taskmaster/coordinator/ownership.py:197, taskmaster/native/commands.py:285,
taskmaster/native/cutover.py:481, taskmaster/native/cutover.py:487,
taskmaster/native/projection.py:661, taskmaster/native/projection.py:1079,
taskmaster/native/projection.py:1174, taskmaster/native_routing/progress.py:245,
taskmaster/store.py:1376, taskmaster/store.py:1927, taskmaster/store.py:1991,
taskmaster/store.py:2008, taskmaster/store.py:2741, taskmaster/store.py:2765,
taskmaster/store.py:5455, taskmaster/store.py:6045, taskmaster/store.py:6816
```

`test_every_sqlite_connection_is_relaxed_or_never_commits` fails, and its `unlisted` value is exactly the six covered rows of inventory 2. `stale` is `[]`:

```
['taskmaster/coordinator/protocol.py:62 in connect', 'taskmaster/native/cutover.py:144 in _connect',
 'taskmaster/native/cutover.py:534 in write_backup', 'taskmaster/store.py:1356 in connection',
 'taskmaster/store.py:2200 in _try_register_session', 'taskmaster/store.py:995 in checkpoint_all']
```

If either list differs, stop and reconcile it with the inventories before editing. Both scans were validated against `a12d3aa` in drafting and produced exactly these lists.

- [ ] **Step 5: Route every site**

First add one import per file. None of the edited files already defines anything named `durability`, and `taskmaster/durability.py` imports only `os` and `sqlite3`, so there can be no import cycle.

`taskmaster/store.py`:
- Line 37: change `from taskmaster import projection_parse, yaml_io` to `from taskmaster import durability, projection_parse, yaml_io`.
- Line 1376: change `connection.execute("PRAGMA synchronous=FULL")` to `connection.execute(durability.synchronous_pragma())`.
- Lines 1927, 1991, 2008, 2741, 2765, 5455, 6045 and 6816 are all the identical text `os.fsync(handle.fileno())`. Replace all 8 in this file with `durability.fsync(handle.fileno())`, using Edit `replace_all`.
- Line 6046, `stat = os.fstat(handle.fileno())`, stays unchanged (Note 6).
- `checkpoint_all`, lines 1002–1004. Before:
```python
            connection.execute("PRAGMA busy_timeout=0")
            assert_compatible(connection)
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
```
After (the compatibility refusal still runs first):
```python
            connection.execute("PRAGMA busy_timeout=0")
            assert_compatible(connection)
            durability.relax(connection)
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
```
- `_try_register_session`, lines 2207–2209. Before:
```python
            activity.execute("PRAGMA busy_timeout=0")
            activity.row_factory = sqlite3.Row
            activity.execute("BEGIN IMMEDIATE")
```
After (must precede `BEGIN IMMEDIATE`):
```python
            activity.execute("PRAGMA busy_timeout=0")
            activity.row_factory = sqlite3.Row
            durability.relax(activity)
            activity.execute("BEGIN IMMEDIATE")
```

`taskmaster/native/commands.py`, lines 9–11. Before:
```python
import sqlite3

from . import contracts, events, metrics, neighbourhood, receipts, schema, search, relations
```
After:
```python
import sqlite3

from taskmaster import durability

from . import contracts, events, metrics, neighbourhood, receipts, schema, search, relations
```
Lines 284–285. Before:
```python
    # These connections belong to the native service; acknowledgement is FULL.
    connection.execute("PRAGMA synchronous=FULL")
```
After:
```python
    # These connections belong to the native service; acknowledgement is FULL.
    connection.execute(durability.synchronous_pragma())
```

`taskmaster/native/cutover.py`:
- Line 44. Before: `from taskmaster.admission import UnsupportedStoreError, assert_compatible, migration_owner`. After:
```python
from taskmaster import durability
from taskmaster.admission import UnsupportedStoreError, assert_compatible, migration_owner
```
- `_connect`, lines 143–146. Before:
```python
def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, isolation_level=None, timeout=30)
    connection.execute("PRAGMA busy_timeout=30000")
    return connection
```
After:
```python
def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, isolation_level=None, timeout=30)
    connection.execute("PRAGMA busy_timeout=30000")
    durability.relax(connection)
    return connection
```
- `_fsync`, lines 478–491. Before:
```python
def _fsync(path: Path) -> None:
    """Flush a file, then its directory entry (a no-op where directories cannot be opened)."""
    with open(path, "rb+") as stream:
        os.fsync(stream.fileno())
    try:
        descriptor = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return  # Windows: directories cannot be opened for fsync; NTFS journals the rename.
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
```
After (only the two inner calls change):
```python
def _fsync(path: Path) -> None:
    """Flush a file, then its directory entry (a no-op where directories cannot be opened)."""
    with open(path, "rb+") as stream:
        durability.fsync(stream.fileno())
    try:
        descriptor = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return  # Windows: directories cannot be opened for fsync; NTFS journals the rename.
    try:
        durability.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
```
- `write_backup`, lines 534–535. Before:
```python
    with closing(_connect_readonly(database_path(root))) as reader, closing(sqlite3.connect(temp)) as destination:
        reader.backup(destination)
```
After:
```python
    with closing(_connect_readonly(database_path(root))) as reader, closing(sqlite3.connect(temp)) as destination:
        durability.relax(destination)
        reader.backup(destination)
```

`taskmaster/native/projection.py`:
- Line 31. Before: `from taskmaster.projection_paths import UnsafePath, safe_path`. After:
```python
from taskmaster import durability
from taskmaster.projection_paths import UnsafePath, safe_path
```
- Lines 661, 1079 and 1174 are the identical text `os.fsync(handle.fileno())`. Replace all 3 with `durability.fsync(handle.fileno())`, using `replace_all`.

`taskmaster/native_routing/progress.py`:
- Line 41. Before: `from taskmaster.native.db import assert_native`. After:
```python
from taskmaster import durability
from taskmaster.native.db import assert_native
```
- Line 245: change `os.fsync(handle.fileno())` to `durability.fsync(handle.fileno())`.

`taskmaster/coordinator/checkouts.py`:
- Line 23. Before: `from taskmaster.native import checkouts as store`. After:
```python
from taskmaster import durability
from taskmaster.native import checkouts as store
```
- Line 363: change `connection.execute('PRAGMA synchronous=FULL')` to `connection.execute(durability.synchronous_pragma())`.

`taskmaster/coordinator/git.py`:
- Line 26. Before: `from taskmaster.native import metrics`. After:
```python
from taskmaster import durability
from taskmaster.native import metrics
```
- Line 275: change `connection.execute('PRAGMA synchronous=FULL')` to `connection.execute(durability.synchronous_pragma())`.

`taskmaster/coordinator/ownership.py`:
- Lines 9–10. Before:
```python
import uuid


class OwnershipUnavailable(RuntimeError):
```
After:
```python
import uuid

from taskmaster import durability


class OwnershipUnavailable(RuntimeError):
```
- Line 197: change `os.fsync(stream.fileno())` to `durability.fsync(stream.fileno())`.

`taskmaster/coordinator/protocol.py`:
- Line 7. Before: `from taskmaster.native import contracts, db, schema`. After:
```python
from taskmaster import durability
from taskmaster.native import contracts, db, schema
```
- `connect`, lines 59–66. Before:
```python
def connect(root: Path, *, readonly=False):
    path = root / '.taskmaster/local/store.db'
    uri = path.as_uri() + ('?mode=ro' if readonly else '?mode=rw')
    connection = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=30)
    connection.execute('PRAGMA busy_timeout=30000')
    if readonly:
        connection.execute('PRAGMA query_only=ON')
    return connection
```
After:
```python
def connect(root: Path, *, readonly=False):
    path = root / '.taskmaster/local/store.db'
    uri = path.as_uri() + ('?mode=ro' if readonly else '?mode=rw')
    connection = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=30)
    connection.execute('PRAGMA busy_timeout=30000')
    if readonly:
        connection.execute('PRAGMA query_only=ON')
    else:
        durability.relax(connection)
    return connection
```

Confirm that no `os.fsync` remains outside the new module. This must print only `taskmaster/durability.py`:

```bash
git grep -l "os\.fsync" -- taskmaster backlog_server.py
```

- [ ] **Step 6: Run to verify it passes, with no behaviour change**

Run: `.venv/Scripts/python.exe -m pytest tests/test_durability.py -q -p no:cacheprovider`
Expected: all tests pass.

Run: `.venv/Scripts/python.exe -c "import taskmaster.store, taskmaster.native.commands, taskmaster.native.cutover, taskmaster.native.projection, taskmaster.native_routing.progress, taskmaster.coordinator.checkouts, taskmaster.coordinator.git, taskmaster.coordinator.ownership, taskmaster.coordinator.protocol, taskmaster.backlog_server; print('ok')"`
Expected: `ok`. This catches import-order mistakes.

Run these one file at a time:
- `.venv/Scripts/python.exe -m pytest tests/test_store_schema.py -q -p no:cacheprovider`
- `.venv/Scripts/python.exe -m pytest tests/test_store_recovery.py -q -p no:cacheprovider`
- `.venv/Scripts/python.exe -m pytest tests/test_native_commands.py -q -p no:cacheprovider`
- `.venv/Scripts/python.exe -m pytest tests/test_native_service_ownership.py -q -p no:cacheprovider`

Expected: each file reports only passed/skipped. Durability is still ON everywhere, so `relax` returns immediately and `test_store_recovery.py:615`'s exact statement list still holds without its marker.

- [ ] **Step 7: Commit Part A**

```bash
git add taskmaster/durability.py tests/test_durability.py taskmaster/store.py taskmaster/native/commands.py taskmaster/native/cutover.py taskmaster/native/projection.py taskmaster/native_routing/progress.py taskmaster/coordinator/checkouts.py taskmaster/coordinator/git.py taskmaster/coordinator/ownership.py taskmaster/coordinator/protocol.py
git commit -m "feat(speed): route every fsync and SQLite sync level through taskmaster.durability

15 os.fsync calls and 4 PRAGMA synchronous=FULL executions now go through one
process-wide switch that is on by default and has no env/CLI path; the 5
connect sites that relied on SQLite's default level call durability.relax,
which does nothing while the switch is on. Scan tests keep new fsyncs,
synchronous pragmas and unrelaxed connect sites from bypassing it. Behaviour is
unchanged: nothing turns the switch off yet.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

## Part B: the test suite turns durability off, except for `durability` tests

- [ ] **Step 8: Write the failing tests**

Append to `tests/test_durability.py`:

```python
def test_an_unmarked_test_runs_with_durability_off():
    assert durability.enabled() is False


@pytest.mark.durability
def test_a_durability_test_keeps_durability_on():
    assert durability.enabled() is True


def test_an_unmarked_test_opens_store_connections_at_synchronous_off(tmp_taskmaster):
    from taskmaster import store

    connection = store.open_store(root=tmp_taskmaster, session="durability-off").connection
    assert connection.execute("PRAGMA synchronous").fetchone()[0] == 0


def test_coordinator_and_cutover_connections_are_relaxed_only_where_they_write(tmp_path):
    from taskmaster.coordinator import protocol
    from taskmaster.native import cutover

    database = tmp_path / ".taskmaster" / "local" / "store.db"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as seed:
        seed.execute("CREATE TABLE t(x)")
    seed.close()
    for connection, level in ((protocol.connect(tmp_path), 0),
                              (protocol.connect(tmp_path, readonly=True), 2),
                              (cutover._connect(database), 0)):
        try:
            assert connection.execute("PRAGMA synchronous").fetchone()[0] == level
        finally:
            connection.close()


def _traced(monkeypatch) -> list[str]:
    statements: list[str] = []
    real = sqlite3.connect

    def connect(*args, **kwargs):
        connection = real(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    return statements


def test_session_activity_and_checkpoint_connections_are_relaxed_before_they_write(tmp_taskmaster, monkeypatch):
    from taskmaster import store

    opened = store.open_store(root=tmp_taskmaster, session="durability-relax")
    primary = opened.connection
    statements = _traced(monkeypatch)
    opened._try_register_session(primary, current_tool=None)
    assert statements.index("PRAGMA synchronous=OFF") < statements.index("BEGIN IMMEDIATE"), statements
    del statements[:]
    store.checkpoint_all()
    assert statements.index("PRAGMA synchronous=OFF") < statements.index("PRAGMA wal_checkpoint(TRUNCATE)"), statements
```

`store.open_store(root=tmp_taskmaster, …).connection` was confirmed to open on this fixture layout at `a12d3aa`, where it returned `2`. `_try_register_session` (`store.py:2191-2229`) opens its own activity connection on every call. `checkpoint_all` (`store.py:987-1012`) opens one for each registered store.

- [ ] **Step 9: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_durability.py -q -p no:cacheprovider`
Expected: 4 failures.
- `test_an_unmarked_test_runs_with_durability_off`: `assert True is False`.
- `test_an_unmarked_test_opens_store_connections_at_synchronous_off`: `assert 2 == 0`.
- `test_coordinator_and_cutover_connections_are_relaxed_only_where_they_write`: `assert 2 == 0`.
- `test_session_activity_and_checkpoint_connections_are_relaxed_before_they_write`: `ValueError: 'PRAGMA synchronous=OFF' is not in list`.

`test_a_durability_test_keeps_durability_on` passes both before and after this step. Its job is to guard the marker once the fixture exists. Until then, a `PytestUnknownMarkWarning` for `durability` appears.

- [ ] **Step 10: Implement the conftest switch and mark the three tests**

`tests/conftest.py`, lines 46–49. Before:
```python
    config.addinivalue_line(
        "markers",
        "scale: full-size acceptance profiles, deselected by default; run with `-m scale`",
    )
```
After (a second registration added below):
```python
    config.addinivalue_line(
        "markers",
        "scale: full-size acceptance profiles, deselected by default; run with `-m scale`",
    )
    config.addinivalue_line(
        "markers",
        "durability: keep real fsync and SQLite `synchronous=FULL` for this test; every other "
        "test runs with both off (see `_durability_switch`)",
    )
```

`tests/conftest.py`: insert this after the end of `_child_process_guard` (HEAD line 237) and before `@pytest.fixture(autouse=True)` / `def _store_isolation():` (HEAD lines 240–241):

```python
@pytest.fixture(autouse=True)
def _durability_switch(request):
    """Unmarked tests skip fsync and run SQLite at `synchronous=OFF`; `durability` tests keep both.

    pytest orders a conftest's autouse fixtures by name, so this runs right after
    `_child_process_guard`: set up before, and torn down after, the coordinator and store
    resets, so every connection a test opens sees its setting. Between tests, and in every
    child process (fresh interpreters), durability is on, as in production.
    """
    from taskmaster import durability  # noqa: PLC0415 — imported after sys.path setup

    durability.set_enabled(request.node.get_closest_marker("durability") is not None)
    try:
        yield
    finally:
        durability.set_enabled(True)
```

The fixture's name decides where it runs. pytest collects a conftest's fixtures with `dir(module)` (`.venv/Lib/site-packages/_pytest/fixtures.py:2104`), which sorts them, so autouse fixtures in one conftest run in alphabetical order, not file order. A throwaway conftest confirmed this in drafting: setup ran `_child_process_guard`, `_durability_switch`, `_native_coordinator_isolation`, `_store_isolation`, and teardown ran in reverse. Autouse fixtures defined in test modules run after all of these.

`tests/test_store_schema.py:209`. Before:
```python
def test_store_connection_uses_required_pragmas_and_explicit_transactions(
```
After:
```python
@pytest.mark.durability
def test_store_connection_uses_required_pragmas_and_explicit_transactions(
```

`tests/test_native_projection_drain.py:57`. Before:
```python
def test_a_peer_command_commits_while_the_drain_is_mid_fsync(twins, monkeypatch):
```
After:
```python
@pytest.mark.durability
def test_a_peer_command_commits_while_the_drain_is_mid_fsync(twins, monkeypatch):
```

`tests/test_store_recovery.py:615`. `pytest` is imported at line 14. Before:
```python
def test_truncate_checkpoint_failure_is_best_effort(tmp_path, monkeypatch):
```
After:
```python
@pytest.mark.durability  # pins checkpoint_all's production statement list; relax would add one
def test_truncate_checkpoint_failure_is_best_effort(tmp_path, monkeypatch):
```

- [ ] **Step 11: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_durability.py -q -p no:cacheprovider`
Expected: all tests pass, with no `PytestUnknownMarkWarning`.

Run: `.venv/Scripts/python.exe -m pytest "tests/test_store_schema.py::test_store_connection_uses_required_pragmas_and_explicit_transactions" "tests/test_native_projection_drain.py::test_a_peer_command_commits_while_the_drain_is_mid_fsync" "tests/test_store_recovery.py::test_truncate_checkpoint_failure_is_best_effort" -q -p no:cacheprovider`
Expected: 3 passed.

- [ ] **Step 12: Wider regression (one file at a time, no `-n`)**

Run each file in turn, and let each one finish before starting the next. Expected: every file reports only passed/skipped. The approximate times come from the 2026-09-29 profile.

```bash
.venv/Scripts/python.exe -m pytest tests/test_store_schema.py -q -p no:cacheprovider               # store.py:1376 pragma; marked test
.venv/Scripts/python.exe -m pytest tests/test_store_recovery.py -q -p no:cacheprovider             # checkpoint_all relax; marked test; corrupt/missing-db paths
.venv/Scripts/python.exe -m pytest tests/test_native_projection_drain.py -q -p no:cacheprovider    # native/projection.py:661; marked test
.venv/Scripts/python.exe -m pytest tests/test_store_transactions.py -q -p no:cacheprovider         # store.py fsyncs + session-activity relax (~25 s)
.venv/Scripts/python.exe -m pytest tests/test_native_projection.py -q -p no:cacheprovider          # outbox publish + commands.execute pragma (~40 s)
.venv/Scripts/python.exe -m pytest tests/test_native_commands.py -q -p no:cacheprovider            # native/commands.py:285
.venv/Scripts/python.exe -m pytest tests/test_native_service.py -q -p no:cacheprovider             # protocol.connect relax via service._connect (~40 s)
.venv/Scripts/python.exe -m pytest tests/test_native_cutover.py -q -p no:cacheprovider             # cutover._connect/_fsync/write_backup; lost-manifest test (~65 s)
.venv/Scripts/python.exe -m pytest tests/test_native_progress.py -q -p no:cacheprovider            # native_routing/progress.py:245 (~80 s)
.venv/Scripts/python.exe -m pytest tests/test_native_git_checkouts.py -q -p no:cacheprovider       # coordinator checkouts.write + git.write_state (~75 s)
.venv/Scripts/python.exe -m pytest tests/test_native_service_ownership.py -q -p no:cacheprovider   # coordinator/ownership.py:197
```

If a file fails, first check whether the test observes durability: an exact statement list, a synchronous level, or bytes on disk after a simulated power loss. Notes 5 and 6 cover the cases already ruled out. Mark a test `@pytest.mark.durability` only if it observes durability. Do not use the marker just to make a failure go away without that explanation.

- [ ] **Step 13: Before/after timing across the switch variants, plus the collection check**

Create two throwaway measurement plugins under `test-results/task3-durability/`. That directory is gitignored, and these files are never committed. Each plugin re-enables part of durability for the run while the conftest keeps the switch off. They only patch module attributes that the call sites look up at call time.

`test-results/task3-durability/durability_fsync_only.py`:
```python
# Throwaway measurement plugin, never committed: with the switch off, keep every SQLite sync at FULL
# so only fsync is skipped (the "fsync-only" variant).
from pathlib import Path

from taskmaster import durability

assert Path(durability.__file__).resolve().parents[1] == Path.cwd().resolve(), durability.__file__
durability.synchronous_pragma = lambda: "PRAGMA synchronous=FULL"
durability.relax = lambda connection: None
```

`test-results/task3-durability/durability_no_relax.py`:
```python
# Throwaway measurement plugin, never committed: with the switch off, skip only the relax step, so
# default-level connections stay FULL (isolates what relax adds on top of fsync + explicit pragmas).
from pathlib import Path

from taskmaster import durability

assert Path(durability.__file__).resolve().parents[1] == Path.cwd().resolve(), durability.__file__
durability.relax = lambda connection: None
```

Confirm again that the Step 0 process count is `0`. Then run the three variants interleaved, three rounds, so machine noise spreads across all of them. Run from the worktree root in Git Bash:

```bash
for round in 1 2 3; do
  echo "B fsync-only";   PYTHONPATH=test-results/task3-durability .venv/Scripts/python.exe -m pytest tests/test_native_projection.py -q -p no:cacheprovider -p durability_fsync_only | tail -1
  echo "D no-relax";     PYTHONPATH=test-results/task3-durability .venv/Scripts/python.exe -m pytest tests/test_native_projection.py -q -p no:cacheprovider -p durability_no_relax | tail -1
  echo "C fsync+relax";  .venv/Scripts/python.exe -m pytest tests/test_native_projection.py -q -p no:cacheprovider | tail -1
done
.venv/Scripts/python.exe -m pytest tests --collect-only -q -p no:cacheprovider > test-results/task3-durability/collect-after.txt
git diff --no-index test-results/task3-durability/collect-before.txt test-results/task3-durability/collect-after.txt
```

Record the median of each variant next to Step 0's variant A:

| Variant | What is off in unmarked tests | Median |
|---|---|---|
| A (Step 0) | nothing (pre-change tree) | |
| B fsync-only | `os.fsync` only; every SQLite connection stays FULL | |
| D no-relax | fsync + the 4 explicit pragmas; default-level connections stay FULL | |
| C fsync+relax (shipped) | fsync + explicit pragmas + `relax` on default-level connections | |

Expected:
- The collection diff shows only added `tests/test_durability.py::…` IDs and the changed final `N tests collected` line. No ID is removed or renamed.
- The timings are **informational, not a gate**. Noise was ±20% while drafting.
- Spec §9's saving of **"~800 s" stays unconfirmed** until the baseline and acceptance runs re-measure worker-seconds (spec §7). This task records one file's medians, not a suite-wide figure.
- If C is not below A, record it anyway and report it to the orchestrator.
- Delete the two plugin files afterwards. They are gitignored, but they must not linger.

- [ ] **Step 14: Commit Part B**

Replace the `<…>` placeholders with the Step 0 and Step 13 medians before committing.

```bash
git add tests/conftest.py tests/test_durability.py tests/test_store_schema.py tests/test_native_projection_drain.py tests/test_store_recovery.py
git commit -m "test(speed): unmarked tests run without fsync or SQLite sync

An autouse fixture turns taskmaster.durability off for every test not marked
'durability' and restores it at teardown; child processes (fresh interpreters)
keep full durability. Marked: test_store_schema's synchronous==2 assertion, the
projection drain's mid-fsync peer-commit test, and test_store_recovery's exact
checkpoint_all statement list.

tests/test_native_projection.py medians (s): on <A>, fsync-only <B>,
no-relax <D>, fsync+relax <C>.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

#### Notes for the reviewer

1. **Spec site counts.** The spec's "22 fsync + 5 synchronous sites" are grep **line** counts.
   - The 22 includes the `cutover._fsync` definition, its 4 callers, a comment at `cutover.py:485` and a comment at `native/metrics.py:49`.
   - The 5 includes the docstrings at `checkouts.py:361` and `git.py:273`.
   - The actual call sites are 15 fsync plus 4 pragma (inventory 1). The design is unaffected.
2. **Why `relax` exists.** The spec's "−17%" probe (scratchpad `tmprobe.py`, `nofsync` mode) forced `synchronous=OFF` on every connection. Routing only the explicit sites would have left the default-level connections syncing. `relax` covers every writing connection in `taskmaster/` (inventory 2). Test-owned raw `sqlite3.connect` calls in `tests/` are out of scope and keep the default. Step 13's B/D/C variants show how much each lever contributes on one file.
3. **`test_native_projection_drain.py:57` does not observe fsync.** It patches no fsync. It hooks `projection.HOOKS["checkpoint"]` at stage `temp_written`, which `_write_temp` fires just after its fsync (`native/projection.py:661-662`), so its target does not change. It is marked because spec §3.2 names it, which keeps a real fsync inside the window the test is named after. No other test patches `os.fsync`, `fsync` or `synchronous` (`git grep -n "fsync\|synchronous" -- tests` at HEAD).
4. **Fixture order is alphabetical.** The `_child_process_guard` docstring says it is "defined first", but pytest orders a conftest's autouse fixtures by name, not by definition order, and verified above. `_durability_switch` is named so that it sorts between `_child_process_guard` and `_native_coordinator_isolation`.
5. **Module-scoped fixtures build with durability ON.** `base` (`test_agent_journeys.py:85`) and `graph` (`test_native_dependency_graph.py:92`) run setup before the function-scoped switch, after the previous test's teardown has restored True. That costs one build per module per worker. A session-scoped switch would cover them, but it would break the invariant that code outside a test body runs as production does. This task keeps the per-test fixture.
6. **No test simulates power loss, and fstat-after-write is safe.**
   - Crash children and `os._exit` children are fresh interpreters with durability ON: `test_native_cutover_crash.py:65`, `test_native_git_crash.py:46`, `test_native_cutover.py:491-494`. A parent reading a child's writes reads the OS cache, which survives process death.
   - The only "power loss" test, `test_native_cutover.py:433`, simulates the loss explicitly with `manifest.write_text("")`.
   - `store.py:6046` records `os.fstat` of the export temp file after the (now switchable) fsync. A throwaway NTFS experiment (write, flush, fstat, close, `os.replace`, stat; 3,000 runs) found 0 mtime or size mismatches with fsync and 0 without.
7. **`scripts/` is out of scope and not routed.** `scripts/benchmark_native_core.py:62`, `scripts/native_backfill_rehearsal.py:52` and `scripts/native_n16_acceptance.py:1707` set `PRAGMA synchronous=FULL` directly. Tests run them only as subprocesses (`test_native_n16_acceptance_smoke.py:25`), so they keep full durability. The scans cover `taskmaster/` and `backlog_server.py`, per spec §3.2's module list. `hooks/` has no durability sites and no writing connections that tests run in-process.

---

### Task 4: Template registry and verify mode

**Files:**
- Create: `tests/project_templates.py`
- Create: `tests/test_project_templates.py`
- Modify: `tests/native_twins.py:8-9` (imports) and `tests/native_twins.py:328-343` (`make_twins`)
- Modify: `tests/conftest.py`, inserting after line 267, the end of `_native_coordinator_isolation`
- Test: `tests/test_project_templates.py`

**Interfaces:**
- Consumes: none.
- Produces:
  - `native_twins.make_twins(tmp_path, monkeypatch, seed=None, *, visibility='legacy', engine_oracle=False, template_key: tuple | None = None) -> Twins`. Without a key its behaviour is unchanged.
  - The key convention is `("<test module name>.<fixture or seed name>", *every value the seed captures)`.
  - `project_templates.VERIFY_ENV == "TASKMASTER_TEMPLATE_VERIFY"`. The `release` runner task sets it to `"1"`.
  - `project_templates.TemplateRegistry` with:
    - `.key(recipe: str, template_key: tuple) -> tuple`
    - `.template(key, build, seed=None) -> Template`
    - `.directory(key) -> Path`
    - `.scratch(key) -> Path`
    - `.builds: Counter`
    - `.base`
  - `project_templates.Template(directory, state)`.
  - Module functions `copy_into(template, target)`, `assert_quiescent(directory)`, `verify_enabled()`, `activate(base)`, `deactivate()` and `active()`.
  - `assert_same_build(fresh, copy, template, fresh_state, fresh_globals, copy_globals)`. It takes the first four arguments until Step 8 adds the globals.
  - `process_globals() -> {(module, name): value}` and `describe_globals(own, template, others=()) -> {"module.name": masked text}` (Step 8).
  - The session-scoped autouse fixture `twin_templates` in `tests/conftest.py`, which yields the active `TemplateRegistry`.

**Design decisions this task fixes (read before coding):**
- **Autouse session fixture.** The brief suggested a fixture that call sites request. With an explicit request, a key that forgot the request would work or fail depending on which fixture a worker happened to set up first. So `twin_templates` is autouse, and call sites only pass `template_key=`.
  - The basetemp is resolved lazily, on the first build. A session that never templates never creates or rotates a basetemp on its account. Until spec §3.1 lands, that rotation can cost 229 s.
- **Where the code lives.** `native_twins.py` is 344 lines and owns `CLOCK`, `install_clock`, `point_server_at` and `Twins`, so the twin-specific build, copy and restore code stays there. The recipe-agnostic registry, the copy filter and the verify diff go in the new `tests/project_templates.py`.
  - Always import it as `project_templates`. Never import it as `tests.project_templates`, which would load a second registry.
- **Process state restored on copy.** The spec names `CLOCK` and `_session_task`. This task also restores `bs._session_bundle`, the third process global a seed can set (`backlog_server.py:7938-7952`, set by a bundle pick at `:7240` and `native_routing/tasks.py:424`).
  - A fresh build leaves the seed's bundle in place, because `point_server_at` resets it *before* the seed (`native_twins.py:113`). The copy therefore calls `point_server_at` first and restores the bundle after.
  - The template build starts `_session_task` and `_session_bundle` at `None` under its private patch. The recorded state is then the seed's own effect, not a value leaked from an earlier test.
- **The fingerprint cache is never copied or diffed.** This is not in the spec. `cutover.activate` saves `local/cache/sync-fingerprints.json` (`cutover.py:666-674`), but only for files older than the 2 s racy window (`sync_files.py:212,336`). A fresh build therefore has the file only when it ran slowly.
  - Its entries are keyed by the builder's absolute path and inodes (`sync_files.py:201-206,379-380`), so they could never match a copy.
  - Found while drafting: the verify probe failed twice on `rigged` when a fresh build was slow, until this file was skipped.
  - Copies get what a fast fresh build gets: no cache.
  - Templates also refuse a `.git` directory.
- **The recipe guard is not the key.** On a cache hit, `template()` checks that the seed has the same source location (file, first line, name) and the same plain captured values. A key that omits a captured value, or two different seeds sharing one key, fails loudly.
  - The location is used instead of the code object because pytest imports a test module as `tests.x` while a sibling's `from x import _seed` loads a second copy with its own code objects. This was confirmed at HEAD for `test_native_routing_viewer`.
- **Where a template is built.** A template is built inside whichever test first uses its key on a worker, under that test's patches, except the private `MonkeyPatch` that the build itself uses. Only share a key between fixtures whose tests patch nothing the build reads (spec §3.3, "patches applied before building").
- **Verify mode builds fresh after the copy, and also compares module globals** (Steps 6-10, a review focus).
  - The file and row diff cannot see module-level state: a latch, a cache or a "current task" that a build sets and a copy does not restore.
  - `process_globals()` walks every module-level data binding of every loaded `taskmaster` module: plain values, containers, and objects with a `__dict__`. Functions, classes, modules, locks, loggers, threads and thread-locals are skipped. `describe_globals()` masks the result the way the file diff does: the test's own roots read `<root>`, the template `<template>`, other projects `<other>`, and the `_NOISE` patterns apply.
  - Verify mode snapshots after the copy. It then runs the fresh build from that state and snapshots again before undoing the build's private patch, which is where a fresh `make_twins` leaves its test. So a global the fresh build sets differently, or one only the copy set, differs.
  - **Globals are compared, never rolled back.** Resetting module globals to a snapshot so both builds started from one pre-state was tried while drafting. It rolled back lazily filled router registries without their companions, and `rigged` exercises were refused with "`backlog_area_get` cannot run here". The review-focus test therefore gives both runs the same *kind* of pre-state instead: another project's fresh build (`_other_project`, which picks a differently titled task) runs just before each.
  - Masking every root `<root>` would have hidden a copy that still names its template. That is why the template gets its own label, in the file diff too.


- [ ] **Step 1: Write the failing test**

Create `tests/test_project_templates.py`:

```python
# User intent: seeded-project templates may only replace fresh builds if they are keyed
# explicitly, built once, copied without live or identity-bound state, and provably equal
# to a fresh build; these tests pin each of those promises (spec 3.3).
from __future__ import annotations

import os
import sqlite3
from contextlib import closing

import pytest

import project_templates
from project_templates import TemplateRegistry, copy_into
from taskmaster import backlog_server as bs
from native_twins import CLOCK, committed, make_twins


def _files(directory):
    (directory / "tree" / ".taskmaster" / "local" / "coordinator").mkdir(parents=True)
    local = directory / "tree" / ".taskmaster" / "local"
    (local / "store.db").write_bytes(b"db")
    (local / "store.db-shm").write_bytes(b"shm")
    (local / "store.db-wal").write_bytes(b"")
    (local / "coordinator" / "owner.lock").write_bytes(b"\0")
    (directory / "tree" / ".taskmaster" / "backlog.yaml.tmp.123").write_text("half", encoding="utf-8")
    (directory / "tree" / ".taskmaster" / "backlog.yaml").write_text("version: 3\n", encoding="utf-8")
    (local / "cache").mkdir()
    (local / "cache" / "sync-fingerprints.json").write_text("{}", encoding="utf-8")
    return {"built": True}


def test_the_key_names_the_recipe_its_values_and_the_twins_verify_flag(tmp_path, monkeypatch):
    registry = TemplateRegistry(tmp_path)
    monkeypatch.setenv("TASKMASTER_TWINS_VERIFY", "1")
    verified = registry.key("make_twins", ("depends", "x"))
    monkeypatch.setenv("TASKMASTER_TWINS_VERIFY", "0")
    assert registry.key("make_twins", ("depends", "x")) != verified
    assert registry.key("make_twins", ("depends", "x")) != registry.key("make_twins", ("depends", "y"))
    for bad in ((_files.__code__,), (_files,), ("rigged", ["list"]), (), "rigged"):
        with pytest.raises(TypeError):
            registry.key("make_twins", bad)


def test_a_template_is_built_once_per_key_under_the_registry_base(tmp_path):
    registry = TemplateRegistry(tmp_path / "templates")
    built = []

    def build(directory):
        built.append(directory)
        return _files(directory)

    one, two = registry.key("files", ("one",)), registry.key("files", ("two",))
    first = registry.template(one, build)
    assert registry.template(one, build) is first
    other = registry.template(two, build)
    assert registry.builds == {one: 1, two: 1} and len(built) == 2
    assert first.directory != other.directory
    assert first.directory.parent == tmp_path / "templates" and first.state == {"built": True}


def _loaded_twice():
    """One seed from two module copies, as pytest's `tests.x` and a sibling's `from x import`."""
    source = "def seed():\n    return 'same source'\n"
    first, second = {}, {}
    exec(compile(source, "seeds_module.py", "exec"), first)
    exec(compile(source, "seeds_module.py", "exec"), second)
    return first["seed"], second["seed"]


def test_one_key_cannot_name_two_seeds(tmp_path):
    registry = TemplateRegistry(tmp_path)

    def seed_for(value):
        def seed():
            return value
        return seed

    key = registry.key("files", ("depends",))
    registry.template(key, _files, seed_for("a"))
    registry.template(key, _files, seed_for("a"))
    with pytest.raises(AssertionError, match="names two different seeds"):
        registry.template(key, _files, seed_for("b"))
    shared = registry.key("files", ("shared",))
    one, other = _loaded_twice()
    assert one.__code__ is not other.__code__
    registry.template(shared, _files, one)
    registry.template(shared, _files, other)
    with pytest.raises(AssertionError, match="names two different seeds"):
        registry.template(shared, _files, lambda: "another seed")


def test_a_copy_keeps_mtimes_and_skips_shm_temp_fingerprint_and_coordinator_files(tmp_path):
    template = TemplateRegistry(tmp_path / "templates").template(("files", ("copy",), None), _files)
    stamp = 1_600_000_000_000_000_000
    os.utime(template.directory / "tree" / ".taskmaster" / "backlog.yaml", ns=(stamp, stamp))
    copy_into(template, tmp_path / "test")
    copied = sorted(p.relative_to(tmp_path / "test").as_posix() for p in (tmp_path / "test").rglob("*"))
    assert copied == ["tree", "tree/.taskmaster", "tree/.taskmaster/backlog.yaml", "tree/.taskmaster/local",
                      "tree/.taskmaster/local/cache", "tree/.taskmaster/local/store.db",
                      "tree/.taskmaster/local/store.db-wal"]
    assert (tmp_path / "test" / "tree" / ".taskmaster" / "backlog.yaml").stat().st_mtime_ns == stamp
    assert (template.directory / "tree" / ".taskmaster" / "local" / "store.db-shm").exists()


@pytest.mark.parametrize("leftover,why", [
    ("tree/.taskmaster/local/store.db-wal", "non-empty WAL"),
    ("tree/.git/HEAD", "Git records absolute paths"),
])
def test_a_template_with_live_or_identity_bound_state_is_refused(tmp_path, leftover, why):
    def build(directory):
        _files(directory)
        (directory / leftover).parent.mkdir(parents=True, exist_ok=True)
        (directory / leftover).write_bytes(b"x")
        return {}
    with pytest.raises(AssertionError, match=why):
        TemplateRegistry(tmp_path).template(("files", ("bad",), None), build)


def _picked():
    bs.backlog_add_task(title="Picked", epic="test-epic", phase="dev")
    bs.backlog_pick_task(task_id="test-epic-001")


def test_templated_twins_match_a_fresh_build_and_restore_its_process_state(tmp_path, monkeypatch, twin_templates):
    monkeypatch.setattr(bs, "_session_task", None)
    fresh = make_twins(tmp_path / "fresh", monkeypatch, _picked)
    fresh_clock, fresh_task, fresh_state = dict(CLOCK), bs._session_task, committed(fresh.native)
    key = ("test_project_templates.picked", tmp_path.name)
    make_twins(tmp_path / "first", monkeypatch, _picked, template_key=key)
    monkeypatch.setattr(bs, "_session_task", None)
    CLOCK.update(at=fresh_clock["at"].replace(year=2030))
    twins = make_twins(tmp_path / "second", monkeypatch, _picked, template_key=key)
    assert twin_templates.builds[twin_templates.key("make_twins", key)] == 1
    assert (twins.legacy, twins.native) == (tmp_path / "second" / "legacy", tmp_path / "second" / "native")
    assert bs.ROOT == twins.legacy
    assert dict(CLOCK) == fresh_clock and bs._session_task == fresh_task and fresh_task is not None
    assert committed(twins.native) == fresh_state
    assert committed(twins.legacy) == committed(fresh.legacy)


def test_verify_mode_passes_a_faithful_copy_and_fails_an_altered_one(tmp_path, monkeypatch, twin_templates):
    monkeypatch.setenv(project_templates.VERIFY_ENV, "1")
    key = ("test_project_templates.verify", tmp_path.name)
    make_twins(tmp_path / "faithful", monkeypatch, _picked, template_key=key)
    template = twin_templates.directory(twin_templates.key("make_twins", key))
    with closing(sqlite3.connect(template / "native" / ".taskmaster" / "local" / "store.db")) as connection:
        connection.execute("UPDATE entities SET body='altered' WHERE kind='task' AND id='test-epic-001'")
        connection.commit()
    with pytest.raises(AssertionError, match="differs from a fresh build"):
        make_twins(tmp_path / "altered", monkeypatch, _picked, template_key=key)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_project_templates.py -q -p no:cacheprovider`
Expected: collection ERROR `ModuleNotFoundError: No module named 'project_templates'`.

- [ ] **Step 3: Implement**

3a. Create `tests/project_templates.py`:

```python
# User intent: a seeded twin project costs 1-3.6 s to build for every test; build each recipe once
# per xdist worker and hand each test a private copy - never across runs, never the template
# itself, and provably equal to a fresh build whenever TASKMASTER_TEMPLATE_VERIFY=1 (spec 3.3).
"""Per-worker, in-session templates of seeded test projects.

A template is built once per explicit key, closed, and copied into each test's own directory
with copy2, which keeps the mtimes the legacy projection checks compare (store.py:3503,4571).
Nothing survives the session: stored rows carry this process's pid, which a later run would
read as dead or reused (claims.py:183-193), and stored wall-clock values would age.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import difflib
import hashlib
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
from typing import Callable, NamedTuple

VERIFY_ENV = "TASKMASTER_TEMPLATE_VERIFY"
_PLAIN = (str, int, float, bool, type(None))


class Template(NamedTuple):
    directory: Path
    state: dict


def verify_enabled() -> bool:
    return os.environ.get(VERIFY_ENV) == "1"


def _plain(value) -> bool:
    if isinstance(value, tuple):
        return all(_plain(part) for part in value)
    return isinstance(value, _PLAIN)


def _recipe(seed) -> tuple:
    """What one key must keep meaning: where the seed's code is written, and the plain
    values its closure holds.

    A guard, not the key: two calls sharing a key must run the same code on the same
    captured values, or the key is missing a value and the second test would get the
    first test's project. The source location, not the code object, names the code:
    pytest imports a test module as `tests.x` while a sibling's `from x import _seed`
    loads a second copy of it, with its own code objects.
    """
    code = getattr(seed, "__code__", None)
    where = None if code is None else (os.path.normcase(code.co_filename), code.co_firstlineno, code.co_name)
    captured = []
    for cell in getattr(seed, "__closure__", None) or ():
        try:
            value = cell.cell_contents
        except ValueError:  # an empty cell
            continue
        if _plain(value):
            captured.append(value)
    return where, tuple(captured)


class TemplateRegistry:
    """The templates of one session on one worker, under `base` (inside the basetemp).

    `base` may be a callable, resolved on first use, so a session that never builds a
    template never creates (or rotates) a basetemp on its account.
    """

    def __init__(self, base: Path | Callable[[], Path]):
        self._base = base
        self.builds: Counter = Counter()
        self._templates: dict[tuple, Template] = {}
        self._recipes: dict[tuple, tuple] = {}
        self._scratch = 0

    @property
    def base(self) -> Path:
        if callable(self._base):
            self._base = self._base()
        return self._base

    def key(self, recipe: str, template_key: tuple) -> tuple:
        """The registry key: the recipe, the caller's explicit key, and TASKMASTER_TWINS_VERIFY
        (activation runs the carry-over oracle only when it is "1")."""
        if not isinstance(template_key, tuple) or not template_key:
            raise TypeError("template_key must be a non-empty tuple naming the recipe and every value its seed captures")
        if not _plain(template_key):
            raise TypeError("template_key parts must be str/int/float/bool/None or tuples of them, never a code "
                            f"object or function (they hide what the seed captured): {template_key!r}")
        return recipe, template_key, os.environ.get("TASKMASTER_TWINS_VERIFY")

    def directory(self, key: tuple) -> Path:
        return self.base / hashlib.sha256(repr(key).encode("utf-8")).hexdigest()[:16]

    def template(self, key: tuple, build: Callable[[Path], dict], seed=None) -> Template:
        """The template for `key`; `build(directory) -> state` runs on first use only."""
        recipe = _recipe(seed)
        if self._recipes.setdefault(key, recipe) != recipe:
            raise AssertionError(f"template_key {key[1]!r} names two different seeds: put every value the seed "
                                 "captures into the key")
        found = self._templates.get(key)
        if found is None:
            directory = self.directory(key)
            shutil.rmtree(directory, ignore_errors=True)  # left by a build that failed earlier this session
            directory.mkdir(parents=True)
            state = build(directory)
            assert_quiescent(directory)
            found = self._templates[key] = Template(directory, state)
            self.builds[key] += 1
        return found

    def scratch(self, key: tuple) -> Path:
        """An empty directory beside the templates for a verify-mode fresh build."""
        self._scratch += 1
        path = self.base / "verify" / f"{self.directory(key).name}-{self._scratch}"
        path.mkdir(parents=True)
        return path


_ACTIVE: TemplateRegistry | None = None


def activate(base: Path | Callable[[], Path]) -> TemplateRegistry:
    global _ACTIVE
    _ACTIVE = TemplateRegistry(base)
    return _ACTIVE


def deactivate() -> None:
    global _ACTIVE
    _ACTIVE = None


def active() -> TemplateRegistry:
    if _ACTIVE is None:
        raise RuntimeError("no template registry: the session fixture `twin_templates` (tests/conftest.py) "
                           "activates it; import this module as `project_templates`, never `tests.project_templates`")
    return _ACTIVE


def assert_quiescent(directory: Path) -> None:
    """A template holds only closed state that a byte copy cannot tear or mis-attribute."""
    for path in directory.rglob("*"):
        rel = path.relative_to(directory).as_posix()
        if path.name.endswith("-wal") and path.is_file() and path.stat().st_size:
            raise AssertionError(f"template {rel} is a non-empty WAL: a connection was left open")
        if path.name == ".git":
            raise AssertionError(f"template {rel}: Git records absolute paths and inodes; this recipe must stay fresh")


# Activation saves the sync fingerprint cache only for files older than the 2 s racy window
# (cutover.py:666-672, sync_files.py:212,336), so a fresh build has one only when it ran slowly.
# Its entries are keyed by the template's absolute path and inodes (sync_files.py:201-206,379-380)
# and could never match a copy, which therefore gets what a fast fresh build gets: no cache.
FINGERPRINTS = "sync-fingerprints.json"


def _ignored(directory: str, names: list[str]) -> set[str]:
    """Never copied: SQLite shared-memory indexes, half-written temp files, the fingerprint
    cache, and the coordinator's private runtime directory, which belongs to one owner."""
    skipped = {name for name in names if name.endswith("-shm") or ".tmp." in name or name == FINGERPRINTS}
    here = Path(directory)
    if here.name == "local" and here.parent.name == ".taskmaster" and "coordinator" in names:
        skipped.add("coordinator")
    return skipped


def copy_into(template: Template, target: Path) -> None:
    """Every top-level tree of the template into `target`; copy2 keeps mtimes."""
    target.mkdir(parents=True, exist_ok=True)
    for tree in sorted(template.directory.iterdir()):
        shutil.copytree(tree, target / tree.name, ignore=_ignored)


# ── Verify mode: a copy must equal a fresh build, up to run-to-run noise ─────

# Values two fresh builds of one recipe already disagree on (the 2026-09-29 study).
_NOISE = (
    (re.compile(re.escape(socket.gethostname()) + r"-\d+-[0-9a-f]{8}(?::t\d+)?"), "<session>"),
    (re.compile(r"coordinator-[0-9a-f]{16,}[^\s\"',]*"), "<owner>"),
    (re.compile(r"sync-[0-9a-f]{64}"), "sync-<id>"),
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"), "<uuid>"),
    (re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+(?:[+-]\d\d:\d\d|Z)?"), "<wall-clock>"),
    (re.compile(r"\b1[6-9]\d{8}(?:\.\d+)?\b"), "<epoch>"),
    (re.compile(r'("cursor":\s*")[^"]*'), r"\1<cursor>"),
)


def _spellings(own: list, template: Path, others: list = ()) -> list[tuple[str, str]]:
    """Every spelling of each path, longest first: the test's own roots read `<root>`, the
    template `<template>` (a copy that still names its template has diverged), `others` `<other>`."""
    found = {}
    for label, roots in (("<template>", [template]), ("<root>", own), ("<other>", others)):
        for root in roots:
            for path in {root, root.resolve()}:
                text = str(path)
                for spelling in (text, path.as_posix(), os.path.normcase(text), text.replace("\\", "\\\\")):
                    found.setdefault(spelling, label)
    return sorted(found.items(), key=lambda item: len(item[0]), reverse=True)


def _masked(text: str, spellings: list[tuple[str, str]]) -> str:
    for spelling, label in spellings:
        text = text.replace(spelling, label)
    for pattern, label in _NOISE:
        text = pattern.sub(label, text)
    return text


def _dump_db(path: Path, rel: str, spellings: list) -> dict:
    tables = {}
    # immutable=1 reads the checkpointed file and leaves no -wal/-shm behind in the copy.
    with closing(sqlite3.connect(f"file:{path.as_posix()}?immutable=1", uri=True)) as connection:
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for name in names:
            cursor = connection.execute(f'SELECT * FROM "{name}"')
            columns = [column[0] for column in cursor.description]
            rows = []
            for row in cursor:
                values = dict(zip(columns, row))
                if name == "sessions" and "cwd" in values:
                    values["cwd"] = "<cwd>"  # the builder's cwd: a copy carries its template's (harmless, spec 3.3)
                rows.append(_masked(repr(values), spellings))
            tables[f"{rel}#{name}"] = sorted(rows)
    return tables


def _dump(tree: Path, spellings: list) -> dict:
    """`{file or file#table: [masked lines]}` for one project tree."""
    found = {}
    for path in sorted(tree.rglob("*")):
        rel = path.relative_to(tree).as_posix()
        if not path.is_file() or _ignored(str(path.parent), [path.name]) or "/local/coordinator/" in f"/{rel}" \
                or (path.name.endswith("-wal") and not path.stat().st_size):
            continue
        if path.suffix == ".db":
            found.update(_dump_db(path, rel, spellings))
        else:
            found[rel] = _masked(path.read_bytes().decode("utf-8", "replace"), spellings).splitlines()
    return found


def assert_same_build(fresh: Path, copy: Path, template: Template, fresh_state: dict) -> None:
    """Fail unless the copy under `copy` equals the fresh build under `fresh`, file by file
    and table by table, once run-to-run noise and paths are masked."""
    spellings = _spellings([fresh, copy], template.directory)
    differences = []
    if _masked(repr(fresh_state), spellings) != _masked(repr(template.state), spellings):
        differences.append(f"restored state: fresh {fresh_state!r}\n  template {template.state!r}")
    for tree in sorted(child.name for child in template.directory.iterdir()):
        built, copied = _dump(fresh / tree, spellings), _dump(copy / tree, spellings)
        for name in sorted(set(built) | set(copied)):
            if built.get(name) != copied.get(name):
                lines = difflib.unified_diff(built.get(name, ["<absent>"]), copied.get(name, ["<absent>"]),
                                             "fresh", "copy", lineterm="", n=0)
                differences.append(f"{tree}/{name}:\n" + "\n".join(list(lines)[2:42]))
    if differences:
        raise AssertionError(
            "templated copy differs from a fresh build (TASKMASTER_TEMPLATE_VERIFY=1). A difference two fresh "
            "builds also show is noise: add it to project_templates._NOISE with that evidence. Anything else "
            "means this recipe must stay fresh.\n" + "\n\n".join(differences))
```

3b. `tests/native_twins.py:8-9`, before:

```python
from contextlib import closing, contextmanager
import datetime as _datetime
```

after:

```python
from contextlib import closing, contextmanager
from copy import deepcopy
import datetime as _datetime
```

3c. `tests/native_twins.py:328-343`, before:

```python
def make_twins(tmp_path: Path, monkeypatch, seed=None, *, visibility='legacy', engine_oracle=False) -> Twins:
    """Seed a legacy project through the legacy tools, copy it, activate the copy."""
    install_clock(monkeypatch)
    legacy = scaffold(tmp_path / "legacy")
    point_server_at(monkeypatch, legacy)
    bs.backlog_add_epic(epic_id="test-epic", name="Test Epic", done_when="all test tasks complete")
    bs.backlog_add_phase(phase_id="dev", name="Development")
    if seed is not None:
        seed()
    store.reset_for_tests()
    native = tmp_path / "native"
    shutil.copytree(legacy, native)
    for leftover in native.rglob("*.tmp.*"):
        os.remove(leftover)
    activate_native(native)
    return Twins(monkeypatch, legacy, native, visibility=visibility, engine_oracle=engine_oracle)
```

after. The fresh build body is unchanged and moved into `_build_twins`. In verify mode the fresh build runs after the copy (Step 8 relies on that order). `pytest` is imported lazily because `scripts/` import this module outside pytest.

```python
def _build_twins(tmp_path: Path, monkeypatch, seed) -> tuple[Path, Path]:
    """The fresh build: seed a legacy project through the legacy tools, copy it, activate the copy."""
    install_clock(monkeypatch)
    legacy = scaffold(tmp_path / "legacy")
    point_server_at(monkeypatch, legacy)
    bs.backlog_add_epic(epic_id="test-epic", name="Test Epic", done_when="all test tasks complete")
    bs.backlog_add_phase(phase_id="dev", name="Development")
    if seed is not None:
        seed()
    store.reset_for_tests()
    native = tmp_path / "native"
    shutil.copytree(legacy, native)
    for leftover in native.rglob("*.tmp.*"):
        os.remove(leftover)
    activate_native(native)
    return legacy, native


def _twin_template(directory: Path, seed) -> dict:
    """One fresh build under a private patch, closed and undone. Returns the process state
    the build leaves behind, which every copy restores."""
    import pytest  # noqa: PLC0415 - scripts import this module without pytest in play
    from tests.native_coordinator_helpers import close_owned  # noqa: PLC0415
    private = pytest.MonkeyPatch()
    try:
        private.setattr(bs, "_session_task", None)
        private.setattr(bs, "_session_bundle", None)
        _build_twins(directory, private, seed)
        return {"clock": dict(CLOCK), "session_task": deepcopy(bs._session_task),
                "session_bundle": deepcopy(bs._session_bundle)}
    finally:
        close_owned()
        store.reset_for_tests()
        projection.reset_for_tests()
        private.undo()


def _templated_twins(tmp_path: Path, monkeypatch, seed, template_key: tuple) -> tuple[Path, Path]:
    import project_templates  # noqa: PLC0415 - the top-level name `twin_templates` activates
    registry = project_templates.active()
    key = registry.key("make_twins", template_key)
    template = registry.template(key, lambda directory: _twin_template(directory, seed), seed)
    install_clock(monkeypatch)
    project_templates.copy_into(template, tmp_path)
    legacy, native = tmp_path / "legacy", tmp_path / "native"
    point_server_at(monkeypatch, legacy)
    CLOCK.update(template.state["clock"])
    monkeypatch.setattr(bs, "_session_task", deepcopy(template.state["session_task"]))
    monkeypatch.setattr(bs, "_session_bundle", deepcopy(template.state["session_bundle"]))
    if project_templates.verify_enabled():
        fresh = registry.scratch(key)
        fresh_state = _twin_template(fresh, seed)
        CLOCK.update(template.state["clock"])
        project_templates.assert_same_build(fresh, tmp_path, template, fresh_state)
        shutil.rmtree(fresh, ignore_errors=True)
    return legacy, native


def make_twins(tmp_path: Path, monkeypatch, seed=None, *, visibility='legacy', engine_oracle=False,
               template_key: tuple | None = None) -> Twins:
    """Seed a legacy project through the legacy tools, copy it, activate the copy.

    With `template_key` - a tuple naming the recipe and every value its seed captures - the
    pair is built once per worker and this test gets a copy (tests/project_templates.py).
    Only for seeds that never read `tmp_path` or the wall clock, with nothing patched before
    the build, and no Git, sync-fingerprint, crash or real-service dependence (spec 3.3).
    """
    if template_key is None:
        legacy, native = _build_twins(tmp_path, monkeypatch, seed)
    else:
        legacy, native = _templated_twins(tmp_path, monkeypatch, seed, template_key)
    return Twins(monkeypatch, legacy, native, visibility=visibility, engine_oracle=engine_oracle)
```

`close_owned()` closes *every* in-process coordinator. The call sites converted in Tasks 5 and 6 start none before `make_twins`: the autouse `_native_coordinator_isolation` already closed them at setup start, `conftest.py:263`.

3d. `tests/conftest.py`: insert after line 267 (`        close_owned()`, the end of `_native_coordinator_isolation`), before the blank lines that precede `tmp_taskmaster`:

```python


@pytest.fixture(scope="session", autouse=True)
def twin_templates(tmp_path_factory):
    """Per-worker registry of seeded-project templates (tests/project_templates.py).

    Autouse, so whether a `template_key` works never depends on which fixture a worker
    happened to set up first. The basetemp is resolved only when a template is built.
    """
    import project_templates  # noqa: PLC0415 - the top-level name native_twins imports too
    registry = project_templates.activate(lambda: tmp_path_factory.getbasetemp() / "templates")
    try:
        yield registry
    finally:
        project_templates.deactivate()
```

- [ ] **Step 4: Run to verify it passes, plus regressions**

Run: `.venv/Scripts/python.exe -m pytest tests/test_project_templates.py -q -p no:cacheprovider`
Expected: `8 passed`.

Then run the regressions one command at a time. None of them passes a key, so fresh builds must be unchanged:
- `.venv/Scripts/python.exe -m pytest tests/test_native_routing_handovers.py -q -p no:cacheprovider`. Expected: 8 passed. It includes `test_twins_start_without_a_session_bundle_leaked_by_an_earlier_test`, which pins the fresh path's bundle reset.
- `.venv/Scripts/python.exe -m pytest tests/test_native_routing_notes.py -q -p no:cacheprovider`. Expected: 7 passed.
- `.venv/Scripts/python.exe -c "import sys; sys.path[:0] = ['.', 'tests']; import native_twins; assert 'pytest' not in sys.modules"`. Expected: no output. `scripts/*` still import `native_twins` without pytest.

- [ ] **Step 5: Commit**

```bash
git add tests/project_templates.py tests/test_project_templates.py tests/native_twins.py tests/conftest.py
git commit -m "test(speed): per-worker seeded-project templates with a fresh-build verify mode" -m "make_twins(template_key=...) builds a recipe once per xdist worker and copies it (copy2, no -shm/tmp/coordinator/fingerprint cache), restoring CLOCK, _session_task and _session_bundle. TASKMASTER_TEMPLATE_VERIFY=1 also builds fresh and diffs with run-to-run noise masked (spec 3.3).

Co-Authored-By: Claude <noreply@anthropic.com>"
```

- [ ] **Step 6: Write the failing review-focus test (process state after a templated build)**

Append to `tests/test_project_templates.py`:

```python
def _every_kind():
    """Touches every entity family a seed can: the widest net for globals a build might set."""
    _picked()
    bs.backlog_note(action="create", text="seeded")
    bs.backlog_handover_create(tldr="Seeded handover", task_ids=["test-epic-001"])
    bs.backlog_bug_create(title="Seeded bug", found_in="test-epic-001")
    bs.backlog_issue_create(title="Seeded issue", severity="P2", evidence="x", related_tasks=["test-epic-001"])
    bs.backlog_idea_create(title="Seeded idea", body="about ISS-001")
    bs.backlog_decision_create(title="Seeded decision", options=["a", "b"])
    bs.backlog_area_create(area_id="seed-area", name="Seeded area")


def _other_project():
    bs.backlog_add_task(title="Other", epic="test-epic", phase="dev")
    bs.backlog_pick_task(task_id="test-epic-001")


def test_templated_twins_leave_the_module_globals_a_fresh_build_leaves(tmp_path, monkeypatch, twin_templates):
    """Verify mode's file and row diff cannot see module-level state. After another
    project's build (the same pre-state for both), a copy must leave every data global of
    every loaded `taskmaster` module as a fresh build of the same seed leaves it."""
    key = ("test_project_templates.globals", tmp_path.name)
    make_twins(tmp_path / "build", monkeypatch, _every_kind, template_key=key)
    template = twin_templates.directory(twin_templates.key("make_twins", key))
    after = {}
    for run, run_key in (("copy", key), ("fresh", None)):
        make_twins(tmp_path / f"before-{run}", monkeypatch, _other_project)
        make_twins(tmp_path / run, monkeypatch, _every_kind, template_key=run_key)
        after[run] = project_templates.describe_globals([tmp_path / run], template, [tmp_path / f"before-{run}"])
    assert len(after["fresh"]) > 200 and "'Picked'" in after["fresh"]["taskmaster.backlog_server._session_task"]
    assert {name: (after["fresh"].get(name), after["copy"].get(name)) for name in set(after["fresh"]) | set(after["copy"])
            if after["fresh"].get(name) != after["copy"].get(name)} == {}


def test_verify_mode_fails_when_a_fresh_build_sets_a_global_the_copy_does_not(tmp_path, monkeypatch,
                                                                             twin_templates):
    import native_twins
    monkeypatch.setenv(project_templates.VERIFY_ENV, "1")
    key = ("test_project_templates.global", tmp_path.name)
    make_twins(tmp_path / "faithful", monkeypatch, _picked, template_key=key)
    real = native_twins._build_twins

    def build_that_starts_a_viewer(*args):
        built = real(*args)
        monkeypatch.setattr(bs, "_viewer_started", not bs._viewer_started)
        return built

    monkeypatch.setattr(native_twins, "_build_twins", build_that_starts_a_viewer)
    with pytest.raises(AssertionError, match="process global taskmaster.backlog_server._viewer_started"):
        make_twins(tmp_path / "diverged", monkeypatch, _picked, template_key=key)
```

- [ ] **Step 7: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_project_templates.py -q -p no:cacheprovider -k "module_globals or sets_a_global"`
Expected: 2 failed.
- `test_templated_twins_leave_the_module_globals_a_fresh_build_leaves` fails with `AttributeError: module 'project_templates' has no attribute 'describe_globals'`.
- `test_verify_mode_fails_when_a_fresh_build_sets_a_global_the_copy_does_not` fails with `Failed: DID NOT RAISE <class 'AssertionError'>`, because verify mode still compares only files and rows.

- [ ] **Step 8: Implement**

8a. `tests/project_templates.py` imports, before:

```python
from collections import Counter
from contextlib import closing
import difflib
import hashlib
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
from typing import Callable, NamedTuple
```

after:

```python
import __future__
from collections import Counter
from contextlib import closing
import datetime
import difflib
import enum
import functools
import hashlib
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import sys
import threading
import types
from typing import Callable, NamedTuple
```

8b. `tests/project_templates.py`: insert this section between `_dump` and `assert_same_build`:

```python
# ── Process state: module-level data a build can leave behind ────────────────
# Files and rows are not the whole fixture: a build can also set module globals (a latch, a
# cache, a "current task"). These let verify mode and its test compare those too. They only
# read: rolling globals back to a snapshot breaks lazily filled registries (tried while
# drafting: the native router then refused its own tools).

_DATA = (str, bytes, int, float, complex, bool, type(None), Path, datetime.date, datetime.time, datetime.timedelta,
         enum.Enum)
_OPAQUE = (types.ModuleType, types.FunctionType, types.BuiltinFunctionType, types.MethodType, type,
           functools.partial, logging.Logger, threading.Thread, type(threading.Lock()), type(threading.RLock()),
           threading.Event, threading.Condition, threading.Semaphore, threading.local, __future__._Feature)


def _snapshotted(value) -> bool:
    if isinstance(value, _OPAQUE) or (callable(value) and not isinstance(value, (dict, list, set))):
        return False
    return isinstance(value, (_DATA, dict, list, tuple, set, frozenset)) \
        or isinstance(getattr(value, "__dict__", None), dict)


def process_globals() -> dict:
    """`{(module, name): value}`: every module-level data binding (plain data, a container,
    or an object with a `__dict__`) of every loaded `taskmaster` module. Functions, classes,
    modules, locks, loggers, threads and thread-locals are left out."""
    found = {}
    for module_name, module in sorted(sys.modules.items()):
        if module is None or not (module_name == "taskmaster" or module_name.startswith("taskmaster.")):
            continue
        for name, value in list(vars(module).items()):
            if not name.startswith("__") and _snapshotted(value):
                found[(module_name, name)] = value
    return found


def _describe(value, depth: int = 0):
    """A comparable picture: plain data kept, containers walked, an object with a `__dict__`
    shown as its type and fields, anything else by its type name only."""
    if isinstance(value, _DATA):
        return value
    if depth > 6:
        return f"<{type(value).__name__}>"
    if isinstance(value, dict):
        return {repr(_describe(k, depth + 1)): _describe(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_describe(v, depth + 1) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(repr(_describe(v, depth + 1)) for v in value)
    if not isinstance(value, _OPAQUE) and not callable(value) and isinstance(getattr(value, "__dict__", None), dict):
        return type(value).__name__, _describe(vars(value), depth + 1)
    return f"<{type(value).__name__}>"


def describe_globals(own: list, template: Path, others: list = ()) -> dict:
    """`process_globals()` as masked text, masked like the file diff: the test's own project
    roots read `<root>`, the template `<template>`, and any `others` `<other>`."""
    spellings = _spellings(own, template, others)
    return {f"{module}.{name}": _masked(repr(_describe(value)), spellings)
            for (module, name), value in process_globals().items()}
```

8c. `tests/project_templates.py`: replace the whole `assert_same_build`. Before:

```python
def assert_same_build(fresh: Path, copy: Path, template: Template, fresh_state: dict) -> None:
    """Fail unless the copy under `copy` equals the fresh build under `fresh`, file by file
    and table by table, once run-to-run noise and paths are masked."""
    spellings = _spellings([fresh, copy], template.directory)
    differences = []
    if _masked(repr(fresh_state), spellings) != _masked(repr(template.state), spellings):
        differences.append(f"restored state: fresh {fresh_state!r}\n  template {template.state!r}")
    for tree in sorted(child.name for child in template.directory.iterdir()):
        built, copied = _dump(fresh / tree, spellings), _dump(copy / tree, spellings)
        for name in sorted(set(built) | set(copied)):
            if built.get(name) != copied.get(name):
                lines = difflib.unified_diff(built.get(name, ["<absent>"]), copied.get(name, ["<absent>"]),
                                             "fresh", "copy", lineterm="", n=0)
                differences.append(f"{tree}/{name}:\n" + "\n".join(list(lines)[2:42]))
    if differences:
        raise AssertionError(
            "templated copy differs from a fresh build (TASKMASTER_TEMPLATE_VERIFY=1). A difference two fresh "
            "builds also show is noise: add it to project_templates._NOISE with that evidence. Anything else "
            "means this recipe must stay fresh.\n" + "\n\n".join(differences))
```

after:

```python
def assert_same_build(fresh: Path, copy: Path, template: Template, fresh_state: dict,
                      fresh_globals: dict, copy_globals: dict) -> None:
    """Fail unless the copy under `copy` equals the fresh build under `fresh` - file by file,
    table by table and module global by module global - once run-to-run noise and paths are
    masked. The `*_globals` come from `describe_globals()` right after each build."""
    spellings = _spellings([fresh, copy], template.directory)
    differences = []
    if _masked(repr(fresh_state), spellings) != _masked(repr(template.state), spellings):
        differences.append(f"restored state: fresh {fresh_state!r}\n  template {template.state!r}")
    for name in sorted(set(fresh_globals) | set(copy_globals)):
        if fresh_globals.get(name) != copy_globals.get(name):
            differences.append(f"process global {name}:\n  fresh {fresh_globals.get(name, '<absent>')[:600]}"
                               f"\n  copy  {copy_globals.get(name, '<absent>')[:600]}")
    for tree in sorted(child.name for child in template.directory.iterdir()):
        built, copied = _dump(fresh / tree, spellings), _dump(copy / tree, spellings)
        for name in sorted(set(built) | set(copied)):
            if built.get(name) != copied.get(name):
                lines = difflib.unified_diff(built.get(name, ["<absent>"]), copied.get(name, ["<absent>"]),
                                             "fresh", "copy", lineterm="", n=0)
                differences.append(f"{tree}/{name}:\n" + "\n".join(list(lines)[2:42]))
    if differences:
        raise AssertionError(
            "templated copy differs from a fresh build (TASKMASTER_TEMPLATE_VERIFY=1). A difference two fresh "
            "builds also show is noise: add it to project_templates._NOISE with that evidence. A process global "
            "the build sets belongs in the state native_twins._twin_template records and restores. Anything "
            "else means this recipe must stay fresh.\n" + "\n\n".join(differences))
```

8d. `tests/native_twins.py`, the whole `_twin_template`. Before:

```python
def _twin_template(directory: Path, seed) -> dict:
    """One fresh build under a private patch, closed and undone. Returns the process state
    the build leaves behind, which every copy restores."""
    import pytest  # noqa: PLC0415 - scripts import this module without pytest in play
    from tests.native_coordinator_helpers import close_owned  # noqa: PLC0415
    private = pytest.MonkeyPatch()
    try:
        private.setattr(bs, "_session_task", None)
        private.setattr(bs, "_session_bundle", None)
        _build_twins(directory, private, seed)
        return {"clock": dict(CLOCK), "session_task": deepcopy(bs._session_task),
                "session_bundle": deepcopy(bs._session_bundle)}
    finally:
        close_owned()
        store.reset_for_tests()
        projection.reset_for_tests()
        private.undo()
```

after:

```python
def _twin_template(directory: Path, seed, observe=None) -> dict:
    """One fresh build under a private patch, closed and undone. Returns the process state
    the build leaves behind, which every copy restores. `observe()` runs while the build's
    patches are still in place, as a fresh `make_twins` leaves them for its test."""
    import pytest  # noqa: PLC0415 - scripts import this module without pytest in play
    from tests.native_coordinator_helpers import close_owned  # noqa: PLC0415
    private = pytest.MonkeyPatch()
    try:
        private.setattr(bs, "_session_task", None)
        private.setattr(bs, "_session_bundle", None)
        _build_twins(directory, private, seed)
        state = {"clock": dict(CLOCK), "session_task": deepcopy(bs._session_task),
                 "session_bundle": deepcopy(bs._session_bundle)}
        if observe is not None:
            observe()
        return state
    finally:
        close_owned()
        store.reset_for_tests()
        projection.reset_for_tests()
        private.undo()
```

8e. `tests/native_twins.py`, the verify block at the end of `_templated_twins`. Before:

```python
    if project_templates.verify_enabled():
        fresh = registry.scratch(key)
        fresh_state = _twin_template(fresh, seed)
        CLOCK.update(template.state["clock"])
        project_templates.assert_same_build(fresh, tmp_path, template, fresh_state)
        shutil.rmtree(fresh, ignore_errors=True)
```

after:

```python
    if project_templates.verify_enabled():
        # The fresh build starts from the state the copy left, so a module global it sets
        # differently from the copy - or one only the copy set - shows up in the comparison.
        copy_globals = project_templates.describe_globals([tmp_path], template.directory)
        fresh, fresh_globals = registry.scratch(key), {}
        fresh_state = _twin_template(fresh, seed, observe=lambda: fresh_globals.update(
            project_templates.describe_globals([fresh], template.directory)))
        CLOCK.update(template.state["clock"])
        project_templates.assert_same_build(fresh, tmp_path, template, fresh_state, fresh_globals, copy_globals)
        shutil.rmtree(fresh, ignore_errors=True)
```

- [ ] **Step 9: Run to verify it passes, and that the test bites**

- `.venv/Scripts/python.exe -m pytest tests/test_project_templates.py -q -p no:cacheprovider`. Expected: `10 passed`.
- Mutation check, reverted afterwards: delete the line `monkeypatch.setattr(bs, "_session_task", deepcopy(template.state["session_task"]))` in `_templated_twins` and rerun with `-k "module_globals or restore_its_process_state"`. Expected: 2 failed, and the globals diff names `taskmaster.backlog_server._session_task`. Re-add the line, then rerun. Expected: 10 passed.
- Rerun the Step 4 regressions. Expected: unchanged.

If the review-focus test ever reports a global beyond the three already restored:
- A cache keyed by path or inode that differs only in which root it names is a copy-invisible cache, like the fingerprint cache. Skip it with a named exclusion and cite its reader.
- Anything else the build sets goes into the state that `_twin_template` records and `_templated_twins` restores, next to `_session_bundle`.

- [ ] **Step 10: Commit**

```bash
git add tests/project_templates.py tests/test_project_templates.py tests/native_twins.py
git commit -m "test(speed): verify mode and a review test compare module globals after templated builds" -m "Every data global of every loaded taskmaster module must match between a copy and a fresh build of the same seed; verify mode now builds fresh after the copy and diffs the globals too. No further global needed restoring beyond CLOCK, _session_task and _session_bundle.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

---

### Task 5: Template the heaviest twin fixtures

**Files:**
- Modify: `tests/test_native_bypass_gate.py:317` (`rigged`)
- Modify: `tests/test_native_routing_tasks.py:25` (`twins`) and `:246` (`rich`)
- Modify: `tests/test_native_routing_epics_phases.py:33` (`twins`)
- Test: the three files themselves, under `TASKMASTER_TEMPLATE_VERIFY=1`

**Interfaces:**
- Consumes (Task 4): `make_twins(..., template_key=...)`, the autouse `twin_templates` and `TASKMASTER_TEMPLATE_VERIFY`.
- Produces: nothing new.

**Why each conversion is safe.** The spec §3.3 must-stay-fresh list was checked against every test that uses each fixture.

| Fixture | Seed | Git / worktree | Sync fingerprint | Crash / WAL | real_service | Seed reads tmp_path or wall clock | Patched before build |
|---|---|---|---|---|---|---|---|
| `rigged` (`test_native_bypass_gate.py:303-380`) | nested `seed()`, captures nothing (`:306-316`) | none | none | none | none | no (fake clock) | none. Every rig patch runs after `make_twins` (`:317`): `linear.yaml`, `GATE_TOKEN`, `LinearClient`, the forbid and guard patches (`:318-380`). |
| `twins` (`test_native_routing_tasks.py:23-25`) | module `_seed_tasks` (`:14-20`) | none | none | none | none | no | none |
| `rich` (`test_native_routing_tasks.py:244-246`) | module `_seed_rich` (`:219-241`); picks set `_session_task`, which is restored | none | none | none | none | no | none |
| `twins` (`test_native_routing_epics_phases.py:31-33`) | module `_seed` (`:15-28`); a pick sets `_session_task`, which is restored | none | none | none | none | no; `target_date="2030-01-01"` is a constant | none |

Notes on `rigged`:
- `test_store_reading_hooks_never_reach_the_legacy_store_or_scan_the_projection` (`:441-465`) runs `merge_gate_decide.decide()`, which calls `git rev-parse` in the copied root. It expects `ALLOW` because the root is not inside a repository. A copy under `tmp_path` satisfies that exactly as a fresh build does.
- The second `make_twins` in this file (`:484`, one test with its own seed) stays fresh.

Notes on routing tasks: `test_native_no_op_reply_...` (`:290-331`) sets `CLOCK.update(tick=False)` after the fixture. The restored `CLOCK` is the build's own, so it behaves as before.

- [ ] **Step 1: Record setup time before (fresh builds)**

Run each command separately. The pipe prints the pytest summary line and the summed setup time:

```bash
.venv/Scripts/python.exe -m pytest tests/test_native_bypass_gate.py -q -p no:cacheprovider --durations=0 | .venv/Scripts/python.exe -c "import re,sys; L=sys.stdin.read().splitlines(); t=[float(m.group(1)) for m in (re.match(r'\s*([\d.]+)s setup', l) for l in L) if m]; print(L[-1]); print(f'setup {sum(t):.1f}s over {len(t)} rows')"
.venv/Scripts/python.exe -m pytest tests/test_native_routing_tasks.py -q -p no:cacheprovider --durations=0 | .venv/Scripts/python.exe -c "import re,sys; L=sys.stdin.read().splitlines(); t=[float(m.group(1)) for m in (re.match(r'\s*([\d.]+)s setup', l) for l in L) if m]; print(L[-1]); print(f'setup {sum(t):.1f}s over {len(t)} rows')"
.venv/Scripts/python.exe -m pytest tests/test_native_routing_epics_phases.py -q -p no:cacheprovider --durations=0 | .venv/Scripts/python.exe -c "import re,sys; L=sys.stdin.read().splitlines(); t=[float(m.group(1)) for m in (re.match(r'\s*([\d.]+)s setup', l) for l in L) if m]; print(L[-1]); print(f'setup {sum(t):.1f}s over {len(t)} rows')"
```

Expected: all pass. Setup is near the profile's 295 s, 171 s and 159 s, and about 15% lower on a quiet machine. Write down the three pass counts and the three setup numbers.

- [ ] **Step 2: Convert the fixtures**

`tests/test_native_bypass_gate.py:317`, before:

```python
    twins = make_twins(tmp_path, monkeypatch, seed)
```

after (the one inside `rigged`; `:484` is untouched):

```python
    twins = make_twins(tmp_path, monkeypatch, seed, template_key=("test_native_bypass_gate.rigged",))
```

`tests/test_native_routing_tasks.py:25`, before:

```python
    return make_twins(tmp_path, monkeypatch, _seed_tasks)
```

after:

```python
    return make_twins(tmp_path, monkeypatch, _seed_tasks, template_key=("test_native_routing_tasks.twins",))
```

`tests/test_native_routing_tasks.py:246`, before:

```python
    return make_twins(tmp_path, monkeypatch, _seed_rich)
```

after:

```python
    return make_twins(tmp_path, monkeypatch, _seed_rich, template_key=("test_native_routing_tasks.rich",))
```

`tests/test_native_routing_epics_phases.py:33`, before:

```python
    return make_twins(tmp_path, monkeypatch, _seed)
```

after:

```python
    return make_twins(tmp_path, monkeypatch, _seed, template_key=("test_native_routing_epics_phases.twins",))
```

- [ ] **Step 3: Prove copies equal fresh builds (verify mode, single worker)**

Run each command separately:

```bash
TASKMASTER_TEMPLATE_VERIFY=1 .venv/Scripts/python.exe -m pytest tests/test_native_bypass_gate.py -q -p no:cacheprovider
TASKMASTER_TEMPLATE_VERIFY=1 .venv/Scripts/python.exe -m pytest tests/test_native_routing_tasks.py -q -p no:cacheprovider
TASKMASTER_TEMPLATE_VERIFY=1 .venv/Scripts/python.exe -m pytest tests/test_native_routing_epics_phases.py -q -p no:cacheprovider
```

Expected: the same pass counts as Step 1, and no `templated copy differs from a fresh build` error. Each run takes about as long as Step 1, because every test also builds fresh.

If a diff appears:
- Rebuild the recipe fresh twice. If the two fresh builds differ in that same value, it is noise: add a pattern to `_NOISE` in `tests/project_templates.py` with a comment citing the evidence.
- Otherwise revert that fixture to a fresh build and record why in the commit message.

- [ ] **Step 4: Record setup time after**

Run the three Step 1 commands again, without the environment variable.
Expected: the same pass counts. Setup is roughly `rows × 0.08 s + one build per recipe`, which is under 15 s per file.

- [ ] **Step 5: Commit**

```bash
git add tests/test_native_bypass_gate.py tests/test_native_routing_tasks.py tests/test_native_routing_epics_phases.py
git commit -m "test(speed): template the bypass-gate rig and routing task/epic twins" -m "Verified under TASKMASTER_TEMPLATE_VERIFY=1 (single worker, all pass). Setup, single worker, --durations=0: bypass_gate <s1>s -> <s4>s, routing_tasks <s1>s -> <s4>s, epics_phases <s1>s -> <s4>s.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

Replace each `<s1>` and `<s4>` with the numbers measured in Steps 1 and 4.

Expected saving, from the profile (setup rows × (mean − 0.08 s) − three per-worker builds):

| Fixture | Setup rows | Setup total | Saving |
|---|---|---|---|
| `rigged` | 134 | 272.6 s | ≈254 s |
| routing tasks `twins` | 63 | ≈110 s | ≈100 s |
| routing tasks `rich` | 13 | 60.3 s | ≈45 s |
| epics `twins` | 59 | 158.8 s | ≈146 s |

**≈545 worker-seconds** in total.

---

---

### Task 6 — survey and decisions (read before 6a, 6b and 6c)

**Conversion rule.** A call site is converted only when all four of these hold:
1. The mean setup of the tests that use it, from the profile, is **≥ 0.5 s**.
2. The estimated saving is **≥ 5 s**. The estimate is `n·(m − 0.08) − min(n, 3)·m`:
   - `n` is the number of setup rows and `m` their mean.
   - 0.08 s is the measured templated setup per test.
   - `min(n, 3)` is one build per xdist worker at `-n 3`.
   - Below 5 s, the per-worker builds cancel the gain.
3. No test that uses it hits a spec §3.3 must-stay-fresh condition: Git or worktrees, the sync-fingerprint or first-sync cache, crash/WAL/durability children, `real_service_process`, a seed that reads `tmp_path`, the wall clock or the pid, or anything patched before the build.
4. Nothing it requests patches anything before the build.

**The spec's other candidates**

| Candidate | Current setup | Decision |
|---|---|---|
| `root` (`test_native_service.py:17-21`) | 0.64–1.36 s/test | **Template** (Task 6a), except in the 10 files below that must stay fresh. 16 files import `root` (the spec says 12), and `test_native_n13_drift_fixes.py:14` imports only `request`. |
| `build_project` / `project` (`test_native_cutover.py:24-43`) | 72 rows, 0.57 s mean (0.45 median) | **Keep fresh. Ambiguity A1.** `build_project` never installs the fake clock, so its rows carry real wall-clock timestamps from build time. Spec §3.3 says "seeds that use … wall-clock values" must stay fresh, yet lists `build_project` as a candidate. The gain is ≈31 s. No cutover, quiesce, carryover or admission code reads a time window (grep: no `last_seen` or `HEARTBEAT` there), so it could be templated if the owner accepts real-clock rows that age by a few minutes. The crash users (`test_native_cutover_crash.py:45-51`, 66 cases, mostly `release` per §4.2) and `test_native_cutover_quarantine.py:35` / `test_native_first_sync.py:186` stay fresh either way, because of crash children and first-sync behaviour. |
| `tm_epic_phase` (`conftest.py:347-353`) | 197 items, **0.444 s/test vs 0.041 s** for `tmp_taskmaster` alone, so the epic and phase cost ≈0.40 s/test, ≈80 s total | **Keep fresh.** The cost clears the 0.1 s bar, but the fixture fails two must-stay-fresh tests. (1) It never resets the store after seeding, so the test's first tool call continues the builder's `Store` session. A copy would start a second `sessions` row and leave the builder's rows with another session id (`store.py:1211`). (2) It runs on the real clock, and the builder's `last_seen` would age past `HEARTBEAT_WINDOW_SECONDS = 60` (`native/claims.py:69,183-185`). Templating it would change 51 files' session semantics. |

**Survey of every other `make_twins` call site.** It covers 48 files. "fresh" means the site stays a fresh build.

| File | Site (line) | Rows | Setup s/test | Seed shape | Must-stay-fresh / other reason | Decision |
|---|---|---|---|---|---|---|
| test_edit_resurface_hook.py | `_twin` helper, in test bodies (:519) | 6 calls | 0.11 (the build runs at call time) | None | outside the setup rule | fresh; call-time follow-up |
| test_native_blocker_callers.py | `case` (:75), 13 params | 78 | 0.96 | closure over `edit = DEPENDENCY_CASES[request.param][0]` | none | **template** `("test_native_blocker_callers.case", request.param)` (6c) |
| 〃 | `claimed` (:176) | 5 | 1.74 | nested def with `os.getpid()` and `_dead_pid()` (:173-174) | seed reads the pid and probes liveness | fresh |
| 〃 | test body (:226) | 1 | — | writes `bs.SESSION_ID` (:225) | pid in seed; n=1 | fresh |
| test_native_claim_review_d.py | `twins` (:37) and test bodies (:99, :130) | 3 | 0.91 | module `_seed` | n=3 | fresh |
| test_native_claim_terminal_and_readers.py | `twins` (:49) | 11 | 1.21 | module `_seed` (keyword default only) | none | **template** `("test_native_claim_terminal_and_readers.twins",)` (6c) |
| 〃 | test bodies (:105-:249) | 1 each | call time | closures `_stranded(status, holder)` and `_depends(value)` | one use per captured value | fresh |
| test_native_context_compact.py | `twins` (:66) | 10 | 3.37 | nested def, no captures | none | **template** `("test_native_context_compact.twins",)` (6c) |
| test_native_dashboard_reads.py | test bodies (:82-:180) | — (N16, no profile row) | — | `_seed(seed=N)` / `_seed(n_tasks, bugs)` closures | one use per value; no data | fresh |
| test_native_dependency_graph.py | module-scoped `graph` (:96); test body (:450) | 26 / 1 | built once per module already | module functions | already amortised | fresh |
| test_native_export_invisible_edits.py | `twins` (:27) | — (N16) | — | module `_seed` | 4 tests; no data | fresh |
| test_native_graph_repair.py | `twins` (:37) | 17 | 1.03 | module `_seed` | none. Real-clock `graph_checked_at` is stamped by activation, and tests compare it only within one test (:436-461). | **template** `("test_native_graph_repair.twins",)` (6c) |
| 〃 | test body (:277) | 1 | — | `drifted` nested def | n=1 | fresh |
| test_native_linear_bootstrap.py | `twins` (:23) | 20 | 0.47 | lambda | below 0.5 s | fresh |
| test_native_locked_by_contract.py | `twins` (:38) | 14 | 1.08 | nested def, no captures | none | **template** `("test_native_locked_by_contract.twins",)` (6c) |
| test_native_n13_d8_linked_eol.py | `repo` (:31) | 2 | 1.14 | nested def | Git: `init_repo` :33, worktrees :56 | fresh |
| test_native_n13_drift_fixes.py | `repo` (:27) | 4 | 1.36 | nested def | Git (:29, :43-48, :110) | fresh |
| test_native_n13_rehearsal_fixes.py | `bug_root` :85, `epic_root` :128, `task_root` :242, `handover_root` :313 | 1 / 6 / 4 / 1 | 0.77 / 0.73 / 1.01 / 2.40 | lambdas / nested def | each estimated below 5 s | fresh |
| test_native_progress.py | `twins` (:85) | 24 | 1.92 | module `_seed` | **crash**: `os._exit` children open the store (:281-297, :310, :371) | fresh (**A2**) |
| 〃 | `history` (:90) | 3 | 2.73 | module `_seed_with_history` | crash file; n=3 | fresh |
| test_native_projection.py | `twins` (:24), `engine_oracle=True` | 28 | 0.70 | module `_seed` | none (only in-process `KeyboardInterrupt`) | **template** `("test_native_projection.twins",)` (6c) |
| test_native_projection_drain.py | `twins` (:27) | 6 | 0.73 | module `_seed` | none | fresh (≈1.7 s) |
| test_native_projection_faults.py | `twins` (:46) | 19 | 0.87 | module `_seed` | **crash**: `os._exit` children (:127-164, :206-214) and a two-process writer (:427-430) | fresh (**A2**) |
| test_native_projection_resolve.py | `twins` (:25) | 5 | 0.67 | module `_seed` | none | fresh (≈0.9 s) |
| test_native_projection_review.py | `twins` (:44) | 17 | 1.10 | module `_seed` | **crash** children (:108-137, :162) | fresh (**A2**) |
| test_native_projection_review2.py | `twins` (:41) | 23 | 0.72 | module `_seed` | **crash** children via `_kill` (:200-230), 16 items | fresh (**A2**) |
| test_native_projection_today.py | `twins` (:24) | 5 | 0.98 | module `_seed` | none | fresh (≈1.6 s) |
| test_native_resolve_take_file.py | `twins` (:27) | 9 | 0.77 | module `_seed` | none | fresh (≈3.9 s) |
| test_native_resync.py | `twins` (:28); test body (:124) | 7 / 1 | 2.27, but two post-build resync calls are most of it | module `_seed` | none | fresh (≈4 s, because the post-build work cannot be templated) |
| test_native_routing_batch.py | `twins` (:27) | 6 | 1.95 | module `_seed` | none | **template** `("test_native_routing_batch.twins",)` (6b) |
| test_native_routing_changes.py | `twins` (:20) | 15 | 0.60 | nested def, no captures | none. The restore test (:179-203) backs up the copy's own `store.db`. The cursor test (:81-87) needs legacy and native to share a `creation_token`, and one build copies both. | **template** `("test_native_routing_changes.twins",)` (6b) |
| 〃 | `epic_twins` (:144) | 2 | 0.95 | nested def | n=2 | fresh |
| test_native_routing_claims.py | `twins` (:37) | 16 | 1.16 | module `_seed` | none. The race test (:265-296) opens its own connections after the build. | **template** `("test_native_routing_claims.twins",)` (6b) |
| 〃 | `_handed_over` helper (:183) | 2 | call time | closure over `holder`, and a pick stamps `SESSION_ID` | one use per holder | fresh |
| test_native_routing_context.py | `twins` (:26) | 25 | 1.03 | nested def (module constants only) | none | **template** `("test_native_routing_context.twins",)` (6b) |
| 〃 | `claimed` (:243) | 5 | 0.74 | `os.getpid()` and `_dead_pid()` in the seed (:241-242) | seed reads the pid and probes liveness | fresh |
| 〃 | `hand_edited` (:283) | 2 | 0.77 | nested def | n=2 | fresh |
| test_native_routing_context_invariant.py | test body (:368) | 5 | 0.01 (call time) | closure over an rng-derived plan | one use per seed; `release` tier (§4.2) | fresh |
| test_native_routing_context_paging.py | `twins` (:38) | 2 | 2.63 | nested def | n=2; `release` tier (§4.2) | fresh |
| test_native_routing_documents.py | `twins` (:37) | 24 | 1.13 | module `_seed_documents`, which writes `docs/spec.md` relative to the cwd, i.e. the build's own legacy root, copied with it | none | **template** `("test_native_routing_documents.twins",)` (6b) |
| test_native_routing_handovers.py | `twins` (:28) | 7 | 1.18 | module `_seed` | none | fresh (≈4.2 s) |
| 〃 | test body (:126) | 1 | — | lambda | patches `_session_bundle` before the build, because it tests `make_twins` itself | fresh |
| test_native_routing_hooks.py | `twins` (:43) | 10 | 3.23, of which ≈1.2 s is `make_twins`; the rest is 5 post-build `twins.same` | module `_seed` | none. The hooks `git rev-parse` the copied root after the build and expect no repository; a copy under `tmp_path` is the same. | **template** `("test_native_routing_hooks.twins",)` (6b) |
| test_native_routing_links_areas.py | `twins` (:28) | 8 | 1.67 | module `_seed` | none | **template** `("test_native_routing_links_areas.twins",)` (6b) |
| test_native_routing_notes.py | `twins` (:20) | 7 | 0.81 | nested def | none | fresh (≈2.7 s) |
| test_native_routing_overview.py | `twins` (:40) | 11 | 1.62 | module `_seed` | none | **template** `("test_native_routing_overview.twins",)` (6b) |
| 〃 | test body (:189) | 1 | — | module `_seed_validation_findings` | n=1 | fresh |
| test_native_routing_records.py | `twins` (:28) | 7 | 1.71 | module `_seed` | none | **template** `("test_native_routing_records.twins",)` (6b) |
| test_native_routing_refusals.py | `twins` (:33) | 7 | 1.02 | nested def | none | fresh (≈3.5 s) |
| test_native_routing_viewer.py | `twins` (:43) | 5 | 2.79 | module `_seed`, shared by the four viewer files below | none | **template** `SEED_KEY = ("test_native_routing_viewer._seed",)` (6c) |
| test_native_sync_base.py | `twins` (:13) | 2 | 0.76 | lambda | n=2 | fresh |
| test_native_sync_batched.py | `repo` (:31), test body (:259) | — (N16) | — | nested def | Git (:18, :164-198) | fresh |
| test_native_sync_commands.py | `twins` (:16-18), `engine_oracle=True` | 19 | 0.77 | lambda, no captures | none | **template** `("test_native_sync_commands.twins",)` (6c) |
| test_native_sync_perf.py | `repo` (:165) | 8 | 2.07 | nested def | Git (:167, :230, :256) and fingerprint read counts (:171-192, :241, :262) | fresh |
| test_native_sync_prepare.py | `twins` (:14) | 6 | 0.81 | lambda | none | fresh (≈2.0 s) |
| test_viewer_board.py | `twins` (:14) | 5 | 3.12 | imported `_seed` | none | **template** `SEED_KEY` (6c) |
| test_viewer_board_detail.py | `twins` (:13) | 4 | 3.29 | imported `_seed` | none | **template** `SEED_KEY` (6c) |
| test_viewer_board_oracles.py | `twins` (:16) | 15 | 3.72 | imported `_seed` | none. `CLOCK` += 365 days and the `SESSION_ID`/`VERSION` patches happen after the build. | **template** `SEED_KEY` (6c) |
| test_viewer_n10_today.py | `twins` (:12) | 2 | 3.35 | imported `_seed` | none | **template** `SEED_KEY` (6c) |

**Ambiguity A2.** The spec's rule reads "crash, WAL and durability tests *that depend on the build's own files or handles*". The crash children in `progress`, `projection_faults`, `projection_review` and `projection_review2` open the test's *copied*, closed store after the build. They do not use the build's WAL or handles, so under that qualifier they could be templated, for ≈98 s. This plan keeps them fresh until the spec owner rules.

**Expected saving:**
- Task 6a (`root`): ≈62 s.
- Task 6b: ≈101 s, split as batch 5.4, changes 6.0, claims 13.8, context 20.7, documents 21.8, hooks 7.6, links_areas 7.7, overview 12.1, records 6.3.
- Task 6c: ≈206 s, split as projection 15.3, sync_commands 10.8, context_compact 22.8, graph_repair 13.1, locked_by_contract 10.8, claim_terminal 8.8, blocker `case` 31.2, viewer group 92.8.
- **Total ≈369 worker-seconds.**

---

---

### Task 6a: Template `root`, with a per-file freshness flag

**Files:**
- Modify: `tests/test_native_service.py:17-21` (`root`)
- Modify, adding the `ROOT_MUST_STAY_FRESH` constant after the imports:
  - `tests/test_native_first_sync.py:25-26`
  - `tests/test_native_git_checkouts.py:19-22`
  - `tests/test_native_git_checkouts_review.py:15-18`
  - `tests/test_native_git_crash.py:22-25`
  - `tests/test_native_git_generation.py:18-22`
  - `tests/test_native_git_hook.py:17-19`
  - `tests/test_native_git_managed.py:15-17`
  - `tests/test_native_metrics.py:193`
  - `tests/test_native_service_process.py:13-16`
  - `tests/test_native_sync_budget.py:13-16`
- Test: `tests/test_project_templates.py`, one new test appended

**Interfaces:**
- Consumes (Task 4): `make_twins(..., template_key=...)`, `project_templates.copy_into` and the autouse `twin_templates`.
- Produces:
  - `root` reads the requesting module's `ROOT_MUST_STAY_FRESH`. If it is truthy, the build is fresh and the value is the reason.
  - `test_native_service._seed_root()`.

**Design.** The alternative, a `fresh_root` fixture, would rename the fixture argument in ≈160 test signatures across 10 files. A module constant keeps every signature. It is one greppable line per file and states the reason. `request.module` is the *requesting* test's module, so `root` imported into a git file sees that file's constant.

The 16 files that import `root` and the per-file decision. Conditions are from a line-cited survey of every test that uses `root`.

| File | Tests using `root` | Decision |
|---|---|---|
| test_native_service.py | 28/28 | template. Identity tests (:222-275) rewrite the test's own `store_id`. |
| test_native_service_adapter.py | 4/4 | template |
| test_native_service_linear.py | 14/14 | template. The autouse `no_real_linear` (:17-21) patches `LinearClient` before the build, but the build never constructs one, so any test's template is identical. The queue is empty after the build either way. |
| test_native_service_progress.py | 4/8 | template |
| test_native_service_routing.py | 7/7 | template. The check at :24 that `local/coordinator/discovery.json` is absent holds, because copies skip `local/coordinator/`. |
| test_native_service_sync.py | 26/31 | template. The first syncs assert no fingerprints. Junction and symlink tests (:216-254) change the copy after the fixture. |
| test_native_metrics_replies.py | 1/2 | template (it shares the same per-worker template) |
| test_native_first_sync.py | 13/21 | **fresh**: Git (:206, :231) and first-sync observation counts (:223-252) |
| test_native_git_checkouts.py | 20/22 | **fresh**: Git and linked worktrees |
| test_native_git_checkouts_review.py | 24/24 | **fresh**: Git, worktrees and submodules (:315-343) |
| test_native_git_crash.py | 13/13 | **fresh**: Git, killed `real_service_process` coordinators |
| test_native_git_generation.py | 4/6 | **fresh**: Git, and `age_projection` plus warm fingerprint reads of the built files (:48-54, :102-112) |
| test_native_git_hook.py | 3/3 | **fresh**: Git hooks |
| test_native_git_managed.py | 24/24 | **fresh**: Git |
| test_native_metrics.py | 4/22 | **fresh**: fingerprint-cache hit and miss counts over the built files (:196-214) |
| test_native_service_process.py | 3/3 | **fresh**: `real_service_process`, killed owners (:72, :122) |
| test_native_sync_budget.py | 4/4 | **fresh**: Git via the imported `repo` |

- [ ] **Step 1: Write the failing test**

Append to `tests/test_project_templates.py`, adding the import next to the others at the top of the file:

```python
from test_native_service import root  # noqa: F401  (the fixture under test)
```

```python
@pytest.mark.parametrize("reason", [None, "git (this test)"])
def test_root_is_templated_unless_its_module_names_why_it_must_stay_fresh(request, monkeypatch, reason):
    if reason is not None:
        monkeypatch.setattr(request.module, "ROOT_MUST_STAY_FRESH", reason, raising=False)
    copied, real = [], project_templates.copy_into
    monkeypatch.setattr(project_templates, "copy_into", lambda template, target: (copied.append(target),
                                                                                  real(template, target))[1])
    native = request.getfixturevalue("root")
    assert (native / ".taskmaster" / "local" / "store.db").is_file()
    assert copied == ([] if reason else [native.parent])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_project_templates.py -q -p no:cacheprovider -k root_is_templated`
Expected: `[None]` FAILS with `assert [] == [WindowsPath(...)]`, because `root` still builds fresh. `[git (this test)]` passes.

- [ ] **Step 3: Implement**

`tests/test_native_service.py:17-21`, before:

```python
@pytest.fixture
def root(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, lambda: bs.backlog_add_task(title='Service task', epic='test-epic', phase='dev'), visibility=None)
    with twins.at(twins.native):
        yield twins.native
```

after:

```python
def _seed_root():
    bs.backlog_add_task(title='Service task', epic='test-epic', phase='dev')


@pytest.fixture
def root(request, tmp_path, monkeypatch):
    """The seeded native twin, copied from a per-worker template unless the requesting
    module sets `ROOT_MUST_STAY_FRESH` to the reason it needs a from-scratch build (spec 3.3)."""
    fresh = getattr(request.module, "ROOT_MUST_STAY_FRESH", None)
    twins = make_twins(tmp_path, monkeypatch, _seed_root, visibility=None,
                       template_key=None if fresh else ("test_native_service.root",))
    with twins.at(twins.native):
        yield twins.native
```

Add the flags. Each before → after below inserts one line; the existing lines are unchanged.

`tests/test_native_first_sync.py:25-28`:

```python
from test_native_service import root, request  # noqa: F401
from test_native_service_sync import REL, edit, title

TAMPERED = "tasks/cut-epic-002.md"
```
→
```python
from test_native_service import root, request  # noqa: F401
from test_native_service_sync import REL, edit, title

ROOT_MUST_STAY_FRESH = "Git repos, and first-sync observation counts over the files the build wrote (spec 3.3)"
TAMPERED = "tasks/cut-epic-002.md"
```

`tests/test_native_git_checkouts.py:19-22`:

```python
from test_native_service import root, request  # noqa: F401
from test_native_service_sync import title

pytestmark = pytest.mark.xdist_group('heavy_processes')
```
→
```python
from test_native_service import root, request  # noqa: F401
from test_native_service_sync import title

ROOT_MUST_STAY_FRESH = "Git repos and linked worktrees record absolute paths and inodes (spec 3.3)"
pytestmark = pytest.mark.xdist_group('heavy_processes')
```

`tests/test_native_git_checkouts_review.py:15-18`: the same lines as `test_native_git_checkouts.py`. Insert:

```python
ROOT_MUST_STAY_FRESH = "Git repos, linked worktrees and submodules record absolute paths and inodes (spec 3.3)"
```

before `pytestmark = pytest.mark.xdist_group('heavy_processes')`.

`tests/test_native_git_crash.py:22-25`:

```python
from test_native_service import root, request  # noqa: F401
from test_native_service_process import ready

pytestmark = [pytest.mark.skipif(sys.platform != 'win32', reason='Windows job boundary'),
```
→ insert before `pytestmark = [`:
```python
ROOT_MUST_STAY_FRESH = "Git repos, and real service processes killed while holding the store (spec 3.3)"
```

`tests/test_native_git_generation.py:18-22`: insert before `pytestmark = [pytest.mark.xdist_group('heavy_processes'), pytest.mark.allow_projection_bypass]`:

```python
ROOT_MUST_STAY_FRESH = "Git repos, and warm fingerprint-cache reads of the files the build wrote (spec 3.3)"
```

`tests/test_native_git_hook.py:17-19`: insert before `pytestmark = pytest.mark.xdist_group('heavy_processes')`:

```python
ROOT_MUST_STAY_FRESH = "Git repos and hooks (spec 3.3)"
```

`tests/test_native_git_managed.py:15-17`: insert before `pytestmark = pytest.mark.xdist_group('heavy_processes')`:

```python
ROOT_MUST_STAY_FRESH = "Git repos (spec 3.3)"
```

`tests/test_native_metrics.py:193`. The import sits mid-file, so the constant goes directly after it:

```python
from test_native_service import root, request  # noqa: E402,F401
```
→
```python
from test_native_service import root, request  # noqa: E402,F401
ROOT_MUST_STAY_FRESH = "fingerprint-cache hit and miss counts over the files the build wrote (spec 3.3)"
```

`tests/test_native_service_process.py:13-16`: insert before `# The process boundary is what this module proves (conftest refuses unmarked launches).`:

```python
ROOT_MUST_STAY_FRESH = "real_service_process: coordinator processes are killed while holding the store (spec 3.3)"
```

`tests/test_native_sync_budget.py:13-16`: insert before `pytestmark = pytest.mark.xdist_group('heavy_processes')`:

```python
ROOT_MUST_STAY_FRESH = "Git repos via the imported `repo` fixture (spec 3.3)"
```

- [ ] **Step 4: Run to verify it passes, plus verify mode, plus timing**

- `.venv/Scripts/python.exe -m pytest tests/test_project_templates.py -q -p no:cacheprovider`. Expected: `12 passed`.
- Every flagged file still collects: `.venv/Scripts/python.exe -m pytest --collect-only -q -p no:cacheprovider tests/test_native_first_sync.py tests/test_native_git_checkouts.py tests/test_native_git_checkouts_review.py tests/test_native_git_crash.py tests/test_native_git_generation.py tests/test_native_git_hook.py tests/test_native_git_managed.py tests/test_native_metrics.py tests/test_native_service_process.py tests/test_native_sync_budget.py`. Expected: no errors, and the same item counts as before the change.
- Two flagged files as a smoke test, one at a time:
  - `.venv/Scripts/python.exe -m pytest tests/test_native_git_hook.py -q -p no:cacheprovider`. Expected: 3 passed.
  - `.venv/Scripts/python.exe -m pytest tests/test_native_service_process.py -q -p no:cacheprovider`. Expected: 3 passed.
- The templated files. Before editing (`git stash` the Step 3 edits, or run this at the task's start), and again after, run the timing one-liner from Task 5 Step 1 on each file. Then run verify mode, one file at a time:

```bash
for f in tests/test_native_service.py tests/test_native_service_adapter.py tests/test_native_service_linear.py tests/test_native_service_progress.py tests/test_native_service_routing.py tests/test_native_service_sync.py tests/test_native_metrics_replies.py; do
  echo "== $f"; TASKMASTER_TEMPLATE_VERIFY=1 .venv/Scripts/python.exe -m pytest "$f" -q -p no:cacheprovider
done
```

Expected: every file passes with the same counts as before the change, with no verify diff. Setup for `tests/test_native_service.py` drops from about 18 s to about 2.3 s (measured while drafting).

- [ ] **Step 5: Commit**

```bash
git add tests/test_native_service.py tests/test_project_templates.py tests/test_native_first_sync.py tests/test_native_git_checkouts.py tests/test_native_git_checkouts_review.py tests/test_native_git_crash.py tests/test_native_git_generation.py tests/test_native_git_hook.py tests/test_native_git_managed.py tests/test_native_metrics.py tests/test_native_service_process.py tests/test_native_sync_budget.py
git commit -m "test(speed): template the native service root; Git, fingerprint and real-process files keep a fresh build" -m "ROOT_MUST_STAY_FRESH names why a module needs a from-scratch root (spec 3.3). Verified under TASKMASTER_TEMPLATE_VERIFY=1 for the 7 templated files. Setup: test_native_service <before>s -> <after>s (single worker).

Co-Authored-By: Claude <noreply@anthropic.com>"
```

Fill in `<before>` and `<after>` from Step 4.

---

---

### Task 6b: Template the routing twin fixtures

**Files:**
- Modify:
  - `tests/test_native_routing_batch.py:27`
  - `tests/test_native_routing_changes.py:20`
  - `tests/test_native_routing_claims.py:37`
  - `tests/test_native_routing_context.py:26`
  - `tests/test_native_routing_documents.py:37`
  - `tests/test_native_routing_hooks.py:43`
  - `tests/test_native_routing_links_areas.py:28`
  - `tests/test_native_routing_overview.py:40`
  - `tests/test_native_routing_records.py:28`
- Test: the nine files, under `TASKMASTER_TEMPLATE_VERIFY=1`

**Interfaces:**
- Consumes (Task 4): `make_twins(..., template_key=...)`.
- Produces: nothing.

The survey above gives each site's seed shape and fresh-only check. None of them patches anything before the build (only the conftest autouse fixtures run first), and none has a Git, fingerprint, crash or real-service user.

- [ ] **Step 1: Record setup time before**

```bash
for f in tests/test_native_routing_batch.py tests/test_native_routing_changes.py tests/test_native_routing_claims.py tests/test_native_routing_context.py tests/test_native_routing_documents.py tests/test_native_routing_hooks.py tests/test_native_routing_links_areas.py tests/test_native_routing_overview.py tests/test_native_routing_records.py; do
  echo "== $f"; .venv/Scripts/python.exe -m pytest "$f" -q -p no:cacheprovider --durations=0 | .venv/Scripts/python.exe -c "import re,sys; L=sys.stdin.read().splitlines(); t=[float(m.group(1)) for m in (re.match(r'\s*([\d.]+)s setup', l) for l in L) if m]; print(L[-1]); print(f'setup {sum(t):.1f}s over {len(t)} rows')"
done
```

Expected: every file passes. Write down each pass count and setup total.

- [ ] **Step 2: Convert.** Each edit changes exactly one line. For files that contain the same text more than once, the anchor line above it is shown.

| File:line | Before | After |
|---|---|---|
| test_native_routing_batch.py:27 | `    return make_twins(tmp_path, monkeypatch, _seed)` | `    return make_twins(tmp_path, monkeypatch, _seed, template_key=("test_native_routing_batch.twins",))` |
| test_native_routing_changes.py:20 (after `        bs.backlog_note(action="create", text="Seeded note", pinned=False)`; `:144` is untouched) | `    return make_twins(tmp_path, monkeypatch, seed)` | `    return make_twins(tmp_path, monkeypatch, seed, template_key=("test_native_routing_changes.twins",))` |
| test_native_routing_claims.py:37 | `    return make_twins(tmp_path, monkeypatch, _seed)` | `    return make_twins(tmp_path, monkeypatch, _seed, template_key=("test_native_routing_claims.twins",))` |
| test_native_routing_context.py:26 (after `        bs.backlog_note(action="create", text="A pinned orientation note", pinned=True)`; `:243` and `:283` are untouched) | `    return make_twins(tmp_path, monkeypatch, seed)` | `    return make_twins(tmp_path, monkeypatch, seed, template_key=("test_native_routing_context.twins",))` |
| test_native_routing_documents.py:37 | `    return make_twins(tmp_path, monkeypatch, _seed_documents)` | `    return make_twins(tmp_path, monkeypatch, _seed_documents, template_key=("test_native_routing_documents.twins",))` |
| test_native_routing_hooks.py:43 | `    twins = make_twins(tmp_path, monkeypatch, _seed)` | `    twins = make_twins(tmp_path, monkeypatch, _seed, template_key=("test_native_routing_hooks.twins",))` |
| test_native_routing_links_areas.py:28 | `    return make_twins(tmp_path, monkeypatch, _seed)` | `    return make_twins(tmp_path, monkeypatch, _seed, template_key=("test_native_routing_links_areas.twins",))` |
| test_native_routing_overview.py:40 (`:189` is untouched) | `    return make_twins(tmp_path, monkeypatch, _seed)` | `    return make_twins(tmp_path, monkeypatch, _seed, template_key=("test_native_routing_overview.twins",))` |
| test_native_routing_records.py:28 | `    return make_twins(tmp_path, monkeypatch, _seed)` | `    return make_twins(tmp_path, monkeypatch, _seed, template_key=("test_native_routing_records.twins",))` |

- [ ] **Step 3: Verify mode, single worker, one file at a time**

```bash
for f in tests/test_native_routing_batch.py tests/test_native_routing_changes.py tests/test_native_routing_claims.py tests/test_native_routing_context.py tests/test_native_routing_documents.py tests/test_native_routing_hooks.py tests/test_native_routing_links_areas.py tests/test_native_routing_overview.py tests/test_native_routing_records.py; do
  echo "== $f"; TASKMASTER_TEMPLATE_VERIFY=1 .venv/Scripts/python.exe -m pytest "$f" -q -p no:cacheprovider
done
```

Expected: the Step 1 pass counts and no verify diff. On a diff, follow Task 5 Step 3: if it is noise, add a mask with evidence; otherwise revert that file's line and note it.

- [ ] **Step 4: Record setup time after.** Rerun the Step 1 loop. Expected: the same pass counts, and each file's setup well under half its Step 1 value. `routing_hooks` keeps about 2 s per test of post-build `twins.same`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_native_routing_batch.py tests/test_native_routing_changes.py tests/test_native_routing_claims.py tests/test_native_routing_context.py tests/test_native_routing_documents.py tests/test_native_routing_hooks.py tests/test_native_routing_links_areas.py tests/test_native_routing_overview.py tests/test_native_routing_records.py
git commit -m "test(speed): template nine routing twin fixtures" -m "Each passes under TASKMASTER_TEMPLATE_VERIFY=1 (single worker). Setup before -> after per file: <from Steps 1 and 4>.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

---

### Task 6c: Template the projection, sync, graph, claim, blocker and viewer fixtures

**Files:**
- Modify:
  - `tests/test_native_projection.py:24`
  - `tests/test_native_sync_commands.py:16-18`
  - `tests/test_native_context_compact.py:66`
  - `tests/test_native_graph_repair.py:37`
  - `tests/test_native_locked_by_contract.py:38`
  - `tests/test_native_claim_terminal_and_readers.py:49`
  - `tests/test_native_blocker_callers.py:75`
  - `tests/test_native_routing_viewer.py:38-43`
  - `tests/test_viewer_board.py:9,14`
  - `tests/test_viewer_board_detail.py:8,13`
  - `tests/test_viewer_board_oracles.py:11,16`
  - `tests/test_viewer_n10_today.py:7,12`
- Test: the twelve files, under `TASKMASTER_TEMPLATE_VERIFY=1`

**Interfaces:**
- Consumes (Task 4): `make_twins(..., template_key=...)`.
- Produces: `test_native_routing_viewer.SEED_KEY = ("test_native_routing_viewer._seed",)`. One key covers the five fixtures that seed with that module's `_seed`, so each worker builds it once for all 31 tests.

Why the shared key and the parametrized key are safe:
- The recipe guard (Task 4) compares the seed's source location. The two module copies of `test_native_routing_viewer` (`tests.test_native_routing_viewer` under pytest, and top-level `test_native_routing_viewer` via the siblings' imports) therefore share it.
- `blocker_callers.case` captures a lambda (`edit`) that no key can hold. Its key carries `request.param`, the `DEPENDENCY_CASES` name, so there is one template per case. All 13 cases passed verify mode while drafting.

- [ ] **Step 1: Record setup time before.** Run the Task 6b Step 1 loop over:

```
tests/test_native_projection.py tests/test_native_sync_commands.py tests/test_native_context_compact.py tests/test_native_graph_repair.py tests/test_native_locked_by_contract.py tests/test_native_claim_terminal_and_readers.py tests/test_native_blocker_callers.py tests/test_native_routing_viewer.py tests/test_viewer_board.py tests/test_viewer_board_detail.py tests/test_viewer_board_oracles.py tests/test_viewer_n10_today.py
```

Expected: every file passes. Write down the counts and setup totals.

- [ ] **Step 2: Convert**

| File:line | Before | After |
|---|---|---|
| test_native_projection.py:24 | `    return make_twins(tmp_path, monkeypatch, _seed, engine_oracle=True)` | `    return make_twins(tmp_path, monkeypatch, _seed, engine_oracle=True, template_key=("test_native_projection.twins",))` |
| test_native_sync_commands.py:18 (end of the call that starts at `:16`) | `                      engine_oracle=True)` | `                      engine_oracle=True, template_key=("test_native_sync_commands.twins",))` |
| test_native_context_compact.py:66 | `    return make_twins(tmp_path, monkeypatch, seed)` | `    return make_twins(tmp_path, monkeypatch, seed, template_key=("test_native_context_compact.twins",))` |
| test_native_graph_repair.py:37 (`:277` is untouched) | `    return make_twins(tmp_path, monkeypatch, _seed)` | `    return make_twins(tmp_path, monkeypatch, _seed, template_key=("test_native_graph_repair.twins",))` |
| test_native_locked_by_contract.py:38 | `    return make_twins(tmp_path, monkeypatch, seed)` | `    return make_twins(tmp_path, monkeypatch, seed, template_key=("test_native_locked_by_contract.twins",))` |
| test_native_claim_terminal_and_readers.py:49 | `    return make_twins(tmp_path, monkeypatch, _seed)` | `    return make_twins(tmp_path, monkeypatch, _seed, template_key=("test_native_claim_terminal_and_readers.twins",))` |
`tests/test_native_blocker_callers.py:75`, before:

```python
    return make_twins(tmp_path, monkeypatch, seed), blocked, request.param
```

after:

```python
    return (make_twins(tmp_path, monkeypatch, seed, template_key=("test_native_blocker_callers.case", request.param)),
            blocked, request.param)
```

The viewer group follows.

`tests/test_native_routing_viewer.py:38-43`, before:

```python
    bs.backlog_handover_create(tldr="Handover for board task", task_ids=["test-epic-001"], thread="line")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)
```

after:

```python
    bs.backlog_handover_create(tldr="Handover for board task", task_ids=["test-epic-001"], thread="line")


# One template per worker for every fixture seeded with `_seed` (this file and the viewer board files).
SEED_KEY = ("test_native_routing_viewer._seed",)


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed, template_key=SEED_KEY)
```

The four sibling files each get one import change and one fixture change.

| File:line | Before | After |
|---|---|---|
| test_viewer_board.py:9 | `from test_native_routing_viewer import _seed, _serve` | `from test_native_routing_viewer import SEED_KEY, _seed, _serve` |
| test_viewer_board.py:14 | `    return make_twins(tmp_path, monkeypatch, _seed)` | `    return make_twins(tmp_path, monkeypatch, _seed, template_key=SEED_KEY)` |
| test_viewer_board_detail.py:8 | `from test_native_routing_viewer import _seed, _serve` | `from test_native_routing_viewer import SEED_KEY, _seed, _serve` |
| test_viewer_board_detail.py:13 | `    return make_twins(tmp_path, monkeypatch, _seed)` | `    return make_twins(tmp_path, monkeypatch, _seed, template_key=SEED_KEY)` |
| test_viewer_board_oracles.py:11 | `from test_native_routing_viewer import _seed` | `from test_native_routing_viewer import SEED_KEY, _seed` |
| test_viewer_board_oracles.py:16 | `    return make_twins(tmp_path, monkeypatch, _seed)` | `    return make_twins(tmp_path, monkeypatch, _seed, template_key=SEED_KEY)` |
| test_viewer_n10_today.py:7 | `from test_native_routing_viewer import _seed, _serve` | `from test_native_routing_viewer import SEED_KEY, _seed, _serve` |
| test_viewer_n10_today.py:12 | `    return make_twins(tmp_path, monkeypatch, _seed)` | `    return make_twins(tmp_path, monkeypatch, _seed, template_key=SEED_KEY)` |

- [ ] **Step 3: Verify mode, single worker, one file at a time.** Run the Task 6b Step 3 loop over the twelve files from Step 1. Then run the shared key across two module copies in one session:

```bash
TASKMASTER_TEMPLATE_VERIFY=1 .venv/Scripts/python.exe -m pytest tests/test_native_routing_viewer.py tests/test_viewer_n10_today.py -q -p no:cacheprovider
```

Expected: the Step 1 pass counts, no verify diff, and no `names two different seeds` from the two-file run. The two files hold 7 tests.

- [ ] **Step 4: Record setup time after.** Rerun the Step 1 loop. Expected: the same pass counts. The five viewer files drop from about 3.3 s to about 0.1 s of setup per test, apart from one build per session.

- [ ] **Step 5: Commit**

```bash
git add tests/test_native_projection.py tests/test_native_sync_commands.py tests/test_native_context_compact.py tests/test_native_graph_repair.py tests/test_native_locked_by_contract.py tests/test_native_claim_terminal_and_readers.py tests/test_native_blocker_callers.py tests/test_native_routing_viewer.py tests/test_viewer_board.py tests/test_viewer_board_detail.py tests/test_viewer_board_oracles.py tests/test_viewer_n10_today.py
git commit -m "test(speed): template projection, sync, graph, claim, blocker-case and viewer twin fixtures" -m "The viewer board files share one per-worker template (SEED_KEY); blocker cases key on their DEPENDENCY_CASES name. All pass under TASKMASTER_TEMPLATE_VERIFY=1 (single worker). Setup before -> after per file: <from Steps 1 and 4>.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: Shutdown-latency decision (spec §3.4)

**Files:**
- Create (throwaway, never committed; `test-results/` is gitignored): `test-results/teardown-probe/teardown_probe.py`, `test-results/teardown-probe/teardown_extrapolate.py`
- Modify (both branches): `docs/reports/2026-09-29-test-speed-baseline.md` (append one section)
- Branch A only, modify: `taskmaster/coordinator/service.py:12,31,79-89,149-150,240,356`, `taskmaster/coordinator/linear_worker.py:75`, `tests/conftest.py:258-268`, plus 17 test-owned server sites in 16 files (table in Step A5)
- Branch A only, test: `tests/test_coordinator_poll_interval.py` (create)

**Interfaces:**
- Consumes: Task 1's `docs/reports/2026-09-29-test-speed-baseline.md`, specifically the full-suite worker-seconds `W` (the sum of junit testcase times of the quiet baseline run).
- Produces, Branch A:
  - `taskmaster.coordinator.service.POLL_INTERVAL = 0.5`
  - the keyword `Coordinator(root, *, ..., poll_interval=None)` and the attribute `Coordinator.poll_interval`
  - a session autouse fixture `_fast_coordinator_polls` in `tests/conftest.py` that sets the interval to 0.02 s in-process
  - test-owned viewer servers call `serve_forever(poll_interval=0.02)`
- Produces, Branch B: only the report section.

Why this is close to the threshold, so it has to be measured:
- `serve_forever` (stdlib default poll 0.5 s) only sees `shutdown()` at its next poll.
- A drafting probe on 2026-09-29 measured:
  - test-owned viewer-server shutdowns at 0.40–0.51 s each (`tests/test_server_api.py`: 4.8 s of its 5.3 s)
  - in-body `with Coordinator(...)` closes at 0.39–0.50 s each
- The 402c17c profile has 176 s in the teardown phase (3.3%). In-body closes in 24 files add call-phase time the profile cannot separate out.

- [ ] **Step 1: Pre-flight (quiet machine)**

Run (PowerShell):
```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'pytest|acceptance|rehearsal' } | Select-Object ProcessId, CommandLine
"{0:N2} GB free" -f ((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB)
```
Expected: no rows, and at least 4 GB free. Otherwise wait. Do not kill other sessions' processes.

- [ ] **Step 2: Create the probe plugin** `test-results/teardown-probe/teardown_probe.py`

```python
# User intent: throwaway probe (never committed) that measures how much test time goes to
# coordinator and HTTP-server shutdown polling, so spec 3.4 is decided on numbers, not guesses.
"""Load with `-p teardown_probe` (its directory on PYTHONPATH). Writes one JSON line per test."""
from __future__ import annotations

import functools
import json
import os
import socketserver
import threading
import time
from pathlib import Path

import pytest

_OUT = Path(os.environ.get("TM_TEARDOWN_PROBE_OUT", "test-results/teardown-probe"))
_local = threading.local()
_current = {"nodeid": "<outside-test>", "phase": "outside"}
_rows: dict[str, dict] = {}


def _row(nodeid: str) -> dict:
    return _rows.setdefault(nodeid, {"nodeid": nodeid, "test_s": 0.0, "close_s": 0.0, "close_n": 0,
                                     "http_s": 0.0, "http_n": 0, "by_phase": {}})


def _record(kind: str, elapsed: float) -> None:
    row = _row(_current["nodeid"])
    row[f"{kind}_s"] += elapsed
    row[f"{kind}_n"] += 1
    row["by_phase"][_current["phase"]] = row["by_phase"].get(_current["phase"], 0.0) + elapsed


def _timed(kind: str, function):
    @functools.wraps(function)
    def wrapper(self, *args, **kwargs):
        if getattr(_local, "closing", False):
            # Inside Coordinator.close: its own server shutdown is already counted as close time.
            return function(self, *args, **kwargs)
        _local.closing = kind == "close"
        started = time.perf_counter()
        try:
            return function(self, *args, **kwargs)
        finally:
            _local.closing = False
            _record(kind, time.perf_counter() - started)
    return wrapper


def pytest_configure(config):
    from taskmaster.coordinator import service
    service.Coordinator.close = _timed("close", service.Coordinator.close)
    socketserver.BaseServer.shutdown = _timed("http", socketserver.BaseServer.shutdown)


def _phase(name):
    @pytest.hookimpl(wrapper=True)
    def hook(item):
        _current.update(nodeid=item.nodeid, phase=name)
        try:
            return (yield)
        finally:
            _current["phase"] = "outside"
    return hook


pytest_runtest_setup = _phase("setup")
pytest_runtest_call = _phase("call")
pytest_runtest_teardown = _phase("teardown")


def pytest_runtest_logreport(report):
    if hasattr(report, "node"):
        return  # xdist controller: the worker that ran the test already recorded it
    _row(report.nodeid)["test_s"] += report.duration


def pytest_sessionfinish(session):
    if not _rows:
        return
    _OUT.mkdir(parents=True, exist_ok=True)
    name = os.environ.get("PYTEST_XDIST_WORKER", "main")
    with (_OUT / f"{name}-{os.getpid()}.jsonl").open("a", encoding="utf-8") as sink:
        for row in _rows.values():
            sink.write(json.dumps(row) + "\n")
```

- [ ] **Step 3: Create the extrapolator** `test-results/teardown-probe/teardown_extrapolate.py`

It sorts every test file into one of four classes by source text:
- **C** constructs a `Coordinator(`.
- **H** runs a test-owned HTTP server.
- **T** uses twins served by the compatibility coordinator.
- **O** is everything else, and costs 0 by construction.

A fixture or helper borrowed with `from test_x import name` counts toward the borrower's class when that function's body matches a class pattern. For each class, the per-test shutdown cost measured in the sample is multiplied by each unsampled file's collected test count.

```python
# User intent: throwaway probe (never committed): turn the sampled shutdown timings into a
# suite-wide estimate, so the spec 3.4 decision uses the whole suite, not one file's noise.
"""usage: teardown_extrapolate.py PROBE_OUT_DIR COLLECT_TXT BASELINE_WORKER_SECONDS [TESTS_DIR]"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

CLASSES = {
    # C: builds a Coordinator in the test body or its own fixtures.
    "C": re.compile(r"\bCoordinator\("),
    # H: runs a test-owned HTTP server and shuts it down.
    "H": re.compile(r"serve_forever|_viewer_servers"),
    # T: twins whose native side is served by an in-process compatibility coordinator.
    "T": re.compile(r"make_twins|native_twins|compatibility_client"),
}
PRECEDENCE = ("C", "H", "T")  # a file matching several classes is costed by the first
IMPORT = re.compile(r"^from (?:tests\.)?(test_\w+) import (\([^)]*\)|[^\n]*)", re.M)
TOP_LEVEL = re.compile(r"^(?:def |class |@)", re.M)


def _block(text: str, name: str) -> str:
    """The source of top-level `def name(`, up to the next top-level statement."""
    start = re.search(rf"^def {re.escape(name)}\(", text, re.M)
    if start is None:
        return ""
    after = TOP_LEVEL.search(text, start.end())
    return text[start.start():after.start() if after else len(text)]


def classify(tests_dir: Path) -> dict[str, str]:
    texts = {path.stem: path.read_text(encoding="utf-8", errors="replace")
             for path in tests_dir.glob("test_*.py")}
    result = {}
    for stem, text in texts.items():
        names = {name for name, rx in CLASSES.items() if rx.search(text)}
        for module, imported in IMPORT.findall(text):  # a fixture or helper borrowed from another test module
            source = texts.get(module, "")
            for name in re.findall(r"\w+", re.sub(r"#[^\n]*", "", imported)):
                block = _block(source, name)
                names |= {cls for cls, rx in CLASSES.items() if block and rx.search(block)}
        result[stem] = next((name for name in PRECEDENCE if name in names), "O")
    return result


def _stem(nodeid: str) -> str:
    return nodeid.split("::", 1)[0].rsplit("/", 1)[-1].removesuffix(".py")


def main() -> None:
    out_dir, collect_txt, baseline = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3])
    tests_dir = Path(sys.argv[4]) if len(sys.argv) > 4 else Path("tests")
    counts = Counter(_stem(line) for line in collect_txt.read_text(encoding="utf-8", errors="replace").splitlines()
                     if "::" in line)
    classes = classify(tests_dir)
    measured = defaultdict(lambda: [0.0, 0, 0])  # file -> [shutdown seconds, tests, shutdown calls]
    for sink in out_dir.glob("*.jsonl"):
        for line in sink.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            entry = measured[_stem(row["nodeid"])]
            entry[0] += row["close_s"] + row["http_s"]
            entry[1] += 1
            entry[2] += row["close_n"] + row["http_n"]
    rate = {}
    for name in PRECEDENCE:
        seconds = sum(m[0] for stem, m in measured.items() if classes.get(stem) == name)
        tests = sum(m[1] for stem, m in measured.items() if classes.get(stem) == name)
        calls = sum(m[2] for stem, m in measured.items() if classes.get(stem) == name)
        rate[name] = seconds / tests if tests else 0.0
        print(f"class {name}: sampled {tests} tests, {calls} shutdowns, {seconds:.1f} s, {rate[name]:.3f} s/test")
    total = sampled = 0.0
    per_class = defaultdict(float)
    for stem, n in counts.items():
        name = classes.get(stem, "O")
        value = measured[stem][0] if stem in measured else rate.get(name, 0.0) * n
        sampled += value if stem in measured else 0.0
        per_class[name] += value
        total += value
    for name in (*PRECEDENCE, "O"):
        files = sum(1 for stem in counts if classes.get(stem, "O") == name)
        tests = sum(n for stem, n in counts.items() if classes.get(stem, "O") == name)
        print(f"class {name}: {files} files, {tests} tests, estimated {per_class[name]:.1f} s")
    share = 100 * total / baseline
    print(f"measured {sampled:.1f} s; suite estimate {total:.1f} s = {share:.2f}% of {baseline:.0f} worker-s")
    if 4.0 <= share < 6.0:
        print("DECISION: inside the 4-6% band - measure every C/H/T file, then decide on that exact sum")
    else:
        print("DECISION:", "ADD the poll-interval parameter (>= 5%)" if share >= 5.0 else "DROP (< 5%)")


if __name__ == "__main__":
    main()
```

Sanity check (the classifier at HEAD gives C 24, H 26, T 49, O 236 files):
```bash
.venv/Scripts/python.exe -c "import sys; sys.path.insert(0, 'test-results/teardown-probe'); import teardown_extrapolate as t; from pathlib import Path; from collections import Counter; print(Counter(t.classify(Path('tests')).values()))"
```
Expected: roughly `Counter({'O': 236, 'T': 49, 'H': 26, 'C': 24})`, with more if Tasks 2–6 added files.

- [ ] **Step 4: Collect per-file test counts (the same population as `W`)**

```bash
.venv/Scripts/python.exe -m pytest tests --collect-only -q -p no:cacheprovider > test-results/teardown-probe/collect.txt
tail -1 test-results/teardown-probe/collect.txt
```
Expected: `N/N+1 tests collected (1 deselected)` (the `scale` test).

- [ ] **Step 5: Run the stratified sample with the probe**

The sample has 12 files, about 354 tests and about 590 worker-s at 402c17c:
- **C:** `test_native_service`, `test_native_service_sync`, `test_native_service_linear`, `test_native_git_checkouts`
- **T:** `test_native_blocker_callers`, `test_batch_structured_commands`, `test_native_claim_review_d`, `test_native_routing_claims`
- **H:** `test_server_api`, `test_viewer_reads_rows`, `test_store_bypass`, `test_viewer_write_gates`

```bash
ROOT="$(pwd -W)"
PYTHONPATH="$ROOT/test-results/teardown-probe" TM_TEARDOWN_PROBE_OUT="$ROOT/test-results/teardown-probe/before" \
  .venv/Scripts/python.exe -m pytest -n 3 -q -p no:cacheprovider -p teardown_probe \
  tests/test_native_service.py tests/test_native_service_sync.py tests/test_native_service_linear.py tests/test_native_git_checkouts.py \
  tests/test_native_blocker_callers.py tests/test_batch_structured_commands.py tests/test_native_claim_review_d.py tests/test_native_routing_claims.py \
  tests/test_server_api.py tests/test_viewer_reads_rows.py tests/test_store_bypass.py tests/test_viewer_write_gates.py
ls test-results/teardown-probe/before/
```
Expected: all pass, and one `gw*-<pid>.jsonl` per worker.

- [ ] **Step 6: Extrapolate and decide**

Set `W` to the full-suite worker-seconds from `docs/reports/2026-09-29-test-speed-baseline.md`:
```bash
W=<W from the baseline report>
.venv/Scripts/python.exe test-results/teardown-probe/teardown_extrapolate.py \
  test-results/teardown-probe/before test-results/teardown-probe/collect.txt "$W" tests \
  | tee test-results/teardown-probe/decision.txt
```
The last line is the decision:
- `ADD...` → Branch A.
- `DROP...` → Branch B.
- `inside the 4-6% band` → run the band step first.

**Band step** (only if the estimate is 4–6%): measure the whole C/H/T population instead of extrapolating (~3,900 worker-s at 402c17c, about 25 min at `-n 3`), then decide at exactly 5%.
```bash
FILES=$(.venv/Scripts/python.exe -c "import sys; sys.path.insert(0, 'test-results/teardown-probe'); import teardown_extrapolate as t; from pathlib import Path; print(' '.join(f'tests/{s}.py' for s, c in sorted(t.classify(Path('tests')).items()) if c != 'O'))")
PYTHONPATH="$ROOT/test-results/teardown-probe" TM_TEARDOWN_PROBE_OUT="$ROOT/test-results/teardown-probe/population" \
  .venv/Scripts/python.exe -m pytest -n 3 -q -p no:cacheprovider -p teardown_probe $FILES
.venv/Scripts/python.exe test-results/teardown-probe/teardown_extrapolate.py \
  test-results/teardown-probe/population test-results/teardown-probe/collect.txt "$W" tests \
  | tee test-results/teardown-probe/decision.txt
```
With every C/H/T file measured, the estimate is an exact sum. In this run only, ignore the band line and decide `>= 5%` → A, else B.

The denominator is the baseline `W`, as spec §3.4 says. Also write the share against the post-Step-1 worker-seconds into the report, if Tasks 2–6 measured it. It is informative only.

#### Branch B (< 5%): record and stop

- [ ] **Step B1: Append to `docs/reports/2026-09-29-test-speed-baseline.md`**

Copy the numbers from `test-results/teardown-probe/decision.txt`:
```markdown
## §3.4 Coordinator and HTTP shutdown latency: dropped

Measured <YYYY-MM-DD> at `<git rev-parse --short HEAD>` (plan Task 7). A throwaway probe timed every
`Coordinator.close()` and every test-owned `server.shutdown()` over a 12-file stratified sample
(C: in-body coordinators; T: twins served by the compatibility coordinator; H: test-owned viewer
servers) and extrapolated each class's per-test cost over the collected test counts.

| Class | Files | Tests | Estimated shutdown time |
|---|---|---|---|
| C | <n> | <n> | <s> s |
| H | <n> | <n> | <s> s |
| T | <n> | <n> | <s> s |
| **Total** | | | **<s> s = <p>% of <W> worker-seconds** |

Below the 5% threshold in spec §3.4: **dropped**. Probe and raw output: `test-results/teardown-probe/` (not committed).
```

- [ ] **Step B2: Commit**

```bash
git add docs/reports/2026-09-29-test-speed-baseline.md
git commit -m "docs(tests): record the 3.4 shutdown-latency measurement - dropped (<p>% of worker-seconds)

Co-Authored-By: Claude <noreply@anthropic.com>"
```
Branch B ends here.

#### Branch A (≥ 5%): poll-interval constructor parameter; tests poll every 0.02 s

- [ ] **Step A1: Write the failing test** `tests/test_coordinator_poll_interval.py`

```python
# User intent: in-process coordinators in tests must notice shutdown in 0.02 s instead of
# 0.5 s, while production keeps the stdlib poll; a fresh interpreter proves the default.
"""The coordinator's poll interval: a constructor parameter, 0.5 s unless a caller says otherwise."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from taskmaster.coordinator import service
from taskmaster.coordinator.service import Coordinator
from test_native_service import root  # noqa: F401

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def test_production_polls_every_half_second_in_a_fresh_interpreter():
    probe = subprocess.run(
        [sys.executable, "-c", "from taskmaster.coordinator import service; print(service.POLL_INTERVAL)"],
        cwd=PLUGIN_ROOT, capture_output=True, text=True, timeout=60, check=True)
    assert probe.stdout.strip() == "0.5"


def test_in_process_coordinators_in_tests_poll_every_20_ms(root):
    with Coordinator(root) as owner:
        assert owner.poll_interval == 0.02


def test_the_ipc_thread_polls_at_the_constructor_interval(root, monkeypatch):
    seen = []
    real = service._Server.serve_forever

    def recording(self, poll_interval=0.5):
        seen.append(poll_interval)
        return real(self, poll_interval)

    monkeypatch.setattr(service._Server, "serve_forever", recording)
    with Coordinator(root, poll_interval=0.05) as owner:
        assert owner.poll_interval == 0.05
    assert seen == [0.05]


@pytest.mark.parametrize("interval", [0, -1, float("inf"), "0.1", True])
def test_a_poll_interval_that_is_not_a_positive_finite_number_is_refused(root, interval):
    with pytest.raises(ValueError, match="poll interval"):
        Coordinator(root, poll_interval=interval)
```

- [ ] **Step A2: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_coordinator_poll_interval.py -q -p no:cacheprovider`
Expected: FAIL:
- `AttributeError: module 'taskmaster.coordinator.service' has no attribute 'POLL_INTERVAL'` (fresh-interpreter test, via `CalledProcessError`)
- `TypeError: Coordinator.__init__() got an unexpected keyword argument 'poll_interval'`
- `AttributeError: 'Coordinator' object has no attribute 'poll_interval'`

- [ ] **Step A3: Implement in `taskmaster/coordinator/service.py` and `linear_worker.py`**

`service.py:12`, add `functools` next to the other imports:
```python
# before
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
# after
from dataclasses import dataclass, field
import functools
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
```

`service.py:31`, after `LOG = logging.getLogger(__name__)`:
```python
LOG = logging.getLogger(__name__)

# How often the service threads look up from their waits. `serve_forever`'s poll bounds how long
# `close()` waits for the IPC thread; 0.5 s is the stdlib default. In-process tests shorten it.
POLL_INTERVAL = 0.5
```

`service.py:79-89`:
```python
# before
    def __init__(self, root, *, queue_limit=256, handler_limit=64, checkpoint=None, exporter=None,
                 linear_client_factory=None):
        ...
        if type(handler_limit) is not int or handler_limit < 1:
            raise ValueError('handler limit must be positive')
        self.handler_limit = handler_limit
# after
    def __init__(self, root, *, queue_limit=256, handler_limit=64, checkpoint=None, exporter=None,
                 linear_client_factory=None, poll_interval=None):
        ...
        if type(handler_limit) is not int or handler_limit < 1:
            raise ValueError('handler limit must be positive')
        poll_interval = POLL_INTERVAL if poll_interval is None else poll_interval
        if type(poll_interval) not in (int, float) or not 0 < poll_interval < float('inf'):
            raise ValueError('poll interval must be a positive number of seconds')
        self.poll_interval = poll_interval
        self.handler_limit = handler_limit
```

`service.py:149-150`:
```python
# before
            for name, target in (('writer', self._writer), ('exporter', self._export), ('linear', self.linear.run),
                                 ('ipc', self.server.serve_forever)):
# after
            for name, target in (('writer', self._writer), ('exporter', self._export), ('linear', self.linear.run),
                                 ('ipc', functools.partial(self.server.serve_forever, self.poll_interval))):
```

`service.py:240` (writer loop):
```python
# before
                    work = self.queue.get(timeout=0.1)
# after
                    work = self.queue.get(timeout=min(0.1, self.poll_interval))
```

`service.py:356` (exporter loop):
```python
# before
            if not self.export_needed.wait(0.25):
# after
            if not self.export_needed.wait(min(0.25, self.poll_interval)):
```

`taskmaster/coordinator/linear_worker.py:75`:
```python
# before
                key, envelope, future = self.queue.get(timeout=0.1)
# after
                key, envelope, future = self.queue.get(timeout=min(0.1, owner.poll_interval))
```
The production defaults are unchanged: `min(0.1, 0.5) = 0.1`, `min(0.25, 0.5) = 0.25`, and `serve_forever(0.5)` is the stdlib default. `main()` (L710-727) keeps its own 0.25 s idle loop. `LinearWorker` is only built by `Coordinator.__init__` (L118).

- [ ] **Step A4: Add the conftest fixture** `tests/conftest.py`, inserted after `_native_coordinator_isolation` (ends at L267)

```python
@pytest.fixture(autouse=True, scope="session")
def _fast_coordinator_polls():
    """In-process coordinators notice shutdown within 0.02 s instead of 0.5 s.

    Session-scoped, so module fixtures that start owners get it too. Only this
    process's default changes: a real service process a test launches keeps the
    production interval.
    """
    from taskmaster.coordinator import service  # noqa: PLC0415
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(service, "POLL_INTERVAL", 0.02)
        yield
```

- [ ] **Step A5: Shorten the test-owned viewer servers' poll (17 sites, test code only)**

| File:line | Current text |
|---|---|
| `tests/test_api_handover_status.py:45` | `t = threading.Thread(target=server.serve_forever, daemon=True)` |
| `tests/test_api_notes.py:35` | `t = threading.Thread(target=server.serve_forever, daemon=True)` |
| `tests/test_bug_pattern_scan_rows.py:74` | `thread = threading.Thread(target=srv.serve_forever, daemon=True)` |
| `tests/test_native_bypass_gate.py:424` | `thread = threading.Thread(target=server.serve_forever, daemon=True)` |
| `tests/test_native_routing_viewer.py:49` | `thread = threading.Thread(target=server.serve_forever, daemon=True)` |
| `tests/test_native_service_routing.py:70` | `thread = threading.Thread(target=server.serve_forever, daemon=True)` |
| `tests/test_native_service_routing.py:186` | `thread = threading.Thread(target=server.serve_forever, daemon=True)` |
| `tests/test_phase_ordering.py:87` | `t = threading.Thread(target=server.serve_forever, daemon=True)` |
| `tests/test_server_api.py:30` | `t = threading.Thread(target=server.serve_forever, daemon=True)` |
| `tests/test_server_etag.py:27` | `t = threading.Thread(target=server.serve_forever, daemon=True)` |
| `tests/test_server_sessions.py:29` | `t = threading.Thread(target=server.serve_forever, daemon=True)` |
| `tests/test_server_task_detail.py:86` | `t = threading.Thread(target=server.serve_forever, daemon=True)` |
| `tests/test_server_task_write.py:29` | `t = threading.Thread(target=server.serve_forever, daemon=True)` |
| `tests/test_status_transition_table_everywhere.py:76` | `thread = threading.Thread(target=srv.serve_forever, daemon=True)` |
| `tests/test_store_bypass.py:205` | `thread = threading.Thread(target=server.serve_forever, daemon=True)` |
| `tests/test_viewer_reads_rows.py:59` | `thread = threading.Thread(target=srv.serve_forever, daemon=True)` |
| `tests/test_viewer_write_gates.py:43` | `thread = threading.Thread(target=srv.serve_forever, daemon=True)` |

The replacement pattern is `target=X.serve_forever, daemon=True)` → `target=X.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)`. The target stays the bound method, so `tests/test_server_continuity.py:9-17` (`_viewer_servers`) still recognises these servers.
```bash
FILES="tests/test_api_handover_status.py tests/test_api_notes.py tests/test_bug_pattern_scan_rows.py tests/test_native_bypass_gate.py tests/test_native_routing_viewer.py tests/test_native_service_routing.py tests/test_phase_ordering.py tests/test_server_api.py tests/test_server_etag.py tests/test_server_sessions.py tests/test_server_task_detail.py tests/test_server_task_write.py tests/test_status_transition_table_everywhere.py tests/test_store_bypass.py tests/test_viewer_reads_rows.py tests/test_viewer_write_gates.py"
sed -i -E 's/target=(server|srv)\.serve_forever, daemon=True\)/target=\1.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)/' $FILES
grep -rn "serve_forever, daemon=True" tests          # expected: no output
grep -rn 'serve_forever, kwargs={"poll_interval": 0.02}' tests | wc -l   # expected: 17
```

- [ ] **Step A6: Run to verify it passes, plus regression**

```bash
.venv/Scripts/python.exe -m pytest tests/test_coordinator_poll_interval.py -q -p no:cacheprovider
.venv/Scripts/python.exe -m pytest -n 3 -q -p no:cacheprovider tests/test_native_service.py tests/test_native_service_linear.py tests/test_native_service_sync.py tests/test_native_service_process.py tests/test_native_git_managed.py tests/test_server_continuity.py tests/test_native_service_routing.py tests/test_native_routing_viewer.py
```
Expected: the new file has 8 passed, and the regression files all pass. `test_native_service_process.py` runs real service processes at the production 0.5 s.

- [ ] **Step A7: Re-measure the same sample to prove the saving**

```bash
PYTHONPATH="$ROOT/test-results/teardown-probe" TM_TEARDOWN_PROBE_OUT="$ROOT/test-results/teardown-probe/after" \
  .venv/Scripts/python.exe -m pytest -n 3 -q -p no:cacheprovider -p teardown_probe \
  tests/test_native_service.py tests/test_native_service_sync.py tests/test_native_service_linear.py tests/test_native_git_checkouts.py \
  tests/test_native_blocker_callers.py tests/test_batch_structured_commands.py tests/test_native_claim_review_d.py tests/test_native_routing_claims.py \
  tests/test_server_api.py tests/test_viewer_reads_rows.py tests/test_store_bypass.py tests/test_viewer_write_gates.py
.venv/Scripts/python.exe test-results/teardown-probe/teardown_extrapolate.py \
  test-results/teardown-probe/after test-results/teardown-probe/collect.txt "$W" tests | tee test-results/teardown-probe/after.txt
```
Expected:
- per-close cost drops from about 0.4–0.5 s to about 0.02–0.05 s
- the suite estimate drops by about 90%

If it does not drop, stop and investigate: some wait is not covered by `poll_interval`.

- [ ] **Step A8: Append to `docs/reports/2026-09-29-test-speed-baseline.md`**

Numbers come from `decision.txt` (before) and `after.txt`:
```markdown
## §3.4 Coordinator and HTTP shutdown latency: adopted

Measured <YYYY-MM-DD> at `<sha>` (plan Task 7). A 12-file stratified probe, extrapolated per class:
shutdown waits cost <s> s = <p>% of <W> worker-seconds (≥ 5%, spec §3.4). `Coordinator` takes
`poll_interval` (default `service.POLL_INTERVAL = 0.5`, unchanged in production); a session fixture
sets 0.02 s in-process, and test-owned viewer servers poll at 0.02 s. After: <s'> s (<p'>%).

| Class | Before (s) | After (s) |
|---|---|---|
| C | <s> | <s> |
| H | <s> | <s> |
| T | <s> | <s> |
```

- [ ] **Step A9: Commit**

```bash
git add taskmaster/coordinator/service.py taskmaster/coordinator/linear_worker.py tests/conftest.py tests/test_coordinator_poll_interval.py $FILES docs/reports/2026-09-29-test-speed-baseline.md
git commit -m "feat(speed): coordinator poll interval is a constructor parameter; tests poll every 0.02 s

Shutdown waits measured at <p>% of worker-seconds (spec 3.4 threshold 5%). Production default
stays the stdlib 0.5 s; only in-process test coordinators and test-owned viewer servers shorten it.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

---

### Task 8: `release` marker absorbs `scale`; slow-test warning

**Files:**
- Create: `tests/tiers.py`
- Modify: `tests/conftest.py:13-20` (import), `tests/conftest.py:46-49` (marker registration), `tests/conftest.py:81-88` (`pytest_collection_modifyitems`)
- Test: `tests/test_tiers.py` (create; extended in Steps 6-10)

**Interfaces:**
- Consumes: none.
- Produces: `tests/tiers.py`, containing:
  - `TIERS = ("release", "scale")`
  - `ALL_TIERS_MARKEXPR = "release or not release"`
  - `SLOW_WARN_SECONDS = 10.0`
  - `tier_kept(markexpr, markers) -> bool`
  - `gate(config, items)`
  - `slow_untiered(durations, tiered, limit) -> list[(nodeid, s)]`
  - `format_slow_warning(slow, limit) -> list[str]`
  - `class SlowTests`, registered as plugin `"taskmaster-slow-tests"`
  - `configure(config)`
  - `explicit_node_ids(config) -> set[(Path, name)]` and `named_explicitly(item, named) -> bool`: a test named by node id (with or without its parametrize id) passes the gate whatever its tier; file and directory arguments are still gated (Steps 6-10)
- Produces (the contract for Task 10): the runner's `release` command selects every test with `-m "release or not release"`, which is `ALL_TIERS_MARKEXPR`. Task 10 may hard-code the literal or import it.
- Produces: the slow-test warning prints under the terminal section title `slow tests outside release`, into the pytest log. It is a warning, not a failure.

Rules, checked against spec §4.2 ("release replaces and absorbs scale. Both stay deselected unless the marker expression names them, and `-m scale` keeps working"):
- The gate runs before pytest evaluates `-m`, and is substring-based.
  - A `release` test is deselected unless the expression contains `release`.
  - A `scale` test is deselected unless it contains `scale` or `release`.
- **The substring gate alone does not make `-m release` pull `scale` tests.** Pytest's own `-m release` evaluation would still drop a test that carries only `@pytest.mark.scale`. So the gate also adds the `release` marker to every kept `scale` test. That makes "absorbs" literal:
  - `-m release` selects both tiers.
  - `-m scale` selects only `scale`.
  - `-m "not release"` equals the bare run.
- The gate hook is `tryfirst`, so it runs before `_pytest.mark`'s deselection. The scratch probe also worked without `tryfirst`.

Resulting selections, verified in a scratch project:

| `-m` | unmarked | `release` | `scale` |
|---|---|---|---|
| (none) | ✓ | – | – |
| `release` | – | ✓ | ✓ |
| `scale` | – | – | ✓ |
| `not release` | ✓ | – | – |
| `not scale` | ✓ | – | – |
| `release or not release` | ✓ | ✓ | ✓ |
| `slow` | slow only | – | – |

Why the tier travels on the report: under xdist the controller has no items. Markers do not travel with reports. The keyword map is unsafe here: pytest adds parametrize ids to `report.keywords`, and `tests/test_batch_structured_commands.py:286` has a case with id `"release"`. So the process that ran the test stamps each report with `taskmaster_tiered`. `TestReport.__init__(**extra)` restores extra attributes, and `_report_to_json` copies `__dict__`, so the stamp crosses the process boundary. Verified with `-n 1`.

- [ ] **Step 1: Write the failing test** `tests/test_tiers.py`

```python
# User intent: the tier gate is the only thing between release-only evidence and the merge suite,
# and the slow-test warning is how new heavy tests get noticed; pin both, including under xdist.
"""`release`/`scale` gate and slow-test warning (tests/tiers.py)."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests import tiers

pytest_plugins = ["pytester"]

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

GATE = [  # (markexpr, the test's tier markers, passes the gate)
    ("", set(), True), ("", {"release"}, False), ("", {"scale"}, False),
    ("release", set(), True), ("release", {"release"}, True), ("release", {"scale"}, True),
    ("scale", set(), True), ("scale", {"release"}, False), ("scale", {"scale"}, True),
    # The gate only opens here; pytest's own `-m` evaluation then drops the tier tests.
    ("not release", set(), True), ("not release", {"release"}, True), ("not release", {"scale"}, True),
    ("release or not release", set(), True), ("release or not release", {"release"}, True),
    ("release or not release", {"scale"}, True),
]


@pytest.mark.parametrize("markexpr,markers,kept", GATE)
def test_a_tier_passes_the_gate_only_when_the_expression_names_it(markexpr, markers, kept):
    assert tiers.tier_kept(markexpr, markers) is kept


def test_no_marker_expression_is_the_bare_merge_run():
    assert tiers.tier_kept(None, set()) is True
    assert tiers.tier_kept(None, {"release"}) is False
    assert tiers.tier_kept(None, {"scale"}) is False


def test_the_select_everything_expression_opens_every_tier():
    assert all(tiers.tier_kept(tiers.ALL_TIERS_MARKEXPR, markers)
               for markers in (set(), {"release"}, {"scale"}, {"release", "scale"}))


def test_slow_untiered_lists_only_untiered_tests_over_the_limit_slowest_first():
    durations = {"t.py::a": 12.0, "t.py::b": 10.0, "t.py::c": 30.0, "t.py::d": 11.0}
    assert tiers.slow_untiered(durations, {"t.py::c"}, limit=10.0) == [("t.py::a", 12.0), ("t.py::d", 11.0)]


def test_the_warning_names_every_slow_test_with_its_seconds():
    lines = tiers.format_slow_warning([("tests/t.py::test_x", 12.34), ("tests/t.py::test_y", 10.5)], limit=10.0)
    assert lines[0].startswith("2 test(s) outside `release` took over 10 s (setup + call)")
    assert lines[1:] == ["     12.3s  tests/t.py::test_x", "     10.5s  tests/t.py::test_y"]


def test_nothing_slow_means_no_warning():
    assert tiers.format_slow_warning([]) == []


def test_the_suite_registers_both_tier_markers_and_the_warning(pytestconfig):
    markers = "\n".join(pytestconfig.getini("markers"))
    assert "release:" in markers and "scale:" in markers
    assert isinstance(pytestconfig.pluginmanager.get_plugin("taskmaster-slow-tests"), tiers.SlowTests)


INNER_CONFTEST = """
from tests import tiers
tiers.SLOW_WARN_SECONDS = 0.2

def pytest_configure(config):
    tiers.configure(config)

def pytest_collection_modifyitems(config, items):
    tiers.gate(config, items)
"""

INNER_TESTS = """
import time
import pytest

def test_plain_slow():
    time.sleep(0.3)

@pytest.mark.release
def test_release_slow():
    time.sleep(0.3)

@pytest.mark.scale
def test_scale_profile():
    pass
"""


@pytest.fixture
def inner(pytester, monkeypatch):
    pytester.makeconftest(INNER_CONFTEST)
    pytester.makepyfile(test_inner=INNER_TESTS)
    monkeypatch.setenv("PYTHONPATH", str(PLUGIN_ROOT))
    return pytester


@pytest.mark.parametrize("markexpr,selected", [
    (None, ["test_plain_slow"]),
    ("release", ["test_release_slow", "test_scale_profile"]),
    ("scale", ["test_scale_profile"]),
    ("not release", ["test_plain_slow"]),
    ("release or not release", ["test_plain_slow", "test_release_slow", "test_scale_profile"]),
])
def test_marker_expressions_select_the_documented_tiers(inner, markexpr, selected):
    args = ["--collect-only", "-q", "-p", "no:cacheprovider"] + (["-m", markexpr] if markexpr else [])
    result = inner.runpytest_subprocess(*args)
    assert sorted(line.split("::")[1] for line in result.outlines if "::" in line) == selected


@pytest.mark.parametrize("workers", [[], ["-n", "1"]], ids=["in-process", "xdist"])
def test_slow_untiered_tests_are_warned_about_but_never_fail_the_run(inner, workers):
    result = inner.runpytest_subprocess("-p", "no:cacheprovider", "-m", "release or not release", *workers)
    assert result.ret == 0
    result.stdout.fnmatch_lines(["*slow tests outside release*", "*test_inner.py::test_plain_slow"])
    assert "test_release_slow" not in result.stdout.str()
```

- [ ] **Step 2: Run it to verify it fails, and capture today's wrong selection**

```bash
.venv/Scripts/python.exe -m pytest tests/test_tiers.py -q -p no:cacheprovider
.venv/Scripts/python.exe -m pytest tests/test_store_concurrency.py --collect-only -q -p no:cacheprovider -m "release or not release" | tail -1
```
Expected:
- the first command fails at collection with `ImportError: cannot import name 'tiers' from 'tests'`
- the second prints `10/11 tests collected (1 deselected)`, because today the `scale` test is dropped even though the expression means "everything"

- [ ] **Step 3: Implement** `tests/tiers.py`

```python
# User intent: keep release-tier evidence (and the `scale` profiles it absorbs) out of the merge
# suite unless `-m` names it, and warn about merge-suite tests slow enough to belong in `release`.
"""Test tiers: the `release`/`scale` gate and the slow-test warning (policy: tests/README.md).

The gate is a substring rule on the `-m` expression, applied before pytest evaluates it:
a tier's tests are collected only when the expression names the tier. `release` absorbs
`scale`: naming `release` also opens `scale`, and a kept `scale` test is marked `release`
so that `-m release` selects it.
"""
from __future__ import annotations

import pytest

TIERS = ("release", "scale")
# The `-m` expression that selects every test in every tier (the runner's `release` command).
ALL_TIERS_MARKEXPR = "release or not release"
SLOW_WARN_SECONDS = 10.0
_TIERED = "taskmaster_tiered"  # stamped on each report where the test ran; xdist ships it to the controller


def tier_kept(markexpr: str | None, markers) -> bool:
    """Whether a test with these tier `markers` passes the gate for `markexpr`."""
    expr = markexpr or ""
    if "scale" in markers:
        return "scale" in expr or "release" in expr
    if "release" in markers:
        return "release" in expr
    return True


def gate(config, items) -> None:
    """Deselect tier tests the expression did not name; a kept `scale` test also counts as `release`."""
    markexpr = getattr(config.option, "markexpr", "") or ""
    kept, dropped = [], []
    for item in items:
        names = {name for name in TIERS if item.get_closest_marker(name) is not None}
        (kept if tier_kept(markexpr, names) else dropped).append(item)
    for item in kept:
        if item.get_closest_marker("scale") is not None and item.get_closest_marker("release") is None:
            item.add_marker(pytest.mark.release)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept


def slow_untiered(durations, tiered, limit: float = SLOW_WARN_SECONDS) -> list[tuple[str, float]]:
    """`(nodeid, seconds)` of tests outside every tier whose setup + call took over `limit`, slowest first."""
    return sorted(((nodeid, seconds) for nodeid, seconds in durations.items()
                   if nodeid not in tiered and seconds > limit), key=lambda pair: (-pair[1], pair[0]))


def format_slow_warning(slow, limit: float = SLOW_WARN_SECONDS) -> list[str]:
    """Terminal lines for `slow_untiered`'s result; no lines when nothing is slow."""
    if not slow:
        return []
    return [f"{len(slow)} test(s) outside `release` took over {limit:g} s (setup + call); "
            "make them faster or move them to `release` per tests/README.md:"] + [
        f"  {seconds:7.1f}s  {nodeid}" for nodeid, seconds in slow]


class SlowTests:
    """Sums setup + call per test and warns at the end of the run; never fails it.

    Markers do not travel with xdist reports, so the process that ran the test
    stamps each report with whether the test is tiered; the controller reads it.
    """

    def __init__(self) -> None:
        self.durations: dict[str, float] = {}
        self.tiered: set[str] = set()

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(self, item, call):
        report = yield
        setattr(report, _TIERED, any(item.get_closest_marker(name) is not None for name in TIERS))
        return report

    def pytest_runtest_logreport(self, report) -> None:
        if report.when not in ("setup", "call"):
            return
        self.durations[report.nodeid] = self.durations.get(report.nodeid, 0.0) + report.duration
        if getattr(report, _TIERED, False):
            self.tiered.add(report.nodeid)

    def pytest_terminal_summary(self, terminalreporter, config) -> None:
        if hasattr(config, "workerinput"):
            return  # xdist worker: the controller reports for the whole run
        lines = format_slow_warning(slow_untiered(self.durations, self.tiered, SLOW_WARN_SECONDS),
                                    SLOW_WARN_SECONDS)
        if lines:
            terminalreporter.write_sep("=", "slow tests outside release", yellow=True)
            for line in lines:
                terminalreporter.write_line(line)


def configure(config) -> None:
    """Register the tier markers and the slow-test warning for this session."""
    config.addinivalue_line(
        "markers",
        "release: release-tier evidence (extra seeds, full stress runs, crash matrices); "
        "deselected unless the `-m` expression names `release` (tests/README.md)",
    )
    config.addinivalue_line(
        "markers",
        "scale: full-size acceptance profiles, part of the release tier; "
        "deselected unless `-m` names `scale` or `release`",
    )
    config.pluginmanager.register(SlowTests(), "taskmaster-slow-tests")
```

Wire it into `tests/conftest.py`.

L20, after the `sys.path` insert:
```python
# before
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))
# after
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from tests import tiers  # noqa: E402 - needs PLUGIN_ROOT on sys.path
```

L46-49, inside `pytest_configure`:
```python
# before
    config.addinivalue_line(
        "markers",
        "scale: full-size acceptance profiles, deselected by default; run with `-m scale`",
    )
# after
    tiers.configure(config)  # `release` and `scale` markers, slow-test warning
```

L81-88:
```python
# before
def pytest_collection_modifyitems(config, items):
    """Deselect `scale` profiles unless the marker expression asks for them."""
    if "scale" in (getattr(config.option, "markexpr", "") or ""):
        return
    kept = [item for item in items if item.get_closest_marker("scale") is None]
    if len(kept) != len(items):
        config.hook.pytest_deselected(items=[item for item in items if item.get_closest_marker("scale")])
        items[:] = kept
# after
@pytest.hookimpl(tryfirst=True)  # before pytest evaluates `-m`: kept `scale` tests are marked `release`
def pytest_collection_modifyitems(config, items):
    """Deselect `release`/`scale` tests unless the marker expression names them (tests/tiers.py)."""
    tiers.gate(config, items)
```
Leave `scripts/measure_test_memory.py:63-66` (`_selects_scale`) unchanged. It only decides which memory budget applies to an explicit `-m scale` measurement.

- [ ] **Step 4: Run to verify it passes, plus the real-conftest selections**

```bash
.venv/Scripts/python.exe -m pytest tests/test_tiers.py -q -p no:cacheprovider
.venv/Scripts/python.exe -m pytest tests/test_tiers.py -q -p no:cacheprovider -n 1
for e in "" "scale" "release" "not release" "release or not release"; do echo "-m '$e'"; .venv/Scripts/python.exe -m pytest tests/test_store_concurrency.py --collect-only -q -p no:cacheprovider ${e:+-m "$e"} | tail -1; done
```
Expected:
- `tests/test_tiers.py` passes both times: 28 passed. About 7 subprocess runs, around 15 s.
- The five collect-only lines, in order:
  - `10/11 tests collected (1 deselected)`
  - `1/11 … (10 deselected)`
  - `1/11 … (10 deselected)`, the `scale` test via absorption
  - `10/11 … (1 deselected)`
  - `11 tests collected`, which is the `ALL_TIERS_MARKEXPR` contract for Task 10

Regression (the files that touch markers or the conftest hooks):
```bash
.venv/Scripts/python.exe -m pytest tests/test_store_concurrency.py tests/test_batch_structured_commands.py -q -p no:cacheprovider -n 3
```

- [ ] **Step 5: Commit**

```bash
git add tests/tiers.py tests/test_tiers.py tests/conftest.py
git commit -m "feat(tests): release marker absorbs scale; warn on merge-suite tests over 10 s

Both tiers stay deselected unless -m names them; -m scale keeps working and -m release
selects scale too. 'release or not release' selects everything (runner contract).

Co-Authored-By: Claude <noreply@anthropic.com>"
```

- [ ] **Step 6: Write the failing review-focus test: a tier test named by node id must run**

Today's gate (Steps 1–5) silently deselects a `release`/`scale` test named by its node id when no `-m` is given. For example, `pytest "tests/test_store_concurrency.py::test_mixed_public_tool_operations_at_acceptance_scale"` prints `1 deselected` and exits 5. An agent reading that concludes the test is fine.

The rule: the gate keeps any item whose node id, or whose node id with the parametrize id stripped, was passed as a positional argument containing `::`. Directory and file arguments still gate.

Append to `tests/test_tiers.py`:
```python
INNER_NAMED = """
import pytest

@pytest.mark.release
def test_release_case():
    pass

def test_plain_case():
    pass

@pytest.mark.parametrize("seed", [1, pytest.param(2, marks=pytest.mark.release)])
def test_seeded(seed):
    pass
"""


@pytest.fixture
def named(pytester, monkeypatch):
    pytester.makeconftest(INNER_CONFTEST)
    pytester.makepyfile(test_named=INNER_NAMED)
    monkeypatch.setenv("PYTHONPATH", str(PLUGIN_ROOT))
    return pytester


def test_a_file_argument_still_gates_its_release_tests(named):
    result = named.runpytest_subprocess("-p", "no:cacheprovider", "test_named.py")
    result.assert_outcomes(passed=2, deselected=2)


@pytest.mark.parametrize("workers", [[], ["-n", "1"]], ids=["in-process", "xdist"])
@pytest.mark.parametrize("node", ["test_named.py::test_release_case", "test_named.py::test_seeded[2]"],
                         ids=["release-test", "release-param-case"])
def test_a_release_test_named_by_its_node_id_runs(named, node, workers):
    result = named.runpytest_subprocess("-p", "no:cacheprovider", node, *workers)
    assert result.ret == 0, result.stdout.str()
    result.assert_outcomes(passed=1)


def test_a_parametrized_test_named_without_its_id_runs_every_case(named):
    result = named.runpytest_subprocess("-p", "no:cacheprovider", "test_named.py::test_seeded")
    result.assert_outcomes(passed=2)
```

- [ ] **Step 7: Run it to verify it fails**

```bash
.venv/Scripts/python.exe -m pytest tests/test_tiers.py -q -p no:cacheprovider -k "named or file_argument"
.venv/Scripts/python.exe -m pytest "tests/test_store_concurrency.py::test_mixed_public_tool_operations_at_acceptance_scale" --collect-only -q -p no:cacheprovider | tail -1
```
Expected:
- `5 failed, 1 passed`. Each `..._named_by_its_node_id_runs` case fails `assert result.ret == 0` with ret 5, and the stdout shows `1 deselected`. The parametrize-base test fails `assert_outcomes` with 1 passed (seed 1 only) instead of 2. The file-argument test already passes.
- The collect line reads `no tests collected (1 deselected)`.

- [ ] **Step 8: Implement in `tests/tiers.py`**

Imports and a module constant:
```python
# before
from __future__ import annotations

import pytest
# after
from __future__ import annotations

from pathlib import Path
import re

import pytest
```
```python
# before
_TIERED = "taskmaster_tiered"  # stamped on each report where the test ran; xdist ships it to the controller
# after
_PARAM_ID = re.compile(r"\[.*\]$")
_TIERED = "taskmaster_tiered"  # stamped on each report where the test ran; xdist ships it to the controller
```

Two helpers before `gate`, and `gate`'s docstring and loop:
```python
# before
def gate(config, items) -> None:
    """Deselect tier tests the expression did not name; a kept `scale` test also counts as `release`."""
    markexpr = getattr(config.option, "markexpr", "") or ""
    kept, dropped = [], []
    for item in items:
        names = {name for name in TIERS if item.get_closest_marker(name) is not None}
        (kept if tier_kept(markexpr, names) else dropped).append(item)
# after
def explicit_node_ids(config) -> set[tuple[Path, str]]:
    """`(file, name)` for every positional argument that names a test node (contains `::`)."""
    named = set()
    for arg in config.args:
        path, separator, name = str(arg).partition("::")
        if separator:
            named.add((Path(config.invocation_params.dir, path).resolve(), name))
    return named


def named_explicitly(item, named) -> bool:
    """Whether the command line named this item, or its test with the parametrize id stripped."""
    if not named:
        return False
    name = item.nodeid.partition("::")[2]
    path = Path(item.path).resolve()
    return (path, name) in named or (path, _PARAM_ID.sub("", name)) in named


def gate(config, items) -> None:
    """Deselect tier tests the expression did not name; a kept `scale` test also counts as `release`.

    A test named on the command line by node id (`file::test` or `file::test[id]`) always
    runs: gating it would report "1 deselected" and exit 5, which reads as success.
    Directory and file arguments are still gated.
    """
    markexpr = getattr(config.option, "markexpr", "") or ""
    named = explicit_node_ids(config)
    kept, dropped = [], []
    for item in items:
        names = {name for name in TIERS if item.get_closest_marker(name) is not None}
        keep = tier_kept(markexpr, names) or named_explicitly(item, named)
        (kept if keep else dropped).append(item)
```
The rest of `gate` (the `scale` → `release` marking and the deselection) is unchanged.

Paths are compared resolved, relative to `config.invocation_params.dir`, because an argument may be relative, absolute or backslashed. xdist popen workers receive the controller's invocation args and start in its working directory, so they reach the same decision (checked with `-n 1`).

An explicit `-m` still wins. With `-m "not release"`, pytest's own `-m` evaluation drops a named `release` test after the gate keeps it.

- [ ] **Step 9: Run to verify it passes, plus the real conftest**

```bash
.venv/Scripts/python.exe -m pytest tests/test_tiers.py -q -p no:cacheprovider
.venv/Scripts/python.exe -m pytest tests/test_tiers.py -q -p no:cacheprovider -n 1
.venv/Scripts/python.exe -m pytest "tests/test_store_concurrency.py::test_mixed_public_tool_operations_at_acceptance_scale" --collect-only -q -p no:cacheprovider | tail -1
.venv/Scripts/python.exe -m pytest tests/test_store_concurrency.py --collect-only -q -p no:cacheprovider | tail -1
```
Expected:
- `34 passed` twice. Verified in a scratch copy of `tests/`, in-process and with `-n 1`.
- The named scale test: `1 test collected`.
- The file argument: still `10/11 tests collected (1 deselected)`.

- [ ] **Step 10: Commit**

```bash
git add tests/tiers.py tests/test_tiers.py
git commit -m "fix(tests): a release/scale test named by node id always runs

The tier gate deselected an explicitly named node id silently (exit 5, '1 deselected'),
which reads as a pass. File and directory arguments still gate.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

**`tests/README.md` sentence for Task 11:** "Naming a test by node id (`pytest "tests/x.py::test_y"` or `"tests/x.py::test_y[param]"`) always runs it, whatever its tier; only directory and file arguments are filtered by the `release`/`scale` gate."

---

---

### Task 9: Move the heavy tail to `release`

**Files:**
- Modify: `tests/test_native_routing_context_invariant.py:358`
- Modify: `tests/test_viewer_board_oracles.py:94`
- Modify: `tests/test_native_routing_context_paging.py:21-22,71-101`
- Modify: `tests/test_store_related_incremental.py:94-95,191-198,331`
- Modify: `tests/test_store_concurrency.py:37-42,790-803` (plus a new test after L803)
- Modify: `tests/test_projection_conflict_property.py:27-29,326-341`
- Modify: `tests/test_native_cutover_crash.py:25,77-78,96-97`
- Modify: `tests/test_native_dependency_graph.py:131`
- Test: the same files (5 new merge-variant test IDs)

**Interfaces:**
- Consumes: Task 8's `release` marker and gate, and `-m "release or not release"` as "everything".
- Produces:
  - the moves table and the kept-list decisions below, for Task 11 to copy into `tests/README.md`
  - 5 new merge-tier test IDs:
    - `test_following_cursors_on_sampled_budgets_delivers_every_row_once_and_terminates[legacy|native]`
    - `test_incremental_related_matches_full_rebuild_over_the_first_hundred_seeds`
    - `test_mixed_public_tool_operations_across_two_processes_never_lose_a_write`
    - `test_three_generated_histories_lose_nothing_and_never_touch_protected_files`
  - a release set of 96 IDs: 95 moved plus the existing `scale` test

**Moves.** Worker-seconds are junit totals at 402c17c.

| File | What moves to `release` | Stays in `merge` (representative) | IDs moved | Worker-s moved |
|---|---|---|---|---|
| `test_native_routing_context_invariant` | seeds 23, 37, 41, 59 | seed 11 (first) | 4 | 89.2 |
| `test_viewer_board_oracles` | `seeded_random_sequences[981-legacy/native]` | seed 17 on both stores | 2 | 37.1 |
| `test_native_routing_context_paging` | full 52-budget sweep `[legacy/native]` | new `…_on_sampled_budgets…[legacy/native]`, every 6th budget | 2 | 29.8 |
| `test_store_related_incremental` | e2e seeds 1–23; the 600-seed direct test | e2e seed 0; new 100-seed direct test | 24 | 67.8 |
| `test_store_concurrency` | 4 processes × 200 ops | new 2 processes × 24 ops (full sweep, every assertion) | 1 | 194.2 |
| `test_projection_conflict_property` | 25-seed run | new seeds (0, 6, 22) | 1 | 115.8 |
| `test_native_cutover_crash` | 60 matrix cases | resume+rollback × {`backfill.entities`-exit, `activate:after-commit`-exception}; both interrupted-rollback cases | 60 | 144.9 |
| `test_native_dependency_graph` | `transitive…[3]` | `[2]` (first transitive level) and `[10]` (cycles/termination) | 1 | 6.2 |
| **Total** | | | **95** | **685** |

Why the representatives:
- **Paging and direct related seeds loop internally.** Their "seeds" are inner loops (52 budgets × 3 includes; 600 seeds), not parametrize cases. So the spec's `pytest.param(..., marks=release)` split cannot keep the test ID. The full test keeps its ID and its assertions and moves; a reduced sibling gets a new ID.
- **Paging sample.** `BUDGETS[::6]` = 900, 1170, …, 3060. It yields chains of 10, 5, 4, 3, 2 and 1 pages on both stores (probed 2026-09-29: all 27 chains per store finish; the full sweep finishes 156/156). It covers the multi-page chains the file's intent names.
- **Direct related seeds.** Seeds 0–99 gave 1,047 incremental vs 153 fallback checks. That satisfies `incremental > 5 * fallback > 0` (1,047 > 765) in 0.6 s.
- **Stress.** 24 operations covers each worker's deterministic 22-operation warm-up sweep (`SWEEP`, L566-568). Worker RNGs are seeded by index, so the classes that get populated are deterministic. Probed at 2×24: 64 tool calls, 0 refused, every asserted class non-empty, 9.1 s wall on a loaded machine. Two processes still prove the cross-process lock (defects 2 and 5).
- **Conflict property.** Seeds 0–24 were probed individually. No pair reaches all four file kinds and all six required events. (0, 6, 22) is the cheapest triple that does, at 6.4 s. Seed 0 is the first seed, so it doubles as the spec's representative.
- **Cutover crash.** `exit`@`backfill.entities` kills the process inside backfill's open transaction, mid-journal: fence, reconcile and backup are committed. `exception`@`activate:after-commit` is the roll-forward-only branch, the file header's third stated intent. The refusal branches ("no cutover journal", "no cutover fence", escape hatch) are also pinned in merge by `test_native_cutover.py:251,288-290,471`. Cutover runs once per project, so the full matrix is release evidence (spec §4.2). Note: every crash case is individually < 5 s, and so is every e2e related seed. They move because §4.2 names them, not because of the > 5 s rule.

**Estimate.** 685 worker-s move and the new variants add about 35 worker-s, a net of about 650 worker-s:
- stress ≈ 9 s
- conflict ≈ 6–14 s
- paging ≈ 10–12 s, including two twin builds
- related ≈ 1 s

Against the 402c17c profile (5,365 worker-s), the merge suite drops to about 4,715 worker-s before Step 1. With spec §9's Step 1 estimate (−2,300 worker-s), that is about 2,400 worker-s, or about 13.4 min wall at `-n 3` (wall ≈ worker-s ÷ 3). That excludes N16's 269 unprofiled tests. **G1 (≤ 10 min, about 1,800 worker-s) is not reached by these moves alone.** Spec §9 assumed 1,300–1,600 s would move, but the §4.2 rule yields about 650. Task 12 reports the gap.

- [ ] **Step 1: Snapshot every test ID before the moves (Task 8 must already be committed)**

```bash
mkdir -p test-results/tier-ids
.venv/Scripts/python.exe -m pytest tests --collect-only -q -p no:cacheprovider -m "release or not release" | grep "::" | LC_ALL=C sort > test-results/tier-ids/all-before.txt
.venv/Scripts/python.exe -m pytest tests --collect-only -q -p no:cacheprovider -m release | grep "::" | LC_ALL=C sort > test-results/tier-ids/release-before.txt
wc -l test-results/tier-ids/*-before.txt
```
Expected: `release-before.txt` holds 1 line (`test_mixed_public_tool_operations_at_acceptance_scale`). Run collect-only without `-n`, so no `@heavy_processes` suffixes appear.

- [ ] **Step 2: Write the four merge-variant tests (they call helpers that do not exist yet)**

`tests/test_native_routing_context_paging.py`, appended after L101:
```python
@pytest.mark.parametrize("side", ["legacy", "native"])
def test_following_cursors_on_sampled_budgets_delivers_every_row_once_and_terminates(twins, side):
    complete_chains = _follow_every_chain(twins, side, SAMPLED_BUDGETS)
    # Most sampled chains must finish, or the sample exercises nothing but stalls.
    assert complete_chains > len(INCLUDES) * len(SAMPLED_BUDGETS) // 2
```

`tests/test_store_related_incremental.py`, appended after L198:
```python
def test_incremental_related_matches_full_rebuild_over_the_first_hundred_seeds():
    _assert_direct_seeds_match(MERGE_DIRECT_SEEDS)
```

`tests/test_store_concurrency.py`, inserted after L803 (the end of the 4×200 test):
```python
@pytest.mark.xdist_group("heavy_processes")  # conftest: one multi-process test at a time
@pytest.mark.slow
def test_mixed_public_tool_operations_across_two_processes_never_lose_a_write(tmp_path):
    """The merge-tier profile: 2 processes x 24 operations, every assertion of the full run.

    24 operations cover each worker's deterministic 22-operation warm-up sweep, so every
    operation class and every asserted field class is exercised (2026-09-29: 64 tool calls,
    none refused, every class non-empty, 9 s). The 4 x 200 run is release evidence.
    """
    _run_mixed_stress(tmp_path, MERGE_PROCESSES, MERGE_OPS)
```

`tests/test_projection_conflict_property.py`, appended after L341:
```python
def test_three_generated_histories_lose_nothing_and_never_touch_protected_files(tmp_path):
    _assert_histories_lose_nothing(tmp_path, MERGE_SEEDS)
```

- [ ] **Step 3: Run them to verify they fail**

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider "tests/test_native_routing_context_paging.py::test_following_cursors_on_sampled_budgets_delivers_every_row_once_and_terminates" tests/test_store_related_incremental.py::test_incremental_related_matches_full_rebuild_over_the_first_hundred_seeds tests/test_store_concurrency.py::test_mixed_public_tool_operations_across_two_processes_never_lose_a_write tests/test_projection_conflict_property.py::test_three_generated_histories_lose_nothing_and_never_touch_protected_files
```
Expected: 5 failed, each with `NameError`: `_follow_every_chain`, `_assert_direct_seeds_match`, `MERGE_PROCESSES`, `_assert_histories_lose_nothing`.

- [ ] **Step 4: Implement the four whole-test moves (helpers, constants, `release` marks)**

`tests/test_native_routing_context_paging.py`, after L22 (`BUDGETS = range(900, 3200, 45)`):
```python
BUDGETS = range(900, 3200, 45)
# Every sixth budget (900, 1170, ... 3060): chains of 10, 5, 4, 3, 2 and 1 pages on both stores;
# on 2026-09-29 every sampled chain finished. The full sweep is release evidence.
SAMPLED_BUDGETS = BUDGETS[::6]
```

L71-101 are replaced by the following. The loop body is moved verbatim into the helper, and the original test keeps its ID and its assertion:
```python
def _follow_every_chain(twins, side, budgets) -> int:
    """Follow every include's cursor chain at each budget on one store; return how many finished."""
    root = getattr(twins, side)
    complete_chains = 0
    with twins.at(root):
        for include in INCLUDES:
            whole = _ask(include, 200000)
            everything = {name: [row["id"] for row in whole["selected"].get(name, [])]
                          for name in include}
            totals = {name: len(ids) for name, ids in everything.items()}
            assert whole.get("cursor", "") == "" and totals["notes"] == 3 and totals["siblings"] == 7
            for budget in budgets:
                delivered, pages = _chain(include, budget, totals)
                for name in include:
                    assert len(delivered[name]) == len(set(delivered[name])), \
                        f"budget {budget}: {name} re-delivered {delivered[name]}"
                    # A prefix of the section, in order: nothing skipped between pages.
                    assert delivered[name] == everything[name][:len(delivered[name])]
                last = pages[-1]
                if last["selected"]:
                    # The chain stopped because it was done, not because it stalled.
                    assert delivered == everything, f"budget {budget}: rows never delivered"
                    assert last["budget"].get("omitted_total", 0) == 0
                    complete_chains += 1
                else:
                    # A page that delivered nothing ends the chain and says, exactly,
                    # what the caller still does not have.
                    assert last["budget"].get("omitted_total", 0) == \
                        sum(totals.values()) - sum(map(len, delivered.values()))
    return complete_chains


@pytest.mark.release  # the full budget sweep; the sampled test below runs every merge
@pytest.mark.parametrize("side", ["legacy", "native"])
def test_following_cursors_delivers_every_row_once_and_terminates(twins, side):
    complete_chains = _follow_every_chain(twins, side, BUDGETS)
    # The range must actually exercise multi-page chains that finish.
    assert complete_chains > len(INCLUDES) * 10
```

`tests/test_store_related_incremental.py`, L94-95:
```python
# before
DIRECT_SEEDS = range(600)
DIRECT_STEPS = 12
# after
DIRECT_SEEDS = range(600)
# The merge run's slice: seeds 0-99 still exercise both branches (2026-09-29: 1,047 incremental,
# 153 fallback). All 600 are release evidence.
MERGE_DIRECT_SEEDS = range(100)
DIRECT_STEPS = 12
```

L191-198:
```python
# before
def test_incremental_related_matches_full_rebuild_over_seeded_edit_sequences():
    incremental = fallback = 0
    for seed in DIRECT_SEEDS:
        done, full = _run_direct_case(seed)
        incremental += done
        fallback += full
    # Both branches must actually be exercised, or equivalence proves nothing.
    assert incremental > 5 * fallback > 0, (incremental, fallback)
# after
def _assert_direct_seeds_match(seeds) -> None:
    incremental = fallback = 0
    for seed in seeds:
        done, full = _run_direct_case(seed)
        incremental += done
        fallback += full
    # Both branches must actually be exercised, or equivalence proves nothing.
    assert incremental > 5 * fallback > 0, (incremental, fallback)


@pytest.mark.release  # all 600 seeds; the first 100 run every merge
def test_incremental_related_matches_full_rebuild_over_seeded_edit_sequences():
    _assert_direct_seeds_match(DIRECT_SEEDS)
```

`tests/test_store_concurrency.py`, L37-42:
```python
# before
# The acceptance profile the plan specifies (M0/N16 evidence): run it with `-m scale`.
SCALE_PROCESSES, SCALE_OPS = 8, 200
# The default run's profile: half the processes, so its peak RAM stays near 0.5 GB.
# Overridable for local debugging only.
STRESS_PROCESSES = int(os.environ.get("TM_STRESS_PROCESSES", "4"))
STRESS_OPS = int(os.environ.get("TM_STRESS_OPS", "200"))
# after
# The acceptance profile the plan specifies (M0/N16 evidence): run it with `-m scale`.
SCALE_PROCESSES, SCALE_OPS = 8, 200
# The release run's profile: half the processes, so its peak RAM stays near 0.5 GB.
# Overridable for local debugging only.
STRESS_PROCESSES = int(os.environ.get("TM_STRESS_PROCESSES", "4"))
STRESS_OPS = int(os.environ.get("TM_STRESS_OPS", "200"))
# The merge run's profile: two processes, one full warm-up sweep each (SWEEP is 22 operations).
MERGE_PROCESSES, MERGE_OPS = 2, 24
```

L790-803:
```python
# before
@pytest.mark.xdist_group("heavy_processes")  # conftest: one multi-process test at a time
@pytest.mark.slow
def test_mixed_public_tool_operations_across_processes_never_lose_a_write(tmp_path):
    """The acceptance case: N processes x M real public tool calls on one store.

    The default run uses 4 processes x 200 operations (`TM_STRESS_PROCESSES` /
    `TM_STRESS_OPS` override it when debugging locally). The committed acceptance
    profile, 8 x 200, is `test_mixed_public_tool_operations_at_acceptance_scale`,
    marked `scale` and deselected by default; run it with
    `pytest tests/test_store_concurrency.py -m scale`.

    Marked `slow`: the fast development loop is `uv run pytest -q -m "not slow"`.
    """
    _run_mixed_stress(tmp_path, STRESS_PROCESSES, STRESS_OPS)
# after
@pytest.mark.release  # 194 s; `..._across_two_processes_...` checks the same invariants every merge
@pytest.mark.xdist_group("heavy_processes")  # conftest: one multi-process test at a time
@pytest.mark.slow
def test_mixed_public_tool_operations_across_processes_never_lose_a_write(tmp_path):
    """The acceptance case: N processes x M real public tool calls on one store.

    Release tier: 4 processes x 200 operations (`TM_STRESS_PROCESSES` / `TM_STRESS_OPS`
    override it when debugging locally). The merge suite runs the 2 x 24 profile. The
    committed acceptance profile, 8 x 200, is `test_mixed_public_tool_operations_at_acceptance_scale`,
    marked `scale` (selected by `-m scale` or `-m release`).

    Marked `slow`: the fast development loop is `uv run pytest -q -m "not slow"`.
    """
    _run_mixed_stress(tmp_path, STRESS_PROCESSES, STRESS_OPS)
```

`tests/test_projection_conflict_property.py`, L27-29:
```python
# before
CASES = int(os.environ.get("TM_CONFLICT_PROPERTY_CASES", "25"))
FIRST_SEED = int(os.environ.get("TM_CONFLICT_PROPERTY_SEED", "0"))
STEPS = 30
# after
CASES = int(os.environ.get("TM_CONFLICT_PROPERTY_CASES", "25"))
FIRST_SEED = int(os.environ.get("TM_CONFLICT_PROPERTY_SEED", "0"))
# The merge run's seeds: the cheapest set whose histories together reach every file kind and
# every required event (probed 2026-09-29 over seeds 0-24; no pair does). The rest are release evidence.
MERGE_SEEDS = (0, 6, 22)
STEPS = 30
```

L326-341:
```python
# before
def test_generated_histories_lose_nothing_and_never_touch_protected_files(tmp_path):
    flagged: set[str] = set()
    events: Counter[str] = Counter()
    for seed in range(FIRST_SEED, FIRST_SEED + CASES):
        ...
# after
def _assert_histories_lose_nothing(tmp_path: Path, seeds) -> None:
    flagged: set[str] = set()
    events: Counter[str] = Counter()
    for seed in seeds:
        ever_flagged, log = _run(seed, tmp_path / f"seed-{seed}")
        flagged |= {
            "tasks/core-001.md" if rel.startswith("tasks/") else rel
            for rel in ever_flagged
        }
        events.update(_event(entry) for entry in log)
    # The generator has to reach the states under test for every file kind and
    # both resolutions, or it proves nothing about them.
    assert flagged >= {"tasks/core-001.md", "epics/core.md", "backlog.yaml", "project.yaml"}, flagged
    for needed in ("take=file", "take=store", "repair with a new value",
                   "repair by restoring", "archive task", "break"):
        assert events[needed], (needed, events)


@pytest.mark.release  # all CASES seeds; MERGE_SEEDS run every merge
def test_generated_histories_lose_nothing_and_never_touch_protected_files(tmp_path):
    _assert_histories_lose_nothing(tmp_path, range(FIRST_SEED, FIRST_SEED + CASES))
```

- [ ] **Step 5: Run the new variants, and time them**

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --durations=0 "tests/test_native_routing_context_paging.py::test_following_cursors_on_sampled_budgets_delivers_every_row_once_and_terminates" tests/test_store_related_incremental.py::test_incremental_related_matches_full_rebuild_over_the_first_hundred_seeds tests/test_store_concurrency.py::test_mixed_public_tool_operations_across_two_processes_never_lose_a_write tests/test_projection_conflict_property.py::test_three_generated_histories_lose_nothing_and_never_touch_protected_files
for i in 1 2 3; do .venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_projection_conflict_property.py::test_three_generated_histories_lose_nothing_and_never_touch_protected_files tests/test_store_concurrency.py::test_mixed_public_tool_operations_across_two_processes_never_lose_a_write | tail -1; done
```
Expected:
- 5 passed.
- Each case ≤ 10 s setup + call on a quiet machine. Probed on a loaded machine: stress 9.1 s, conflict 6.4 s, related 0.6 s, paging about 2–5 s call plus twin setup.
- The 3× loop passes every time. It shows that the coverage unions of the two smallest variants do not depend on timing.

If a variant fails its coverage assertion (not its invariant):
- conflict: switch `MERGE_SEEDS` to `tuple(range(7))`, the smallest prefix that covers (probed).
- stress: raise `MERGE_OPS` to 40.

Record either change in the commit message. If a variant exceeds 10 s on a quiet machine, report the time to the orchestrator; do not shrink below the probed coverage.

- [ ] **Step 6: Apply the parametrize moves (IDs unchanged)**

`tests/test_native_routing_context_invariant.py:358`:
```python
# before
@pytest.mark.parametrize("seed", SEEDS)
# after
# The first seed runs every merge; the other seeds are release evidence (tests/README.md).
@pytest.mark.parametrize("seed", [SEEDS[0], *(pytest.param(seed, marks=pytest.mark.release) for seed in SEEDS[1:])])
```

`tests/test_viewer_board_oracles.py:94`. The IDs stay `[17-legacy]`, `[981-native]`, and so on:
```python
# before
@pytest.mark.parametrize('seed', [17, 981])
# after
@pytest.mark.parametrize('seed', [17, pytest.param(981, marks=pytest.mark.release)])  # 981: release evidence
```

`tests/test_store_related_incremental.py:331`:
```python
# before
@pytest.mark.parametrize("seed", list(E2E_SEEDS))
# after
# Seed 0 runs every merge; seeds 1-23 are release evidence.
@pytest.mark.parametrize("seed", [E2E_SEEDS[0], *(pytest.param(seed, marks=pytest.mark.release) for seed in E2E_SEEDS[1:])])
```

`tests/test_native_cutover_crash.py`, after L25 (`ACTIVATED = {...}`):
```python
# One crash point per mode runs every merge; cutover runs once per project, so the rest of the
# matrix is release evidence (spec 4.2). `exit` at `backfill.entities` kills the process inside
# backfill's open transaction (fence, reconcile and backup committed); `exception` at
# `activate:after-commit` is the roll-forward-only branch. The refusal branches are also pinned
# in test_native_cutover.py.
MERGE_CRASHES = {("backfill.entities", "exit"), ("activate:after-commit", "exception")}
CRASH_CASES = [
    pytest.param(point, mode, id=f"{point}-{mode}",
                 marks=() if (point, mode) in MERGE_CRASHES else pytest.mark.release)
    for point in POINTS for mode in ("exception", "exit")
]
```

L77-78 and L96-97. The explicit ids reproduce the stacked-parametrize ids `<point>-<mode>`, and the list order matches the stacked order (point outer, mode inner, verified in scratch):
```python
# before (both tests)
@pytest.mark.parametrize("mode", ["exception", "exit"])
@pytest.mark.parametrize("point", POINTS)
def test_crash_then_resume(project, point, mode, monkeypatch):
...
@pytest.mark.parametrize("mode", ["exception", "exit"])
@pytest.mark.parametrize("point", POINTS)
def test_crash_then_rollback(project, point, mode, monkeypatch):
# after
@pytest.mark.parametrize("point,mode", CRASH_CASES)
def test_crash_then_resume(project, point, mode, monkeypatch):
...
@pytest.mark.parametrize("point,mode", CRASH_CASES)
def test_crash_then_rollback(project, point, mode, monkeypatch):
```
`test_an_interrupted_rollback_changed_nothing_and_runs_again` (L144-145) is unchanged; both of its modes stay in merge.

`tests/test_native_dependency_graph.py:131`:
```python
# before
@pytest.mark.parametrize("depth", [2, 3, 10])
# after
@pytest.mark.parametrize("depth", [2, pytest.param(3, marks=pytest.mark.release), 10])  # 2: first level; 10: cycles
```

- [ ] **Step 7: Verify the per-file tier split**

```bash
for f in test_native_routing_context_invariant test_viewer_board_oracles test_native_routing_context_paging test_store_related_incremental test_store_concurrency test_projection_conflict_property test_native_cutover_crash test_native_dependency_graph; do printf "%-40s release=" $f; .venv/Scripts/python.exe -m pytest tests/$f.py --collect-only -q -p no:cacheprovider -m release | grep -c "::"; done
```
Expected release counts:

| File | release |
|---|---|
| `test_native_routing_context_invariant` | 4 |
| `test_viewer_board_oracles` | 2 |
| `test_native_routing_context_paging` | 2 |
| `test_store_related_incremental` | 24 |
| `test_store_concurrency` | 2 (4×200 + scale) |
| `test_projection_conflict_property` | 1 |
| `test_native_cutover_crash` | 60 |
| `test_native_dependency_graph` | 1 |
| **Total** | **96** |

Then run the merge tier of the touched files:
```bash
.venv/Scripts/python.exe -m pytest -n 3 -q -p no:cacheprovider tests/test_native_routing_context_invariant.py tests/test_viewer_board_oracles.py tests/test_native_routing_context_paging.py tests/test_store_related_incremental.py tests/test_store_concurrency.py tests/test_projection_conflict_property.py tests/test_native_cutover_crash.py tests/test_native_dependency_graph.py
```
Expected: all pass. There is no `slow tests outside release` section, except the pre-existing kept tests listed below if they exceed 10 s on this machine, such as `test_one_touched_key_does_not_compare_every_pair_of_path_rows`.

- [ ] **Step 8: Prove no test ID was lost (whole suite, collect-only)**

```bash
.venv/Scripts/python.exe -m pytest tests --collect-only -q -p no:cacheprovider -m "release or not release" | grep "::" | LC_ALL=C sort > test-results/tier-ids/all-after.txt
.venv/Scripts/python.exe -m pytest tests --collect-only -q -p no:cacheprovider | grep "::" | LC_ALL=C sort > test-results/tier-ids/merge-after.txt
.venv/Scripts/python.exe -m pytest tests --collect-only -q -p no:cacheprovider -m release | grep "::" | LC_ALL=C sort > test-results/tier-ids/release-after.txt
echo "removed:"; LC_ALL=C comm -23 test-results/tier-ids/all-before.txt test-results/tier-ids/all-after.txt
echo "added:";   LC_ALL=C comm -13 test-results/tier-ids/all-before.txt test-results/tier-ids/all-after.txt
echo "overlap:"; LC_ALL=C comm -12 test-results/tier-ids/merge-after.txt test-results/tier-ids/release-after.txt | wc -l
LC_ALL=C sort test-results/tier-ids/merge-after.txt test-results/tier-ids/release-after.txt | diff - test-results/tier-ids/all-after.txt && echo "merge + release == all"
wc -l test-results/tier-ids/release-after.txt
```
Expected:
- `removed:` is empty.
- `added:` is exactly these 5 lines:
  ```
  tests/test_native_routing_context_paging.py::test_following_cursors_on_sampled_budgets_delivers_every_row_once_and_terminates[legacy]
  tests/test_native_routing_context_paging.py::test_following_cursors_on_sampled_budgets_delivers_every_row_once_and_terminates[native]
  tests/test_projection_conflict_property.py::test_three_generated_histories_lose_nothing_and_never_touch_protected_files
  tests/test_store_concurrency.py::test_mixed_public_tool_operations_across_two_processes_never_lose_a_write
  tests/test_store_related_incremental.py::test_incremental_related_matches_full_rebuild_over_the_first_hundred_seeds
  ```
  (4 new test functions; the paging one contributes 2 IDs)
- `overlap:` is `0`.
- `merge + release == all` prints.
- `release-after.txt` has 96 lines.

- [ ] **Step 9: Cross-check the kept list against Task 1's quiet baseline**

The tables in this task come from the noisy 402c17c profile, which predates N16's 269 tests. From Task 1's baseline durations log, list every test whose setup + call is over 5 s and that is not in the moves table or the kept table below. Apply the same rule to each: move it only if it is a repeat of a scenario that keeps a representative in merge. Add a row to the kept table (or to the moves, with the same pattern and a Step 8 re-run) and state the decision. If none appear, write "none".

- [ ] **Step 10: Commit**

```bash
git add tests/test_native_routing_context_invariant.py tests/test_viewer_board_oracles.py tests/test_native_routing_context_paging.py tests/test_store_related_incremental.py tests/test_store_concurrency.py tests/test_projection_conflict_property.py tests/test_native_cutover_crash.py tests/test_native_dependency_graph.py
git commit -m "test(speed): move repeat seeds, full stress runs and the cutover crash matrix to release

95 IDs move (685 worker-s at 402c17c); each moved scenario keeps a merge representative.
Paging, direct related seeds, the 4x200 stress run and the conflict property keep their IDs
in release and gain reduced merge variants (5 new IDs). merge + release == every ID; none lost.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

**Kept in `merge`: every other test over 5 s (setup + call) in the 402c17c profile.** There are 121 rows totalling about 1,040 s. Decision codes:
- **U:** a unique scenario; no sibling in merge covers it.
- **B:** one backend half of a twin pair. Legacy and native are distinct scenarios; pruning the legacy half is Step 3 (§5), deferred.
- **S:** setup-dominated (twin build); §3.3 templating addresses it, not a tier move.
- **W:** a distinct window, state or configuration of a matrix, where each member exercises a different recovery or behaviour branch. Unlike cutover, managed Git and PROGRESS run on every write, so these are not repeats.

The `@heavy_processes` suffixes are xdist loadgroup decorations and are omitted.

| Test (under tests/) | s (setup+call) | of which setup | Decision |
|---|---|---|---|
| `test_agent_journeys.py::test_both_stores_give_the_same_required_context` | 8.7 | 0.0 | keep U |
| `test_agent_journeys.py::test_the_new_path_yields_every_required_fact[orient-legacy]` | 9.7 | 9.1 | keep B,S |
| `test_agent_journeys.py::test_the_new_path_yields_every_required_fact[orient-native]` | 9.5 | 9.0 | keep B,S |
| `test_list_tasks_limit.py::test_default_caps_at_50_with_overflow_footer` | 7.3 | 0.5 | keep U |
| `test_list_tasks_limit.py::test_limit_zero_returns_all` | 7.2 | 0.4 | keep U |
| `test_list_tasks_limit.py::test_truncated_header_shows_total` | 7.1 | 0.3 | keep U |
| `test_native_claim_terminal_and_readers.py::test_a_viewer_patch_cannot_set_or_erase_a_holder` | 7.2 | 1.9 | keep U |
| `test_native_claim_terminal_and_readers.py::test_a_viewer_status_change_follows_the_tools_claim_rules` | 5.9 | 1.4 | keep U |
| `test_native_context_compact.py::test_the_notes_section_is_the_whole_desk_pinned_first[legacy]` | 5.1 | 5.0 | keep B,S |
| `test_native_context_compact.py::test_the_notes_section_is_the_whole_desk_pinned_first[native]` | 5.0 | 4.9 | keep B,S |
| `test_native_dependency_graph.py::test_default_output_is_the_frozen_pre_n14_answer` | 19.2 | 6.0 | keep U |
| `test_native_dependency_graph.py::test_transitive_answers_match_on_both_stores[2]` | 13.0 | 7.2 | keep W (depth 2 = first transitive level; [10] is termination; [3] moves) |
| `test_native_git_checkouts.py::test_managed_checkout_in_a_linked_worktree_holds_drift_only_there` | 6.5 | 1.0 | keep U |
| `test_native_git_checkouts_review.py::test_in_progress_operation_holds_every_differing_file[merge-no-commit]` | 5.1 | 1.1 | keep W (in-progress op kind) |
| `test_native_git_checkouts_review.py::test_unknown_previous_observation_is_judged_by_bytes` | 5.8 | 1.0 | keep U |
| `test_native_git_crash.py::test_checkout_killed_mid_unpack_is_ambiguous_and_stays_pinned` | 7.4 | 0.6 | keep U |
| `test_native_git_crash.py::test_crash_after_proven_quiescence_recovers_without_acknowledgement` | 7.5 | 0.8 | keep U |
| `test_native_git_crash.py::test_linked_worktree_op_killed_is_recovered_in_that_worktree[active_git]` | 8.5 | 0.6 | keep W (job window) |
| `test_native_git_crash.py::test_linked_worktree_op_killed_is_recovered_in_that_worktree[completed_pre_receipt]` | 8.8 | 0.6 | keep W (job window) |
| `test_native_git_crash.py::test_linked_worktree_op_killed_is_recovered_in_that_worktree[pre_permission]` | 8.3 | 0.6 | keep W (job window) |
| `test_native_git_crash.py::test_replacement_cannot_publish_before_the_job_is_proven_empty[active_git]` | 7.3 | 0.7 | keep W (job window) |
| `test_native_git_crash.py::test_replacement_cannot_publish_before_the_job_is_proven_empty[completed_pre_receipt]` | 7.5 | 0.6 | keep W (job window) |
| `test_native_git_crash.py::test_replacement_cannot_publish_before_the_job_is_proven_empty[pre_assignment]` | 6.5 | 0.6 | keep W (job window) |
| `test_native_git_crash.py::test_replacement_cannot_publish_before_the_job_is_proven_empty[pre_permission]` | 6.9 | 0.6 | keep W (job window) |
| `test_native_git_hook.py::test_unmanaged_projection_staging_is_refused_with_guidance` | 5.4 | 1.0 | keep U |
| `test_native_git_managed.py::test_checkout_drift_can_be_released_explicitly` | 6.0 | 1.0 | keep U |
| `test_native_git_managed.py::test_exporter_stays_idle_under_a_startup_pin` | 5.3 | 1.2 | keep U |
| `test_native_git_managed.py::test_managed_checkout_reports_drift_and_never_rolls_back` | 8.2 | 1.1 | keep U |
| `test_native_git_managed.py::test_nothing_to_commit_is_a_completed_no_op` | 5.6 | 1.1 | keep U |
| `test_native_git_managed.py::test_retry_of_an_older_request_never_reruns_git` | 6.5 | 1.1 | keep U |
| `test_native_graph_repair.py::test_link_before_a_task_target` | 5.6 | 1.1 | keep U |
| `test_native_n13_aside_review.py::test_l1_move_error_restores_and_settles_as_refused` | 8.8 | 1.3 | keep U |
| `test_native_n13_aside_review.py::test_l1_transient_sharing_violation_on_move_is_retried` | 8.5 | 1.2 | keep U |
| `test_native_n13_aside_review.py::test_l2_a_file_without_a_retained_base_is_never_set_aside` | 8.3 | 1.3 | keep U |
| `test_native_n13_aside_review.py::test_m1_changed_file_that_cannot_be_put_back_is_kept_and_pins` | 8.6 | 1.6 | keep U |
| `test_native_n13_aside_review.py::test_m1_crash_between_move_and_digest_check_never_drops_changed_bytes` | 8.2 | 1.2 | keep U |
| `test_native_n13_aside_review.py::test_m2_helper_start_failure_puts_set_aside_files_back` | 8.2 | 1.2 | keep U |
| `test_native_n13_d8_linked_eol.py::test_d8_managed_commit_in_linked_worktree_of_a_mixed_generation[autocrlf]` | 8.2 | 1.1 | keep W (line-ending config) |
| `test_native_n13_d8_linked_eol.py::test_d8_managed_commit_in_linked_worktree_of_a_mixed_generation[eol-lf]` | 9.0 | 1.2 | keep W (line-ending config) |
| `test_native_n13_drift_fixes.py::test_d5_bypassed_restore_of_a_derived_index_is_reported_and_rerendered` | 5.5 | 1.2 | keep U |
| `test_native_n13_drift_fixes.py::test_d5_import_is_refused_for_a_derived_index_and_sync_agrees_with_git` | 5.5 | 1.2 | keep U |
| `test_native_n13_drift_fixes.py::test_d5_take_published_rerenders_a_drifted_derived_index` | 5.6 | 2.1 | keep U |
| `test_native_n13_drift_fixes.py::test_d6_crash_after_setting_files_aside_is_undone_by_recovery` | 9.4 | 1.2 | keep U |
| `test_native_n13_drift_fixes.py::test_d6_linked_checkout_round_trip` | 9.4 | 1.3 | keep U |
| `test_native_n13_drift_fixes.py::test_d6_managed_checkout_back_after_take_published` | 8.5 | 1.2 | keep U |
| `test_native_n13_drift_fixes.py::test_d6_managed_checkout_back_never_overwrites_authored_bytes` | 8.5 | 1.5 | keep U |
| `test_native_n13_drift_fixes.py::test_d6_managed_checkout_back_while_drift_is_held` | 8.4 | 1.3 | keep U |
| `test_native_n13_drift_fixes.py::test_d7_fresh_linked_worktree_of_a_mixed_generation_is_not_held[eol-lf]` | 5.6 | 1.7 | keep W (line-ending config) |
| `test_native_n13_drift_fixes.py::test_d7_git_line_ending_normalisation_is_not_drift_in_main[autocrlf]` | 7.1 | 1.3 | keep W (line-ending config) |
| `test_native_n13_drift_fixes.py::test_d7_git_line_ending_normalisation_is_not_drift_in_main[eol-lf]` | 6.9 | 1.2 | keep W (line-ending config) |
| `test_native_n13_fastpath_fixes.py::test_a_change_no_fingerprint_shows_is_read_once_the_cache_ages_out` | 11.1 | 4.7 | keep U |
| `test_native_n13_fastpath_fixes.py::test_absent_budget_fits_an_old_clients_read_timeout` | 9.1 | 4.1 | keep U |
| `test_native_n13_fastpath_fixes.py::test_git_rewrite_between_discovery_and_detection_is_never_imported` | 12.6 | 4.6 | keep U |
| `test_native_n13_fastpath_fixes.py::test_managed_commit_never_trusts_a_fingerprint_for_the_generation` | 11.0 | 4.8 | keep U |
| `test_native_progress.py::test_a_killed_process_at_every_checkpoint_loses_nothing_and_leaves_no_temp[progress_applying]` | 5.5 | 3.7 | keep W (checkpoint) |
| `test_native_progress.py::test_more_pending_paragraphs_than_the_cap_are_all_written` | 8.1 | 2.3 | keep U |
| `test_native_projection.py::test_retention_keeps_one_export_record_per_file` | 12.3 | 0.8 | keep U |
| `test_native_projection_review2.py::test_a_kill_at_every_step_recovers_and_never_loses_a_newcomer[USER NEWCOMER\n-before_replace]` | 5.2 | 2.7 | keep W (publish step) |
| `test_native_projection_review2.py::test_repeated_kills_with_the_file_aside_recover_to_the_last_revision` | 12.2 | 1.1 | keep U |
| `test_native_projection_today.py::test_twin_projections_are_byte_identical_after_a_mixed_journey` | 6.5 | 1.3 | keep U |
| `test_native_routing_epics_phases.py::test_add_epic_matches[kwargs1]` | 5.8 | 5.3 | keep S (distinct input) |
| `test_native_routing_epics_phases.py::test_archive_epic_cascade_matches` | 5.1 | 1.8 | keep U,S |
| `test_native_routing_epics_phases.py::test_phase_deliverables_sequence_and_advance_match` | 5.7 | 2.0 | keep U,S |
| `test_native_routing_epics_phases.py::test_status_reads_match` | 6.0 | 2.1 | keep U,S |
| `test_native_routing_epics_phases.py::test_update_epic_matches[docs-design:docs/d.md]` | 5.8 | 3.9 | keep S (distinct input) |
| `test_native_routing_epics_phases.py::test_update_phase_matches[dev-docs-nope:y]` | 5.9 | 5.6 | keep S (distinct input) |
| `test_native_routing_epics_phases.py::test_update_phase_matches[dev-docs-x]` | 5.6 | 5.2 | keep S (distinct input) |
| `test_native_routing_handovers.py::test_handover_index_cap_archives_overflow` | 19.5 | 1.0 | keep U |
| `test_native_routing_handovers.py::test_handover_reads_match` | 5.1 | 1.4 | keep U |
| `test_native_routing_hooks.py::test_edit_hook_answers_match_across_stores_over_seeded_edits` | 9.5 | 3.1 | keep U |
| `test_native_routing_hooks.py::test_merge_gate_decides_from_live_native_rows` | 5.3 | 3.6 | keep U |
| `test_native_routing_links_areas.py::test_areas_match` | 6.0 | 1.3 | keep U |
| `test_native_routing_links_areas.py::test_link_create_and_remove_match` | 7.2 | 1.4 | keep U |
| `test_native_routing_overview.py::test_project_manifest_tools_match` | 5.8 | 2.2 | keep U |
| `test_native_routing_records.py::test_bug_lifecycle_matches` | 9.4 | 1.9 | keep U |
| `test_native_routing_records.py::test_bug_reads_match` | 6.2 | 1.9 | keep U |
| `test_native_routing_records.py::test_decision_lifecycle_and_reads_match` | 8.7 | 1.5 | keep U |
| `test_native_routing_records.py::test_idea_lifecycle_and_reads_match` | 12.7 | 1.5 | keep U |
| `test_native_routing_records.py::test_issue_lifecycle_matches` | 9.9 | 1.6 | keep U |
| `test_native_routing_records.py::test_issue_reads_match` | 7.5 | 1.7 | keep U |
| `test_native_routing_tasks.py::test_dependencies_and_next_available_match` | 7.7 | 4.3 | keep U |
| `test_native_routing_tasks.py::test_gates_pipeline_and_completion_match` | 10.3 | 3.4 | keep U |
| `test_native_routing_tasks.py::test_get_task_views_match` | 10.9 | 4.0 | keep U |
| `test_native_routing_tasks.py::test_in_review_completion_and_archive_match` | 6.1 | 1.2 | keep U |
| `test_native_routing_tasks.py::test_lane_transition_table_is_enforced_the_same_way` | 5.3 | 1.5 | keep U |
| `test_native_routing_tasks.py::test_list_tasks_matches[kwargs2]` | 5.1 | 4.7 | keep S (distinct input) |
| `test_native_routing_tasks.py::test_list_tasks_matches[kwargs4]` | 5.5 | 5.3 | keep S (distinct input) |
| `test_native_routing_tasks.py::test_list_tasks_matches[kwargs5]` | 6.0 | 5.8 | keep S (distinct input) |
| `test_native_routing_tasks.py::test_list_tasks_matches[kwargs7]` | 5.8 | 5.6 | keep S (distinct input) |
| `test_native_routing_tasks.py::test_list_tasks_matches[kwargs9]` | 6.0 | 5.8 | keep S (distinct input) |
| `test_native_routing_tasks.py::test_merge_and_spec_review_match` | 5.5 | 1.6 | keep U |
| `test_native_routing_viewer.py::test_board_and_detail_reads_match` | 35.9 | 2.9 | keep U |
| `test_native_routing_viewer.py::test_continuity_writes_match` | 26.6 | 2.8 | keep U |
| `test_native_routing_viewer.py::test_task_edits_match_including_preconditions` | 22.0 | 2.8 | keep U |
| `test_native_routing_viewer.py::test_unservable_native_store_refuses_every_verb_with_a_json_body` | 6.9 | 2.7 | keep U |
| `test_native_service_progress.py::test_later_barrier_cannot_hide_earlier_progress_debt[completion]` | 13.6 | 2.3 | keep W (debt source) |
| `test_native_service_progress.py::test_later_barrier_cannot_hide_earlier_progress_debt[legacy_list]` | 12.2 | 0.7 | keep W (debt source) |
| `test_native_service_progress.py::test_later_barrier_cannot_hide_earlier_progress_debt[seed_row]` | 12.9 | 1.8 | keep W (debt source) |
| `test_native_service_progress.py::test_later_barrier_cannot_hide_earlier_progress_debt[unseeded]` | 11.6 | 0.7 | keep W (debt source) |
| `test_native_service_sync.py::test_completion_receipt_failure_is_pending[timeout]` | 6.7 | 0.8 | keep W (failure mode) |
| `test_native_service_sync.py::test_junctioned_job_file_is_a_per_job_refusal_not_a_drain_failure` | 11.0 | 0.6 | keep U |
| `test_native_sync_perf.py::test_edit_after_warm_sync_is_imported` | 7.7 | 2.8 | keep U |
| `test_native_sync_perf.py::test_first_linked_sync_reads_each_file_once` | 12.4 | 5.8 | keep U |
| `test_native_sync_perf.py::test_named_resync_reads_even_when_the_fingerprint_matches` | 6.5 | 2.2 | keep U |
| `test_native_sync_perf.py::test_warm_linked_sync_reads_no_file` | 8.7 | 2.2 | keep U |
| `test_native_sync_perf.py::test_warm_no_edit_full_sync_reads_no_file_and_resolves_per_directory` | 10.8 | 5.6 | keep U |
| `test_store_bug_cluster.py::test_writer_wait_notices_never_abort_interpreter_shutdown` | 6.0 | 0.0 | keep U |
| `test_store_concurrency.py::test_thread_pool_status_human_action_and_branch_writes_all_persist` | 13.2 | 0.8 | keep U |
| `test_store_entity_round_trips.py::test_handover_get_reads_rows_for_live_and_archived_handovers` | 7.7 | 0.0 | keep U |
| `test_store_related_incremental.py::test_a_real_write_pairs_only_the_rows_it_touched` | 5.7 | 0.0 | keep U |
| `test_store_related_incremental.py::test_one_touched_key_does_not_compare_every_pair_of_path_rows` | 22.5 | 0.0 | keep U (quadratic guard; needs n=2000) |
| `test_viewer_board.py::test_delta_and_resync[legacy]` | 5.8 | 3.4 | keep B,S |
| `test_viewer_board.py::test_delta_and_resync[native]` | 5.3 | 2.9 | keep B,S |
| `test_viewer_board_detail.py::test_combined_detail_twins` | 5.5 | 4.4 | keep U |
| `test_viewer_board_oracles.py::test_board_bytes_do_not_depend_on_session[legacy]` | 5.1 | 4.6 | keep B,S |
| `test_viewer_board_oracles.py::test_delta_equals_fresh_snapshot_over_edits[legacy]` | 13.4 | 5.0 | keep B,S |
| `test_viewer_board_oracles.py::test_delta_equals_fresh_snapshot_over_edits[native]` | 12.0 | 5.4 | keep B,S |
| `test_viewer_board_oracles.py::test_phase_epic_and_archive_deltas_match_full[legacy]` | 6.6 | 3.6 | keep B,S |
| `test_viewer_board_oracles.py::test_phase_epic_and_archive_deltas_match_full[native]` | 5.4 | 3.2 | keep B,S |
| `test_viewer_board_oracles.py::test_unrelated_entity_writers_preserve_revision_and_bytes[legacy]` | 10.0 | 3.7 | keep B,S |
| `test_viewer_board_oracles.py::test_unrelated_entity_writers_preserve_revision_and_bytes[native]` | 8.0 | 3.0 | keep B,S |

---

---

### Task 10: Test runner `scripts/run_tests.py`

**Files:**
- Create: `scripts/run_tests.py`
- Test: `tests/test_run_tests.py`
- Modify: `scripts/run_detached_check.py:1-6` (docstring only)

**Interfaces:**
- Consumes:
  - `scripts.analyze_test_durations.main(["--junit", PATH, "--top", "10", "--warn-over", "10"])` (Task 1).
  - `tests._temp_cleanup.pid_alive(pid) -> bool` (Task 2).
  - Task 8's conftest gate (draft D): a `release` test is kept only when the `-m` expression contains `release`; a `scale` test when it contains `scale` or `release`.
  - Task 4's templating reads `TASKMASTER_TEMPLATE_VERIFY=1`.
  - `tests.native_git_helpers.git`, `init_repo` (existing, `tests/native_git_helpers.py:12-31`) in the tests.
- Produces:
  - CLI `python scripts/run_tests.py {changed,merge,release} [-n N] [--base REF] [--detach] [--dry-run] [--force-ram] [-- extra pytest args]`; exit codes: pytest's own (5 counts as 0 for `changed`), `10` lock held, `11` low RAM, `12` git failed.
  - `select_changed_tests(changed_paths: list[str], test_sources: dict[str, str]) -> list[str] | None` (None = everything), `module_names`, `direct_imports`, `mention_needles`, `changed_files(base, cwd=REPO)`, `collect_test_sources(root=REPO)`.
  - `build_pytest_command(command, *, workers, targets, out_dir, extra) -> list[str]`, `pytest_env(command, base=None)`, `RELEASE_MARKEXPR = "release or not release"`.
  - `lock_path(repo=REPO) -> Path` (the main checkout's `test-results/.run_tests.lock`, shared by all worktrees), `acquire_lock`, `record_child(path, child_pid)`, `release_lock`, `live_holder`, `describe_holder`, `LockHeld`. Lock record: `{"pid", "command", "started", "child_pid"}`.
  - `git_failure(error) -> str`; `wait_for_start(child, started, timeout=DETACH_START_TIMEOUT) -> int | None`, `DETACH_START_TIMEOUT = 10.0`.
  - `MIN_FREE_BYTES = 2 * 1024**3`, `free_memory_bytes()`, `parse_meminfo(text)`, `ram_refusal(free, force)`.
  - Output dir `test-results/<command>-<YYYYmmdd-HHMMSS>/` with `junit.xml`, `pytest.log` (and `runner.log` when detached).

Design decisions, each pinned by a test:

- **`release` marker expression.** `release or not release` (the coordinator's choice, equal to draft D's `ALL_TIERS_MARKEXPR`) names `release`, so Task 8's gate keeps `release` tests and `scale` tests with them, and it is true for every marker combination, so nothing else drops out. A test evaluates it with pytest's own `Expression` for `{}`, `{release}`, `{scale}`, `{release, scale}`, `{durability}`.
- **`changed` selection.** Spec §4.1 rules 1-4, applied to every Python module and every changed file (see the drafter notes 1-2 at the top for why the rule scope is wider than the spec's list). Imports are read with `ast.walk`, so the function-level imports the conftest and many tests use count. `tests/` is on `sys.path` (`tests/conftest.py:91-93`), so `from native_twins import …` and `from test_native_service import …` resolve to `tests/…`. Relative imports resolve against the test's package. A test file that does not parse is selected for any Python change.
- **Changed set.** `git diff --name-only --no-renames <merge-base>` (working tree against the merge-base: committed and uncommitted, both sides of a rename) plus `git ls-files --others --exclude-standard`. `core.quotepath=off` keeps non-ASCII paths literal. `GIT_*` variables are dropped, as `tests/native_git_helpers.py:13` does. A ref that does not resolve, or a merge-base that fails (no common history), is refused with exit 12 and git's message; it never becomes an empty change set, which would read as "no tests selected" and exit 0.
- **Lock.** `O_CREAT | O_EXCL` create holding `{"pid", "command", "started"}`; the runner adds `child_pid` right after starting pytest. A lock is stale only when the runner **and** its pytest child are both dead (the reaper can kill the runner alone), and then it is taken over; an unparseable lock younger than 60 s counts as held (a runner mid-write), older as stale; release removes only our own lock.
- **RAM.** `GlobalMemoryStatusEx().ullAvailPhys` on Windows, `MemAvailable` on Linux, unknown elsewhere (warn and continue).
- **Output.** pytest's stdout and stderr go to `pytest.log` only, so an agent's context gets just the failure summary and the analyzer's top 10. `--warn-over 10` is passed for `changed` and `merge`, where every test is outside `release` by construction; `release` runs can't tell from junit which tests carry the marker, so they skip the warning. Output streams use `errors="replace"`: a cp1252 console must not crash on pytest's output.
- **Detach.** The parent checks the lock (fast refusal), creates the output dir and relaunches itself with `--out-dir` and without `--detach`, windowless in its own process group (`run_detached_check.py`'s flags), stdout to `runner.log`. The child takes the lock with its own pid. The parent then waits, up to 10 s, until the child has created `pytest.log` (past its lock and RAM checks). If the child exits first, the parent returns its exit code and prints its output instead of "Started PID".

- [ ] **Step 1: Write the failing tests**

Create `tests/test_run_tests.py`:

```python
# User intent: the runner decides what a review round actually tests and refuses unsafe starts;
# its selection rules, command lines, lock and RAM check are pinned here.
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest
from _pytest.mark.expression import Expression

from scripts import run_tests
from tests.native_git_helpers import git, init_repo

SOURCES = {
    "tests/test_store_thing.py": "from taskmaster import store\n",
    "tests/test_native_proj.py": "from taskmaster.native import projection\n",
    "tests/test_native_pkg.py": "import taskmaster.native\n",
    "tests/test_twins_user.py": "from native_twins import make_twins\n",
    "tests/test_relative.py": "from .entity_helpers import make\n",
    "tests/test_service_user.py": "from test_native_service import root\n",
    "tests/test_native_service.py": "import pytest\n",
    "tests/test_lazy.py": "def test_x():\n    from taskmaster.coordinator.service import main\n",
    "tests/test_skill_lint.py": "SKILL = ROOT / 'skills' / 'pick-task' / 'SKILL.md'\n",
    "tests/test_hook_loader.py": "paths = [HOOKS / f'{name}.py' for name in ('merge_gate',)]\n",
}


def _finished_pid() -> int:
    done = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True)
    return int(done.stdout)


@pytest.mark.parametrize(("changed", "expected"), [
    (["tests/test_native_service.py"], ["tests/test_native_service.py", "tests/test_service_user.py"]),
    (["taskmaster/native/projection.py"], ["tests/test_native_proj.py"]),
    (["taskmaster/native/__init__.py"], ["tests/test_native_pkg.py", "tests/test_native_proj.py"]),
    (["taskmaster/store.py"], ["tests/test_store_thing.py"]),
    (["tests/native_twins.py"], ["tests/test_twins_user.py"]),
    (["tests/entity_helpers.py"], ["tests/test_relative.py"]),
    (["taskmaster/coordinator/service.py"], ["tests/test_lazy.py"]),
    (["skills/pick-task/SKILL.md"], ["tests/test_skill_lint.py"]),
    (["hooks/merge_gate.py"], ["tests/test_hook_loader.py"]),
    (["taskmaster\\native\\projection.py"], ["tests/test_native_proj.py"]),
    (["docs/specs/unrelated.md", "CHANGELOG.md"], []),
])
def test_select_changed_tests_applies_the_direct_and_mention_rules(changed, expected):
    assert run_tests.select_changed_tests(changed, SOURCES) == expected


@pytest.mark.parametrize("trigger", ["tests/conftest.py", "pyproject.toml"])
def test_conftest_or_pyproject_selects_everything(trigger):
    assert run_tests.select_changed_tests(["taskmaster/store.py", trigger], SOURCES) is None


def test_a_test_file_that_does_not_parse_is_selected_for_any_python_change():
    sources = {**SOURCES, "tests/test_broken.py": "def oops(:\n"}
    assert "tests/test_broken.py" in run_tests.select_changed_tests(["taskmaster/paths.py"], sources)
    assert "tests/test_broken.py" not in run_tests.select_changed_tests(["docs/x.md"], sources)


def test_changed_files_cover_committed_uncommitted_and_untracked_work(tmp_path):
    init_repo(tmp_path)  # branch `main`, one commit
    (tmp_path / "a.txt").write_text("1", encoding="utf-8")
    git(tmp_path, "add", "a.txt")
    git(tmp_path, "commit", "-q", "-m", "a")
    git(tmp_path, "switch", "-q", "-c", "work")
    (tmp_path / "b.txt").write_text("1", encoding="utf-8")
    git(tmp_path, "add", "b.txt")
    git(tmp_path, "commit", "-q", "-m", "b")
    (tmp_path / "a.txt").write_text("2", encoding="utf-8")
    (tmp_path / "c.txt").write_text("1", encoding="utf-8")
    assert run_tests.changed_files("main", cwd=tmp_path) == ["a.txt", "b.txt", "c.txt"]


def test_merge_runs_the_default_suite(tmp_path):
    command = run_tests.build_pytest_command("merge", workers=3, targets=None, out_dir=tmp_path, extra=["-x"])
    assert command == [sys.executable, "-m", "pytest", "tests", "-n", "3", "--durations=25",
                       f"--junitxml={tmp_path / 'junit.xml'}", "-x"]
    assert "TASKMASTER_TEMPLATE_VERIFY" not in run_tests.pytest_env("merge", {})


def test_changed_runs_only_the_selected_files(tmp_path):
    command = run_tests.build_pytest_command("changed", workers=2, targets=["tests/test_a.py", "tests/test_b.py"],
                                             out_dir=tmp_path, extra=[])
    assert command[3:7] == ["tests/test_a.py", "tests/test_b.py", "-n", "2"]


def test_release_selects_every_marker_combination_and_verifies_templates(tmp_path):
    command = run_tests.build_pytest_command("release", workers=3, targets=None, out_dir=tmp_path, extra=[])
    expression = command[command.index("-m", 3) + 1]
    assert expression == run_tests.RELEASE_MARKEXPR
    assert "release" in expression  # Task 8's gate keeps release and scale tests once `release` is named
    compiled = Expression.compile(expression)
    for marks in (set(), {"release"}, {"scale"}, {"release", "scale"}, {"durability"}):
        assert compiled.evaluate(lambda name, /, **kwargs: name in marks), marks
    assert run_tests.pytest_env("release", {})["TASKMASTER_TEMPLATE_VERIFY"] == "1"


def test_no_tests_collected_is_a_pass_only_for_changed():
    assert run_tests.final_exit_code("changed", 5) == 0
    assert run_tests.final_exit_code("merge", 5) == 5
    assert run_tests.final_exit_code("changed", 1) == 1


def test_the_lock_is_single_flight_and_released_by_its_owner(tmp_path):
    lock = tmp_path / ".run_tests.lock"
    run_tests.acquire_lock(lock, "pytest tests")
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid()
    with pytest.raises(run_tests.LockHeld, match=f"pid {os.getpid()}"):
        run_tests.acquire_lock(lock, "pytest tests")
    run_tests.release_lock(lock)
    assert not lock.exists()


def test_a_dead_runners_lock_is_taken_over(tmp_path):
    lock = tmp_path / ".run_tests.lock"
    lock.write_text(json.dumps({"pid": _finished_pid(), "command": "old", "started": "earlier"}), encoding="utf-8")
    run_tests.acquire_lock(lock, "pytest tests")
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_release_leaves_another_processes_lock_alone(tmp_path):
    lock = tmp_path / ".run_tests.lock"
    lock.write_text(json.dumps({"pid": os.getpid() + 1, "command": "x", "started": "y"}), encoding="utf-8")
    run_tests.release_lock(lock)
    assert lock.exists()


def test_an_unreadable_lock_is_held_briefly_then_stale(tmp_path):
    lock = tmp_path / ".run_tests.lock"
    lock.write_text("", encoding="utf-8")
    assert run_tests.live_holder(lock, now=time.time()) is not None
    assert run_tests.live_holder(lock, now=time.time() + 120) is None


def test_every_worktree_shares_the_main_checkouts_lock(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    init_repo(main)
    git(main, "worktree", "add", "-q", "-b", "side", str(tmp_path / "side"))
    expected = (main / "test-results" / ".run_tests.lock").resolve()
    assert run_tests.lock_path(main).resolve() == expected
    assert run_tests.lock_path(tmp_path / "side").resolve() == expected


def test_parse_meminfo_reads_mem_available():
    text = "MemTotal:       16303428 kB\nMemFree:          812344 kB\nMemAvailable:    2621440 kB\n"
    assert run_tests.parse_meminfo(text) == 2621440 * 1024
    assert run_tests.parse_meminfo("MemTotal: 1 kB\n") is None


@pytest.mark.skipif(sys.platform not in ("win32", "linux"), reason="measured on Windows and Linux only")
def test_free_memory_is_measured_here():
    free = run_tests.free_memory_bytes()
    assert isinstance(free, int) and free > 0


def test_ram_preflight_refuses_below_two_gigabytes():
    assert run_tests.MIN_FREE_BYTES == 2 * 1024**3
    assert "refusing: 1.50 GB" in run_tests.ram_refusal(int(1.5 * 1024**3), force=False)
    assert run_tests.ram_refusal(int(1.5 * 1024**3), force=True) is None
    assert run_tests.ram_refusal(3 * 1024**3, force=False) is None
    assert run_tests.ram_refusal(None, force=False) is None


def test_summarize_log_keeps_the_failure_list_and_the_count():
    log = "....F\n=== short test summary info ===\nFAILED tests/test_a.py::test_x - boom\n1 failed, 4 passed in 2.00s\n"
    assert run_tests.summarize_log(log).splitlines() == [
        "=== short test summary info ===", "FAILED tests/test_a.py::test_x - boom", "1 failed, 4 passed in 2.00s"]
    assert run_tests.summarize_log(".....\n5 passed in 1.00s\n") == "5 passed in 1.00s"


def test_detach_relaunches_the_same_run_with_its_output_dir(tmp_path):
    argv = run_tests.detach_argv(["merge", "-n", "2", "--detach"], ["-x"], tmp_path)
    assert argv[0] == sys.executable
    assert argv[2:] == ["merge", "-n", "2", "--out-dir", str(tmp_path), "--", "-x"]


def test_changed_with_nothing_selected_exits_zero_without_running(monkeypatch, capsys):
    monkeypatch.setattr(run_tests, "changed_files", lambda base: ["docs/notes.md"])
    monkeypatch.setattr(run_tests, "collect_test_sources", lambda: dict(SOURCES))
    monkeypatch.setattr(run_tests, "_run", lambda *args: pytest.fail("pytest must not start"))
    assert run_tests.main(["changed"]) == 0
    assert "no tests selected" in capsys.readouterr().out


def test_dry_run_prints_the_command_and_takes_no_lock(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(run_tests, "lock_path", lambda: tmp_path / ".run_tests.lock")
    assert run_tests.main(["merge", "--dry-run", "-n", "2", "--", "-x"]) == 0
    out = capsys.readouterr().out
    assert "-m pytest tests -n 2 --durations=25" in out and out.rstrip().endswith("-x")
    assert not (tmp_path / ".run_tests.lock").exists()
```

`_pytest.mark.expression.Expression` is private pytest API; the venv pins pytest 9.1.1, where `Expression.compile(str).evaluate(matcher)` calls `matcher(name, **kwargs)`.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_run_tests.py -q -p no:cacheprovider`
Expected: collection error, `ImportError: cannot import name 'run_tests' from 'scripts'`.

- [ ] **Step 3: Implement the runner**

Create `scripts/run_tests.py`:

```python
# User intent: one command per testing moment (changed / merge / release) so agents stop re-running
# the 30-minute suite every review round, and suite runs stop dying to RAM pressure or the shell reaper.
"""Run the Taskmaster suite at the right tier (docs/specs/2026-09-29-test-suite-speed-design.md, 4.1).

    python scripts/run_tests.py {changed,merge,release} [-n N] [--base REF] [--detach]
        [--dry-run] [--force-ram] [-- extra pytest args]

changed  test files the diff since the merge-base with --base touches (implementation, review rounds)
merge    everything except `release`, the same set as bare `pytest tests` (once per merge)
release  everything including `release`/`scale`, with TASKMASTER_TEMPLATE_VERIFY=1 (once per release)

Every run takes the single-flight lock test-results/.run_tests.lock in the main checkout (one
suite at a time across all worktrees), refuses below 2 GB of free RAM, writes junit.xml and
pytest.log to test-results/<command>-<stamp>/, and prints the slowest tests. --detach relaunches
the run windowless in its own process group, out of a shell's reach.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import time
import warnings
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts import analyze_test_durations  # noqa: E402
from tests._temp_cleanup import pid_alive  # noqa: E402

COMMANDS = ("changed", "merge", "release")
DEFAULT_BASE = "feat/database-native-foundation"
DEFAULT_WORKERS = 3
MIN_FREE_BYTES = 2 * 1024**3
RESULTS_DIR = REPO / "test-results"
LOCK_NAME = ".run_tests.lock"
# Task 8's conftest gate keeps `release` tests, and `scale` tests with them, once the -m expression
# names `release`; this expression does and is true for every marker combination, so nothing drops out.
RELEASE_MARKEXPR = "release or not release"
SLOW_WARN_SECONDS = 10.0
# Release evidence outside pytest (see tests/README.md); this runner reminds, it does not run them.
RELEASE_SCRIPTS = ("scripts/native_n16_acceptance.py", "scripts/native_n15_rehearsal.py")
EVERYTHING = ("pyproject.toml",)
EXIT_LOCKED, EXIT_LOW_RAM, EXIT_GIT = 10, 11, 12
_UNREADABLE_LOCK_GRACE = 60.0
_DETACH_FLAGS = ({"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
                 if os.name == "nt" else {"start_new_session": True})


# ── changed: which test files a change set reaches ─────────────────────────


def module_names(path: str) -> set[str]:
    """Dotted names a test can import `path` by; tests/ is on sys.path too (tests/conftest.py:91-93)."""
    pure = PurePosixPath(path)
    if pure.suffix != ".py":
        return set()
    parts = list(pure.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    if not parts:
        return set()
    names = {".".join(parts)}
    if parts[0] == "tests" and len(parts) > 1:
        names.add(".".join(parts[1:]))
    return names


def direct_imports(source: str, path: str) -> set[str] | None:
    """Every module `source` imports, at any depth in the file; None when it does not parse."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    package = list(PurePosixPath(path).parent.parts)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            anchor = package[: len(package) - node.level + 1] if node.level else []
            base = ".".join([*anchor, *([node.module] if node.module else [])])
            if base:
                names.add(base)
                names.update(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
    return names


def mention_needles(path: str) -> set[str]:
    """Strings a test's source would contain to use `path` without importing it."""
    pure = PurePosixPath(path)
    if pure.suffix == ".py" and pure.parts[0] == "taskmaster":
        return {path, *module_names(path)}  # a bare stem ("store", "paths") would match every file
    needles = {path, pure.name}
    if pure.suffix == ".py":
        needles.add(pure.stem)  # hooks and scripts are loaded by path, often as f"{name}.py"
    return needles


def select_changed_tests(changed_paths: list[str], test_sources: dict[str, str]) -> list[str] | None:
    """Test files to run for a change set, or None for everything (spec §4.1 rules 1-4).

    1. Changed test files. 2. Test files that directly import a changed Python module.
    3. Test files whose source names a changed file (see `mention_needles`).
    4. A changed conftest.py or pyproject.toml selects everything.
    Direct imports only: transitive ones reach nearly everything through backlog_server, and
    the one `merge` run before every merge catches what this misses. A test file that does not
    parse is selected for any Python change.
    """
    changed = sorted({path.strip().replace("\\", "/") for path in changed_paths if path.strip()})
    if any(PurePosixPath(path).name == "conftest.py" or path in EVERYTHING for path in changed):
        return None
    selected = {path for path in changed if path in test_sources}
    imports = None
    for path in changed:
        names = module_names(path)
        if names:
            if imports is None:
                imports = {test: direct_imports(source, test) for test, source in test_sources.items()}
            selected.update(test for test, imported in imports.items() if imported is None or imported & names)
        needles = mention_needles(path)
        selected.update(test for test, source in test_sources.items() if any(n in source for n in needles))
    return sorted(selected)


def collect_test_sources(root: Path = REPO) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): path.read_text(encoding="utf-8", errors="replace")
            for path in sorted((root / "tests").rglob("test_*.py")) if "__pycache__" not in path.parts}


def _git(cwd: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    return subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=cwd, env=env, check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout


def changed_files(base: str, cwd: Path = REPO) -> list[str]:
    """Committed, uncommitted and untracked changes since the merge-base of HEAD and `base`."""
    merge_base = _git(cwd, "merge-base", "HEAD", base).strip()
    tracked = _git(cwd, "diff", "--name-only", "--no-renames", merge_base).splitlines()
    untracked = _git(cwd, "ls-files", "--others", "--exclude-standard").splitlines()
    return sorted({line.strip() for line in [*tracked, *untracked] if line.strip()})


# ── the pytest command ─────────────────────────────────────────────────────


def build_pytest_command(command: str, *, workers: int, targets: list[str] | None, out_dir: Path,
                         extra: list[str]) -> list[str]:
    argv = [sys.executable, "-m", "pytest", *(targets or ["tests"]), "-n", str(workers),
            "--durations=25", f"--junitxml={out_dir / 'junit.xml'}"]
    if command == "release":
        argv += ["-m", RELEASE_MARKEXPR]
    return [*argv, *extra]


def pytest_env(command: str, base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    if command == "release":
        env["TASKMASTER_TEMPLATE_VERIFY"] = "1"  # re-prove every templated fixture against a fresh build
    return env


def final_exit_code(command: str, pytest_code: int) -> int:
    """pytest's code, except that `changed` reaching only deselected tests (5) is a pass."""
    return 0 if command == "changed" and pytest_code == 5 else pytest_code


def summarize_log(text: str, max_lines: int = 80) -> str:
    """pytest's short failure summary through the final count line, else the final line."""
    lines = text.rstrip().splitlines()
    marks = [index for index, line in enumerate(lines) if "short test summary info" in line]
    tail = lines[marks[-1]:] if marks else lines[-1:]
    if len(tail) > max_lines:
        tail = [*tail[: max_lines - 1], f"... {len(tail) - max_lines} more lines in pytest.log", tail[-1]]
    return "\n".join(tail)


# ── guards: single flight and free RAM ─────────────────────────────────────


class LockHeld(RuntimeError):
    """Another suite run holds the single-flight lock."""


def live_holder(path: Path, now: float | None = None) -> dict | None:
    """The lock's live holder, or None when the lock is absent or stale."""
    try:
        text = path.read_text(encoding="utf-8")
        age = (time.time() if now is None else now) - path.stat().st_mtime
    except FileNotFoundError:
        return None
    try:
        holder = json.loads(text)
        pid = int(holder["pid"])
    except (ValueError, KeyError, TypeError):
        # A runner writes its record right after creating the file; give it time before calling it stale.
        return {"pid": None, "command": "(being written)", "started": "?"} if age < _UNREADABLE_LOCK_GRACE else None
    return holder if pid_alive(pid) else None


def acquire_lock(path: Path, command: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder = live_holder(path)
            if holder is not None:
                raise LockHeld(f"pid {holder['pid']} since {holder['started']}: {holder['command']}") from None
            try:
                path.unlink()  # its runner died without releasing it
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "command": command, "started": time.strftime("%Y-%m-%d %H:%M:%S")},
                      handle)
        return
    raise LockHeld(f"{path} keeps reappearing; another runner is starting")


def release_lock(path: Path) -> None:
    """Remove the lock only if this process holds it."""
    try:
        holder = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if holder.get("pid") == os.getpid():
        path.unlink(missing_ok=True)


def lock_path(repo: Path = REPO) -> Path:
    """`test-results/.run_tests.lock` of the main checkout, so every worktree of this clone shares it."""
    try:
        common = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    except (OSError, subprocess.CalledProcessError):
        return repo / "test-results" / LOCK_NAME
    return Path(common).parent / "test-results" / LOCK_NAME


def parse_meminfo(text: str) -> int | None:
    """MemAvailable from Linux /proc/meminfo, in bytes."""
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    return None


def free_memory_bytes() -> int | None:
    """Available physical memory in bytes, or None where this platform cannot say."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                        *((name, ctypes.c_ulonglong) for name in (
                            "ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile",
                            "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual"))]

        status = MemoryStatus(dwLength=ctypes.sizeof(MemoryStatus))
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GlobalMemoryStatusEx.argtypes = (ctypes.POINTER(MemoryStatus),)
        kernel.GlobalMemoryStatusEx.restype = wintypes.BOOL
        return int(status.ullAvailPhys) if kernel.GlobalMemoryStatusEx(ctypes.byref(status)) else None
    try:
        return parse_meminfo(Path("/proc/meminfo").read_text(encoding="ascii"))
    except OSError:
        return None


def ram_refusal(free: int | None, force: bool) -> str | None:
    """Why the run must not start, or None. Measured: ~100 MB per worker, 0.65 GB dip at -n 3."""
    if force or free is None or free >= MIN_FREE_BYTES:
        return None
    return (f"refusing: {free / 1024**3:.2f} GB of RAM free, a suite run needs {MIN_FREE_BYTES / 1024**3:.0f} GB "
            "(stop other suites or acceptance runs, or pass --force-ram)")


# ── entry point ────────────────────────────────────────────────────────────


def detach_argv(argv: list[str], extra: list[str], out_dir: Path) -> list[str]:
    own = [arg for arg in argv if arg != "--detach"]
    return [sys.executable, str(Path(__file__).resolve()), *own, "--out-dir", str(out_dir), "--", *extra]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("-n", dest="workers", type=int, default=DEFAULT_WORKERS, help="xdist workers (default 3)")
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"merge-base ref for `changed` (default {DEFAULT_BASE})")
    parser.add_argument("--detach", action="store_true", help="run windowless in its own process group and return")
    parser.add_argument("--dry-run", action="store_true", help="print the selection and the pytest command only")
    parser.add_argument("--force-ram", action="store_true", help="start even below 2 GB of free RAM")
    parser.add_argument("--out-dir", type=Path, help=argparse.SUPPRESS)  # set by --detach for its child
    return parser


def _detach(argv: list[str], extra: list[str], out_dir: Path) -> int:
    lock = lock_path()
    holder = live_holder(lock)
    if holder is not None:
        print(f"refusing: another suite run holds {lock}: pid {holder['pid']} since {holder['started']}: "
              f"{holder['command']}", file=sys.stderr)
        return EXIT_LOCKED
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "runner.log").open("xb") as log:
        child = subprocess.Popen(detach_argv(argv, extra, out_dir), cwd=REPO, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT, **_DETACH_FLAGS)
    print(f"Started PID {child.pid}; results in {out_dir} (runner.log, then pytest.log)")
    return 0


def _run(command_name: str, command: list[str], out_dir: Path, force_ram: bool) -> int:
    description = subprocess.list2cmdline(command)
    lock = lock_path()
    try:
        acquire_lock(lock, description)
    except LockHeld as held:
        print(f"refusing: another suite run holds {lock}: {held}", file=sys.stderr)
        return EXIT_LOCKED
    log_path = out_dir / "pytest.log"
    try:
        free = free_memory_bytes()
        refusal = ram_refusal(free, force_ram)
        if refusal:
            print(refusal, file=sys.stderr)
            return EXIT_LOW_RAM
        if free is None:
            print("warning: free RAM cannot be measured on this platform; not checked", file=sys.stderr)
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"running: {description}\nlog: {log_path}", flush=True)
        with log_path.open("wb") as log:
            code = subprocess.run(command, cwd=REPO, env=pytest_env(command_name), stdin=subprocess.DEVNULL,
                                  stdout=log, stderr=subprocess.STDOUT).returncode
    finally:
        release_lock(lock)
    print(summarize_log(log_path.read_text(encoding="utf-8", errors="replace")))
    junit = out_dir / "junit.xml"
    if junit.exists():
        warn = [] if command_name == "release" else ["--warn-over", f"{SLOW_WARN_SECONDS:g}"]
        analyze_test_durations.main(["--junit", str(junit), "--top", "10", *warn])
    if command_name == "release":
        print("\nrelease evidence this command does not run: " + ", ".join(RELEASE_SCRIPTS))
    return final_exit_code(command_name, code)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, extra = argv[:split], argv[split + 1:]
    args = _parser().parse_args(argv)
    targets = None
    if args.command == "changed":
        try:
            changed = changed_files(args.base)
        except subprocess.CalledProcessError as error:
            print(f"git failed: {subprocess.list2cmdline(error.cmd)}\n{error.stderr}", file=sys.stderr)
            return EXIT_GIT
        targets = select_changed_tests(changed, collect_test_sources())
        print(f"{len(changed)} file(s) changed since the merge-base with {args.base}")
        if targets is None:
            print("selection: everything (conftest.py or pyproject.toml changed)")
        elif not targets:
            print("no tests selected")
            return 0
        else:
            print(f"selection: {len(targets)} test file(s)\n" + "\n".join(f"  {target}" for target in targets))
    out_dir = (args.out_dir or RESULTS_DIR / f"{args.command}-{time.strftime('%Y%m%d-%H%M%S')}").resolve()
    command = build_pytest_command(args.command, workers=args.workers, targets=targets, out_dir=out_dir, extra=extra)
    if args.dry_run:
        print(subprocess.list2cmdline(command))
        return 0
    if args.detach:
        return _detach(argv, extra, out_dir)
    return _run(args.command, command, out_dir, args.force_ram)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")  # a cp1252 console must not crash on pytest's output
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify they pass; exercise the CLI**

Run: `.venv/Scripts/python.exe -m pytest tests/test_run_tests.py -q -p no:cacheprovider`
Expected: `31 passed`.

Run: `.venv/Scripts/python.exe -m pytest tests/test_analyze_test_durations.py tests/test_temp_cleanup.py -q -p no:cacheprovider`
Expected: `14 passed` (the modules the runner imports are untouched).

Run: `.venv/Scripts/python.exe scripts/run_tests.py merge --dry-run`
Expected: one line, `…\python.exe -m pytest tests -n 3 --durations=25 --junitxml=…\test-results\merge-<stamp>\junit.xml`; no lock file is created.

Run: `.venv/Scripts/python.exe scripts/run_tests.py release --dry-run`
Expected: the same plus `-m "release or not release"`.

Run: `.venv/Scripts/python.exe scripts/run_tests.py changed --base HEAD --dry-run`
Expected (clean tree): `0 file(s) changed since the merge-base with HEAD` then `no tests selected`; exit 0.

The next three cycles harden the runner against the review's three failure modes. Each one is a quiet way to end up with two suites at once or a false green. Line numbers refer to `scripts/run_tests.py` as created in Step 3.

- [ ] **Step 5: Write the failing test: an unresolvable `--base` fails loudly**

A bad ref, or `git merge-base` failing (no common history), must never become an empty change set, which would print "no tests selected" and exit 0. Append to `tests/test_run_tests.py`:

```python
@pytest.mark.parametrize("unrelated", [False, True], ids=["unknown-ref", "no-common-history"])
def test_an_unresolvable_base_fails_loudly_instead_of_selecting_nothing(tmp_path, monkeypatch, capsys, unrelated):
    init_repo(tmp_path)  # branch `main`, one commit
    base = "no-such-ref"
    if unrelated:
        git(tmp_path, "switch", "-q", "--orphan", "elsewhere")
        (tmp_path / "other.txt").write_text("1", encoding="utf-8")
        git(tmp_path, "add", "other.txt")
        git(tmp_path, "commit", "-q", "-m", "unrelated history")
        git(tmp_path, "switch", "-q", "main")
        base = "elsewhere"
    real_changed_files = run_tests.changed_files
    monkeypatch.setattr(run_tests, "changed_files", lambda ref: real_changed_files(ref, cwd=tmp_path))
    monkeypatch.setattr(run_tests, "_run", lambda *args: pytest.fail("pytest must not start"))
    assert run_tests.main(["changed", "--base", base]) == run_tests.EXIT_GIT
    captured = capsys.readouterr()
    assert "no tests selected" not in captured.out
    prefix = f"refusing: cannot tell what changed against --base {base}: "
    assert prefix in captured.err
    detail = captured.err.split(prefix, 1)[1]
    assert ("no common history" if unrelated else "no-such-ref") in detail
```

- [ ] **Step 6: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_run_tests.py -q -p no:cacheprovider -k unresolvable`
Expected: `2 failed`. Both runs exit 12, but stderr reads `git failed: git -c core.quotepath=off merge-base HEAD …`, not the `refusing: cannot tell what changed against --base …:` message, and the no-common-history case carries no explanation at all (git prints nothing).

- [ ] **Step 7: Implement the refusal**

Before `def changed_files` (`scripts/run_tests.py:144`), add:

```python
def git_failure(error: Exception) -> str:
    """What went wrong, in git's words when it gave any."""
    if isinstance(error, subprocess.CalledProcessError):
        return (error.stderr or "").strip() or (
            f"`{subprocess.list2cmdline(error.cmd)}` exited {error.returncode} with no output "
            "(no common history with that ref?)")
    return str(error)
```

`scripts/run_tests.py:366-370` (in `main`) before:

```python
        try:
            changed = changed_files(args.base)
        except subprocess.CalledProcessError as error:
            print(f"git failed: {subprocess.list2cmdline(error.cmd)}\n{error.stderr}", file=sys.stderr)
            return EXIT_GIT
```

after:

```python
        try:
            changed = changed_files(args.base)
        except (subprocess.CalledProcessError, OSError) as error:
            # Never fall through to "no tests selected": an empty change set would be a false green.
            print(f"refusing: cannot tell what changed against --base {args.base}: {git_failure(error)}",
                  file=sys.stderr)
            return EXIT_GIT
```

`OSError` covers a missing `git` executable. `--detach` computes the selection in the parent first, so a bad base is refused there too, before anything is launched.

- [ ] **Step 8: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_run_tests.py -q -p no:cacheprovider`
Expected: `33 passed`.

- [ ] **Step 9: Write the failing tests: the lock holds while the pytest child lives**

The memory reaper can kill the runner while its pytest child keeps running. If the lock then counted as stale, a second suite would start beside the first and exhaust RAM, which is the failure this runner exists to prevent. The lock records the pytest child's pid too and is stale only when both pids are dead. Append:

```python
def test_the_lock_stays_held_while_the_pytest_child_lives(tmp_path):
    lock = tmp_path / ".run_tests.lock"
    lock.write_text(json.dumps({"pid": _finished_pid(), "child_pid": os.getpid(), "command": "pytest tests",
                                "started": "earlier"}), encoding="utf-8")
    with pytest.raises(run_tests.LockHeld, match=f"pytest pid {os.getpid()}"):
        run_tests.acquire_lock(lock, "pytest tests")
    assert json.loads(lock.read_text(encoding="utf-8"))["child_pid"] == os.getpid()


def test_the_lock_is_stale_only_once_runner_and_child_are_both_dead(tmp_path):
    lock = tmp_path / ".run_tests.lock"
    lock.write_text(json.dumps({"pid": _finished_pid(), "child_pid": _finished_pid(), "command": "old",
                                "started": "earlier"}), encoding="utf-8")
    run_tests.acquire_lock(lock, "pytest tests")
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_the_runner_records_its_pytest_child_before_waiting(monkeypatch, tmp_path):
    lock = tmp_path / ".run_tests.lock"
    monkeypatch.setattr(run_tests, "lock_path", lambda: lock)
    monkeypatch.setattr(run_tests, "free_memory_bytes", lambda: 8 * 1024**3)
    reader = [sys.executable, "-c", f"import pathlib, time; time.sleep(0.3); print(pathlib.Path({str(lock)!r}).read_text())"]
    assert run_tests._run("merge", reader, tmp_path / "out", force_ram=False) == 0
    seen = json.loads((tmp_path / "out" / "pytest.log").read_text(encoding="utf-8").strip())
    assert seen["pid"] == os.getpid() and isinstance(seen["child_pid"], int)
    assert not lock.exists()
```

- [ ] **Step 10: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_run_tests.py -q -p no:cacheprovider -k "lock_stays or stale_only or records_its"`
Expected: `2 failed, 1 passed`. `test_the_lock_stays_held_while_the_pytest_child_lives` fails with `DID NOT RAISE LockHeld`, and `test_the_runner_records_its_pytest_child_before_waiting` fails with `KeyError: 'child_pid'`. The both-dead test already passes; it pins the other half of the rule.

- [ ] **Step 11: Implement the child pid in the lock**

`scripts/run_tests.py:206` (end of `live_holder`) before:

```python
    return holder if pid_alive(pid) else None
```

after:

```python
    child = holder.get("child_pid")
    # The shell reaper can kill this runner while its pytest child runs on; the lock holds until both are gone.
    return holder if pid_alive(pid) or (isinstance(child, int) and pid_alive(child)) else None


def describe_holder(holder: dict) -> str:
    child = f", pytest pid {holder['child_pid']}" if holder.get("child_pid") else ""
    return f"runner pid {holder['pid']}{child}, since {holder['started']}: {holder['command']}"
```

`scripts/run_tests.py:217` (in `acquire_lock`) before:

```python
                raise LockHeld(f"pid {holder['pid']} since {holder['started']}: {holder['command']}") from None
```

after:

```python
                raise LockHeld(describe_holder(holder)) from None
```

Before `def release_lock` (`scripts/run_tests.py:230`), add:

```python
def record_child(path: Path, child_pid: int) -> None:
    """Add the pytest child's pid to this runner's lock, so the lock outlives a killed runner."""
    holder = json.loads(path.read_text(encoding="utf-8"))
    if holder.get("pid") == os.getpid():
        holder["child_pid"] = child_pid
        # Rewritten in place: a reader mid-write sees an unparseable, young lock, which counts as held.
        path.write_text(json.dumps(holder), encoding="utf-8")
```

In `_detach` (`scripts/run_tests.py:312-313`), before:

```python
        print(f"refusing: another suite run holds {lock}: pid {holder['pid']} since {holder['started']}: "
              f"{holder['command']}", file=sys.stderr)
```

after:

```python
        print(f"refusing: another suite run holds {lock}: {describe_holder(holder)}", file=sys.stderr)
```

In `_run` (`scripts/run_tests.py:342-344`), before:

```python
        with log_path.open("wb") as log:
            code = subprocess.run(command, cwd=REPO, env=pytest_env(command_name), stdin=subprocess.DEVNULL,
                                  stdout=log, stderr=subprocess.STDOUT).returncode
```

after:

```python
        with log_path.open("wb") as log:
            child = subprocess.Popen(command, cwd=REPO, env=pytest_env(command_name), stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT)
            record_child(lock, child.pid)
            code = child.wait()
```

On Windows the recorded pid is the venv `python.exe` launcher, which lives exactly as long as the interpreter it starts. Killing only the runner therefore leaves the lock held until pytest ends. I checked this end to end in the scratch copy: after the runner was killed mid-run, a new start was refused, naming the `pytest pid`, and once pytest finished the lock read as stale.

- [ ] **Step 12: Run to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_run_tests.py -q -p no:cacheprovider`
Expected: `36 passed`.

- [ ] **Step 13: Write the failing tests: a detached start failure is reported**

`--detach` must not print "Started PID …" and exit 0 when the relaunched run dies before pytest starts (bad interpreter, lock refused, RAM refused). The parent waits, bounded at 10 s, for the child to create `pytest.log`, which `_run` does only after its lock and RAM checks pass. If the child exits first, the parent returns the child's exit code and shows its output. Append:

```python
def test_a_detached_run_that_dies_before_starting_is_reported(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(run_tests, "lock_path", lambda: tmp_path / ".run_tests.lock")
    monkeypatch.setattr(run_tests, "detach_argv", lambda argv, extra, out_dir: [
        sys.executable, "-c", "import sys; print('refusing: 1.20 GB of RAM free'); sys.exit(11)"])
    assert run_tests._detach(["merge"], [], tmp_path / "out") == 11
    captured = capsys.readouterr()
    assert "Started PID" not in captured.out
    assert "exit 11" in captured.err and "refusing: 1.20 GB of RAM free" in captured.err


def test_a_detached_run_reports_started_once_pytest_is_under_way(monkeypatch, tmp_path, capsys):
    started = tmp_path / "out" / "pytest.log"
    monkeypatch.setattr(run_tests, "lock_path", lambda: tmp_path / ".run_tests.lock")
    monkeypatch.setattr(run_tests, "detach_argv", lambda argv, extra, out_dir: [
        sys.executable, "-c", f"import pathlib, time; pathlib.Path({str(started)!r}).write_text(''); time.sleep(0.5)"])
    assert run_tests._detach(["merge"], [], tmp_path / "out") == 0
    assert "Started PID" in capsys.readouterr().out


def test_waiting_for_a_detached_start_is_bounded(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1)"])
    try:
        assert run_tests.wait_for_start(child, tmp_path / "never", timeout=0.2) is None
        assert child.poll() is None
    finally:
        child.wait()
```

- [ ] **Step 14: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_run_tests.py -q -p no:cacheprovider -k "detached or bounded"`
Expected: `2 failed, 1 passed`. `test_a_detached_run_that_dies_before_starting_is_reported` fails with `assert 0 == 11`, and `test_waiting_for_a_detached_start_is_bounded` fails with `AttributeError: … no attribute 'wait_for_start'`. The started-path test already passes; it pins the success path.

- [ ] **Step 15: Implement the bounded start check**

`scripts/run_tests.py:49` before:

```python
EXIT_LOCKED, EXIT_LOW_RAM, EXIT_GIT = 10, 11, 12
```

after:

```python
EXIT_LOCKED, EXIT_LOW_RAM, EXIT_GIT = 10, 11, 12
DETACH_START_TIMEOUT = 10.0
```

Before `def _parser` (`scripts/run_tests.py:296`), add:

```python
def wait_for_start(child: subprocess.Popen, started: Path, timeout: float = DETACH_START_TIMEOUT) -> int | None:
    """The relaunched run's exit code if it ends before creating `started`; None once it is under way.

    Bounded: a run still busy after `timeout` (a slow `changed` selection, say) counts as started.
    """
    deadline = time.monotonic() + timeout
    while True:
        code = child.poll()
        if code is not None:
            return None if started.exists() else code
        if started.exists() or time.monotonic() >= deadline:
            return None
        time.sleep(0.1)
```

In `_detach` (`scripts/run_tests.py:315-320`), before:

```python
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "runner.log").open("xb") as log:
        child = subprocess.Popen(detach_argv(argv, extra, out_dir), cwd=REPO, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT, **_DETACH_FLAGS)
    print(f"Started PID {child.pid}; results in {out_dir} (runner.log, then pytest.log)")
    return 0
```

after:

```python
    out_dir.mkdir(parents=True, exist_ok=True)
    runner_log = out_dir / "runner.log"
    with runner_log.open("xb") as log:
        child = subprocess.Popen(detach_argv(argv, extra, out_dir), cwd=REPO, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT, **_DETACH_FLAGS)
    # The child writes pytest.log only after its lock and RAM checks pass (`_run`).
    code = wait_for_start(child, out_dir / "pytest.log")
    if code is not None:
        output = runner_log.read_text(encoding="utf-8", errors="replace").strip()
        print(f"the detached run ended before pytest started (exit {code}):\n{output}",
              file=sys.stderr if code else sys.stdout)
        return code
    print(f"Started PID {child.pid}; results in {out_dir} (runner.log, then pytest.log)")
    return 0
```

An exit of 0 before pytest starts is legitimate (for example `changed` with "no tests selected"), so that output goes to stdout and the exit stays 0.

- [ ] **Step 16: Run to verify they pass; the runner's neighbours still pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_run_tests.py -q -p no:cacheprovider`
Expected: `39 passed`.

Run: `.venv/Scripts/python.exe -m pytest tests/test_run_tests.py tests/test_analyze_test_durations.py tests/test_temp_cleanup.py -q -p no:cacheprovider -n 1`
Expected: `53 passed`. The xdist run proves the detach and lock tests hold under a worker too.

- [ ] **Step 17: Point `run_detached_check.py` at the runner**

`scripts/run_detached_check.py:1-6` before:

```python
"""Run a local check in its own process group, with bounded log inspection.

On Windows, use a windowless console and a separate process group. A detached
console-less parent can cause console children to allocate visible windows.
This only launches an explicitly supplied command; it never kills a process.
"""
```

after:

```python
"""Run a local check in its own process group, with bounded log inspection.

On Windows, use a windowless console and a separate process group. A detached
console-less parent can cause console children to allocate visible windows.
This only launches an explicitly supplied command; it never kills a process.

For the test suite prefer `scripts/run_tests.py <changed|merge|release> --detach`,
which adds the single-flight lock, the free-RAM check and a durations summary.
This script stays for other long commands, such as acceptance and rehearsal runs.
"""
```

References to `run_detached_check` (via `git grep -n run_detached_check`): `docs/specs/2026-09-29-test-suite-speed-design.md:122,141` only; no code imports it. The user's memory note `database-native-tracking.md:98` also names it; it is outside the repo and stays accurate.

Run: `.venv/Scripts/python.exe scripts/run_detached_check.py --help`
Expected: the help text ends with the new paragraph; exit 0.

- [ ] **Step 18: Commit**

```bash
git add scripts/run_tests.py tests/test_run_tests.py scripts/run_detached_check.py
git commit -m "feat(tests): run_tests.py with changed, merge and release tiers

One runner for every testing moment: diff-based selection for review
rounds, the default suite once per merge, everything plus template
verification per release. Single-flight lock shared by all worktrees
that also holds while an orphaned pytest child runs, a 2 GB free-RAM
preflight, a loud refusal for an unresolvable --base, junit and log per
run, slowest-test summary, and --detach that reports a failed start.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

- [ ] **Step 19: Live check on the committed runner (one worker)**

```powershell
.\.venv\Scripts\python.exe scripts\run_tests.py changed --base HEAD~1 --dry-run
.\.venv\Scripts\python.exe scripts\run_tests.py changed --base HEAD~1 -n 1
```

Expected: the dry run lists 3 changed files (`scripts/run_detached_check.py`, `scripts/run_tests.py`, `tests/test_run_tests.py`) and selects `tests/test_run_tests.py`; the real run prints `running: …`, `39 passed in …`, then the analyzer's top 10; exit 0; `test-results/changed-<stamp>/` holds `junit.xml` and `pytest.log`; the lock is gone afterwards.

```powershell
.\.venv\Scripts\python.exe scripts\run_tests.py changed --base HEAD~1 -n 1 --detach
.\.venv\Scripts\python.exe scripts\run_tests.py changed --base HEAD~1 -n 1
```

Expected: the first prints `Started PID <PID>; results in …` once pytest is under way; the second, started while the first runs, prints `refusing: another suite run holds …\test-results\.run_tests.lock: runner pid …, pytest pid …` and exits 10. When `Get-Process -Id <PID>` prints nothing, `runner.log` in the reported directory ends with the analyzer output.

Kill only the runner, as the shell reaper would, and check that the lock still holds:

```powershell
.\.venv\Scripts\python.exe scripts\run_tests.py changed --base HEAD~1 -n 1 --detach
$lock = Join-Path (Split-Path -Parent (git rev-parse --path-format=absolute --git-common-dir)) 'test-results\.run_tests.lock'
Stop-Process -Id (Get-Content $lock | ConvertFrom-Json).pid -Force
.\.venv\Scripts\python.exe scripts\run_tests.py changed --base HEAD~1 -n 1 --dry-run
.\.venv\Scripts\python.exe scripts\run_tests.py changed --base HEAD~1 -n 1
```

Expected: the last command, run while the orphaned pytest is still going, refuses with `pytest pid …` and exits 10. Once that pytest ends, the same command runs and takes over the stale lock. Finally, `.\.venv\Scripts\python.exe scripts\run_tests.py changed --base no-such-ref` prints `refusing: cannot tell what changed against --base no-such-ref: fatal: Not a valid object name no-such-ref` and exits 12. No commit: nothing changed.

---

---

### Task 11: Policy docs

**Files:**
- Create: `tests/README.md`
- Create: `CLAUDE.md`
- Modify: `README.md:270-277`

**Interfaces:**
- Consumes: the CLI and exit codes of `scripts/run_tests.py` (Task 10); the `release`/`scale` rule and the moved tests (Tasks 8-9); the `durability` marker (Task 3); `template_key=` and `TASKMASTER_TEMPLATE_VERIFY` (Tasks 4-5); the temp root (Task 2).
- Produces: the written policy spec §4.3 requires. Agents read `CLAUDE.md`, which sends them to `tests/README.md`.

- [ ] **Step 1: Write `tests/README.md`**

Before writing, list what is marked today and reconcile the "What runs only in `release`" table with it (one row per file; the table below is spec §4.2's list, which Tasks 8-9 implement):

```bash
git grep -n -E "pytest\.mark\.(release|scale)" -- tests
```

Create `tests/README.md`:

````markdown
<!-- User intent: tell every agent and human which test command to run when, so the full suite runs
     once per merge instead of once per review round, and suite runs stop dying. -->

# Taskmaster tests

The suite has about 4,600 tests, so run the tier that fits the moment. Every tier goes through one
runner, `scripts/run_tests.py`, started with the project's venv interpreter
(`.venv/Scripts/python.exe` on Windows, `.venv/bin/python` elsewhere).

## Which command

| When | Command | What runs | Target |
|---|---|---|---|
| While implementing, and in every review round | `python scripts/run_tests.py changed` | The test files your diff reaches (rules below) | < 2 min |
| Once, on the final candidate, before merging into the working branch | `python scripts/run_tests.py merge` | Everything except `release`: the same set as bare `pytest tests` | ≤ 10 min at `-n 3` |
| Once per release | `python scripts/run_tests.py release` | Everything, including `release` and `scale`, with `TASKMASTER_TEMPLATE_VERIFY=1` | Unbounded |

**One full `merge` run per merge. Review rounds use `changed`.** The `merge` run catches whatever
`changed` misses, so it is not optional. It is also the run that milestone handoffs and ledger rows
cite: the one at the merge commit, by its `test-results/merge-<stamp>/` directory.

`release` covers pytest only. The release evidence outside pytest is `scripts/native_n16_acceptance.py`
(see [the N16 report](../docs/reports/2026-09-29-native-n16.md)) and `scripts/native_n15_rehearsal.py`
(see [the N15 report](../docs/reports/2026-09-24-native-n15.md)); run them after it.

## Runner options

| Option | Meaning |
|---|---|
| `-n N` | xdist workers (default 3). |
| `--base REF` | `changed` compares against the merge-base with `REF` (default `feat/database-native-foundation`). `--base HEAD` looks at uncommitted work only; `--base HEAD~N` at the last N commits too. |
| `--detach` | Relaunch windowless in its own process group, so a reaped shell cannot kill the run. Returns once pytest has started, or within 10 s with the run's own exit code and message if it could not start (lock, RAM, bad `--base`). Progress: `runner.log`, then `pytest.log`, in the printed directory. |
| `--dry-run` | Print the selection and the pytest command; run nothing. |
| `--force-ram` | Start even below 2 GB of free RAM. |
| `-- ARGS` | Passed to pytest as-is, e.g. `-- -x -k cutover`. |

Every run:

- refuses to start while another run holds `test-results/.run_tests.lock` in the main checkout. The
  lock is shared by every worktree, so there is one suite at a time on the machine (exit 10). It
  records the runner and its pytest child, and holds while either is alive: a killed runner does not
  free the machine for a second suite while its pytest runs on. A lock whose runner and child have
  both died is taken over.
- refuses below 2 GB of free physical RAM (exit 11).
- writes `junit.xml` and `pytest.log` to `test-results/<command>-<stamp>/`. It then prints pytest's
  failure summary, the ten slowest files and tests, and a warning for any test over 10 s outside
  `release`.
- exits with pytest's exit code. For `changed`, "no tests collected" (5) counts as success. Exit 12
  means `changed` could not work out the change set (unknown `--base`, no common history, no git);
  it never reports "no tests selected" in that case.

A single file is still fine to run directly:
`.venv/Scripts/python.exe -m pytest tests/test_x.py -q -p no:cacheprovider`. Don't start the whole
suite by hand. The runner's lock and RAM check are what stop parallel sessions from killing each
other's runs.

## How `changed` picks tests

"Changed" means everything since the merge-base with `--base`: commits, uncommitted edits and
untracked files. The rules apply in this order:

1. Changed test files.
2. Test files that directly import a changed Python module, anywhere in the file (`import x`,
   `from x import y`, `from . import y`). `tests/` is importable by bare name, so
   `from native_twins import …` counts as importing `tests/native_twins.py`.
3. Test files whose source names a changed file by repo path or file name. For a Python file outside
   `taskmaster/` (hooks, scripts and test helpers are often loaded by path), the name without `.py`
   also counts. `taskmaster/` modules match by path and dotted module name only.
4. A change to any `conftest.py` or to `pyproject.toml` selects everything, the same set as `merge`.

Direct imports only, on purpose: transitive imports reach almost everything through
`backlog_server`. The `merge` run before the merge catches what `changed` misses. A branch that edits
`tests/conftest.py` gets "everything" from `changed` until it merges. Use `--base HEAD` or
`--base HEAD~N` to scope a round to recent work.

## Markers

| Marker | Meaning |
|---|---|
| `release` | Runs only in `release`. Deselected unless the `-m` expression names it. See the list below. |
| `scale` | Full-size acceptance profiles, part of `release`. Deselected unless `-m` names `scale` or `release`; `-m scale` still runs just them. |
| `durability` | The test observes real fsync or `PRAGMA synchronous`, so durability stays on for it. Every other test runs with durability off, switched off in-process only. |
| `real_service_process` | The test may launch a real `taskmaster.coordinator.service` process; without the marker such a launch fails. |
| `allow_projection_bypass` | Turns off the projection write guard, for tests that drive a legacy migration writer on purpose. |
| `xdist_group(name)` | Tests sharing a name run on one worker, one at a time. |
| `slow` | Real git or bash subprocesses. |

Naming a test by node id (`pytest "tests/x.py::test_y"` or `"tests/x.py::test_y[param]"`) always runs it, whatever its tier; only directory and file arguments are filtered by the `release`/`scale` gate.

## What runs only in `release`

The rule (spec §4.2): a test moves out of `merge` only if it took more than 5 s in the
[baseline](../docs/reports/2026-09-29-test-speed-baseline.md) and repeats a scenario that keeps a
representative in `merge`. Never move one without leaving that representative. The authoritative
list is `git grep -n -E "pytest\.mark\.(release|scale)" -- tests`; keep this table in step with it.

| File | Stays in `merge` | Only in `release` |
|---|---|---|
| `test_native_routing_context_invariant.py` | the first seed | the other seeds |
| `test_viewer_board_oracles.py` | the first seeded sequence | the other seeded sequences |
| `test_native_routing_context_paging.py` | the first seed | the other seeds |
| `test_store_related_incremental.py` | the first seeded edit sequence | the other seeded edit sequences |
| `test_store_concurrency.py` | a reduced-size mixed-process run | `test_mixed_public_tool_operations_across_processes_never_lose_a_write` at full size; `test_mixed_public_tool_operations_at_acceptance_scale` (8 × 200, `scale`) |
| `test_projection_conflict_property.py` | a reduced-size history run | `test_generated_histories_lose_nothing_and_never_touch_protected_files` at full size |
| `test_native_cutover_crash.py` | one representative crash point per mode | the rest of the 66-case crash matrix |

To move another test:

1. Mark it `pytest.mark.release` (a whole test), or its extra cases `pytest.param(..., marks=pytest.mark.release)`.
2. Check that a representative stays in `merge`.
3. Add a row here.
4. Show that `merge` ∪ `release` still collects the same IDs as before.

## Templated fixtures

Some slow fixtures copy a per-worker template instead of rebuilding a seeded project
([design, §3.3](../docs/specs/2026-09-29-test-suite-speed-design.md#33-seeded-project-templates)).
To opt a fixture in:

- Pass an explicit `template_key=`. Seeds that capture arguments (`_depends(value)`, `request.param`)
  put those values in the key. Never key on the seed function's code object alone.
  `TASKMASTER_TWINS_VERIFY` is part of every key.
- Templates live in the session's basetemp and die with it. Nothing is cached on disk across runs.
- Keep these fresh:
  - fixtures with git or worktrees
  - sync-fingerprint and first-sync cache tests
  - crash, WAL and durability tests that rely on the build's own files or handles
  - `real_service_process` tests
  - seeds that use `tmp_path` or wall-clock values
  - tests that patch something before building
- `release` sets `TASKMASTER_TEMPLATE_VERIFY=1`, which also builds each templated fixture fresh and
  diffs the two.

## Temporary directories

Each session gets its own basetemp: `<system temp>/taskmaster-tests/run-<YYYYmmdd-HHMMSS>-<pid>/`.
Under xdist, each worker uses `popen-gwN/` inside it, and a test's files are in
`<first 30 characters of the test name><n>/`. At session start, a detached, windowless pruner
deletes older `run-*` dirs. It keeps the newest two others and any whose session is still running.
No pytest session deletes anything inline. An explicit `--basetemp` is used as given.
````

- [ ] **Step 2: Write the project `CLAUDE.md`**

Create `CLAUDE.md` (the spec's approval authorizes it, §4.3; 12 lines):

````markdown
<!-- User intent: make agents in this repo run the right test tier (changed per review round, one merge run per merge) instead of the full suite every round. -->

# Taskmaster: notes for agents

## Testing

Read [`tests/README.md`](tests/README.md) before running tests. In short:

- Implementation and every review round: `.venv/Scripts/python.exe scripts/run_tests.py changed`.
- Once, on the final candidate before a merge: `.venv/Scripts/python.exe scripts/run_tests.py merge` (≤ 10 min). Handoffs and ledger rows cite that run, not per-round runs.
- `release` runs once per release, never per round.
- Never start the whole suite by hand or run two suites at once; the runner's lock and 2 GB RAM check refuse both. Add `--detach` to long runs.
````

- [ ] **Step 3: Replace the README's test command**

`README.md:270-277` before:

````markdown
## Development

Create the environment and run the Python suite:

```bash
uv sync
uv run --with pytest python -m pytest -q
```
````

after:

````markdown
## Development

Create the environment, then run the tests through the runner. It picks the tier
and refuses to start a second suite or one without enough free RAM (details in
[`tests/README.md`](tests/README.md)):

```bash
uv sync
uv run --with pytest --with pytest-xdist python scripts/run_tests.py changed   # while working, every review round
uv run --with pytest --with pytest-xdist python scripts/run_tests.py merge     # once before merging (<= 10 min)
uv run --with pytest --with pytest-xdist python scripts/run_tests.py release   # once per release
```
````

`uv run` syncs inexactly, so it leaves the venv's extra packages alone, and `--with` supplies pytest and xdist to the interpreter the runner re-uses (`sys.executable`).

- [ ] **Step 4: Verify the docs against the code**

```powershell
.\.venv\Scripts\python.exe scripts\run_tests.py --help
.\.venv\Scripts\python.exe scripts\run_tests.py changed --base HEAD --dry-run
git grep -n -E "pytest\.mark\.(release|scale)" -- tests
```

Expected: every option in the tests/README.md options table appears in `--help`. With the three doc files uncommitted, the dry run reports 3 changed files. `README.md` and `CLAUDE.md` names appear in a few tests' sources, so it may select those; that is rule 3 working, not an error. Every file the grep lists has a row in "What runs only in `release`", and every row names a file the grep lists. Check that the relative links resolve: `tests/README.md` → `../docs/specs/2026-09-29-test-suite-speed-design.md` and `../docs/reports/…`; `CLAUDE.md` → `tests/README.md`.

- [ ] **Step 5: Commit**

```bash
git add tests/README.md CLAUDE.md README.md
git commit -m "docs(tests): testing tiers policy in tests/README.md, CLAUDE.md and README

changed for implementation and review rounds, one merge run per merge,
release once per release; the release list and moving rule, templating
opt-in rules, markers and where temp files live.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 12: Final acceptance

**Files:**
- Create: `docs/reports/2026-09-29-test-speed-acceptance.md`
- Test: none (measurement only)

**Interfaces:**
- Consumes:
  - **Task 1:** `docs/reports/2026-09-29-test-speed-baseline.md`, containing the quiet-baseline wall, worker-seconds, pass/skip counts, and the path of its collected-ID list.
  - **Task 7:** its report section (the §3.4 decision).
  - **Task 8:** `-m "release or not release"` means everything.
  - **Task 9:** the ID-set verification pattern.
  - **Task 10:** `scripts/run_tests.py {changed,merge,release}`, `--detach`, and `--base REF` (spec §4.1). Output goes to `test-results/<command>-<ts>/junit.xml` and `pytest.log`. It prints the top 10 via `scripts/analyze_test_durations.py --junit PATH [--durations-log PATH] [--top N]` and refuses while another run holds the single-flight lock.
  - **Task 11:** `tests/README.md` and `CLAUDE.md` (Testing section).
- Produces: `docs/reports/2026-09-29-test-speed-acceptance.md`, a G1–G5 pass/fail report against spec §2.

Measurement rules (fixed before running):
- **G1:** pass iff the `merge` run's pytest session time is ≤ 600 s at `-n 3`.
- **G2:** "typical" means the median of the three `changed` runs below is < 120 s. Each run is also reported.
- The `changed` runs use **`--base HEAD`**. On `feat/test-speed` the default base, the merge-base with `feat/database-native-foundation`, includes this branch's own `tests/conftest.py` edits. Spec §4.1 rule 4 would then select everything.
- The native-routing probe file is `taskmaster/native_routing/projection.py`, which has 7 direct test importers. `context.py`, `claims.py` and similar have none, so rule 2 would select nothing and the timing would say nothing.

- [ ] **Step 1: Pre-flight (quiet machine), the same as Task 1**

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'pytest|acceptance|rehearsal' } | Select-Object ProcessId, CommandLine
"{0:N2} GB free" -f ((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB)
```
Expected: no rows and at least 4 GB free, or wait. Then:
```bash
git status --short          # expected: clean
git rev-parse --short HEAD  # record in the report
```

- [ ] **Step 2: Run `merge` detached, and check the single-flight lock while it runs**

```bash
.venv/Scripts/python.exe scripts/run_tests.py merge --detach
RUN=$(ls -td test-results/merge-*/ | head -1); echo "$RUN"
.venv/Scripts/python.exe scripts/run_tests.py changed --base HEAD; echo "exit=$?"
```
Expected: the second runner refuses at once with a non-zero exit and names the running suite or its lock. That is G5, the single-flight evidence.

Wait for completion without a foreground sleep. Use the Bash tool with `run_in_background: true`, or Monitor, on:
```bash
until grep -qE "(passed|failed|error).* in [0-9.]+s" "$RUN/pytest.log"; do sleep 20; done; tail -3 "$RUN/pytest.log"
```

- [ ] **Step 3: One-test session right after the full run (§3.1 acceptance, < 10 s)**

Run this immediately after Step 2 finishes:
```bash
time .venv/Scripts/python.exe -m pytest tests/test_viewer_board_oracles.py::test_golden_board_b1 -q -p no:cacheprovider
```
Expected: `1 passed`, and `real` < 10 s.

- [ ] **Step 4: Read the `merge` numbers**

```bash
tail -1 "$RUN/pytest.log"   # wall (pytest session seconds), passed/skipped/failed
.venv/Scripts/python.exe -c "import sys,xml.etree.ElementTree as E; c=list(E.parse(sys.argv[1]).getroot().iter('testcase')); print(len(c), 'tests', round(sum(float(x.get('time', 0)) for x in c)), 'worker-s')" "$RUN/junit.xml"
.venv/Scripts/python.exe scripts/analyze_test_durations.py --junit "$RUN/junit.xml" --top 10
.venv/Scripts/python.exe -m pytest tests --collect-only -q -p no:cacheprovider | tail -1   # G3: merge == the bare pytest set
grep -A40 "slow tests outside release" "$RUN/pytest.log" || echo "no slow-test warning"
```
Record the wall, worker-seconds, counts, top 10 and the slow-test warning lines. The junit test count must equal the bare collect count (G3). 0 failed is required. Any failure: stop, file it (`taskmaster:bug`), and do not fix it in this task.

- [ ] **Step 5: G2, three representative changes**

For each file, make the edit, run `changed`, record the wall and the selection, then restore:
```bash
# 1) native routing module
printf '\n# speed-acceptance probe\n' >> taskmaster/native_routing/projection.py
time .venv/Scripts/python.exe scripts/run_tests.py changed --base HEAD
C=$(ls -td test-results/changed-*/ | head -1); tail -1 "$C/pytest.log"
.venv/Scripts/python.exe -c "import sys,xml.etree.ElementTree as E; c=list(E.parse(sys.argv[1]).getroot().iter('testcase')); print(len(c), 'tests in', len({x.get('classname').split('.')[1] for x in c}), 'files')" "$C/junit.xml"
git restore taskmaster/native_routing/projection.py

# 2) the store
printf '\n# speed-acceptance probe\n' >> taskmaster/store.py
time .venv/Scripts/python.exe scripts/run_tests.py changed --base HEAD
C=$(ls -td test-results/changed-*/ | head -1); tail -1 "$C/pytest.log"
.venv/Scripts/python.exe -c "import sys,xml.etree.ElementTree as E; c=list(E.parse(sys.argv[1]).getroot().iter('testcase')); print(len(c), 'tests in', len({x.get('classname').split('.')[1] for x in c}), 'files')" "$C/junit.xml"
git restore taskmaster/store.py

# 3) a skill markdown file
printf '\n' >> skills/pick-task/SKILL.md
time .venv/Scripts/python.exe scripts/run_tests.py changed --base HEAD
C=$(ls -td test-results/changed-*/ | head -1); tail -1 "$C/pytest.log"
.venv/Scripts/python.exe -c "import sys,xml.etree.ElementTree as E; c=list(E.parse(sys.argv[1]).getroot().iter('testcase')); print(len(c), 'tests in', len({x.get('classname').split('.')[1] for x in c}), 'files')" "$C/junit.xml"
git restore skills/pick-task/SKILL.md
git status --short   # expected: clean
```
Rough expectations at HEAD (direct importers):
- `projection.py` → about 7 files
- `store.py` → about 95 files; this is the likeliest to exceed 2 min, and the report must say so rather than excuse it
- `SKILL.md` → the about 21 files that mention `SKILL.md`

A failure caused only by the probe edit (for example a skill size budget) is recorded as such, and its wall time still counts.

- [ ] **Step 6: G4, the ID sets at final HEAD**

```bash
mkdir -p test-results/acceptance
.venv/Scripts/python.exe -m pytest tests --collect-only -q -p no:cacheprovider -m "release or not release" | grep "::" | LC_ALL=C sort > test-results/acceptance/all.txt
.venv/Scripts/python.exe -m pytest tests --collect-only -q -p no:cacheprovider | grep "::" | LC_ALL=C sort > test-results/acceptance/merge.txt
.venv/Scripts/python.exe -m pytest tests --collect-only -q -p no:cacheprovider -m release | grep "::" | LC_ALL=C sort > test-results/acceptance/release.txt
LC_ALL=C comm -12 test-results/acceptance/merge.txt test-results/acceptance/release.txt | wc -l      # expected 0
LC_ALL=C sort test-results/acceptance/merge.txt test-results/acceptance/release.txt | diff - test-results/acceptance/all.txt && echo "merge + release == all"
BASE_IDS=<path of Task 1's collected-ID list, from the baseline report>
LC_ALL=C sort "$BASE_IDS" > test-results/acceptance/base.txt
echo "lost:";  LC_ALL=C comm -23 test-results/acceptance/base.txt test-results/acceptance/all.txt    # expected: empty
echo "added:"; LC_ALL=C comm -13 test-results/acceptance/base.txt test-results/acceptance/all.txt    # list in the report
```
Expected added IDs:
- the new tests of Tasks 2–11
- Task 9's 5 variant IDs
- `test_mixed_public_tool_operations_at_acceptance_scale`, if Task 1 collected with the bare `-m`

If Task 1 did not keep an ID list, generate one from a temporary worktree at `a12d3aa`:
```bash
git worktree add ../test-speed-base a12d3aa
.venv/Scripts/python.exe -m pytest ../test-speed-base/tests --rootdir ../test-speed-base --collect-only -q -p no:cacheprovider -m "scale or not scale" | grep "::" | LC_ALL=C sort > test-results/acceptance/base.txt
git worktree remove ../test-speed-base
```
Never pass `--force`. If `remove` fails, stop and leave the directory.

- [ ] **Step 7: Run `release` once**

```bash
.venv/Scripts/python.exe scripts/run_tests.py release --detach
REL=$(ls -td test-results/release-*/ | head -1); echo "$REL"
```
Wait as in Step 2, in the background, then:
```bash
tail -1 "$REL/pytest.log"
.venv/Scripts/python.exe -c "import sys,xml.etree.ElementTree as E; c=list(E.parse(sys.argv[1]).getroot().iter('testcase')); print(len(c), 'tests', round(sum(float(x.get('time', 0)) for x in c)), 'worker-s')" "$REL/junit.xml"
```
Record:
- wall, counts and worker-seconds
- that `TASKMASTER_TEMPLATE_VERIFY=1` was set (from the runner's output)
- the outcome of each runbook acceptance or rehearsal script

Expected: the junit count equals `wc -l < test-results/acceptance/all.txt`. Failures are filed, not fixed here.

- [ ] **Step 8: G5 evidence beyond the lock**

```bash
grep -rln "preflight\|2 GB\|free_ram\|FreePhysicalMemory" tests/ scripts/run_tests.py
```
Run the test files it lists with `-q -p no:cacheprovider`. Record the file and test names that prove the RAM refusal below 2 GB. Also record that both detached runs (Steps 2 and 7) finished with a summary line, meaning nothing was reaped.

- [ ] **Step 8b: Worker-count data point (only if the Step 4 `merge` wall exceeded 600 s)**

This gives the user a measured option for closing the G1 gap. Do not change the runner's default here.

Pre-flight: the same as Step 1, but with at least **6 GB** free. Start the RAM sampler in PowerShell, run from the worktree. It stops by itself once the shared lock is gone:

```powershell
$lock = Join-Path (git rev-parse --path-format=absolute --git-common-dir | Split-Path) "test-results/.run_tests.lock"
$csv = Join-Path (Resolve-Path .).Path "test-results/ram-n6.csv"
Start-Job -ScriptBlock { param($lock, $csv)
  Start-Sleep 20
  while (Test-Path $lock) {
    $free = [int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1KB)
    "$(Get-Date -Format s),$free" | Add-Content $csv
    Start-Sleep 5
  }
} -ArgumentList $lock, $csv
```

Then:

```bash
.venv/Scripts/python.exe scripts/run_tests.py merge -n 6 --detach
```

When it finishes, record:
- wall time and worker-seconds (via `scripts/analyze_test_durations.py --junit <run>/junit.xml --top 10`);
- the per-test cost inflation against the `-n 3` run (worker-seconds at `-n 6` ÷ worker-seconds at `-n 3`);
- the minimum free MB from `test-results/ram-n6.csv`;
- whether the run completed without being reaped (a summary line is present in `pytest.log`).

- [ ] **Step 9: Write `docs/reports/2026-09-29-test-speed-acceptance.md`**

```markdown
<!-- User intent: show, with measured numbers, whether the test-speed work met its goals (G1-G5), so the
     user can decide to merge it or pick a fallback; every miss is stated, not hidden. -->

# Test suite speed: acceptance

Status: **<all goals met | G… missed>**. Branch `feat/test-speed` at `<sha>`. Spec:
[`2026-09-29-test-suite-speed-design.md`](../specs/2026-09-29-test-suite-speed-design.md). Baseline:
[`2026-09-29-test-speed-baseline.md`](2026-09-29-test-speed-baseline.md). Machine: quiet (no other suite,
<x.x> GB free at start).

## Goals

| Goal | Target | Measured | Result |
|---|---|---|---|
| G1 merge suite | ≤ 600 s wall at `-n 3` | <s> s wall, <ws> worker-s (baseline <s0> s / <ws0>) | pass/fail |
| G2 `changed` | median < 120 s | projection.py <s> s (<n> tests/<f> files); store.py <s> s (<n>/<f>); SKILL.md <s> s (<n>/<f>) | pass/fail |
| G3 one full run per merge | `merge` == bare pytest set; policy in tests/README.md + CLAUDE.md | <n> == <n>; docs present | pass/fail |
| G4 no silent coverage loss | merge ∩ release = ∅; merge ∪ release = all; no baseline ID lost | 0 overlap; union equal; lost: none; added: <n> (listed below) | pass/fail |
| G5 runs don't die | lock refusal; RAM preflight; detached runs complete | refused with exit <n>; <test ids>; both runs finished | pass/fail |
| §3.1 temp dirs | one-test session after a full run < 10 s | <s> s | pass/fail |

## Merge run
Counts vs baseline (passed/skipped): <p>/<s> vs <p0>/<s0>; the difference is <moved to release> and <added tests>.
Top 10 (setup + call): <table from analyze_test_durations.py>. Slow-test warnings: <lines | none>.

## Release run
Wall <s> s, <n> tests, <ws> worker-s, `TASKMASTER_TEMPLATE_VERIFY=1`; runbook scripts: <each with outcome>.

## Added test IDs
<the comm -13 list>

## If G1 missed: options for the user (none applied)
Gap: <ws − 1800> worker-s over the ≤ 10 min budget (<s> s wall vs 600 s). Spec §8 fallbacks:
1. §3.4 shutdown latency: <already adopted in Task 7 | dropped at <p>%; re-measured against today's
   worker-seconds it is <p'>%>.
2. A larger `release` move. The §4.2 rule moved ~650 worker-s (spec §9 assumed 1,300–1,600). More
   needs either reduced merge variants for slow unique scenarios (the pattern Task 9 used for the stress
   and property tests) or relaxing the "representative stays" rule. Largest remaining candidates, from
   this run's top list: <e.g. test_native_routing_viewer board/continuity/task-edit twins, the N13
   heavy-process Git pins, test_native_service_progress debts, test_store_related_incremental quadratic guard>.
3. More workers (Step 8b): `-n 6` took <wall> s at <ws6> worker-s (<x>× the `-n 3` per-test cost),
   with a minimum of <m> MB free. If wall ≤ 600 s and minimum free ≥ 1,500 MB, the change would be to
   raise the runner default to 6 and scale the RAM preflight per worker.
4. Step 3 pruning (deferred in the spec, §5): the legacy half of the twins is the largest remaining
   block. It waits for the legacy-backend decision.
Each needs the user's approval (spec §8); nothing here was applied.
```
All measured values come from Steps 2–8. Do not estimate any of them.

- [ ] **Step 10: Commit**

```bash
git add docs/reports/2026-09-29-test-speed-acceptance.md
git commit -m "docs(tests): test-speed acceptance - G1-G5 measured (<summary: e.g. G1 612 s missed, rest pass>)

Co-Authored-By: Claude <noreply@anthropic.com>"
```
Do not push or merge. The user decides after reading the report.
