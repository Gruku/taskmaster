# User intent: a direct change that grew into a tracked task keeps working on its branch; the pick reply must not
# insist on a new worktree when the branch is already recorded.
from taskmaster import backlog_server as bs


def test_recorded_branch_reply_says_keep_working_where_you_are():
    text = bs._build_worktree_instruction("T-001", "", "feature/x", "")
    assert "Branch already recorded as `feature/x`" in text and "no new worktree" in text


def test_no_branch_reply_asks_for_a_worktree():
    text = bs._build_worktree_instruction("T-001", "", "", "")
    assert "Create a worktree before writing any code" in text and "already recorded" not in text


def test_recorded_worktree_wins():
    assert "Already recorded at" in bs._build_worktree_instruction("T-001", "", "feature/x", ".worktrees/x")
