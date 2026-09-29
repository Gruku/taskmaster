"""User intent: a packaged way for an operator to see and stop a project's coordinator (for
example before a cutover step), without building a venv inside the plugin cache or needing
a pyproject.toml the Codex snapshot does not ship.

    uv run <plugin>/taskmaster_cli.py coordinator status [--root DIR]
    uv run <plugin>/taskmaster_cli.py coordinator stop [--root DIR] [--wait SECONDS]

Prints JSON. `stop` shuts down a coordinator of this build gracefully (the `shutdown`
command) and waits for it to release its ownership lock. It never starts one, and never
runs a command on a coordinator of another build: it reports that build and pid and how to
stop it instead, and exits 1. Exit 0: stopped, not running, or (status) any answer.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

DISCOVERY = '.taskmaster/local/coordinator/discovery.json'
LEFT_BEHIND = (f'{DISCOVERY} is left behind after a coordinator exits and can name a pid that has exited '
               '(or been reused by another process); it is harmless: the next write starts a coordinator, '
               'which replaces it')


def _other_build(record, mine) -> str:
    from .protocol import describe
    pid = record.get('pid')
    return (f'the coordinator (pid {pid}) runs taskmaster build {describe(record.get("build"))}, not this CLI\'s '
            f'build {describe(mine)}, and a command never runs on another build, so this CLI did not stop it. '
            f'To stop it: run any write from a session of a newer build, which retires an idle older '
            f'coordinator; or wait until it is idle, when it exits by itself (TASKMASTER_SERVICE_IDLE_SECONDS, '
            f'300 s by default); or end the session that started it, or stop pid {pid}')


def _inspect(client):
    """(held, record): whether an owner holds the lock, and its discovery record (or None)."""
    from .ownership import ownership_held
    held = ownership_held(client.root)
    try:
        record = client._discovery()
    except FileNotFoundError:
        record = None
    return held, record


def _report(client, held, record):
    from .protocol import same_build
    if not held:
        report = {'state': 'stopped'}
        if record is not None:
            report['note'] = LEFT_BEHIND
        return report
    if record is None:
        return {'state': 'starting', 'note': f'the ownership lock is held but {DISCOVERY} is not published yet'}
    report = {'state': 'running', 'pid': record.get('pid'), 'build': record.get('build'),
              'same_build': same_build(record.get('build'), client.build)}
    if not report['same_build']:
        report['guidance'] = _other_build(record, client.build)
    return report


def status(client):
    held, record = _inspect(client)
    report = _report(client, held, record)
    if report.get('same_build'):
        answer = client._send(record, 'status')
        report.update({key: answer.get(key) for key in ('queued', 'linear_queued', 'active_syncs', 'export_error')})
    return 0, report


def stop(client, wait):
    from .ownership import ownership_held
    held, record = _inspect(client)
    report = _report(client, held, record)
    if report['state'] == 'stopped':
        report['detail'] = 'no coordinator is running'
        return 0, report
    if not report.get('same_build'):
        return 1, report
    report.update(client._send(record, 'shutdown'))  # {'state': 'stopping'}
    deadline = time.monotonic() + wait
    while ownership_held(client.root):
        if time.monotonic() >= deadline:
            report['detail'] = (f'still finishing in-flight work after {wait:g} s; it exits once that is done. '
                                'Run `coordinator status` to check')
            return 1, report
        time.sleep(0.05)
    report['state'] = 'stopped'
    report['note'] = LEFT_BEHIND
    return 0, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', type=Path, default=None, help='main checkout (default: resolved from cwd)')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status', help='report the running coordinator, its pid and build')
    stop_parser = commands.add_parser('stop', help='gracefully stop a coordinator of this build')
    stop_parser.add_argument('--wait', type=float, default=30.0,
                             help='seconds to wait for it to release its lock (default 30)')
    # --root is accepted before or after the subcommand.
    for sub in commands.choices.values():
        sub.add_argument('--root', type=Path, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    from .client import Client
    from taskmaster.root import resolve_root
    root = args.root or resolve_root(Path.cwd()).root
    client = Client(root, autostart=False, timeout=15)
    code, report = status(client) if args.command == 'status' else stop(client, args.wait)
    report.setdefault('root', str(client.root))
    print(json.dumps(report, indent=1, default=str))
    return code


if __name__ == '__main__':
    sys.exit(main())
