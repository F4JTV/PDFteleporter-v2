# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Writes the image files of the desktop test suite for the differential
tests, and prints their paths, one a line.

A screenshot, a page in black and white, a grey scan and a photograph: each
takes its own way through the encoding of an image file's page (a PNG at
the file's own resolution, a PNG, a JPEG, AVIF or WebP in a .psdz). They
are application/tests/fixtures.py's, made the same way on every run.

    python make_images.py WORKDIR
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "application"))
from tests import fixtures  # noqa: E402

IMAGES = (
    ("dialog.png", fixtures.screenshot_png),
    ("fax.png", fixtures.bilevel_scan_png),
    ("scan.png", fixtures.grey_scan_png),
    ("photo.jpg", fixtures.photo_jpeg),
)


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    workdir = sys.argv[1]
    os.makedirs(workdir, exist_ok=True)
    for name, make in IMAGES:
        print(make(os.path.join(workdir, name)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
