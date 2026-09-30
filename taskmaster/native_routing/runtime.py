"""Native adapter seam: read-only snapshots and coordinator-only writes.

Direct engine calls belong to explicit core tests. A missing coordinator never
enables an in-process writer or synchronous projection fallback.
"""
from taskmaster.coordinator.adapter import NativeCall, open_call  # noqa: F401


def error_text(exc: BaseException) -> str:
    """A refusal from the core as the tools' own Error sentence."""
    message = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
    return f"Error: {message}"
