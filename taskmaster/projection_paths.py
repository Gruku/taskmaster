"""Projection path safety shared by explicit import and publication."""
from pathlib import Path, PurePosixPath
import stat


class UnsafePath(ValueError):
    pass


def relative(rel):
    if not isinstance(rel, str) or not rel or any(char in rel for char in (":", "\\", "\0")):
        raise UnsafePath(f"unsafe projection path {rel!r}")
    path = PurePosixPath(rel)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != rel:
        raise UnsafePath(f"unsafe projection path {rel!r}")
    return path


def check_component(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise UnsafePath(f"linked/reparse projection path refused: {path}")
    return info


def safe_path(root: Path, rel: str) -> Path:
    """Reject links/reparse points and escaping paths before filesystem access.

    This does not sandbox a hostile process continuously replacing ancestors;
    callers still recheck observed files around non-transactional publication.
    """
    parts = relative(rel).parts
    root = Path(root).absolute()
    check_component(root)
    path = root
    for part in parts:
        path /= part
        try:
            check_component(path)
        except FileNotFoundError:
            break
    candidate = root.joinpath(*parts)
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise UnsafePath(f"projection escapes backlog directory: {rel}")
    return candidate
