"""Read-only projection discovery and bounded observation for explicit sync.

Never walks local state or arbitrary globs supplied by a caller. Links/junctions
are refused. Rechecking closes ordinary editor races; this is not a security
sandbox against a hostile process continuously replacing ancestor directories.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat

from taskmaster.projection_parse import ENTITY_FILE_SPECS, classify
from taskmaster.projection_paths import UnsafePath, check_component as _check_component, safe_path

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


def _visible(name):
    return not (name.startswith(".") or ".tmp." in name or ".corrupt-" in name)


def discover(root: Path) -> Inventory:
    root = Path(root).absolute()
    result = Inventory()
    seen = {}

    def listing(rel, *, directories_only=False):
        try:
            directory = root if not rel else safe_path(root, rel)
            _check_component(directory)
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
            path = safe_path(root, rel)
            info = _check_component(path)
            if not stat.S_ISREG(info.st_mode):
                raise UnsafePath(f"projection is not a regular file: {rel}")
        except FileNotFoundError:
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
    path = safe_path(root, rel)
    try:
        before = _check_component(path)
        if not stat.S_ISREG(before.st_mode):
            raise UnsafePath(f"projection is not a regular file: {rel}")
        if before.st_size > limit:
            raise ValueError(f"projection exceeds byte limit ({limit}): {rel}")
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            content = handle.read(limit + 1)
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
                       after.st_size, after.st_dev, after.st_ino)


def unchanged(root: Path, observed: Observation) -> bool:
    try:
        current = observe(root, observed.file)
    except (OSError, ValueError):
        return False
    return current is not None and current.content == observed.content
