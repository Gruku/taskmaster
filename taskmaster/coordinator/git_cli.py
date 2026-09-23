"""User intent: explicit managed Git commands for hosts and humans, so projections are
committed or checked out only under the coordinator's publication pin.

    python -m taskmaster.coordinator.git_cli commit -m MESSAGE [--request-id ID]
    python -m taskmaster.coordinator.git_cli checkout REF [--request-id ID]
    python -m taskmaster.coordinator.git_cli status
    python -m taskmaster.coordinator.git_cli recover [--acknowledge-quiescent] [--accept-outcome] [--release-drift]
Prints JSON. Exit 0 only for completed/clear outcomes.
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
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--timeout', type=int, default=600)
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
    recover.add_argument('--release-drift', action='store_true',
                         help='let ordinary sync import/repair paths a managed checkout left drifted')
    args = parser.parse_args(argv)
    from .client import Client
    client = Client(args.root, timeout=args.timeout + 60)
    if args.command in ('commit', 'checkout'):
        # Print the id first: a lost response is recovered by retrying it, never by re-running Git.
        request_id = args.request_id or uuid.uuid4().hex
        print(json.dumps({'request_id': request_id}), file=sys.stderr)
        result = client.git_run(kind=args.command, message=getattr(args, 'message', None),
                                ref=getattr(args, 'ref', None), request_id=request_id, timeout=args.timeout)
    elif args.command == 'status':
        result = client.git_status()
        result = dict(result, state='clear' if result['active'] is None and result['pin'] is None else 'pinned')
    else:
        result = client.git_recover(acknowledge_quiescent=args.acknowledge_quiescent,
                                    accept_outcome=args.accept_outcome, release_drift=args.release_drift)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get('state') in OK_STATES else 1


if __name__ == '__main__':
    sys.exit(main())
