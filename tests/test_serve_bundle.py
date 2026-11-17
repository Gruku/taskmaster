# User intent: prove the 014 bundle format (team-server-014 Step 3): a byte-exact round trip, every refusal in design §5.3
# before anything is extracted, and extraction that verifies each member's hash and size against the manifest.
from __future__ import annotations

import hashlib
import json
import os
import stat
import zipfile
from pathlib import Path

import pytest

from taskmaster.serve import bundle

HOST_ID = "9f0c3e7a5b1d4e2f8a6c0b9d7e5f3a1c"
COMMIT = "a" * 40
STORE_BYTES = b"SQLite format 3\0" + os.urandom(2000)
FILES = [
    ("projection", "backlog.yaml", b"tasks:\r\n  - id: a\r\n", 111),
    ("projection", "tasks/a.md", b"# a\nlf only\n", 222),
    ("config", "taskmaster.json", b'{"x": 1}\n', 333),
    ("progress", "local/PROGRESS.md", b"progress\n", 444),
]


def base_manifest(**changes):
    manifest = {"created": "2026-11-07T10:00:00+00:00", "source_build": {"version": "x", "digest": "y"},
                "store_id": "store-1", "schema_version": 4, "high_seq": 10, "exported_through": 10,
                "marker": {"url": "https://tm.example.com/mcp", "host_id": HOST_ID},
                "current_branch": "main", "commits": {"main": COMMIT}, "counts": {"task": 1}, "claims": [],
                "team_mode": True, "not_migrated": {}}
    manifest.update(changes)
    return manifest


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "store-copy.db"
    path.write_bytes(STORE_BYTES)
    return path


def make(tmp_path, store, name="good.tmbundle", manifest=None, files=FILES):
    out = tmp_path / name
    bundle.write_bundle(out, manifest or base_manifest(), store, list(files))
    return out


def rewrite(source: Path, target: Path, *, manifest=None, drop=(), add=(), mutate=None):
    """Copy `source` into `target`, changing the manifest and members: for crafting refused bundles."""
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as new:
        for info in old.infolist():
            if info.filename in drop:
                continue
            data = old.read(info)
            if info.filename == bundle.MANIFEST_NAME and manifest is not None:
                data = json.dumps(manifest).encode() if not isinstance(manifest, bytes) else manifest
            if mutate:
                data = mutate(info.filename, data)
            new.writestr(info, data)
        for item in add:
            if isinstance(item, zipfile.ZipInfo):
                new.writestr(item, b"x")
            else:
                new.writestr(*item)
    return target


def manifest_of(path):
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read(bundle.MANIFEST_NAME))


# -- round trip --------------------------------------------------------------------------------------

def test_round_trip_is_byte_exact_including_crlf(tmp_path, store):
    out = make(tmp_path, store)
    with bundle.open_bundle(out) as opened:
        assert opened.manifest["store"]["size"] == len(STORE_BYTES)
        assert [(e["path"], e["role"], e["mtime_ns"]) for e in opened.tree()] == [
            (rel, role, mtime) for role, rel, _, mtime in FILES]
        staging = opened.extract(tmp_path / "staging")
    assert (staging / "store.db").read_bytes() == STORE_BYTES
    for _, rel, data, _ in FILES:
        assert (staging / "tree" / Path(*rel.split("/"))).read_bytes() == data


def test_manifest_is_the_first_member_and_members_are_regular_files(tmp_path, store):
    out = make(tmp_path, store)
    with zipfile.ZipFile(out) as archive:
        infos = archive.infolist()
        assert infos[0].filename == "manifest.json"
        assert all(stat.S_ISREG(info.external_attr >> 16) for info in infos)


def test_the_output_is_exclusive_and_owner_only(tmp_path, store):
    out = make(tmp_path, store)
    with pytest.raises(FileExistsError):
        bundle.write_bundle(out, base_manifest(), store, [])
    if os.name != "nt":
        assert out.stat().st_mode & 0o777 == 0o600


def test_capped_lists_carry_their_totals():
    into = {}
    bundle.capped([f"f{n}" for n in range(450)], "tracked_total", into, "tracked")
    assert len(into["tracked"]) == bundle.LIST_CAP and into["tracked_total"] == 450


# -- §5.3 refusals, nothing extracted ----------------------------------------------------------------

def refused(path, match):
    with pytest.raises(bundle.BundleRefused, match=match):
        bundle.open_bundle(path)


def test_not_a_zip(tmp_path):
    path = tmp_path / "x.tmbundle"
    path.write_bytes(b"this is not a zip")
    refused(path, "not a readable bundle")


def test_manifest_missing_oversized_or_not_an_object(tmp_path, store, monkeypatch):
    good = make(tmp_path, store)
    refused(rewrite(good, tmp_path / "a", drop=("manifest.json",)), "no manifest.json")
    refused(rewrite(good, tmp_path / "b", manifest=b"[1, 2]"), "not a JSON object")
    refused(rewrite(good, tmp_path / "c", manifest=b"{nope"), "not valid JSON")
    monkeypatch.setattr(bundle, "MAX_MANIFEST", 50)
    refused(good, "larger than 50 bytes")


@pytest.mark.parametrize("change,match", [
    ({"format": 2}, "newer build"),
    ({"kind": "other"}, "wrong kind"),
    ({"schema_version": 6}, "newer or unknown"),
    ({"marker": {"url": "ftp://x", "host_id": HOST_ID}}, "marker is not usable"),
    ({"marker": {"url": "https://tm.example.com/mcp", "host_id": "nothex"}}, "marker is not usable"),
    ({"current_branch": "dev"}, "current_branch"),
    ({"commits": {"main": "zz"}}, "40-hex"),
])
def test_manifest_field_refusals(tmp_path, store, change, match):
    good = make(tmp_path, store)
    manifest = manifest_of(good)
    manifest.update(change)
    refused(rewrite(good, tmp_path / "bad", manifest=manifest), match)


def test_extra_or_missing_members(tmp_path, store):
    good = make(tmp_path, store)
    refused(rewrite(good, tmp_path / "extra", add=[("tree/other.txt", b"x")]), "unexpected")
    refused(rewrite(good, tmp_path / "missing", drop=("tree/tasks/a.md",)), "missing")
    refused(rewrite(good, tmp_path / "nostore", drop=("store.db",)), "missing")


def test_directory_symlink_and_duplicate_members(tmp_path, store):
    good = make(tmp_path, store)
    directory = zipfile.ZipInfo("tree/sub/")
    refused(rewrite(good, tmp_path / "dir", add=[directory]), "directory entry")
    link = zipfile.ZipInfo("tree/link")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    refused(rewrite(good, tmp_path / "link", add=[link]), "symlink")
    with pytest.warns(UserWarning):
        refused(rewrite(good, tmp_path / "dup", add=[("manifest.json", b"{}")]), "duplicate")


@pytest.mark.parametrize("bad", ["../x", "/x", "a\\b", "C:x", "a/../b"])
def test_unsafe_paths(tmp_path, store, bad):
    good = make(tmp_path, store)
    manifest = manifest_of(good)
    manifest["tree"][0]["path"] = bad
    refused(rewrite(good, tmp_path / "bad", manifest=manifest), "unsafe path")


def test_role_rules(tmp_path, store):
    good = make(tmp_path, store)
    manifest = manifest_of(good)
    manifest["tree"][0]["path"] = "local/x"
    refused(rewrite(good, tmp_path / "local", manifest=manifest), "under local/")
    manifest = manifest_of(good)
    manifest["tree"][2]["path"] = "secrets.json"
    refused(rewrite(good, tmp_path / "config", manifest=manifest), "not a known config file")
    manifest = manifest_of(good)
    manifest["tree"][3]["path"] = "local/other.md"
    refused(rewrite(good, tmp_path / "progress", manifest=manifest), "not the progress file")


def test_size_mismatch_between_member_and_manifest(tmp_path, store):
    good = make(tmp_path, store)
    manifest = manifest_of(good)
    manifest["tree"][1]["size"] += 1
    refused(rewrite(good, tmp_path / "size", manifest=manifest), "the manifest says")


def test_too_many_members(tmp_path, store, monkeypatch):
    good = make(tmp_path, store)
    monkeypatch.setattr(bundle, "MAX_MEMBERS", 3)
    refused(good, "more than 3 members")


def test_a_refused_bundle_extracts_nothing(tmp_path, store):
    good = make(tmp_path, store)
    manifest = manifest_of(good)
    manifest["schema_version"] = 6
    bad = rewrite(good, tmp_path / "bad", manifest=manifest)
    refused(bad, "newer or unknown")
    assert not (tmp_path / "staging").exists()


# -- extraction verifies --------------------------------------------------------------------------------

def test_extract_with_a_wrong_hash_refuses_and_removes_staging(tmp_path, store):
    good = make(tmp_path, store)

    def flip(name, data):
        return data.replace(b"lf only", b"LF only") if name == "tree/tasks/a.md" else data

    bad = rewrite(good, tmp_path / "bad", mutate=flip)
    with bundle.open_bundle(bad) as opened:
        with pytest.raises(bundle.BundleRefused, match="hash"):
            opened.extract(tmp_path / "staging")
    assert not (tmp_path / "staging").exists()


def test_a_member_larger_than_declared_is_cut_at_the_cap(tmp_path, store, monkeypatch):
    good = make(tmp_path, store)
    read = []
    real_open = zipfile.ZipFile.open

    def spying(self, *args, **kwargs):
        stream = real_open(self, *args, **kwargs)
        original = stream.read

        def counting(size=-1):
            data = original(size)
            read.append(len(data))
            return data
        stream.read = counting
        return stream

    with bundle.open_bundle(good) as opened:
        opened.caps["store.db"] = 100            # as if the member were bigger than its manifest entry says
        monkeypatch.setattr(zipfile.ZipFile, "open", spying)
        with pytest.raises(bundle.BundleRefused, match="larger than the manifest"):
            opened.extract(tmp_path / "staging")
    assert sum(read) <= 101
    assert not (tmp_path / "staging").exists()


def test_extract_refuses_an_existing_staging_directory(tmp_path, store):
    good = make(tmp_path, store)
    (tmp_path / "staging").mkdir()
    with bundle.open_bundle(good) as opened:
        with pytest.raises(FileExistsError):
            opened.extract(tmp_path / "staging")
    assert (tmp_path / "staging").exists()
