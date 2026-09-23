"""User intent: launch the managed Git helper so its whole process tree lives inside
an owned boundary, and prove that boundary empty before publication resumes.

Launch order is the containment proof: create the private job, start the hidden
helper blocked on an owned pipe, assign it, and only then grant permission.
The helper is the real interpreter (never a venv launcher) started isolated, and it
re-verifies its own membership before running anything.
POSIX has no equivalent tested boundary yet; there it is honestly `contained=False`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

from . import job as jobs

MAX_LINE = 16 * 1024 * 1024


# The helper imports only the standard library and this package. It is started
# isolated (-I: no PYTHON* variables, no user site; -S: no site/.pth code), so
# nothing but interpreter start-up runs before it is assigned to the job.
_BOOTSTRAP = ('import sys; sys.path.insert(0, sys.argv[1]); '
              'from taskmaster.coordinator.git_helper import main; main(sys.argv[2:])')


def interpreter():
    """The real interpreter. A venv's python.exe on Windows is a launcher that starts the
    base interpreter as its own child, so assigning the launcher would leave the helper
    (and Git) outside the job."""
    base = getattr(sys, '_base_executable', None)
    return base if base and Path(base).is_file() else sys.executable


def helper_argv(package_root):
    return [interpreter(), '-I', '-S', '-c', _BOOTSTRAP, str(package_root)]


class ManagedChild:
    def __init__(self, job_name, *, checkpoint=None, launcher=None):
        self.job_name = job_name
        self.launcher = list(launcher or [])  # tests only: simulate an interposed launcher
        self.checkpoint = checkpoint or (lambda stage: None)
        self.contained = jobs.supported()
        self.job = None
        self.process = None
        self.lines = queue.Queue()
        self.reader = None

    def start(self):
        package_root = Path(__file__).resolve().parents[2]
        environment = {key: value for key, value in os.environ.items() if not key.upper().startswith('PYTHON')}
        argv = self.launcher + helper_argv(package_root)
        options = dict(cwd=package_root, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL)
        if self.contained:
            self.job = jobs.Job.create(self.job_name)
            inherited = self.job.inheritable()
            try:
                info = subprocess.STARTUPINFO()
                info.lpAttributeList = {'handle_list': [inherited]}
                self.process = subprocess.Popen(argv + ['--job-handle', str(inherited)], startupinfo=info,
                                                creationflags=subprocess.CREATE_NO_WINDOW, **options)
            finally:
                jobs.close_handle(inherited)
        else:
            self.process = subprocess.Popen(argv, start_new_session=True, **options)
        self.reader = threading.Thread(target=self._read, name='taskmaster-git-helper-reader', daemon=True)
        self.reader.start()
        self.checkpoint('git_helper_started')

    def _read(self):
        stream = self.process.stdout
        try:
            while True:
                line = stream.readline(MAX_LINE)
                if not line:
                    break
                try:
                    self.lines.put(json.loads(line))
                except ValueError:
                    self.lines.put({'error': 'malformed helper output'})
        except (OSError, ValueError):
            pass
        finally:
            # The pipe is released on every path once the helper is gone, including
            # after an unproven retirement that recovery later completes.
            try:
                stream.close()
            except OSError:
                pass
        self.lines.put(None)

    def assign(self):
        if self.contained:
            self.job.assign(self.process._handle)
            if not self.job.contains(self.process._handle):
                raise jobs.JobUnavailable(0, 'helper is not inside its managed job')
        self.checkpoint('git_assigned')

    def permit(self, commands):
        self.checkpoint('git_permit')
        raw = json.dumps({'permit': True, 'commands': commands}, separators=(',', ':')).encode('utf-8') + b'\n'
        if len(raw) > MAX_LINE:
            raise ValueError('managed Git plan is too large')
        self.process.stdin.write(raw)
        self.process.stdin.flush()

    def results(self, timeout):
        """Per-command results until `done`; None if the deadline passes first."""
        import time
        deadline = time.monotonic() + max(0, timeout)
        collected = []
        while True:
            try:
                item = self.lines.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                return None, collected
            if item is None:
                return None, collected  # helper output ended without `done`
            if item.get('done'):
                return True, collected
            collected.append(item)

    def retire(self, timeout) -> bool:
        """True only when the boundary is proven empty (Windows) or the helper exited (POSIX)."""
        if self.contained:
            try:
                quiet = self.job.retire(timeout)
            except OSError:
                return False
        else:
            try:
                self.process.stdin.close()
            except OSError:
                pass
            try:
                self.process.wait(timeout=timeout)
                quiet = True
            except subprocess.TimeoutExpired:
                quiet = False
        if quiet:
            self._reap()
        return quiet

    def _reap(self):
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        if self.reader is not None:
            self.reader.join(5)  # EOF follows the helper's exit; the reader closes stdout

    def close(self):
        """Close our handles. On Windows the job's kill-on-close retires any member left."""
        if self.process is not None:
            for stream in (self.process.stdin,):
                try:
                    stream.close()
                except OSError:
                    pass
        if self.job is not None:
            self.job.close()
