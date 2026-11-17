# User intent: the read cache serves fresh and stale replies only as each view's policy allows, never caches the
# merge gate, never rolls back, stays bounded, fails fast after an outage or a confirmed refusal (two strikes), and
# keeps no secret on disk (team-server-007 D11-D13).
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from taskmaster.remote import cache as cache_mod
from taskmaster.remote.cache import CACHE_POLICY, Cache

KEY = "tm_u_0123456789abcdef_" + "s" * 43


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def env(data, revision=5, store_id="store-a"):
    return {"v": 1, "view": "paths", "store_id": store_id, "revision": revision, "generated_at": "x", "data": data}


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def cache(tmp_path, clock):
    return Cache("srv0123456789abc", "0123456789abcdef", base=tmp_path / "c", clock=clock)


P = {"path": "a.py"}


def test_fresh_then_stale_then_gone(cache, clock):
    cache.put("paths", P, env({"n": 1}))
    assert cache.fresh("paths", P).data == {"n": 1}
    clock.now += CACHE_POLICY["paths"].fresh + 1
    assert cache.fresh("paths", P) is None
    assert cache.stale("paths", P, "unreachable").data == {"n": 1}
    clock.now += CACHE_POLICY["paths"].stale
    assert cache.stale("paths", P, "unreachable") is None


@pytest.mark.parametrize("kind", ["config", "no-key", "refused", "version", "bad-request"])
def test_some_failures_never_serve_stale(cache, clock, kind):
    cache.put("paths", P, env({"n": 1}))
    clock.now += 31
    assert cache.stale("paths", P, kind) is None


def test_merge_gate_is_never_written_or_read(cache):
    cache.put("merge-gate", {"branch": "b"}, env({"policy_required": True}))
    assert cache.get("merge-gate", {"branch": "b"}) is None
    assert not list(Path(cache.key_dir).rglob("*.json"))


def test_meta_never_serves_stale(cache, clock):
    cache.put("meta", {}, env({"supported": [1]}))
    clock.now += 301
    assert cache.stale("meta", {}, "unreachable") is None


def test_a_negative_age_counts_as_expired(cache, clock):
    cache.put("paths", P, env({"n": 1}))
    clock.now -= 100
    assert cache.fresh("paths", P) is None
    assert cache.stale("paths", P, "unreachable") is None


def test_a_corrupt_entry_is_a_miss_and_deleted(cache):
    cache.put("paths", P, env({"n": 1}))
    path = cache._path("paths", P)
    path.write_text("{not json", encoding="utf-8")
    assert cache.get("paths", P) is None
    assert not path.exists()


def test_an_older_revision_never_replaces_a_newer_one(cache):
    cache.put("paths", P, env({"n": 2}, revision=9))
    cache.put("paths", P, env({"n": 1}, revision=7))
    assert cache.get("paths", P).data == {"n": 2}
    cache.put("paths", P, env({"n": 3}, revision=9))
    assert cache.get("paths", P).data == {"n": 3}


def test_a_lost_race_is_retried_once(cache, monkeypatch):
    real = cache_mod._write_json
    calls = []

    def racing(path, value):
        ok = real(path, value)
        if path.name.endswith(".json") and "v1" in path.parts and not calls:
            calls.append(1)
            real(path, dict(value, revision=value["revision"] - 1))   # an older writer lands after us
        return ok
    monkeypatch.setattr(cache_mod, "_write_json", racing)
    cache.put("paths", P, env({"n": 1}, revision=5))
    assert cache.get("paths", P).revision == 5


def test_a_new_store_id_empties_the_cache(cache):
    cache.put("paths", P, env({"n": 1}, store_id="store-a"))
    cache.put("paths", {"path": "b.py"}, env({"n": 2}, revision=1, store_id="store-b"))
    assert cache.get("paths", P) is None
    assert cache.get("paths", {"path": "b.py"}).data == {"n": 2}


def test_pruning_by_age_and_count(cache, clock, monkeypatch):
    monkeypatch.setattr(cache_mod, "MAX_FILES_PER_VIEW", 3)
    for i in range(5):
        cache.put("paths", {"path": f"{i}.py"}, env({"n": i}))
        path = cache._path("paths", {"path": f"{i}.py"})
        os.utime(path, (clock.now + i, clock.now + i))
    cache.put("paths", {"path": "last.py"}, env({"n": 9}))
    os.utime(cache._path("paths", {"path": "last.py"}), (clock.now + 10, clock.now + 10))
    cache._prune(cache._path("paths", P).parent)
    assert len(list(cache._path("paths", P).parent.glob("*.json"))) == 3
    old = cache._path("paths", {"path": "last.py"})
    os.utime(old, (clock.now - cache_mod.MAX_AGE - 10,) * 2)
    cache._prune(old.parent)
    assert not old.exists()


def test_pruning_by_bytes(cache, monkeypatch):
    monkeypatch.setattr(cache_mod, "MAX_SERVER_BYTES", 600)
    for i in range(6):
        cache.put("paths", {"path": f"{i}.py"}, env({"blob": "x" * 150}))
    total = sum(p.stat().st_size for p in Path(cache.server_dir).glob("*/v1/*/*.json"))
    assert total <= 600


def test_down_entry(cache, clock):
    assert cache.down_since() is None
    cache.mark_down()
    assert cache.down_since() == clock.now
    clock.now += cache_mod.DOWN_SECONDS + 1
    assert cache.down_since() is None


def test_refusal_needs_two_strikes_in_the_window(cache, clock):
    assert cache.note_refused("file:1:2") is False
    clock.now += 2
    assert cache.note_refused("file:1:2") is False      # too soon: one blip, not two
    assert not cache.refused("file:1:2")
    clock.now += 4                                        # 6 s after the first
    assert cache.note_refused("file:1:2") is True
    assert cache.refused("file:1:2")
    clock.now += cache_mod.REFUSED_SECONDS + 1
    assert not cache.refused("file:1:2")


def test_strikes_too_far_apart_restart_the_count(cache, clock):
    cache.note_refused("k")
    clock.now += cache_mod.REFUSED_MAX_GAP + 5
    assert cache.note_refused("k") is False
    clock.now += 6
    assert cache.note_refused("k") is True


def test_a_changed_key_clears_the_refusal(cache, clock):
    cache.note_refused("file:1:2")
    clock.now += 6
    cache.note_refused("file:1:2")
    assert not cache.refused("file:9:9")


def test_a_confirmed_refusal_drops_the_keys_entries(cache, clock):
    cache.put("paths", P, env({"n": 1}))
    cache.note_refused("k")
    clock.now += 6
    cache.note_refused("k")
    assert cache.get("paths", P) is None
    assert cache.refused("k")


def test_success_clears_negative_entries(cache, clock):
    cache.mark_down()
    cache.note_refused("k")
    clock.now += 6
    cache.note_refused("k")
    cache.note_success()
    assert cache.down_since() is None and not cache.refused("k")


def test_merge_gate_policy_ignores_negative_entries():
    assert CACHE_POLICY["merge-gate"].negative is False
    assert all(p.negative for v, p in CACHE_POLICY.items() if v != "merge-gate")


def test_a_link_in_the_path_disables_the_cache(tmp_path, clock):
    base = tmp_path / "c"
    base.mkdir()
    target = tmp_path / "elsewhere"
    target.mkdir()
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(base / "remote"))
    else:
        (base / "remote").symlink_to(target, target_is_directory=True)
    logged = []
    c = Cache("srv", "0123456789abcdef", base=base, clock=clock, log=logged.append)
    assert not c.enabled and logged
    c.put("paths", P, env({"n": 1}))
    assert c.get("paths", P) is None and c.down_since() is None and not c.note_refused("k")
    assert not list(target.rglob("*"))


def test_no_secret_is_ever_written(cache, clock, tmp_path):
    cache.put("paths", P, env({"n": 1}))
    cache.put("tools", {"calls": [{"tool": "backlog_get_task", "args": {"task_id": "x"}}]}, env({"results": []}))
    cache.mark_down()
    cache.note_refused("env:" + "f" * 16)
    clock.now += 6
    cache.note_refused("env:" + "f" * 16)
    cache.hooks_dir()
    for path in (tmp_path / "c").rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert KEY not in text and KEY.split("_")[-1] not in text, path
