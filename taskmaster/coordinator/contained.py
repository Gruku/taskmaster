"""User intent: launch the managed Git helper so its whole process tree lives inside
an owned boundary, and prove that boundary empty before publication resumes.

Launch order is the containment proof: create the private job, start the hidden
helper blocked on an owned pipe, assign it, and only then grant permission.
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


class ManagedChild:
    def __init__(self, job_name, *, checkpoint=None):
        self.job_name = job_name
        self.checkpoint = checkpoint or (lambda stage: None)
        self.contained = jobs.supported()
        self.job = None
        self.process = None
        self.lines = queue.Queue()
        self.reader = None

    def start(self):
        package_root = Path(__file__).resolve().parents[2]
        environment = dict(os.environ)
        environment['PYTHONPATH'] = str(package_root) + (os.pathsep + environment['PYTHONPATH']
                                                         if environment.get('PYTHONPATH') else '')
        argv = [sys.executable, '-m', 'taskmaster.coordinator.git_helper']
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
        for stream in (self.process.stdin, self.process.stdout):
            try:
                stream.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

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
