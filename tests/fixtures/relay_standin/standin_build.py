# User intent: build the std-only tm-relay stand-in once per session so entry and self-heal tests can force every --version outcome.
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

SRC = Path(__file__).with_name("main.rs")


def build_standin() -> Path | None:
    """Return the stand-in executable, or None when no Rust compiler is available."""
    rustc = shutil.which("rustc") or shutil.which("rustc", path=str(Path.home() / ".cargo" / "bin"))
    if rustc is None:
        return None
    target = Path(os.environ.get("CARGO_TARGET_DIR") or tempfile.gettempdir()) / "relay-standin"
    target.mkdir(parents=True, exist_ok=True)
    out = target / ("standin.exe" if os.name == "nt" else "standin")
    if not out.exists() or out.stat().st_mtime < SRC.stat().st_mtime:
        subprocess.run([rustc, "-O", str(SRC), "-o", str(out)], check=True, timeout=120,
                       stdin=subprocess.DEVNULL, capture_output=True)
    return out
