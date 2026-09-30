"""User intent: disposable Git repositories around a native test root, with
test-only hooks that can pause or fail Git. Never touches a real repository.
"""
import os
from pathlib import Path
import subprocess
import time

REL = '.taskmaster/tasks/test-epic-001.md'


def git(root, *args, check=True):
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
    completed = subprocess.run(['git', *args], cwd=root, env=env, capture_output=True, text=True,
                               encoding='utf-8', errors='replace')
    if check and completed.returncode:
        raise AssertionError(f'git {args} failed: {completed.stderr}')
    return completed.stdout


def init_repo(root):
    root = Path(root)
    git(root, 'init', '-q', '-b', 'main')
    for key, value in (('user.name', 'Taskmaster Tests'), ('user.email', 'tests@example.invalid'),
                       ('commit.gpgsign', 'false'), ('core.hooksPath', '.git/hooks'), ('core.autocrlf', 'false')):
        git(root, 'config', key, value)
    (root / '.gitignore').write_text('.taskmaster/local/\nhook-*\nexport-*\nreached-*\n', encoding='utf-8')
    git(root, 'add', '-A')
    git(root, 'commit', '-q', '-m', 'initial')
    return git(root, 'rev-parse', 'HEAD').strip()


def install_hook(root, name, body):
    """A hook inside this disposable repository only."""
    hook = Path(root) / '.git' / 'hooks' / name
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text('#!/bin/sh\n' + body + '\n', encoding='utf-8', newline='\n')
    return hook


def pausing_hook(root, name='pre-commit'):
    base = Path(root).as_posix()
    return install_hook(root, name, f'touch "{base}/hook-entered"\n'
                                    f'while [ ! -f "{base}/hook-release" ]; do sleep 0.05; done\nexit 0')


def wait_for(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def show(root, rev, path=REL):
    return git(root, 'show', f'{rev}:{path}')


def commit_count(root):
    return int(git(root, 'rev-list', '--count', 'HEAD').strip())


def gone(name, timeout=10):
    """The job name disappears once every handle closes; handle-table teardown of an
    exited member can lag its active-count decrement by a moment."""
    from taskmaster.coordinator import job as jobs

    def absent():
        found = jobs.Job.open(name)
        if found is None:
            return True
        found.close()
        return False
    return wait_for(absent, timeout)
