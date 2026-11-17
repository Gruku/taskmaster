# User intent: the one place a twohost test may wait; every wait is a polled condition with a bound and a description, never a bare sleep (015 A4).
"""Deterministic waiting for the two-host harness."""
from __future__ import annotations

import time

DEFAULT_TIMEOUT = 15.0
MAX_TIMEOUT = 30.0
POLL = 0.05


class WaitFailed(AssertionError):
    pass


def until(condition, timeout: float = DEFAULT_TIMEOUT, what: str = "condition", *, interval: float = POLL):
    """Poll `condition()` every `interval` (default 50 ms; slower for calls a rate limit counts) until it returns a truthy value (returned); fail naming `what` and the last value."""
    assert timeout <= MAX_TIMEOUT, f"no single wait may exceed {MAX_TIMEOUT}s ({what})"
    deadline = time.monotonic() + timeout
    last = None
    while True:
        try:
            last = condition()
        except Exception as exc:  # noqa: BLE001 - a condition that raises is "not yet", reported if it never settles
            last = f"raised {type(exc).__name__}: {exc}"
        else:
            if last:
                return last
        if time.monotonic() >= deadline:
            raise WaitFailed(f"timed out after {timeout}s waiting for {what}; last value: {last!r}")
        time.sleep(interval)
