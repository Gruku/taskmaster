# User intent: regressions for the N13 CodeMaestro copy-only rehearsal import defects
# (D2 conflict-marker false positive, D3 accepted-but-dropped document fields, D4
# trailing-newline base asymmetry) -- sync must never claim an edit it did not apply.
from contextlib import closing
import base64
import hashlib
import json
import sqlite3

import pytest
import yaml

from taskmaster import backlog_server as bs, projection_parse as parse, store
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.protocol import connect
from taskmaster.coordinator.service import Coordinator
from taskmaster.coordinator.sync_prepare import prepare
from taskmaster.native.queries import Repository
from native_twins import commit_only, make_twins, native_connection

# Anonymised shape of CodeMaestro B-339: a bug body quoting pytest's summary rule.
B339 = """---
id: B-339
title: 'host: `pytest tests/` is un-runnable'
status: open
severity: P2
components:
- host
- tests
discovered: '2026-07-29T17:54:51Z'
discovered_by: claude
fix_commit: null
links: []
---
## Symptom

`pytest tests/` exits 2 with **zero tests executed**.

```
from host.patches import PATCHES_57, apply_57_patches
E   ImportError: cannot import name 'PATCHES_57' from 'host.patches'
=========================== short test summary info ===========================
ERROR tests/test_patches.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.29s
```

## Notes

- A setext heading underline is also not a conflict marker
=======
- nor is a quoted `<<<<<<< HEAD` mid-line.
"""


# --- D2: conflict markers ---------------------------------------------------

def test_d2_body_quoting_pytest_rule_parses():
    doc, body = parse.entity_text("bug", B339)
    assert doc["id"] == "B-339" and "short test summary info" in body


@pytest.mark.parametrize("block", [
    "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> feature/x\n",
    "<<<<<<< HEAD\r\nours\r\n=======\r\ntheirs\r\n>>>>>>> 1a2b3c\r\n",
    "<<<<<<< ours\nmine\n||||||| base\norig\n=======\nyours\n>>>>>>> theirs\n",
    "<<<<<<<\nours\n=======\ntheirs\n>>>>>>>\n",
])
def test_d2_real_git_conflict_blocks_are_still_rejected(block):
    with pytest.raises(ValueError, match="conflict markers"):
        parse.entity_text("bug", "---\nid: B-1\ntitle: t\n---\nintro\n" + block)


def test_d2_conflict_in_frontmatter_is_rejected():
    raw = "---\nid: B-1\n<<<<<<< HEAD\ntitle: a\n=======\ntitle: b\n>>>>>>> other\n---\nbody\n"
    with pytest.raises(ValueError):
        parse.entity_text("bug", raw)


BUG_REL = "bugs/B-339.md"


@pytest.fixture
def bug_root(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, lambda: bs.backlog_bug_create(title="Seed bug"),
                       visibility=None)
    with twins.at(twins.native):
        yield twins.native


def _bug_rel(root):
    [path] = (root / ".taskmaster" / "bugs").glob("B-*.md")
    return path.relative_to(root / ".taskmaster").as_posix()


def test_d2_previously_quarantined_bytes_become_eligible_once_they_parse(bug_root):
    """The live store quarantined B-339 under the substring rule. The unchanged bytes
    must be re-parsed on the next sync, not skipped as 'unchanged quarantined bytes'."""
    rel = _bug_rel(bug_root)
    ident = rel.split("/")[-1][:-3]
    path = bug_root / ".taskmaster" / rel
    content = B339.replace("B-339", ident).encode("utf-8")
    path.write_bytes(content)
    with native_connection(bug_root) as connection:
        with Repository(connection).snapshot() as snapshot:
            token = prepare(snapshot, bug_root / ".taskmaster", rel).arguments["expected_manifest"]
        # Record the quarantine exactly as the old substring rule did.
        commit_only(connection, "sync.apply", {
            "file": rel, "mode": "quarantine", "rows": [], "reason": "invalid authored projection: git conflict markers",
            "observed_base64": base64.b64encode(content).decode("ascii"),
            "observed_hash": hashlib.sha1(content).hexdigest(), "expected_manifest": token})
        assert connection.execute("SELECT quarantined FROM projection WHERE file=?", (rel,)).fetchone()[0] == 1
    with Coordinator(bug_root):
        client = Client(bug_root, autostart=False)
        result = client.sync(files=[rel])
        assert result["imports"] and result["imports"][0]["state"] in {"accepted", "conflict"}, result
    with native_connection(bug_root) as connection:
        assert connection.execute("SELECT quarantined FROM projection WHERE file=?", (rel,)).fetchone()[0] == 0
