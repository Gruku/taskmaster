# User intent: a relay that cannot locate its own plugin directory must fail closed (exit non-zero, spawn nothing), never run or read
# files relative to the project's cwd (review L2 finding I1).
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "bin" / ("tm-relay.exe" if sys.platform == "win32" else "tm-relay-linux-x86_64")


@pytest.mark.skipif(not EXE.is_file() or (sys.platform != "win32" and not sys.platform.startswith("linux")), reason="no runnable relay binary")
def test_relay_without_tm_bridge_py_beside_bin_exits_nonzero_and_ignores_cwd(tmp_path):
    plugin = tmp_path / "plugin"
    (plugin / "bin").mkdir(parents=True)
    copy = plugin / "bin" / EXE.name
    shutil.copy2(EXE, copy)
    cwd = tmp_path / "project"
    cwd.mkdir()
    # hostile cwd: the files the relay would resolve if it fell back to relative paths
    (cwd / "tm_bridge.py").write_text("raise SystemExit(99)\n")
    (cwd / "tm_bridge_handshake.json").write_text("{}")
    out = subprocess.run([str(copy)], input=b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n', capture_output=True, cwd=cwd, timeout=20)
    assert out.returncode != 0
    assert b"cannot locate the plugin directory" in out.stderr
    assert out.stdout == b""
