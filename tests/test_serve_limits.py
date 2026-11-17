# User intent: token buckets trip per key, refill on the clock, never let one key spend another's tokens, and stay bounded.
import math
import threading

import pytest

from taskmaster.serve.config import ServeConfig
from taskmaster.serve.limits import (InFlight, KeyedLimiter, failure_budget, peer_key, principal_inflight,
                                     principal_limiter, retry_after)


@pytest.fixture
def clock():
    now = [0.0]

    class Clock:
        def __call__(self):
            return now[0]

        def advance(self, seconds):
            now[0] += seconds
    return Clock()


def test_bucket_trips_after_capacity(clock):
    limiter = KeyedLimiter(3, 1.0, clock=clock)
    assert [limiter.take("a") for _ in range(3)] == [0.0, 0.0, 0.0]
    wait = limiter.take("a")
    assert 0 < wait <= 1


def test_other_key_is_unaffected(clock):
    limiter = KeyedLimiter(2, 1.0, clock=clock)
    limiter.take("a"), limiter.take("a")
    assert limiter.take("a") > 0
    assert limiter.take("b") == 0.0


def test_refill_follows_the_clock(clock):
    limiter = KeyedLimiter(2, 1.0, clock=clock)
    limiter.take("a"), limiter.take("a")
    clock.advance(1.0)
    assert limiter.take("a") == 0.0
    assert limiter.take("a") > 0


def test_blocked_takes_nothing(clock):
    limiter = KeyedLimiter(3, 1.0, clock=clock)
    assert [limiter.blocked("a") for _ in range(10)] == [0.0] * 10
    assert [limiter.take("a") for _ in range(3)] == [0.0] * 3
    assert limiter.blocked("a") > 0
    assert limiter.take("a") > 0


def test_table_is_bounded(clock):
    limiter = KeyedLimiter(2, 0.0, max_keys=4, clock=clock)
    for key in range(10):
        limiter.take(str(key))
        limiter.take(str(key))
    assert len(limiter) == 4
    assert limiter.take("0") == 0.0        # evicted, so it comes back full


def test_recently_touched_key_survives_eviction(clock):
    limiter = KeyedLimiter(1, 0.0, max_keys=2, clock=clock)
    limiter.take("a")
    limiter.take("b")
    limiter.blocked("a")                   # touch a; b is now the least recent
    limiter.take("c")
    assert limiter.take("a") > 0           # a kept its drained bucket
    assert limiter.take("b") == 0.0        # b was evicted


def test_thread_safety():
    limiter = KeyedLimiter(100, 0.0)
    grants = []
    lock = threading.Lock()

    def work():
        mine = sum(1 for _ in range(1000) if limiter.take("k") == 0.0)
        with lock:
            grants.append(mine)
    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(grants) == 100


def test_factories(tmp_path, clock):
    limiter = principal_limiter(ServeConfig(root=tmp_path, rate_per_minute=60, rate_burst=2), clock=clock)
    assert [limiter.take("k") for _ in range(2)] == [0.0, 0.0]
    assert limiter.take("k") > 0
    clock.advance(1.0)
    assert limiter.take("k") == 0.0
    assert limiter.take("k") > 0
    budget = failure_budget(clock=clock)
    assert sum(1 for _ in range(25) if budget.take("p") == 0.0) == 20
    inflight = principal_inflight(ServeConfig(root=tmp_path, max_inflight=3))
    assert [inflight.acquire("k") for _ in range(4)] == [True, True, True, False]


def test_zero_refill_returns_inf_and_retry_after_clamps(clock):
    limiter = KeyedLimiter(1, 0.0, clock=clock)
    assert limiter.take("a") == 0.0
    assert limiter.take("a") == math.inf
    assert limiter.blocked("a") == math.inf
    assert retry_after(math.inf) == 3600
    assert retry_after(0.2) == 1
    assert retry_after(2.1) == 3
    assert retry_after(10 ** 9) == 3600
    assert retry_after(0.0) == 1


def test_inflight_caps_per_key_and_releases():
    inflight = InFlight(2)
    assert inflight.acquire("a") and inflight.acquire("a")
    assert not inflight.acquire("a")
    assert inflight.count("a") == 2
    assert inflight.acquire("b")
    inflight.release("a")
    assert inflight.acquire("a")
    for _ in range(2):
        inflight.release("a")
    inflight.release("b")
    assert inflight.count("a") == 0 and len(inflight) == 0


def test_inflight_thread_safety():
    peak = [0]

    class Recording(InFlight):
        def _admitted(self, key, count):
            peak[0] = max(peak[0], count)
            assert count <= 4

    inflight = Recording(4)

    def work():
        for _ in range(1000):
            if inflight.acquire("k"):
                inflight.release("k")
    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert 1 <= peak[0] <= 4
    assert inflight.count("k") == 0


@pytest.mark.parametrize("addr, key", [
    ("203.0.113.9", "203.0.113.9"),
    ("2001:db8:1:2:aaaa::1", "2001:db8:1:2::/64"),
    ("2001:db8:1:2:ffff::9", "2001:db8:1:2::/64"),
    ("2001:db8:1:3::1", "2001:db8:1:3::/64"),
    ("::ffff:203.0.113.9", "203.0.113.9"),
    ("-", "-"),
    ("not-an-ip", "not-an-ip"),
])
def test_peer_key(addr, key):
    assert peer_key(addr) == key


def test_a_cost_takes_all_or_nothing(clock):
    limiter = KeyedLimiter(5, 1.0, clock=clock)
    assert limiter.take("a", cost=3) == 0.0
    wait = limiter.take("a", cost=3)          # 2 left: nothing taken, the shortfall reported
    assert wait == pytest.approx(1.0)
    assert limiter.take("a", cost=2) == 0.0
    assert limiter.take("a") > 0


def test_a_cost_above_capacity_can_never_be_met(clock):
    limiter = KeyedLimiter(4, 1.0, clock=clock)
    assert math.isinf(limiter.take("a", cost=5))
    assert limiter.take("a", cost=4) == 0.0   # and it took nothing meanwhile


def test_default_cost_is_one(clock):
    limiter = KeyedLimiter(2, 1.0, clock=clock)
    assert [limiter.take("a"), limiter.take("a")] == [0.0, 0.0]
    assert limiter.take("a") > 0
