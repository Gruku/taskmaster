# User intent: adding a Linear workspace on a native store (N13 step 7) rewrites the
# authored linear.yaml atomically under the coordinator's publication boundary, keeping
# every other workspace and setting, and never writing anything but a token's env-var name.
"""`backlog_linear(action="bootstrap_apply")` on the coordinator side."""
from __future__ import annotations

from copy import deepcopy
import re

import yaml

from taskmaster import taskmaster_v3 as v3, yaml_io
from taskmaster.native import projection

CONFIG = "linear.yaml"
PUBLICATION_TIMEOUT = 10
_TEXT_FIELDS = ("alias", "team_id", "token_env")
_MAPPINGS = ("status_mapping", "priority_mapping")
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
# Linear credential prefixes: a value that looks like one is a token, not its name.
_TOKEN_PREFIXES = ("lin_api_", "lin_oauth_")


def validate(entry, default_workspace):
    if not isinstance(entry, dict) or not set(_TEXT_FIELDS) <= set(entry) <= set(_TEXT_FIELDS + _MAPPINGS):
        raise ValueError("invalid Linear workspace entry")
    if type(default_workspace) is not bool:
        raise ValueError("default_workspace must be a boolean")
    for key in _TEXT_FIELDS:
        if not isinstance(entry[key], str) or not entry[key].strip() or len(entry[key]) > 256:
            raise ValueError(f"{key} is required")
    for key in _MAPPINGS:
        mapping = entry.get(key)
        if key in entry and (not isinstance(mapping, dict) or not mapping or not all(
                isinstance(k, str) and isinstance(v, str) and k and v for k, v in mapping.items())):
            raise ValueError(f"invalid {key}")
    token_env = entry["token_env"]
    if not _ENVIRONMENT_NAME.fullmatch(token_env) or token_env.lower().startswith(_TOKEN_PREFIXES):
        raise ValueError("token_env must be the name of the environment variable that holds the Linear "
                         "token (for example LINEAR_TOKEN), never the token itself; linear.yaml was not changed")
    # linear.yaml is tracked: no key or value anywhere in the entry may be a token.
    texts = [(key, entry[key]) for key in _TEXT_FIELDS]
    texts += [(key, text) for key in _MAPPINGS for pair in (entry.get(key) or {}).items() for text in pair]
    for key, text in texts:
        if text.strip().lower().startswith(_TOKEN_PREFIXES):
            raise ValueError(f"{key} looks like a Linear credential; linear.yaml is tracked and must never "
                             f"hold a token; linear.yaml was not changed")


def _parse(content):
    if content is None:
        return {}
    try:
        loaded = yaml_io.safe_load(content.decode("utf-8"))
    except (UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read {CONFIG}: {exc}") from None
    if loaded is not None and not isinstance(loaded, dict):
        raise ValueError(f"{CONFIG} top-level must be a mapping")
    return loaded or {}


def bootstrap(owner, entry, default_workspace):
    """Add one workspace and return the legacy answer. An identical entry already
    present (a retry after a lost response) answers `unchanged` without writing."""
    validate(entry, default_workspace)
    backlog_dir = owner.root / ".taskmaster"
    answer = {"ok": True, "path": str(backlog_dir / CONFIG), "workspace": entry["alias"],
              "default": default_workspace}
    if not owner.publication.acquire(timeout=PUBLICATION_TIMEOUT):
        raise ValueError(f"publisher busy; {CONFIG} was not changed; retry")
    try:
        refusal = owner.publication_refusal()
        if refusal:
            raise ValueError(f"{refusal}; {CONFIG} was not changed")
        current = projection.read_config(backlog_dir, CONFIG)
        config = _parse(current)
        present = next((ws for ws in config.get("workspaces") or []
                        if isinstance(ws, dict) and ws.get("alias") == entry["alias"]), None)
        if present is not None:
            if present == entry and (not default_workspace or config.get("default_workspace") == entry["alias"]):
                return dict(answer, unchanged=True)
            raise ValueError(f"workspace alias {entry['alias']!r} already exists in linear.yaml")
        updated = deepcopy(config)
        updated.setdefault("workspaces", []).append(deepcopy(entry))
        if default_workspace:
            updated["default_workspace"] = entry["alias"]
        try:
            v3._validate_linear_config(updated)
        except ValueError as exc:
            raise ValueError(f"config validation failed: {exc}") from None
        payload = yaml.dump(updated, default_flow_style=False, sort_keys=False, allow_unicode=True)
        try:
            projection.replace_config(backlog_dir, CONFIG, expected=current, content=payload.encode("utf-8"))
        except OSError as exc:
            raise ValueError(f"{CONFIG} was not changed: {exc}") from None
        return answer
    finally:
        owner.publication.release()
