"""Four copy-only browser measurements, strictly sequential and windowless.

Each child owns its fixture server in a thread and closes it after Chromium;
no detached servers or PID-based cleanup are needed.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--copies', type=Path, required=True)
    parser.add_argument('--node', default='node')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    copies = args.copies.resolve(strict=True)
    if (repo / 'test-results').resolve() not in copies.parents:
        parser.error('copies must be under this worktree test-results')
    flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    for store in ('legacy', 'native'):
        for baseline in (False, True):
            name = f'browser-{store}' + ('-baseline' if baseline else '')
            output = copies / f'{name}.json'
            if output.exists():
                raise FileExistsError(output)
            command = [sys.executable, str(repo / 'scripts/native_n10_viewer_bench.py'),
                       '--serve-copy', str(copies / store / 'project'), '--port', '0',
                       '--browser-output', str(output), '--node', args.node]
            if baseline:
                command.append('--baseline')
            print(f'Starting {name}', flush=True)
            subprocess.run(command, cwd=repo, check=True, **flags)
            print(f'Completed {name}', flush=True)


if __name__ == '__main__':
    main()
