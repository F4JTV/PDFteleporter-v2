# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Differential test of the rebuild: `pdft-cli rebuild` against engine.py.

Rebuilds each archive with the desktop engine and with the C++ one, and
compares the two PDFs:

  identical   the same bytes, the random /ID of the trailer aside;
  objects     other bytes but the same objects, stream contents included
              (a difference in how the file is laid out);
  render      other objects, the same pixels at 100 dpi;
  DIFF        anything else, with the first object that differs.

    python compare_rebuild.py PDFT_CLI WORKDIR ARCHIVE...
"""

import json
import os
import re
import subprocess
import sys
import time

import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "application"))
from psditool import engine  # noqa: E402

# A PDF string: <hex> or (literal), escapes included.
_STRING = rb"(?:<[0-9A-Fa-f]*>|\((?:\\.|[^\\)])*\))"
ID = re.compile(rb"/ID\[" + _STRING + _STRING + rb"\]")


def masked(pdf):
    return ID.sub(b"/ID[]", pdf)


def first_object_difference(a, b):
    da, db = pymupdf.open("pdf", a), pymupdf.open("pdf", b)
    if da.xref_length() != db.xref_length():
        return f"{da.xref_length()} objects != {db.xref_length()}"
    for xref in range(1, da.xref_length()):
        oa, ob = da.xref_object(xref, compressed=True), db.xref_object(xref, compressed=True)
        if oa != ob:
            return f"object {xref}: {oa[:160]!r} != {ob[:160]!r}"
        sa, sb = da.xref_stream(xref), db.xref_stream(xref)
        if sa != sb:
            where = next((i for i, (x, y) in enumerate(zip(sa or b"", sb or b"")) if x != y),
                         min(len(sa or b""), len(sb or b"")))
            return (f"stream {xref} at byte {where}: {(sa or b'')[max(0, where - 60):where + 60]!r} != "
                    f"{(sb or b'')[max(0, where - 60):where + 60]!r}")
    return None


def same_render(a, b):
    da, db = pymupdf.open("pdf", a), pymupdf.open("pdf", b)
    if da.page_count != db.page_count:
        return False
    for pa, pb in zip(da, db):
        if pa.get_pixmap(dpi=100).samples != pb.get_pixmap(dpi=100).samples:
            return False
    return True


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    cli, workdir, archives = sys.argv[1], sys.argv[2], sys.argv[3:]
    os.makedirs(workdir, exist_ok=True)
    tally = {}
    time_py = time_cpp = 0.0
    for path in archives:
        with open(path, "rb") as f:
            data = f.read()
        name = os.path.basename(path)
        started = time.perf_counter()
        py, info = engine.archive_to_pdf(data)
        t_py = time.perf_counter() - started
        out = os.path.join(workdir, name + ".cpp.pdf")
        started = time.perf_counter()
        done = subprocess.run([cli, "rebuild", path, out], capture_output=True, text=True)
        t_cpp = time.perf_counter() - started
        time_py += t_py
        time_cpp += t_cpp
        with open(os.path.join(workdir, name + ".py.pdf"), "wb") as f:
            f.write(py)
        note = ""
        if done.returncode != 0:
            status, note = "DIFF", done.stderr.strip()[:200]
        else:
            with open(out, "rb") as f:
                cpp = f.read()
            report = json.loads(done.stdout)
            if masked(py) == masked(cpp):
                status = "identical"
            else:
                found = first_object_difference(py, cpp)
                if found is None:
                    status = "objects"
                elif same_render(py, cpp):
                    status, note = "render", found
                else:
                    status, note = "DIFF", found
            for key in ("mode", "pages", "version", "compact"):
                if report.get(key) != info.get(key):
                    status, note = "DIFF", f"report {key}: {info.get(key)!r} != {report.get(key)!r}"
        tally[status] = tally.get(status, 0) + 1
        print(f"{status:9} {name:58} {t_py:6.2f}s/{t_cpp:6.2f}s  {note}", flush=True)
    print(json.dumps(tally), f"time python {time_py:.1f}s, c++ {time_cpp:.1f}s")
    return 0 if set(tally) <= {"identical", "objects"} else 1


if __name__ == "__main__":
    sys.exit(main())
