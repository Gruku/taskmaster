"""User intent: prove the managed-Git containment boundary on Windows using only the
private job capability: no PID probing or signalling, cleanup by closing our handles.
"""
import os
import sys
import time

import pytest

pytestmark = [pytest.mark.skipif(sys.platform != 'win32', reason='Windows Job Objects'),
              pytest.mark.xdist_group('heavy_processes')]

from taskmaster.coordinator import job as jobs  # noqa: E402
from taskmaster.coordinator.contained import ManagedChild  # noqa: E402


def python(tmp_path, code, *args):
    return {'argv': [sys.executable, '-c', code, *map(str, args)], 'cwd': str(tmp_path), 'env': dict(os.environ)}


def wait_for(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_name_is_private_unpredictable_and_never_reused():
    first, second = jobs.new_name(), jobs.new_name()
    assert first != second and jobs.valid_name(first)
    assert not jobs.valid_name('Global\\taskmaster-git-' + '0' * 32)
    with jobs.Job.create(first):
        with pytest.raises(jobs.JobUnavailable, match='already exists'):
            jobs.Job.create(first)
    assert jobs.Job.open(first) is None


def test_permitted_command_and_its_descendant_run_inside_the_job(tmp_path):
    child = ManagedChild(jobs.new_name())
    try:
        child.start()
        child.assign()
        assert child.job.contains(child.process._handle)
        # The command starts a grandchild that outlives it; the job must still hold it.
        code = ('import subprocess,sys; subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]);'
                'print("ran")')
        child.permit([python(tmp_path, code)])
        done, results = child.results(30)
        assert done and results[0]['returncode'] == 0 and results[0]['stdout'].strip() == 'ran'
        assert child.job.active_processes() >= 2  # helper plus the sleeping grandchild
        assert child.retire(10)
        assert child.job.active_processes() == 0
    finally:
        child.close()
    assert jobs.Job.open(child.job_name) is None


def test_eof_before_assignment_exits_without_launching(tmp_path):
    child = ManagedChild(jobs.new_name())
    try:
        child.start()
        child.process.stdin.close()  # the coordinator died before assignment
        assert child.process.wait(timeout=20) == 0
    finally:
        child.close()
    assert jobs.Job.open(child.job_name) is None


def test_eof_before_permission_holds_the_job_until_retired(tmp_path):
    marker = tmp_path / 'launched'
    child = ManagedChild(jobs.new_name())
    try:
        child.start()
        child.assign()
        child.process.stdin.close()
        child.job.close()  # the coordinator's handle is gone, as after its death
        time.sleep(0.5)
        recovered = jobs.Job.open(child.job_name)
        assert recovered is not None, 'an assigned helper keeps its job recoverable'
        with recovered:
            assert recovered.active_processes() >= 1
            assert recovered.retire(10)
            assert recovered.active_processes() == 0
        child.process.wait(timeout=10)
    finally:
        child.close()
    assert not marker.exists()
    assert jobs.Job.open(child.job_name) is None


def test_completed_helper_holds_job_after_coordinator_eof(tmp_path):
    child = ManagedChild(jobs.new_name())
    try:
        child.start()
        child.assign()
        child.permit([python(tmp_path, 'print(1)')])
        done, _ = child.results(30)
        assert done
        child.process.stdin.close()
        child.job.close()
        recovered = jobs.Job.open(child.job_name)
        assert recovered is not None
        with recovered:
            assert recovered.retire(10) and recovered.active_processes() == 0
        child.process.wait(timeout=10)
    finally:
        child.close()


def test_closing_the_last_handle_kills_members(tmp_path):
    started = tmp_path / 'started'
    child = ManagedChild(jobs.new_name())
    try:
        child.start()
        child.assign()
        code = 'import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(120)'
        child.permit([python(tmp_path, code, started)])
        assert wait_for(started.exists)
        observer = jobs.Job.open(child.job_name)
        with observer:
            child.job.close()
            # The helper still holds its inherited handle: members keep running.
            assert observer.active_processes() >= 2
            assert observer.retire(10)
    finally:
        child.close()
    assert child.process.wait(timeout=10) == jobs.RETIRED_EXIT_CODE
