# User intent: one shared reader of Bash command lines for the merge gate and the merge recorder, so a gated merge is
# never misread (wrong token, `&& git push`, `-m msg`) or missed, and a command it cannot read with confidence is
# reported as unsure instead of guessed at (team-server-008 D11). Standard library only; no import cost on the hot path.
"""`git merge` / `git pull` branches out of a Bash command line.

`prefilter(command)` is the cheap substring test the hooks run first. `parse(command)` tokenizes the line the way
a shell would (quotes, `;`, `&&`, `||`, `|`, newlines, redirections, comments), finds every `git merge` and
`git pull` segment, and returns the named branches of each, plus `unsure` when the line holds a construct the
reader cannot follow (an unbalanced quote, `$(...)`, `eval`, `sh -c`, a variable as the program, an unknown Git
global option before `merge`).
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
from typing import NamedTuple

# Anonymous references: pure hex SHAs (7 or more hex), HEAD~, @{, FETCH_HEAD, ORIG_HEAD, MERGE_HEAD
ANON_REF_RE = re.compile(r"^[0-9a-fA-F]{7,}$|^HEAD[~^]|^@\{|^FETCH_HEAD$|^ORIG_HEAD$|^MERGE_HEAD$")

SUB_MARK = ""           # stands in for a `$(...)` or backtick substitution while the line is tokenized
MAX_DEPTH = 3
WRAPPERS = {"env", "command", "builtin", "exec", "time", "nice", "timeout", "nohup", "stdbuf", "sudo"}
WRAPPER_VALUE_OPTIONS = {"env": {"-u", "-C", "-S"}, "nice": {"-n"}, "exec": {"-a"}, "timeout": {"-s", "-k"},
                         "sudo": {"-u", "-g", "-C", "-h", "-p", "-r", "-t", "-U"}}
WRAPPER_POSITIONALS = {"timeout": 1}  # `timeout 60 git ...`: the duration is not the program
RESERVED = {"!", "{", "}", "if", "then", "elif", "else", "do", "while", "until"}  # shell words before a command
SHELLS = {"eval", "sh", "bash", "zsh", "dash", "ksh", "xargs", "cmd", "pwsh", "powershell"}
GLOBAL_VALUE_OPTIONS = {"-c", "--config-env", "--git-dir", "--work-tree", "--namespace"}
GLOBAL_FLAGS = {"--no-pager", "-P", "-p", "--paginate", "--bare", "--no-replace-objects", "--no-optional-locks",
                "--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs", "--icase-pathspecs",
                "--exec-path"}
MERGE_VALUE_OPTIONS = {"-m", "-F", "-s", "-X", "--message", "--file", "--strategy", "--strategy-option",
                       "--into-name", "--cleanup"}
MERGE_NOTHING = {"--abort", "--continue", "--quit", "--skip"}
PULL_VALUE_OPTIONS = {"-s", "-X", "--strategy", "--strategy-option", "--depth", "--deepen", "--shallow-since",
                      "--shallow-exclude", "-j", "--jobs", "--upload-pack", "-o", "--server-option",
                      "--negotiation-tip", "--refmap"}
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
PUNCTUATION = set("();<>|&")


class Merge(NamedTuple):
    kind: str                       # "merge" or "pull"
    branches: list
    directory: str | None = None    # the composed `git -C <dir>`, as written


class Parsed(NamedTuple):
    merges: list
    unsure: str | None = None


def prefilter(command: str) -> bool:
    """Whether the raw text names `git` and `merge` or `pull`: the only commands worth parsing."""
    return "git" in command and ("merge" in command or "pull" in command)


# ── Step 0: quotes, newlines, comments, substitutions ───────────────────────────────────────────


def _closing_paren(text: str, start: int) -> int:
    """Index of the `)` that closes a `$(` whose body starts at `start`, or -1."""
    depth, i, single, double = 1, start, False, False
    while i < len(text):
        ch = text[i]
        if ch == "\\" and not single:
            i += 2
            continue
        if single:
            single = ch != "'"
        elif ch == "'" and not double:
            single = True
        elif ch == '"':
            double = not double
        elif not double and ch == "(":
            depth += 1
        elif not double and ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _closing_tick(text: str, start: int) -> int:
    i = start
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "`":
            return i
        i += 1
    return -1


def _prepare(command: str):
    """`(text, substitutions, error)`: newlines outside quotes become `;`, comments are dropped, and each command
    substitution is cut out and replaced by a marker word. `error` is a reason string or None."""
    out: list[str] = []
    subs: list[str] = []
    i, single, double = 0, False, False
    while i < len(command):
        ch = command[i]
        if single:
            out.append(ch)
            single = ch != "'"
            i += 1
            continue
        if ch == "\\":
            if i + 1 < len(command) and command[i + 1] == "\n":
                i += 2                      # a line continuation
                continue
            if i + 1 < len(command) and command[i + 1] == "\r" and command[i + 2:i + 3] == "\n":
                i += 3
                continue
            out.append(command[i:i + 2])
            i += 2
            continue
        if ch == "$" and command[i + 1:i + 2] == "(":
            end = _closing_paren(command, i + 2)
            if end < 0:
                return "", subs, "an unterminated $( substitution"
            subs.append(command[i + 2:end])
            out.append(f"{SUB_MARK}{len(subs) - 1}{SUB_MARK}")
            i = end + 1
            continue
        if ch == "`":
            end = _closing_tick(command, i + 1)
            if end < 0:
                return "", subs, "an unterminated backtick substitution"
            subs.append(command[i + 1:end])
            out.append(f"{SUB_MARK}{len(subs) - 1}{SUB_MARK}")
            i = end + 1
            continue
        if double:
            out.append(ch)
            double = ch != '"'
            i += 1
            continue
        if ch == "'":
            single = True
        elif ch == '"':
            double = True
        elif ch == "#" and (not out or out[-1][-1:] in " \t\n;&|()"):
            while i < len(command) and command[i] != "\n":
                i += 1
            continue
        elif ch == "\r":
            i += 1
            continue
        elif ch == "\n":
            out.append(" ; ")
            i += 1
            continue
        out.append(ch)
        i += 1
    if single or double:
        return "", subs, "an unbalanced quote"
    return "".join(out), subs, None


# ── Step 1-2: tokens and segments ───────────────────────────────────────────────────────────────


def _tokens(text: str) -> list[str]:
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _is_punctuation(token: str) -> bool:
    return bool(token) and all(ch in PUNCTUATION for ch in token)


def _segments(tokens: list[str]) -> list[list[str]]:
    """Words per command segment: split at `;`, `&&`, `||`, `|`, `&`, `(` and `)`; redirections dropped with their
    target and a digit-only file descriptor right before them."""
    segments: list[list[str]] = [[]]
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if _is_punctuation(token):
            if "<" in token or ">" in token:           # a redirection: its target goes with it
                if segments[-1] and segments[-1][-1].isdigit():
                    segments[-1].pop()
                i += 2
                continue
            segments.append([])
        else:
            segments[-1].append(token)
        i += 1
    return [segment for segment in segments if segment]


# ── Step 3-7: one segment ───────────────────────────────────────────────────────────────────────


def _base(word: str) -> str:
    name = word.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _dynamic(word: str) -> bool:
    return "$" in word or SUB_MARK in word


def _compose(base: str | None, new: str) -> str:
    if base is None or os.path.isabs(new):
        return new
    return f"{base.rstrip('/')}/{new}"


def _positionals(args: list[str], value_options: set, stop_flags: set = frozenset()):
    """`(positionals, unsure_reason, stopped)`: arguments minus options and their values."""
    found, i, options = [], 0, True
    while i < len(args):
        arg = args[i]
        i += 1
        if options and arg == "--":
            options = False
            continue
        if options and arg in stop_flags:
            return [], None, True
        if options and arg in value_options:
            i += 1
            continue
        if options and arg == "-":
            return found, "'-' (the previous branch) names no branch", False
        if options and arg.startswith("-"):
            continue
        found.append(arg)
    return found, None, False


def _refspec_source(spec: str) -> str:
    spec = spec[1:] if spec.startswith("+") else spec
    return spec.split(":", 1)[0]


def _segment(words: list[str]) -> tuple[Merge | None, str | None]:
    i = 0
    while i < len(words):
        word = words[i]
        if ASSIGNMENT_RE.match(word):
            i += 1
            continue
        if word in RESERVED:
            i += 1
            continue
        base = _base(word)
        if base in WRAPPERS and not _dynamic(word):
            i += 1
            while i < len(words) and words[i].startswith("-"):
                option = words[i]
                i += 1
                if option in WRAPPER_VALUE_OPTIONS.get(base, ()):
                    i += 1
            i += WRAPPER_POSITIONALS.get(base, 0)
            continue
        break
    if i >= len(words):
        return None, None
    program, rest = words[i], words[i + 1:]
    if _dynamic(program):
        if any(w in ("merge", "pull") for w in rest):
            return None, "the program is a variable or a command substitution"
        return None, None
    base = _base(program)
    if base in SHELLS or base == "cmd":
        text = " ".join(rest)
        if "git" in text and ("merge" in text or "pull" in text):
            return None, f"{base} runs a command line the reader cannot follow"
        return None, None
    if base != "git":
        # Fail closed: a program that is not git but runs it later on the line (unknown wrappers, xargs-like).
        for k, word in enumerate(rest):
            if _base(word) == "git" and any(w in ("merge", "pull") for w in rest[k + 1:]):
                return None, f"{base} runs git in a way the reader cannot follow"
        return None, None
    directory, j = None, 0
    while j < len(rest):
        token = rest[j]
        if token == "-C":
            if j + 1 >= len(rest):
                return None, "git -C without a directory"
            directory = _compose(directory, rest[j + 1])
            j += 2
        elif token in GLOBAL_VALUE_OPTIONS:
            j += 2
        elif token.split("=", 1)[0] in GLOBAL_VALUE_OPTIONS | {"--exec-path"} and "=" in token:
            j += 1
        elif token in GLOBAL_FLAGS:
            j += 1
        elif token.startswith("-"):
            if any(w in ("merge", "pull") for w in rest[j + 1:]):
                return None, f"unknown git option {token} before the subcommand"
            return None, None
        else:
            break
    if j >= len(rest):
        return None, None
    sub, args = rest[j], rest[j + 1:]
    if _dynamic(sub):
        return None, "the git subcommand is a variable or a command substitution"
    if sub == "merge":
        names, unsure, stopped = _positionals(args, MERGE_VALUE_OPTIONS, MERGE_NOTHING)
        if stopped:
            return None, None
        if unsure:
            return None, unsure
    elif sub == "pull":
        positional, unsure, _ = _positionals(args, PULL_VALUE_OPTIONS)
        if unsure:
            return None, unsure
        names = [_refspec_source(spec) for spec in positional[1:]]
    else:
        return None, None
    branches = [name for name in names if name and not ANON_REF_RE.search(name)]
    if any(_dynamic(name) for name in branches):
        return None, "a branch name is a variable or a command substitution"
    if not branches:
        return None, None
    return Merge(sub, branches, directory), None


def parse(command: str, _depth: int = 0) -> Parsed:
    """Every merge and pull the command line runs, with the branches each names."""
    if _depth > MAX_DEPTH:
        return Parsed([], "command substitutions nested too deeply")
    text, subs, error = _prepare(command)
    if error:
        return Parsed([], error)
    try:
        tokens = _tokens(text)
    except ValueError as exc:
        return Parsed([], f"unbalanced quotes ({exc})")
    merges: list[Merge] = []
    for inner in subs:
        found = parse(inner, _depth + 1)
        if found.unsure or found.merges:
            return Parsed([], found.unsure or "a command substitution runs a merge")
    for words in _segments(tokens):
        merge, unsure = _segment(words)
        if unsure:
            return Parsed([], unsure)
        if merge is not None:
            merges.append(merge)
    return Parsed(merges, None)


def marker_file_above(cwd) -> bool:
    """Whether a `.taskmaster-server.json` sits in `cwd` or a parent (a stdlib walk, no Git)."""
    from pathlib import Path
    try:
        current = Path(cwd).resolve()
    except OSError:
        current = Path(cwd)
    while True:
        if (current / ".taskmaster-server.json").exists():
            return True
        if current.parent == current:
            return False
        current = current.parent


# ── Names a branch is checked under ─────────────────────────────────────────────────────────────


def remotes_of(directory) -> list[str]:
    """The clone's remote names (`git remote`), or an empty list when Git can't say."""
    try:
        from taskmaster.bounded_run import run_bounded
        completed = run_bounded(["git", "remote"], cwd=str(directory), timeout=3, text=True, errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:  # noqa: BLE001
        return []
    if completed.returncode != 0:
        return []
    return [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]


def branch_names(branch: str, directory, remotes=remotes_of) -> list[str]:
    """The names the gate asks the store about: the branch as written first, then the shorter names it also
    means (`refs/heads/X` and, for a real remote `r`, `r/X` and `refs/remotes/r/X`)."""
    names = [branch]
    if branch.startswith("refs/heads/") and branch[len("refs/heads/"):]:
        names.append(branch[len("refs/heads/"):])
    elif "/" in branch:
        known = remotes(directory)
        stripped = branch[len("refs/remotes/"):] if branch.startswith("refs/remotes/") else branch
        remote, _, short = stripped.partition("/")
        if remote in known and short:
            names.append(short)
    return names
