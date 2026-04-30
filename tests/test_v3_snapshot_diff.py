import json


def test_save_session_snapshot_writes_named_file(tmp_path, monkeypatch):
    from taskmaster_v3 import save_session_snapshot
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster" / "snapshots").mkdir(parents=True)
    save_session_snapshot("SNAP-0184", {"tasks": {"T-1": {"status": "done"}}, "lessons_fired": [], "issues": {}})
    p = tmp_path / ".taskmaster" / "snapshots" / "SNAP-0184.json"
    assert p.exists()
    body = json.loads(p.read_text())
    assert body["tasks"]["T-1"]["status"] == "done"
