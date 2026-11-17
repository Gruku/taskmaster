# User intent: a small per-user disk cache for team-server reads, so a burst of edits or several sessions on one
# machine cost the server one request, an offline laptop or a revoked key fails fast, and nothing secret or
# host-private ever lands on a teammate's disk (team-server-007 D11-D13). It is an optimization: it never decides safety.
"""Owner-only, best-effort cache of `/read/v1/` replies, with down and refused negative entries."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from . import dirs


@dataclass(frozen=True)
class Policy:
    fresh: float        # seconds a reply is served without asking the server
    stale: float        # seconds a reply may still be served when the server fails (0: never)
    cacheable: bool = True
    negative: bool = True   # whether down/refused entries make this view fail fast


CACHE_POLICY = {
    "merge-gate": Policy(0, 0, cacheable=False, negative=False),
    "merge-target": Policy(300, 86400),
    "paths": Policy(30, 600),
    "tools": Policy(10, 600),
    "meta": Policy(300, 0),
}
STALE_KINDS = frozenset({"unreachable", "busy", "server", "bad-response"})
DOWN_SECONDS = 15
REFUSED_SECONDS = 60
REFUSED_MIN_GAP, REFUSED_MAX_GAP = 5, 120
MAX_AGE = 86400
MAX_FILES_PER_VIEW = 512
MAX_SERVER_BYTES = 64 * 1024 * 1024
MAX_ENTRY_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Entry:
    data: dict
    revision: int
    store_id: str
    stored_at: float
    age: float


def params_key(params: dict) -> str:
    text = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        _unlink(path)
        return None


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _write_json(path: Path, value) -> bool:
    """Private temp file then os.replace; False when the replace lost to a reader (Windows sharing)."""
    temp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temp, path)
        return True
    except OSError:
        _unlink(temp)
        return False


class Cache:
    """The cache of one server for one key: `<cache>/remote/<server_id>/<key_id>/`."""

    def __init__(self, server_id: str, key_id: str, *, base: Path | None = None, clock=time.time, log=None):
        self.server_id, self.key_id = server_id, key_id
        self._clock, self._log = clock, log or (lambda text: None)
        self.reason = None
        try:
            self.server_dir = dirs.private_chain(base or dirs.cache_base(), "remote", server_id)
            self.key_dir = dirs.private_chain(self.server_dir, key_id)
        except OSError as exc:      # PermissionError included: a link or a foreign owner
            self.server_dir = self.key_dir = None
            self.reason = f"cache disabled: {exc}"
            self._log(self.reason)

    @property
    def enabled(self) -> bool:
        return self.key_dir is not None

    # -- entries ----------------------------------------------------------------------------------

    def _path(self, view: str, params: dict) -> Path:
        return self.key_dir / "v1" / view / f"{params_key(params)}.json"

    def get(self, view: str, params: dict) -> Entry | None:
        if not self.enabled or not CACHE_POLICY[view].cacheable:
            return None
        raw = _read_json(self._path(view, params))
        if not isinstance(raw, dict) or not isinstance(raw.get("data"), dict) or raw.get("params") != params:
            return None
        try:
            stored_at, revision, store_id = float(raw["stored_at"]), int(raw["revision"]), str(raw["store_id"])
        except (KeyError, TypeError, ValueError):
            _unlink(self._path(view, params))
            return None
        return Entry(raw["data"], revision, store_id, stored_at, self._clock() - stored_at)

    def fresh(self, view: str, params: dict) -> Entry | None:
        entry = self.get(view, params)
        if entry is None or entry.age < 0 or entry.age > CACHE_POLICY[view].fresh:
            return None
        return entry

    def stale(self, view: str, params: dict, kind: str) -> Entry | None:
        """An entry the policy lets stand in for a failed read of `kind`, or None."""
        if kind not in STALE_KINDS:
            return None
        entry = self.get(view, params)
        if entry is None or entry.age < 0 or entry.age > CACHE_POLICY[view].stale:
            return None
        return entry

    def put(self, view: str, params: dict, envelope: dict) -> None:
        if not self.enabled or not CACHE_POLICY[view].cacheable:
            return
        store_id, revision = str(envelope["store_id"]), int(envelope["revision"])
        self._note_store(store_id)
        path = self._path(view, params)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        value = {"stored_at": self._clock(), "store_id": store_id, "revision": revision, "view": view,
                 "params": params, "data": envelope["data"]}
        if len(json.dumps(value, ensure_ascii=False)) > MAX_ENTRY_BYTES:
            return
        for _attempt in range(2):
            current = _read_json(path)
            if isinstance(current, dict) and current.get("store_id") == store_id and \
                    isinstance(current.get("revision"), int) and current["revision"] > revision:
                return          # never replace a newer reply with an older one (best effort, D12)
            if not _write_json(path, value):
                self._log("cache write skipped (file in use)")
                return
            after = _read_json(path)
            if not isinstance(after, dict) or after.get("revision", -1) >= revision:
                break           # ours, or a newer one, stands
        self._prune(path.parent)

    def _note_store(self, store_id: str) -> None:
        marker = self.server_dir / "store.json"
        known = _read_json(marker)
        if isinstance(known, dict) and known.get("store_id") not in (None, store_id):
            self._log(f"the server's store changed ({known.get('store_id')} -> {store_id}); cache emptied")
            for child in self.server_dir.iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child / "v1", ignore_errors=True)
            shutil.rmtree(self.server_dir / "hooks" / "hook-seen", ignore_errors=True)
        if not isinstance(known, dict) or known.get("store_id") != store_id:
            _write_json(marker, {"store_id": store_id})

    def _prune(self, view_dir: Path) -> None:
        now = self._clock()
        try:
            files = [(p, p.stat()) for p in view_dir.glob("*.json")]
        except OSError:
            return
        files.sort(key=lambda item: item[1].st_mtime, reverse=True)
        for index, (path, info) in enumerate(files):
            if index >= MAX_FILES_PER_VIEW or now - info.st_mtime > MAX_AGE:
                _unlink(path)
        try:
            every = [(p, p.stat()) for p in self.server_dir.glob("*/v1/*/*.json")]
        except OSError:
            return
        total = sum(info.st_size for _, info in every)
        for path, info in sorted(every, key=lambda item: item[1].st_mtime):
            if total <= MAX_SERVER_BYTES:
                break
            _unlink(path)
            total -= info.st_size

    def forget_key(self) -> None:
        """Drop this key's cached replies (a confirmed refusal), keeping the negative entries."""
        if self.enabled:
            shutil.rmtree(self.key_dir / "v1", ignore_errors=True)

    # -- negative entries -------------------------------------------------------------------------

    def down_since(self) -> float | None:
        """When the server was last found unreachable, while that still holds; else None."""
        if not self.enabled:
            return None
        raw = _read_json(self.server_dir / "down.json")
        if isinstance(raw, dict) and isinstance(raw.get("until"), (int, float)) and raw["until"] > self._clock():
            return float(raw.get("at", raw["until"] - DOWN_SECONDS))
        return None

    def mark_down(self) -> None:
        if self.enabled:
            now = self._clock()
            _write_json(self.server_dir / "down.json", {"at": now, "until": now + DOWN_SECONDS})

    def refused(self, key_marker: str) -> bool:
        """Whether a confirmed refusal of this key (same file or env value) still holds."""
        if not self.enabled:
            return False
        path = self.key_dir / "refused.json"
        raw = _read_json(path)
        if not isinstance(raw, dict):
            return False
        if raw.get("key") == key_marker and isinstance(raw.get("until"), (int, float)) and raw["until"] > self._clock():
            return True
        _unlink(path)
        return False

    def note_refused(self, key_marker: str) -> bool:
        """Count a 401/403. True once it is confirmed: a second strike 5-120 s after the first (D13)."""
        if not self.enabled:
            return False
        now = self._clock()
        pending_path = self.key_dir / "refused-pending.json"
        pending = _read_json(pending_path)
        first = pending.get("first_at") if isinstance(pending, dict) and pending.get("key") == key_marker else None
        if isinstance(first, (int, float)) and REFUSED_MIN_GAP <= now - first <= REFUSED_MAX_GAP:
            _write_json(self.key_dir / "refused.json", {"key": key_marker, "until": now + REFUSED_SECONDS})
            _unlink(pending_path)
            self.forget_key()
            return True
        if not isinstance(first, (int, float)) or not (0 <= now - first < REFUSED_MIN_GAP):
            _write_json(pending_path, {"key": key_marker, "first_at": now})
        return False

    def note_success(self) -> None:
        if not self.enabled:
            return
        for path in (self.server_dir / "down.json", self.key_dir / "refused-pending.json",
                     self.key_dir / "refused.json"):
            if path.exists():
                _unlink(path)

    # -- hook state -------------------------------------------------------------------------------

    def hooks_dir(self) -> Path | None:
        """`<server_id>/hooks/`, where server-mode hooks keep their logs and dedupe state."""
        if self.server_dir is None:
            return None
        try:
            return dirs.private_chain(self.server_dir, "hooks")
        except OSError:
            return None
