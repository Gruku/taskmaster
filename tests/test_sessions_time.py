import pytest
from datetime import datetime, timezone


def test_handover_time_prefers_created_over_date():
    from taskmaster_v3 import _handover_time

    h = {
        "id": "2026-05-19-foo",
        "date": "2026-05-19",
        "created": "2026-05-19T14:23:45.123456+00:00",
    }
    t = _handover_time(h)
    assert t == datetime(2026, 5, 19, 14, 23, 45, 123456, tzinfo=timezone.utc)


def test_handover_time_falls_back_to_date_when_created_missing():
    from taskmaster_v3 import _handover_time

    h = {"id": "2026-04-26-legacy", "date": "2026-04-26T16:40:00Z"}
    t = _handover_time(h)
    assert t == datetime(2026, 4, 26, 16, 40, 0, tzinfo=timezone.utc)


def test_handover_time_falls_back_to_date_only_string():
    from taskmaster_v3 import _handover_time

    h = {"id": "2026-05-13-legacy", "date": "2026-05-13"}
    t = _handover_time(h)
    # Date-only parses as midnight UTC; that's the legacy behaviour we tag as such.
    assert t == datetime(2026, 5, 13, 0, 0, 0, tzinfo=timezone.utc)


def test_handover_time_raises_when_no_date_or_created():
    from taskmaster_v3 import _handover_time

    with pytest.raises(ValueError, match="neither 'created' nor 'date'"):
        _handover_time({"id": "2026-05-19-broken"})
