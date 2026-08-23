#!/usr/bin/env python3
"""PDF Teleporter entry point.

One executable serves three callers, distinguished by argv:

    pdfteleporter.py                    -> GUI
    pdfteleporter.py --compress FILE    -> Explorer context menu
    pdfteleporter.py compress FILE ...  -> command line

Keeping a single entry point matters for the registry: the context-menu
command string points at whatever this file resolves to, frozen or not.
"""

from __future__ import annotations

import sys


def main() -> int:
    argv = sys.argv[1:]

    if not argv:
        from psditool.gui.app import run

        return run(sys.argv)

    from psditool.cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
