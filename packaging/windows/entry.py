"""PyInstaller entry point: the packaged app is the JSON command line (gigradar.cli)."""

import sys

from gigradar.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
