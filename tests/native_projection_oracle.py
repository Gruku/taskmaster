"""Frozen N11 engine-plus-drain test harness, never a production fallback.

Projection crash/lease tests deliberately exercise the low-level synchronous
primitive. Public compatibility twins use the real coordinator with explicit
legacy visibility instead. Keep both lanes distinct after N12 routing changes.
"""
from contextlib import closing, contextmanager
import os
from pathlib import Path
import sqlite3
import uuid

from taskmaster.native import commands
from taskmaster.native.queries import Repository
from taskmaster.native_routing import progress, projection


def child_environment():
    """Crash helpers import this checkout, not an unrelated editable install."""
    repo = str(Path(__file__).resolve().parents[1])
    environment = dict(os.environ)
    environment['PYTHONPATH'] = repo + (os.pathsep + environment['PYTHONPATH'] if environment.get('PYTHONPATH') else '')
    return environment


class NativeCall:
    def __init__(self, connection, backlog_dir, session):
        self.connection, self.backlog_dir, self.session = connection, backlog_dir, session
        self.seq = None
        self.notices = []
        self.receipts = []

    @contextmanager
    def read(self):
        with Repository(self.connection).snapshot() as snapshot:
            yield snapshot

    def execute(self, operation, arguments, *, expected=None):
        store_id = self.connection.execute("SELECT value FROM native_manifest WHERE key='store_id'").fetchone()[0]
        receipt = commands.execute(self.connection, {
            'protocol': 2, 'store_id': store_id, 'caller_scope': self.session,
            'request_id': uuid.uuid4().hex, 'operation': operation,
            'arguments': arguments, 'expected_revisions': list(expected or [])})
        self.drain(through=receipt['commit_seq'])
        self.receipts.append(receipt)
        if receipt['affected']:
            self.seq = receipt['commit_seq']
        return receipt

    def drain(self, through=None):
        waited = progress.NOTICE in self.notices
        for notice in projection.drain(self.connection, self.backlog_dir, session=self.session,
                                       through=through, progress_wait=not waited):
            if notice not in self.notices:
                self.notices.append(notice)

    def finish(self, result):
        from taskmaster import backlog_server
        return backlog_server._stamp_result(result, self.seq, list(self.notices))


@contextmanager
def open_call(database, backlog_dir, session):
    with closing(sqlite3.connect(database, isolation_level=None, timeout=30, check_same_thread=False)) as connection:
        connection.execute('PRAGMA busy_timeout=30000')
        yield NativeCall(connection, backlog_dir, session)
