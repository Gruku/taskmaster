#!/usr/bin/env bash
# User intent: one Linux test lane for the team server (team-server-016), run the same way from WSL, any Linux box
# or CI: it tests a commit (never a working tree) on a filesystem where POSIX modes stick, and fails on any skip
# that would hide the POSIX paths it exists to exercise.
#
#   bash scripts/linux_lane.sh [--source REPO] [--ref REF] [--tier serve|regression|deploy|twohost|full]...
#                              [--python SPEC] [--dir DIR] [--log-dir DIR]
#   tier twohost: the 015 two-host harness on Linux (loopback pair, T2's POSIX half), opt-in with -m twohost.
#
# From Windows: wsl -d Ubuntu -- bash /mnt/c/.../scripts/linux_lane.sh --ref <sha> --log-dir /mnt/d/...
# Exit codes: 0 every gating tier green; 1 a gating tier failed; 2 refused (arguments, filesystem); 3 setup failed.
# The `full` tier is informational: its result is reported and never changes the exit code.
set -uo pipefail

# Caddy for tier `deploy`: pinned, and checked against a hash committed here (not one fetched beside the binary).
CADDY_VERSION=2.11.7
CADDY_SHA512=a7a433a1b133efc3c8d10eb0b99d52a24b5ef5c322dc77f5282182b1c0402139ab83f3a99f0c52409df77d20123fb0b523edad8a66d8f5e49136197bf61ef0e7

SERVE_TESTS=(tests/test_serve_*.py tests/test_viewer_serve_hooks.py tests/test_packaging.py tests/test_deploy_files.py)
REGRESSION_TESTS=(tests/test_native_service*.py tests/test_identity_gate.py tests/test_team_identity.py
  tests/test_native_claims.py tests/test_native_routing_claims.py tests/test_bounded_run.py tests/test_server_fence.py
  tests/test_server_api.py tests/test_server_etag.py tests/test_server_instructions.py tests/test_server_task_write.py
  tests/test_viewer_write_gates.py tests/test_api_notes.py tests/test_api_handover_status.py
  tests/test_native_routing_viewer.py)
DEPLOY_TESTS=(tests/test_serve_deploy_smoke.py)
# 015's T2 subset (A3): principals, claims, merge gate, read endpoint, probes; plus the POSIX-only checks the harness skips on Windows.
TWOHOST_TESTS=(tests/test_twohost_principals.py tests/test_twohost_claims.py tests/test_twohost_merge_gate.py tests/test_twohost_read.py tests/test_twohost_probes.py tests/test_twohost_migration.py tests/test_twohost_fence.py
  tests/test_twohost_viewer.py tests/test_twohost_caddy.py)

# Skips the lane accepts, by JUnit classname::test name, each with the reason text it must carry. Any other skip in a
# gating tier fails the lane (design §3.2): matching reason words alone would let a new "needs fcntl" skip through.
ALLOWED_SKIPS='
tests.test_serve_viewer::test_a_real_browser_can_sign_in::@playwright/test is not installed
tests.test_native_service_sync::test_junctioned_sample_directory_does_not_block_unrelated_publication::_winapi
tests.test_native_service_sync::test_junctioned_job_file_is_a_per_job_refusal_not_a_drain_failure::_winapi
tests.test_deploy_files::test_actionlint_accepts_the_workflow_when_installed::actionlint is not installed
tests.test_twohost_migration::test_p_acl_the_windows_bundle_acl_grants_a_principal_beyond_owner_system_and_administrators::the DACL pin is Windows-only
'

die() { echo "linux_lane: $2" >&2; exit "$1"; }

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SOURCE="$SCRIPT_DIR/.."
REF=HEAD
TIERS=()
# An absolute path, not "python3": once uv holds a managed Python (after a --python 3.11 run) it prefers that over the system one.
PY=$(command -v python3 || echo python3)
DIR="${TASKMASTER_LANE_DIR:-$HOME/scratchbook/taskmaster/linux-lane}"
LOGDIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --source) SOURCE="${2:?}"; shift 2 ;;
    --ref) REF="${2:?}"; shift 2 ;;
    --tier) case "${2:-}" in serve|regression|deploy|twohost|full) TIERS+=("$2") ;; *) die 2 "unknown tier: ${2:-}" ;; esac; shift 2 ;;
    --python) PY="${2:?}"; shift 2 ;;
    --dir) DIR="${2:?}"; shift 2 ;;
    --log-dir) LOGDIR="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) die 2 "unknown argument: $1" ;;
  esac
done
[ ${#TIERS[@]} -gt 0 ] || TIERS=(serve regression)
# `wsl --exec` starts a non-login shell, so uv's default install dir may be missing from PATH.
command -v uv >/dev/null || PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
for tool in git uv; do command -v "$tool" >/dev/null || die 3 "$tool is not installed"; done

# POSIX modes must stick where the tests run: /mnt/* (drvfs, 9p) ignores chmod, so owner-only checks would fail there
# for the wrong reason.
mkdir -p "$DIR" || die 2 "cannot create $DIR"
probe=$(mktemp "$DIR/.mode-probe.XXXXXX") || die 2 "cannot write in $DIR"
chmod 600 "$probe"; mode=$(stat -c %a "$probe"); chmod 640 "$probe"; mode2=$(stat -c %a "$probe"); rm -f "$probe"
if [ "$mode" != 600 ] || [ "$mode2" != 640 ]; then
  die 2 "run the lane on a Linux filesystem (e.g. ~/...): $DIR ignores chmod (got $mode/$mode2); /mnt/* does"
fi

SHA=$(git -C "$SOURCE" rev-parse --verify "$REF^{commit}" 2>/dev/null) || die 2 "cannot resolve $REF in $SOURCE"
COMMON=$(git -C "$SOURCE" rev-parse --path-format=absolute --git-common-dir) || die 3 "cannot find the git dir of $SOURCE"
RUN="$DIR/runs/${SHA:0:12}-$(date +%s)"
mkdir -p "$RUN"
echo "linux_lane: testing $SHA in $RUN (tiers: ${TIERS[*]})"
git clone -q --no-checkout "$COMMON" "$RUN/repo" || die 3 "clone failed"
git -C "$RUN/repo" checkout -q --detach "$SHA" || die 3 "checkout of $SHA failed"
cd "$RUN/repo" || die 3 "no checkout"
export LANG=C.UTF-8 PYTHONIOENCODING=utf-8 UV_PROJECT_ENVIRONMENT="$RUN/repo/.venv"
uv sync --frozen --extra dev --python "$PY" -q > "$RUN/uv-sync.log" 2>&1 || { cat "$RUN/uv-sync.log" >&2; die 3 "uv sync failed"; }
PYVER=$(.venv/bin/python -c 'import sys; print(sys.version.split()[0])')
echo "linux_lane: python $PYVER"
if grep -q '^taskmaster-serve *=' pyproject.toml; then
  .venv/bin/taskmaster-serve --help > /dev/null || die 3 "the taskmaster-serve console entry does not run"
fi

fetch_caddy() {
  local cache="$DIR/cache" tarball="caddy_${CADDY_VERSION}_linux_amd64.tar.gz"
  mkdir -p "$cache/caddy-$CADDY_VERSION"
  if [ ! -x "$cache/caddy-$CADDY_VERSION/caddy" ]; then
    if [ ! -f "$cache/$tarball" ]; then
      curl -fsSL -o "$cache/$tarball.part" \
        "https://github.com/caddyserver/caddy/releases/download/v$CADDY_VERSION/$tarball" || return 1
      mv "$cache/$tarball.part" "$cache/$tarball"
    fi
    echo "$CADDY_SHA512  $cache/$tarball" | sha512sum -c --quiet - || { rm -f "$cache/$tarball"; return 1; }
    tar -xzf "$cache/$tarball" -C "$cache/caddy-$CADDY_VERSION" caddy || return 1
  fi
  export TASKMASTER_CADDY="$cache/caddy-$CADDY_VERSION/caddy"
}

existing() { local f; for f in "$@"; do [ -e "$f" ] && echo "$f"; done; }

check_skips() {  # $1 = junit xml; prints unexpected skips, returns 1 if any
  ALLOWED_SKIPS="$ALLOWED_SKIPS" .venv/bin/python - "$1" <<'PY'
import os, sys, xml.etree.ElementTree as ET
allowed = []
for line in os.environ["ALLOWED_SKIPS"].strip().splitlines():
    cls, name, reason = line.split("::", 2)
    allowed.append((cls, name, reason))
bad = []
for case in ET.parse(sys.argv[1]).iter("testcase"):
    skipped = case.find("skipped")
    if skipped is None:
        continue
    cls, name = case.get("classname", ""), case.get("name", "")
    reason = (skipped.get("message") or "") + (skipped.text or "")
    base = name.split("[", 1)[0]
    if not any(cls == c and base == n and r in reason for c, n, r in allowed):
        bad.append(f"{cls}::{name}: {reason.strip()[:200]}")
for item in bad:
    print("unexpected skip:", item)
sys.exit(1 if bad else 0)
PY
}

counts() {
  .venv/bin/python - "$1" <<'PY'
import sys, xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
t = {k: sum(int(s.get(k, 0)) for s in suites) for k in ("tests", "failures", "errors", "skipped")}
print(f"{t['tests'] - t['failures'] - t['errors'] - t['skipped']} passed, {t['failures']} failed, "
      f"{t['errors']} errors, {t['skipped']} skipped")
PY
}

STATUS=0
SUMMARY="$RUN/summary.txt"
{ echo "sha $SHA"; echo "python $PYVER"; } > "$SUMMARY"
for tier in "${TIERS[@]}"; do
  args=(-q -p no:cacheprovider --timeout=900 -rs --basetemp="$RUN/bt-$tier" --junitxml="$RUN/junit-$tier.xml")
  case "$tier" in
    serve) mapfile -t files < <(existing "${SERVE_TESTS[@]}" | grep -vx 'tests/test_serve_deploy_smoke.py') ;;  # that file runs in tier deploy, with Caddy
    regression) mapfile -t files < <(existing "${REGRESSION_TESTS[@]}") ;;
    deploy)
      fetch_caddy || { echo "deploy: setup failed (Caddy $CADDY_VERSION fetch or hash)" | tee -a "$SUMMARY"; STATUS=1; continue; }
      mapfile -t files < <(existing "${DEPLOY_TESTS[@]}") ;;
    twohost)
      # S13 through Caddy (Step 15) needs the real Caddy: a missing one is a setup failure, not a silent skip.
      fetch_caddy || { echo "twohost: setup failed (Caddy $CADDY_VERSION fetch or hash)" | tee -a "$SUMMARY"; STATUS=1; continue; }
      mapfile -t files < <(existing "${TWOHOST_TESTS[@]}"); args+=(-m twohost -p no:randomly -n 0) ;;
    full) files=(tests); args+=(-n 3) ;;
  esac
  if [ ${#files[@]} -eq 0 ]; then echo "$tier: no test files" | tee -a "$SUMMARY"; STATUS=1; continue; fi
  echo "linux_lane: tier $tier (${#files[@]} paths)"
  .venv/bin/python -m pytest "${args[@]}" "${files[@]}" > "$RUN/$tier.log" 2>&1
  rc=$?
  tail -n 25 "$RUN/$tier.log"
  line="$tier: pytest exit $rc"
  [ -f "$RUN/junit-$tier.xml" ] && line="$line; $(counts "$RUN/junit-$tier.xml")"
  if [ "$tier" = full ]; then
    echo "$line (informational)" | tee -a "$SUMMARY"
    grep -E '^(FAILED|ERROR) ' "$RUN/$tier.log" > "$RUN/full-failures.txt" || true
    continue
  fi
  skips_ok=0
  if [ -f "$RUN/junit-$tier.xml" ]; then
    check_skips "$RUN/junit-$tier.xml" > "$RUN/skips-$tier.txt" || skips_ok=1
    cat "$RUN/skips-$tier.txt"
  fi
  if [ $rc -ne 0 ] || [ $skips_ok -ne 0 ]; then STATUS=1; line="$line; FAIL"; else line="$line; ok"; fi
  echo "$line" | tee -a "$SUMMARY"
done

if [ -n "$LOGDIR" ]; then
  out="$LOGDIR/${SHA:0:12}-$(basename "$RUN" | cut -d- -f2)"
  mkdir -p "$out" && cp "$RUN"/*.log "$RUN"/*.txt "$out"/ 2>/dev/null; cp "$RUN"/junit-*.xml "$out"/ 2>/dev/null
  echo "linux_lane: logs copied to $out"
fi
echo "linux_lane: old runs under $DIR/runs take $(du -sh "$DIR/runs" 2>/dev/null | cut -f1)"
echo "linux_lane: $( [ $STATUS -eq 0 ] && echo GREEN || echo RED ) for $SHA"
exit $STATUS
