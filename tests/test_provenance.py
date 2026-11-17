# User intent: prove the provenance ledger, the read guard and the authors query decide who wrote a stored field, so
# the server frames teammates' text and leaves a member's own text bare (team-server-027 Steps 2-3).
import json
import sqlite3

import pytest

from taskmaster.native import provenance as pv, schema


def make_db(*, actor=True):
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.execute("CREATE TABLE entity_core(entity_key INTEGER PRIMARY KEY, kind TEXT, public_id TEXT, "
              "title_json TEXT, status_json TEXT)")
    cols = ", actor TEXT, entity_key INTEGER" if actor else ""
    c.execute("CREATE TABLE domain_events(seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, session TEXT, tool TEXT, "
              "kind TEXT, id TEXT, op TEXT, fields TEXT, before TEXT, after TEXT, commit_key INTEGER" + cols + ")")
    c.execute("CREATE INDEX ix_domain_events_entity ON domain_events(kind,id,seq)")
    if actor:
        c.execute(schema.SCHEMA_5_INDEX)
    return c


def entity(c, kind, ident):
    cur = c.execute("INSERT INTO entity_core(kind,public_id) VALUES(?,?)", (kind, ident))
    return (kind, ident, cur.lastrowid)


def event(c, ent, handle, fields, *, tool="backlog_update_task", op="update", after=None, key=True):
    actor = None if handle is None else json.dumps({"handle": handle, "agent": "claude", "clone_id": "c"})
    values = ("t", "s", tool, ent[0], ent[1], op, json.dumps(fields), "{}", json.dumps(after or {}), None)
    if c.execute("SELECT 1 FROM pragma_table_info('domain_events') WHERE name='actor'").fetchone():
        c.execute("INSERT INTO domain_events(ts,session,tool,kind,id,op,fields,before,after,commit_key,actor,entity_key)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", values + (actor, ent[2] if key else None))
    else:
        c.execute("INSERT INTO domain_events(ts,session,tool,kind,id,op,fields,before,after,commit_key)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?)", values)


def test_creator_is_author_and_own_is_empty():
    c = make_db()
    t = entity(c, "task", "t-1")
    event(c, t, "bob", ["title", "notes"], op="create")
    assert pv.authors_of(c, [t], reader="ann") == {"bob"}
    assert pv.authors_of(c, [t], reader="bob") == set()


def test_structured_only_change_is_not_authorship():
    c = make_db()
    t = entity(c, "task", "t-1")
    event(c, t, "bob", ["title", "status"], op="create")
    event(c, t, "ann", ["status"])
    assert pv.authors_of(c, [t], reader="bob") == set()
    event(c, t, "ann", ["notes"])
    assert pv.authors_of(c, [t], reader="bob") == {"ann"}
    assert pv.authors_of(c, [t], reader="ann") == {"bob"}


def test_null_actor_is_unattributed_and_pre_team_owner_and_cutoff():
    c = make_db()
    t = entity(c, "task", "t-1")
    event(c, t, None, ["title"], op="create")
    assert pv.authors_of(c, [t], reader="bob") == {pv.UNATTRIBUTED}
    cutoff = c.execute("SELECT MAX(seq) FROM domain_events").fetchone()[0]
    assert pv.authors_of(c, [t], reader="ann", pre_team=("ann", cutoff)) == set()
    assert pv.authors_of(c, [t], reader="bob", pre_team=("ann", cutoff)) == {"ann"}
    event(c, t, "bob", ["notes"])
    assert pv.authors_of(c, [t], reader="bob", pre_team=("ann", cutoff)) == {"ann"}
    # a laptop handle at or below the cutoff is pre-team whatever it says
    c2 = make_db()
    t2 = entity(c2, "task", "t-2")
    event(c2, t2, "bob", ["title"], op="create")
    cut2 = c2.execute("SELECT MAX(seq) FROM domain_events").fetchone()[0]
    assert pv.authors_of(c2, [t2], reader="bob", pre_team=("ann", cut2)) == {"ann"}
    # a NULL actor above the cutoff is never the owner's
    event(c2, t2, None, ["notes"])
    assert pv.authors_of(c2, [t2], reader="ann", pre_team=("ann", cut2)) == {pv.UNATTRIBUTED}
    # no cutoff: nothing is pre-team
    assert pv.authors_of(c2, [t2], reader="ann", pre_team=("ann", None)) == {"bob", pv.UNATTRIBUTED}


@pytest.mark.parametrize("tool,op", [("document.import", "update"), ("sync.apply", "update"),
                                      ("batch", "document.import")])
def test_import_is_imported_even_to_runner(tool, op):
    c = make_db()
    t = entity(c, "task", "t-1")
    event(c, t, "ann", ["notes"], tool=tool, op=op)
    assert pv.authors_of(c, [t], reader="ann") == {pv.IMPORTED}


def test_import_operations_pinned():
    assert pv.IMPORT_OPERATIONS == {"sync.apply", "document.import", "linear.settle"}


def test_copy_rule_bug_promote():
    c = make_db()
    bug = entity(c, "bug", "B-1")
    event(c, bug, "ann", ["title", "components"], op="create")
    issue = entity(c, "issue", "I-1")
    event(c, issue, "bob", ["title", "components", "promoted_from"], tool="backlog_bug_promote", op="create",
          after={"promoted_from": ["B-1"], "components": ["x"]})
    assert pv.authors_of(c, [issue], reader="bob") == {"ann"}
    # also when Bob passed his own list: the event doesn't say, over-framing is safe
    assert pv.authors_of(c, [issue], reader="ann") == {"bob"}


def test_external_note_adds_token_for_every_reader():
    ledger = pv.Ledger(reader="bob")
    tok = pv.bind(ledger)
    try:
        pv.note_external(pv.REPOSITORY)
    finally:
        pv.unbind(tok)
    assert ledger.external == {pv.REPOSITORY}


def test_singleton_kinds_by_kind_and_id():
    c = make_db()
    project = ("project", "project", None)
    event(c, project, "ann", ["conventions"], key=False)
    assert pv.authors_of(c, [project], reader="bob") == {"ann"}
    # Snapshot notes a singleton with its real core key, but its events keep entity_key NULL (review M1)
    assert pv.authors_of(c, [("project", "project", 7)], reader="bob") == {"ann"}


def test_no_actor_column():
    c = make_db(actor=False)
    ent = (("task", "t-1", None))
    event(c, ent, None, ["title"])
    assert pv.authors_of(c, [ent], reader="bob") == {pv.UNATTRIBUTED}


def test_many_keys_chunked():
    c = make_db()
    ents = []
    c.execute("BEGIN")
    for i in range(1200):
        e = entity(c, "task", f"t-{i}")
        event(c, e, "ann", ["title"], op="create")
        ents.append(e)
    c.execute("COMMIT")
    assert pv.authors_of(c, ents, reader="bob") == {"ann"}


def test_query_plan_uses_entity_key_index():
    c = make_db()
    placeholders, structured = pv._prose_placeholders()
    plan = c.execute("EXPLAIN QUERY PLAN SELECT DISTINCT d.kind FROM domain_events d WHERE d.entity_key IN (?,?) AND "
                     + pv._prose_event(placeholders), [1, 2] + structured).fetchall()
    assert any("ix_domain_events_entity_key" in str(row) for row in plan)


def test_store_wide_cache_incremental_and_rescan():
    c = make_db()
    t = entity(c, "task", "t-1")
    event(c, t, "ann", ["title"], op="create")
    cache = pv.StoreAuthors()
    assert cache.authors(c, store_id="s1", reader="bob") == {"ann"}
    event(c, t, "carol", ["notes"])
    assert cache.authors(c, store_id="s1", reader="bob") == {"ann", "carol"}
    assert cache._seq == 2
    assert cache.authors(c, store_id="s1", reader="ann") == {"carol"}
    # another store id, or a changed owner or cutoff, rescans from zero; the same key does not
    cache._tokens.add("stale")
    assert "stale" in cache.authors(c, store_id="s1", reader="bob")
    for kwargs in ({"store_id": "s2"}, {"store_id": "s2", "pre_team": ("ann", 1)},
                   {"store_id": "s2", "pre_team": ("carol", 1)}, {"store_id": "s2", "pre_team": ("carol", 2)}):
        cache._tokens.add("stale")
        assert "stale" not in cache.authors(c, reader="bob", **kwargs)
    # a lower max(seq) (restore) rescans
    cache._seq = 99
    cache._tokens.add("stale")
    assert "stale" not in cache.authors(c, store_id="s2", reader="bob", pre_team=("carol", 2))


def guard_db():
    c = sqlite3.connect(":memory:", isolation_level=None, cached_statements=0)
    c.execute("CREATE TABLE entity_core(entity_key INTEGER PRIMARY KEY, kind TEXT, public_id TEXT, title_json TEXT, "
              "status_json TEXT)")
    c.execute("INSERT INTO entity_core VALUES(1,'task','t-1','\"hello\"','\"open\"')")
    pv.install_guard(c)
    return c


def test_guard_flags_prose_outside_accounted_only():
    ledger = pv.Ledger(reader="bob")
    tok = pv.bind(ledger)
    try:
        c = guard_db()
        c.execute("SELECT title_json FROM entity_core").fetchall()
        assert ledger.unaccounted == ["entity_core.title_json"]
        ledger.unaccounted.clear()
        with pv.accounted():
            c.execute("SELECT title_json FROM entity_core").fetchall()
        assert ledger.unaccounted == []
        c.execute("SELECT public_id, json_extract(status_json,'$') FROM entity_core").fetchall()
        assert ledger.unaccounted == []
        # with no statement cache, a second execution of the same SQL is seen again
        c.execute("SELECT title_json FROM entity_core").fetchall()
        c.execute("SELECT title_json FROM entity_core").fetchall()
        assert len(ledger.unaccounted) == 2
    finally:
        pv.unbind(tok)


def test_guard_never_denies_and_noops_without_ledger():
    c = guard_db()
    assert c.execute("SELECT title_json FROM entity_core").fetchall() == [('"hello"',)]
    pv.note_entities([{"kind": "task", "public_id": "t-1", "entity_key": 1}])
    pv.note_receipt({"affected": [{"kind": "task", "id": "t-1"}]})
    pv.note_opened(); pv.note_external(pv.REPOSITORY); pv.note_unaccounted("x"); pv.note_refused()
    with pv.accounted():
        pass
    assert pv.current() is None


def test_notes_with_ledger():
    ledger = pv.Ledger(reader="bob")
    tok = pv.bind(ledger)
    try:
        pv.note_entities([{"kind": "task", "public_id": "t-1", "entity_key": 4}])
        pv.note_receipt({"affected": [{"kind": "epic", "id": "e-1"}]})
        pv.note_opened()
        pv.note_refused()
    finally:
        pv.unbind(tok)
    assert ledger.entities == {("task", "t-1", 4), ("epic", "e-1", None)}
    assert ledger.opened and ledger.refused
