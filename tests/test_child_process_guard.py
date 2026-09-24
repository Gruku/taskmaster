# User intent: keep the suite's RAM bounded by proving its recurrence guards work — an
# unmarked test cannot launch a real coordinator service, and a real service's idle
# timeout can be shortened from the environment without changing the production default.
import os
import subprocess
import sys

import pytest

from taskmaster.coordinator import client as client_module
from taskmaster.coordinator import service
from tests.conftest import RealServiceLaunchError


def test_an_unmarked_test_cannot_launch_a_real_coordinator_service(tmp_path):
    with pytest.raises(RealServiceLaunchError, match="real_service_process"):
        client_module._launch(tmp_path)


def test_an_unmarked_test_may_still_run_other_python_children():
    completed = subprocess.run([sys.executable, "-c", "print('ok')"], capture_output=True, text=True, check=True)
    assert completed.stdout.strip() == "ok"


def test_the_suite_runs_services_with_a_short_idle_timeout():
    assert 0 < float(os.environ[service.IDLE_SECONDS_ENV]) <= 10


@pytest.mark.parametrize(("value", "expected"), [
    (None, 300.0), ("", 300.0), ("junk", 300.0), ("0", 300.0), ("-4", 300.0), ("nan", 300.0),
    ("inf", 300.0), ("2.5", 2.5), ("600", 600.0),
])
def test_idle_timeout_defaults_to_five_minutes_and_honours_a_valid_override(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv(service.IDLE_SECONDS_ENV, raising=False)
    else:
        monkeypatch.setenv(service.IDLE_SECONDS_ENV, value)
    assert service.default_idle_seconds() == expected
