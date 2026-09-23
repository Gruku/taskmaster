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
from native_git_helpers import gone  # noqa: E402


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
    assert gone(first)


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
    assert gone(child.job_name)


def test_eof_before_assignment_exits_without_launching(tmp_path):
    child = ManagedChild(jobs.new_name())
    try:
        child.start()
        child.process.stdin.close()  # the coordinator died before assignment
        assert child.process.wait(timeout=20) == 0
    finally:
        child.close()
    assert gone(child.job_name)


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
    assert gone(child.job_name)


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


def test_members_survive_coordinator_handle_close_while_helper_holds_job(tmp_path):
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


BASE = getattr(sys, '_base_executable', sys.executable)
# A venv-style launcher: it starts the real interpreter as its own child and waits.
LAUNCHER = 'import subprocess,sys; sys.exit(subprocess.Popen(sys.argv[1:], close_fds=False).wait())'


def test_helper_is_the_assigned_process_not_a_launcher(tmp_path):
    """H1: the process we assign must be the helper itself (no venv launcher between)."""
    child = ManagedChild(jobs.new_name())
    try:
        child.start()
        child.assign()
        child.permit([{'argv': [BASE, '-I', '-S', '-c', 'import os; print(os.getppid())'], 'cwd': str(tmp_path),
                       'env': dict(os.environ)}])
        done, results = child.results(30)
        assert done and results[0]['returncode'] == 0, results
        assert int(results[0]['stdout'].strip()) == child.process.pid
        assert child.retire(10)
    finally:
        child.close()


def test_helper_outside_its_job_never_runs_a_command(tmp_path):
    """H1: a launcher assigned after it already started the real helper leaves the helper
    outside the job; the helper must verify membership itself and refuse."""
    ran = tmp_path / 'ran'
    child = ManagedChild(jobs.new_name(), launcher=[BASE, '-I', '-S', '-c', LAUNCHER])
    try:
        child.start()
        time.sleep(0.5)  # delayed assignment: the real helper already exists
        child.assign()
        child.permit([python(tmp_path, 'import pathlib,sys; pathlib.Path(sys.argv[1]).touch()', ran)])
        done, results = child.results(30)
        assert done and results and results[0].get('returncode') is None, results
        assert 'not inside its managed job' in results[0]['error']
        assert not ran.exists()
        assert child.retire(10)
    finally:
        child.close()
    assert not ran.exists()


def test_inherited_job_handle_is_query_only():
    """M6: the helper's handle can prove membership but cannot terminate or reassign."""
    job = jobs.Job.create(jobs.new_name())
    try:
        duplicate = job.inheritable()
        try:
            api = jobs._load()
            assert jobs.current_process_in(duplicate) is False
            assert not api.kernel.TerminateJobObject(duplicate, 1)
            assert api.c.get_last_error() == 5  # ERROR_ACCESS_DENIED
        finally:
            jobs.close_handle(duplicate)
    finally:
        job.close()


def test_recovery_access_is_query_and_terminate_only():
    """L2: recovery opens the job with exactly what retire needs."""
    assert jobs._JOB_ACCESS == 0x0004 | 0x0008


def test_unassigned_helper_exits_when_membership_is_unprovable(monkeypatch):
    """L5: before assignment is known, an IsProcessInJob failure must not hold forever."""
    from taskmaster.coordinator import git_helper

    def broken(handle):
        raise OSError(6, 'invalid handle')
    monkeypatch.setattr(jobs, 'current_process_in', broken)
    with pytest.raises(SystemExit):
        git_helper._hold_or_exit(1234, assigned=False)
