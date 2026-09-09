"""Broader performance audit. All mutations use benchmark_store's isolated copy.

Algorithms are experimental measurement code, never installed in the store.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
from contextlib import contextmanager
import fnmatch
from functools import wraps
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

from benchmark_store import REPO, bind, call, handover, snapshot
from taskmaster.integrity import check_database


def measure(fn, repeat=3):
    results = []
    for _ in range(repeat):
        start = time.perf_counter()
        try:
            value = fn()
            encoded = str(value)
            results.append({"seconds": time.perf_counter()-start,
                            "ok": not encoded.startswith("Error"), "bytes": len(encoded.encode()),
                            "preview": encoded[:180]})
        except Exception as exc:
            results.append({"seconds": time.perf_counter()-start, "ok": False, "error": repr(exc)})
    return results


def grouped_weights(rows, prefix=False):
    """Preserve raw-row multiplicity and the original fnmatchcase semantics."""
    groups = defaultdict(Counter)
    for kind, ident, path, match in rows:
        groups[path, match][kind, ident] += 1
    keys = sorted(groups)
    matches = set()
    by_path = defaultdict(list)
    for i, (path, _) in enumerate(keys):
        by_path[path].append(i)
    for indices in by_path.values():
        for pos, i in enumerate(indices):
            for j in indices[pos + 1:]:
                matches.add((i, j))
    ordered_paths = sorted(by_path)
    comparisons = 0
    for i, (pattern, match) in enumerate(keys):
        if match != "glob":
            continue
        candidates = ordered_paths
        if prefix:
            literal = re.split(r"[*?\[]", pattern, maxsplit=1)[0]
            start = bisect_left(ordered_paths, literal)
            candidates = []
            for path in ordered_paths[start:]:
                if not path.startswith(literal):
                    break
                candidates.append(path)
        for path in candidates:
            comparisons += 1
            if fnmatch.fnmatchcase(path, pattern):
                for j in by_path[path]:
                    if i != j:
                        matches.add(tuple(sorted((i, j))))
    weights = Counter()
    for counts in groups.values():
        entities = sorted(counts)
        for pos, left in enumerate(entities):
            for right in entities[pos + 1:]:
                weights[left, right] += counts[left] * counts[right]
    for i, j in matches:
        for left, left_count in groups[keys[i]].items():
            for right, right_count in groups[keys[j]].items():
                if left != right:
                    weights[tuple(sorted((left, right)))] += left_count * right_count
    return weights, {"groups": len(groups), "unique_paths": len(by_path),
                     "glob_groups": sum(m == "glob" for _, m in keys),
                     "glob_comparisons": comparisons, "matching_group_pairs": len(matches)}


def reference_weights(rows):
    weights = Counter()
    for i, (kind, ident, path, match) in enumerate(rows):
        left = (kind, ident)
        for rk, ri, rp, rm in rows[i+1:]:
            right = (rk, ri)
            if left != right and (path == rp or (match == "glob" and fnmatch.fnmatchcase(rp, path))
                                  or (rm == "glob" and fnmatch.fnmatchcase(path, rp))):
                weights[tuple(sorted((left, right)))] += 1
    return weights


def algorithms(root, output):
    root = root.resolve()
    if not (root / ".benchmark-copy").exists():
        raise ValueError("Isolated copy required")
    with sqlite3.connect(root / ".taskmaster/local/store.db") as db:
        rows = db.execute("SELECT kind,id,path,match_kind FROM entity_paths WHERE source IN ('anchors','location') ORDER BY kind,id,path").fetchall()
        stored = Counter({((a, ai), (b, bi)): w for a, ai, b, bi, w in db.execute(
            "SELECT a_kind,a_id,b_kind,b_id,weight FROM related WHERE via='path'")})
    report = {"rows": len(rows), "candidate_pairs": len(rows)*(len(rows)-1)//2, "variants": {}}
    for name, fn in (("reference", lambda: (reference_weights(rows), {})),
                     ("grouped", lambda: grouped_weights(rows)),
                     ("grouped_prefix", lambda: grouped_weights(rows, True))):
        samples = []
        for _ in range(3):
            start = time.perf_counter()
            weights, counts = fn()
            samples.append(time.perf_counter()-start)
            assert weights == stored, f"{name} differs from stored path relationship weights"
        report["variants"][name] = {"seconds": samples, "equivalent": True, "edges": len(weights), **counts}
        output.write_text(json.dumps(report, indent=2))
        print(name, report["variants"][name], flush=True)
    # Pin duplicate rows, both-direction globs, literal wildcard characters,
    # character classes, exact/glob same text, same entity, and case semantics.
    import random
    rng = random.Random(1701)
    paths = ["src/a.py", "src/B.py", "src/*", "src/?.py", "*", "src/[aB].py", "docs/x", "SRC/a.py"]
    for _ in range(100):
        fixture = [("task", f"t{rng.randrange(6)}", rng.choice(paths), rng.choice(["glob", "exact"])) for _ in range(30)]
        expected = reference_weights(fixture)
        assert grouped_weights(fixture)[0] == expected
        assert grouped_weights(fixture, True)[0] == expected
    report["seeded_equivalence_fixtures"] = 100
    output.write_text(json.dumps(report, indent=2))


def optimized_rebuild(connection):
    rows = [tuple(row) for row in connection.execute("SELECT kind,id,path,match_kind FROM entity_paths WHERE source IN ('anchors','location')")]
    weights, _ = grouped_weights(rows, True)
    connection.execute("DELETE FROM related")
    connection.executemany("INSERT INTO related(a_kind,a_id,b_kind,b_id,via,weight) VALUES(?,?,?,?,?,?)",
        [(a[0], a[1], b[0], b[1], 'path', weight) for (a,b), weight in weights.items()])
    by_handover = defaultdict(list)
    for hid, tid in connection.execute("SELECT handover_id,task_id FROM handover_tasks ORDER BY handover_id,task_id"):
        by_handover[hid].append(tid)
    entries = []
    for tids in by_handover.values():
        for i, left in enumerate(tids):
            for right in tids[i+1:]:
                a,b = sorted((left,right))
                entries.append(('task',a,'task',b,'handover',1))
    connection.executemany("INSERT INTO related(a_kind,a_id,b_kind,b_id,via,weight) VALUES(?,?,?,?,?,?)", entries)


def experiments(root, output):
    """Equivalent full rebuild, actual HTTP payload, and transactional index probes."""
    root = root.resolve()
    server, store = bind(root)
    call(server, 'backlog_status')
    instance = store.open_store(root/'.taskmaster')
    db = instance.connection
    report = {}
    def save():
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    task = db.execute("SELECT id FROM entities WHERE kind='task' AND archived=0 AND deleted=0 ORDER BY id LIMIT 1").fetchone()[0]
    # No installation: this process alone uses the experimental replacement.
    original = store.Store._rebuild_related
    report['baseline_handover'] = measure(lambda: handover(server, f'audit-baseline-{time.time_ns()}'), 1)
    store.Store._rebuild_related = staticmethod(optimized_rebuild)
    try:
        report['optimized_handover'] = measure(lambda: handover(server, f'audit-optimized-{time.time_ns()}'), 3)
        before = sorted(tuple(r) for r in db.execute('SELECT * FROM related'))
        db.execute('BEGIN IMMEDIATE')
        try:
            original(db)
            report['full_related_equivalent'] = before == sorted(tuple(r) for r in db.execute('SELECT * FROM related'))
            report['related_rows'] = len(before)
        finally:
            db.rollback()
    finally:
        store.Store._rebuild_related = staticmethod(original)
    save()
    # Index probes roll back their schema changes, preserving the test baseline.
    seq = db.execute('SELECT max(seq) FROM changes').fetchone()[0] - 1
    probes = {
        'updated_seq': ('SELECT kind,id,updated_seq FROM entities WHERE updated_seq>? ORDER BY updated_seq', (seq,), 'CREATE INDEX audit_updated_seq ON entities(updated_seq)'),
        'entity_id': ('SELECT kind FROM entities WHERE id=? AND deleted=0', (task,), 'CREATE INDEX audit_entity_id ON entities(id,deleted,kind)'),
        'task_handovers': ('SELECT handover_id FROM handover_tasks WHERE task_id=?', (task,), 'CREATE INDEX audit_task_handovers ON handover_tasks(task_id,handover_id)'),
    }
    report['indexes'] = {}
    for name, (sql, args, ddl) in probes.items():
        result = {'before':measure(lambda: db.execute(sql,args).fetchall(),20),
                  'plan_before':[tuple(r) for r in db.execute('EXPLAIN QUERY PLAN '+sql,args)]}
        db.execute('BEGIN IMMEDIATE')
        try:
            db.execute(ddl)
            result['after'] = measure(lambda: db.execute(sql,args).fetchall(),20)
            result['plan_after'] = [tuple(r) for r in db.execute('EXPLAIN QUERY PLAN '+sql,args)]
        finally:
            db.rollback()
        report['indexes'][name]=result
    save()
    # Exercise the actual viewer HTTP route without opening a browser.
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    class QuietHandler(server.ViewerHandler):
        def log_message(self, *args):
            pass
    http = ThreadingHTTPServer(('127.0.0.1',0),QuietHandler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        base = f'http://127.0.0.1:{http.server_port}'
        def request(path, headers=None):
            start = time.perf_counter()
            with urllib.request.urlopen(urllib.request.Request(base+path,headers=headers or {}),timeout=30) as response:
                body=response.read()
                return {'seconds':time.perf_counter()-start,'status':response.status,'bytes':len(body),'etag':response.headers.get('ETag')},body
        full, body = request('/api/backlog')
        payload=json.loads(body)
        report['http']={'backlog':full,'conditional_backlog':request('/api/backlog',{'If-None-Match':full['etag']})[0],
                        'task':request('/api/task/'+task)[0],'related':request('/api/task/'+task+'/related')[0]}
        report['payload_bytes_by_key']={k:len(json.dumps(v,default=str).encode()) for k,v in payload.items()}
        report['payload_task_count']=len(payload.get('tasks',[]))
    finally:
        http.shutdown()
        http.server_close()
        thread.join()
    report['db_rows']={name:db.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ('entities','changes','entity_paths','related','links','linear_queue','sessions')}
    report['db_pages']={key:db.execute('PRAGMA '+key).fetchone()[0] for key in ('page_count','page_size','freelist_count')}
    report['json_bytes']=dict(db.execute("SELECT kind,sum(length(doc)+coalesce(length(body),0)) FROM entities GROUP BY kind"))
    report['database_check']=check_database(root/'.taskmaster/local/store.db')
    report['integrity']=report['database_check']['integrity'][0]
    save()
    print('experimental handovers',[(round(r['seconds'],3),r['ok']) for r in report['optimized_handover']],flush=True)
    print('HTTP',report['http'],flush=True)
    print('equivalent',report['full_related_equivalent'],flush=True)
    store.close_thread_connection()


def adoption(source, output):
    output=output.resolve()
    root=snapshot(source,output)
    database=(root/'.taskmaster/local/store.db').resolve()
    assert root.resolve() in database.parents and (root/'.benchmark-copy').is_file()
    # The backup is closed and has no live connections; only this copy is reset.
    assert not Path(str(database)+'-wal').exists()
    database.unlink()
    start=time.perf_counter()
    server,store=bind(root)
    report={'import_and_bind_seconds':time.perf_counter()-start}
    print('fresh adoption starting',flush=True)
    report['first_status']=measure(lambda: call(server,'backlog_status'),1)
    report['warm_status']=measure(lambda: call(server,'backlog_status'))
    report['database_check']=check_database(root/'.taskmaster/local/store.db')
    report['integrity']=report['database_check']['integrity'][0]
    (output/'adoption.json').write_text(json.dumps(report,indent=2))
    print(report,flush=True)
    store.close_thread_connection()


def parallel_worker(root, ready, start, tag, optimized=True):
    server,store=bind(Path(root))
    if optimized:
        store.Store._rebuild_related=staticmethod(optimized_rebuild)
    call(server,'backlog_status')
    Path(ready).touch()
    deadline=time.monotonic()+120
    while not Path(start).exists():
        if time.monotonic()>deadline:
            raise TimeoutError('barrier')
        time.sleep(0.02)
    result=measure(lambda:handover(server,tag),1)[0]
    store.close_thread_connection()
    return result


def parallel(root,output,baseline_writers=0):
    from concurrent.futures import ProcessPoolExecutor
    root=root.resolve()
    server,store=bind(root)
    call(server,'backlog_status')
    barriers=output.parent/(output.stem+'-barriers')
    barriers.mkdir(exist_ok=False)
    start=barriers/'start'
    workers=baseline_writers or 4
    tags=[f'audit-parallel-{time.time_ns()}-{i}' for i in range(workers)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(parallel_worker,str(root),str(barriers/str(i)),str(start),tags[i],not baseline_writers) for i in range(workers)]
        deadline=time.monotonic()+120
        while not all((barriers/str(i)).exists() for i in range(workers)):
            for future in futures:
                if future.done(): future.result()
            if time.monotonic()>deadline: raise TimeoutError('worker startup')
            time.sleep(0.05)
        start.touch()
        report={'reads_during_writes':measure(lambda:call(server,'backlog_status'),5),
                'writers':[f.result(timeout=120) for f in futures]}
    db=store.open_store(root/'.taskmaster').connection
    report['acknowledged_rows']={tag:db.execute("SELECT count(*) FROM entities WHERE kind='handover' AND doc LIKE ?",('%'+tag+'%',)).fetchone()[0] for tag in tags}
    before=sorted(tuple(row) for row in db.execute('SELECT * FROM related'))
    db.execute('BEGIN IMMEDIATE')
    try:
        store.Store._rebuild_related(db)
        report['equivalent_to_full_rebuild']=before==sorted(tuple(row) for row in db.execute('SELECT * FROM related'))
    finally:
        db.rollback()
    report['database_check']=check_database(root/'.taskmaster/local/store.db')
    report['integrity']=report['database_check']['integrity'][0]
    report['optimized']=not bool(baseline_writers)
    store.close_thread_connection()
    fresh=sqlite3.connect(root/'.taskmaster/local/store.db')
    try:
        report['fresh_connection_integrity']=[tuple(row) for row in fresh.execute('PRAGMA integrity_check')]
        report['fresh_connection_foreign_keys']=[tuple(row) for row in fresh.execute('PRAGMA foreign_key_check')]
        report['sqlite_version']=sqlite3.sqlite_version
    finally:
        fresh.close()
    output.write_text(json.dumps(report,indent=2))
    print(report,flush=True)
    store.close_thread_connection()


def suite(source, output):
    output = output.resolve()
    root = snapshot(source, output)
    report = {"copy": str(root), "timings": {}}
    def save():
        (output / "suite.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    start = time.perf_counter()
    server, store = bind(root)
    report["import_and_bind_seconds"] = time.perf_counter()-start
    def bench(name, fn, repeat=3):
        report["timings"][name] = measure(fn, repeat)
        save()
        print(name, [(round(s["seconds"], 4), s["ok"]) for s in report["timings"][name]], flush=True)
    bench("first_status", lambda: call(server, "backlog_status"), 1)
    instance = store.open_store(root / ".taskmaster")
    db = instance.connection
    task = db.execute("SELECT id FROM entities WHERE kind='task' AND archived=0 AND deleted=0 AND status='todo' ORDER BY id LIMIT 1").fetchone()[0]
    report["selected_task"] = task
    names = ["backlog_status", "backlog_list_tasks", "backlog_bug_list", "backlog_issue_list", "backlog_decision_list",
             "backlog_note_list", "backlog_thread_list", "backlog_continuity_items", "backlog_next_available",
             "backlog_index_status", "backlog_store_status", "backlog_phase_status", "backlog_linear_status"]
    for name in names:
        bench(name, lambda name=name: call(server, name))
    for name, kwargs in (("backlog_get_task", {"task_id":task}), ("backlog_dependencies", {"task_id":task}),
                         ("backlog_search", {"query":"asset"}),
                         ("backlog_query", {"sql":"SELECT kind,count(*) AS n FROM entities GROUP BY kind"})):
        bench(name, lambda name=name, kwargs=kwargs: call(server, name, **kwargs))
    bench("viewer_task_data", lambda: server._load_task_full_identified(task))
    bench("viewer_related_data", lambda: server._load_related_for_task(task))
    bench("full_validation", lambda: call(server, "backlog_validate"), 1)
    bench("direct_sql_task", lambda: db.execute("SELECT doc,body FROM entities WHERE kind='task' AND id=?", (task,)).fetchone(), 20)
    bench("direct_fts", lambda: db.execute("SELECT id FROM entity_fts WHERE entity_fts MATCH ? ORDER BY rank LIMIT 20", ('asset',)).fetchall(), 20)
    bench("cached_load_dict", instance.load_dict)
    bench("unchanged_projection_check", instance._projection_changed_on_disk)
    bench("forced_read_scan", lambda: (instance.force_scan_on_next_read(), instance.load_dict()))
    report["query_plans"] = {}
    queries = {
        "incremental_entities": ("SELECT kind,id,epic,deleted,doc,body,updated_seq FROM entities WHERE updated_seq>? ORDER BY updated_seq", (4000,)),
        "kind_for_id": ("SELECT kind FROM entities WHERE id=? AND deleted=0", (task,)),
        "fts_delete": ("SELECT rowid FROM entity_fts WHERE kind=? AND id=?", ('task',task)),
        "handovers_for_task": ("SELECT handover_id FROM handover_tasks WHERE task_id=?", (task,)),
    }
    for name, (sql, args) in queries.items():
        report["query_plans"][name] = [tuple(row) for row in db.execute("EXPLAIN QUERY PLAN " + sql, args)]
    try:
        report["table_bytes"] = dict(db.execute("SELECT name,sum(pgsize) FROM dbstat GROUP BY name ORDER BY sum(pgsize) DESC"))
    except sqlite3.Error as exc:
        report["table_bytes_error"] = str(exc)
    save()
    # Capture the slow components of all writes without changing their behavior.
    spans = defaultdict(list)
    for name in ("_refresh_derived", "_scan_projection", "_export_touched", "_regenerate_progress_if_due", "_apply_dict_diff"):
        original = getattr(store.Store, name)
        def wrap(original=original, name=name):
            @wraps(original)
            def measured(*args, **kwargs):
                start = time.perf_counter()
                try:
                    return original(*args, **kwargs)
                finally:
                    spans[name].append(time.perf_counter()-start)
            return measured
        setattr(store.Store, name, wrap())
    def writebench(name, fn, repeat=1):
        spans.clear()
        bench(name, fn, repeat)
        report.setdefault("write_spans", {})[name] = {k: list(v) for k,v in spans.items()}
        save()
    writebench("task_metadata_update", lambda: call(server, "backlog_update_task", task_id=task, field="next_step", value=f"BENCHMARK {time.time_ns()}"), 3)
    writebench("invalid_task_update", lambda: call(server, "backlog_update_task", task_id=task, field="not-a-field", value="x"))
    writebench("three_field_batch", lambda: call(server, "backlog_batch_update", operations=f"update {task} next_step BENCHMARK batch\nupdate {task} tldr BENCHMARK summary\nupdate {task} estimate S"))
    writebench("note_create", lambda: call(server, "backlog_note_create", text="BENCHMARK isolated note"))
    writebench("bug_create", lambda: call(server, "backlog_bug_create", title="BENCHMARK isolated bug"))
    # Real hook subprocess startup, local copies only; no remote Linear calls.
    env = __import__('os').environ.copy()
    env['TASKMASTER_ROOT'] = str(root)
    def hook(path, args=(), payload=None):
        run = subprocess.run([sys.executable, str(REPO/path), *args], input=json.dumps(payload) if payload else None,
                             text=True, capture_output=True, cwd=root, env=env, timeout=30)
        if run.returncode:
            raise RuntimeError(run.stderr)
        return run.stdout
    bench("merge_gate_subprocess", lambda: hook('hooks/merge_gate_decide.py', ('benchmark-no-branch',str(root))))
    bench("edit_hook_subprocess", lambda: hook('hooks/edit_resurface.py', payload={"cwd":str(root),"session_id":"performance-audit","tool_name":"Edit","tool_input":{"file_path":str(root/'README.md')}}))
    report["database_check"] = check_database(root / ".taskmaster/local/store.db")
    report["integrity"] = report["database_check"]["integrity"][0]
    report["foreign_key_violations"] = len(db.execute("PRAGMA foreign_key_check").fetchall())
    save()
    store.close_thread_connection()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--algorithm-copy', type=Path)
    parser.add_argument('--experiments-copy', type=Path)
    parser.add_argument('--parallel-copy', type=Path)
    parser.add_argument('--baseline-writers', type=int, default=0)
    parser.add_argument('--adopt', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.parallel_copy:
        parallel(args.parallel_copy,args.output.resolve(),args.baseline_writers)
    elif args.experiments_copy:
        experiments(args.experiments_copy,args.output.resolve())
    elif args.adopt and args.source:
        adoption(args.source,args.output)
    elif args.algorithm_copy:
        algorithms(args.algorithm_copy, args.output.resolve())
    elif args.source:
        suite(args.source, args.output)
    else:
        parser.error('Provide --source or --algorithm-copy')
