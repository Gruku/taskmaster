# User intent: pin the team-identity rules every later team task relies on: how a git name becomes a
# handle, when a name is refused, how the machine-local identity.yaml round-trips, which operations
# need attribution, and the exact refusal wording that points the user at the bootstrap skill.
from __future__ import annotations

from datetime import datetime, timezone
import re

import pytest

from taskmaster import team_identity as ti
from taskmaster.team_identity import GitIdentity, IdentityRefused, TeamIdentity


# §3.3 rows with no profiles that are not refused. The handle column is what
# choose_handle(slug_for(...)) yields; only `Claude` differs from its slug (D7).
SLUG_ROWS = [
    ("John Doe", "", "jdoe"),
    ("Gruku", "", "gruku"),
    ("Mary Jane Watson", "", "mwatson"),
    ("Jean-Luc Picard", "", "jpicard"),
    ("José Ñúñez", "", "jnunez"),
    ("O'Brien", "", "obrien"),
    ("  JOHN   DOE ", "", "jdoe"),
    ("Straße Müller", "", "smuller"),
    ("A" * 40, "", "a" * 30),
    ("Володимир", "v.dmytriiev+tm@corp.io", "vdmytriiev"),
    ("Claude", "", "claude2"),
]


@pytest.mark.parametrize("name,email,handle", SLUG_ROWS)
def test_slug_examples(name, email, handle):
    slug = ti.slug_for(name, email)
    assert slug == ("claude" if handle == "claude2" else handle)
    assert ti.choose_handle(slug, ti.usable_email(email), {}) == (handle, False)
    assert ti.HANDLE_RE.fullmatch(handle)


def test_slug_is_empty_when_nothing_is_usable():
    assert ti.slug_for("李小龙", "") == ""
    assert ti.slug_for("Володимир", "you@example.com") == ""


@pytest.mark.parametrize("name", ["", "   ", "Your Name", "your  name", "YOUR NAME", "Name", "user", "root",
                                  "???", "-", "First Last"])
def test_degenerate_names(name):
    assert ti.is_degenerate(name) is True


@pytest.mark.parametrize("name", ["John Doe", "Gruku", "Володимир", "Test User"])
def test_real_names_are_not_degenerate(name):
    assert ti.is_degenerate(name) is False


def test_placeholder_emails_are_unusable():
    assert ti.usable_email("you@example.com") == ""
    assert ti.usable_email("") == ""
    assert ti.usable_email("nobody") == ""
    assert ti.usable_email("jd@corp.io") == "jd@corp.io"


def test_choose_handle_takes_a_free_slug():
    assert ti.choose_handle("jdoe", "jd@corp.io", {}) == ("jdoe", False)


def test_choose_handle_suffixes_on_a_clash_with_another_email():
    assert ti.choose_handle("jdoe", "jd@corp.io", {"jdoe": ["other@x.io"]}) == ("jdoe2", False)
    assert ti.choose_handle("jdoe", "jd@corp.io", {"jdoe": ["other@x.io"], "jdoe2": ["z@x.io"]}) == ("jdoe3", False)


def test_choose_handle_reuses_the_profile_holding_the_email():
    assert ti.choose_handle("jdoe", "jd@corp.io", {"jdoe": ["JD@corp.io"]}) == ("jdoe", True)
    assert ti.choose_handle("jdoe", "jd@corp.io", {"johnny": ["jd@corp.io"]}) == ("johnny", True)


def test_choose_handle_without_an_email_never_reuses():
    assert ti.choose_handle("jdoe", "", {"jdoe": []}) == ("jdoe2", False)


def test_reserved_slugs_take_a_suffix():
    assert ti.choose_handle("claude", "", {}) == ("claude2", False)
    assert ti.choose_handle("unassigned", "", {}) == ("unassigned2", False)


def test_choose_handle_refuses_past_the_last_suffix():
    profiles = {"jdoe": ["a@x.io"], **{f"jdoe{n}": [f"{n}@x.io"] for n in range(2, 100)}}
    with pytest.raises(IdentityRefused) as refused:
        ti.choose_handle("jdoe", "jd@corp.io", profiles)
    assert ti.BOOTSTRAP_SKILL in str(refused.value)
    assert "Nothing was written" in str(refused.value)


def test_new_identity_shape():
    git = GitIdentity("John Doe", "jd@corp.io")
    now = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    first = ti.new_identity("jdoe", git, clone_label="HOST", now=now)
    second = ti.new_identity("jdoe", git, clone_label="HOST", now=now)
    assert first.confirmed is False
    assert re.fullmatch(r"[0-9a-f]{16}", first.clone_id)
    assert first.clone_id != second.clone_id
    assert first.created == "2026-10-31T12:00:00Z" and first.created.endswith("Z")
    assert (first.git_name, first.git_email) == ("John Doe", "jd@corp.io")
    assert (first.handle, first.clone_label) == ("jdoe", "HOST")


def _identity():
    return TeamIdentity(handle="jdoe", clone_id="3f9a2c4e5b6d7e8f", clone_label="GRUKU-PC", confirmed=False,
                        created="2026-10-31T12:00:00Z", git_name="John Doe", git_email="jd@corp.io")


def test_identity_file_round_trips(tmp_path):
    backlog_dir = tmp_path / ".taskmaster"
    backlog_dir.mkdir()
    identity = _identity()
    ti.write_identity(backlog_dir, identity)
    assert ti.read_identity(backlog_dir) == identity
    path = backlog_dir / ti.IDENTITY_REL
    text = path.read_text(encoding="utf-8")
    assert "confirmed: false" in text
    assert text.startswith("# Taskmaster team identity for this clone. Machine-local: never synced or committed.\n")
    assert not list(path.parent.glob("identity.yaml.tmp.*"))


def test_read_identity_absent_is_none(tmp_path):
    assert ti.read_identity(tmp_path / ".taskmaster") is None


VALID = ("version: 1\nhandle: jdoe\nclone_id: 3f9a2c4e5b6d7e8f\nclone_label: HOST\nconfirmed: false\n"
         "created: '2026-10-31T12:00:00Z'\n")


@pytest.mark.parametrize("text", [
    "- a\n- b\n",
    VALID.replace("handle: jdoe", "handle: JDoe"),
    VALID.replace("clone_id: 3f9a2c4e5b6d7e8f\n", ""),
    VALID.replace("confirmed: false", "confirmed: 'no'"),
    VALID.replace("version: 1", "version: 2"),
])
def test_read_identity_rejects_invalid_documents(tmp_path, text):
    path = tmp_path / ".taskmaster" / ti.IDENTITY_REL
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        ti.read_identity(tmp_path / ".taskmaster")


def test_read_identity_accepts_the_minimal_document_and_unknown_keys(tmp_path):
    path = tmp_path / ".taskmaster" / ti.IDENTITY_REL
    path.parent.mkdir(parents=True)
    path.write_text(VALID + "future_key: 1\n", encoding="utf-8")
    identity = ti.read_identity(tmp_path / ".taskmaster")
    assert (identity.handle, identity.git_name, identity.git_email) == ("jdoe", "", "")


EXEMPT = ["sync.apply", "sync.begin", "sync.finish", "sync.job", "linear.claim", "linear.settle", "linear.link",
          "linear.unlink", "graph.repair", "projection.resolve", "task.claim_renew", "task.claim_release",
          "document.import", "profile.create"]


def test_attribution_bearing_operations():
    for operation in ("task.create", "task.patch", "note.create", "task.pick", "project.set", "handover.create",
                      "future.op"):
        assert ti.is_attribution_bearing(operation, {}) is True, operation
    assert ti.is_attribution_bearing("batch", {"commands": [{"operation": "task.patch", "arguments": {}}]}) is True
    for operation in EXEMPT:
        assert ti.is_attribution_bearing(operation, {}) is False, operation
    assert ti.is_attribution_bearing("batch", {"commands": [{"operation": "linear.link", "arguments": {}}]}) is False


def test_refusals_name_the_skill_and_say_nothing_was_written():
    for refusal in (ti.refused_name(""), ti.refused_name("Your Name"), ti.refused_unsluggable("李小龙"),
                    ti.refused_git("git config timed out"), ti.refused_file("bad handle"),
                    ti.refused_exhausted("jdoe")):
        assert isinstance(refusal, IdentityRefused) and isinstance(refusal, ValueError)
        assert "taskmaster:init-team" in str(refusal)
        assert "Nothing was written" in str(refusal)
    assert "is empty" in str(ti.refused_name(""))
    assert "is empty" in str(ti.refused_name("   "))
    assert '"Your Name"' in str(ti.refused_name("Your Name"))


def test_refusal_text_is_exact():
    tail = ('Run the taskmaster:init-team skill to set your team identity, or set a real name with '
            'git config user.name "<your name>", then retry. Nothing was written.')
    assert str(ti.refused_name("Your Name")) == (
        'Refused: team mode attributes every shared change to a person, and this clone has no identity yet. '
        'git user.name is "Your Name", which can\'t be used as a handle. ' + tail)
    assert str(ti.refused_unsluggable("李小龙")) == (
        'Refused: team mode needs a handle for this clone, but neither git user.name "李小龙" nor user.email '
        'gives one (handles use a-z and 0-9). ' + tail)
    assert str(ti.refused_git("git config timed out")) == (
        "Refused: team mode needs this clone's identity, but git config could not be read (git config timed out). "
        + tail)
    assert str(ti.refused_file("bad handle")) == (
        "Refused: .taskmaster/local/identity.yaml is unreadable (bad handle). Fix or delete that file, or run the "
        "taskmaster:init-team skill, then retry. Nothing was written.")
    assert str(ti.refused_exhausted("jdoe")) == (
        'Refused: no free handle from "jdoe" up to jdoe99 among this backlog\'s profiles. Run the '
        'taskmaster:init-team skill to choose one, then retry. Nothing was written.')
