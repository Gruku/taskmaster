# User intent: the one-file bundle that carries a solo store to the team server (team-server-014): written by `pack`
# on the laptop, validated member by member before anything is extracted on the host, so a damaged, forged or
# oversized bundle can never put a wrong store or a file outside the project in place.
"""The 014 bundle: a zip holding `manifest.json`, `store.db` and `tree/<path>` members (design §5).

`write_bundle` is used by `pack` (and validates its own output with `open_bundle`); `open_bundle` and
`Bundle.extract` are used by `init --from`. Nothing here touches a project root.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import zipfile
from pathlib import Path

from taskmaster import server_marker
from taskmaster.native import projection
from taskmaster.native.schema import SUPPORTED

FORMAT = 1
KIND = "taskmaster-server-bundle"
MANIFEST_NAME = "manifest.json"
STORE_NAME = "store.db"
TREE_PREFIX = "tree/"
MAX_MANIFEST = 16 << 20
MAX_MEMBERS = 200_000
LIST_CAP = 200
CONFIG_FILES = frozenset({"taskmaster.json", "linear.yaml"})
PROGRESS_PATH = "local/PROGRESS.md"
ROLES = ("projection", "config", "progress")
CHUNK = 1 << 20
_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


class BundleRefused(Exception):
    """The bundle can't be written or isn't acceptable; nothing was extracted."""


def capped(items: list, total_key: str, into: dict, name: str) -> None:
    """`into[name]` = the first LIST_CAP of `items`, and `into[total_key]` = how many there were (design §5.2)."""
    into[name] = list(items[:LIST_CAP])
    into[total_key] = len(items)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_member(rel: str) -> str:
    return TREE_PREFIX + rel


# -- writing -----------------------------------------------------------------------------------------

def write_bundle(out: Path, manifest: dict, store_file: Path, files: list[tuple[str, str, bytes, int]]) -> dict:
    """Write the bundle at `out` (exclusive create, owner-only). `files` is `(role, rel, data, mtime_ns)`.

    Fills `manifest["store"]` and `manifest["tree"]` from the bytes actually written, then returns
    `{"sha256", "size"}` of the finished file. The caller validates it with `open_bundle`.
    """
    store_file = Path(store_file)
    manifest = dict(manifest)
    manifest["format"], manifest["kind"] = FORMAT, KIND
    manifest["store"] = {"sha256": _sha256_file(store_file), "size": store_file.stat().st_size}
    manifest["tree"] = [{"path": rel, "role": role, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                         "mtime_ns": int(mtime_ns)} for role, rel, data, mtime_ns in files]
    raw = (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode("utf-8")
    if len(raw) > MAX_MANIFEST:
        raise BundleRefused(f"the manifest is larger than {MAX_MANIFEST} bytes; too many files to bundle")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(out, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                _put(archive, MANIFEST_NAME, raw)
                with open(store_file, "rb") as source, _member(archive, STORE_NAME) as target:
                    shutil.copyfileobj(source, target, CHUNK)
                for _, rel, data, _ in files:
                    _put(archive, tree_member(rel), data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            os.unlink(out)
        except OSError:
            pass
        raise
    return {"sha256": _sha256_file(out), "size": Path(out).stat().st_size}


def _info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    return info


def _put(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    archive.writestr(_info(name), data)


def _member(archive: zipfile.ZipFile, name: str):
    return archive.open(_info(name), "w", force_zip64=True)


# -- reading -----------------------------------------------------------------------------------------

def _refuse(text: str):
    raise BundleRefused(text)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF)


def _check_manifest(manifest) -> dict:
    if not isinstance(manifest, dict):
        _refuse("manifest.json is not a JSON object")
    version = manifest.get("format")
    if type(version) is not int:
        _refuse("manifest format must be an integer")
    if version > FORMAT:
        _refuse(f"the bundle is format {version}, made by a newer build: update the plugin on the host")
    if version != FORMAT:
        _refuse(f"manifest format {version} is not known")
    if manifest.get("kind") != KIND:
        _refuse("this is not a taskmaster server bundle (wrong kind)")
    schema = manifest.get("schema_version")
    if type(schema) is not int or schema not in SUPPORTED:
        _refuse(f"the store is schema {schema!r}, newer or unknown to this build (supported: "
                f"{sorted(SUPPORTED)}); update the plugin")
    marker = manifest.get("marker")
    if not isinstance(marker, dict) or not isinstance(marker.get("url"), str) or not isinstance(marker.get("host_id"), str):
        _refuse("manifest marker must hold url and host_id")
    try:
        server_marker.parse(server_marker.render(server_marker.Marker(marker["url"], marker["host_id"])).encode("utf-8"))
    except server_marker.ServerConfigError as exc:
        _refuse(f"manifest marker is not usable: {exc.reason}")
    commits = manifest.get("commits")
    if not isinstance(commits, dict) or not commits or not all(
            isinstance(k, str) and isinstance(v, str) and _HEX40.fullmatch(v) for k, v in commits.items()):
        _refuse("manifest commits must map branch names to 40-hex commit ids")
    if manifest.get("current_branch") not in commits:
        _refuse("manifest current_branch is not one of its commits")
    for key in ("high_seq", "exported_through"):
        if type(manifest.get(key)) is not int or manifest[key] < 0:
            _refuse(f"manifest {key} must be a non-negative integer")
    if not isinstance(manifest.get("store_id"), str) or not manifest["store_id"]:
        _refuse("manifest store_id is missing")
    store = manifest.get("store")
    if (not isinstance(store, dict) or not isinstance(store.get("sha256"), str)
            or not _HEX64.fullmatch(store["sha256"]) or type(store.get("size")) is not int or store["size"] < 0):
        _refuse("manifest store must hold sha256 and size")
    tree = manifest.get("tree")
    if not isinstance(tree, list):
        _refuse("manifest tree must be a list")
    seen = set()
    for entry in tree:
        if not isinstance(entry, dict):
            _refuse("manifest tree entries must be objects")
        rel, role = entry.get("path"), entry.get("role")
        try:
            projection.safe_relative(rel)
        except ValueError:
            _refuse(f"unsafe path in the bundle: {rel!r}")
        if role not in ROLES:
            _refuse(f"unknown role {role!r} for {rel}")
        if role == "projection" and (rel == "local" or rel.startswith("local/")):
            _refuse(f"a projection path may not be under local/: {rel}")
        if role == "config" and rel not in CONFIG_FILES:
            _refuse(f"{rel} is not a known config file")
        if role == "progress" and rel != PROGRESS_PATH:
            _refuse(f"{rel} is not the progress file")
        if (not isinstance(entry.get("sha256"), str) or not _HEX64.fullmatch(entry["sha256"])
                or type(entry.get("size")) is not int or entry["size"] < 0 or type(entry.get("mtime_ns")) is not int):
            _refuse(f"manifest entry for {rel} must hold sha256, size and mtime_ns")
        if rel in seen:
            _refuse(f"duplicate path in the manifest: {rel}")
        seen.add(rel)
    return manifest


def open_bundle(path) -> "Bundle":
    """Validate the bundle at `path` without extracting anything; BundleRefused otherwise."""
    path = Path(path)
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise BundleRefused(f"{path} is not a readable bundle ({exc.__class__.__name__}: not a zip file)") from None
    try:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS:
            _refuse(f"the bundle has more than {MAX_MEMBERS} members")
        names = [info.filename for info in infos]
        if len(set(names)) != len(names):
            _refuse("the bundle has a duplicate member")
        for info in infos:
            if info.is_dir() or info.filename.endswith("/"):
                _refuse(f"directory entry in the bundle: {info.filename}")
            if _is_symlink(info):
                _refuse(f"symlink in the bundle: {info.filename}")
        by_name = {info.filename: info for info in infos}
        head = by_name.get(MANIFEST_NAME)
        if head is None:
            _refuse("the bundle has no manifest.json")
        if head.file_size > MAX_MANIFEST:
            _refuse(f"manifest.json is larger than {MAX_MANIFEST} bytes")
        with archive.open(head) as stream:
            raw = stream.read(MAX_MANIFEST + 1)
        if len(raw) > MAX_MANIFEST:
            _refuse(f"manifest.json is larger than {MAX_MANIFEST} bytes")
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            _refuse("manifest.json is not valid JSON")
        _check_manifest(manifest)
        expected = {MANIFEST_NAME, STORE_NAME} | {tree_member(entry["path"]) for entry in manifest["tree"]}
        if set(by_name) != expected:
            extra, missing = sorted(set(by_name) - expected), sorted(expected - set(by_name))
            _refuse("the bundle's members don't match its manifest"
                    + (f" (unexpected: {extra[:3]})" if extra else "") + (f" (missing: {missing[:3]})" if missing else ""))
        caps = {STORE_NAME: manifest["store"]["size"],
                **{tree_member(entry["path"]): entry["size"] for entry in manifest["tree"]}}
        for name, size in caps.items():
            if by_name[name].file_size != size:
                _refuse(f"member {name} is {by_name[name].file_size} bytes, the manifest says {size}")
    except BaseException:
        archive.close()
        raise
    return Bundle(path, archive, manifest, caps)


class Bundle:
    """A validated bundle. `extract` writes its members under an empty staging directory, verifying each hash."""

    def __init__(self, path: Path, archive: zipfile.ZipFile, manifest: dict, caps: dict):
        self.path, self.archive, self.manifest, self.caps = path, archive, manifest, caps

    def close(self) -> None:
        self.archive.close()

    def __enter__(self) -> "Bundle":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def tree(self) -> list[dict]:
        return list(self.manifest["tree"])

    def _copy(self, name: str, target: Path, sha256: str) -> None:
        cap = self.caps[name]
        digest, total = hashlib.sha256(), 0
        target.parent.mkdir(parents=True, exist_ok=True)
        owner_only = lambda path, flags: os.open(path, flags, 0o600)        # noqa: E731 -- never the umask's 0644
        with self.archive.open(name) as source, open(target, "xb", opener=owner_only) as sink:
            while True:
                block = source.read(min(CHUNK, cap + 1 - total))
                if not block:
                    break
                total += len(block)
                if total > cap:
                    _refuse(f"member {name} is larger than the manifest says ({cap} bytes)")
                digest.update(block)
                sink.write(block)
            sink.flush()
            os.fsync(sink.fileno())
        if total != cap or digest.hexdigest() != sha256:
            _refuse(f"member {name} does not match the manifest's hash")

    def extract(self, staging: Path) -> Path:
        """Create `staging` (it must not exist) and write `store.db` and `tree/…` into it. A mismatch removes it."""
        staging = Path(staging)
        staging.mkdir(parents=True, mode=0o700)
        try:
            self._copy(STORE_NAME, staging / STORE_NAME, self.manifest["store"]["sha256"])
            for entry in self.manifest["tree"]:
                self._copy(tree_member(entry["path"]), staging / "tree" / Path(*entry["path"].split("/")),
                           entry["sha256"])
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return staging
