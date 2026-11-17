# User intent: python -m taskmaster.serve is the same CLI as taskmaster_cli.py serve.
import sys

from .cli import main

sys.exit(main())
