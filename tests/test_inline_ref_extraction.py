from taskmaster.taskmaster_v3 import extract_inline_refs


def test_extract_bare_task_id():
    assert extract_inline_refs("Working on T-001 now.") == ["T-001"]


def test_extract_bare_issue_and_handover_and_idea():
    body = "Picked up T-001 to fix ISS-007; see HND-012 and IDEA-005."
    assert set(extract_inline_refs(body)) == {"T-001", "ISS-007", "HND-012", "IDEA-005"}


def test_extract_wiki_style():
    assert extract_inline_refs("Picked up [[T-001]] and [[ISS-007]].") == ["T-001", "ISS-007"]


def test_extract_mention_style():
    assert extract_inline_refs("Cc @T-001, blocked by @ISS-007.") == ["T-001", "ISS-007"]


def test_extract_dedupes():
    body = "T-001 is great. Again: T-001. Also [[T-001]] and @T-001."
    assert extract_inline_refs(body) == ["T-001"]


def test_extract_preserves_first_seen_order():
    body = "First ISS-007, then T-001, then HND-012, then T-001 again."
    assert extract_inline_refs(body) == ["ISS-007", "T-001", "HND-012"]


def test_extract_ignores_lowercase_and_partials():
    # Lowercase prefixes are not valid IDs.
    assert extract_inline_refs("t-001 and iss-7 should not match.") == []
    # Numbers without prefix don't match.
    assert extract_inline_refs("Refs 001 or 7.") == []


def test_extract_ignores_id_inside_word():
    # "noT-001" is not a valid ID mention — must be preceded by start/whitespace/punct.
    assert extract_inline_refs("This is noT-001 prefix.") == []


def test_extract_excludes_self_reference():
    assert extract_inline_refs("Self-ref to T-001 here.", self_id="T-001") == []


def test_extract_empty_body():
    assert extract_inline_refs("") == []
    assert extract_inline_refs(None) == []


# B-035 tests: date-slug handover ID extraction
def test_extract_matches_date_slug_handover_id():
    result = extract_inline_refs("See 2026-05-01-my-handover for context.")
    assert "2026-05-01-my-handover" in result


def test_extract_ignores_bare_date_and_timestamp():
    assert extract_inline_refs("shipped on 2026-05-01 today") == []
    assert extract_inline_refs("ran at 2026-05-01T20:38:15Z ok") == []


def test_extract_prefixed_id_still_works_after_b035():
    # Confirm an existing prefixed-ID test still passes.
    assert extract_inline_refs("Working on T-001 now.") == ["T-001"]
