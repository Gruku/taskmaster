<!-- User intent: one tested how-to for putting the team's Taskmaster server on a fresh Hetzner Ubuntu 24.04 box behind
     Caddy HTTPS, and for running it afterwards (keys, backups, upgrades, rollback, checklist). Its `rehearsed` blocks are
     the exact commands scripts/linux_rehearsal.sh runs as root in a throwaway Ubuntu 24.04 distro, so a step that fails
     there is a bug in this page (team-server-016). -->

# Runbook: deploying the team server on Hetzner

**Audience.** The operator, over SSH, as root (or `sudo -i`) on a dedicated Ubuntu 24.04 Hetzner Cloud VM that runs
nothing else: no Docker, no other login users. The design is
[`2026-11-01-team-server-016-linux-deploy-design.md`](../specs/2026-11-01-team-server-016-linux-deploy-design.md);
the migration of an existing solo project is [`team-server-migration.md`](team-server-migration.md).

**How to read the blocks.** Every command block is tagged. `rehearsed:<id>` blocks are run, in order, by
`scripts/linux_rehearsal.sh` as root in a throwaway Ubuntu 24.04 distro, with the variables below set to rehearsal
values. `not-rehearsed` blocks need something the throwaway distro lacks (your real domain and DNS, the Hetzner
network, GitHub, a firewall that works in WSL), and each says why. Run the blocks of one section in one root shell: they
share variables.

**Three repositories.** `TASKMASTER_URL` is the public Taskmaster plugin repository: the release is a checkout of it, with
no credential. The other two are the team's:

| Repository | Host credential | Why |
|---|---|---|
| The team's project repository (`REPO_URL`) | a **read-only** deploy key | The host clones and pulls it. It holds no write credential for the code, so a compromised host can't change the code. |
| A dedicated private **backup** repository (`BACKUP_URL`) | a **repo-scoped write** deploy key | The server pushes its state export there (`taskmaster-state` branch). It can push nothing else. |

The server marker is committed and pushed from **your laptop**, never from the host.

## 0. Before you start

- A Hetzner Cloud VM, Ubuntu 24.04, an SSH key in it, **password login off**, and a Hetzner Cloud Firewall attached. Its inbound rules: TCP 22 (from your address if you can) and TCP 443 from anywhere; nothing else, in particular not 80, 7470, 7471 or any UDP port (section 6 mirrors them in ufw).
- DNS A (and AAAA, if you use IPv6) records for **two names**, both pointing at the VM: the public name
  (`tm.example.com`, the MCP and read/write API) and the viewer's own name, `viewer.` in front of it
  (`viewer.tm.example.com`). The viewer must be its own host so its session cookie never shares a site with the MCP
  host; the daemon refuses a `--viewer-origin` on an `--allowed-host` (exit 2). The shipped files say
  `tm.example.com`; section 3 replaces it in each place that matters (the two Caddy site names, the unit's
  `--allowed-host` and its `--viewer-origin`), and a parity test keeps the unit and the Caddyfile equal (a mismatch
  makes every request return 421).
- The commit SHA the Linux lane reported green (`bash scripts/linux_lane.sh --tier serve --tier regression --tier deploy`
  on that SHA, in WSL or CI, **never on this host**: as root the owner-only checks behave differently, and as
  `taskmaster` its test daemons would land beside the live one). The rehearsal passed on the same commit.
- The tested Caddy is **2.11.7** and the tested uv is **0.11.2**. Both are pinned below.
- Go-live blockers: every task in the pre-go-live checklist (section 12) is merged and its review closed.

```bash not-rehearsed
# Edit these lines; the rehearsal sets its own. SHA is the full 40-character commit the lane tested.
export DOMAIN=tm.example.com
export SHA=0000000000000000000000000000000000000000
export TASKMASTER_URL=https://github.com/OWNER/taskmaster.git     # the Taskmaster plugin repository (public)
export REPO_URL=git@github.com:OWNER/PROJECT.git                  # the team's project repository
export BACKUP_URL=git@github-backup:OWNER/PROJECT-backup.git     # its backup repository, via the github-backup alias (section 2)
```

Why not rehearsed: the real values only exist for the real host.

```bash rehearsed:helpers
export SERVE=/opt/taskmaster/current/.venv/bin/taskmaster-serve
export ROOT=/srv/taskmaster/project
# Run a command as the service user, under umask 077, from its home (never from a directory that holds a taskmaster/ package). Never run the admin CLI as root: the owner-only checks refuse a
# root-owned tree, or create root-owned files the daemon then refuses.
tm() { sudo -u taskmaster -H bash -c 'umask 077; cd "$HOME"; exec "$@"' _ "$@"; }
```

## 1. Packages and the two pinned tools

```bash rehearsed:packages
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git curl ca-certificates gpg ufw python3 sudo openssh-client debian-keyring debian-archive-keyring apt-transport-https
```

uv builds the virtualenv from the lock. It comes from a pinned release, checked against a checksum written here (not one
fetched beside it). The host's Python is the distro's 3.12, never a uv-managed one: `ProtectHome=yes` hides
`/root/.local`, so a uv-managed interpreter would dangle for the service.

```bash rehearsed:uv
UV_VERSION=0.11.2
UV_SHA256=7ac2ca0449c8d68dae9b99e635cd3bc9b22a4cb1de64b7c43716398447d42981
curl -fsSL -o /tmp/uv.tar.gz "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-unknown-linux-gnu.tar.gz"
echo "$UV_SHA256  /tmp/uv.tar.gz" | sha256sum -c -
tar -xzf /tmp/uv.tar.gz -C /tmp
install -m 0755 /tmp/uv-x86_64-unknown-linux-gnu/uv /usr/local/bin/uv
uv --version
```

Caddy comes from Caddy's own signed apt repository at the tested version and is then **held**, so a routine
`apt upgrade` can't move it (Ubuntu's own archive carries 2.6.2). The package starts its default site on port 80 at
install, so the block stops and disables it at once; section 7 enables it with the real Caddyfile.

```bash rehearsed:caddy-install
curl -fsSL 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -fsSL 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' -o /etc/apt/sources.list.d/caddy-stable.list
apt-get update
apt-get install -y caddy=2.11.7
apt-mark hold caddy
systemctl disable --now caddy
install -d -o caddy -g caddy -m 0750 /var/log/caddy
caddy version
```

A Caddy upgrade later means: bump the pin in `scripts/linux_lane.sh`, re-run tier `deploy`, then `apt-mark unhold caddy`,
upgrade, `apt-mark hold caddy`.

## 2. The service user and the release

```bash rehearsed:user
useradd --system --home-dir /srv/taskmaster --no-create-home --shell /usr/sbin/nologin taskmaster
install -d -o taskmaster -g taskmaster -m 0700 /srv/taskmaster     # not --create-home: it copies world-readable /etc/skel dotfiles
install -d -o taskmaster -g taskmaster -m 0700 /srv/taskmaster/.ssh
tm git config --global user.name "Taskmaster server"
tm git config --global user.email "taskmaster@$DOMAIN"
```

Two deploy keys, both generated on the host so no private key ever travels. Register each **public** key on GitHub: the
code key as a read-only deploy key on the project repository, the backup key as a deploy key **with write access** on the
backup repository only.

```bash rehearsed:ssh-keys
tm ssh-keygen -q -t ed25519 -N '' -C "taskmaster code (read-only)" -f /srv/taskmaster/.ssh/code_deploy_key
tm ssh-keygen -q -t ed25519 -N '' -C "taskmaster backup (write)" -f /srv/taskmaster/.ssh/backup_deploy_key
cat /srv/taskmaster/.ssh/code_deploy_key.pub /srv/taskmaster/.ssh/backup_deploy_key.pub
```

Pin the Git host's key. Compare the fingerprints with the host's published ones before you trust the file.

```bash not-rehearsed
tm bash -c 'ssh-keyscan -t ed25519 github.com > /srv/taskmaster/.ssh/known_hosts'
tm ssh-keygen -lf /srv/taskmaster/.ssh/known_hosts      # compare with https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
```

Why not rehearsed: it needs GitHub; the rehearsal uses local bare repositories.

Both keys are used through one SSH config the service user reads. GitHub binds a deploy key to one repository, so the backup repository needs its own host alias, `github-backup`; with a single `github.com` entry the backup push would use the read-only key and be denied. The backup push key is the only write credential on
the host.

```bash rehearsed:ssh-config
tm bash -c 'cat > /srv/taskmaster/.ssh/config' <<EOF
Host github.com
    User git
    IdentitiesOnly yes
    IdentityFile /srv/taskmaster/.ssh/code_deploy_key
    UserKnownHostsFile /srv/taskmaster/.ssh/known_hosts
    StrictHostKeyChecking yes
    BatchMode yes

Host github-backup
    HostName github.com
    User git
    IdentitiesOnly yes
    IdentityFile /srv/taskmaster/.ssh/backup_deploy_key
    UserKnownHostsFile /srv/taskmaster/.ssh/known_hosts
    StrictHostKeyChecking yes
    BatchMode yes
EOF
chmod 0600 /srv/taskmaster/.ssh/config
```

The rehearsal checks this file with `ssh -G` (which key each alias selects) but runs no SSH authentication: its remotes are local paths.

The release is a checkout of the **tested commit** plus `uv sync --frozen`, not a wheel (the package finds its viewer
and version through the checkout). Releases live side by side and a symlink picks the live one, so an upgrade never
mixes old and new code in a running process and rollback is the same switch back. Root owns the tree, so a compromised
daemon can't change its own code. Never pass `--no-editable` to uv.

```bash rehearsed:release
mkdir -p /opt/taskmaster/releases
BUILD=/opt/taskmaster/releases/.build-${SHA:0:12}
rm -rf "$BUILD"
git clone --quiet --no-checkout "$TASKMASTER_URL" "$BUILD"
git -C "$BUILD" checkout --quiet --detach "$SHA"
test "$(git -C "$BUILD" rev-parse HEAD)" = "$SHA"           # what was tested is what runs
VERSION=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$BUILD/.claude-plugin/plugin.json")
RELEASE=/opt/taskmaster/releases/$VERSION-${SHA:0:12}
test "$(readlink -f /opt/taskmaster/current)" != "$RELEASE" || { echo "$RELEASE is the live release; refusing to replace it"; exit 1; }
rm -rf "$RELEASE"; mv "$BUILD" "$RELEASE"
cd "$RELEASE"
UV_PYTHON=/usr/bin/python3 UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy uv sync --frozen
# The service can't write __pycache__ into a root-owned tree, so compile now (the build digest hashes *.py only).
.venv/bin/python -m compileall -q taskmaster .venv/lib
cd /
ln -sfn "$RELEASE" /opt/taskmaster/current
"$SERVE" --help > /dev/null
echo "release: $RELEASE"
```

## 3. Replace the placeholder domain, then install the files

The unit and the Caddyfile are copied out of the checkout and edited **in `/etc`**, so the root-owned release stays
byte-identical to the tested commit.

```bash rehearsed:install-files
install -d -m 0755 /etc/taskmaster
install -m 0644 /opt/taskmaster/current/deploy/systemd/taskmaster.service /etc/systemd/system/taskmaster.service
install -m 0644 /opt/taskmaster/current/deploy/caddy/Caddyfile /etc/caddy/Caddyfile
sed -i "s/tm\.example\.com/$DOMAIN/g" /etc/systemd/system/taskmaster.service /etc/caddy/Caddyfile   # also turns viewer.tm.example.com into viewer.$DOMAIN
sed -i "s/ops@example\.com/ops@$DOMAIN/" /etc/caddy/Caddyfile        # the ACME contact; use a real mailbox
grep -n "$DOMAIN" /etc/systemd/system/taskmaster.service /etc/caddy/Caddyfile
```

Linear is optional. Skip this block unless you use it; the token lives only in this root-only file, never in the unit
(`systemctl show` prints `Environment=`, and unit files are world-readable).

```bash not-rehearsed
install -m 0600 -o root -g root /opt/taskmaster/current/deploy/taskmaster.env.example /etc/taskmaster/taskmaster.env
$EDITOR /etc/taskmaster/taskmaster.env      # uncomment and set TASKMASTER_LINEAR_TOKEN
```

Why not rehearsed: there is no Linear workspace to point at. With Linear, the unit also needs outbound access to
`api.linear.app`; without Linear **and** without the backup push, the unit's commented `IPAddressDeny` lines may be
enabled.

With Linear, give the token write access only to the Linear team this project syncs to, and make sure every key holder
is also a member of that Linear team: the server pushes task changes to Linear with the operator's token, so a member
must not reach, through it, issues they couldn't edit in Linear themselves. On a team server only the operator links or
unlinks a task to a Linear issue (`backlog_linear` link and unlink), since a link chooses which issue the token writes.

## 4. The host clone, the backup remote and the store (team-server-014)

The store is created once, from the bundle your laptop packs. **There is no manual path.** Read
[`team-server-migration.md`](team-server-migration.md) first; the order is:

0. On your laptop: `git pull`, then `backlog_sync`, so the store takes the `.taskmaster/` changes teammates pushed.
1. On your laptop: confirm every teammate is on a build that includes the split-brain fence **and has handed over any
   backlog changes not yet in your store** (edits in their own local store, `.taskmaster/` changes on unmerged branches;
   the server starts from your store as it is at the pack, and nothing else is migrated). See the rollout rule, below.
2. On your laptop: `uv run <plugin>/taskmaster_cli.py serve init pack --root <laptop clone> --url https://$DOMAIN/mcp --out ~/proj.tmbundle`.
   It writes `.taskmaster-server.json`, untracks `.taskmaster/` in a local commit and pushes nothing.
3. On your laptop: `git push origin main`, then copy the bundle to the host (`scp ~/proj.tmbundle root@host:/root/`).
4. On the host (this section): clone **after** that push, add the backup remote, run `init --from`.

```bash not-rehearsed
# On the laptop; PLUGIN is the Taskmaster plugin checkout (the rehearsal runs the equivalent in a "laptop" account with a full dev checkout):
uv run "$PLUGIN/taskmaster_cli.py" serve init pack --root "$LAPTOP_CLONE" --url "https://$DOMAIN/mcp" --out ~/proj.tmbundle
git -C "$LAPTOP_CLONE" push origin main
scp ~/proj.tmbundle root@"$DOMAIN":/root/proj.tmbundle
```

Why not rehearsed on the host: it runs on your laptop. The rehearsal performs the same two commands in a separate
account inside the throwaway distro.

```bash rehearsed:host-clone
tm git clone --quiet "$REPO_URL" "$ROOT"
tm git -C "$ROOT" remote add backup "$BACKUP_URL"
install -o taskmaster -g taskmaster -m 0600 /root/proj.tmbundle /srv/taskmaster/proj.tmbundle
```

Both remotes must answer before `init`. On GitHub this is the first real test of the deploy keys; the rehearsal's local remotes don't test authentication.

```bash rehearsed:remote-check
tm git ls-remote "$REPO_URL" > /dev/null
tm git ls-remote "$BACKUP_URL" > /dev/null
```

`init` refuses unless HEAD carries the bundle's marker and tracks no `.taskmaster/`. It publishes the store, verifies it,
makes the first export to `taskmaster-state` and pushes it to `backup`. Run it as the service user, with the installed
console script (never `python -m taskmaster.serve`: from a directory that holds its own `taskmaster/` package that
imports the project's copy).

```bash rehearsed:init
cd "$ROOT"
tm "$SERVE" init --root "$ROOT" --from /srv/taskmaster/proj.tmbundle --export-push backup
cd /
rm -f /srv/taskmaster/proj.tmbundle /root/proj.tmbundle        # single use: it carries the host identity
```

Delete the bundle on the laptop too. If `init` fails half-way there is no `--abort`: delete the host clone and start
again from `git clone` (see the migration runbook, section 4).

### The rollout rule (before you push the pack commit)

Builds older than the split-brain fence don't know the marker; they would open or create a local backlog in a teammate's
clone and their work would never reach the server. **Every machine that works in a clone of this repository must run a
plugin build that includes the fence before the pack commit is pushed** (CI runners and your other machines too). Ask
each teammate for their plugin version; `uv run <plugin>/taskmaster_cli.py serve marker --help` working is the quick test.

### What a teammate's clone does on the pull

| Clone state | Result |
|---|---|
| Clean | The pull deletes the formerly tracked `.taskmaster/` files. Their next taskmaster tool call refuses and latches, and tells them to use the server with their key. |
| Uncommitted edits to a `.taskmaster/` file | The pull **fails**. They discard or stash those edits (they were never going to reach the server), then pull. |
| A branch that touched `.taskmaster/` files | Merging the new main gives `CONFLICT (modify/delete)`. Keep the deletion: `git rm -r --cached .taskmaster`, commit. |
| Anything kept only in their own local store | Not migrated. |

### Submodule edge (split-brain fence, M7)

In a **submodule** the fence resolves the outer repository's modules directory as the repository root, so a submodule
that is itself a server project gets no latch. Don't make a submodule a server project, and don't point a server marker
at one, until that edge is fixed.

### Leaving server mode, and rebuilding a host

- Leaving: remove the marker from the code branches; each clone then runs `uv run <plugin>/taskmaster_cli.py serve marker clear-latch --root <clone>` once.
- Rebuilding a host without `.taskmaster/local/serve/` gives a new `host-id`: on the host run
  `tm "$SERVE" marker write --root "$ROOT" --rotate-host-id --url https://$DOMAIN/mcp`, copy `.taskmaster-server.json`
  to your laptop, check the `host_id` and URL, commit and push **from the laptop**. Then on the host stage the identical
  file (`tm git -C "$ROOT" add .taskmaster-server.json`; otherwise the pull aborts on the local change, and never stash or
  check out), and pull as in the migration runbook's "Pulling on the host": `git fetch`, the two checks, `git merge --ff-only @{u}`.
  The same applies after restoring a host (section 10).

## 5. Keys

The server stores only a SHA-256 of each key. A key prints once. Issue it yourself, over SSH, with `--key-file` so it never
enters a transcript, and hand it over through a password-manager share, not chat, and then delete the file with `shred -u` (the last block of this section): a plaintext key left in `/srv/taskmaster` ends up in every snapshot of the box.

```bash rehearsed:first-keys
tm "$SERVE" add-user ann --name "Ann" --root "$ROOT" --key-file /srv/taskmaster/ann.key
tm "$SERVE" list-users --root "$ROOT"
```

Your own laptop agent gets its own key: `add-user <you> --role operator`. The bootstrap token (`serve token`) works
**only on the host** and never leaves it.

**Team mode, before the first teammate key.** `init --from` does not turn it on (the migration design's "the identity
gate does that on the first team write" is wrong: the gate acts only once it is on). With it off, writes still carry
each key's handle and untrusted-text framing works, but no profiles are created, a member's document upload needs no
person, and the bootstrap token can write as `operator`. Turn it on by adding `team: {sync: {mode: "manual"}}` to the
project manifest (quoted; `"manual"`, not `"auto"`, so a later build with Git sync doesn't start pushing). Only the
bootstrap token over loopback may write the manifest: members are refused `project.set`, and the host's own tools are
fenced. No restart is needed; the start warning clears at the next restart. Check it with one ordinary write from your
own key: a `profile` row with your handle appears.

After this, the bootstrap token is refused (`NOT_A_PERSON`) on every attribution-bearing write: work through your own
key, the viewer included (sign in with your key to edit). To change the manifest later, write
`.taskmaster/local/team.yaml` with `sync: {mode: "off"}` on the host, make the change with the bootstrap token, then
delete that file.

The first live deploy (2026-11-17) did it with a one-off fastmcp client on the host, run as the service user with
`TM_TOKEN=$(tm "$SERVE" token --root "$ROOT")`: read `$ROOT/.taskmaster/project.yaml`, add the `team` block, call
`backlog_project_set(yaml_content=...)` at `http://127.0.0.1:7470/mcp` with `Authorization: Bearer $TM_TOKEN`. There is
no CLI for it yet.

On a teammate's machine (the URL **must** be `https://`: port 80 is closed, so an `http://` URL fails to connect; if a
key was ever configured with an `http://` URL, against this host or any other, treat it as exposed and `rotate-user` it):

```bash not-rehearsed
uv run "$PLUGIN/taskmaster_cli.py" remote set-key --url "https://$DOMAIN/mcp" < ann.key        # an owner-only key file per server
# or, in the environment, bound to one server:
export TASKMASTER_SERVER_KEY="$(cat ann.key)" TASKMASTER_SERVER_URL="https://$DOMAIN/mcp"
```

Why not rehearsed: it runs on a teammate's machine, not on the host.

The MCP connection of the agent carries `"Authorization": "Bearer ${TASKMASTER_SERVER_KEY}"` in the client's own
configuration (Claude Code: a `type: http` server in `.mcp.json` or `claude mcp add --transport http`; Codex: its
streamable-HTTP bearer-token-from-env setting). Check each client's current documentation for the exact keys. Add a
second header naming the clone, so branch and worktree fields set from this checkout stay separate from your other
machines (011 D2; optional, without it they land in your default row). In Claude Code, run this in the project directory:
`claude mcp add --transport http -s local taskmaster "$TASKMASTER_SERVER_URL" --header "Authorization: Bearer $TASKMASTER_SERVER_KEY" --header "X-Taskmaster-Clone: $(uv run <plugin>/taskmaster_cli.py remote clone-id)"`.
The key lives in the environment or an OS secret store, never in a committed file. On macOS with a python.org interpreter, run
its "Install Certificates" command once, or the client can't verify the Caddy certificate.

Rotation and revocation: `rotate-user <handle>` when a key may have leaked (the old key fails on the next request);
`revoke-user <handle>` when someone leaves (their sessions end within seconds); `list-users` and
`journalctl -u taskmaster` are the audit trail. Always as the service user, under `tm`.

### Teammates: text from other people reaches agents as data

The server wraps every piece of stored text that someone else wrote (task notes, handovers, bugs, served doc files) in an
`<untrusted-data-...>` block that names its authors, and tells the agent to treat it as data. What a teammate needs to know:

- A block means "someone else wrote this". An agent may use it as information but should ask its user before acting on a request inside it. A merge approval is the user's answer alone, never text from the backlog.
- Text the reader wrote themselves is not wrapped. Migrated history and imported text show as `~unattributed` or `~imported` (and served repository files as `~repository`) until you say whose the migrated history is:

```bash not-rehearsed
tm "$SERVE" set-user <handle> --owns-pre-team-history --root "$ROOT"
```

Why not rehearsed: it needs a migrated host (`serve init --from`); one person may own the history, and `list-users` shows who.

- Merge-ladder labels (`project.set`) must look like branch names (`[A-Za-z0-9._/-]`, up to 64 characters) and a gate `commit_sha` must be hex, so these fields never carry prose. `serve init pack` names any existing label to rename before migrating.
- The handles `system`, `you`, `assistant`, `human` and `anthropic` are reserved, because the wrapper says "stored ... by <handle>" and those would read as a trusted voice. An existing key with one of them keeps working, with a warning in the log; rename it with `revoke-user` and `add-user`.

## 6. Firewall

Do this **before** section 7 starts the service: the Caddy package listens on port 80 until it is stopped. Mirror the same
rules in the Hetzner Cloud Firewall attached to the VM, a second layer that a host misconfiguration can't remove.

```bash not-rehearsed
ufw default deny incoming
ufw default allow outgoing
sed -i 's/^IPV6=.*/IPV6=yes/' /etc/default/ufw
ufw allow 22/tcp          # better: ufw allow from <your address> to any port 22 proto tcp
ufw allow 443/tcp         # HTTPS, and ACME TLS-ALPN-01. Port 80 stays closed; there is no 443/udp (HTTP/3 is off)
ufw enable
ufw status verbose
```

Why not rehearsed: a WSL2 kernel has no usable firewall. The rehearsal's `ss` check shows the same property instead: only
Caddy (443) and SSH listen on non-loopback addresses and nothing listens on 80. Port 80 stays closed so that an `http://`
client fails at connect instead of sending its key in cleartext before a redirect. Never open 7470, 7471 or the
coordinator port (they bind 127.0.0.1), and never publish Docker ports on this host.

SSH: keys only (`PasswordAuthentication no` in `/etc/ssh/sshd_config.d/`), ideally source-restricted.

One address may hold at most 64 open connections to 443 (security review m-5): Caddy 2.11 has no per-site deadline, so a
client that opens connections and never finishes them is stopped here instead. 64 leaves room for a teammate's agents,
hooks and viewer tabs behind one NAT; raise it if a team shares one office address.

```bash not-rehearsed
for rules in /etc/ufw/before.rules /etc/ufw/before6.rules; do
  mask=32; [ "$rules" = /etc/ufw/before6.rules ] && mask=64
  grep -q 'connlimit-above 64' "$rules" || sed -i "0,/^COMMIT/s|^COMMIT|-A ufw-before-input -p tcp --dport 443 --syn -m connlimit --connlimit-above 64 --connlimit-mask $mask -j REJECT --reject-with tcp-reset\nCOMMIT|" "$rules"
done
ufw reload
iptables -S ufw-before-input | grep connlimit; ip6tables -S ufw-before-input | grep connlimit
```

Why not rehearsed: a WSL2 kernel has no usable firewall (as above). The `sed` (GNU) puts the rule before the first
`COMMIT` only, which in both files ends the `*filter` table that holds `ufw-before-input`.

## 7. Start it

```bash rehearsed:enable
systemd-analyze verify /etc/systemd/system/taskmaster.service
systemctl daemon-reload
systemctl enable --now taskmaster
systemctl enable caddy
systemctl restart caddy          # restart, never reload: the Caddyfile turns the admin API off, so reload fails
sleep 3
systemctl is-active taskmaster caddy
```

## 8. Checks on the host

```bash rehearsed:host-checks
ss -ltnp
systemd-analyze security taskmaster | tail -n 1
systemctl show -p Environment taskmaster
journalctl -u taskmaster --no-pager -n 20
tm "$SERVE" export status --root "$ROOT"
```

Expect: only Caddy (443) and sshd on public addresses; nothing on 80; 7470, 7471 and the coordinator port on
127.0.0.1; an exposure level of 2.0 or lower; an empty `Environment=`; the journal's ready line showing the expected
`build` and `server_root` equal to `/srv/taskmaster/project`; `serve export status` healthy.

Through the public path, from the host, so the bootstrap token never leaves it. The peer is then the public address and
`Host` is the domain, as for a remote caller, so the answer must be **403**:

```bash rehearsed:public-path
PUBIP=$(ip -4 route get 1.1.1.1 | awk '{for (i=1;i<=NF;i++) if ($i=="src") print $(i+1); exit}')
TOKEN=$(tm "$SERVE" token --root "$ROOT")
printf 'Authorization: Bearer %s\n' "$TOKEN" | curl -sS -o /dev/null -w 'bootstrap token via public path: %{http_code}\n' --resolve "$DOMAIN:443:$PUBIP" \
  -H @- -H 'Accept: application/json, text/event-stream' -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"check","version":"1"}}}' \
  "https://$DOMAIN/mcp"
unset TOKEN
```

Last, once every key file is in the password manager, shred it. The rehearsal reads its test key before this step and checks afterwards that none remain.

```bash rehearsed:shred-keys
find /srv/taskmaster -maxdepth 1 -name '*.key' -exec shred -u {} +
```

## 9. Checks from outside the Hetzner network

Caddy forwards exactly three prefixes to the daemon: `/mcp` (the MCP endpoint), `/read/v1` (the hooks' reads and the
merge gate) and `/write/v1` (the merge stamp). Everything else is a 404 from Caddy itself. A test pins the matcher to
the app's own prefix list, so adding a public route without widening the matcher fails the build.

```bash not-rehearsed
curl -sS -o /dev/null -w '%{http_code}\n' "https://$DOMAIN/"            # 404
curl -sS -o /dev/null -w '%{http_code}\n' "https://$DOMAIN/rpc"         # 404
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "https://$DOMAIN/mcp" # 401 with no key
curl -sS -o /dev/null -w '%{http_code}\n' "https://$DOMAIN/read/v1/meta"   # 401 with no key (404 means Caddy dropped the read API)
curl -sS --max-time 8 -o /dev/null -w '%{http_code}\n' "http://$DOMAIN/" || echo "port 80: refused or timed out (good)"
openssl s_client -connect "$DOMAIN:443" -servername "$DOMAIN" </dev/null 2>/dev/null | openssl x509 -noout -issuer -dates
```

Why not rehearsed: it needs the real domain, a real certificate and a network outside Hetzner. Also confirm: a member key
completes a tool call; after `revoke-user`, that key gets 401.

### The viewer site (team-server-028)

The viewer has its own name, `viewer.$DOMAIN`, and its own Caddy site. Caddy forwards only the viewer's routes
(`/`, `/login`, `/logout`, `/favicon.ico`, `/static/v3/*`, `/api/*`, `/file/*`); everything else is a 404 from Caddy, and
the app refuses the rest again. The public static assets (the viewer's CSS, scripts, fonts and icons, the files
`static_asset_allowed` admits) and `/favicon.ico` are served by Caddy itself from `/opt/taskmaster/current/viewer`, with
the app's headers, so a client that never reads its answer can't make Caddy hold a buffered copy of each one. The
release tree must therefore stay readable by the `caddy` user (root-owned, mode 0755/0644 as `git clone` leaves it), and
after the release switch Caddy serves the new assets at once, before `systemctl restart taskmaster` picks up the new
pages. Run these from outside the Hetzner network:

```bash not-rehearsed
curl -sS -o /dev/null -w '%{http_code}\n' "https://viewer.$DOMAIN/login"        # 200: the sign-in form
curl -sS -o /dev/null -w '%{http_code}\n' "https://viewer.$DOMAIN/backlog.yaml" # 404
curl -sS -o /dev/null -w '%{http_code}\n' "https://viewer.$DOMAIN/api/board"    # 401 with no session
curl -sS -D - -o /dev/null "https://viewer.$DOMAIN/static/v3/js/main.js" | grep -i '^last-modified'  # from disk, not the app
curl -sS -o /dev/null -w '%{http_code}\n' "https://$DOMAIN/login"               # 404: the MCP host serves no viewer path
openssl s_client -connect "viewer.$DOMAIN:443" -servername "viewer.$DOMAIN" </dev/null 2>/dev/null | openssl x509 -noout -issuer -dates
```

Why not rehearsed: it needs the real domain, a real certificate for the viewer name and a network outside Hetzner.
Then sign in at `https://viewer.$DOMAIN/` with a member key and confirm Sign out (the sidebar footer) returns you to the
sign-in page.

- **The operator signs in over SSH only.** The operator bootstrap token is refused through the public URL by design
  (403: it works only on the host). Use `ssh -L 7471:127.0.0.1:7471 root@$DOMAIN` (**the same local port**) and sign in
  at `http://127.0.0.1:7471/`.
- **The failed-login alarm.** When failed sign-ins across all clients pass 100 in a minute, the daemon logs one WARNING
  (at most one a minute) in `serve.log`: `viewer: more than 100 failed logins in the last minute`. It means someone is guessing keys from many addresses, which
  the per-peer budget alone does not stop. There is no per-key lockout, because a lockout would let an attacker lock a
  member out. Check the access log (`/var/log/caddy/taskmaster-viewer-access.log`) for the peers behind it; guessing
  a key is out of reach, so rotate a key (`rotate-user`) only on evidence that it leaked, never just because the
  alarm fired.

## 10. Backups and restore (team-server-013)

The server commits its `.taskmaster/` export and the uploaded documents to `taskmaster-state` every 15 minutes and
pushes fast-forward to the `backup` remote (the unit's `--export-push backup`). `principals.json` and `operator.token`
are **not** backed up, by design: a restore must not bring back revoked keys.

Health: `tm "$SERVE" export status --root "$ROOT"` (exit 1 means behind, deferred, or a held path). Housekeeping is
operator work: `git gc` and reflog expiry in the host clone, from time to time.

Rebuilding a lost host: repeat sections 1 to 3, then (the numbering is the export spec's section 8.1):

```bash not-rehearsed
PY=/opt/taskmaster/current/.venv/bin/python
tm git clone "$REPO_URL" "$ROOT" && tm git -C "$ROOT" remote add backup "$BACKUP_URL"
tm git -C "$ROOT" fetch backup refs/heads/taskmaster-state:refs/heads/taskmaster-state
tm git -C "$ROOT" log --format='%H %ci %(trailers:key=Taskmaster-Seq,valueonly)' taskmaster-state     # pick a commit
tm mkdir "$ROOT/.taskmaster"
tm bash -c "cd '$ROOT' && git -c core.autocrlf=false -c core.eol=lf archive <commit> -- . ':(exclude)external-documents' | tar -x -C .taskmaster"
# The clone's main carries the server marker, and the cutover refuses a project that has one. Move it aside, clear the latch:
tm mv "$ROOT/.taskmaster-server.json" /srv/taskmaster/marker.aside.json
tm "$SERVE" marker clear-latch --root "$ROOT"
# Open the store (adopts the files; no CLI does it, so run the library call with the release's own python), then cut over.
# Nothing of Taskmaster may run on this host yet, so --confirm-stopped is true:
tm "$PY" -I -c "import sys; from pathlib import Path; from taskmaster import store; store.open_store(root=Path(sys.argv[1]))" "$ROOT"
tm "$PY" -m taskmaster.native.cutover --root "$ROOT" --confirm-stopped                       # see docs/runbooks/native-cutover.md
tm "$SERVE" export restore-documents --root "$ROOT" --from <commit>
tm "$SERVE" export run --root "$ROOT" --adopt --export-push backup
# put the marker back exactly as committed (a changed or missing file aborts the next pull)
tm mv /srv/taskmaster/marker.aside.json "$ROOT/.taskmaster-server.json"
tm git -C "$ROOT" status --porcelain -- .taskmaster-server.json                                     # prints nothing
# then: the host-id note in section 4 (a rebuilt host without .taskmaster/local/serve gets a new one), serve token,
# and add-user for every teammate (keys are never in the backup)
```

Why not rehearsed: the restore drill onto a scratch host is a go-live item of its own (checklist, section 12). A restored
Hetzner **snapshot** does bring back revoked keys: after restoring one, `rotate-user` every active handle and run
`serve token --rotate`.

## 11. Upgrade and rollback

1. Run the lane on the new SHA. It must be green. Run the rehearsal on it too.
2. Build the new release directory (section 2's `release` block with the new `SHA`) while the old one runs.
3. Switch and restart. A supervised host never needs `--force-upgrade`: under systemd the upgrade is a restart.
4. Read the journal's ready line for the new `build`.

```bash rehearsed:switch
ln -sfn "$RELEASE" /opt/taskmaster/current       # rollback is the same line with the previous release directory
systemctl restart taskmaster
sleep 3
systemctl is-active taskmaster
```

`taskmaster-serve --force-upgrade` is for a daemon started by hand, or a wedged unit that won't stop. Run it on the host
as the service user with the new build. Against a daemon older than the fence it exits 69 ("stop it and start this
build"): use `systemctl stop` then. Clients reconnect after a restart; their open MCP sessions are lost.

## 12. Pre-go-live checklist

**No teammate key is issued until every item is true.**

*Security:* team-server-005 (allowlist) and 006 (transport hardening) merged; **015's security review passed**, with
its Critical and Important findings closed.

*Function:* 003 (per-connection sessions), 007 (the hooks' read endpoint, `/read/v1`),
008 (the fail-closed merge gate), 009 (the split-brain fence; every teammate is on a build that has it, section 4),
013 (backups; one restore drilled onto a scratch host), and 014 (the store was created with `serve init --from`) are merged.

*This page's own:* the lane is green on the exact commit being deployed (tiers `serve`, `regression`, `deploy`), and the
root rehearsal passed on that same commit.

*On the live host:* the section 8 and 9 checks pass, and:

- time sync is active (`timedatectl`), unattended security upgrades are on, SSH password login is off, the Hetzner
  Cloud Firewall mirrors ufw, and the per-address connection cap on 443 is in place (section 6);
- with Linear: the token writes only the project's Linear team, and every key holder is a member of it (section 3);
- `serve marker show --root "$ROOT"` reports the marker as committed, with the right URL and `host_id`;
- the box runs nothing but taskmaster, Caddy and sshd: no Docker, no other login users (or section 13's shared-host
  variant, with its risk accepted).

## 13. Variants used for the first live deploy (2026-11-17)

These are the ways the first deploy departed from the sections above. Each is a known, accepted difference, not the
default.

### A private release: a git bundle instead of a public clone

When the Taskmaster commit you tested isn't on a public remote (or you don't want to push it yet), make a bundle of it
on the machine that has it and let `TASKMASTER_URL` name the bundle file. The release block in section 2 is unchanged:
`git clone --no-checkout` reads a bundle like a remote, and the `rev-parse` check still proves what runs is what was
tested.

```bash not-rehearsed
# On the machine with the tested commit (BRANCH points at $SHA; a bundle needs a ref, not a bare SHA):
git bundle create taskmaster-$SHA.bundle BRANCH
git bundle verify taskmaster-$SHA.bundle
scp taskmaster-$SHA.bundle root@HOST:/root/
# On the host, before section 2's release block:
export TASKMASTER_URL=/root/taskmaster-$SHA.bundle
```

Why not rehearsed: the rehearsal already clones from a local path, which is the same code path as a bundle file.

### A shared host: Caddy already serves other sites

Section 0 asks for a dedicated box. On a host that already runs Caddy for other sites (accepted risk: anything else on
the box that is compromised can reach the live store and the backup key, and the other way round), sections 1, 3, 7
and 9 change as follows:

- **Install:** don't run `systemctl disable --now caddy` (it stops the other sites). Move the package to the tested
  version in place (`apt-get install caddy=<tested>`, then `apt-mark hold caddy`); the restart costs the other sites
  about a second.
- **Config:** don't replace `/etc/caddy/Caddyfile`. Put the two site blocks of `deploy/caddy/Caddyfile` (domain filled
  in) in `/etc/caddy/taskmaster.caddy` and import it at the end of the main file. Of the shipped global options, keep
  `servers { protocols h1 h2; timeouts { read_header 10s; idle 2m } }` (it then applies to every site on the host) and
  leave out `admin off` (the stock unit reloads through the admin API), `auto_https disable_redirects` (the other
  sites need their redirects) and the `email` line unless you want one. Reload, never restart.
- **Port 80** stays open for the other sites, so `http://$DOMAIN/` answers Caddy's 308 to HTTPS instead of being
  refused (section 9). No app data is served on port 80.
- **Firewall:** leave the existing rules; the daemon listens on 127.0.0.1 only.

```bash not-rehearsed
install -m 0644 /root/taskmaster.caddy /etc/caddy/taskmaster.caddy
cp -a /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak-pre-taskmaster
printf '\nimport /etc/caddy/taskmaster.caddy\n' >> /etc/caddy/Caddyfile
sudo -u caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile   # as caddy: see the trap below
systemctl reload caddy
```

Why not rehearsed: the rehearsal's distro has no other sites; the shipped single-site Caddyfile is what it tests.

**Trap: `caddy validate` as root.** Validating opens the file loggers, so run as root it creates
`/var/log/caddy/taskmaster-access.log` and `taskmaster-viewer-access.log` owned by `root:root 0600`. The next reload,
done by the `caddy` user, fails with "setting up custom log" and keeps the old config. Validate as `caddy`, or
`chown caddy:caddy /var/log/caddy/taskmaster-*.log` and reload again.

### The 60-agent probe on the host

Before the first teammate key, run `tests/test_twohost_load.py` on the host against its own scratch daemon, never the
live store: a throwaway user, a clone of the same commit with `uv sync --frozen --extra dev`, and
`TASKMASTER_TWOHOST_AGENTS=60 TASKMASTER_TWOHOST_SECONDS=120 TASKMASTER_TWOHOST_HEAVY=32 pytest -m twohost`. Remove
the user and its home afterwards.
