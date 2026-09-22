"""Per-call adapter over read-only snapshots and the repository coordinator.

There is deliberately no direct command/export path or writer fallback here.
"""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import uuid

from taskmaster.native.queries import Repository
from .client import Client
from .protocol import connect


class NativeCall:
    def __init__(self, connection: sqlite3.Connection, backlog_dir: Path, session: str, *, visibility='native'):
        if visibility not in ('native', 'legacy'):
            raise ValueError('visibility must be native or legacy')
        self.connection, self.backlog_dir, self.session = connection, backlog_dir, session
        self.visibility = visibility
        self.seq = None
        self.notices = []
        self.receipts = []
        self.projections = []
        self._client = None

    @property
    def client(self):
        # Reads do not discover/start a service. Only a requested mutation or
        # explicit publication barrier constructs its local IPC client.
        if self._client is None:
            self._client = Client(self.backlog_dir.parent, visibility=self.visibility)
        return self._client

    @contextmanager
    def read(self):
        with Repository(self.connection).snapshot() as snapshot:
            yield snapshot

    def execute(self, operation, arguments, *, expected=None):
        with self.read() as snapshot:
            store_id = snapshot.identity['store_id']
        envelope = {'protocol': 2, 'store_id': store_id, 'caller_scope': self.session,
                    'request_id': uuid.uuid4().hex, 'operation': operation,
                    'arguments': arguments, 'expected_revisions': list(expected or [])}
        outcome = self.client.execute(envelope)
        receipt = outcome['receipt']
        self.receipts.append(receipt)
        self._projection(outcome['projection'])
        if receipt['affected']:
            self.seq = receipt['commit_seq']
        return receipt

    def _projection(self, projection):
        self.projections.append(projection)
        notices = list(projection.get('notices') or [])
        if projection['state'] == 'pending' and not notices:
            notices.append(f"export pending: committed through sequence {projection['through']}; background export queued")
        for notice in notices:
            if notice not in self.notices:
                self.notices.append(notice)

    def drain(self, through=None):
        if through is None:
            with self.read() as snapshot:
                through = int(snapshot.identity['event_high_water'])
        self._projection(self.client.flush(through))

    def finish(self, result):
        from taskmaster import backlog_server
        return backlog_server._stamp_result(result, self.seq, list(self.notices))


@contextmanager
def open_call(database: Path, backlog_dir: Path, session: str, *, visibility='native'):
    backlog_dir = Path(backlog_dir).resolve(strict=True)
    if Path(database).resolve(strict=True) != backlog_dir / 'local/store.db':
        raise ValueError('native database does not belong to the requested backlog directory')
    with closing(connect(backlog_dir.parent, readonly=True)) as connection:
        yield NativeCall(connection, backlog_dir, session, visibility=visibility)
