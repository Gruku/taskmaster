# User intent: call every tool a member can reach once and scan every answer for host paths, other machines' homes, the OS user name, hostnames and tracebacks, pin which tools leak what today as a ratchet, and show an injected host-path error reaches a member as a reference only (team-server-015 S10 coverage sweep, fault injection and leak report; evidence for 006 G2 and 007 M3).
from __future__ import annotations

import getpass
import json
import socket
from pathlib import Path

import pytest

from twohost import evidence as ev
from twohost.fixtures import team
from twohost.sweep import minimal_arguments
from twohost.world import Machine

import surface_inventory as inv

pytestmark = [pytest.mark.twohost, pytest.mark.slow, pytest.mark.real_service_process,
              pytest.mark.xdist_group("heavy_processes"), pytest.mark.timeout(240)]

FAULT_TOOL = "harness_fault"
# Not called by the sweep: on a native store the adapter runs webbrowser.open on the HOST (native_routing/links_areas.py
# open_viewer), which opened a browser on the dev machine. Reported as a finding (015 resume file), pinned by name here.
SIDE_EFFECT_SKIP = {"backlog_open_viewer": "native adapter calls webbrowser.open on the host"}

# The first green run's leaking set (design A5: "whatever the first green run finds"). A tool or needle class not listed
# here fails the ratchet; one that no longer leaks is noted in leaks.json. One line per tool and needle class.
EXPECTED_LEAKS: dict[str, list[str]] = {
    "backlog_handover_create": ["host-root"],            # the handover file's absolute path under the host root (G2)
    "backlog_index_status": ["host-root"],               # the store path (G2)
    "backlog_store_status": ["host-root", "hostname"],   # store path and the exporter lease holder's hostname (G2, 007 M3)
}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    try:
        with team(tmp_path_factory.mktemp("leaks") / "run", patch, fault=FAULT_TOOL, name="leaks") as the_team:
            yield the_team
    finally:
        patch.undo()


def needles(world) -> dict[str, list[str]]:
    ann_home = str(Machine.create(world.base, "ann").home)
    bob_home = str(Machine.create(world.base, "bob").home)
    return {
        "host-root": [str(world.root)],
        "other-machine": [ann_home, bob_home],
        "home-shape": list(ev.HOME_SHAPES),
        "os-user": [getpass.getuser()],
        "hostname": [socket.gethostname()],
        "traceback": list(ev.TRACEBACK_MARKERS),
    }


def test_an_injected_host_path_error_reaches_a_member_as_a_reference_only(world):
    answer = world.ann.call(FAULT_TOOL)
    assert "ref" in answer.lower(), answer
    for needle in (str(world.root), "secret.db", "Permission denied", "Errno"):
        assert needle not in answer, (needle, answer)


def test_every_tool_and_every_read_tool_is_called_once_and_the_answers_are_scanned(world):
    surface = json.loads(inv.FIXTURE.read_text(encoding="utf-8"))
    with world.ann.mcp() as connection:
        listed = {t["name"]: t["inputSchema"] for t in connection.tools()}
        assert set(listed) - {FAULT_TOOL} == set(surface["tools"]), (sorted(set(surface["tools"]) - set(listed)),
                                                                     sorted(set(listed) - set(surface["tools"]) - {FAULT_TOOL}))
        for name in sorted(set(surface["tools"]) - set(SIDE_EFFECT_SKIP)):
            text = connection.call(name, minimal_arguments(name, listed[name]), timeout=90)
            world.evidence.record("mcp", name, text)
    batch = [{"tool": name, "args": minimal_arguments(name, listed[name])} for name in surface["read_tools"]]
    response = world.bob.http("POST", "/read/v1/tools", headers={"Content-Type": "application/json"},
                              json={"calls": batch})
    assert response.status_code == 200, response.text
    for result in response.json()["data"]["results"]:
        world.evidence.record("read", "/read/v1/tools:" + result["tool"], result["text"])
    swept = {a["tool"] for a in world.evidence.answers if a["channel"] == "mcp"} - {FAULT_TOOL}
    assert swept == set(surface["tools"]) - set(SIDE_EFFECT_SKIP)                  # the answer set equals the tool list (review I4)

    found = world.evidence.scan(needles(world), mask={"os-user": [str(world.base)], "home-shape": [str(world.base)]})
    outcomes = {}
    for answer in world.evidence.answers:           # ok vs error per tool: an error answer proves less about leaks (fake ids)
        outcomes.setdefault(answer["tool"], set()).add("error" if answer["text"].lstrip().startswith("Error") else "ok")
    outcomes = {tool: sorted(kinds) for tool, kinds in sorted(outcomes.items())}
    summary = {tool: sorted(classes) for tool, classes in sorted(found.items())}
    world.evidence.write("leaks.json", {"leaks": found, "needles": needles(world), "answer_outcomes": outcomes,
                                        "only_error_answers": sorted(t for t, k in outcomes.items() if k == ["error"])})
    # per-need evidence for G2 (host paths in success results) and M3: nothing may name another machine or a key file
    worst = {t: c for t, c in summary.items() if {"other-machine", "traceback"} & set(c)}
    assert worst == {}, f"answers naming another machine's home or a traceback: {worst}"
    new = {t: sorted(set(c) - set(EXPECTED_LEAKS.get(t, []))) for t, c in summary.items()}
    new = {t: c for t, c in new.items() if c}
    assert new == {}, "new leaking tools or needle classes (review them, then add to EXPECTED_LEAKS):\n" + json.dumps(new, indent=1)
    Path(world.base / "leaks-summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
