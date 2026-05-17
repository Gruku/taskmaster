from plugins.taskmaster.taskmaster_v3 import backfill_tldr


def test_backfill_tldr_adds_when_missing():
    fm = {"id": "T-001", "title": "Refactor auth"}
    body = "Refactor auth middleware. Steps follow."
    new_fm, changed = backfill_tldr(fm, body)
    assert changed is True
    assert new_fm["tldr"] == "Refactor auth middleware."
    assert new_fm["tldr_autogen"] is True


def test_backfill_tldr_skips_when_present():
    fm = {"id": "T-001", "title": "Refactor auth", "tldr": "Existing tldr."}
    body = "Some body."
    new_fm, changed = backfill_tldr(fm, body)
    assert changed is False
    assert new_fm["tldr"] == "Existing tldr."
    assert "tldr_autogen" not in new_fm


def test_backfill_tldr_uses_title_when_body_empty():
    fm = {"id": "T-001", "title": "Refactor auth middleware"}
    new_fm, changed = backfill_tldr(fm, "")
    assert changed is True
    assert new_fm["tldr"] == "Refactor auth middleware"
    assert new_fm["tldr_autogen"] is True
