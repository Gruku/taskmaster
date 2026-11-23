# User intent: build the committed relay binaries (bin/tm-relay.exe, bin/tm-relay-linux-*) reproducibly from relay/ and prove it - the
# manifest ties each binary to the sources, --check catches stale binaries, --verify rebuilds from a clean git archive and compares hashes.
"""Build, check and verify the Rust tm relay (local-server-011).

  python scripts/build_relay.py            build every buildable target into bin/ and write bin/relay-manifest.json
  python scripts/build_relay.py --check    exit 1 when relay/ sources no longer match the manifest (no build)
  python scripts/build_relay.py --verify   rebuild from `git archive HEAD relay/` in TASKMASTER_RELAY_VERIFY_DIR and require every
                                           sha256 to equal both the manifest and the committed file

Stdlib only, like bump_version.py. Environment: CARGO_TARGET_DIR (build output; default relay/target),
TASKMASTER_RELAY_VERIFY_DIR (--verify scratch; default a temp dir).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "bin" / "relay-manifest.json"

# triple -> (file under bin/, extra RUSTFLAGS). Every target also gets the three path remaps (see rustflags()).
TARGETS = {
    "x86_64-pc-windows-msvc": ("tm-relay.exe", ["-C", "target-feature=+crt-static", "-C", "link-arg=/Brepro"]),
    "x86_64-unknown-linux-musl": ("tm-relay-linux-x86_64", ["-C", "linker=rust-lld"]),
}
# aarch64-unknown-linux-musl is deliberately not shipped: relay/rust-toolchain.toml pins no such target, so it cannot be built or --verify'd from
# the pinned toolchain (follow-up). The sh entry falls back to the Python bridge there.


def lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def source_digest(relay_dir: Path) -> str:
    """sha256 over relay/Cargo.toml, Cargo.lock and src/** (LF-normalized, sorted relative paths): the identity of the sources."""
    files = [relay_dir / "Cargo.toml", relay_dir / "Cargo.lock"] + sorted(p for p in (relay_dir / "src").rglob("*") if p.is_file())
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(relay_dir).as_posix().encode() + b"\0" + lf(path.read_bytes()) + b"\0")
    return digest.hexdigest()


def sha256(path_or_bytes) -> str:
    data = path_or_bytes if isinstance(path_or_bytes, bytes) else Path(path_or_bytes).read_bytes()
    return hashlib.sha256(data).hexdigest()


def toolchain_channel(relay_dir: Path) -> str:
    match = re.search(r'(?m)^channel\s*=\s*"([^"]+)"', (relay_dir / "rust-toolchain.toml").read_text(encoding="utf-8"))
    return match.group(1) if match else "unknown"


def cargo_home() -> Path:
    return Path(os.environ.get("CARGO_HOME") or Path.home() / ".cargo")


def rustup_home() -> Path:
    return Path(os.environ.get("RUSTUP_HOME") or Path.home() / ".rustup")


def rustflags(triple: str, relay_dir: Path, placeholders: bool = False) -> str:
    """The exact RUSTFLAGS for one target: no local path may reach a public binary, so the checkout, CARGO_HOME and RUSTUP_HOME are remapped.
    placeholders=True gives the form recorded in the public manifest (no local paths or user names)."""
    if placeholders:
        remaps = ["--remap-path-prefix=<relay>=/src", "--remap-path-prefix=<CARGO_HOME>=/cargo", "--remap-path-prefix=<RUSTUP_HOME>=/rustup"]
    else:
        remaps = [f"--remap-path-prefix={relay_dir}=/src", f"--remap-path-prefix={cargo_home()}=/cargo", f"--remap-path-prefix={rustup_home()}=/rustup"]
    return " ".join(remaps + TARGETS[triple][1])


def bin_binaries() -> set[str]:
    """Relay binaries present in bin/ (the sh entry and the manifest are not binaries)."""
    return {p.name for p in (ROOT / "bin").glob("tm-relay*") if p.is_file() and (p.name == "tm-relay.exe" or p.name.startswith("tm-relay-"))}


def manifest_mismatch(manifest: dict) -> list[str]:
    listed = {info["file"] for info in manifest.get("targets", {}).values()}
    present = bin_binaries()
    return [f"bin/{n} is not listed in the manifest (stale binary)" for n in sorted(present - listed)] + \
           [f"the manifest lists bin/{n} but the file is missing" for n in sorted(listed - present)]


def installed_targets(relay_dir: Path) -> set[str]:
    out = subprocess.run(["rustup", "target", "list", "--installed"], cwd=relay_dir, capture_output=True, text=True)
    return set(out.stdout.split())


def rustc_version(relay_dir: Path) -> str:
    return subprocess.run(["rustc", "-V"], cwd=relay_dir, capture_output=True, text=True, check=True).stdout.strip()


def build_target(triple: str, relay_dir: Path, target_dir: Path, digest: str) -> Path:
    env = dict(os.environ, RUSTFLAGS=rustflags(triple, relay_dir), TM_RELAY_SOURCE_DIGEST=digest, CARGO_TARGET_DIR=str(target_dir))
    env.pop("CARGO_ENCODED_RUSTFLAGS", None)
    result = subprocess.run(["cargo", "build", "--release", "--locked", "--target", triple], cwd=relay_dir, env=env)
    if result.returncode != 0:
        raise SystemExit(f"cargo build failed for {triple}")
    return target_dir / triple / "release" / ("tm-relay.exe" if triple.endswith("msvc") else "tm-relay")


def build_all(relay_dir: Path, target_dir: Path, targets: list[str]) -> tuple[str, dict]:
    digest = source_digest(relay_dir)
    built = {}
    for triple in targets:
        built[triple] = build_target(triple, relay_dir, target_dir, digest)
    return digest, built


def buildable(relay_dir: Path) -> list[str]:
    have = installed_targets(relay_dir)
    return [t for t in TARGETS if t in have]


def entry(triple: str, data: bytes, relay_dir: Path) -> dict:
    return {"file": TARGETS[triple][0], "sha256": sha256(data), "size": len(data), "rustflags": rustflags(triple, relay_dir, placeholders=True)}


def cmd_build(args) -> int:
    relay_dir = ROOT / "relay"
    target_dir = Path(os.environ.get("CARGO_TARGET_DIR") or relay_dir / "target")
    exclude = set(args.exclude or [])
    unknown = exclude - set(TARGETS)
    if unknown:
        raise SystemExit(f"--exclude names unknown target(s): {', '.join(sorted(unknown))}")
    targets = [t for t in buildable(relay_dir) if t not in exclude]
    missing = [t for t in TARGETS if t not in targets and t not in exclude]
    if missing:
        raise SystemExit(f"target(s) {', '.join(missing)} are not installed (rustup target add ...): refusing to leave a stale binary; install them or pass --exclude <triple> to drop them")
    digest, built = build_all(relay_dir, target_dir, targets)
    (ROOT / "bin").mkdir(exist_ok=True)
    manifest = {"source_digest": digest, "rustc": rustc_version(relay_dir), "toolchain_channel": toolchain_channel(relay_dir), "targets": {}}
    for triple, path in built.items():
        data = path.read_bytes()
        dest = ROOT / "bin" / TARGETS[triple][0]
        try:
            dest.write_bytes(data)
        except PermissionError as exc:
            raise SystemExit(f"{dest} is locked ({exc}); a running tm-relay.exe holds it - close the sessions or stop the process, then rebuild")
        if not triple.endswith("msvc"):
            dest.chmod(0o755)
        manifest["targets"][triple] = entry(triple, data, relay_dir)
    for name in sorted(bin_binaries() - {i["file"] for i in manifest["targets"].values()}):
        (ROOT / "bin" / name).unlink()
        print(f"removed bin/{name}: not a built target (excluded or no longer shipped; the sh entry uses the Python bridge there)")
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"built {', '.join(built)}; manifest written to {MANIFEST.relative_to(ROOT)}")
    for triple, info in manifest["targets"].items():
        print(f"  {info['file']}: {info['size']} bytes sha256 {info['sha256'][:16]}...")
    return 0


def cmd_check(args) -> int:
    if not MANIFEST.is_file():
        print("bin/relay-manifest.json is missing: run python scripts/build_relay.py")
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    digest = source_digest(ROOT / "relay")
    if manifest.get("source_digest") != digest:
        print(f"relay/ sources changed since the committed binaries were built (manifest {str(manifest.get('source_digest'))[:12]}, sources {digest[:12]}); "
              "run python scripts/build_relay.py, then --verify, and commit relay/, bin/ and the manifest")
        return 1
    mismatch = manifest_mismatch(manifest)
    if mismatch:
        for line in mismatch:
            print("CHECK FAILED:", line)
        return 1
    print(f"relay sources match the manifest ({digest[:12]})")
    return 0


def committed_bytes(relative: str) -> bytes | None:
    out = subprocess.run(["git", "show", f"HEAD:{relative}"], cwd=ROOT, capture_output=True)
    return out.stdout if out.returncode == 0 else None


def cmd_verify(args) -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    scratch = Path(os.environ.get("TASKMASTER_RELAY_VERIFY_DIR") or tempfile.mkdtemp(prefix="relay-verify-"))
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    archive = subprocess.run(["git", "archive", "HEAD", "relay/"], cwd=ROOT, capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(scratch / "src", filter="data")
    relay_dir = scratch / "src" / "relay"
    digest = source_digest(relay_dir)
    failures = []
    if digest != manifest["source_digest"]:
        failures.append(f"source digest of HEAD's relay/ {digest[:12]} != manifest {manifest['source_digest'][:12]}")
    wanted = [t for t in manifest["targets"] if t in installed_targets(relay_dir)]
    for skipped in sorted(set(manifest["targets"]) - set(wanted)):
        failures.append(f"target {skipped} is in the manifest but not installed here: cannot verify it")
    _, built = build_all(relay_dir, scratch / "target", wanted)
    for triple, path in built.items():
        info = manifest["targets"][triple]
        got = sha256(path)
        committed = committed_bytes(f"bin/{info['file']}")
        if got != info["sha256"]:
            failures.append(f"{info['file']}: rebuilt sha256 {got[:16]} != manifest {info['sha256'][:16]}")
        if committed is None or sha256(committed) != got:
            failures.append(f"{info['file']}: rebuilt sha256 {got[:16]} != the file committed at HEAD")
    for line in failures:
        print("VERIFY FAILED:", line)
    if not failures:
        print(f"verified {', '.join(built)}: every rebuilt sha256 equals the manifest and the committed file")
    return 1 if failures else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true")
    group.add_argument("--verify", action="store_true")
    parser.add_argument("--exclude", action="append", metavar="TRIPLE", help="build: drop this target and delete its binary from bin/ and the manifest")
    args = parser.parse_args(argv)
    if args.check:
        return cmd_check(args)
    if args.verify:
        return cmd_verify(args)
    return cmd_build(args)


if __name__ == "__main__":
    sys.exit(main())
