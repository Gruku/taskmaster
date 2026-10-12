# User intent: tell the restore pane what each of this repo's recent Claude Code sessions did — transcript facts only
# (prompts, last event, tm task ids and handover ids, end-session) — so Taskmaster, not a regex, says what is unfinished.
"""Session scanner for taskmaster-mods' restore pane (spec §10.1).

  scan.py scan   --config-dir DIR --root DIR [--days N] [--exclude ID] [--cache FILE]
  scan.py digest --config-dir DIR --session ID [--max-chars N]

Prints one JSON document on stdout. Transcripts are streamed line by line (they run past the mod runtime's 4 MiB read
cap) and per-file facts are cached by mtime + size. Only sessions whose working directory lies under --root are read.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

CACHE_VERSION = 1
TM_TOOL = "mcp__plugin_taskmaster_tm__"
HANDOVER_TOOL = TM_TOOL + "backlog_handover_create"
END_SESSION_SKILL = "taskmaster:end-session"
END_SESSION_COMMAND = "<command-name>/taskmaster:end-session</command-name>"
INTERRUPT_MARK = "[Request interrupted by user"
HANDOVER_WRITTEN = re.compile(r"Handover written: (\S+)")
HANDOVER_PATH = re.compile(r"^- Path: (.+?)\s*$", re.MULTILINE)
MAX_TASK_IDS = 8
# A closed session's last event: anything but the agent's own finished reply means it was cut off mid-turn.
MID_TURN = ("interrupt", "prompt", "tool_use", "tool_result")


def iso_to_epoch(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def clip(text: str | None, limit: int) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def result_text(content) -> str:
    """A tool result's text, a `{"result": "<text>"}` wrapper (the tm server's output schema) unwrapped."""
    text = content if isinstance(content, str) else text_of(content)
    stripped = text.strip()
    if stripped.startswith("{") and '"result"' in stripped:
        try:
            value = json.loads(stripped)
        except ValueError:
            return text
        if isinstance(value, dict) and isinstance(value.get("result"), str):
            return value["result"]
    return text


def encode_dir(path: str) -> str:
    """Claude Code's project folder name for a working directory: every non-alphanumeric character becomes `-`."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def is_under(path: str | None, root: str) -> bool:
    if not path:
        return False
    p = os.path.normcase(os.path.normpath(path))
    r = os.path.normcase(os.path.normpath(root))
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def is_human_prompt(rec: dict) -> bool:
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isSidechain"):
        return False
    if rec.get("toolUseResult") is not None or rec.get("sourceToolUseID"):
        return False
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return False
    text = text_of(content).strip()
    if not text:
        return False
    # Engine-injected wrappers: command echoes, reminders, teammate and task notifications, the Esc marker.
    return not text.startswith(("<", "Caveat:", "Another Claude session sent a message", INTERRUPT_MARK))


def touch(task_ids: list[str], value) -> None:
    """Keeps the ids in last-touched order: the last one is the session's bound task (spec §10.2)."""
    for tid in value if isinstance(value, list) else [value]:
        if isinstance(tid, str) and tid.strip():
            tid = tid.strip()
            if tid in task_ids:
                task_ids.remove(tid)
            task_ids.append(tid)


def scan_file(path: Path) -> dict:
    s: dict = {
        "id": path.stem, "cwd": None, "branch": None, "entrypoint": None, "aiTitle": None, "customTitle": None,
        "firstPrompt": None, "lastPrompt": None, "lastReply": None, "lastEvent": None, "prompts": 0,
        "ctx": 0, "cost": 0.0, "start": None, "end": None, "costEnd": None,
        "taskIds": [], "handovers": [], "endedSession": False, "promptsAfterEnd": 0,
    }
    pending: dict[str, dict] = {}
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            t = rec.get("type")
            if t == "ai-title":
                s["aiTitle"] = rec.get("aiTitle")
                continue
            if t == "custom-title":
                s["customTitle"] = rec.get("customTitle")
                continue
            if t == "cost-state":
                s["cost"] = rec.get("totalCostUSD") or 0.0
                if rec.get("startTime") and rec.get("totalDuration") is not None:
                    # Written as the process exits: start + wall-clock duration = when it closed.
                    s["costEnd"] = (rec["startTime"] + rec["totalDuration"]) / 1000
                continue
            if t not in ("user", "assistant") or rec.get("isSidechain"):
                continue
            ts = rec.get("timestamp")
            if ts:
                s["start"] = s["start"] or ts
                s["end"] = ts
            s["cwd"] = s["cwd"] or rec.get("cwd")
            s["entrypoint"] = s["entrypoint"] or rec.get("entrypoint")
            if rec.get("gitBranch"):
                s["branch"] = rec["gitBranch"]
            msg = rec.get("message") or {}
            content = msg.get("content")
            if t == "user":
                if END_SESSION_COMMAND in text_of(content):
                    s["endedSession"] = True
                    s["promptsAfterEnd"] = 0
                if is_human_prompt(rec):
                    txt = text_of(content)
                    s["firstPrompt"] = s["firstPrompt"] or clip(txt, 300)
                    s["lastPrompt"] = clip(txt, 400)
                    s["prompts"] += 1
                    s["lastEvent"] = "prompt"
                    if s["endedSession"]:
                        s["promptsAfterEnd"] += 1
                if isinstance(content, list):
                    for b in content:
                        if not isinstance(b, dict) or b.get("type") != "tool_result":
                            continue
                        s["lastEvent"] = "tool_result"
                        h = pending.pop(b.get("tool_use_id"), None)
                        if h is None or b.get("is_error"):
                            continue
                        res = result_text(b.get("content"))
                        written = HANDOVER_WRITTEN.search(res)
                        if written:
                            where = HANDOVER_PATH.search(res)
                            s["handovers"].append(dict(h, id=written.group(1), path=where.group(1) if where else ""))
                if text_of(content).lstrip().startswith(INTERRUPT_MARK):
                    s["lastEvent"] = "interrupt"
                continue
            usage = msg.get("usage") or {}
            ctx = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            if ctx > 0:
                # What the last request carried: the context a resume starts from.
                s["ctx"] = ctx
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text" and b.get("text", "").strip():
                    s["lastReply"] = clip(b["text"], 600)
                    s["lastEvent"] = "text"
                elif bt == "tool_use":
                    s["lastEvent"] = "tool_use"
                    name = b.get("name") or ""
                    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                    if name == "Skill" and inp.get("skill") == END_SESSION_SKILL:
                        s["endedSession"] = True
                        s["promptsAfterEnd"] = 0
                    if name.startswith(TM_TOOL):
                        touch(s["taskIds"], inp.get("task_id"))
                        touch(s["taskIds"], inp.get("task_ids"))
                    if name == HANDOVER_TOOL:
                        pending[b.get("id")] = {"tldr": clip(inp.get("tldr"), 200), "next": clip(inp.get("next_action"), 300)}
            if msg.get("stop_reason") == "end_turn" and s["lastEvent"] == "text":
                s["lastEvent"] = "end_turn"
    s["taskIds"] = s["taskIds"][-MAX_TASK_IDS:]
    return s


def live_sessions(cfg: Path) -> set[str]:
    """Session ids whose Claude Code process is still alive, from <config>/sessions/<pid>.json."""
    reg = cfg / "sessions"
    if not reg.is_dir():
        return set()
    by_pid: dict[int, str] = {}
    for f in reg.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            by_pid[int(d["pid"])] = d["sessionId"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return {sid for pid, sid in by_pid.items() if pid_alive(pid)}


def pid_alive(pid: int) -> bool:
    # Per-pid probe: tasklist can take 10 s and more, and a timeout used to mark every session closed.
    if os.name == "nt":
        import ctypes

        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def load_cache(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {}
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return cache.get("files", {}) if isinstance(cache, dict) and cache.get("v") == CACHE_VERSION else {}


def facts(s: dict, mtime: float, live: set[str]) -> dict:
    end = iso_to_epoch(s["end"]) or 0
    return {
        "id": s["id"],
        "cwd": s["cwd"],
        "cwdExists": Path(s["cwd"]).is_dir(),
        "branch": s["branch"] or "",
        "title": s["customTitle"] or s["aiTitle"] or clip(s["firstPrompt"], 70) or s["id"][:8],
        "firstPrompt": s["firstPrompt"] or "",
        "lastPrompt": s["lastPrompt"] or "",
        "lastReply": s["lastReply"] or "",
        "lastEvent": s["lastEvent"] or "",
        "interrupted": s["lastEvent"] in MID_TURN,
        "prompts": s["prompts"],
        "ctx": s["ctx"],
        "cost": round(float(s["cost"] or 0), 2),
        # Background monitors append to transcripts after exit, so mtime is the last resort for the close time.
        "closedEpoch": s["costEnd"] or end or mtime,
        "hasClosed": s["costEnd"] is not None,
        "running": s["id"] in live,
        "resumedAfterClose": s["costEnd"] is not None and end > s["costEnd"] + 60,
        "taskIds": s["taskIds"],
        "handovers": s["handovers"],
        "endedSession": s["endedSession"],
        "promptsAfterEnd": s["promptsAfterEnd"],
    }


def scan(args) -> dict:
    cfg = Path(args.config_dir)
    projects = cfg / "projects"
    root = os.path.normpath(args.root)
    prefix = encode_dir(root).lower()
    cutoff = time.time() - args.days * 86400
    cache_path = Path(args.cache) if args.cache else None
    entries = load_cache(cache_path)
    fresh: dict = {}
    found: list[tuple[dict, float]] = []
    if projects.is_dir():
        for pdir in projects.iterdir():
            # The folder name is the encoded working directory: anything under the root starts with the root's encoding.
            if not pdir.is_dir() or not pdir.name.lower().startswith(prefix):
                continue
            for f in pdir.glob("*.jsonl"):
                st = f.stat()
                if st.st_mtime < cutoff or f.stem == args.exclude:
                    continue
                key = str(f)
                sig = f"{st.st_mtime_ns}:{st.st_size}"
                hit = entries.get(key)
                s = hit["s"] if isinstance(hit, dict) and hit.get("sig") == sig else scan_file(f)
                fresh[key] = {"sig": sig, "s": s}
                if s["prompts"] > 0 and is_under(s["cwd"], root) and not str(s["entrypoint"] or "").startswith("sdk"):
                    found.append((s, st.st_mtime))
    if cache_path:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"v": CACHE_VERSION, "files": fresh}), encoding="utf-8")
        except OSError:
            pass
    live = live_sessions(cfg)
    sessions = sorted((facts(s, mtime, live) for s, mtime in found), key=lambda x: -x["closedEpoch"])
    return {"v": CACHE_VERSION, "scannedAt": time.time(), "root": root, "sessions": sessions}


def digest(args) -> dict:
    """The condensed tail of one transcript (human prompts and assistant prose), for a model brief."""
    path = next((Path(args.config_dir) / "projects").glob(f"*/{args.session}.jsonl"), None)
    if path is None:
        return {"error": "session not found"}
    rows: list[str] = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict) or rec.get("isSidechain"):
                continue
            if is_human_prompt(rec):
                rows.append("USER: " + clip(text_of(rec["message"].get("content")), 1200))
            elif rec.get("type") == "assistant":
                for b in (rec.get("message") or {}).get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip():
                        rows.append("ASSISTANT: " + clip(b["text"], 1500))
                    elif isinstance(b, dict) and b.get("type") == "tool_use":
                        rows.append(f"TOOL: {b.get('name')}")
    out: list[str] = []
    for r in rows:
        if r.startswith("TOOL:") and out and out[-1].startswith("TOOL:"):
            continue
        out.append(r)
    text, used = [], 0
    for r in reversed(out):
        if used + len(r) > args.max_chars:
            break
        text.append(r)
        used += len(r)
    head = next((r for r in out if r.startswith("USER:")), "")
    return {"first": head, "tail": "\n".join(reversed(text))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["scan", "digest"])
    ap.add_argument("--config-dir", required=True)
    ap.add_argument("--root", default="")
    ap.add_argument("--days", type=float, default=4)
    ap.add_argument("--exclude", default="")
    ap.add_argument("--cache", default="")
    ap.add_argument("--session", default="")
    ap.add_argument("--max-chars", type=int, default=24000)
    args = ap.parse_args(argv)
    if args.mode == "scan" and not args.root:
        ap.error("scan needs --root")
    result = scan(args) if args.mode == "scan" else digest(args)
    sys.stdout.reconfigure(encoding="utf-8")
    json.dump(result, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
