"""User intent: the hidden launcher that runs managed Git only after the coordinator
has placed it inside its private job and explicitly granted permission.

stdin is an anonymous pipe owned by the coordinator. EOF before the permission
line never launches Git: an unassigned helper exits, an assigned one keeps
waiting (holding its job) so a replacement coordinator can prove and retire it.
Before running anything it verifies its own job membership: the coordinator can
only verify the process it started, and an interposed launcher would not be us.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import threading

MAX_PLAN_BYTES = 16 * 1024 * 1024
OUTPUT_LIMIT = 64 * 1024
READER_GRACE = 2


def _member(job_handle):
    """True/False, or None when membership cannot be determined."""
    from . import job
    try:
        return job.current_process_in(job_handle)
    except OSError:
        return None


def _hold_or_exit(job_handle, *, assigned):
    """The coordinator is gone. Stay inside a recoverable job; never outlive one silently.

    `assigned` is True once membership is known. Before that, unprovable membership
    exits rather than holding forever: an unassigned helper pins nothing recoverable."""
    if job_handle is not None:
        member = _member(job_handle)
        if member or (member is None and assigned):
            threading.Event().wait()  # retired only by TerminateJobObject
    sys.exit(0)


def _drain_and_hold(job_handle, *, assigned):
    try:
        while sys.stdin.buffer.read(65536):
            pass
    except OSError:
        pass
    _hold_or_exit(job_handle, assigned=assigned)


def _bounded(stream, sink):
    kept, total = bytearray(), 0
    while True:
        chunk = stream.read1(65536)
        if not chunk:
            break
        total += len(chunk)
        if len(kept) < OUTPUT_LIMIT:
            kept.extend(chunk[:OUTPUT_LIMIT - len(kept)])
        sink.update(data=bytes(kept), total=total)
    sink.update(data=bytes(kept), total=total)


def run(command):
    """Run one argv without a shell; bounded output; hidden; inside this job."""
    argv, cwd, env = command['argv'], command['cwd'], command['env']
    if not (isinstance(argv, list) and argv and all(isinstance(part, str) for part in argv)):
        raise ValueError('managed command must be a non-empty argv list')
    data = base64.b64decode(command['stdin_b64']) if command.get('stdin_b64') else None
    flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    child = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, **flags)
    out, err = {}, {}
    readers = [threading.Thread(target=_bounded, args=(child.stdout, out), daemon=True),
               threading.Thread(target=_bounded, args=(child.stderr, err), daemon=True)]
    for reader in readers:
        reader.start()
    if data is not None:
        try:
            child.stdin.write(data)
        except OSError:
            pass
        finally:
            try:
                child.stdin.close()
            except OSError:
                pass
    code = child.wait()
    # A descendant that outlives the command (a daemonised hook) may keep the
    # pipes open; report after a short grace instead of waiting on it. The job,
    # not this helper, bounds that descendant's lifetime.
    for reader in readers:
        reader.join(READER_GRACE)
    out.setdefault('data', b''), out.setdefault('total', 0)
    err.setdefault('data', b''), err.setdefault('total', 0)
    return {'returncode': code, 'stdout': out['data'].decode('utf-8', 'replace'), 'stdout_bytes': out['total'],
            'stderr': err['data'].decode('utf-8', 'replace'), 'stderr_bytes': err['total']}


def _emit(value):
    try:
        sys.stdout.buffer.write(json.dumps(value, separators=(',', ':')).encode('utf-8') + b'\n')
        sys.stdout.buffer.flush()
    except OSError:
        pass  # the coordinator may be gone; the job boundary still holds


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-handle', type=int)
    args = parser.parse_args(argv)
    line = sys.stdin.buffer.readline(MAX_PLAN_BYTES)
    if not line.endswith(b'\n'):
        _hold_or_exit(args.job_handle, assigned=False)  # EOF (or oversize) before permission: never launch
    plan = json.loads(line)
    if plan.get('permit') is not True:
        _hold_or_exit(args.job_handle, assigned=False)
    if args.job_handle is not None:
        # The coordinator verified the process it assigned; prove that process is
        # really this one (no launcher in between) before anything can start Git.
        member = _member(args.job_handle)
        if member is not True:
            reason = ('helper is not inside its managed job; nothing was launched' if member is False
                      else 'helper job membership cannot be verified; nothing was launched')
            _emit({'index': 0, 'returncode': None, 'error': reason})
            _emit({'done': True})
            _drain_and_hold(args.job_handle, assigned=member is None)
    for index, command in enumerate(plan['commands']):
        try:
            result = run(command)
        except (OSError, ValueError) as exc:
            result = {'returncode': None, 'error': str(exc)[:4096]}
        _emit(dict(result, index=index))
        if result.get('returncode') != 0:
            break
    _emit({'done': True})
    _drain_and_hold(args.job_handle, assigned=True)


if __name__ == '__main__':
    main()
