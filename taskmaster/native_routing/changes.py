# User intent: serve `backlog_changes_since` from the native core, so a resuming
# session asks what moved instead of re-reading every task and every body.
"""`backlog_changes_since` over the native core."""
from __future__ import annotations

from taskmaster import backlog_server as bs
from taskmaster.native import cursors

from .registry import adapter


def _created_of(snapshot, ident):
    """`bs._change_anchor_seq`'s `created_of`: when an entity says it was written."""
    row = snapshot.connection.execute(
        "SELECT kind FROM entity_core WHERE public_id=? AND deleted=0 ORDER BY kind LIMIT 1", (ident,)).fetchone()
    if row is None:
        return None
    fields = snapshot.get(row[0], ident)["fields"]
    return str(fields.get("created") or fields.get("date") or "")


@adapter("backlog_changes_since")
def backlog_changes_since(call, *, cursor, kinds, ids, epic, limit, group_commits, since_seq, since):
    # A change query is a read: no command, no projection drain, no `[seq N]`
    # stamp. The answer's own `sequence` is what a caller correlates against.
    with call.read() as snapshot:
        try:
            bs._change_query_scope(cursor, kinds, ids, epic, limit, group_commits, since_seq, since)
            if since.strip():
                # Resolved in the snapshot the feed is read from, by the legacy
                # tool's own rule; `domain_events` keeps the `changes` columns.
                since_seq = bs._change_anchor_seq(snapshot.connection, "domain_events", since,
                                                  lambda ident: _created_of(snapshot, ident))
            answer = snapshot.changes_since(cursor, kinds=kinds, ids=ids, epic=epic, limit=limit,
                                            group_commits=group_commits, since_seq=since_seq,
                                            skip_imports=bool(since.strip()))
        except (ValueError, KeyError) as exc:
            return cursors.refusal(exc)
    return cursors.present(answer)
