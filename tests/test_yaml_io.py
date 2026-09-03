# User intent: prove taskmaster/yaml_io.py picks libyaml's CSafeLoader when available,
# returns byte-identical results to yaml.SafeLoader, accepts str/bytes/file streams, and
# that no call site in the package still calls yaml.safe_load directly (the mechanical
# swap this task performs must be total, not partial).
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

FIXTURE = PLUGIN_ROOT / "tests" / "fixtures" / "index_backlog"


def test_loader_selection_matches_libyaml_flag():
    import yaml

    from taskmaster import yaml_io

    assert yaml_io.LOADER_NAME == ("CSafeLoader" if yaml.__with_libyaml__ else "SafeLoader")


def test_safe_load_equivalence_on_fixture_backlog():
    import yaml

    from taskmaster import yaml_io

    text = (FIXTURE / ".taskmaster" / "backlog.yaml").read_text(encoding="utf-8")
    assert yaml_io.safe_load(text) == yaml.load(text, Loader=yaml.SafeLoader)


def test_safe_load_accepts_stream(tmp_path):
    from taskmaster import yaml_io

    p = tmp_path / "a.yaml"
    p.write_text("a: 1\nb: [x, y]\n", encoding="utf-8")
    with p.open(encoding="utf-8") as fh:
        assert yaml_io.safe_load(fh) == {"a": 1, "b": ["x", "y"]}


def test_safe_load_accepts_bytes():
    from taskmaster import yaml_io

    assert yaml_io.safe_load(b"a: 1\nb: [x, y]\n") == {"a": 1, "b": ["x", "y"]}


def test_no_remaining_direct_safe_load_calls():
    import re

    for f in (PLUGIN_ROOT / "taskmaster").glob("*.py"):
        if f.name == "yaml_io.py":
            continue
        assert not re.search(r"\byaml\.safe_load\(", f.read_text(encoding="utf-8")), f


def test_yaml_io_does_not_import_taskmaster_package():
    text = (PLUGIN_ROOT / "taskmaster" / "yaml_io.py").read_text(encoding="utf-8")
    import re

    assert not re.search(r"^\s*(from taskmaster|import taskmaster)\b", text, re.MULTILINE)
