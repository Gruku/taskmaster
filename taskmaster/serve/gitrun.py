# User intent: one hardened Git runner shared by the state export (013) and the migration (014), so every server-side
# Git call is refused before a process starts unless its argv is on that caller's allowlist, with no hook able to run.
"""`AllowlistedGit`: the argv-allowlisted, hook-free, scrubbed-environment Git runner.

A subclass supplies `refusal(args, env)` (None when the argv is allowed) and the two exception types it raises.
Extracted unchanged from `state_export.Git`; 013's allowlist and spy tests stay green on the subclass.
"""
from __future__ import annotations

import os
from pathlib import Path

from taskmaster.coordinator import git as managed

GIT_TIMEOUT = 60
HOOKS_DIR = "no-hooks"


class _Any:
    def __contains__(self, item):
        return True


class AllowlistedGit:
    """Every Git call of one feature. `record`, when a list, receives (argv, env) for the allowlist spy test."""

    refused_error: type[Exception] = RuntimeError
    failed_error: type[Exception] = RuntimeError
    hooks_dir = HOOKS_DIR
    PERMITTED_CONFIG: frozenset = frozenset()

    def __init__(self, root: Path, directory: Path, *, record=None):
        self.root, self.directory, self.record = Path(root), Path(directory), record
        self._counter = 0

    def temp_name(self, kind: str) -> Path:
        self._counter += 1
        return self.directory / f"{kind}-{os.getpid()}-{self._counter}.tmp"

    def refusal(self, args, env) -> str | None:
        raise NotImplementedError

    def _config_ok(self, config) -> bool:
        items = list(config)
        return len(items) % 2 == 0 and all(
            items[i] == "-c" and items[i + 1] in self.PERMITTED_CONFIG for i in range(0, len(items), 2))

    def run(self, *args, stdin=None, env=None, timeout=GIT_TIMEOUT, config=()):
        args, env = list(args), dict(env or {})
        refused = self.refusal(args, env)
        if refused is None and not self._config_ok(config):
            refused = f"git -c {' '.join(map(str, config))[:120]}"
        if refused is not None:
            raise self.refused_error(f"argv refused: {refused}")
        if self.record is not None:
            self.record.append((tuple(args), dict(env)))
        options = [*managed.QUIET_GIT, "-c", f"core.hooksPath={self.directory / self.hooks_dir}", *config]
        try:
            code, out, err = managed.probe(self.root, *options, *args, ok=_Any(), stdin=stdin, timeout=timeout,
                                           env=env, with_stderr=True)
        except managed.GitRefused as exc:
            text = str(exc)
            reason = "timed out" if "timed out" in text else "produced too much output" if "exceeds" in text else "failed"
            raise self.failed_error(f"git {args[0]} {reason}") from None
        return code, out, err

    def text(self, *args, **kwargs) -> tuple[int, str]:
        code, out, _ = self.run(*args, **kwargs)
        return code, out.decode("utf-8", "replace").strip()
