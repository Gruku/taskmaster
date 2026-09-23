"""Read-only projection discovery and bounded observation for explicit sync.

Never walks local state or arbitrary globs supplied by a caller. Links/junctions
are refused. Rechecking closes ordinary editor races; this is not a security
sandbox against a hostile process continuously replacing ancestor directories.

`Scan` is the per-sync fast path (N13 D1): directory safety once per directory, one
lstat per file, and digests reused only under an unchanged file fingerprint.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import time
from typing import NamedTuple

from taskmaster.projection_parse import ENTITY_FILE_SPECS, classify
from taskmaster.projection_paths import UnsafePath, check_component as _check_component, relative, safe_path

MAX_FILE_BYTES = 1024 * 1024


class ChangedDuringRead(ValueError):
    pass


@dataclass
class Inventory:
    files: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    duplicates: dict[str, str] = field(default_factory=dict)
    refused: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    file: str
    content: bytes
    digest: str
    mtime_ns: int
    size: int
    device: int
    inode: int
    # The lstat fingerprint taken after the read, under which its digests may be reused.
    fingerprint: tuple = ()


def _visible(name):
    return not (name.startswith(".") or ".tmp." in name or ".corrupt-" in name)


def discover(root: Path, scan: "Scan | None" = None) -> Inventory:
    scan = Scan(root) if scan is None else scan
    result = Inventory()
    seen = {}

    def listing(rel, *, directories_only=False):
        try:
            directory = scan.directory(rel)
            with os.scandir(directory) as entries:
                return sorted((entry.name for entry in entries
                               if not directories_only or entry.is_dir(follow_symlinks=False)
                               or entry.is_symlink()
                               or getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400),
                              key=os.path.normcase)
        except FileNotFoundError:
            return []
        except (OSError, UnsafePath) as exc:
            result.refused[rel or "."] = str(exc)
            return []

    def consider(rel, kind, ident):
        try:
            if scan.info(rel) is None:
                return
        except (OSError, UnsafePath) as exc:
            result.refused[rel] = str(exc)
            return
        key = (kind, ident)
        if key in seen:
            result.duplicates[rel] = seen[key]
        else:
            result.files[rel] = key
            seen[key] = rel

    for rel in ("backlog.yaml", "project.yaml"):
        consider(rel, rel.removesuffix(".yaml"), None)
    for kind, patterns in ENTITY_FILE_SPECS:
        for pattern in patterns:
            parent, _, name_pattern = pattern.rpartition("/")
            if "*" in parent:
                prefix, _, suffix = parent.partition("*")
                directories = [prefix + name + suffix for name in listing(prefix.rstrip("/"), directories_only=True)]
            else:
                directories = [parent]
            matches = [f"{directory}/{name}" for directory in directories for name in listing(directory)
                       if _visible(name) and fnmatch.fnmatch(name, name_pattern)]
            for rel in sorted(matches, key=os.path.normcase):
                consider(rel, kind, PurePosixPath(rel).stem)
    return result


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def observe(root: Path, rel: str, *, limit=MAX_FILE_BYTES) -> Observation | None:
    classify(rel)
    return _read_observed(root, rel, limit)


def _read_observed(root: Path, rel: str, limit: int) -> Observation | None:
    path = safe_path(root, rel)
    try:
        before = _check_component(path)
        if not stat.S_ISREG(before.st_mode):
            raise UnsafePath(f"projection is not a regular file: {rel}")
        if before.st_size > limit:
            raise ValueError(f"projection exceeds byte limit ({limit}): {rel}")
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            # read(n) preallocates n bytes: ask for the observed size (+1 to see growth,
            # which the signature check below then refuses), never the whole limit.
            content = handle.read(min(limit, opened.st_size) + 1)
            after = os.fstat(handle.fileno())
        if len(content) > limit:
            raise ValueError(f"projection exceeds byte limit ({limit}): {rel}")
        safe_path(root, rel)
        current = _check_component(path)
    except FileNotFoundError:
        # An absent path is distinguishable from a parser failure; callers still
        # revalidate absence before treating it as a publication repair.
        return None
    if not (_signature(before) == _signature(opened) == _signature(after) == _signature(current)):
        raise ChangedDuringRead(f"projection changed during read: {rel}")
    return Observation(rel, content, hashlib.sha1(content).hexdigest(), after.st_mtime_ns,
                       after.st_size, after.st_dev, after.st_ino, _fingerprint(current))


def _fingerprint(info) -> tuple:
    """File identity plus size and both timestamps (ns). A different file at the path
    (replaced, restored by Git, copied with preserved times) has another identity."""
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


# A fingerprint is recorded only for a file whose timestamps are older than this at
# read time, so an edit in the same timestamp tick as the read cannot keep it (racy-Git
# rule; 2 s also covers coarse filesystems).
RACY_NS = 2_000_000_000


class Digests(NamedTuple):
    """What a sync needs to know about one file's bytes without re-reading them."""
    digest: str    # sha1 of the bytes (projection manifest and observation digest)
    lf: str        # sha1 of the LF-normalised bytes
    crlf: str      # sha1 of the CRLF-normalised bytes
    blob: str      # Git blob id of the bytes
    blob_lf: str   # Git blob id of the LF-normalised bytes

    @property
    def variants(self) -> set[str]:
        return {self.digest, self.lf, self.crlf}

    @classmethod
    def of(cls, content: bytes) -> "Digests":
        lf = content.replace(b"\r\n", b"\n")
        crlf = lf.replace(b"\n", b"\r\n")

        def blob(data):
            return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        return cls(hashlib.sha1(content).hexdigest(), hashlib.sha1(lf).hexdigest(),
                   hashlib.sha1(crlf).hexdigest(), blob(content), blob(lf))


class Scan:
    """One sync's view of one projection directory.

    - Each directory's path safety (no link/reparse component, no escape) is
      established once per scan instead of once per file and phase.
    - Each file is lstat'ed once per scan (`info`); the lstat refuses a link/reparse
      file and a non-regular file exactly as `observe` does.
    - `digests` answers from `known` only when the file's current fingerprint
      (volume, file id, size, mtime_ns, ctime_ns) equals the one recorded when those
      digests were computed from bytes read with `observe`'s before/after identity
      checks. It never yields bytes: anything that is imported, merged, classified
      as differing or published is read in full.
    """

    def __init__(self, root: Path, known: dict | None = None):
        self.root = Path(root).absolute()
        self.known = known or {}
        self._entries: dict[str, list] = {}
        self._directories: dict[str, Path | BaseException] = {}
        self._info: dict[str, os.stat_result | None] = {}
        self._stale: set[str] = set()  # known entries this scan proved outdated
        self.hits = 0

    def directory(self, rel: str) -> Path:
        """The checked directory `rel` ("" for the root); raises what the check raised."""
        if rel not in self._directories:
            try:
                path = self.root if not rel else safe_path(self.root, rel)
                _check_component(path)
                self._directories[rel] = path
            except (OSError, UnsafePath) as exc:
                self._directories[rel] = exc
        found = self._directories[rel]
        if isinstance(found, BaseException):
            raise found
        return found

    def info(self, rel: str, *, fresh: bool = False):
        """lstat of a regular projection file, None when it (or its directory) is absent."""
        if fresh or rel not in self._info:
            parent, _, name = str(relative(rel)).rpartition("/")
            try:
                path = self.directory(parent) / name
                info = _check_component(path)
            except FileNotFoundError:
                info = None
            if info is not None and not stat.S_ISREG(info.st_mode):
                raise UnsafePath(f"projection is not a regular file: {rel}")
            self._info[rel] = info
        return self._info[rel]

    def digests(self, rel: str, *, fresh: bool = False) -> Digests | None:
        """The recorded digests when the file still has the fingerprint they were
        recorded under; None on any miss (absent, changed, never recorded)."""
        info = self.info(rel, fresh=fresh)
        entry = self._entries.get(rel) or self.known.get(rel)
        if info is None or not _valid_entry(entry) or tuple(entry[0]) != _fingerprint(info):
            self._stale.add(rel)
            self._entries.pop(rel, None)
            return None
        self._entries[rel] = entry
        self.hits += 1
        return Digests(*entry[1])

    def observe(self, rel: str, *, authored: bool = True, limit: int = MAX_FILE_BYTES) -> Observation | None:
        """A full `observe` (every identity check), recording its fingerprint when safe."""
        if authored:
            classify(rel)
        observed = _read_observed(self.root, rel, limit)
        if observed is not None:
            self.record(observed)
        return observed

    def record(self, observed: Observation) -> None:
        fingerprint = observed.fingerprint
        if not fingerprint:
            return
        # POSIX ctime is the inode change time (a write moves it); Windows reports the
        # creation time there, which says nothing about later writes.
        changed = fingerprint[3] if os.name == "nt" else max(fingerprint[3], fingerprint[4])
        if time.time_ns() - changed > RACY_NS:
            self._entries[observed.file] = [list(fingerprint), list(Digests.of(observed.content))]
            self._info.pop(observed.file, None)  # the next lookup takes a fresh lstat

    def entries(self) -> dict:
        """Fingerprints this scan confirmed or recorded (persisted for the next sync)."""
        return dict(self._entries)

    def merged(self) -> dict:
        """What to persist: the known entries this scan did not disprove, updated by
        what it confirmed or recorded (a scan over some paths keeps the others')."""
        merged = {rel: entry for rel, entry in self.known.items() if rel not in self._stale}
        merged.update(self._entries)
        if len(merged) > 2 * len(self._entries) + 1024:
            return dict(self._entries)  # bound entries for paths nobody looks up any more
        return merged


def _valid_entry(entry) -> bool:
    return (isinstance(entry, list) and len(entry) == 2 and isinstance(entry[0], list) and len(entry[0]) == 5
            and all(type(value) is int for value in entry[0]) and isinstance(entry[1], list)
            and len(entry[1]) == 5 and all(isinstance(value, str) and len(value) == 40 for value in entry[1]))


# ── Persisted fingerprints (a cache: any doubt means a full read) ──────────────
CACHE_VERSION = 1
CACHE_CHECKOUTS = 40


def cache_path(store_root: Path) -> Path:
    return Path(store_root) / ".taskmaster" / "local" / "cache" / "sync-fingerprints.json"


def _cache_key(backlog: Path) -> str:
    return os.path.normcase(str(Path(backlog).absolute()))


def _load_cache(store_root: Path) -> dict:
    try:
        with cache_path(store_root).open("rb") as stream:
            value = json.loads(stream.read(64 * 1024 * 1024))
    except (OSError, ValueError, UnicodeError):
        return {}
    if not isinstance(value, dict) or value.get("version") != CACHE_VERSION or not isinstance(value.get("checkouts"), dict):
        return {}
    return value["checkouts"]


def open_scan(store_root: Path, backlog: Path, *, fast: bool = True) -> Scan:
    """A scan of `backlog` seeded with its persisted fingerprints (none when not `fast`)."""
    known = _load_cache(store_root).get(_cache_key(backlog)) if fast else None
    return Scan(backlog, known if isinstance(known, dict) else None)


def save_scan(store_root: Path, scan: Scan) -> None:
    """Replace `scan.root`'s fingerprints with what this scan confirmed; best effort."""
    entries = scan.merged()
    if entries == scan.known:
        return
    checkouts = _load_cache(store_root)
    key = _cache_key(scan.root)
    checkouts.pop(key, None)
    while len(checkouts) >= CACHE_CHECKOUTS:
        checkouts.pop(next(iter(checkouts)))
    checkouts[key] = entries
    path = cache_path(store_root)
    temp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(json.dumps({"version": CACHE_VERSION, "checkouts": checkouts}, separators=(",", ":")),
                        encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        try:
            temp.unlink()
        except OSError:
            pass


def unchanged(root: Path, observed: Observation) -> bool:
    try:
        current = observe(root, observed.file)
    except (OSError, ValueError):
        return False
    return current is not None and current.content == observed.content
