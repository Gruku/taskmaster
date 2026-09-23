"""User intent: contain a managed Git child in a private Windows Job Object so that
Git and its hooks provably stop before any replacement coordinator publishes.

Only the job capability is used: no PID is ever probed or signalled. The name is
private (owner-only DACL) and unpredictable, so recovery can reopen exactly the
job a crashed coordinator created and nobody can pre-create or squat it.
"""
from __future__ import annotations

import os
import secrets

ERROR_ALREADY_EXISTS = 183
ERROR_FILE_NOT_FOUND = 2
ERROR_INVALID_NAME = 123
_KILL_ON_JOB_CLOSE = 0x2000
_DIE_ON_UNHANDLED_EXCEPTION = 0x400
_JOB_QUERY = 0x0004
_JOB_ACCESS = _JOB_QUERY | 0x0008  # recovery needs only query + terminate
_NAME_PREFIX = 'Local\\taskmaster-git-'
RETIRED_EXIT_CODE = 0x7A5C


class JobUnavailable(OSError):
    """The job boundary could not be created, opened or proven."""


def supported() -> bool:
    return os.name == 'nt'


def new_name() -> str:
    return _NAME_PREFIX + secrets.token_hex(16)


def valid_name(name) -> bool:
    return (isinstance(name, str) and name.startswith(_NAME_PREFIX) and len(name) == len(_NAME_PREFIX) + 32
            and all(c in '0123456789abcdef' for c in name[len(_NAME_PREFIX):]))


_api = None


def _load():
    global _api
    if _api is not None:
        return _api
    import ctypes as c
    from ctypes import wintypes as w

    kernel = c.WinDLL('kernel32', use_last_error=True)
    advapi = c.WinDLL('advapi32', use_last_error=True)

    class SECURITY_ATTRIBUTES(c.Structure):
        _fields_ = [('nLength', w.DWORD), ('lpSecurityDescriptor', c.c_void_p), ('bInheritHandle', w.BOOL)]

    class IO_COUNTERS(c.Structure):
        _fields_ = [(name, c.c_ulonglong) for name in ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                                                       'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

    class BASIC_LIMIT(c.Structure):
        _fields_ = [('PerProcessUserTimeLimit', c.c_longlong), ('PerJobUserTimeLimit', c.c_longlong),
                    ('LimitFlags', w.DWORD), ('MinimumWorkingSetSize', c.c_size_t),
                    ('MaximumWorkingSetSize', c.c_size_t), ('ActiveProcessLimit', w.DWORD),
                    ('Affinity', c.c_size_t), ('PriorityClass', w.DWORD), ('SchedulingClass', w.DWORD)]

    class EXTENDED_LIMIT(c.Structure):
        _fields_ = [('BasicLimitInformation', BASIC_LIMIT), ('IoInfo', IO_COUNTERS),
                    ('ProcessMemoryLimit', c.c_size_t), ('JobMemoryLimit', c.c_size_t),
                    ('PeakProcessMemoryUsed', c.c_size_t), ('PeakJobMemoryUsed', c.c_size_t)]

    class BASIC_ACCOUNTING(c.Structure):
        _fields_ = [('TotalUserTime', c.c_longlong), ('TotalKernelTime', c.c_longlong),
                    ('ThisPeriodTotalUserTime', c.c_longlong), ('ThisPeriodTotalKernelTime', c.c_longlong),
                    ('TotalPageFaultCount', w.DWORD), ('TotalProcesses', w.DWORD),
                    ('ActiveProcesses', w.DWORD), ('TotalTerminatedProcesses', w.DWORD)]

    kernel.CreateJobObjectW.argtypes = [c.POINTER(SECURITY_ATTRIBUTES), w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.OpenJobObjectW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
    kernel.OpenJobObjectW.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    kernel.SetInformationJobObject.restype = w.BOOL
    kernel.QueryInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
    kernel.QueryInformationJobObject.restype = w.BOOL
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.AssignProcessToJobObject.restype = w.BOOL
    kernel.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
    kernel.TerminateJobObject.restype = w.BOOL
    kernel.IsProcessInJob.argtypes = [w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)]
    kernel.IsProcessInJob.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.DuplicateHandle.argtypes = [w.HANDLE, w.HANDLE, w.HANDLE, c.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD]
    kernel.DuplicateHandle.restype = w.BOOL
    kernel.LocalFree.argtypes = [c.c_void_p]
    kernel.LocalFree.restype = c.c_void_p
    advapi.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
    advapi.OpenProcessToken.restype = w.BOOL
    advapi.GetTokenInformation.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
    advapi.GetTokenInformation.restype = w.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [c.c_void_p, c.POINTER(w.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = w.BOOL
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR, w.DWORD, c.POINTER(c.c_void_p), c.c_void_p]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
    kernel.ProcessIdToSessionId.argtypes = [w.DWORD, c.POINTER(w.DWORD)]
    kernel.ProcessIdToSessionId.restype = w.BOOL
    kernel.GetCurrentProcessId.restype = w.DWORD

    class Api:
        pass
    api = Api()
    api.c, api.w, api.kernel, api.advapi = c, w, kernel, advapi
    api.SECURITY_ATTRIBUTES, api.EXTENDED_LIMIT, api.BASIC_ACCOUNTING = SECURITY_ATTRIBUTES, EXTENDED_LIMIT, BASIC_ACCOUNTING
    _api = api
    return api


def _error(api, action):
    code = api.c.get_last_error()
    return JobUnavailable(code, f'{action} failed: {api.c.FormatError(code).strip()}')


def _user_sid(api, information_class=1):
    """String SID from the process token: TokenUser (1) or TokenIntegrityLevel (25);
    both structures start with the SID pointer."""
    c, w = api.c, api.w
    token = w.HANDLE()
    if not api.advapi.OpenProcessToken(api.kernel.GetCurrentProcess(), 8, c.byref(token)):
        raise _error(api, 'OpenProcessToken')
    try:
        size = w.DWORD()
        api.advapi.GetTokenInformation(token, information_class, None, 0, c.byref(size))
        buffer = c.create_string_buffer(size.value)
        if not api.advapi.GetTokenInformation(token, information_class, buffer, size, c.byref(size)):
            raise _error(api, 'GetTokenInformation')
        text = w.LPWSTR()
        if not api.advapi.ConvertSidToStringSidW(c.cast(buffer, c.POINTER(c.c_void_p))[0], c.byref(text)):
            raise _error(api, 'ConvertSidToStringSid')
        try:
            return text.value
        finally:
            api.kernel.LocalFree(c.cast(text, c.c_void_p))
    finally:
        api.kernel.CloseHandle(token)


def identity() -> dict:
    """Where a `Local\\` job name is visible and openable: logon session + integrity level."""
    api = _load()
    session = api.w.DWORD()
    if not api.kernel.ProcessIdToSessionId(api.kernel.GetCurrentProcessId(), api.c.byref(session)):
        raise _error(api, 'ProcessIdToSessionId')
    return {'session': int(session.value), 'integrity': _user_sid(api, 25)}


class Job:
    """One owned handle to a named job. Closing the last handle kills its members."""

    def __init__(self, handle, name):
        self.handle, self.name = handle, name

    @classmethod
    def create(cls, name):
        if not valid_name(name):
            raise ValueError('invalid managed job name')
        api = _load()
        c = api.c
        descriptor = c.c_void_p()
        # Protected owner-only DACL: other users cannot open, assign or terminate.
        if not api.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                f'D:P(A;;GA;;;{_user_sid(api)})', 1, c.byref(descriptor), None):
            raise _error(api, 'ConvertStringSecurityDescriptor')
        try:
            attributes = api.SECURITY_ATTRIBUTES(c.sizeof(api.SECURITY_ATTRIBUTES), descriptor, False)
            handle = api.kernel.CreateJobObjectW(c.byref(attributes), name)
            if not handle:
                raise _error(api, 'CreateJobObject')
            if c.get_last_error() == ERROR_ALREADY_EXISTS:
                # Someone else holds this name; it is not ours to use or retire.
                api.kernel.CloseHandle(handle)
                raise JobUnavailable(ERROR_ALREADY_EXISTS, 'managed job name already exists; refusing to reuse it')
        finally:
            api.kernel.LocalFree(descriptor)
        job = cls(handle, name)
        try:
            limits = api.EXTENDED_LIMIT()
            # Kill-on-close with no breakaway flags: no member may leave the job.
            limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE | _DIE_ON_UNHANDLED_EXCEPTION
            if not api.kernel.SetInformationJobObject(handle, 9, c.byref(limits), c.sizeof(limits)):
                raise _error(api, 'SetInformationJobObject')
        except BaseException:
            job.close()
            raise
        return job

    @classmethod
    def open(cls, name):
        """The recorded job, or None when the kernel has no object by that name."""
        if not valid_name(name):
            raise ValueError('invalid managed job name')
        api = _load()
        handle = api.kernel.OpenJobObjectW(_JOB_ACCESS, False, name)
        if not handle:
            code = api.c.get_last_error()
            if code in (ERROR_FILE_NOT_FOUND, ERROR_INVALID_NAME):
                return None
            raise JobUnavailable(code, f'OpenJobObject failed: {api.c.FormatError(code).strip()}')
        return cls(handle, name)

    def assign(self, process_handle):
        api = _load()
        if not api.kernel.AssignProcessToJobObject(self.handle, int(process_handle)):
            raise _error(api, 'AssignProcessToJobObject')

    def contains(self, process_handle) -> bool:
        api = _load()
        result = api.w.BOOL()
        if not api.kernel.IsProcessInJob(int(process_handle), self.handle, api.c.byref(result)):
            raise _error(api, 'IsProcessInJob')
        return bool(result.value)

    def active_processes(self) -> int:
        api = _load()
        info = api.BASIC_ACCOUNTING()
        if not api.kernel.QueryInformationJobObject(self.handle, 1, api.c.byref(info), api.c.sizeof(info), None):
            raise _error(api, 'QueryInformationJobObject')
        return int(info.ActiveProcesses)

    def total_processes(self) -> int:
        api = _load()
        info = api.BASIC_ACCOUNTING()
        if not api.kernel.QueryInformationJobObject(self.handle, 1, api.c.byref(info), api.c.sizeof(info), None):
            raise _error(api, 'QueryInformationJobObject')
        return int(info.TotalProcesses)

    def terminate(self):
        api = _load()
        if not api.kernel.TerminateJobObject(self.handle, RETIRED_EXIT_CODE):
            raise _error(api, 'TerminateJobObject')

    def retire(self, timeout) -> bool:
        """Terminate every member and wait until the kernel reports none active."""
        import time
        self.terminate()
        deadline = time.monotonic() + max(0, timeout)
        while True:
            if self.active_processes() == 0:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)

    def inheritable(self):
        """A temporary inheritable, query-only duplicate for one helper launch; caller closes it.

        Query is all the helper needs (IsProcessInJob); a hook that somehow reached this
        handle could neither terminate the job nor assign other processes to it."""
        api = _load()
        current = api.kernel.GetCurrentProcess()
        duplicate = api.w.HANDLE()
        if not api.kernel.DuplicateHandle(current, self.handle, current, api.c.byref(duplicate), _JOB_QUERY, True, 0):
            raise _error(api, 'DuplicateHandle')
        return duplicate.value

    def close(self):
        if self.handle:
            _load().kernel.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def close_handle(handle):
    if handle:
        _load().kernel.CloseHandle(handle)


def current_process_in(handle) -> bool:
    """Helper-side: whether this process is a member of the inherited job handle."""
    api = _load()
    result = api.w.BOOL()
    if not api.kernel.IsProcessInJob(api.kernel.GetCurrentProcess(), int(handle), api.c.byref(result)):
        raise _error(api, 'IsProcessInJob')
    return bool(result.value)
