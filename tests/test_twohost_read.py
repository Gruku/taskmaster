# User intent: a member's hooks and mods read the team server through their own key, cache and clone: tm_read answers with the member's view, the cache lands owner-only in the member's own cache dir, a stopped server is answered stale with an age, a revoked key is refused on a fresh read (a plain read inside the cache window is still answered from the member's own cache; finding revoked-key-plain-read), and the read endpoint keeps the host's private tables from the member (team-server-015 S9, Step 6; P-008 is already closed by team-server-008).
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from twohost.fixtures import team
from twohost.member import HOOKS, make_clone

pytestmark = [pytest.mark.twohost, pytest.mark.slow, pytest.mark.real_service_process,
              pytest.mark.xdist_group("heavy_processes"), pytest.mark.timeout(240)]

CALLS = [{"tool": "backlog_list_tasks", "args": {"limit": 3}}]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    try:
        with team(tmp_path_factory.mktemp("s9") / "run", patch) as the_team:
            for member in (the_team.ann, the_team.bob):
                make_clone(member, the_team.host.url)
                assert member.set_key().returncode == 0
            yield the_team
    finally:
        patch.undo()


def tm_read(member, calls=CALLS, *extra) -> dict:
    done = subprocess.run([sys.executable, str(HOOKS / "tm_read.py"), "tools", "--root", str(member.machine.root), *extra],
                          input=json.dumps({"calls": calls}), text=True, capture_output=True, env=member.machine.env,
                          cwd=str(member.machine.root), timeout=60)
    assert done.returncode == 0, (done.stdout, done.stderr)
    return json.loads(done.stdout)


def tm_mode(member) -> dict:
    done = subprocess.run([sys.executable, str(HOOKS / "tm_read.py"), "mode", "--root", str(member.machine.root)],
                          text=True, capture_output=True, env=member.machine.env, cwd=str(member.machine.root), timeout=60)
    return json.loads(done.stdout)


def test_tm_read_answers_a_members_view_from_the_server_and_names_the_mode(world):
    assert tm_mode(world.ann)["mode"] == "server"
    answer = tm_read(world.ann)
    assert answer["source"] == "server" and answer["data"]["results"][0]["tool"] == "backlog_list_tasks", answer
    assert "Build the thing" in answer["data"]["results"][0]["text"]
    world.evidence.record("read", "tm_read:backlog_list_tasks", answer["data"]["results"][0]["text"])


def test_the_cache_lands_in_the_members_own_cache_dir_owner_only(world):
    tm_read(world.ann)
    cache = Path(world.ann.machine.env["TASKMASTER_CACHE_DIR"])
    files = [p for p in cache.rglob("*") if p.is_file()]
    assert files, f"no cache file under {cache}"
    if os.name != "nt":
        for path in files:
            assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0, path
    assert world.ann.key not in "".join(p.read_text(errors="replace") for p in files)     # the cache never holds the key


def test_the_member_cannot_read_the_hosts_private_tables_through_the_read_endpoint(world):
    for table in ("sessions", "linear_queue", "projection"):
        answer = tm_read(world.ann, [{"tool": "backlog_query", "args": {"sql": f"SELECT count(*) FROM {table}"}}], "--fresh")
        assert "not authorized" in json.dumps(answer), (table, answer)


def test_a_revoked_key_is_refused_fresh_and_a_plain_read_is_served_from_the_members_cache(world):
    tm_read(world.bob)                                            # warm bob's cache
    revoked = world.operator.cli("serve", "revoke-user", "bob", "--root", str(world.root))
    assert revoked.returncode == 0, revoked.stderr
    answer = tm_read(world.bob, CALLS, "--fresh")
    assert answer["error"]["kind"] == "refused", answer
    again = tm_read(world.bob)
    # FINDING (revoked-key-plain-read, observed 2026-11-11): a plain (non-fresh) read inside the view's freshness window
    # is answered from the member's own cache with no server contact, so revocation does not reach it. It is the
    # member's own previously-read data on their own disk, bounded by the window; only --fresh (and the next stale
    # fallback) meets the refusal. Pinned as the actual behaviour, not as a design requirement.
    assert again["source"] == "cache" and "error" not in again, again
    assert again["data"]["results"][0]["text"], again
    world.evidence.record("read", "revoked-key-plain-read", json.dumps(again)[:600])


def test_with_the_server_down_the_answer_is_stale_with_an_age(world):
    fresh = tm_read(world.ann, CALLS, "--fresh")
    assert fresh["source"] == "server"
    world.host.stop()
    stale = tm_read(world.ann, CALLS, "--fresh")      # --fresh skips the fresh-cache hit, so only the outage path can answer
    assert stale["source"] == "stale" and stale["age_s"] >= 0, stale
    assert stale["data"]["results"][0]["text"] == fresh["data"]["results"][0]["text"]
