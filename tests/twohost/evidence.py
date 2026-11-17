# User intent: record every member-visible answer with keys reduced to key ids and bodies capped, scan them for host leaks, and write the evidence pack the Opus security reviewer reads (015 A5, B4).
"""Transcript recorder, leak scanner and evidence pack writer."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

BODY_CAP = 2048
TRACEBACK_MARKERS = ("Traceback (most recent call last)",)
HOME_SHAPES = ("C:\\Users\\", "/home/", "/Users/")


def key_id(authorization: str | None) -> str:
    """`Bearer <key>` -> the member key's id (principals.KEY_RE group 1), "operator" for the bootstrap token; the secret
    never leaves."""
    from taskmaster.serve.principals import KEY_RE
    if not authorization:
        return "none"
    token = authorization.split(None, 1)[-1]
    found = re.fullmatch(KEY_RE.pattern, token)
    if found:
        return found.group(1)
    return "operator" if token.startswith("tm_op_") else "unknown"


def redact(text: str) -> str:
    """Key-shaped text and a login form's `token=` value never reach the pack."""
    text = re.sub(r"(?<=token=)[^&\s\"']+", "<redacted>", text)
    return re.sub(r"tm_u_[0-9a-f]{16}_[A-Za-z0-9_-]{43}|tm_op_[A-Za-z0-9_-]+", "<key>", text)


def cap(body, limit: int = BODY_CAP) -> str:
    text = body if isinstance(body, str) else json.dumps(body, default=str)
    return text if len(text) <= limit else text[:limit] + f"...[{len(text) - limit} more chars]"


def needle_forms(needle: str) -> set[str]:
    """Forward-slash, backslash, JSON-escaped and case-folded spellings of one needle."""
    forms = {needle, needle.replace("\\", "/"), needle.replace("/", "\\")}
    forms |= {json.dumps(form)[1:-1] for form in list(forms)}
    return {form.casefold() for form in forms if form}


class Evidence:
    def __init__(self, directory: Path | None = None, name: str = "world"):
        self.directory = Path(directory) if directory else None
        self.name = name                    # one transcript file per world, so a later module cannot overwrite an earlier one
        self.lines: list[dict] = []
        self.answers: list[dict] = []

    @classmethod
    def from_env(cls, name: str = "world") -> "Evidence":
        target = os.environ.get("TASKMASTER_TWOHOST_EVIDENCE")
        return cls(Path(target) if target else None, name)

    def transcript(self, *, who: str, method: str, target: str, authorization=None, request=None, status=None,
                   response=None) -> int:
        entry = {"id": len(self.lines), "who": who, "method": method, "target": target,
                 "key_id": key_id(authorization), "request": redact(cap(request if request is not None else "")),
                 "status": status, "response": redact(cap(response if response is not None else ""))}
        self.lines.append(entry)
        return entry["id"]

    def mcp(self, *, who: str, tool: str, arguments, key: str | None, answer) -> int:
        """An MCP tool call as a transcript line: tool, arguments and answer capped, the key reduced to its id."""
        return self.transcript(who=who, method="MCP", target=tool, authorization=f"Bearer {key}" if key else None,
                               request=json.dumps(arguments or {}, default=str), status="answered", response=answer)

    def record(self, channel: str, tool: str, text) -> None:
        """A member-visible answer, for the leak scan. `channel` is mcp, read, write or viewer."""
        self.answers.append({"channel": channel, "tool": tool,
                             "text": text if isinstance(text, str) else json.dumps(text, default=str)})

    def scan(self, needles: dict[str, list[str]], *, mask: dict[str, list[str]] | None = None) -> dict[str, dict[str, dict]]:
        """{tool: {needle class: {"needle", "excerpt"}}}: every answer searched for every needle spelling. `mask` maps a
        needle class to strings removed from the haystack first (so a host root under the OS user's home does not also
        count as an os-user or home-shape leak)."""
        leaks: dict[str, dict[str, dict]] = {}
        for answer in self.answers:
            for klass, values in needles.items():
                haystack = answer["text"].casefold()
                for hidden in (mask or {}).get(klass, []):
                    for form in needle_forms(hidden):
                        haystack = haystack.replace(form, "")
                for value in values:
                    for form in needle_forms(value):
                        at = haystack.find(form)
                        if at >= 0:
                            raw = answer["text"]
                            leaks.setdefault(answer["tool"], {}).setdefault(
                                klass, {"needle": value, "excerpt": raw[max(0, at - 40):at + len(form) + 40]})
                            break
        return leaks

    def write(self, name: str, payload) -> Path | None:
        if self.directory is None:
            return None
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / name
        text = payload if isinstance(payload, str) else json.dumps(payload, indent=2, sort_keys=True, default=str)
        target.write_text(text, encoding="utf-8")
        return target

    def flush(self) -> None:
        if self.directory is None:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        target, n = self.directory / f"transcripts-{self.name}.jsonl", 1
        while target.exists():              # never overwrite an earlier world that shared this name
            n += 1
            target = self.directory / f"transcripts-{self.name}-{n}.jsonl"
        target.write_text(
            "".join(json.dumps(line, default=str) + "\n" for line in self.lines), encoding="utf-8")
