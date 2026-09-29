<!-- User intent: the exact, ordered steps that turn a Taskmaster commit into a claude-tools
     release (version bump, Codex snapshot, version check, submodule pointer), plus how an
     installed plugin runs each entry point, so a release never depends on a dev venv. -->

# Runbook: release packaging

This runbook covers building the release. It does not authorize a push, a publish, an
install into a live Claude or Codex config, or a native cutover of a live project. Each
of those is a separate, recorded decision. The version number comes from the release
review. It is not chosen here.

## How the package runs

The plugin ships as source. Nothing is built or installed ahead of time: `uv` creates a
cached environment per script on first use, from the script's own PEP 723 header.

| Entry point | Launched by | Interpreter | Dependencies from |
|---|---|---|---|
| MCP server (Claude) | `.mcp.json`: `uv run ${CLAUDE_PLUGIN_ROOT}/backlog_server.py`, cwd = project | uv env `backlog-server-<hash>` (a venv launcher; the base interpreter runs as its child) | `backlog_server.py` header |
| MCP server (Codex) | `.codex-plugin/plugin.json`: `uv run backlog_server.py`, cwd `.` | same | same |
| Viewer | a thread in the MCP server process, on port 6800-6899 derived from the project root | server's | server's |
| Coordinator | first native write: `sys.executable -m taskmaster.coordinator.service --root <project>`, detached, `PYTHONPATH` = plugin root; exits after 300 s idle | the caller's interpreter (the server's uv env) | caller's |
| Managed Git helper | the coordinator: `sys._base_executable -I -S -c <bootstrap>`, assigned to a Windows Job Object before it may run Git | the real base interpreter, isolated, standard library only | none |
| Hooks | `hooks.json` via `run_hook.sh`: first of `$CLAUDE_HOOKS_PYTHON`, `python3`, `python`, `py -3` that is 3.9+ | the machine's Python, **not** the uv env | standard library only, except the merge stamp below |
| Merge stamp | `merge_recorder.py` resolves the merge's target branch and SHA synchronously, then runs the stamp in-process only if the hook interpreter is Python 3.11+ with fastmcp >=3.4,<4, pydantic 2, pyyaml and httpx; otherwise `uv run --script hooks/merge_recorder_stamp.py`, detached (breaking away from the host's Windows job when allowed; output to `hook.log`) | hook's or uv env `merge-recorder-stamp-<hash>` | `merge_recorder_stamp.py` header |
| Operator CLIs | `uv run <plugin>/taskmaster_cli.py {cutover,git,git-hook} ...` | uv env `taskmaster-cli-<hash>` | `taskmaster_cli.py` header |

`<plugin>` is the installed plugin directory, `${CLAUDE_PLUGIN_ROOT}` inside Claude Code
(for a local marketplace, `claude-tools/plugins/taskmaster`). From a source checkout,
`python -m taskmaster.native.cutover` and friends still work with the dev venv.

```
uv run <plugin>/taskmaster_cli.py cutover --root <project> --dry-run
uv run <plugin>/taskmaster_cli.py git commit -m "<message>"      # cwd = the project
uv run <plugin>/taskmaster_cli.py git status
uv run <plugin>/taskmaster_cli.py git-hook pre-commit            # inside a user's pre-commit hook
```

The dependency list is the same in `backlog_server.py`, `taskmaster/backlog_server.py`,
`taskmaster_cli.py`, `hooks/merge_recorder_stamp.py` and `pyproject.toml`.
`tests/test_packaging.py` fails if the lists differ or if a third-party import is not
declared. FastMCP is held below 4: a fresh environment resolved 4.0.10 before the pin, and
no test has run against it.

### When a merge shows up on the task

The merge recorder stamps the branch and commit that `HEAD` named when the hook fired,
never a later `HEAD`. On the uv path the stamp is detached, so `merge_gate_state` and
`merge_status` lag the merge by the stamp's run time: about 0.5 s with a warm
environment, 3-4 s the first time the environment is built, longer with a cold package
index. A merge gate evaluated in that window sees the previous rung.

On a native store the stamp writes only through a running coordinator; a hook never
starts one. If none is running (it exits after 5 minutes idle), the stamp is queued in
`.taskmaster/local/merge-stamps-pending.jsonl` and logged to `hook.log`. The MCP server
applies the queue after a native tool call, but only through a coordinator that is
already running: after any write, or after a read while one is up. A read never starts
a coordinator just to replay, so until the next write the rung may stay unrecorded.

Queue rules (`taskmaster/native_routing/merge_stamps.py`):

- Every queue change happens under an `O_EXCL` lock file, `merge-stamps.lock`, which
  records its owner's pid and time. A lock whose owner is dead, or that is older than
  10 minutes, is broken.
- A replayer moves the queue into `merge-stamps.claim` (owner on line 1), applies it
  without the lock, then puts back what it could not apply ahead of anything queued
  meanwhile. It never removes a claim another replayer took over.
- A line that does not parse, or lacks `task_id`, `rung` or `sha`, is moved to
  `merge-stamps-rejected.jsonl` with a log line. So is an entry refused outright, or one
  that failed 5 times. A coordinator that is unreachable, or a Conflict, keeps the entry
  queued. One bad line never blocks the rest.
- Whether a queued stamp is still wanted is decided from a snapshot of the task. Git
  ancestry is preferred; without it, a record from the same minute or later wins. The
  write carries that snapshot's task revision as its expected revision, so if another
  merge is recorded in between, the coordinator refuses it and the replayer decides
  again from fresh state.
- `hook.log` is capped at 1 MB (the last 256 KB is kept). An unreachable coordinator
  logs nothing on the server path.

Every reason a merge is not recorded, or not yet, is one line in
`.taskmaster/local/hook.log`.

### Wiring the pre-commit check into a project

Taskmaster never installs Git hooks. The `git-hook` command takes the plugin's path, and
a plugin installed through a marketplace cache lives in a versioned directory
(`~/.claude/plugins/cache/<marketplace>/taskmaster/<version>/`) that disappears when the
old version is cleaned up. A hook line with that path baked in then fails on every commit.
Resolve the path when the hook runs, and let the check fail open if the plugin is gone:

```sh
#!/bin/sh
# .git/hooks/pre-commit - Taskmaster projection check
cli="${TASKMASTER_CLI:-}"
if [ -z "$cli" ]; then
  cli=$(ls -d "$HOME"/.claude/plugins/cache/*/taskmaster/*/taskmaster_cli.py 2>/dev/null | sort -V | tail -n 1)
fi
if [ -z "$cli" ] || [ ! -f "$cli" ]; then
  echo "taskmaster pre-commit: taskmaster_cli.py not found; projection check skipped" >&2
  exit 0
fi
exec uv run "$cli" git-hook pre-commit
```

A local-marketplace install (`claude-tools/plugins/taskmaster`) has a stable path; set
`TASKMASTER_CLI` to it. The refusal message names the CLI path of the running plugin, which
is correct at the moment it is printed but is not a path to copy into a hook.

## Version strings

| # | Repository | File | Changed by |
|---|---|---|---|
| 1 | taskmaster | `.claude-plugin/plugin.json` `version` | `scripts/bump_version.py` |
| 2 | taskmaster | `.codex-plugin/plugin.json` `version` | `scripts/bump_version.py` |
| 3 | taskmaster | `pyproject.toml` `[project] version` | `scripts/bump_version.py` |
| 4 | taskmaster | `uv.lock`, the `taskmaster` package entry | `scripts/bump_version.py` |
| 5 | taskmaster | `README.md` shields badge (`-` written `--`) | `scripts/bump_version.py` |
| 6 | taskmaster | `CHANGELOG.md` `## <version>` heading | by hand, with the release notes |
| 7 | claude-tools | `.claude-plugin/marketplace.json` taskmaster `version` | by hand |
| 8 | claude-tools | `codex-plugins/taskmaster/.codex-plugin/plugin.json` `version` | generated by the sync script |
| 9 | claude-tools | `codex-plugins/taskmaster/.taskmaster-distribution.json` `version`, `upstream_commit` | generated by the sync script |

At runtime the server reads #1 (or #2 in the Codex snapshot, which has no
`.claude-plugin/`). It reports that version as MCP `serverInfo.version`, in the viewer's
`/api/identity`, and in board ETags. No Python `__version__` exists.

Versions must be `X.Y.Z` or `X.Y.Z-rc.N`. `bump_version.py` refuses anything else, because
the version also has to be valid for pip and uv (PEP 440).

## Release steps

Run them in this order. Steps 1-3 happen in the taskmaster repository, steps 4-8 in
claude-tools.

1. **Write the CHANGELOG entry.** Add `## <version>` to `CHANGELOG.md` with the release notes.
   The heading must match the version exactly: a release candidate needs `## 7.0.0-rc.1`, and
   `## 7.0.0-rc.1` does not count for `7.0.0`. The 7.0.0 entry is titled `## 7.0.0-rc.1` for the
   candidate; retitle it `## 7.0.0` when the final release is bumped.
2. **Bump.** `python scripts/bump_version.py <version>` rewrites strings #1-#5 and then
   runs the check. It exits 0 only when all five agree and the CHANGELOG heading exists.
   `python scripts/bump_version.py --check` repeats the check without changing anything.
3. **Test and commit** on the release branch: the full suite (`pytest tests -n 4`), then
   commit. The version test in `tests/test_codex_plugin_manifest.py` no longer contains a
   literal version.
4. **Advance the submodule.** In claude-tools, check out the release commit in
   `plugins/taskmaster`.
5. **Update the marketplace.** Set the taskmaster `version` in
   `.claude-plugin/marketplace.json`, and update its `description` if the release changes
   what it says.
6. **Regenerate the Codex snapshot.** Run `python scripts/sync_taskmaster_codex_distribution.py`.
   It copies the submodule's working tree and stamps `upstream_commit` with the
   submodule's `HEAD`. The claude-tools change listed below must be applied first, or the
   snapshot will not contain `taskmaster_cli.py`.
7. **Commit the gitlink, `codex-plugins/` and `marketplace.json` together.**
8. **Check.** Run `python scripts/check_plugin_version_bump.py --base origin/master`. It
   exits 0 when:
   - `plugin.json`, read through the committed gitlink, equals the marketplace version;
   - the Codex snapshot's manifest carries that version;
   - `.taskmaster-distribution.json` `upstream_commit` equals the gitlink SHA; and
   - the version changed since `--base` and the CHANGELOG has its heading.

**Why step 7 comes before step 8 (the HEAD-gitlink rule).** The check reads the taskmaster
version through the gitlink recorded in claude-tools' `HEAD`, not through the files checked
out in `plugins/taskmaster`, so that a lagging `git submodule update` cannot skew it. The
Codex snapshot, however, is read from the working tree. Until the new gitlink is
committed, these two sources disagree:

- Run the sync before committing the gitlink, and the check fails with "generated Codex
  distribution is stale against the submodule pointer".
- Change `marketplace.json` alone, and it fails with "OUT OF SYNC - plugin.json vX vs
  marketplace.json vY".

Both failures are expected mid-release. The check passes only after the commit in step 7.
The push-time guard, `.claude/hooks/check-version-bump.sh`, repeats the bump rule on
`git push`.

The whole sequence was rehearsed in a disposable clone with a placeholder version. Before
step 7 the check reported both failures above. After it, both
`check_plugin_version_bump.py taskmaster --base origin/master` and the all-plugins check
exited 0.

## Required claude-tools change (not committed there)

Add the new CLI entry to the Codex snapshot. This is the only claude-tools source change
the release needs:

```diff
--- a/scripts/sync_taskmaster_codex_distribution.py
+++ b/scripts/sync_taskmaster_codex_distribution.py
@@ FILES = (
     "backlog_server.py",
+    "taskmaster_cli.py",
     "viewer/index.html",
 )
```

Recommended alongside it: `check_plugin_version_bump.py` `_changelog_has` matches
`^##\s+{version}\b`, so a `## 7.0.0-rc.1` heading satisfies a `7.0.0` release. Make it
exact, as `bump_version.py` does:

```diff
-    return re.search(rf"^##\s+{re.escape(version)}\b", text, re.MULTILINE) is not None
+    return re.search(rf"^##\s+{re.escape(version)}(?=\s|$)", text, re.MULTILINE) is not None
```

Everything else in claude-tools is data for each release (steps 4-7): the gitlink, the
version in `marketplace.json`, and the regenerated `codex-plugins/taskmaster/`.
