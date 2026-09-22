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


_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT
_NAME_SURROGATE = 0x20000000  # IsReparseTagNameSurrogate: symlink, junction/mount point


def check_component(path):
    """Refuse components that redirect a name elsewhere.

    Only name-surrogate reparse tags (symlinks, junctions/mount points) redirect;
    cloud/dedup placeholders are ordinary local files and stay publishable. A
    reparse point whose tag cannot be read is refused conservatively.
    """
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise UnsafePath(f"linked/reparse projection path refused: {path}")
    if getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
        tag = getattr(info, "st_reparse_tag", 0)
        if not tag or tag & _NAME_SURROGATE:
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
