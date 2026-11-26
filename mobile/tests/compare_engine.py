# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Differential test of the conversions: `pdft-cli compress` against engine.py.

Compresses each file with the desktop engine and with the C++ one, under a
set of settings, and compares the archives:

  identical   the same bytes;
  manifest    the same manifest once the payload sizes ("images.*.s") are set
              aside, with every picture of the same kind and size: the
              pictures were encoded by different codec builds (see
              compare_imaging.py), and the PSNR between the two decodings
              says how far apart they are;
  DIFF        anything else, with the first difference found.

    python compare_engine.py PDFT_CLI WORKDIR FILE...
"""

import io
import json
import math
import os
import subprocess
import sys
import time

from PIL import Image, ImageChops

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "application"))
from psditool import engine, format as psdi  # noqa: E402

PDF_CASES = [
    {"quality": "medium"},
    {"quality": "ultra_low", "compact": True},
    {"quality": "low", "mode": "image"},
    {"quality": "medium", "skip_images": True},
    {"quality": "high"},
]
IMAGE_CASES = [
    {"quality": "ultra_low"},
    {"quality": "medium"},
    {"quality": "high"},
    {"quality": "low", "compact": True},
    {"quality": "high", "compact": True},
]


def case_name(stem, case):
    parts = [stem, case["quality"]]
    if case.get("compact"):
        parts.append("compact")
    if case.get("mode", "struct") != "struct":
        parts.append(case["mode"])
    if case.get("skip_images"):
        parts.append("text")
    return "__".join(parts)


def run_python(path, case):
    started = time.perf_counter()
    data, info = engine.pdf_to_archive(
        path, quality=case["quality"], mode=case.get("mode", "struct"),
        skip_images=case.get("skip_images", False), compact=case.get("compact", False))
    return data, info, time.perf_counter() - started


def run_cpp(cli, path, case, out):
    args = [cli, "compress", path, out, "--quality", case["quality"],
            "--mode", case.get("mode", "struct")]
    if case.get("compact"):
        args.append("--compact")
    if case.get("skip_images"):
        args.append("--text-only")
    started = time.perf_counter()
    done = subprocess.run(args, capture_output=True, text=True)
    elapsed = time.perf_counter() - started
    if done.returncode != 0:
        return None, {"error": done.stderr.strip()}, elapsed
    with open(out, "rb") as f:
        return f.read(), json.loads(done.stdout), elapsed


# --- comparing ---------------------------------------------------------------------


def first_difference(a, b, path="$"):
    """The path of the first place two JSON values differ, or None."""
    if type(a) is not type(b):
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
                and not isinstance(a, bool) and not isinstance(b, bool):
            return f"{path}: {a!r} ({type(a).__name__}) != {b!r} ({type(b).__name__})"
        return f"{path}: {type(a).__name__} != {type(b).__name__}"
    if isinstance(a, dict):
        if list(a) != list(b):
            return f"{path}: keys {list(a)[:12]} != {list(b)[:12]}"
        for key in a:
            found = first_difference(a[key], b[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(a, list):
        for index, (x, y) in enumerate(zip(a, b)):
            found = first_difference(x, y, f"{path}[{index}]")
            if found:
                return found
        if len(a) != len(b):
            return f"{path}: {len(a)} items != {len(b)}"
        return None
    return None if a == b else f"{path}: {a!r} != {b!r}"


def decoded(payload):
    img = Image.open(io.BytesIO(payload))
    img.load()
    return img


def psnr(a, b):
    if a.mode != b.mode:
        a, b = a.convert("RGBA"), b.convert("RGBA")
    diff = ImageChops.difference(a, b)
    # histogram(): 256 counts per band, one band after the other
    squares = sum(count * (value % 256) ** 2 for value, count in enumerate(diff.histogram()) if count)
    samples = a.size[0] * a.size[1] * len(a.getbands())
    if squares == 0:
        return math.inf
    return 10 * math.log10(65025 * samples / squares)


def compare_payloads(a, b):
    """("same" | "codec" | "DIFF", psnr, note) for two image payloads."""
    if a == b:
        return "same", math.inf, ""
    kind_a, kind_b = psdi.payload_kind(a), psdi.payload_kind(b)
    if kind_a != kind_b:
        return "DIFF", None, f"kind {kind_a} != {kind_b}"
    img_a, img_b = decoded(a), decoded(b)
    if img_a.size != img_b.size or img_a.mode != img_b.mode:
        return "DIFF", None, f"{img_a.mode}{img_a.size} != {img_b.mode}{img_b.size}"
    return "codec", psnr(img_a, img_b), f"{kind_a} {len(a)}/{len(b)}"


def without_sizes(manifest):
    out = json.loads(json.dumps(manifest))
    for meta in out.get("images", {}).values():
        meta.pop("s", None)
    return out


def compare_archives(py, cpp):
    """(status, details) for two archives of the same document."""
    if py == cpp:
        return "identical", {}
    v_py, v_cpp = psdi.peek_version(py), psdi.peek_version(cpp)
    if v_py != v_cpp:
        return "DIFF", {"note": f"version {v_py} != {v_cpp}"}
    details = {"psnr": [], "notes": []}
    worst = "manifest"
    if v_py in (psdi.ARCHIVE_VERSION_IMAGE, psdi.ARCHIVE_VERSION_COMPACT_IMAGE):
        pages_py, pages_cpp = psdi.read_image(py).pages, psdi.read_image(cpp).pages
        if len(pages_py) != len(pages_cpp):
            return "DIFF", {"note": f"{len(pages_py)} pages != {len(pages_cpp)}"}
        for index, ((w1, h1, p1), (w2, h2, p2)) in enumerate(zip(pages_py, pages_cpp)):
            if (w1, h1) != (w2, h2):
                return "DIFF", {"note": f"page {index}: {(w1, h1)} != {(w2, h2)}"}
            status, value, note = compare_payloads(p1, p2)
            if status == "DIFF":
                return "DIFF", {"note": f"page {index}: {note}"}
            if value is not None and value != math.inf:
                details["psnr"].append(value)
        return worst, details
    read = psdi.read_struct if v_py == psdi.ARCHIVE_VERSION_STRUCT else psdi.read_compact_struct
    a_py, a_cpp = read(py), read(cpp)
    found = first_difference(without_sizes(a_py.manifest), without_sizes(a_cpp.manifest))
    if found:
        return "DIFF", {"note": found}
    if list(a_py.images) != list(a_cpp.images):
        return "DIFF", {"note": f"image keys {list(a_py.images)} != {list(a_cpp.images)}"}
    for key in a_py.images:
        status, value, note = compare_payloads(a_py.images[key], a_cpp.images[key])
        if status == "DIFF":
            return "DIFF", {"note": f"image {key}: {note}"}
        if value is not None and value != math.inf:
            details["psnr"].append(value)
    return worst, details


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    cli, workdir, files = sys.argv[1], sys.argv[2], sys.argv[3:]
    for sub in ("py", "cpp"):
        os.makedirs(os.path.join(workdir, sub), exist_ok=True)
    totals = {"identical": 0, "manifest": 0, "DIFF": 0}
    time_py = time_cpp = 0.0
    results = []
    for path in files:
        stem = os.path.splitext(os.path.basename(path))[0][:24]
        cases = IMAGE_CASES if engine.is_image_file(path) else PDF_CASES
        for case in cases:
            name = case_name(stem, case)
            ext = ".psdz" if case.get("compact") else ".psdi"
            py, info_py, t_py = run_python(path, case)
            with open(os.path.join(workdir, "py", name + ext), "wb") as f:
                f.write(py)
            cpp, info_cpp, t_cpp = run_cpp(cli, path, case, os.path.join(workdir, "cpp", name + ext))
            time_py += t_py
            time_cpp += t_cpp
            if cpp is None:
                status, details = "DIFF", {"note": info_cpp["error"]}
            else:
                status, details = compare_archives(py, cpp)
                for key in ("mode", "auto_mode_switch", "source", "original_size", "extension"):
                    if info_py[key] != info_cpp[key]:
                        status, details = "DIFF", {"note": f"report {key}: {info_py[key]!r} != {info_cpp[key]!r}"}
            totals[status] += 1
            ratio = f"{len(cpp) / len(py):.3f}" if cpp else "-"
            values = details.get("psnr") or []
            quality = f" psnr min {min(values):.1f}" if values else ""
            line = (f"{status:9} {name:52} py {len(py):8} cpp {len(cpp) if cpp else 0:8} x{ratio}"
                    f"  {t_py:6.2f}s/{t_cpp:6.2f}s{quality}")
            if details.get("note"):
                line += "  " + details["note"]
            print(line, flush=True)
            results.append({"case": name, "status": status, "py": len(py),
                            "cpp": len(cpp) if cpp else None, "t_py": t_py, "t_cpp": t_cpp,
                            "psnr": values, "note": details.get("note")})
    print(json.dumps(totals), f"time python {time_py:.1f}s, c++ {time_cpp:.1f}s")
    with open(os.path.join(workdir, "results.json"), "w") as f:
        json.dump(results, f, indent=1)
    return 0 if totals["DIFF"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
