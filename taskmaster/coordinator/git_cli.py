"""User intent: explicit managed Git commands for hosts and humans, so projections are
committed or checked out only under the coordinator's publication pin.

    python -m taskmaster.coordinator.git_cli commit -m MESSAGE [--request-id ID]
    python -m taskmaster.coordinator.git_cli checkout REF [--request-id ID]
    python -m taskmaster.coordinator.git_cli status
    python -m taskmaster.coordinator.git_cli recover [--acknowledge-quiescent] [--accept-outcome]
                                                     [--release-drift {import,take-published} [--worktree W]]
commit/checkout run in the linked worktree containing cwd unless --worktree names one;
recover acts on the main checkout unless --worktree names a linked one (never cwd).
--release-drift import: ordinary sync imports the held bytes; take-published: the held
bytes (still exactly as held) are retained and receive the published file. The store is
always the main checkout's. Prints JSON with the checkout acted on. Exit 0 only for
completed/clear outcomes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import uuid

OK_STATES = {'completed', 'clear', 'accepted'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', type=Path, default=None, help='main checkout (default: resolved from cwd)')
    parser.add_argument('--worktree', type=Path, default=None,
                        help='linked checkout to operate on (default: the checkout containing cwd)')
    parser.add_argument('--timeout', type=int, default=600, help='seconds allowed for the Git child')
    parser.add_argument('--sync-timeout', type=int, default=None,
                        help='seconds allowed for the pre-sync (default: the coordinator sync budget)')
    commands = parser.add_subparsers(dest='command', required=True)
    commit = commands.add_parser('commit')
    commit.add_argument('-m', '--message', required=True)
    commit.add_argument('--request-id')
    checkout = commands.add_parser('checkout')
    checkout.add_argument('ref')
    checkout.add_argument('--request-id')
    commands.add_parser('status')
    recover = commands.add_parser('recover')
    recover.add_argument('--acknowledge-quiescent', action='store_true',
                         help='assert no Git/hook process of the interrupted operation remains')
    recover.add_argument('--accept-outcome', action='store_true',
                         help='accept an inspected ambiguous Git state and release the pin')
    recover.add_argument('--release-drift', choices=['import', 'take-published'], default=None,
                         help='import: ordinary sync imports the held bytes; take-published: the held bytes are '
                              'retained and replaced by the published file')
    args = parser.parse_args(argv)
    from .client import Client
    from taskmaster.root import _git_checkout_root, resolve_root
    root = args.root or resolve_root(Path.cwd()).root
    # Only commit/checkout default to the checkout containing cwd; recover acts on an
    # explicitly named checkout. Either way the result names the checkout acted on.
    worktree = args.worktree or (_git_checkout_root(Path.cwd()) if args.command in ('commit', 'checkout') else None)
    if worktree is not None and Path(worktree).resolve() == Path(root).resolve():
        worktree = None
    if args.command == 'recover' and worktree is not None and not args.release_drift:
        parser.error('--worktree applies to recover only with --release-drift')
    client = Client(root, timeout=args.timeout + 60)
    if args.command in ('commit', 'checkout'):
        # Print the id first: a lost response is recovered by retrying it, never by re-running Git.
        request_id = args.request_id or uuid.uuid4().hex
        print(json.dumps({'request_id': request_id}), file=sys.stderr)
        result = client.git_run(kind=args.command, message=getattr(args, 'message', None),
                                ref=getattr(args, 'ref', None), request_id=request_id, timeout=args.timeout,
                                sync_timeout=args.sync_timeout,
                                worktree=worktree)
    elif args.command == 'status':
        result = client.git_status()
        result = dict(result, state='clear' if result['active'] is None and result['pin'] is None else 'pinned')
    else:
        release = None if args.release_drift is None else args.release_drift.replace('-', '_')
        result = client.git_recover(acknowledge_quiescent=args.acknowledge_quiescent,
                                    accept_outcome=args.accept_outcome, release_drift=release,
                                    worktree=worktree)
    result = dict(result)
    result.setdefault('checkout', str(worktree if worktree is not None else root))
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get('state') in OK_STATES else 1


if __name__ == '__main__':
    sys.exit(main())
