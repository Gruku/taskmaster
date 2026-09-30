"""User intent: the change cursor is the one thing standing between a resuming
agent and a silent replay, so its fence is pinned directly — what it binds to,
what it refuses, and which typed reason each refusal carries.
"""
from __future__ import annotations

import base64
import json

import pytest

from taskmaster.native import cursors

STORE = {"store_id": "store-a", "source_digest": "digest-a"}


def scope(kinds=None, ids=None, epic="", group_commits=True):
    return cursors.scope(kinds, ids, epic, group_commits)


def issue(last_seq=41, **over):
    args = {**STORE, "scope": scope(), "last_seq": last_seq, **over}
    return cursors.issue(**args)


def parse(token, **over):
    # A store whose history reaches well past every cursor these tests issue.
    args = {**STORE, "scope": scope(), "sequence": 1000, **over}
    return cursors.parse(token, **args)


def test_a_cursor_round_trips_its_sequence_within_one_store_and_scope():
    assert parse(issue(41)) == 41
    assert parse(issue(0)) == 0


def test_a_cursor_is_opaque_and_bounded():
    token = issue(41)
    assert token.isascii() and "{" not in token and "41" not in token
    assert len(token) <= cursors.MAX_LENGTH


def test_a_write_does_not_invalidate_a_cursor():
    """The whole job of this cursor is to survive writes.

    The entity list cursor binds to `event_high_water` on purpose; this one must
    not, or every call in an active project would answer `resync_required`.
    """
    token = issue(41)
    payload = json.loads(base64.b64decode(token.encode(), altchars=b"-_", validate=True))
    assert "event_high_water" not in json.dumps(payload)
    assert parse(token) == 41


def test_a_cursor_from_another_store_or_a_rebuilt_one_reports_store_rebuilt():
    token = issue(41)
    with pytest.raises(cursors.StoreRebuilt) as other:
        parse(token, store_id="store-b")
    assert other.value.reason == "store_rebuilt"
    with pytest.raises(cursors.StoreRebuilt):
        parse(token, source_digest="digest-b")


def test_a_cursor_from_a_different_scope_reports_scope_changed():
    token = issue(41)
    for narrowed in (scope(kinds=["task"]), scope(ids=["N09-2"]), scope(epic="native"),
                     scope(group_commits=False)):
        with pytest.raises(cursors.ScopeChanged) as exc:
            parse(token, scope=narrowed)
        assert exc.value.reason == "scope_changed"


def test_scope_is_fingerprinted_by_value_not_by_the_order_a_caller_listed_it():
    assert cursors.fingerprint(scope(kinds=["task", "bug"])) == cursors.fingerprint(scope(kinds=["bug", "task"]))
    assert cursors.fingerprint(scope(kinds=["task"])) != cursors.fingerprint(scope(kinds=["bug"]))
    assert cursors.fingerprint(scope(ids=["a", "a"])) == cursors.fingerprint(scope(ids=["a"]))


def test_a_cursor_below_the_retention_floor_reports_history_expired():
    token = issue(41)
    assert parse(token, floor=41) == 41
    with pytest.raises(cursors.HistoryExpired) as exc:
        parse(token, floor=42)
    assert exc.value.reason == "history_expired"


def test_a_tampered_missing_or_oversized_cursor_reports_cursor_unreadable():
    token = issue(41)
    for broken in ("", "not-base64!!", token[:-4], token + "AAAA", "x" * (cursors.MAX_LENGTH + 1), None, 41):
        with pytest.raises(cursors.CursorUnreadable) as exc:
            parse(broken)
        assert exc.value.reason == "cursor_unreadable"


def test_a_cursor_of_another_version_or_shape_is_unreadable():
    for payload in ({"v": cursors.VERSION + 1, "store": ["store-a", "digest-a"], "scope": cursors.fingerprint(scope()), "seq": 41},
                    {"v": cursors.VERSION, "store": ["store-a"], "scope": cursors.fingerprint(scope()), "seq": 41},
                    {"v": cursors.VERSION, "store": ["store-a", "digest-a"], "scope": "short", "seq": 41},
                    {"v": cursors.VERSION, "store": ["store-a", "digest-a"], "scope": cursors.fingerprint(scope()), "seq": -1},
                    {"v": cursors.VERSION, "store": ["store-a", "digest-a"], "scope": cursors.fingerprint(scope()), "seq": True}):
        token = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        with pytest.raises(cursors.CursorUnreadable):
            parse(token)


def test_every_refusal_reason_is_one_of_the_declared_set():
    assert set(cursors.REASONS) == {"cursor_unreadable", "store_rebuilt", "history_rewound", "scope_changed",
                                    "history_expired"}
    for cls in (cursors.CursorUnreadable, cursors.StoreRebuilt, cursors.HistoryRewound, cursors.ScopeChanged,
                cursors.HistoryExpired):
        assert issubclass(cls, cursors.CursorInvalid) and cls.reason in cursors.REASONS


def test_a_rebuilt_store_is_reported_before_a_changed_scope_or_an_expired_floor():
    """Reason precedence is pinned: the most fundamental condition wins, so an
    agent whose store was rebuilt is never told merely to re-scope."""
    token = issue(41)
    with pytest.raises(cursors.StoreRebuilt):
        parse(token, store_id="store-b", scope=scope(kinds=["task"]), floor=99)
    with pytest.raises(cursors.ScopeChanged):
        parse(token, scope=scope(kinds=["task"]), floor=99)


def test_scope_refuses_an_unknown_kind_an_oversized_filter_and_a_bad_type():
    with pytest.raises(ValueError, match="kind"):
        scope(kinds=["nonsense"])
    with pytest.raises(ValueError, match="kinds"):
        scope(kinds="task")
    with pytest.raises(ValueError, match="ids"):
        scope(ids=[str(n) for n in range(cursors.MAX_FILTER + 1)])
    with pytest.raises(ValueError, match="epic"):
        scope(epic="x" * 300)


def test_the_normal_answer_carries_a_fresh_cursor_and_no_resync():
    answer = cursors.feed(store_id="store-a", source_digest="digest-a", sequence=100,
                          scope=scope(), items=[{"commit_seq": 9}], last_seq=9, more=True,
                          group_commits=True)
    assert answer["store_id"] == "store-a" and answer["sequence"] == 100
    assert answer["commits"] == [{"commit_seq": 9}] and answer["more"] is True
    assert answer["resync_required"] is False and answer["reason"] is None
    assert parse(answer["cursor"]) == 9


def test_a_flat_answer_names_its_items_changes_not_commits():
    answer = cursors.feed(store_id="store-a", source_digest="digest-a", sequence=100,
                          scope=scope(group_commits=False), items=[], last_seq=7, more=False,
                          group_commits=False)
    assert "commits" not in answer and answer["changes"] == []


def test_a_resync_answer_reports_the_reason_and_a_cursor_at_the_current_sequence():
    """Never an error and never a silent replay: an agent recovers in one call,
    and the fresh cursor starts from now so history is not re-acted on."""
    exc = cursors.HistoryExpired("gone")
    answer = cursors.resync(exc, store_id="store-a", source_digest="digest-a", sequence=100,
                            scope=scope(), group_commits=True)
    assert answer["resync_required"] is True and answer["reason"] == "history_expired"
    assert answer["commits"] == [] and answer["more"] is False
    assert parse(answer["cursor"]) == 100


# ── A cursor the store's history cannot reach (review B, item 6) ────────────


def test_a_cursor_ahead_of_the_stores_history_reports_history_rewound():
    """A store restored from a backup keeps its identity but loses the tail of
    its history. A cursor issued before the restore then points past the end,
    and resuming it would silently skip every event written into the gap."""
    token = issue(41)
    assert parse(token, sequence=41) == 41
    with pytest.raises(cursors.HistoryRewound):
        parse(token, sequence=40)


def test_a_rewound_history_is_reported_after_a_rebuilt_store_and_before_scope_or_floor():
    token = issue(41)
    with pytest.raises(cursors.StoreRebuilt):
        parse(token, store_id="store-b", sequence=40)
    with pytest.raises(cursors.HistoryRewound):
        parse(token, sequence=40, scope=scope(kinds=["task"]), floor=30)


def test_a_resync_cursor_is_usable_even_when_the_floor_is_above_the_high_water_mark():
    """Issued at the current sequence below the floor, the recovery cursor would
    itself be expired, and every call would answer another resync."""
    answer = cursors.resync(cursors.HistoryExpired("gone"), store_id="store-a", source_digest="digest-a",
                            sequence=10, floor=15, scope=scope(), group_commits=True)
    assert parse(answer["cursor"], sequence=10, floor=15) == 15


def test_a_start_from_now_cursor_is_usable_even_when_the_floor_is_above_the_high_water_mark():
    assert cursors.resume_point(10, 15) == 15 and cursors.resume_point(20, 15) == 20
