# User intent: managed Git's drift walk must not count the state exporter's commits (taskmaster-state) as earlier
# authored versions of a path, while authored history still counts (team-server-022; held on the base by the layout).
import subprocess

from taskmaster.coordinator import checkouts


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                   capture_output=True)


def _blob(root, content):
    return subprocess.run(["git", "-C", str(root), "hash-object", "--stdin"], input=content, check=True,
                          capture_output=True).stdout.decode().strip()


def _repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / ".taskmaster").mkdir()
    (tmp_path / ".taskmaster" / "a.md").write_bytes(b"authored\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "authored")
    return checkouts.Checkout("main", tmp_path, tmp_path / ".git", tmp_path / ".git", False)


def test_export_commits_at_the_real_layout_are_not_drift_and_authored_history_is(tmp_path):
    # The exporter writes the projection at the tree root (a.md), never under .taskmaster/ (state_export._shaped).
    checkout = _repo(tmp_path)
    _git(tmp_path, "checkout", "-q", "--orphan", "taskmaster-state")
    _git(tmp_path, "rm", "-rq", "--cached", ".")
    (tmp_path / "a.md").write_bytes(b"exported\n")
    _git(tmp_path, "add", "a.md")
    _git(tmp_path, "commit", "-q", "-m", "export")
    _git(tmp_path, "checkout", "-q", "-f", "main")
    blobs, complete = checkouts.history_blobs(checkout, ["a.md"])
    assert complete
    assert blobs == {_blob(tmp_path, b"authored\n")}
    assert _blob(tmp_path, b"exported\n") not in blobs
