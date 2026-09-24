# User intent: let the N15 cutover tests control the process-level quiesce probes. A real
# process scan sees every Taskmaster server on this machine, so tests install a quiet
# `taskmaster.native.quiesce` stand-in; the carry-over primitives are always the real ones.
"""Test seam for `taskmaster.native.cutover`; never imported by production code."""
from __future__ import annotations

import sys
import types

from taskmaster.native.quiesce import ScanResult


def quiet_quiesce() -> types.ModuleType:
    module = types.ModuleType("taskmaster.native.quiesce")
    module.ScanResult = ScanResult
    module.live_owner = lambda root: None
    module.open_writers = lambda db_path: False
    module.scan_processes = lambda root, **kwargs: ScanResult([])
    return module


def install(monkeypatch=None):
    """Install a quiet quiesce module (all clear); returns it so a test can override probes."""
    quiesce = quiet_quiesce()
    if monkeypatch is None:
        sys.modules["taskmaster.native.quiesce"] = quiesce
    else:
        monkeypatch.setitem(sys.modules, "taskmaster.native.quiesce", quiesce)
    return quiesce
