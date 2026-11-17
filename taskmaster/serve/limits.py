# User intent: bounded, thread-safe token buckets and in-flight counters, so one key or one misbehaving address cannot starve the team server.
"""Token buckets per key, in-flight counters per key, and the peer key for the pre-auth failure budget."""
from __future__ import annotations

import ipaddress
import math
import threading
import time
from collections import OrderedDict

from .config import FAILURE_BUDGET, FAILURE_PEERS, RETRY_AFTER_MAX


class KeyedLimiter:
    """One token bucket per key. A threading.Lock guards an O(1) section and is never held across an await,
    so the viewer's threads and the MCP event loop can share one limiter (design D7)."""

    def __init__(self, capacity: int, per_second: float, *, max_keys: int | None = None, clock=time.monotonic):
        self.capacity = float(capacity)
        self.per_second = float(per_second)
        self.max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: OrderedDict[str, list[float]] = OrderedDict()   # key -> [tokens, last refill]

    def _bucket(self, key: str) -> list[float]:
        now = self._clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            if self.max_keys is not None and len(self._buckets) >= self.max_keys:
                self._buckets.popitem(last=False)      # the least recently touched; a full bucket loses nothing
            bucket = self._buckets[key] = [self.capacity, now]
        else:
            self._buckets.move_to_end(key)
            elapsed = max(0.0, now - bucket[1])
            bucket[0] = min(self.capacity, bucket[0] + elapsed * self.per_second)
            bucket[1] = now
        return bucket

    def _wait(self, bucket, cost: float = 1.0) -> float:
        if bucket[0] >= cost:
            return 0.0
        if self.per_second <= 0 or cost > self.capacity:
            return math.inf
        return (cost - bucket[0]) / self.per_second

    def take(self, key: str, cost: int = 1) -> float:
        """0.0 when `cost` tokens were taken (all or none); otherwise the seconds until they are due
        (math.inf with no refill, or for a cost above the bucket's capacity, which can never be met)."""
        with self._lock:
            bucket = self._bucket(key)
            wait = self._wait(bucket, float(cost))
            if wait == 0.0:
                bucket[0] -= float(cost)
            return wait

    def blocked(self, key: str) -> float:
        """Like take, but takes nothing."""
        with self._lock:
            return self._wait(self._bucket(key))

    def __len__(self) -> int:
        with self._lock:
            return len(self._buckets)


class InFlight:
    """Requests in progress per key; the table holds only keys with at least one."""

    def __init__(self, max_per_key: int):
        self.max_per_key = int(max_per_key)
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}

    def acquire(self, key: str) -> bool:
        with self._lock:
            count = self._counts.get(key, 0)
            if count >= self.max_per_key:
                return False
            self._counts[key] = count + 1
            self._admitted(key, count + 1)
            return True

    def release(self, key: str) -> None:
        with self._lock:
            count = self._counts.get(key, 0) - 1
            if count > 0:
                self._counts[key] = count
            else:
                self._counts.pop(key, None)

    def count(self, key: str) -> int:
        with self._lock:
            return self._counts.get(key, 0)

    def __len__(self) -> int:
        with self._lock:
            return len(self._counts)

    def _admitted(self, key: str, count: int) -> None:
        """Called under the lock after an acquire; a seam for tests."""


def retry_after(wait: float) -> int:
    if math.isinf(wait) or math.isnan(wait):
        return RETRY_AFTER_MAX
    return min(RETRY_AFTER_MAX, max(1, math.ceil(min(wait, RETRY_AFTER_MAX))))


def peer_key(addr: str) -> str:
    """IPv4 as itself; IPv6 by its /64, since one host usually holds a whole /64 (design D6)."""
    try:
        ip = ipaddress.ip_address(addr)
    except (ValueError, TypeError):
        return addr
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.IPv6Network(f"{ip}/64", strict=False))
    return str(ip)


def principal_limiter(config, *, clock=time.monotonic) -> KeyedLimiter:
    return KeyedLimiter(config.rate_burst, config.rate_per_minute / 60.0, clock=clock)


def principal_inflight(config) -> InFlight:
    return InFlight(config.max_inflight)


def failure_budget(clock=time.monotonic) -> KeyedLimiter:
    return KeyedLimiter(FAILURE_BUDGET, FAILURE_BUDGET / 60.0, max_keys=FAILURE_PEERS, clock=clock)
