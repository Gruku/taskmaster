# User intent: a teammate's merge stamp reaches the team server as one command, and whether the teammate's merge is
# older than what the server already records is decided in the teammate's own clone, never on the host
# (team-server-008 D7).
"""`remote_apply`: the `apply` for `merge_stamps.replay_queue` over the server's `/write/v1/merge-stamp`."""
from __future__ import annotations

import time

from taskmaster.native_routing import merge_stamps

from .client import ReadUnavailable

ROUNDS = 3
ANCESTRY_MIN_SECONDS = 0.5


def _body(entry: dict, checked: str | None) -> dict:
    body = {"branch": entry["branch"], "target": entry["target"], "sha": entry["sha"],
            "merged_at": entry["merged_at"]}
    if entry.get("clone"):
        body["clone"] = entry["clone"]
    if checked is not None:
        body["checked"] = {"against": checked}
    return body


def remote_apply(session, log, deadline: float, *, monotonic=time.monotonic):
    """`apply(entry)` returning "applied", "dropped", "retry" or ("reject", reason); raises `Unavailable`."""
    def apply(entry: dict):
        name = f"{entry['branch']}→{entry['target']} {entry['sha'][:12]}"
        checked = None
        for _ in range(ROUNDS):
            try:
                result = session.client.write("merge-stamp", _body(entry, checked), deadline=deadline)
            except ReadUnavailable as failure:
                if failure.kind == "bad-request":
                    return ("reject", failure.message)
                # Every other kind (unreachable, busy, server, bad-response, refused, no-key, config, version) keeps the
                # entry queued: a fixed key, a restarted server or an upgraded plugin replays it.
                raise merge_stamps.Unavailable(f"{failure.kind}: {failure.message}") from None
            data = result.data
            outcome = data.get("outcome")
            if outcome == "recorded":
                return "applied"
            if outcome == "already":
                log(f"merge stamp {name} is already recorded")
                return "dropped"
            if outcome == "no-task":
                log(f"no task uses branch {entry['branch']}; nothing to record")
                return "dropped"
            if outcome == "ambiguous":
                return ("reject", f"several tasks use branch {entry['branch']} ({', '.join(data.get('candidates') or [])})")
            if outcome == "refused":
                return ("reject", str(data.get("message") or "the server refused the stamp"))
            if outcome != "check" or not isinstance(data.get("current"), dict):
                return ("reject", f"the server answered an outcome this client does not know: {outcome!r}")
            current = data["current"]
            remaining = max(ANCESTRY_MIN_SECONDS, deadline - monotonic())
            repo = entry.get("repo")
            ancestor = merge_stamps.git_is_ancestor(repo, timeout=remaining) if repo else (lambda old, new: None)
            if merge_stamps.superseded(entry, current, ancestor):
                log(f"merge stamp {name} is superseded by {current.get('merge_commit')}; dropped")
                return "dropped"
            checked = str(current.get("merge_commit") or "")
        return "retry"
    return apply
