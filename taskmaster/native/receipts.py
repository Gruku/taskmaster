"""Durable scoped retry receipts; keys are retained without silent expiration."""
import json
from .contracts import Conflict
from .migrate import encode


def lookup(connection, request, fingerprint):
    if request.get("request_id") is None:
        return None
    row = connection.execute("SELECT payload_hash,outcome_json,expires_at FROM command_receipts "
                             "WHERE store_id=? AND caller_scope=? AND request_id=?",
                             (request["store_id"], request["caller_scope"], request["request_id"])).fetchone()
    if row is None:
        return None
    if row[0] != fingerprint:
        raise Conflict("request_id reused with a different payload")
    # No GC currently runs. Future expiry must preserve a rejected-key tombstone.
    if row[2] is not None:
        raise Conflict("receipt retry window no longer available; use a new request_id")
    return json.loads(row[1])


def save(connection, request, fingerprint, outcome):
    if request.get("request_id") is not None:
        connection.execute("INSERT INTO command_receipts(store_id,caller_scope,request_id,payload_hash,outcome_json,commit_seq) VALUES(?,?,?,?,?,?)",
                           (request["store_id"], request["caller_scope"], request["request_id"], fingerprint, encode(outcome), outcome["commit_seq"]))
