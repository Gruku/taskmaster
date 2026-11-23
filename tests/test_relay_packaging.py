# User intent: the committed relay binaries must be exactly what relay/ builds, carry no local paths, and have the launch properties the
# spec promises (GUI subsystem, static CRT, exec bit) - checked on every run so a stale or leaky binary cannot ship.
from __future__ import annotations

import json
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"
MANIFEST = BIN / "relay-manifest.json"
SCRIPT = ROOT / "scripts" / "build_relay.py"

# Spec section 6: any absolute path other than the remapped prefixes (/src, /cargo, /rustup). The look-behind keeps URL schemes
# (https://github.com/... in the musl build) from matching as drive letters.
LEAK = re.compile(rb"(?<![A-Za-z0-9+.-])[A-Za-z]:[\\/]|/home/|/Users/|\\Users\\|/root/")


@pytest.fixture(scope="module")
def manifest():
    assert MANIFEST.is_file(), "bin/relay-manifest.json is missing: run python scripts/build_relay.py"
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _binaries(manifest):
    return {triple: BIN / info["file"] for triple, info in manifest["targets"].items()}


def test_manifest_hashes_equal_the_committed_files(manifest):
    import hashlib

    assert manifest["targets"], "the manifest lists no target"
    for triple, path in _binaries(manifest).items():
        data = path.read_bytes()
        info = manifest["targets"][triple]
        assert hashlib.sha256(data).hexdigest() == info["sha256"], f"{path.name} differs from the manifest: rebuild with scripts/build_relay.py"
        assert len(data) == info["size"]


def test_each_binary_is_within_the_size_bound(manifest):
    for path in _binaries(manifest).values():
        assert path.stat().st_size <= 1024 * 1024, f"{path.name} is over 1 MB"


def test_build_relay_check_passes():
    out = subprocess.run([sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr


def test_version_prints_the_manifest_source_digest(manifest):
    exe = BIN / "tm-relay.exe"
    if sys.platform != "win32" or not exe.is_file():
        pytest.skip("the Windows binary runs on Windows only")
    out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20)
    assert out.returncode == 0
    parts = out.stdout.split()
    assert parts[0] == "tm-relay" and len(parts) == 3
    assert parts[2] == manifest["source_digest"]


def test_no_absolute_path_outside_the_remapped_prefixes(manifest):
    # every file under bin/ (binaries, the manifest, any entry script), not only the manifest-listed binaries
    files = [p for p in BIN.rglob("*") if p.is_file()]
    assert files
    for path in files:
        data = path.read_bytes()
        match = LEAK.search(data)
        assert match is None, f"{path.name} embeds a local path near {data[max(match.start() - 20, 0):match.end() + 60]!r}"


def test_bin_holds_exactly_the_manifest_binaries(manifest):
    listed = {info["file"] for info in manifest["targets"].values()}
    present = {p.name for p in BIN.iterdir() if p.is_file() and (p.name == "tm-relay.exe" or p.name.startswith("tm-relay-"))}
    assert present == listed, f"stale or missing binaries: unlisted {sorted(present - listed)}, missing {sorted(listed - present)}"


def _pe(path):
    data = path.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[pe:pe + 4] == b"PE\0\0"
    optional = pe + 24
    assert struct.unpack_from("<H", data, optional)[0] == 0x20B, "expected PE32+"
    return data, struct.unpack_from("<H", data, optional + 68)[0]


def test_windows_exe_is_gui_subsystem_without_the_vc_runtime():
    exe = BIN / "tm-relay.exe"
    if not exe.is_file():
        pytest.skip("no Windows binary committed")
    data, subsystem = _pe(exe)
    assert subsystem == 2, "tm-relay.exe must be the GUI subsystem so it can never own a console (R4)"
    assert b"VCRUNTIME" not in data.upper(), "the CRT must be linked statically (no VC++ redistributable)"


def test_linux_binaries_are_static_elf(manifest):
    for triple, path in _binaries(manifest).items():
        if "linux" not in triple:
            continue
        data = path.read_bytes()
        assert data[:4] == b"\x7fELF"
        assert b"ld-musl" not in data and b"/lib64/ld-linux" not in data, f"{path.name} names a dynamic interpreter"


def test_binaries_are_tracked_with_the_exec_bit(manifest):
    out = subprocess.run(["git", "ls-files", "-s", "--", *[f"bin/{i['file']}" for i in manifest["targets"].values()]],
                         capture_output=True, text=True, cwd=ROOT, timeout=30)
    tracked = {line.split("\t")[1]: line.split()[0] for line in out.stdout.splitlines()}
    for info in manifest["targets"].values():
        mode = tracked.get(f"bin/{info['file']}")
        assert mode == "100755", f"bin/{info['file']} must be tracked as 100755 (git update-index --chmod=+x), found {mode}"
