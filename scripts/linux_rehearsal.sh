#!/usr/bin/env bash
# User intent: run docs/runbooks/team-server-deploy.md's own `rehearsed` commands, as root, on a fresh Ubuntu 24.04
# (the Hetzner host's OS) in a throwaway WSL distro, then check what only root and a real systemd can show. A step that
# fails here is a bug in the how-to. The distro is removed afterwards, pass or fail (team-server-016 design §3.6).
#
#   bash scripts/linux_rehearsal.sh --source REPO --ref REF --rootfs FILE --install-dir DIR --log-dir DIR [--keep]
#
# Run it from Git Bash or WSL on a Windows machine with wsl.exe. It creates the distro ts016-rehearsal, never touches any
# other distro, and unregisters only that exact name. The `--inner` mode is the part that runs as root inside the distro.
set -uo pipefail
DISTRO=ts016-rehearsal
DOMAIN_FOR_REHEARSAL=tm.localhost

die() { echo "linux_rehearsal: $2" >&2; exit "$1"; }

# ----------------------------------------------------------------------------------------------------------------------
# inner: runs as root inside the throwaway distro
# ----------------------------------------------------------------------------------------------------------------------
inner() {
  [ "${WSL_DISTRO_NAME:-}" = "$DISTRO" ] || die 2 "--inner runs only inside the throwaway distro $DISTRO (this is '${WSL_DISTRO_NAME:-not WSL}')"
  [ "$(id -u)" = 0 ] || die 2 "--inner must run as root"
  local bundle="" sha="" logdir="" howto_rel="docs/runbooks/team-server-deploy.md"
  while [ $# -gt 0 ]; do
    case "$1" in
      --bundle) bundle="$2"; shift 2 ;; --sha) sha="$2"; shift 2 ;; --log-dir) logdir="$2"; shift 2 ;;
      *) die 2 "unknown inner argument: $1" ;;
    esac
  done
  cd /root
  mkdir -p "$logdir" /srv/rehearsal
  exec > >(tee -a "$logdir/rehearsal.log") 2>&1
  . /etc/os-release; echo "rehearsal: $PRETTY_NAME, systemd $(systemctl --version | head -1)"
  [ "$VERSION_ID" = 24.04 ] || die 2 "expected Ubuntu 24.04"
  until [ "$(systemctl is-system-running 2>/dev/null)" = running ] || [ "$(systemctl is-system-running 2>/dev/null)" = degraded ]; do sleep 1; done

  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq git python3 sudo curl ca-certificates >/dev/null
  git config --system safe.directory '*'      # rehearsal only: local-path remotes belong to other accounts
  # The tested commit, as the "Taskmaster plugin repository" and (for the how-to text) the source of the how-to itself.
  git clone -q --bare "$bundle" /srv/rehearsal/taskmaster.git
  git -C /srv/rehearsal/taskmaster.git cat-file -e "$sha^{commit}" || die 3 "the bundle does not contain $sha"

  export DOMAIN=$DOMAIN_FOR_REHEARSAL SHA=$sha TASKMASTER_URL=/srv/rehearsal/taskmaster.git
  export REPO_URL=/srv/rehearsal/origin.git BACKUP_URL=/srv/rehearsal/backup.git
  WORK=/root/rehearsal-work; rm -rf "$WORK"; mkdir -p "$WORK"
  git -C /srv/rehearsal/taskmaster.git show "$sha:$howto_rel" > "$WORK/howto.md"
  git -C /srv/rehearsal/taskmaster.git show "$sha:scripts/linux_rehearsal.sh" > "$WORK/script-at-sha.sh"
  python3 - "$WORK/howto.md" "$WORK" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read().replace("\r\n", "\n")
ids = []
with open(sys.argv[2] + "/run.sh", "w", encoding="utf-8") as out:
    for m in re.finditer(r"^```bash rehearsed:([a-z0-9-]+)\n(.*?)^```$", text, re.S | re.M):
        step, body = m.group(1), m.group(2)
        ids.append(step)
        out.write(f'echo; echo "### step {step}"\n')
        out.write(f'if declare -F before_{step} >/dev/null; then before_{step}; fi\n')
        out.write(body)
        out.write(f'if declare -F after_{step} >/dev/null; then after_{step}; fi\n')
open(sys.argv[2] + "/steps.txt", "w").write("\n".join(ids) + "\n")
PY
  echo "rehearsal: steps: $(tr '\n' ' ' < "$WORK/steps.txt")"

  CHECKS_FAILED=0
  check() {  # check "description" command...
    local what="$1"; shift
    if "$@" >/dev/null 2>&1; then echo "CHECK ok:   $what"; else echo "CHECK FAIL: $what"; CHECKS_FAILED=$((CHECKS_FAILED + 1)); fi
  }
  expect_eq() { if [ "$2" = "$3" ]; then echo "CHECK ok:   $1 ($2)"; else echo "CHECK FAIL: $1: got '$2', wanted '$3'"; CHECKS_FAILED=$((CHECKS_FAILED + 1)); fi; }

  # --- hooks: the few places the throwaway distro differs from the Hetzner host --------------------------------------
  after_install-files() {
    # No ACME here: certify every site (tm.localhost, viewer.tm.localhost) from Caddy's own CA instead. This is the only
    # edit to the shipped Caddyfile; tests/test_deploy_files.py runs it against the shipped file.
    python3 - <<'PY'
import re
p = "/etc/caddy/Caddyfile"
t = open(p).read()
sites = re.findall(r"^[^\s#{}][^\n]*\{$", t, re.M)          # top-level site blocks; the global options block is a bare `{`
t, n = re.subn(r"\ttls \{\n\t\tissuer acme \{\n\t\t\tdisable_http_challenge\n\t\t\}\n\t\}\n", "\ttls internal\n", t)
assert n == len(sites) >= 2, f"the shipped tls blocks changed: {n} replaced for sites {sites}"
t = t.replace("\tadmin off\n", "\tadmin off\n\tskip_install_trust\n", 1)
open(p, "w").write(t)
PY
    expect_eq "viewer.\$DOMAIN site in the Caddyfile" "$(grep -c "^viewer\.$DOMAIN {" /etc/caddy/Caddyfile)" 1
    expect_eq "viewer.\$DOMAIN origin in the unit" "$(grep -c "https://viewer\.$DOMAIN" /etc/systemd/system/taskmaster.service)" 1
    # Local-path backup remote: the unit's ProtectSystem=strict needs it writable (a GitHub remote needs no such path).
    install -d -m 0755 /etc/systemd/system/taskmaster.service.d
    printf '[Service]\nReadWritePaths=/srv/rehearsal/backup.git\n' > /etc/systemd/system/taskmaster.service.d/rehearsal.conf
    SCORE_SHIPPED=$(systemd-analyze security --offline=yes --no-pager /etc/systemd/system/taskmaster.service 2>&1 | sed -n 's/.*Overall exposure level for [^:]*: \([0-9.]*\).*/\1/p')
    echo "rehearsal: offline exposure of the installed unit: ${SCORE_SHIPPED:-unknown}"
  }

  before_host-clone() {
    # The "laptop": a separate account with a full dev checkout packs a solo project and pushes it.
    useradd -m -s /bin/bash laptop
    install -d -o laptop -g laptop /home/laptop/work
    git init -q --bare -b main /srv/rehearsal/origin.git && chmod -R a+rwX /srv/rehearsal/origin.git
    cat > /home/laptop/fixture.py <<'PY'
# A native project in a Git repo that tracks .taskmaster/, as a solo project does (tests/migration_helpers.py).
import pytest
from pathlib import Path
from native_twins import make_twins
from taskmaster import backlog_server as bs
from migration_helpers import make_source

patch = pytest.MonkeyPatch()
twins = make_twins(Path("/home/laptop/work"), patch,
                   lambda: bs.backlog_add_task(title="Build task", epic="test-epic", phase="dev"), visibility=None)
with twins.at(twins.native):
    make_source(twins.native)
patch.undo()
print(twins.native)
PY
    chown laptop:laptop /home/laptop/fixture.py
    sudo -u laptop -H bash -euo pipefail <<LAPTOP
      git clone -q /srv/rehearsal/taskmaster.git ~/tm && git -C ~/tm checkout -q --detach $SHA
      export PATH=/usr/local/bin:\$PATH UV_PYTHON=/usr/bin/python3 UV_PYTHON_DOWNLOADS=never
      cd ~/tm && uv sync --frozen --extra dev -q
      PYTHONPATH=\$PWD/tests:\$PWD .venv/bin/python ~/fixture.py
      git -C ~/work/native remote add origin /srv/rehearsal/origin.git
      git -C ~/work/native push -q -u origin main
      .venv/bin/python taskmaster_cli.py serve init pack --root ~/work/native --url https://$DOMAIN/mcp --out ~/proj.tmbundle
      git -C ~/work/native push -q origin main
LAPTOP
    install -m 0600 /home/laptop/proj.tmbundle /root/proj.tmbundle
    # The backup repository, owned by the service user (a real one lives on GitHub, behind the deploy key).
    git init -q --bare -b taskmaster-state /srv/rehearsal/backup.git && chown -R taskmaster:taskmaster /srv/rehearsal/backup.git
    chmod a+rx /srv/rehearsal && chmod -R a+rX /srv/rehearsal/origin.git /srv/rehearsal/taskmaster.git
  }

  after_enable() {
    export CURL_CA_BUNDLE=/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt
    local i; for i in $(seq 1 60); do [ -s "$CURL_CA_BUNDLE" ] && curl -sS -o /dev/null --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/" 2>/dev/null && break; sleep 1; done
  }

  after_public-path() { rehearsal_checks; }

  after_ssh-config() {   # no SSH authentication runs here; at least prove which key each alias selects
    check "ssh -G github.com selects the read-only code key" bash -c "sudo -u taskmaster -H ssh -G github.com | grep -qi 'identityfile /srv/taskmaster/.ssh/code_deploy_key'"
    check "ssh -G github-backup selects the backup key" bash -c "sudo -u taskmaster -H ssh -G github-backup | grep -qi 'identityfile /srv/taskmaster/.ssh/backup_deploy_key'"
    check "github-backup resolves to github.com" bash -c "sudo -u taskmaster -H ssh -G github-backup | grep -qi '^hostname github.com'"
  }

  after_shred-keys() {
    expect_eq "plaintext key files left under /srv/taskmaster" "$(find /srv/taskmaster -name '*.key' | wc -l)" 0
    echo "rehearsal: checks failed: $CHECKS_FAILED"
  }

  rehearsal_checks() {
    set +e
    echo; echo "### rehearsal-only checks"
    local nonlo; nonlo=$(ss -H -ltn | awk '{print $4}' | grep -Ev '^(127\.|\[::1\])' || true)
    echo "non-loopback listeners: $(echo $nonlo)"
    check "nothing listens on port 80" bash -c "! ss -H -ltn | awk '{print \$4}' | grep -E ':80\$'"
    # :53 is WSL's own DNS forwarder; a Hetzner host has none.
    check "only :443 and :22 are non-loopback listeners" bash -c "! ss -H -ltn | awk '{print \$4}' | grep -Ev '^(127\.|\[::1\])' | grep -Ev ':(443|22|53)\$'"
    check "7470 is on 127.0.0.1" bash -c "ss -H -ltn | awk '{print \$4}' | grep -q '^127.0.0.1:7470\$'"
    check "7471 is on 127.0.0.1" bash -c "ss -H -ltn | awk '{print \$4}' | grep -q '^127.0.0.1:7471\$'"
    expect_eq "files under /srv/taskmaster not owned by taskmaster" "$(find /srv/taskmaster ! -user taskmaster | wc -l)" 0
    find /srv/taskmaster ! -type l -perm /077 | sed 's/^/  readable by group or other: /'
    expect_eq "files under /srv/taskmaster readable by group or other" "$(find /srv/taskmaster ! -type l -perm /077 | wc -l)" 0
    expect_eq "store.db mode (014 owner-only install)" "$(stat -c %a /srv/taskmaster/project/.taskmaster/local/store.db)" 600
    expect_eq ".taskmaster/local mode (014 owner-only install)" "$(stat -c %a /srv/taskmaster/project/.taskmaster/local)" 700
    expect_eq "/opt/taskmaster owner" "$(stat -c %U:%G "$(readlink -f /opt/taskmaster/current)")" root:root
    check "taskmaster cannot write the release" bash -c "! sudo -u taskmaster test -w \"$(readlink -f /opt/taskmaster/current)/taskmaster\""
    expect_eq "systemctl show Environment" "$(systemctl show -p Environment --value taskmaster)" ""
    local live; live=$(systemd-analyze security taskmaster --no-pager 2>&1 | sed -n 's/.*Overall exposure level for [^:]*: \([0-9.]*\).*/\1/p')
    echo "rehearsal: LIVE exposure level: ${live:-unknown}   (offline, installed unit: ${SCORE_SHIPPED:-unknown})"
    check "live exposure level is 2.0 or lower" python3 -c "import sys; sys.exit(0 if float('${live:-99}') <= 2.0 else 1)"
    check "MemoryMax is 1G" bash -c "[ \"\$(systemctl show -p MemoryMax --value taskmaster)\" = 1073741824 ]"
    # Through Caddy, from the non-loopback address.
    local pub key; pub=$(ip -4 route get 1.1.1.1 | awk '{for (i=1;i<=NF;i++) if ($i=="src") print $(i+1); exit}')
    key=$(cat /srv/taskmaster/ann.key)
    local init='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"rehearsal","version":"1"}}}'
    local code
    for want_path in "200:Bearer $key:/mcp" "401:none:/mcp" "404:none:/" "404:none:/rpc"; do
      IFS=: read -r want auth path <<<"$want_path"
      if [ "$auth" = none ]; then
        code=$(curl -sS -o /dev/null -w '%{http_code}' --resolve "$DOMAIN:443:$pub" -X POST -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$init" "https://$DOMAIN$path")
      else
        code=$(curl -sS -o /dev/null -w '%{http_code}' --resolve "$DOMAIN:443:$pub" -H "Authorization: $auth" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$init" "https://$DOMAIN$path")
      fi
      expect_eq "through Caddy $path (${auth%% *})" "$code" "$want"
    done
    # The viewer site (team-server-028): its own host and certificate, only the viewer's routes. Caddy serves the public
    # static assets from the release itself, so the main.js 200 also proves the caddy user can read the release.
    local vhost="viewer.$DOMAIN"
    for want_path in "200:/login" "404:/backlog.yaml" "200:/static/v3/js/main.js" "401:/api/board" "404:/mcp"; do
      IFS=: read -r want path <<<"$want_path"
      code=$(curl -sS -o /dev/null -w '%{http_code}' --resolve "$vhost:443:$pub" "https://$vhost$path")
      expect_eq "viewer through Caddy $path" "$code" "$want"
    done
    # Exit-code policy (design §5.2).
    systemctl stop taskmaster; sleep 8
    expect_eq "stop: Result" "$(systemctl show -p Result --value taskmaster)" success
    expect_eq "stop: ExecMainStatus" "$(systemctl show -p ExecMainStatus --value taskmaster)" 0
    expect_eq "stop: stays stopped (no restart)" "$(systemctl is-active taskmaster || true)" inactive
    systemctl start taskmaster; sleep 4
    local pid; pid=$(systemctl show -p MainPID --value taskmaster); kill -9 "$pid"; sleep 9
    expect_eq "kill -9: restarted with a new pid" "$([ "$(systemctl show -p MainPID --value taskmaster)" != "$pid" ] && [ "$(systemctl show -p MainPID --value taskmaster)" != 0 ] && echo yes || echo no)" yes
    expect_eq "kill -9: NRestarts" "$(systemctl show -p NRestarts --value taskmaster)" 1
    systemctl stop taskmaster
    # A refused configuration (exit 2) must not restart-loop.
    install -d /etc/systemd/system/taskmaster.service.d
    printf '[Service]\nExecStart=\nExecStart=%s --root %s --host 127.0.0.1 --port 7470 --allowed-host "bad host!"\n' \
      "$SERVE" "$ROOT" > /etc/systemd/system/taskmaster.service.d/zz-refuse.conf
    systemctl daemon-reload; systemctl reset-failed taskmaster 2>/dev/null; systemctl start taskmaster 2>/dev/null; sleep 9
    expect_eq "refused config: exit status" "$(systemctl show -p ExecMainStatus --value taskmaster)" 2
    expect_eq "refused config: not restarted" "$(systemctl show -p NRestarts --value taskmaster)" 0
    expect_eq "refused config: state" "$(systemctl is-active taskmaster || true)" failed
    rm -f /etc/systemd/system/taskmaster.service.d/zz-refuse.conf; systemctl daemon-reload; systemctl reset-failed taskmaster
    # Port 7470 held by someone else: exit 69, restarts, then `failed` after the start limit.
    python3 -c "import socket,time; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('127.0.0.1',7470)); s.listen(); time.sleep(150)" &
    local holder=$!; sleep 1
    systemctl start taskmaster 2>/dev/null
    local n; for n in $(seq 1 100); do [ "$(systemctl is-active taskmaster || true)" = failed ] && break; sleep 1; done
    expect_eq "port held: exit status" "$(systemctl show -p ExecMainStatus --value taskmaster)" 69
    check "port held: restarted at least once" bash -c "[ \"\$(systemctl show -p NRestarts --value taskmaster)\" -ge 1 ]"
    expect_eq "port held: failed after the start limit" "$(systemctl is-active taskmaster || true)" failed
    kill "$holder" 2>/dev/null; wait "$holder" 2>/dev/null
    systemctl reset-failed taskmaster; systemctl start taskmaster; sleep 5
    expect_eq "recovers once the port is free" "$(systemctl is-active taskmaster)" active
    journalctl -u taskmaster --no-pager > "$logdir/taskmaster-journal.txt"; journalctl -u caddy --no-pager > "$logdir/caddy-journal.txt"
    systemd-analyze security taskmaster --no-pager > "$logdir/systemd-analyze-security.txt" 2>&1
    echo "rehearsal: checks failed: $CHECKS_FAILED"
    set -e
  }

  # --- run the how-to's blocks, in order, in one shell ---------------------------------------------------------------
  set -e
  # shellcheck disable=SC1091
  . "$WORK/run.sh"
  set +e
  echo; echo "rehearsal: how-to steps finished; checks failed: $CHECKS_FAILED"
  [ "$CHECKS_FAILED" = 0 ] && echo "REHEARSAL GREEN for $sha" || echo "REHEARSAL RED for $sha"
  [ "$CHECKS_FAILED" = 0 ]
}

# ----------------------------------------------------------------------------------------------------------------------
# outer: creates the distro, runs inner, copies logs, unregisters
# ----------------------------------------------------------------------------------------------------------------------
if [ "${1:-}" = --inner ]; then shift; inner "$@"; exit $?; fi

SOURCE="" REF=HEAD ROOTFS="" INSTALL_DIR="" LOGDIR="" KEEP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --source) SOURCE="${2:?}"; shift 2 ;; --ref) REF="${2:?}"; shift 2 ;; --rootfs) ROOTFS="${2:?}"; shift 2 ;;
    --install-dir) INSTALL_DIR="${2:?}"; shift 2 ;; --log-dir) LOGDIR="${2:?}"; shift 2 ;; --keep) KEEP=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) die 2 "unknown argument: $1" ;;
  esac
done
[ -n "$SOURCE" ] && [ -n "$ROOTFS" ] && [ -n "$INSTALL_DIR" ] && [ -n "$LOGDIR" ] || die 2 "need --source, --rootfs, --install-dir and --log-dir"
command -v wsl.exe >/dev/null || die 2 "wsl.exe not found: run this from Git Bash or WSL on Windows"
SHA=$(git -C "$SOURCE" rev-parse --verify "$REF^{commit}") || die 2 "cannot resolve $REF"
distros() { wsl.exe -l -q 2>/dev/null | tr -d '\0\r'; }
if distros | grep -qx "$DISTRO"; then die 2 "a distro named $DISTRO already exists; remove it yourself first (wsl --unregister $DISTRO)"; fi
mkdir -p "$LOGDIR" "$INSTALL_DIR"
BUNDLE="$LOGDIR/taskmaster-$SHA.bundle"
git -C "$SOURCE" bundle create "$BUNDLE" "$REF" >/dev/null 2>&1 || die 3 "bundle failed (REF must be a branch or HEAD)"
unwin() { if command -v cygpath >/dev/null; then cygpath -m "$1" | sed 's#^\([A-Za-z]\):#/mnt/\L\1#'; else echo "$1"; fi; }
cleanup() {
  if [ "$KEEP" = 0 ] && distros | grep -qx "$DISTRO"; then
    echo "linux_rehearsal: unregistering $DISTRO (only this distro)"
    wsl.exe --unregister "$DISTRO" >/dev/null 2>&1
  fi
}
trap cleanup EXIT
echo "linux_rehearsal: importing $DISTRO from $ROOTFS into $INSTALL_DIR"
MSYS_NO_PATHCONV=1 wsl.exe --import "$DISTRO" "$(cygpath -w "$INSTALL_DIR" 2>/dev/null || echo "$INSTALL_DIR")" "$(cygpath -w "$ROOTFS" 2>/dev/null || echo "$ROOTFS")" --version 2 || die 3 "import failed"
MSYS_NO_PATHCONV=1 wsl.exe -d "$DISTRO" -u root --exec sh -c 'printf "[boot]\nsystemd=true\n" > /etc/wsl.conf' || die 3 "wsl.conf failed"
wsl.exe --terminate "$DISTRO" >/dev/null 2>&1
sleep 3
INNER_SCRIPT=$(unwin "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/linux_rehearsal.sh")
MSYS_NO_PATHCONV=1 wsl.exe -d "$DISTRO" -u root --exec bash "$INNER_SCRIPT" --inner \
  --bundle "$(unwin "$BUNDLE")" --sha "$SHA" --log-dir "$(unwin "$LOGDIR")"
rc=$?
echo "linux_rehearsal: inner exit $rc"
exit $rc
