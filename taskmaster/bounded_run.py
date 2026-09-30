# User intent: a subprocess timeout must actually bound the call on Windows; a stalled
# Git-for-Windows grandchild once hung a tool call forever (7.0.0-rc.1, CodeMaestro).
# Standard library only — root.py imports this and the hooks import root.py.
"""`subprocess.run(..., capture_output=True, timeout=...)` that cannot outlive its timeout.

On Windows, `subprocess.run` kills only the direct child on timeout and then calls
`communicate()` with no timeout. Launchers such as Git for Windows' `cmd\\git.exe`
start the real program as a grandchild that inherits the output pipes, so a stalled
grandchild keeps them open and the caller waits forever. Here output goes to
temporary files (nothing to drain) and a timeout ends the whole process tree.
POSIX `subprocess.run` already returns on timeout, so it is used unchanged there.
"""
from __future__ import annotations

import io
import os
import subprocess
import tempfile

_KILL_WAIT = 5


def run_bounded(args, *, timeout, input=None, check=False, text=False, encoding=None, errors=None,
                stdin=None, **popen):
    """Capture stdout/stderr like `subprocess.run(capture_output=True)`; raise `TimeoutExpired` on time.

    stdin defaults to DEVNULL rather than inheriting: these are probes, and the MCP
    server's own stdin is the protocol stream.
    """
    if input is not None and stdin is not None:
        raise ValueError("stdin and input arguments may not both be used")
    if stdin is None and input is None:
        stdin = subprocess.DEVNULL
    if os.name != "nt":
        return subprocess.run(args, input=input, stdin=stdin, capture_output=True, timeout=timeout,
                              check=check, text=text, encoding=encoding, errors=errors, **popen)
    decode = text or encoding is not None or errors is not None
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err, \
            tempfile.TemporaryFile() as feed:
        if input is not None:
            feed.write(input.encode(encoding or _locale_encoding(), errors or "strict")
                       if isinstance(input, str) else input)
            feed.seek(0)
            stdin = feed
        process = subprocess.Popen(args, stdin=stdin, stdout=out, stderr=err, **popen)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            raise subprocess.TimeoutExpired(args, timeout) from None
        except BaseException:
            _kill_tree(process)
            raise
        stdout, stderr = _read(out), _read(err)
    if decode:
        stdout, stderr = _decode(stdout, encoding, errors), _decode(stderr, encoding, errors)
    if check and process.returncode:
        raise subprocess.CalledProcessError(process.returncode, args, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


def _kill_tree(process):
    """End the child and every descendant; never block past a short, fixed bound."""
    try:
        killer = subprocess.Popen(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            killer.wait(timeout=_KILL_WAIT)
        except subprocess.TimeoutExpired:
            killer.kill()
    except OSError:
        pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=_KILL_WAIT)
    except subprocess.TimeoutExpired:
        pass


def _read(handle):
    handle.seek(0)
    return handle.read()


def _locale_encoding():
    import locale
    return locale.getpreferredencoding(False)


def _decode(data, encoding, errors):
    # Same translation as subprocess's text mode: locale encoding, universal newlines.
    return io.TextIOWrapper(io.BytesIO(data), encoding=encoding or _locale_encoding(),
                            errors=errors).read()
