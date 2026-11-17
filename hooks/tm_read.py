# User intent: the taskmaster-mods' reader for team-server mode: run by the mod, it reads the team server through the
# hooks' client and cache and prints one JSON object. Kept parseable by old interpreters so the mod can tell
# "this Python is too old" (exit 3: try the next one) from a real answer (team-server-007 D14, review N1).
import sys

TOO_OLD = 3

if sys.version_info < (3, 9):
    sys.stderr.write("tm_read.py needs Python 3.9 or newer\n")
    sys.exit(TOO_OLD)

import os  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from taskmaster.remote.readcli import main  # noqa: E402

if __name__ == "__main__":
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
