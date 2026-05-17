from plugins.taskmaster.taskmaster_v3 import extract_tldr


def test_extract_tldr_from_first_sentence():
    body = "Fix the auth middleware. It currently stores tokens in localStorage."
    assert extract_tldr(body) == "Fix the auth middleware."


def test_extract_tldr_strips_markdown_headings():
    body = "## Why\n\nFix the auth middleware. More detail follows."
    assert extract_tldr(body) == "Fix the auth middleware."


def test_extract_tldr_caps_at_200_chars():
    long_sentence = "A" * 250 + "."
    result = extract_tldr(long_sentence)
    assert len(result) <= 200
    assert result.endswith("…")


def test_extract_tldr_empty_body_returns_none():
    assert extract_tldr("") is None
    assert extract_tldr("   \n\n   ") is None


def test_extract_tldr_collapses_whitespace():
    body = "Fix   the\nauth\n\nmiddleware."
    assert extract_tldr(body) == "Fix the auth middleware."
