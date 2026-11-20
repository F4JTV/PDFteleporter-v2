# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Differential test of the image layer: `pdft-cli imgcheck` against Pillow.

Builds image jobs from the pages and pictures of the PDFs given and from
synthetic files covering the formats and modes the engine meets, runs them
through Pillow and the desktop engine's own helpers (_psnr, _ssim,
_own_palette, _is_effectively_monochrome), then through `pdft-cli imgcheck`,
and compares.

Everything must come out identical: Pillow's own code (resampling,
quantisation, conversion, colour counts, the fidelity measures, PNG
decoding) is built in, and the codec libraries are Pillow's own versions
(libjpeg-turbo 3.2.0, zlib-ng 2.3.3, libwebp 1.6.0). The one exception is
AVIF where this build's libaom is not Pillow's 3.14.1 (a desktop build on
the system's libaom): the bytes may then differ ("codec"). Files MuPDF
decodes for want of Pillow's decoder (TIFF, BMP, JPEG 2000, PNM) are
compared by their pixels too.

    python compare_imaging.py PDFT_CLI WORKDIR PDF...
"""

import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import zlib

import pymupdf
from PIL import Image, ImageChops, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "application"))
from psditool import engine  # noqa: E402

# --- Pillow's side -------------------------------------------------------------------


def describe(img):
    out = {"mode": img.mode, "size": list(img.size),
           "sha": hashlib.sha256(img.tobytes()).hexdigest()}
    if img.mode == "P":
        out["palette"] = bytes(img.getpalette() or []).hex()
    return out


def raw_image(job, key="raw"):
    with open(job[key], "rb") as f:
        return Image.frombytes(job["mode"], tuple(job["size"]), f.read())


def orientation(img):
    value = img.getexif().get(0x0112, 1)
    return value if isinstance(value, int) else 0


def prepared(job, img):
    if "quantize" in job:
        colors, kmeans = job["quantize"]
        img = img.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, kmeans=kmeans)
    if job.get("own_palette"):
        img = engine._own_palette(img, img.getcolors(256))
    return img


def run(job):
    op = job["op"]
    if op == "open":
        try:
            img = Image.open(job["file"])
        except Exception:  # noqa: BLE001
            return {"error": "cannot open"}
        out = {"mode_before_load": img.mode}
        if "thumb" in job:
            d = job["thumb"]
            img.thumbnail((d, d), Image.LANCZOS)
        out["image"] = describe(img)
        if "dump" in job:
            with open(job["dump"] + ".pillow", "wb") as f:
                f.write(img.tobytes())
        icc = img.info.get("icc_profile")
        out["icc"] = len(icc) if icc is not None else None
        comment = img.info.get("comment")
        out["comment"] = comment.hex() if comment is not None else None
        out["orientation"] = orientation(img)
        return out
    if op == "frames":
        img = Image.open(job["file"])
        frames = []
        for index in range(getattr(img, "n_frames", 1)):
            img.seek(index)
            upright = ImageOps.exif_transpose(img.copy())
            frames.append(describe(upright))
            if "dump" in job:
                with open(f"{job['dump']}.{index}.pillow", "wb") as f:
                    f.write(upright.tobytes())
        return frames
    if op == "resize":
        img = raw_image(job)
        box = tuple(job["box"]) if job.get("box") is not None else None
        return describe(img.resize(tuple(job["to"]), job.get("filter", 3), box=box,
                                   reducing_gap=job.get("gap")))
    if op == "thumbnail":
        img = raw_image(job)
        img.thumbnail((job["max"], job["max"]), Image.LANCZOS, reducing_gap=job.get("gap"))
        return describe(img)
    if op == "convert":
        return describe(raw_image(job).convert(job["to"]))
    if op == "quantize":
        return describe(raw_image(job).quantize(colors=job["colors"],
                                                method=Image.Quantize.MEDIANCUT,
                                                kmeans=job["kmeans"]))
    if op in ("getcolors", "own_palette"):
        img = raw_image(job)
        colours = img.getcolors(job.get("max", 4096))
        if colours is None:
            return None
        if op == "getcolors":
            return [[count, list(pixel) if isinstance(pixel, tuple) else [pixel]]
                    for count, pixel in colours]
        return describe(engine._own_palette(img, colours))
    if op == "metrics":
        a, b = raw_image(job, "a"), raw_image(job, "b")
        return {"psnr": engine._psnr(a, b), "ssim": engine._ssim(a, b)}
    if op == "metrics_files":
        a = raw_image(job, "a")
        try:
            b = Image.open(job["file"])
            b.load()
        except Exception:  # noqa: BLE001
            return {"error": "cannot open"}
        return {"psnr": engine._psnr(a, b), "ssim": engine._ssim(a, b)}
    if op == "monochrome":
        return engine._is_effectively_monochrome(raw_image(job))
    if op == "save":
        img = prepared(job, raw_image(job))
        fmt = job["format"]
        if fmt == "PNG":
            data = engine._image_bytes(img, format="PNG", compress_level=job["level"])
        elif fmt == "JPEG":
            data = engine._image_bytes(img, format="JPEG", quality=job["quality"],
                                       optimize=True, progressive=job["progressive"])
        elif fmt == "WEBP":
            options = {"lossless": job["lossless"], "quality": job["quality"], "method": job["method"]}
            data = engine._image_bytes(img, format="WEBP", **options)
        else:
            data = engine._image_bytes(img, format="AVIF", quality=job["quality"], speed=job["speed"])
        if "out" in job:
            with open(job["out"] + ".pillow", "wb") as f:
                f.write(data)
        decoded = Image.open(io.BytesIO(data))
        return {"len": len(data), "sha": hashlib.sha256(data).hexdigest(), "decoded": describe(decoded)}
    raise ValueError(op)


# --- inputs ----------------------------------------------------------------------------


class Work:
    def __init__(self, root):
        self.root = root
        self.count = 0
        os.makedirs(root, exist_ok=True)

    def path(self, suffix):
        self.count += 1
        return os.path.join(self.root, f"{self.count:04d}{suffix}")

    def raw(self, img):
        path = self.path(".raw")
        with open(path, "wb") as f:
            f.write(img.tobytes())
        return {"raw": path, "mode": img.mode, "size": list(img.size)}

    def file(self, data, suffix):
        path = self.path(suffix)
        with open(path, "wb") as f:
            f.write(data)
        return path


def png_chunk(cid, data):
    return struct.pack(">I", len(data)) + cid + data + struct.pack(">I", zlib.crc32(cid + data))


def handmade_png(width, height, depth, colour, rows, interlace=False, plte=None, trns=None):
    """A PNG written by hand, for what Pillow cannot write: 16-bit colour,
    interlacing. `rows(x0, dx, y)` returns the raw bytes of a pass row."""
    out = b"\x89PNG\r\n\x1a\n"
    out += png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0,
                                          1 if interlace else 0))
    if plte:
        out += png_chunk(b"PLTE", plte)
    if trns:
        out += png_chunk(b"tRNS", trns)
    passes = ([(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2),
               (0, 1, 1, 2)] if interlace else [(0, 0, 1, 1)])
    raw = b""
    for x0, y0, dx, dy in passes:
        if width <= x0 or height <= y0:
            continue
        for y in range(y0, height, dy):
            line = rows(x0, dx, y)
            raw += bytes([y % 5]) + line  # every filter type, applied below
    # apply the filters declared in the first byte of each row
    out_raw = bytearray()
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]
    bpp = max(1, depth * channels // 8)
    offset = 0
    for x0, y0, dx, dy in passes:
        if width <= x0 or height <= y0:
            continue
        pw = (width - x0 + dx - 1) // dx
        row_len = (pw * depth * channels + 7) // 8
        previous = bytearray(row_len)
        for _y in range(y0, height, dy):
            ftype = raw[offset]
            line = bytearray(raw[offset + 1: offset + 1 + row_len])
            offset += 1 + row_len
            encoded = bytearray(row_len)
            for i in range(row_len):
                a = line[i - bpp] if i >= bpp else 0
                b = previous[i]
                c = previous[i - bpp] if i >= bpp else 0
                if ftype == 0:
                    pred = 0
                elif ftype == 1:
                    pred = a
                elif ftype == 2:
                    pred = b
                elif ftype == 3:
                    pred = (a + b) // 2
                else:
                    pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                    pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                encoded[i] = (line[i] - pred) & 0xFF
            out_raw += bytes([ftype]) + encoded
            previous = line
    out += png_chunk(b"IDAT", zlib.compress(bytes(out_raw)))
    out += png_chunk(b"IEND", b"")
    return out


def pattern_rows(width, depth, channels):
    def rows(x0, dx, y):
        values = []
        for x in range(x0, width, dx):
            for c in range(channels):
                v = (x * 37 + y * 11 + c * 101) % (1 << depth)
                values.append(v)
        if depth >= 8:
            fmt = ">" + ("H" if depth == 16 else "B") * len(values)
            return struct.pack(fmt, *values)
        bits = "".join(format(v, f"0{depth}b") for v in values)
        bits += "0" * (-len(bits) % 8)
        return bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))
    return rows


def synthetic_files(work):
    jobs = []
    base = Image.radial_gradient("L").resize((203, 149)).convert("RGB")
    colour = Image.merge("RGB", [base.getchannel(0), base.getchannel(0).rotate(90, expand=False),
                                 base.getchannel(0).transpose(Image.Transpose.FLIP_LEFT_RIGHT)])
    icc = bytes(range(256)) * 300  # a 76.8 kB profile: several APP2 markers
    for quality in (35, 90):
        for progressive in (False, True):
            buf = io.BytesIO()
            colour.save(buf, "JPEG", quality=quality, progressive=progressive)
            jobs.append({"op": "open", "file": work.file(buf.getvalue(), ".jpg")})
    for mode in ("L", "CMYK"):
        buf = io.BytesIO()
        colour.convert(mode).save(buf, "JPEG", quality=80)
        jobs.append({"op": "open", "file": work.file(buf.getvalue(), ".jpg")})
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6
    colour.save(buf, "JPEG", quality=80, icc_profile=icc, comment=b"PDF Teleporter", exif=exif.tobytes())
    oriented = work.file(buf.getvalue(), ".jpg")
    jobs.append({"op": "open", "file": oriented})
    jobs.append({"op": "frames", "file": oriented})
    buf = io.BytesIO()
    colour.save(buf, "JPEG", quality=80,
                xmp=b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><tiff:Orientation>3</tiff:Orientation></x:xmpmeta>')
    xmp_file = work.file(buf.getvalue(), ".jpg")
    jobs.append({"op": "open", "file": xmp_file})
    jobs.append({"op": "frames", "file": xmp_file})
    # large JPEG for the draft path of thumbnail()
    big = colour.resize((2400, 1700), Image.BICUBIC)
    buf = io.BytesIO()
    big.save(buf, "JPEG", quality=85)
    big_file = work.file(buf.getvalue(), ".jpg")
    for d in (100, 400, 700, 1300):
        jobs.append({"op": "open", "file": big_file, "thumb": d})

    # PNG: what Pillow writes, every mode
    palette_img = colour.quantize(colors=12)
    variants = [
        (colour.convert("1"), {}),
        (colour.convert("L"), {}),
        (colour.convert("L"), {"transparency": 17}),
        (colour.convert("L"), {"bits": 2}),
        (colour.convert("L"), {"bits": 4}),
        (colour.convert("I;16"), {}),
        (colour.convert("LA"), {}),
        (colour, {}),
        (colour, {"transparency": (10, 20, 30)}),
        (colour.convert("RGBA"), {}),
        (palette_img, {}),
        (palette_img, {"transparency": 3}),
        (palette_img, {"transparency": bytes(range(0, 240, 20))}),
        (colour.quantize(colors=3), {}),
        (colour.quantize(colors=200), {}),
        (colour, {"icc_profile": icc[:5000]}),
        (colour, {"exif": exif.tobytes()}),
    ]
    for img, options in variants:
        buf = io.BytesIO()
        img.save(buf, "PNG", **options)
        path = work.file(buf.getvalue(), ".png")
        jobs.append({"op": "open", "file": path})
        jobs.append({"op": "frames", "file": path})
    # PNG: hand-made, 16-bit colour and interlaced
    w, h = 37, 29
    for depth, colour_type, channels in ((16, 2, 3), (16, 6, 4), (16, 4, 2), (8, 2, 3), (4, 0, 1),
                                         (2, 0, 1), (1, 0, 1), (16, 0, 1), (8, 4, 2), (8, 6, 4)):
        for interlace in (False, True):
            data = handmade_png(w, h, depth, colour_type, pattern_rows(w, depth, channels), interlace)
            jobs.append({"op": "open", "file": work.file(data, ".png")})
    plte = bytes(v for i in range(16) for v in (i * 16, 255 - i * 16, i * 7))
    for depth in (1, 2, 4, 8):
        data = handmade_png(w, h, depth, 3, pattern_rows(w, depth, 1), True, plte=plte,
                            trns=bytes([255] * 3 + [0] + [255] * 4))
        path = work.file(data, ".png")
        jobs.append({"op": "open", "file": path})
        jobs.append({"op": "frames", "file": path})

    # WebP: still, lossless, alpha, animated
    for options in ({"quality": 80}, {"lossless": True}, {"quality": 50, "method": 6}):
        for img in (colour, colour.convert("RGBA")):
            buf = io.BytesIO()
            img.save(buf, "WEBP", **options)
            path = work.file(buf.getvalue(), ".webp")
            jobs.append({"op": "open", "file": path})
    buf = io.BytesIO()
    colour.save(buf, "WEBP", save_all=True, append_images=[colour.rotate(30), colour.convert("L").convert("RGB")],
                duration=100, lossless=True)
    jobs.append({"op": "frames", "file": work.file(buf.getvalue(), ".webp")})
    buf = io.BytesIO()
    colour.save(buf, "WEBP", quality=80, exif=exif.tobytes(), icc_profile=icc[:3000])
    path = work.file(buf.getvalue(), ".webp")
    jobs.append({"op": "open", "file": path})
    jobs.append({"op": "frames", "file": path})

    # AVIF, as the engine writes it
    for img in (colour, colour.convert("L")):
        for quality in (30, 80):
            buf = io.BytesIO()
            img.save(buf, "AVIF", quality=quality, speed=6)
            jobs.append({"op": "open", "file": work.file(buf.getvalue(), ".avif")})

    # Formats left to MuPDF
    for fmt, suffix, img in (("TIFF", ".tif", colour), ("BMP", ".bmp", colour), ("TIFF", ".tif", colour.convert("L")),
                             ("JPEG2000", ".jp2", colour), ("PPM", ".ppm", colour)):
        buf = io.BytesIO()
        img.save(buf, fmt)
        jobs.append({"op": "open", "file": work.file(buf.getvalue(), suffix)})
    buf = io.BytesIO()
    colour.save(buf, "TIFF", save_all=True, append_images=[colour.convert("L"), colour.rotate(90)])
    jobs.append({"op": "frames", "file": work.file(buf.getvalue(), ".tif")})
    return jobs


def pdf_images(work, pdf_path, limit_pages=4):
    """Rendered pages and placed pictures of a PDF, as raw RGB and L images."""
    images = []
    files = []
    doc = pymupdf.open(pdf_path)
    for number in range(min(limit_pages, doc.page_count)):
        page = doc[number]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(0.9, 0.9), alpha=False)
        images.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
        for info in page.get_images(full=True)[:3]:
            try:
                raw = doc.extract_image(info[0])
            except Exception:  # noqa: BLE001
                continue
            files.append(work.file(raw["image"], "." + raw["ext"]))
    return images, files


def image_jobs(work, img, small):
    jobs = []
    raw = work.raw(img)
    grey = img.convert("L")
    raw_l = work.raw(grey)
    w, h = img.size
    jobs.append(dict(raw, op="monochrome"))
    jobs.append(dict(raw, op="convert", to="L"))
    jobs.append(dict(raw_l, op="convert", to="RGB"))
    for target in ((max(1, w // 3), max(1, h // 3)), (max(1, w * 2 // 5), max(1, h * 3 // 7)), (64, 64),
                   (w + 13, h + 7)):
        for flt in (Image.LANCZOS, Image.BICUBIC):
            jobs.append(dict(raw, op="resize", to=list(target), filter=flt))
        jobs.append(dict(raw, op="resize", to=list(target), filter=Image.LANCZOS, gap=2.0))
    jobs.append(dict(raw_l, op="resize", to=[max(1, w // 4), max(1, h // 4)], filter=Image.LANCZOS, gap=2.0))
    jobs.append(dict(raw, op="resize", to=[max(1, w // 5), max(1, h // 5)], filter=Image.LANCZOS,
                     box=[0, 0, w - 0.5, h - 0.25], gap=2.0))
    for d in (100, 333, 800):
        jobs.append(dict(raw, op="thumbnail", max=d, gap=2.0))
    jobs.append(dict(raw_l, op="thumbnail", max=150, gap=None))
    # fidelity: against a JPEG made and decoded by Pillow, so both sides read
    # the same pixels
    for source, raw_src in ((img, raw), (grey, raw_l)):
        buf = io.BytesIO()
        source.save(buf, "JPEG", quality=30)
        decoded = Image.open(io.BytesIO(buf.getvalue()))
        decoded.load()
        jobs.append({"op": "metrics", "a": raw_src["raw"], "b": work.raw(decoded)["raw"],
                     "mode": source.mode, "size": list(source.size)})
    if small:
        colours = img.getcolors(4096)
        jobs.append(dict(raw, op="getcolors", max=4096))
        for colors in (4, 16, 64):
            jobs.append(dict(raw, op="quantize", colors=colors, kmeans=2))
            jobs.append(dict(raw_l, op="quantize", colors=colors, kmeans=2))
        if colours is not None and len(colours) <= 256:
            jobs.append(dict(raw, op="own_palette"))
        for level in (0, 9):
            jobs.append(dict(raw_l, op="save", format="PNG", level=level))
            jobs.append(dict(raw, op="save", format="PNG", level=level, quantize=[16, 2]))
        jobs.append(dict(raw, op="save", format="WEBP", lossless=True, quality=100, method=4))
        jobs.append(dict(raw_l, op="save", format="WEBP", lossless=True, quality=100, method=4))
        jobs.append(dict(raw, op="save", format="WEBP", lossless=False, quality=60, method=6))
        jobs.append(dict(raw, op="save", format="JPEG", quality=60, progressive=True))
        jobs.append(dict(raw_l, op="save", format="JPEG", quality=60, progressive=False))
        jobs.append(dict(raw, op="save", format="AVIF", quality=60, speed=6))
        jobs.append(dict(raw_l, op="save", format="AVIF", quality=60, speed=6))
    return jobs


# --- comparison ------------------------------------------------------------------------


LOSSY_SUFFIXES = (".jpg", ".jpeg", ".avif", ".jp2", ".jpx")


def unsha(value):
    """The result without pixel digests, for comparing everything else."""
    if isinstance(value, dict):
        return {k: (None if k == "sha" else unsha(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [unsha(v) for v in value]
    return value


def raw_psnr(job, expected):
    """PSNR between the two decodes of a lossy file, from their dumps."""
    worst = 99.0
    images = [expected["image"]] if isinstance(expected, dict) else expected
    for index, desc in enumerate(images):
        suffix = "" if isinstance(expected, dict) else f".{index}"
        try:
            with open(job["dump"] + suffix + ".pillow", "rb") as f:
                a = Image.frombytes(desc["mode"], tuple(desc["size"]), f.read())
            with open(job["dump"] + suffix + ".pdft", "rb") as f:
                b = Image.frombytes(desc["mode"], tuple(desc["size"]), f.read())
        except (OSError, ValueError):
            return None
        worst = min(worst, engine._psnr(a, b))
    return worst


def compare(job, expected, got):
    """'ok', 'codec' (a lossy codec's own difference) or a description."""
    if expected == got:
        return "ok"
    if job["op"] == "save" and isinstance(expected, dict) and isinstance(got, dict):
        if "exception" in got:
            return "exception: " + got["exception"]
        if job["format"] == "AVIF":
            return "codec"
    if job["op"] in ("open", "frames") and job["file"].endswith(LOSSY_SUFFIXES):
        if unsha(expected) == unsha(got):
            psnr = raw_psnr(job, expected)
            CODEC_PSNR.append((psnr, job["file"]))
            return "codec" if psnr is not None and psnr >= 30 else "codec, far apart"
    return "DIFF"


CODEC_PSNR = []


def main():
    cli, root = sys.argv[1], sys.argv[2]
    work = Work(root)
    jobs = synthetic_files(work)
    for pdf in sys.argv[3:]:
        images, files = pdf_images(work, pdf)
        for img in images:
            jobs.extend(image_jobs(work, img, small=False))
            # graphics-sized pieces for the palette and codec jobs
            w, h = img.size
            jobs.extend(image_jobs(work, img.crop((w // 8, h // 6, w // 8 + 260, h // 6 + 190)), small=True))
            jobs.extend(image_jobs(work, img.resize((61, 43), Image.LANCZOS), small=True))
        for path in files:
            jobs.append({"op": "open", "file": path})
            jobs.append({"op": "open", "file": path, "thumb": 100})
            try:
                picture = Image.open(path)
                picture.load()
                if picture.mode != "RGB":
                    picture = picture.convert("RGB")
                picture.thumbnail((240, 240), Image.LANCZOS)
                jobs.extend(image_jobs(work, picture, small=True))
            except Exception:  # noqa: BLE001 - formats Pillow cannot read
                pass
    for index, job in enumerate(jobs):
        if job["op"] == "save":
            job["out"] = os.path.join(root, f"save{index:04d}")
        if job["op"] in ("open", "frames"):
            job["dump"] = os.path.join(root, f"dump{index:04d}")
    jobs_path = os.path.join(root, "jobs.json")
    with open(jobs_path, "w") as f:
        json.dump(jobs, f)
    expected = []
    for job in jobs:
        try:
            expected.append(run(job))
        except Exception as exc:  # noqa: BLE001
            expected.append({"exception": f"{type(exc).__name__}: {exc}"})
    got = json.loads(subprocess.run([cli, "imgcheck", jobs_path], check=True,
                                    capture_output=True).stdout)
    tally = {}
    for job, e, g in zip(jobs, expected, got):
        verdict = compare(job, e, g)
        tally[verdict] = tally.get(verdict, 0) + 1
        if verdict not in ("ok", "codec"):
            print(json.dumps(job)[:300])
            print("  pillow:", json.dumps(e)[:400])
            print("  pdft:  ", json.dumps(g)[:400])
    print(tally)
    if CODEC_PSNR:
        values = sorted(p for p, _ in CODEC_PSNR if p is not None)
        print(f"decoders of lossy formats: {len(values)} images, PSNR between the two decodes "
              f"min {values[0]:.1f} dB, median {values[len(values) // 2]:.1f} dB")
    return 0 if set(tally) <= {"ok", "codec"} else 1


if __name__ == "__main__":
    sys.exit(main())
