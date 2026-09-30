"""Read-only conditional-GET wire probe on the existing marked N10 copies."""
import argparse
import os
from pathlib import Path
import subprocess
import threading

from native_n10_viewer_bench import REPO, bs, point


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--copies', type=Path, required=True)
    parser.add_argument('--node', required=True)
    args = parser.parse_args()
    copies = args.copies.resolve(strict=True)
    if (REPO / 'test-results').resolve() not in copies.parents:
        parser.error('copies must be inside this worktree test-results')
    for kind in ('legacy', 'native'):
        output = copies / f'wire-304-{kind}.json'
        if output.exists():
            raise FileExistsError(output)
        point(copies / kind / 'project')
        server, port = bs._make_server(host='127.0.0.1', port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            subprocess.run([args.node, str(REPO / 'scripts/native_n10_wire_probe.mjs'),
                            f'--url=http://127.0.0.1:{port}', f'--output={output}'],
                           cwd=REPO, check=True, **flags)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
        print(f'Completed wire-304-{kind}', flush=True)


if __name__ == '__main__':
    main()
