# User intent: stop the test suite's RAM from creeping back up — measure each test file's peak
# process-tree RSS (pytest plus every child it spawns) and the children it leaves behind, and fail
# when a file exceeds the budget or leaks, so a regression is caught before it kills a shell.
"""Per-file peak memory and leftover-child check for the Taskmaster test suite.

Each test file runs in its own serial pytest process (no xdist). A sampler polls
that process and all its descendants, including orphans whose parent already
exited, and records the peak summed RSS. When pytest exits, any descendant still
alive after a short linger window is a leftover child.

A file fails when its peak tree RSS exceeds the budget (default 600 MB) or it
leaves children behind. Files marked `scale` are exempt: a module whose
`pytestmark` is `pytest.mark.scale`, or any run whose `-m` expression selects
`scale` profiles.

psutil is required but is not a project dependency. Without it, install it
anywhere outside the project and point this script at it:

    python -m pip install --target C:/somewhere/pylib psutil
    python scripts/measure_test_memory.py --psutil-path C:/somewhere/pylib tests/test_x.py

Examples:
    python scripts/measure_test_memory.py tests/test_batch_structured_commands.py
    python scripts/measure_test_memory.py --all --jsonl mem.jsonl
    python scripts/measure_test_memory.py tests/test_store_concurrency.py -m scale
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
MB = 1024 * 1024
DEFAULT_BUDGET_MB = 600

PSUTIL_HELP = """psutil is not importable, and it is deliberately not a project dependency.
Install it outside the project and pass its directory:

    {python} -m pip install --target <dir> psutil
    {python} {script} --psutil-path <dir> ...

(or put <dir> on PYTHONPATH for this command only)."""


def _import_psutil(extra_path):
    if extra_path:
        sys.path.insert(0, str(extra_path))
    try:
        import psutil  # noqa: PLC0415
    except ImportError:
        sys.stderr.write(PSUTIL_HELP.format(python=sys.executable, script="scripts/measure_test_memory.py") + "\n")
        raise SystemExit(2)
    return psutil


def _selects_scale(markexpr: str | None) -> bool:
    if not markexpr:
        return False
    return re.search(r"(?<!not )\bscale\b", markexpr) is not None


def _file_is_scale(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return re.search(r"^pytestmark\s*=.*\bmark\.scale\b", text, re.MULTILINE) is not None


def _counts(log: str) -> dict:
    last = log.strip().splitlines()[-1] if log.strip() else ""
    counts = {kind: int(value) for value, kind in re.findall(
        r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|deselected)", last)}
    match = re.search(r" in ([\d.]+)s", last)
    return {"counts": counts, "pytest_s": float(match.group(1)) if match else None}


def measure(psutil, test_file: str, *, markexpr=None, timeout=1800, linger=3.0, interval=0.25) -> dict:
    """Run one file under pytest and sample its whole process tree until it and its children end."""
    command = [sys.executable, "-m", "pytest", test_file, "-q", "-p", "no:cacheprovider",
               "-p", "no:xdist", "-p", "no:randomly"]
    if markexpr:
        command += ["-m", markexpr]
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)  # the tests set their own; a psutil path must not leak in
    started = time.time()
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as log:
        process = subprocess.Popen(command, cwd=REPO, env=environment, stdout=log, stderr=subprocess.STDOUT)
        known = {process.pid: psutil.Process(process.pid)}
        seen = {process.pid}
        peak_tree = peak_single = peak_python = 0
        exited_at = None
        timed_out = False
        while True:
            try:
                parents = psutil._ppid_map()  # includes children whose parent already exited
            except (AttributeError, psutil.Error):
                parents = {child.pid: parent.pid for parent in list(known.values())
                           for child in _safe_children(psutil, parent)}
            grew = True
            while grew:
                grew = False
                for pid, ppid in parents.items():
                    if pid in seen or ppid not in seen:
                        continue
                    seen.add(pid)
                    try:
                        child = psutil.Process(pid)
                        if child.create_time() >= started - 1:  # not a recycled PID
                            known[pid] = child
                            grew = True
                    except psutil.Error:
                        pass
            tree = python = 0
            for pid, child in list(known.items()):
                try:
                    if not child.is_running():
                        del known[pid]
                        continue
                    rss = child.memory_info().rss
                    name = child.name().lower()
                except psutil.Error:
                    known.pop(pid, None)
                    continue
                tree += rss
                peak_single = max(peak_single, rss)
                python += name.startswith("python")
            peak_tree, peak_python = max(peak_tree, tree), max(peak_python, python)
            now = time.time()
            if process.poll() is not None:
                exited_at = exited_at or now
                if not known or now - exited_at >= linger:
                    break
            elif now - started > timeout and not timed_out:
                timed_out = True
                for child in list(known.values()):
                    try:
                        child.kill()
                    except psutil.Error:
                        pass
            time.sleep(interval)
        leftovers = []
        for child in known.values():
            try:
                leftovers.append({"pid": child.pid, "name": child.name(),
                                  "rss_mb": child.memory_info().rss // MB,
                                  "cmdline": " ".join(child.cmdline())[:240]})
            except psutil.Error:
                pass
        log.seek(0)
        output = log.read()
    result = {"file": test_file, "rc": process.returncode, "wall_s": round(time.time() - started, 1),
              "peak_tree_mb": peak_tree // MB, "peak_single_mb": peak_single // MB,
              "peak_python_procs": peak_python, "leftover_children": leftovers, "timed_out": timed_out}
    result.update(_counts(output))
    if process.returncode not in (0, 5):  # 5: nothing collected (e.g. everything deselected)
        result["log_tail"] = output.strip().splitlines()[-15:]
    return result


def _safe_children(psutil, process):
    try:
        return process.children()
    except psutil.Error:
        return []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", help="test files, relative to the repository root")
    parser.add_argument("--all", action="store_true", help="every tests/test_*.py file")
    parser.add_argument("-m", dest="markexpr", help="pytest marker expression, passed through")
    parser.add_argument("--budget-mb", type=int, default=DEFAULT_BUDGET_MB,
                        help=f"peak tree RSS budget per file (default {DEFAULT_BUDGET_MB})")
    parser.add_argument("--linger", type=float, default=3.0,
                        help="seconds to wait after pytest exits before a live child counts as leftover")
    parser.add_argument("--timeout", type=int, default=1800, help="per-file timeout in seconds")
    parser.add_argument("--jsonl", type=Path, help="append one JSON result per file here")
    parser.add_argument("--psutil-path", type=Path, help="directory holding a psutil install")
    args = parser.parse_args(argv)
    psutil = _import_psutil(args.psutil_path)

    files = list(args.files)
    if args.all:
        files += sorted(f"tests/{path.name}" for path in (REPO / "tests").glob("test_*.py"))
    if not files:
        parser.error("name test files or pass --all")

    failures = []
    print(f"{'file':<58} {'peak MB':>8} {'py':>3} {'left':>4} {'wall s':>7}  result")
    for test_file in files:
        result = measure(psutil, test_file, markexpr=args.markexpr, timeout=args.timeout, linger=args.linger)
        exempt = _selects_scale(args.markexpr) or _file_is_scale(REPO / test_file)
        problems = []
        if result["peak_tree_mb"] > args.budget_mb:
            problems.append(f"peak {result['peak_tree_mb']} MB > {args.budget_mb} MB")
        if result["leftover_children"]:
            problems.append(f"{len(result['leftover_children'])} leftover child process(es)")
        if result["timed_out"]:
            problems.append("timed out")
        if result["rc"] not in (0, 5):
            problems.append(f"pytest exited {result['rc']}")
        result["budget_exempt"] = exempt
        result["problems"] = problems
        verdict = ("exempt (scale): " if exempt and problems else "") + ("; ".join(problems) or "ok")
        tests = " ".join(f"{count} {kind}" for kind, count in result["counts"].items())
        print(f"{Path(test_file).name:<58} {result['peak_tree_mb']:>8} {result['peak_python_procs']:>3} "
              f"{len(result['leftover_children']):>4} {result['wall_s']:>7}  {verdict} [{tests}]", flush=True)
        for child in result["leftover_children"]:
            print(f"    leftover pid {child['pid']} {child['name']} {child['rss_mb']} MB: {child['cmdline']}")
        if problems and not exempt:
            failures.append(test_file)
        if args.jsonl:
            with args.jsonl.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(result) + "\n")
    if failures:
        print(f"\n{len(failures)} file(s) over budget or leaking: {', '.join(failures)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
