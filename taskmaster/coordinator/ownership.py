"""Private coordinator discovery and kernel ownership; PIDs never establish authority."""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import re
import uuid


class OwnershipUnavailable(RuntimeError):
    pass


def _windows_private(path: Path, *, verify_only=False) -> None:
    """Restrict a service directory to its current-user owner, including children."""
    import ctypes as c
    from ctypes import wintypes as w
    advapi, kernel = c.WinDLL('advapi32', use_last_error=True), c.WinDLL('kernel32', use_last_error=True)
    advapi.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
    advapi.OpenProcessToken.restype = w.BOOL
    advapi.GetTokenInformation.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
    advapi.GetTokenInformation.restype = w.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [c.c_void_p, c.POINTER(w.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = w.BOOL
    advapi.GetNamedSecurityInfoW.argtypes = [w.LPWSTR, c.c_int, w.DWORD, c.POINTER(c.c_void_p),
                                           c.c_void_p, c.c_void_p, c.c_void_p, c.POINTER(c.c_void_p)]
    advapi.GetNamedSecurityInfoW.restype = w.DWORD
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR, w.DWORD, c.POINTER(c.c_void_p), c.c_void_p]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [c.c_void_p, w.DWORD, w.DWORD, c.POINTER(w.LPWSTR), c.c_void_p]
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = w.BOOL
    advapi.GetSecurityDescriptorDacl.argtypes = [c.c_void_p, c.POINTER(w.BOOL), c.POINTER(c.c_void_p), c.POINTER(w.BOOL)]
    advapi.GetSecurityDescriptorDacl.restype = w.BOOL
    advapi.SetNamedSecurityInfoW.argtypes = [w.LPWSTR, c.c_int, w.DWORD, c.c_void_p, c.c_void_p, c.c_void_p, c.c_void_p]
    advapi.SetNamedSecurityInfoW.restype = w.DWORD
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.LocalFree.argtypes = [c.c_void_p]
    kernel.LocalFree.restype = c.c_void_p

    def sid_text(sid):
        text = w.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid, c.byref(text)):
            raise c.WinError(c.get_last_error())
        try:
            return text.value
        finally:
            kernel.LocalFree(c.cast(text, c.c_void_p))

    token = w.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, c.byref(token)):
        raise c.WinError(c.get_last_error())
    try:
        size = w.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, c.byref(size))
        buffer = c.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size, c.byref(size)):
            raise c.WinError(c.get_last_error())
        user = sid_text(c.cast(buffer, c.POINTER(c.c_void_p))[0])
    finally:
        kernel.CloseHandle(token)
    owner, current = c.c_void_p(), c.c_void_p()
    code = advapi.GetNamedSecurityInfoW(str(path), 1, 5, c.byref(owner), None, None, None, c.byref(current))
    if code:
        raise c.WinError(code)
    try:
        if sid_text(owner) != user:
            raise PermissionError('coordinator directory must be owned by the current user')
        if verify_only:
            text = w.LPWSTR()
            if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(current, 1, 4, c.byref(text), None):
                raise c.WinError(c.get_last_error())
            try:
                aces = [ace.split(';') for ace in re.findall(r'\(([^()]*)\)', text.value)]
                if not aces or any(ace[0] != 'A' or ace[-1] != user for ace in aces):
                    raise PermissionError('coordinator discovery permissions are not owner-only')
            finally:
                kernel.LocalFree(c.cast(text, c.c_void_p))
            return
    finally:
        kernel.LocalFree(current)
    descriptor = c.c_void_p()
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(f'D:P(A;OICI;FA;;;{user})', 1, c.byref(descriptor), None):
        raise c.WinError(c.get_last_error())
    try:
        present, defaulted, acl = w.BOOL(), w.BOOL(), c.c_void_p()
        if not advapi.GetSecurityDescriptorDacl(descriptor, c.byref(present), c.byref(acl), c.byref(defaulted)) or not present:
            raise c.WinError(c.get_last_error())
        code = advapi.SetNamedSecurityInfoW(str(path), 1, 0x80000004, None, None, acl, None)
        if code:
            raise c.WinError(code)
    finally:
        kernel.LocalFree(descriptor)


def private_directory(root: Path) -> Path:
    path = root / '.taskmaster' / 'local' / 'coordinator'
    path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
        raise PermissionError('coordinator directory cannot be a link')
    if path.resolve().parent != (root / '.taskmaster' / 'local').resolve():
        raise PermissionError('coordinator directory escapes its local store')
    if os.name == 'nt':
        _windows_private(path)
    else:
        if path.stat().st_uid != os.getuid():
            raise PermissionError('coordinator directory must be owned by the current user')
        path.chmod(0o700)
    return path


def verify_private(path: Path):
    """Read-only validation before trusting an existing discovery token."""
    if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
        raise PermissionError('coordinator discovery cannot be a link')
    if os.name == 'nt':
        _windows_private(path, verify_only=True)
    else:
        info = path.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError('coordinator discovery permissions are not owner-only')


def ownership_held(root: Path) -> bool:
    """Whether some process holds the kernel ownership lock right now.

    Probes by taking and immediately releasing the lock. A concurrent startup
    that loses to the probe exits cleanly; the caller then starts its own."""
    try:
        descriptor = os.open(Path(root) / '.taskmaster/local/coordinator/owner.lock', os.O_RDWR)
    except FileNotFoundError:
        return False
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN, 13, 36):
            return True
        raise
    finally:
        os.close(descriptor)
    return False


class Ownership:
    """A held byte/flock, released only by closing our own handle or process exit."""
    def __init__(self, root: Path):
        self.directory = private_directory(root)
        self.descriptor = None

    def acquire(self):
        if self.descriptor is not None:
            raise RuntimeError('ownership is already held by this instance')
        descriptor = os.open(self.directory / 'owner.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b'\0')
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(descriptor)
            if exc.errno in (errno.EACCES, errno.EAGAIN, 13, 36):
                raise OwnershipUnavailable('repository coordinator ownership is already held') from exc
            raise
        self.descriptor = descriptor
        return self

    def close(self):
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None

    def publish(self, record: dict):
        if self.descriptor is None:
            raise RuntimeError('discovery publication requires ownership')
        temp = self.directory / f'discovery.{uuid.uuid4().hex}.tmp'
        try:
            with temp.open('x', encoding='utf-8') as stream:
                if os.name != 'nt':
                    os.fchmod(stream.fileno(), 0o600)
                json.dump(record, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.directory / 'discovery.json')
        finally:
            temp.unlink(missing_ok=True)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.close()
