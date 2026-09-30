"""Direct FTS5 queries, preserving porter/unicode61 and bm25 semantics."""
from taskmaster.query_guard import Deadline, PROGRESS_INSTRUCTIONS, QUERY_TIMEOUT_S
from .migrate import rows

PROSE_FIELDS = ("description", "notes", "review_instructions", "next_action", "tldr", "impact", "evidence", "options", "branch", "done_when")


def inputs(doc, body):
    from taskmaster.paths import as_list, normalize_task_anchor
    title = str(doc.get("title") or doc.get("name") or doc.get("tldr") or "")
    parts = [str(doc.get(field) or "") for field in PROSE_FIELDS]
    if isinstance(doc.get("docs"), dict):
        parts.extend(str(value or "") for value in doc["docs"].values())
    parts.extend(normalize_task_anchor(str(anchor), doc.get("sub_repo"))[0] for anchor in as_list(doc.get("anchors")))
    if body:
        parts.append(str(body))
    return title, "\n".join(part for part in parts if part)


def maintain(connection, entity_key, kind, ident, before, after, *, deleted=False):
    """Replace only a changed document; stable keys survive delete/reinsert."""
    if before == after and not deleted:
        return False
    key = connection.execute("SELECT document_key FROM document_search_keys WHERE entity_key=?", (entity_key,)).fetchone()
    if key:
        connection.execute("DELETE FROM document_search WHERE rowid=?", (key[0],))
    if deleted:
        return bool(key)
    if key is None:
        connection.execute("INSERT INTO document_search_keys(entity_key) VALUES(?)", (entity_key,))
        key = connection.execute("SELECT document_key FROM document_search_keys WHERE entity_key=?", (entity_key,)).fetchone()
    connection.execute("INSERT INTO document_search(rowid,kind,id,title,body) VALUES(?,?,?,?,?)", (key[0], kind, ident, *after))
    return True


def search(connection, text, *, kind=None, limit=50, include_archived=True):
    from .queries import page_limit
    page_limit(limit)
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 16384:
        raise ValueError("search requires a nonempty query up to 16384 bytes")
    conditions = ["document_search MATCH ?", "c.deleted=0"]
    args = [text]
    if kind is not None:
        conditions.append("c.kind=?")
        args.append(kind)
    if not include_archived:
        conditions.append("c.archived=0")
    deadline = Deadline(QUERY_TIMEOUT_S)
    connection.set_progress_handler(deadline, PROGRESS_INSTRUCTIONS)
    try:
        return rows(connection, "SELECT c.kind,c.public_id id,bm25(document_search) rank "
                    "FROM document_search JOIN document_search_keys k ON k.document_key=document_search.rowid "
                    "JOIN entity_core c ON c.entity_key=k.entity_key WHERE " + " AND ".join(conditions) +
                    " ORDER BY bm25(document_search),document_search.rowid LIMIT ?", args + [limit])
    finally:
        connection.set_progress_handler(None, 0)
