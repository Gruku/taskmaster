"""Guard: mark_task_handovers_complete must be removed after smart-close wiring."""
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))


def test_mark_task_handovers_complete_not_importable():
    """The old function must be gone — its only caller now uses smart_auto_close_handovers."""
    import taskmaster_v3 as m
    assert not hasattr(m, "mark_task_handovers_complete"), (
        "mark_task_handovers_complete is dead code after Task 5 wiring. "
        "Use smart_auto_close_handovers instead."
    )


def test_smart_auto_close_handovers_is_importable():
    """The replacement must be present."""
    from taskmaster_v3 import smart_auto_close_handovers
    assert callable(smart_auto_close_handovers)
