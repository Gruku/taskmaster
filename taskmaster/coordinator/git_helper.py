"""User intent: the hidden launcher that runs managed Git only after the coordinator
has placed it inside its private job and explicitly granted permission.

stdin is an anonymous pipe owned by the coordinator. EOF before the permission
line never launches Git: an unassigned helper exits, an assigned one keeps
waiting (holding its job) so a replacement coordinator can prove and retire it.
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


def _hold_or_exit(job_handle):
    """The coordinator is gone. Stay inside a recoverable job; never outlive one silently."""
    if job_handle is not None:
        from . import job
        try:
            member = job.current_process_in(job_handle)
        except OSError:
            member = True  # unprovable: keep the handle so recovery can decide
        if member:
            threading.Event().wait()  # retired only by TerminateJobObject
    sys.exit(0)


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
        _hold_or_exit(args.job_handle)  # EOF (or oversize) before permission: never launch
    plan = json.loads(line)
    if plan.get('permit') is not True:
        _hold_or_exit(args.job_handle)
    for index, command in enumerate(plan['commands']):
        try:
            result = run(command)
        except (OSError, ValueError) as exc:
            result = {'returncode': None, 'error': str(exc)[:4096]}
        _emit(dict(result, index=index))
        if result.get('returncode') != 0:
            break
    _emit({'done': True})
    while sys.stdin.buffer.read(65536):
        pass
    _hold_or_exit(args.job_handle)


if __name__ == '__main__':
    main()
