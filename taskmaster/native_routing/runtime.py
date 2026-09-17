# User intent: give every native adapter one way to read a snapshot, run a native
# command and make its files visible, so presentation stays in the adapter and no
# adapter can reach the legacy store, its dict or a projection scan.
"""Per-call native session: one connection, snapshot reads, commands, export drain."""
from __future__ import annotations

from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import uuid

from taskmaster.native import commands
from taskmaster.native.queries import Repository
from . import projection


class NativeCall:
    """One public tool call against a native-authority store.

    Commands run one at a time; each is its own native transaction and is followed
    immediately by the synchronous export drain, so a later command in the same
    call reads what the earlier one committed and the files match before the call
    returns. `seq` is the last commit's sequence and `notices` the export
    warnings, which `finish` stamps onto the result the way the legacy
    transaction wrapper does.
    """

    def __init__(self, connection: sqlite3.Connection, backlog_dir: Path, session: str):
        self.connection, self.backlog_dir, self.session = connection, backlog_dir, session
        self.seq: int | None = None
        self.notices: list[str] = []
        self.receipts: list[dict] = []

    @contextmanager
    def read(self):
        with Repository(self.connection).snapshot() as snapshot:
            yield snapshot

    def execute(self, operation: str, arguments: dict, *, expected=None) -> dict:
        store_id = self.connection.execute(
            "SELECT value FROM native_manifest WHERE key='store_id'").fetchone()[0]
        envelope = {"protocol": 2, "store_id": store_id, "caller_scope": self.session,
                    "request_id": uuid.uuid4().hex, "operation": operation,
                    "arguments": arguments, "expected_revisions": list(expected or [])}
        receipt = commands.execute(self.connection, envelope)
        self.drain()
        self.receipts.append(receipt)
        if receipt["affected"]:
            self.seq = receipt["commit_seq"]
        return receipt

    def drain(self) -> None:
        for notice in projection.drain(self.connection, self.backlog_dir, session=self.session):
            if notice not in self.notices:
                self.notices.append(notice)

    def finish(self, result):
        from taskmaster import backlog_server
        return backlog_server._stamp_result(result, self.seq, list(self.notices))


@contextmanager
def open_call(database: Path, backlog_dir: Path, session: str):
    with closing(sqlite3.connect(database, isolation_level=None, timeout=30,
                                 check_same_thread=False)) as connection:
        connection.execute("PRAGMA busy_timeout=30000")
        yield NativeCall(connection, backlog_dir, session)


def error_text(exc: BaseException) -> str:
    """A refusal from the core as the tools' own `Error: …` sentence."""
    message = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
    return f"Error: {message}"
