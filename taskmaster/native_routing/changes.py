# User intent: serve `backlog_changes_since` from the native core, so a resuming
# session asks what moved instead of re-reading every task and every body.
"""`backlog_changes_since` over the native core."""
from __future__ import annotations

from taskmaster.native import cursors

from .registry import adapter


@adapter("backlog_changes_since")
def backlog_changes_since(call, *, cursor, kinds, ids, epic, limit, group_commits, since_seq):
    # A change query is a read: no command, no projection drain, no `[seq N]`
    # stamp. The answer's own `sequence` is what a caller correlates against.
    with call.read() as snapshot:
        try:
            answer = snapshot.changes_since(cursor, kinds=kinds, ids=ids, epic=epic, limit=limit,
                                            group_commits=group_commits, since_seq=since_seq)
        except (ValueError, KeyError) as exc:
            return cursors.refusal(exc)
    return cursors.present(answer)
