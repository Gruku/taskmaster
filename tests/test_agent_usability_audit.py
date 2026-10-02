"""User intent: the agent tool-use pilot (tm-audit-030) showed where agents were misled
or left to wander — an open line of work vanishing from the thread board, a stale "last
session", no way to ask "what changed since X", search hits with no hint of where they
matched. Each fix must hold on the legacy store and on its native copy alike, and must not
flood or slow a store that carries hundreds of old open handovers.
"""
from __future__ import annotations

from contextlib import closing
import datetime as _datetime
import json
import sqlite3

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store
from native_twins import CLOCK, hand_edit_entity, make_twins

ATLAS = "2026-09-17-atlas-budget-settled"
IMPORTED = "2026-09-17-filler-handover-10"
UTC = _datetime.timezone.utc


def _as_bootstrap_import(ident: str, created: str) -> None:
    """Make one seeded entity look the way a store imported from files holds it: its
    first change-log row is the bootstrap import, stamped whenever the import ran,
    and only the document's own `created` says when it was written."""
    store.reset_for_tests()
    with closing(sqlite3.connect(bs.ROOT / ".taskmaster" / "local" / "store.db", isolation_level=None)) as connection:
        connection.execute("UPDATE changes SET op='import', tool='bootstrap' WHERE seq="
                           "(SELECT MIN(seq) FROM changes WHERE id=?)", (ident,))
    hand_edit_entity("handover", ident, lambda doc: doc.__setitem__("created", created))


def _seed():
    bs.backlog_add_task(title="First", epic="test-epic", phase="dev")
    bs.backlog_note(action="create", text="Seeded note", pinned=False)
    # The oldest handover: thirty newer ones push it out of the 30-entry index
    # while it is still the open resume point of its thread.
    bs.backlog_handover_create(
        tldr="Atlas budget settled", thread="atlas-budget", next_action="Split the foliage atlas",
        body="## Decisions\nThe schema version lives in the manifest header because loaders read it first.\n"
             "| where | `manifest\n```\n")
    for n in range(30):
        bs.backlog_handover_create(tldr=f"Filler handover {n:02d}", thread=f"filler-{n:02d}")
    # Seeding ends well before 13:00 on the twins' clock; the tests write after it.
    _as_bootstrap_import(IMPORTED, "2026-09-17T13:00:00.000000+00:00")
    bs.backlog_handover_resync()  # the stored index is ordered by `created`


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


MIXED_OPEN = "2026-09-17-mixed-work-still-open"
INNER_OPEN = "2026-09-17-inner-still-open"
DIGITS = "2026-09-17-0100"


def _seed_mixed():
    """Threads whose newest handover is closed while an older one is still open: `mixed`
    with the open one archived, `inner` with both in the index. The open ones are pinned
    by hand, which is what keeps a later handover in the thread from superseding them."""
    bs.backlog_handover_create(tldr="Mixed work still open", thread="mixed", next_action="Finish the open part")
    bs.backlog_handover_update_status(handover_id=MIXED_OPEN, status="open", reason="still going")
    bs.backlog_handover_create(tldr="0100", thread="digits")
    for n in range(2):
        bs.backlog_handover_create(tldr=f"Filler handover {n:02d}", thread=f"filler-{n:02d}")
    bs.backlog_handover_create(tldr="Inner still open", thread="inner", next_action="Finish the inner part")
    bs.backlog_handover_update_status(handover_id=INNER_OPEN, status="open", reason="still going")
    for n in range(2, 29):
        bs.backlog_handover_create(tldr=f"Filler handover {n:02d}", thread=f"filler-{n:02d}")
    for thread in ("inner", "mixed"):
        bs.backlog_handover_create(tldr=f"{thread.title()} side note", thread=thread, next_action="Nothing")
        bs.backlog_handover_update_status(handover_id=f"2026-09-17-{thread}-side-note", status="closed", reason="done")


@pytest.fixture
def mixed(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed_mixed)


# ── An open thread outside the handover index ───────────────────────────────


def test_the_default_board_counts_archived_only_threads_instead_of_listing_them(twins):
    listed, _native = twins.same("backlog_handover_list", limit=0)
    assert ATLAS not in listed and "1 older handovers" in listed  # the premise
    board, _native = twins.same("backlog_thread_list")
    assert "atlas-budget" not in board and "**filler-29**" in board
    assert "1 more thread has only archived handovers" in board and "include_archived=True" in board


def test_the_board_lists_archived_only_threads_on_request(twins):
    board, _native = twins.same("backlog_thread_list", include_archived=True)
    assert "**atlas-budget** [archived]" in board and "next: Split the foliage atlas" in board
    assert "more thread" not in board


def test_resume_reaches_a_thread_whose_only_open_handover_is_archived(twins):
    resumed, _native = twins.same("backlog_thread_resume", ref="atlas-budget")
    assert f"- newest: {ATLAS}" in resumed and "manifest header" in resumed


def test_a_thread_outside_the_index_can_be_parked(twins):
    answer, _native = twins.same("backlog_thread_update", name="atlas-budget", status="parked", reason="later")
    assert answer.startswith("Thread atlas-budget → parked.")
    board, _native = twins.same("backlog_thread_list", include_archived=True)
    assert "**atlas-budget** [parked] [archived]" in board
    twins.assert_state_matches()
    twins.assert_files_match()


def test_a_closed_archived_handover_does_not_bring_its_thread_back(twins):
    twins.same("backlog_handover_update_status", handover_id=ATLAS, status="closed", reason="done")
    board, _native = twins.same("backlog_thread_list", include_closed=True, include_archived=True)
    assert "atlas-budget" not in board and "more thread" not in board


def test_a_superseded_archived_handover_does_not_bring_its_thread_back(twins):
    twins.same("backlog_handover_supersede", old_id=ATLAS, new_id="2026-09-17-filler-handover-29")
    board, _native = twins.same("backlog_thread_list", include_closed=True, include_archived=True)
    assert "atlas-budget" not in board and "more thread" not in board


def _stored_threads(root):
    store.reset_for_tests()
    with closing(sqlite3.connect(root / ".taskmaster" / "local" / "store.db")) as connection:
        return json.loads(connection.execute(
            "SELECT doc FROM entities WHERE kind='backlog' AND id='__backlog__'").fetchone()[0])["threads"]


def test_the_stored_registry_never_holds_archived_only_threads(twins):
    """Every write re-renders the document the registry lives in, so a store with
    hundreds of old open handovers must not carry a thread for each."""
    assert "atlas-budget" not in _stored_threads(twins.legacy) and "atlas-budget" not in (
        twins.legacy / ".taskmaster" / "backlog.yaml").read_text(encoding="utf-8")
    twins.same("backlog_thread_update", name="atlas-budget", status="parked", reason="later")
    assert "atlas-budget" not in _stored_threads(twins.legacy)
    twins.same("backlog_handover_create", tldr="One more", thread="one-more")
    assert "atlas-budget" not in _stored_threads(twins.legacy)
    twins.assert_files_match()
    board, _native = twins.same("backlog_thread_list", include_archived=True)
    assert "**atlas-budget** [parked] [archived]" in board  # the override outlived the write


def test_the_viewers_thread_rows_leave_archived_only_threads_out(twins):
    from taskmaster.taskmaster_v3 import list_threads
    with twins.at(twins.legacy):
        data = bs._threads_data(bs._backlog_path())
    assert "atlas-budget" not in [row["name"] for row in list_threads(data)]
    assert [row["name"] for row in list_threads(data, archived_only=True) if row.get("archived_only")] == ["atlas-budget"]


# ── A thread whose newest handover is closed and an older one is open ───────


def test_an_open_thread_is_described_by_its_newest_open_handover(mixed):
    board, _native = mixed.same("backlog_thread_list")
    lines = board.splitlines()
    for name, tldr, step in (("mixed", "Mixed work still open", "Finish the open part"),
                             ("inner", "Inner still open", "Finish the inner part")):
        at = next(i for i, line in enumerate(lines) if line.startswith(f"- **{name}**"))
        assert tldr in lines[at] and "[archived]" not in lines[at] and lines[at + 1] == f"  next: {step}"


def test_resume_returns_the_newest_open_handover_of_an_open_thread(mixed):
    for name, newest in (("mixed", MIXED_OPEN), ("inner", INNER_OPEN)):
        resumed, _native = mixed.same("backlog_thread_resume", ref=name)
        assert f"- newest: {newest}" in resumed and "- status: open" in resumed
    # Any handover of the thread, the closed newest one included, lands on the same place.
    resumed, _native = mixed.same("backlog_thread_resume", ref="2026-09-17-mixed-side-note")
    assert f"- newest: {MIXED_OPEN}" in resumed


def test_a_thread_with_an_indexed_handover_is_never_counted_as_archived_only(mixed):
    board, _native = mixed.same("backlog_thread_list")
    assert "3 more threads have only archived handovers" in board  # digits, filler-00, filler-01


def _as_an_older_build_stored_it(mixed, change):
    """The legacy store's registry, hand-set to what a build before the fix wrote."""
    with mixed.at(mixed.legacy):
        hand_edit_entity("backlog", "__backlog__", lambda doc: change(doc["threads"]))


def test_a_legacy_read_does_not_trust_a_registry_stored_before_the_fix(mixed):
    def stale(threads):
        threads["mixed"].update(status="closed", tldr="Mixed side note", next_action="Nothing")
        threads["mixed"].pop("resume", None)
    _as_an_older_build_stored_it(mixed, stale)
    with mixed.at(mixed.legacy):
        assert "- **mixed** — Mixed work still open" in bs.backlog_thread_list()
        assert f"- newest: {MIXED_OPEN}" in bs.backlog_thread_resume("mixed")


def test_a_legacy_thread_update_does_not_trust_a_registry_stored_before_the_fix(mixed):
    _as_an_older_build_stored_it(mixed, lambda threads: threads.pop("inner"))
    with mixed.at(mixed.legacy):
        assert bs.backlog_thread_update(name="inner", status="parked").startswith("Thread inner → parked.")
        assert "- **inner** [parked]" in bs.backlog_thread_list()


# ── A resume that misses ────────────────────────────────────────────────────


def test_a_resume_miss_names_the_closest_threads(twins):
    missed, _native = twins.same("backlog_thread_resume", ref="atlas")
    assert missed.startswith("No thread or handover matches 'atlas'.")
    assert "atlas-budget (archived)" in missed and "filler-00" not in missed


def test_a_resume_miss_ranks_by_similarity_not_board_order(twins):
    # Thirty threads share the word `filler`; the misspelt one shares two and nearly matches.
    missed, _native = twins.same("backlog_thread_resume", ref="filler-atlas-budgte")
    assert missed.split("Closest threads: ")[1].startswith("atlas-budget")
    assert missed.count("filler-") <= 6  # the ref once, then at most five suggestions


def test_a_resume_miss_finds_a_near_spelling_that_shares_no_word(twins):
    missed, _native = twins.same("backlog_thread_resume", ref="atlasbudget")
    assert "Closest threads: atlas-budget (archived)." in missed


def test_a_resume_miss_marks_closed_threads(twins):
    twins.same("backlog_thread_update", name="filler-07", status="closed")
    missed, _native = twins.same("backlog_thread_resume", ref="filler-7")
    assert "filler-07 (closed)" in missed


def test_a_resume_miss_with_nothing_close_lists_the_boards_open_threads(twins):
    missed, _native = twins.same("backlog_thread_resume", ref="zzz-nothing-like-it")
    assert "Open threads:" in missed and "filler-29" in missed and "atlas-budget" not in missed
    assert "backlog_thread_list" in missed  # the list is capped; the board is the whole answer


# ── The dashboard points at the thread board ────────────────────────────────


def test_the_dashboard_points_at_the_thread_board(twins):
    dashboard, _native = twins.same("backlog_status")
    assert "backlog_thread_list" in dashboard
    assert "| Workstream |" not in dashboard  # epics are not the lines of work


# ── The last session, and whether it is the newest work ─────────────────────


def _progress(twins, text):
    for root in (twins.legacy, twins.native):
        (root / ".taskmaster" / "local" / "PROGRESS.md").write_text(text, encoding="utf-8")


def test_last_session_says_when_handovers_are_newer_than_the_entry(twins):
    _progress(twins, "# Progress\n\n## Changelog\n\n### 2026-09-15 — Session A\n- a\n\n### 2026-09-14 — Older\n- b\n")
    answer, _native = twins.same("backlog_last_session")
    assert "### 2026-09-15 — Session A" in answer
    # Every handover counts, the archived one included: the index cap is not the answer.
    assert "31 handovers are newer than this entry" in answer
    assert "2026-09-17-filler-handover-29" in answer and "backlog_handover_list" in answer


def test_last_session_adds_nothing_when_no_handover_is_newer(twins):
    _progress(twins, "# Progress\n\n## Changelog\n\n### 2026-09-17 — Today\n- a\n")
    answer, _native = twins.same("backlog_last_session")
    assert answer == "**Last Session:**\n\n### 2026-09-17 — Today\n- a"


def test_last_session_reads_the_latest_date_of_a_heading_that_spans_several(twins):
    _progress(twins, "## Changelog\n\n### Sessions 2026-09-10 → 2026-09-17\n- a\n")
    answer, _native = twins.same("backlog_last_session")
    assert "newer than this entry" not in answer
    _progress(twins, "## Changelog\n\n### Sessions 2026-09-10 → 2026-09-16\n- a\n")
    answer, _native = twins.same("backlog_last_session")
    assert "31 handovers are newer than this entry" in answer


def test_last_session_claims_nothing_for_a_heading_with_no_date(twins):
    _progress(twins, "## Changelog\n\n### The big cleanup\n- a\n")
    answer, _native = twins.same("backlog_last_session")
    assert answer == "**Last Session:**\n\n### The big cleanup\n- a"


# ── What changed since a time or an entity ──────────────────────────────────


def _changes(twins, root, **kwargs):
    with twins.at(root):
        return json.loads(bs.backlog_changes_since(**kwargs))


def _moved(answer):
    return [(c["kind"], c["id"], c["op"]) for commit in answer["commits"] for c in commit["changes"]]


def _later_note(twins, root, text="Written later"):
    """One note written at 14:00, after everything seeded and after `IMPORTED`'s `created`."""
    CLOCK.update(at=_datetime.datetime(2026, 9, 17, 14, 0, tzinfo=UTC), tick=True)
    with twins.at(root):
        bs.backlog_note(action="create", text=text, pinned=False)


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_changes_since_an_entity_starts_right_after_it_was_created(twins, side):
    root = getattr(twins, side)
    with twins.at(root):
        bs.backlog_note(action="create", text="The anchor", pinned=False)
        bs.backlog_note(action="create", text="Written after the anchor", pinned=False)
    moved = _moved(_changes(twins, root, since="NOTE-002", limit=500))
    assert ("note", "NOTE-003", "create") in moved
    assert ("note", "NOTE-002", "create") not in moved and ("note", "NOTE-001", "create") not in moved


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_changes_since_an_imported_entity_anchors_on_when_it_was_written(twins, side):
    """Its first change-log row is the import, in the middle of the seeded history; its
    `created` is 13:00. Only what was written after 13:00 changed since it."""
    root = getattr(twins, side)
    _later_note(twins, root)
    assert _moved(_changes(twins, root, since=IMPORTED, limit=500)) == [("note", "NOTE-002", "create")]


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_changes_since_a_timestamp_starts_at_the_first_change_at_or_after_it(twins, side):
    root = getattr(twins, side)
    _later_note(twins, root)
    assert _moved(_changes(twins, root, since="2026-09-17T13:30:00+00:00", limit=500)) == [("note", "NOTE-002", "create")]


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_a_naive_since_is_read_as_local_time(twins, side, monkeypatch):
    root = getattr(twins, side)
    _later_note(twins, root)
    # A caller five hours east of UTC: 18:30 on its clock is 13:30 UTC, before the note.
    monkeypatch.setattr(bs, "_as_local", lambda moment: moment.replace(tzinfo=_datetime.timezone(
        _datetime.timedelta(hours=5))))
    assert _moved(_changes(twins, root, since="2026-09-17T18:30", limit=500)) == [("note", "NOTE-002", "create")]
    assert _moved(_changes(twins, root, since="2026-09-17T19:30", limit=500)) == []


def _is_bootstrap(change):
    return change[2] == "import"


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_changes_since_a_date_leaves_the_store_import_out_and_says_so(twins, side):
    root = getattr(twins, side)
    everything = _moved(_changes(twins, root, since_seq=0, limit=500))
    imports = [change for change in everything if _is_bootstrap(change)]
    assert ("handover", IMPORTED, "import") in imports  # the premise; since_seq still reports it
    answer = _changes(twins, root, since="2020-01-01", limit=500)
    assert _moved(answer) == [change for change in everything if not _is_bootstrap(change)]
    assert answer["note"].startswith(f"{len(imports)} store-import row")
    assert _moved(_changes(twins, root, since="2999-01-01", limit=500)) == []


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_paging_on_from_a_since_answer_keeps_the_store_import_out(twins, side):
    root = getattr(twins, side)
    whole = _moved(_changes(twins, root, since="2020-01-01", limit=500))
    paged, page = [], _changes(twins, root, since="2020-01-01", limit=7)
    while True:
        paged += _moved(page)
        if not page["more"]:
            break
        page = _changes(twins, root, cursor=page["cursor"], limit=7)
    assert paged == whole and not any(_is_bootstrap(change) for change in paged)


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_a_cursor_from_a_plain_call_still_reports_the_store_import(twins, side):
    root = getattr(twins, side)
    first = _changes(twins, root, since_seq=0, limit=1)
    rest, page = _moved(first), first
    while page["more"]:
        page = _changes(twins, root, cursor=page["cursor"], limit=500)
        rest += _moved(page)
    assert ("handover", IMPORTED, "import") in rest and "note" not in first


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_changes_since_refuses_an_anchor_it_cannot_place(twins, side):
    root = getattr(twins, side)
    unknown = _changes(twins, root, since="NOTE-999")
    assert set(unknown) == {"error"} and "NOTE-999" in unknown["error"]
    assert set(_changes(twins, root, since="NOTE-001", since_seq=0)) == {"error"}
    start = _changes(twins, root)
    assert set(_changes(twins, root, since="NOTE-001", cursor=start["cursor"])) == {"error"}


@pytest.mark.parametrize("side", ["legacy", "native"])
@pytest.mark.parametrize("moment", ["0001-01-01", "1969-01-01", "9999-12-31T23:59-10:00", "0001-01-01T00:00+10:00",
                                    "2026-13-45"])
def test_changes_since_answers_an_out_of_range_time_without_raising(twins, side, moment):
    answer = _changes(twins, getattr(twins, side), since=moment)
    assert set(answer) == {"error"} or "commits" in answer


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_an_entity_id_that_reads_like_a_timestamp_is_an_entity(mixed, side):
    """`2026-09-17-0100` parses as midnight at UTC-01:00; it is a handover's id."""
    root = getattr(mixed, side)
    moved = _moved(_changes(mixed, root, since=DIGITS, limit=500))
    assert ("handover", "2026-09-17-filler-handover-00", "create") in moved
    assert ("handover", MIXED_OPEN, "create") not in moved and ("handover", DIGITS, "create") not in moved


# ── Where a search hit matched ──────────────────────────────────────────────


def test_a_search_hit_in_the_body_alone_shows_the_matching_text(twins):
    found, _native = twins.same("backlog_search", query="loaders")
    lines = found.splitlines()
    hit = next(i for i, line in enumerate(lines) if ATLAS in line)
    assert lines[hit + 1].startswith("  matched: ")
    assert "manifest header because loaders read it first" in lines[hit + 1]
    assert len(lines) == 3  # the snippet is one line, whatever the prose held


def test_a_snippet_cannot_break_the_result_list(twins):
    found, _native = twins.same("backlog_search", query="loaders")
    snippet = found.splitlines()[2]
    assert "where" in snippet  # the table row and the fence are inside the window
    assert not any(mark in snippet for mark in ("`", "|", "#"))


def test_a_search_hit_in_the_title_adds_no_snippet(twins):
    found, _native = twins.same("backlog_search", query="settled")
    assert found.splitlines() == ["**1 match** for `settled`:", f"- `{ATLAS}` — Atlas budget settled (handover, open)"]


def test_a_failing_snippet_query_leaves_the_result_list_intact(twins, monkeypatch):
    monkeypatch.setattr(bs, "_SEARCH_SNIPPET_TOKENS", "not a number")
    found, _native = twins.same("backlog_search", query="loaders")
    assert found.splitlines() == ["**1 match** for `loaders`:", f"- `{ATLAS}` — Atlas budget settled (handover, open)"]
