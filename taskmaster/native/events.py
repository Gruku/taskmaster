"""Domain event groups share the command's writer transaction."""
from datetime import datetime, timezone
from .migrate import encode


def append(connection, request, group, kind, ident, operation, before, after):
    if group is None:
        group = connection.execute("INSERT INTO command_commits(operation) VALUES(?)", (request["operation"],)).lastrowid
    seq = connection.execute("INSERT INTO domain_events(ts,session,tool,kind,id,op,fields,before,after,commit_key) VALUES(?,?,?,?,?,?,?,?,?,?)",
                             (datetime.now(timezone.utc).isoformat(), request["caller_scope"], request["operation"], kind, ident, operation,
                              encode(sorted(set(before) | set(after))), encode(before), encode(after), group)).lastrowid
    connection.execute("UPDATE command_commits SET first_seq=COALESCE(first_seq,?),final_seq=? WHERE commit_key=?", (seq, seq, group))
    return group, seq
