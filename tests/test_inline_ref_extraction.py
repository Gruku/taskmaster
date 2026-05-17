from taskmaster_v3 import extract_inline_refs


def test_extract_bare_task_id():
    assert extract_inline_refs("Working on T-001 now.") == ["T-001"]


def test_extract_bare_issue_and_lesson_and_handover_and_idea():
    body = "Picked up T-001 to fix ISS-007 using L-003; see HND-012 and IDEA-005."
    assert set(extract_inline_refs(body)) == {"T-001", "ISS-007", "L-003", "HND-012", "IDEA-005"}


def test_extract_wiki_style():
    assert extract_inline_refs("Picked up [[T-001]] and [[ISS-007]].") == ["T-001", "ISS-007"]


def test_extract_mention_style():
    assert extract_inline_refs("Cc @T-001, blocked by @ISS-007.") == ["T-001", "ISS-007"]


def test_extract_dedupes():
    body = "T-001 is great. Again: T-001. Also [[T-001]] and @T-001."
    assert extract_inline_refs(body) == ["T-001"]


def test_extract_preserves_first_seen_order():
    body = "First ISS-007, then T-001, then L-003, then T-001 again."
    assert extract_inline_refs(body) == ["ISS-007", "T-001", "L-003"]


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
