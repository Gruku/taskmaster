"""Seed and serve an isolated N10 browser fixture, or perform a peer write on it.

Never points at the repository's live backlog. ctl requires the fixture marker.
"""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sys
import sqlite3
import tempfile
import threading

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]

from taskmaster import backlog_server as bs, store
from native_twins import scaffold, activate_native

MARKER = "taskmaster-n10-isolated-browser-fixture"


def bind(root):
    root = root.resolve()
    if (root / ".viewer-fixture").read_text(encoding="utf-8") != MARKER:
        raise ValueError("not an isolated viewer fixture")
    os.environ["TASKMASTER_ROOT"] = str(root)
    bs.ROOT = root
    bs.CONFIG_PATH = root / ".taskmaster/taskmaster.json"
    bs.LEGACY_CONFIG_PATH = root / ".claude/taskmaster.json"
    bs._HANDOVER_STATUS_BACKFILL_RAN = True
    os.chdir(root)
    store.reset_for_tests()
    assert bs._backlog_path().resolve() == root / ".taskmaster/backlog.yaml"


def seed(authority):
    parent = REPO / "test-results"
    parent.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix=f"n10-{authority}-", dir=parent))
    scaffold(root)
    (root / ".viewer-fixture").write_text(MARKER, encoding="utf-8")
    bind(root)
    bs.backlog_add_epic(epic_id="board", name="Board", done_when="fixture tested")
    bs.backlog_add_epic(epic_id="other", name="Other", done_when="fixture tested")
    bs.backlog_add_phase(phase_id="dev", name="Development")
    bs.backlog_add_phase(phase_id="next", name="Next")
    bs.backlog_update_epic(epic_id="board", field="status", value="active")
    for title in ("Board task", "Peer task", "Archive task", "Live claim", "Terminal claim", "Expired claim", "Dead holder"):
        bs.backlog_add_task(title=title, epic="board", phase="dev", notes="Fixture notes")
    bs.backlog_update_task(task_id="board-002", field="depends_on", value="board-001")
    bs.backlog_archive_task(task_id="board-003", reason="done")
    bs.backlog_pick_task(task_id="board-004")
    # Historical claims, as a migrated project can contain them. This fixture
    # deliberately bypasses current writer cleanup to exercise the read rule.
    with bs._transaction(tool="fixture:historical-claims") as data:
        epic = next(e for e in data['epics'] if e['id'] == 'board')
        epic['tasks'].extend(dict(id=f'bulk-{i:03d}', epic='board', title=f'Bulk {i}',
                                 status='archived', phase='dev') for i in range(201))
        for ident, status, holder, expires in (
            ("board-005", "done", bs.SESSION_ID, "2099-01-01T00:00:00Z"),
            ("board-006", "in-progress", bs.SESSION_ID, "2000-01-01T00:00:00Z"),
            ("board-007", "in-progress", f"{bs.socket.gethostname()}-4294967295-12345678", "2099-01-01T00:00:00Z"),
        ):
            task = bs._find_task(data, ident)[0]
            task.update(status=status, locked_by=holder, claim_expires=expires, claim_expires_for=holder)
        bs._mutate_and_save(data)
    if authority == "native":
        activate_native(root)
    return root


def mutate_fixture(args):
    from taskmaster.native_routing.viewer import database, _open
    native = database()
    if args.action == 'swap':
        (args.root / '.swap-request').touch()
        return 'swap requested'
    if args.action == 'burst':
        if native:
            with _open(native) as call:
                # Public batches are bounded at 100 commands. Accumulate 201
                # changed rows since the browser cursor across three commits.
                for start in range(0, 201, 100):
                    call.execute('batch', {'commands': [
                        {'operation': 'task.patch', 'arguments': {'id': f'bulk-{i:03d}', 'set': {'title': f'Changed {i}'}}}
                        for i in range(start, min(start + 100, 201))]})
        else:
            with bs._transaction(tool='fixture:burst') as data:
                for i in range(201):
                    bs._find_task(data, f'bulk-{i:03d}')[0]['title'] = f'Changed {i}'
                bs._mutate_and_save(data)
        return '201 rows committed'
    if args.action == 'remove':
        if native:
            # No public native delete command exists before N13. Inject the
            # future tombstone contract into this marked test fixture only.
            with closing(sqlite3.connect(native, isolation_level=None)) as conn:
                conn.execute('BEGIN IMMEDIATE')
                seq = conn.execute("INSERT INTO domain_events(ts,session,tool,kind,id,op) VALUES(?,?,?,?,?,?)",
                                   ('2026-09-22T12:00:00Z', 'fixture', 'fixture:remove', 'task', args.task, 'delete')).lastrowid
                conn.execute("UPDATE entity_core SET deleted=1,revision=revision+1,last_seq=? WHERE kind='task' AND public_id=?",
                             (seq, args.task))
                conn.commit()
        else:
            # Omitting a row from the compatibility dict is deliberately not a
            # deletion. Use the legacy store's explicit tombstone operation.
            with bs._store().transaction(tool='fixture:remove') as tx:
                tx.delete('task', args.task)
        return 'removed'
    if args.field == 'epic':
        # Epic reassignment belongs to the viewer writer, not update_task's
        # narrower tool field allowlist.
        if native:
            with _open(native) as call:
                call.execute('task.viewer_update', {'id': args.task, 'patch': {'epic': args.value}, 'if_match': ''})
        else:
            bs._viewer_update_task(args.task, {'epic': args.value})
        return 'epic reassigned'
    return bs.backlog_update_task(task_id=args.task, field=args.field, value=args.value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=("serve", "ctl"), default="serve")
    parser.add_argument("--store", choices=("legacy", "native"), default="legacy")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--task", default="board-001")
    parser.add_argument("--field", default="title")
    parser.add_argument("--value", default="Peer edited")
    parser.add_argument('--action', choices=('update', 'remove', 'burst', 'swap'), default='update')
    args = parser.parse_args()
    if args.mode == "ctl":
        if args.root is None:
            parser.error("ctl requires --root")
        bind(args.root)
        result = mutate_fixture(args)
        if str(result).startswith("Error"):
            raise SystemExit(str(result))
        # Pipe-safe on Windows consoles whose default encoding is cp1252.
        print(json.dumps({"result": result}, ensure_ascii=True))
        return
    root = seed(args.store)
    while True:
        server, port = bs._make_server(host="127.0.0.1", port=args.port)
        print(json.dumps({"root": str(root), "port": port}), flush=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            while not (root / '.swap-request').exists():
                thread.join(0.1)
                if not thread.is_alive():
                    return
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        # Rebind only after every request on the old server has stopped.
        store.reset_for_tests()
        root = seed(args.store)


if __name__ == "__main__":
    main()
