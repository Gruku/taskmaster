import json


def test_save_session_snapshot_writes_named_file(tmp_path, monkeypatch):
    from taskmaster.taskmaster_v3 import save_session_snapshot
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster" / "snapshots").mkdir(parents=True)
    save_session_snapshot("SNAP-0184", {"tasks": {"T-1": {"status": "done"}}, "lessons_fired": [], "issues": {}})
    p = tmp_path / ".taskmaster" / "snapshots" / "SNAP-0184.json"
    assert p.exists()
    body = json.loads(p.read_text())
    assert body["tasks"]["T-1"]["status"] == "done"


def test_snapshot_diff_detects_added_removed_changed_tasks():
    from taskmaster.taskmaster_v3 import snapshot_diff
    a = {
        "tasks": {
            "T-1": {"status": "todo",        "title": "Old"},
            "T-2": {"status": "in-progress", "title": "Hold"},
        },
        "lessons_fired": [],
        "issues": {},
        "files_touched": [],
    }
    b = {
        "tasks": {
            "T-2": {"status": "done", "title": "Hold"},
            "T-3": {"status": "todo", "title": "New"},
        },
        "lessons_fired": [{"id": "LSN-08", "fires": 3, "first_time": False}],
        "issues":        {"ISS-12": {"severity": "High", "status": "open"}},
        "files_touched": ["a.py", "b.css"],
    }
    d = snapshot_diff(a, b)
    assert {t["id"] for t in d["tasks_added"]}   == {"T-3"}
    assert {t["id"] for t in d["tasks_removed"]} == {"T-1"}
    assert d["tasks_changed"][0]["id"] == "T-2"
    assert d["tasks_changed"][0]["from"]["status"] == "in-progress"
    assert d["tasks_changed"][0]["to"]["status"]   == "done"
    assert d["lessons_fired"] == [{"id": "LSN-08", "fires": 3, "first_time": False}]
    assert d["issues_opened"][0]["id"] == "ISS-12"
    assert d["issues_transitioned"] == []
    assert d["files_touched"] == ["a.py", "b.css"]


def test_snapshot_diff_detects_issue_transitions():
    from taskmaster.taskmaster_v3 import snapshot_diff
    a = {"tasks": {}, "issues": {"ISS-1": {"severity": "High", "status": "open"}},
         "lessons_fired": [], "files_touched": []}
    b = {"tasks": {}, "issues": {"ISS-1": {"severity": "High", "status": "fixed"}},
         "lessons_fired": [], "files_touched": []}
    d = snapshot_diff(a, b)
    assert d["issues_opened"] == []
    assert d["issues_transitioned"][0]["id"] == "ISS-1"
    assert d["issues_transitioned"][0]["from"] == "open"
    assert d["issues_transitioned"][0]["to"]   == "fixed"
