"""Side-by-side benchmark: F1GBD psdi_lib 2.1.0 (loaded from its .pyc) vs our psditool 2.2.1.

Usage: python3 bench.py <psdi_lib.pyc> <v2 application dir> <outdir>
"""
import io
import json
import logging
import marshal
import os
import sys
import time
import types
import warnings

warnings.filterwarnings("ignore")
logging.disable(logging.CRITICAL)

PYC, APPDIR, OUT = sys.argv[1:4]
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, APPDIR)

import fitz  # noqa: E402

# --- load F1GBD engine from bytecode -----------------------------------
with open(PYC, "rb") as fh:
    fh.read(16)
    code = marshal.load(fh)
theirs = types.ModuleType("psdi_lib")
theirs.__file__ = "psdi_lib.py"
exec(code, theirs.__dict__)

# --- our engine ---------------------------------------------------------
from psditool import engine as ours, format as psdi  # noqa: E402
from tests import fixtures  # noqa: E402

QUALITIES = ["ultra_low", "low", "medium", "high"]

docs = {}
docs["text_report"] = fixtures.text_report(os.path.join(OUT, "text_report.pdf"), pages=3)
docs["rich_report"] = fixtures.rich_report(os.path.join(OUT, "rich_report.pdf"))
docs["tiny_table"] = fixtures.tiny_table(os.path.join(OUT, "tiny_table.pdf"))
docs["rotated"] = fixtures.rotated_report(os.path.join(OUT, "rotated.pdf"))
docs["scan_like"] = fixtures.monochrome_scan_like(os.path.join(OUT, "scan_like.pdf"))
ttf = fixtures.annotated_without_tounicode(os.path.join(OUT, "no_tounicode.pdf"))
if ttf:
    docs["no_tounicode"] = ttf


def words(pdf_bytes):
    d = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = []
    for p in d:
        out.extend(w[4] for w in p.get_text("words"))
    d.close()
    return out


def render_hash_diff(a_bytes, b_bytes, dpi=50):
    """Mean absolute pixel difference (0..255) between first pages."""
    da = fitz.open(stream=a_bytes, filetype="pdf")
    db = fitz.open(stream=b_bytes, filetype="pdf")
    if len(da) == 0 or len(db) == 0:
        return None
    pa = da[0].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    pb = db[0].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    if (pa.width, pa.height) != (pb.width, pb.height):
        return f"size differs {pa.width}x{pa.height} vs {pb.width}x{pb.height}"
    sa, sb = pa.samples, pb.samples
    tot = sum(abs(x - y) for x, y in zip(sa, sb))
    return round(tot / len(sa), 2)


results = []
for name, path in docs.items():
    orig = open(path, "rb").read()
    orig_words = words(orig)
    for q in QUALITIES:
        row = {"doc": name, "quality": q, "orig_size": len(orig), "orig_words": len(orig_words)}
        # --- theirs: compress
        t0 = time.perf_counter()
        try:
            t_arch, t_info = theirs.pdf_to_archive(path, quality=q, mode="struct")
            row["t_size"] = len(t_arch)
            row["t_mode"] = t_info["mode"]
            row["t_ctime"] = round(time.perf_counter() - t0, 2)
        except Exception as exc:
            row["t_size"] = f"ERR {exc}"
            t_arch = None
        # --- ours: compress
        t0 = time.perf_counter()
        try:
            o_arch, o_info = ours.pdf_to_archive(path, quality=q)
            row["o_size"] = len(o_arch)
            row["o_mode"] = o_info["mode"]
            row["o_ctime"] = round(time.perf_counter() - t0, 2)
        except Exception as exc:
            row["o_size"] = f"ERR {exc}"
            o_arch = None
        # --- rebuild matrix
        for src, arch in (("t", t_arch), ("o", o_arch)):
            if arch is None:
                continue
            for dec in ("t", "o"):
                key = f"{src}->{dec}"
                t0 = time.perf_counter()
                try:
                    if dec == "t":
                        pdf_bytes, _ = theirs.archive_to_pdf(arch)
                    else:
                        pdf_bytes, _ = ours.archive_to_pdf(arch)
                    w = words(pdf_bytes)
                    row[key] = f"ok {len(w)}w {round(time.perf_counter() - t0, 2)}s"
                    row[key + "_px"] = render_hash_diff(orig, pdf_bytes)
                except Exception as exc:
                    row[key] = f"ERR {type(exc).__name__}: {str(exc)[:60]}"
        results.append(row)
        print(json.dumps(row, ensure_ascii=False))

with open(os.path.join(OUT, "bench.json"), "w") as fh:
    json.dump(results, fh, indent=1, ensure_ascii=False)
