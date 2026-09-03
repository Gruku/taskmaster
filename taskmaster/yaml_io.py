# User intent: single fast-path for reading backlog YAML — every safe_load call in the
# package routes through here so it transparently gets libyaml's C loader (~6x faster)
# when available, falling back to pure-Python PyYAML otherwise. No behavior change.
from __future__ import annotations

from typing import Any

import yaml

if yaml.__with_libyaml__:
    _LOADER = yaml.CSafeLoader
    LOADER_NAME = "CSafeLoader"
else:
    _LOADER = yaml.SafeLoader
    LOADER_NAME = "SafeLoader"


def safe_load(stream: Any) -> Any:
    """Drop-in replacement for `yaml.safe_load`, using the fastest safe loader available."""
    return yaml.load(stream, Loader=_LOADER)
