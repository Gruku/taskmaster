# User intent: identify a process by pid, creation time and command line, so the harness can sweep its own orphaned daemons without ever killing another lane's process when Windows reuses a pid (015 re-review R2).
"""Process identity, liveness and tree kill, with and without psutil."""
from __future__ import annotations

import json
import os
import subprocess
import sys


def alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x1000, False, pid)         # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def identity(pid: int) -> dict | None:
    """{"create_time": str, "cmdline": str} for a live process, or None when it cannot be read (never guessed)."""
    try:
        import psutil
        proc = psutil.Process(pid)
        return {"create_time": repr(proc.create_time()), "cmdline": " ".join(proc.cmdline())}
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 - gone, or not ours to read
        return None
    if sys.platform.startswith("linux"):
        try:
            stat = open(f"/proc/{pid}/stat", "rb").read().decode("utf-8", "replace")
            start = stat.rsplit(")", 1)[1].split()[19]            # field 22 overall
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            return {"create_time": start, "cmdline": cmd}
        except OSError:
            return None
    if os.name == "nt":
        script = (f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}'; if ($p) {{ "
                  "@{create_time = $p.CreationDate.ToString('o'); cmdline = $p.CommandLine} | ConvertTo-Json -Compress }")
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                 capture_output=True, text=True, timeout=30).stdout.strip()
            found = json.loads(out) if out else None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        if not found or not found.get("create_time") or not found.get("cmdline"):
            return None
        return {"create_time": found["create_time"], "cmdline": found["cmdline"]}
    return None


def kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=30)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def cpu_seconds_tree(pid: int) -> float | None:
    """CPU seconds (user + kernel) used so far by `pid` and all its descendants; None when it cannot be read."""
    try:
        import psutil
        proc = psutil.Process(pid)
        total = 0.0
        for one in [proc, *proc.children(recursive=True)]:
            times = one.cpu_times()
            total += times.user + times.system
        return total
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        return None
    if sys.platform.startswith("linux"):
        ticks = os.sysconf("SC_CLK_TCK")
        children: dict[int, list[int]] = {}
        used: dict[int, float] = {}
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                fields = open(f"/proc/{entry}/stat", "rb").read().decode("utf-8", "replace").rsplit(")", 1)[1].split()
            except OSError:
                continue
            children.setdefault(int(fields[1]), []).append(int(entry))
            used[int(entry)] = (int(fields[11]) + int(fields[12])) / ticks
        todo, total = [pid], 0.0
        while todo:
            one = todo.pop()
            total += used.get(one, 0.0)
            todo += children.get(one, [])
        return total if pid in used else None
    if os.name == "nt":
        script = ("function Tree($p) { $p; Get-CimInstance Win32_Process -Filter \"ParentProcessId=$p\" | "
                  "ForEach-Object { Tree $_.ProcessId } }; $sum = 0.0; "
                  f"foreach ($i in (Tree {int(pid)})) {{ $pr = Get-Process -Id $i -ErrorAction SilentlyContinue; "
                  "if ($pr) { $sum += $pr.TotalProcessorTime.TotalSeconds } }; $sum")
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                 capture_output=True, text=True, timeout=60).stdout.strip()
            return float(out)
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
    return None
