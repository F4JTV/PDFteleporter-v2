# Copyright (C) 2026 The PDF Teleporter contributors
# Portions derived from PDF Teleporter by F1GBD (Jean-Louis Naudin),
# ADRASEC 77 - FNRASEC, author of the original program and of the .psdi
# format.
# SPDX-License-Identifier: GPL-3.0-only
"""PDF <-> .psdi conversion.

Two encodings share one container:

*Structured* keeps text as text. Each span carries its bounding box, size,
colour and a coarse font classification; images are extracted once by xref and
re-placed by reference. This is by far the most compact option for a document
that was born digital, because the glyphs never become pixels.

*Image* renders every page to a single JPEG. It is bigger, but it is the only
thing that works for a scan, and it is the only path that honours page
rotation and ink masks correctly, because get_pixmap composites the page the
same way a viewer would.

Choosing between them is not left to the operator: a scanned prefectural order
looks like a PDF but behaves like a photograph, and picking the structured
path for it produces a page rotated by 90 degrees on a black background.
`detect_optimal_mode` catches that case up front.
"""

from __future__ import annotations

import array
import functools
import io
import logging
import math
import os
import statistics
import time
from collections.abc import Callable

try:
    # PyMuPDF 1.24+ exposes the package under its own name; the legacy `fitz`
    # alias still works but prints a deprecation warning on every import,
    # which is noise in an operator-facing log.
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older PyMuPDF
    try:
        import fitz
    except ImportError as _exc:  # pragma: no cover
        # A frozen build reaching this point was packaged without PyMuPDF.
        # The bare ModuleNotFoundError traceback tells an operator nothing,
        # so say what is actually wrong and who can fix it.
        raise ImportError(
            "PyMuPDF is required but is not available.\n"
            "  Running from source: pip install pymupdf\n"
            "  Running a packaged build: the build environment was missing "
            "PyMuPDF, so it was never bundled. Rebuild it."
        ) from _exc

from PIL import Image, ImageChops, ImageMath, features as _pil_features

from . import format as psdi
from .presets import QUALITY_MEDIUM, QUALITY_PRESETS, estimate_times

log = logging.getLogger(__name__)

MODE_STRUCT = "struct"
MODE_IMAGE = "image"

# A page holding fewer than this many characters while a single image covers
# at least IMG_COVER_MIN of its area is, in practice, a scan.
TEXT_MIN_CHARS = 50
IMG_COVER_MIN = 0.5

ProgressFn = Callable[[int, int, str], None] | None

# Ligatures that survive as private or non-standard code points in PDFs
# produced by LibreOffice. Left alone they reach the rebuilt document as
# replacement characters in the middle of ordinary French words.
_LIGATURES = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "ft",
    "\ufb06": "st",
    "\u019f": "ti",  # LibreOffice CIDFont glyph for the "ti" ligature
    "\u01a9": "tt",  # ... and for "tt"
}

_MONO_HINTS = ("courier", "mono", "consolas", "menlo", "cour")
_SERIF_HINTS = (
    "times",
    "serif",
    "cambria",
    "georgia",
    "garamond",
    "tinos",
    "liberation serif",
    "freeserif",
    "dejavu serif",
    "noto serif",
)

_FAMILY_CSS = {
    "s": "Helvetica, Arial, sans-serif",
    "t": "'Times New Roman', Times, serif",
    "m": "'Courier New', Courier, monospace",
}

_FONT_FALLBACK = {"R": "helv", "B": "hebo", "I": "heit", "BI": "hebi"}

# Span keys whose value the rebuild side already supplies as a default. Storing
# them explicitly costs bytes on every span for no gain, and omitting them is
# safe for any decoder that reads the manifest with .get(key, default) --
# which is what the format has always required.
_SPAN_DEFAULTS = {"f": "R", "fa": "s", "c": 0}

_HTML_BASE_CSS = (
    "* { margin:0; padding:0; box-sizing:border-box; }"
    "html, body { margin:0; padding:0; line-height:1; }"
)


def _progress(cb: ProgressFn, step: int, total: int, message: str) -> None:
    """Report progress, never letting a faulty callback abort a conversion."""
    if cb is None:
        return
    try:
        cb(step, total, message)
    except Exception:  # noqa: BLE001
        log.debug("Progress callback raised", exc_info=True)


# Progress is reported on a fixed scale so the bar never changes its maximum
# mid-conversion, which made the percentage jump when a phase with a
# different step count took over.
PROGRESS_SCALE = 1000
_PROGRESS_MIN_INTERVAL = 0.05  # seconds between two updates of one phase

# Relative cost of each unit of work, measured on an eight-page bulletin. The
# absolute values are irrelevant; only their ratios shape the bar. A text
# line on rebuild is the reference: one Story layout, about a millisecond.
_COST_REBUILD_LINE = 1.0
# Page creation, rectangles, images, links, and grafting the page's text
# layer: around 20 ms per page.
_COST_REBUILD_PAGE = 20.0
# Saving with garbage=4 deduplicates resources, so its cost follows the
# object count, which follows the line count: a few percent of drawing.
_COST_REBUILD_SAVE_PER_LINE = 0.05
_COST_REBUILD_SAVE_PER_PAGE = 0.5  # page-image mode: one image per page
_COST_COMPRESS_IMAGE = 1.0      # extract, mask, downscale, JPEG encode
# Compact archives add the AVIF quality search: about 0.2 s per image of the
# WHO bulletin, eighteen times a page's share of the rest.
_COST_COMPRESS_IMAGE_COMPACT = 18.0
_COST_COMPRESS_PAGE = 5.0       # artwork regions, rawdict extraction
_COST_COMPRESS_WRITE_PER_PAGE = 8.0  # LZMA at preset 9 over the manifest
_COST_RENDER_PAGE = 5.0         # page-image mode: pixmap and JPEG encode
_COST_RENDER_WRITE_PER_PAGE = 1.0


class _Progress:
    """Drive the progress callback from weighted units of work.

    Reporting once per phase left the bar at 50 % for the whole of a rebuild,
    which on a dense bulletin is fifteen seconds of apparent hang. Work is
    counted in units weighted to track wall time -- one per text line on
    rebuild, one per image and five per page on compression -- so the bar
    advances with what is actually being done.

    Updates are rate-limited to one every _PROGRESS_MIN_INTERVAL: a page of
    three hundred table cells would otherwise queue hundreds of cross-thread
    signals in a second. Phase changes and the final report always go
    through, so no label is ever lost.
    """

    def __init__(self, cb: ProgressFn, total_units: float):
        self._cb = cb
        self._total = max(float(total_units), 1.0)
        self._done = 0.0
        self._last_time = 0.0

    def advance(self, units: float, message: str, force: bool = False) -> None:
        """Credit `units` of work and report, subject to rate limiting."""
        self._done = min(self._done + units, self._total)
        self._emit(message, force)

    def phase(self, message: str) -> None:
        """Report the start of a new phase, unconditionally."""
        self._emit(message, force=True)

    def finish(self, message: str) -> None:
        self._done = self._total
        self._emit(message, force=True)

    def _emit(self, message: str, force: bool) -> None:
        if self._cb is None:
            return
        now = time.monotonic()
        if not force and now - self._last_time < _PROGRESS_MIN_INTERVAL:
            return
        self._last_time = now
        step = int(round(self._done / self._total * PROGRESS_SCALE))
        _progress(self._cb, min(step, PROGRESS_SCALE), PROGRESS_SCALE, message)


def _color_to_hex(color) -> str:
    if not color or not isinstance(color, (list, tuple)) or len(color) != 3:
        return ""
    r, g, b = (max(0, min(255, int(round(c * 255)))) for c in color)
    return f"#{r:02x}{g:02x}{b:02x}"


def _hex_to_rgb(hex_color: str):
    """Convert #rrggbb to the 0..1 float triple PyMuPDF expects."""
    if not hex_color or not hex_color.startswith("#") or len(hex_color) != 7:
        return None
    try:
        return tuple(int(hex_color[i : i + 2], 16) / 255.0 for i in (1, 3, 5))
    except ValueError:
        return None


# A span whose replacement characters outnumber its readable ones is not a
# word with a ligature in it; it is text whose font carries no ToUnicode table
# at all. Guessing a ligature for every glyph turns a callsign into a string
# of "ti", silently. Half is the dividing line: real ligatures are one or two
# glyphs inside an otherwise readable word, so their share never approaches it.
_UNREADABLE_RATIO = 0.5


def _is_unreadable(text: str) -> bool:
    """Report whether a span's text was lost to a missing ToUnicode table.

    Two symptoms, depending on the font. A simple font yields U+FFFD for each
    unmapped glyph. A CID font with Identity encoding yields the raw glyph
    identifiers instead, which land as C0 control characters and stray
    punctuation -- "F4LTV" arrives as ")\\x17/79".

    Replacement characters are judged by share, because a real ligature is
    one lost glyph inside an otherwise readable word. A control character is
    different: prose never contains one at all, so a single occurrence is
    enough to condemn the span. Without that distinction ")\\x17/79" -- one
    control character among five -- reads as 80% fine and slips through.
    """
    visible = [c for c in text if not c.isspace()]
    if not visible:
        return False
    if any(ord(c) < 0x20 or 0x7F <= ord(c) < 0xA0 for c in visible):
        return True
    lost = sum(1 for c in visible if c == "\ufffd")
    return lost / len(visible) >= _UNREADABLE_RATIO


def _normalize_text(text: str) -> str:
    """Undo the ligature damage described above.

    Where the source glyph is already lost and only U+FFFD remains, the
    substitution is guessed from context: "tt" before an accented e or after
    "lu", "ti" otherwise. That covers the French vocabulary this is used on
    (situation, lutte, quitté, pollution) without a dictionary.

    This guess is only safe for the odd lost glyph inside a readable word.
    A span that is mostly replacement characters never reaches here: the
    caller rasterises it instead, because no amount of guessing recovers a
    value typed by an operator in a font that carries no ToUnicode table.
    """
    for src, dst in _LIGATURES.items():
        text = text.replace(src, dst)

    if "\ufffd" not in text:
        return text

    chars = list(text)
    for i, char in enumerate(chars):
        if char != "\ufffd":
            continue
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if nxt in ("é", "è", "ê"):
            chars[i] = "tt"
        elif nxt == "e" and "".join(chars[max(0, i - 2) : i]) == "lu":
            chars[i] = "tt"
        else:
            chars[i] = "ti"
    return "".join(chars)


def _quantize(value: float, precision: int) -> float | int:
    """Round one coordinate, returning an int when no decimal is needed.

    Emitting 42 rather than 42.0 matters: the manifest is JSON, so every
    saved character is a byte the modem does not have to send.
    """
    if precision <= 0:
        return int(round(value))
    rounded = round(value, precision)
    return int(rounded) if float(rounded).is_integer() else rounded


def _quantize_box(box, precision: int) -> list:
    return [_quantize(v, precision) for v in box]


def _classify_font(font_name: str) -> tuple[str, str]:
    """Map a PDF font name to a (weight/style key, family key) pair.

    Only the classification is stored, not the font itself: embedding fonts
    would defeat the whole point, and the rebuild side approximates with the
    base-14 equivalents.
    """
    lower = font_name.lower()
    bold = "bold" in lower
    italic = "italic" in lower or "oblique" in lower

    if bold and italic:
        key = "BI"
    elif bold:
        key = "B"
    elif italic:
        key = "I"
    else:
        key = "R"

    if any(hint in lower for hint in _MONO_HINTS):
        family = "m"
    elif any(hint in lower for hint in _SERIF_HINTS):
        family = "t"
    else:
        family = "s"

    return key, family


def _is_axis_aligned_rectangle(points, tolerance: float = 0.6) -> bool:
    """Report whether a polyline traces an axis-aligned rectangle.

    Accepting any four-or-more-point polyline as a rectangle is wrong and
    visibly so: the data series of a line chart becomes a coloured frame
    around its own extent, and every straight segment inside a glyph outline
    becomes a small black box. A rectangle has to be checked, not assumed --
    four corners, every side horizontal or vertical, and consecutive sides
    perpendicular.
    """
    if len(points) < 4:
        return False

    # Drop the closing point when the path returns to its origin.
    pts = list(points)
    if (abs(pts[0][0] - pts[-1][0]) <= tolerance
            and abs(pts[0][1] - pts[-1][1]) <= tolerance):
        pts = pts[:-1]
    if len(pts) != 4:
        return False

    horizontal = []
    for index in range(4):
        x0, y0 = pts[index]
        x1, y1 = pts[(index + 1) % 4]
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        if dy <= tolerance and dx > tolerance:
            horizontal.append(True)
        elif dx <= tolerance and dy > tolerance:
            horizontal.append(False)
        else:
            return False  # diagonal, or a zero-length side

    # Sides must alternate direction, otherwise it is a zigzag, not a box.
    return horizontal[0] != horizontal[1] and horizontal[1] != horizontal[2] \
        and horizontal[2] != horizontal[3]


def _rects_from_path_items(items) -> list:
    """Recover axis-aligned rectangles from a vector path.

    Two shapes turn up in practice. Microsoft's PDF chain emits native "re"
    rectangles. LibreOffice draws the same table cell as four separate line
    segments, and MuPDF reports it that way, so runs of connected segments
    are examined too -- but only accepted when they genuinely close into a
    rectangle.
    """
    rects = []
    points = []

    def flush():
        if _is_axis_aligned_rectangle(points):
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            rects.append(fitz.Rect(min(xs), min(ys), max(xs), max(ys)))
        points.clear()

    for item in items:
        kind = item[0]
        if kind == "re":
            flush()
            rects.append(fitz.Rect(item[1]))
        elif kind == "qu":
            # MuPDF folds four connected segments into a quad. When its
            # corners are axis-aligned it is the same table border LibreOffice
            # drew as separate lines, and must be kept.
            flush()
            quad = item[1]
            corners = [(p.x, p.y) for p in (quad.ul, quad.ur, quad.lr, quad.ll)]
            if _is_axis_aligned_rectangle(corners):
                xs = [c[0] for c in corners]
                ys = [c[1] for c in corners]
                rects.append(fitz.Rect(min(xs), min(ys), max(xs), max(ys)))
        elif kind == "l":
            start = (item[1].x, item[1].y)
            end = (item[2].x, item[2].y)
            # A gap means a new sub-path; the previous run cannot continue
            # into it, so evaluate what has accumulated so far.
            if points and (abs(points[-1][0] - start[0]) > 0.6
                           or abs(points[-1][1] - start[1]) > 0.6):
                flush()
            if not points:
                points.append(start)
            points.append(end)
        else:
            # Curves and anything else cannot be reduced to a rectangle.
            flush()

    flush()
    return rects


def detect_optimal_mode(pdf_path: str) -> tuple[str, str | None]:
    """Return (mode, reason) for a document, preferring the compact encoding.

    Reason is None when the structured mode was kept, otherwise it names what
    forced the switch, so the operator can see it in the log.
    """
    try:
        doc = fitz.open(pdf_path)
    except Exception:  # noqa: BLE001
        return MODE_STRUCT, None

    try:
        if doc.page_count == 0:
            return MODE_STRUCT, None

        image_only_pages = 0
        for page in doc:
            # A rotated page cannot be rebuilt from unrotated coordinates.
            if page.rotation % 360 != 0:
                return MODE_IMAGE, "rotation"

            text_len = len(page.get_text("text").strip())
            page_area = abs(page.rect.width * page.rect.height) or 1.0

            img_area = 0.0
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") != 1:
                    continue
                b = block["bbox"]
                img_area += abs((b[2] - b[0]) * (b[3] - b[1]))

            if text_len < TEXT_MIN_CHARS and img_area / page_area >= IMG_COVER_MIN:
                image_only_pages += 1

        if image_only_pages >= max(1, doc.page_count // 2):
            return MODE_IMAGE, "scan"
        return MODE_STRUCT, None
    except Exception:  # noqa: BLE001
        log.debug("Mode detection failed, defaulting to struct", exc_info=True)
        return MODE_STRUCT, None
    finally:
        doc.close()


def _is_effectively_monochrome(img: Image.Image, threshold: float = 0.002) -> bool:
    """Report whether an image carries no meaningful colour.

    Sampled on a thumbnail rather than the full raster: the question is
    whether the page has colour at all, and a scan of a black-and-white order
    answers that at any resolution. A small tolerance absorbs the chroma noise
    that JPEG scanning introduces into nominally grey pixels.
    """
    sample = img.resize((64, 64))
    # getdata() is deprecated in Pillow 11 and removed in 14; tobytes gives
    # the same pixels without the deprecation and without a Python-level
    # sequence wrapper.
    raw = sample.tobytes()
    total = len(raw) // 3
    coloured = 0
    for offset in range(0, total * 3, 3):
        channels = raw[offset:offset + 3]
        if max(channels) - min(channels) > 18:
            coloured += 1
    return (coloured / total) <= threshold if total else True


def _encode_jpeg(img: Image.Image, quality: int, allow_grayscale: bool = True) -> bytes:
    """Encode to JPEG, dropping empty colour channels where possible.

    Progressive encoding is a free five to nine percent: identical pixels,
    smaller file, and every JPEG decoder reads it. Discarding the chroma
    planes of a page that has no colour saves as much again, and a scanned
    order in black ink has nothing to lose.
    """
    if allow_grayscale and img.mode == "RGB" and _is_effectively_monochrome(img):
        img = img.convert("L")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True,
             progressive=True)
    return buf.getvalue()


# --- payload codec for the images of a structured archive ---------------------
# Both decoders hand every image payload of a version 1 archive to MuPDF's
# insert_image, which identifies the format from the bytes and reads PNG as
# readily as JPEG. A logo, a chart or a line of outlined text is a few flat
# colours with sharp edges: exactly what JPEG codes worst, with ringing around
# every edge, and what a small palette codes best. So each image is offered
# both ways and the smaller one is kept -- but a palette version only when it
# is measurably closer to the source than the JPEG it replaces.
#
# Version 2 archives (page images) are left as JPEG: on a scan the noise makes
# any lossless or palette coding several times larger, measured.
_PALETTE_SIZES = (4, 8, 16, 32, 64, 128, 256)
# More distinct colours than this is a photograph; no palette will compete.
_PALETTE_MAX_COLOURS = 4096
# How much closer to the source a palette image must be than the JPEG, in dB
# of PSNR. Without a margin, a two-colour rendering of anti-aliased text could
# edge past a poor JPEG on the metric while looking lighter and thinner.
_PALETTE_MARGIN_DB = 1.0
# PNG's own deflate is only worth trying on small images; on large ones the
# stored form compressed by the container's LZMA always wins.
_PNG_DEFLATE_MAX_PIXELS = 65536
# Stored uncompressed, a palette PNG takes a byte per pixel before LZMA, and
# the reader refuses a payload that expands past MAX_PAYLOAD_SIZE (32 MiB).
# Eight million pixels -- an A4 page at 340 dpi -- keeps far clear of it.
_PNG_MAX_PIXELS = 8_000_000


def _psnr(reference: Image.Image, candidate: Image.Image) -> float:
    """Peak signal-to-noise ratio of `candidate` against `reference`, in dB.

    Computed from Pillow's difference histogram, so it needs no numerical
    library -- numpy is deliberately kept out of the frozen build.
    """
    diff = ImageChops.difference(reference, candidate.convert(reference.mode))
    histogram = diff.histogram()
    squares = 0
    for band in range(len(diff.getbands())):
        counts = histogram[band * 256:(band + 1) * 256]
        squares += sum(count * value * value for value, count in enumerate(counts))
    if squares == 0:
        return 99.0
    samples = reference.width * reference.height * len(diff.getbands())
    return 10 * math.log10(255 * 255 * samples / squares)


def _image_bytes(img: Image.Image, **options) -> bytes:
    buf = io.BytesIO()
    img.save(buf, **options)
    return buf.getvalue()


def _own_palette(img: Image.Image, colours: list) -> Image.Image:
    """Map an image of at most 256 colours onto a palette of those colours.

    Not quite lossless: Pillow maps pixels to palette entries through a cache
    that folds colours a few levels apart, so (101, 101, 101) may come out as
    (100, 100, 100). The result is checked like any other candidate.
    """
    palette = []
    for _, rgb in colours:
        palette.extend(rgb)
    holder = Image.new("P", (1, 1))
    holder.putpalette(palette + [0] * (768 - len(palette)))
    return img.quantize(palette=holder, dither=Image.Dither.NONE)


# K-means passes refining the median-cut palette. Median cut averages each
# colour box, which on anti-aliased text pulls the darkest level towards grey:
# on the bulletin's axis labels the core of the strokes came out at 126 for a
# source at 89. Refined, it comes to 108, PSNR rises by about 3 dB at four
# colours, and the file comes out slightly smaller, not larger. Pillow's
# k-means converges in one or two passes on these images; more cost the same.
_PALETTE_KMEANS = 2


def _palettes_at(base: Image.Image, size: int, target: float) -> list:
    """The quantisation of `base` to `size` colours, if it reaches `target` dB.

    Undithered: Pillow applies no dithering when it builds the palette itself,
    and dithered noise is in any case what LZMA predicts worst.
    """
    quantised = base.quantize(colors=size, method=Image.Quantize.MEDIANCUT,
                              kmeans=_PALETTE_KMEANS)
    return [quantised] if _psnr(base, quantised) >= target else []


def _smallest_palettes(base: Image.Image, n_colours: int, target: float) -> list:
    """Quantisations at the smallest palette size that reaches `target`.

    Fidelity rises with the palette size, so the size is searched rather than
    scanned: a first probe at eight colours, where graphics and outlined text
    usually land, then down while it still passes or up until it does.
    """
    sizes = [size for size in _PALETTE_SIZES if size < n_colours]
    if not sizes:
        return []
    index = min(1, len(sizes) - 1)
    found = _palettes_at(base, sizes[index], target)
    if found:
        while index > 0:
            lower = _palettes_at(base, sizes[index - 1], target)
            if not lower:
                break
            index -= 1
            found = lower
    else:
        while not found and index < len(sizes) - 1:
            index += 1
            found = _palettes_at(base, sizes[index], target)
    return found


def _encode_payload(img: Image.Image, quality: int, lzma_preset: int,
                    compact: bool = False) -> bytes:
    """Encode one image of a structured archive in its smallest faithful form.

    The classic JPEG-or-PNG choice is what a .psdi carries; a compact archive
    then offers AVIF and WebP the same image at equal fidelity.
    """
    if img.mode == "RGB" and _is_effectively_monochrome(img):
        img = img.convert("L")
    payload = _smallest_classic_payload(img, quality, lzma_preset)
    if compact:
        payload = _compact_payload(img, payload, lzma_preset)
    return payload


def _smallest_classic_payload(img: Image.Image, quality: int, lzma_preset: int) -> bytes:
    """The smallest faithful JPEG or PNG, as every .psdi decoder reads them.

    The JPEG is written both progressive and baseline: the two hold the same
    coefficients and decode to the same pixels, and on small images the extra
    scans of a progressive file cost more than they save -- baseline won on
    two thirds of the images of the WHO bulletin. Then, for anything with few
    enough colours to be a graphic, a PNG: lossless for a grey image, the
    image's own colours when it has 256 or fewer, otherwise the smallest
    palette -- and every one of these only when it beats the JPEG's fidelity
    by _PALETTE_MARGIN_DB (a grey PNG always does). Sizes are compared as the
    container will store them, after its optional LZMA, which is what makes
    an uncompressed PNG the usual winner: LZMA codes raw palette indices
    better than deflate.
    """
    def stored(payload: bytes) -> int:
        return len(psdi.pack_payload(payload, lzma_preset)[1])

    best = None
    for progressive in (True, False):
        payload = _image_bytes(img, format="JPEG", quality=quality,
                               optimize=True, progressive=progressive)
        size = stored(payload)
        if best is None or size < best[0]:
            best = (size, payload)

    if img.width * img.height > _PNG_MAX_PIXELS:
        return best[1]
    colours = img.getcolors(_PALETTE_MAX_COLOURS)
    if colours is None:
        return best[1]

    target = _psnr(img, Image.open(io.BytesIO(best[1]))) + _PALETTE_MARGIN_DB
    candidates = []
    if img.mode == "L":
        candidates.append(img)  # grey PNG: lossless
    elif len(colours) <= 256:
        own = _own_palette(img, colours)
        if _psnr(img, own) >= target:
            candidates.append(own)
    candidates.extend(_smallest_palettes(img, len(colours), target))

    levels = (0, 9) if img.width * img.height <= _PNG_DEFLATE_MAX_PIXELS else (0,)
    for candidate in candidates:
        for level in levels:
            payload = _image_bytes(candidate, format="PNG", compress_level=level)
            size = stored(payload)
            if size < best[0]:
                best = (size, payload)
    return best[1]


# --- compact archives (.psdz): AVIF photographs, WebP graphics -----------------
# MuPDF reads neither format, so neither can travel in a .psdi: a station
# running the original would lose the picture. In a .psdz the rebuild decodes
# them itself (see _place_image).
#
# A photograph that the classic choice keeps as JPEG is offered as AVIF at the
# lowest quality whose PSNR *and* SSIM against the very pixels the JPEG was
# made from are at least the JPEG's. Matching PSNR alone was measured to let
# AVIF smooth fine texture below the JPEG's SSIM on four images, two of them
# photographs of the WHO bulletin at "high"; requiring both keeps the promise
# "no less faithful by either measure". On the bulletin's photographs this
# takes 39 to 45 % off the bytes, depending on the preset.
#
# A graphic kept as PNG is re-coded as lossless WebP: the same pixels, so the
# same fidelity, usually smaller.
#
# Tiny images also try lossy WebP, whose container is lighter than AVIF's:
# on an icon of 40x50 pixels AVIF's own headers outweigh the JPEG.

# libaom's speed setting: 6 is within 2 % of the slowest setting's size on the
# bulletin and fourteen times faster. Whole pages (page-image archives) use 7,
# about twice as fast again for 3 to 6 % more bytes: a page is a million
# pixels or more, and the quality search encodes it seven times.
_AVIF_SPEED = 6
_AVIF_PAGE_SPEED = 7
_AVIF_PAGE_PIXELS = 1_000_000
_WEBP_LOSSY_MAX_PIXELS = 20_000
# SSIM as Wang et al. define it and scikit-image computes it by default: a 7x7
# window slid one pixel at a time, sample (co)variances, windows that would
# reach past the border left out. A first version took non-overlapping
# windows, which was quicker; on small and thin images, which hold few of
# them, that estimate disagreed with the standard measure often enough to let
# some lossy WebP and AVIF through slightly below the JPEG's SSIM -- found by
# re-measuring a corpus of real documents with scikit-image.
_SSIM_WINDOW = 7
_SSIM_C1 = (0.01 * 255) ** 2
_SSIM_C2 = (0.03 * 255) ** 2
# A decoded AVIF or WebP larger than this is refused rather than allocated. A
# page at 600 dpi is under 35 megapixels; anything this encoder writes is far
# smaller.
_MAX_DECODED_PIXELS = 50_000_000


def _add_shifted(a):
    """ImageMath body: the sum of the operands s0, s1, ... in order."""
    total = a["s0"]
    for index in range(1, _SSIM_WINDOW):
        total = total + a[f"s{index}"]
    return total


def _window_sums(img: Image.Image) -> Image.Image:
    """The sum of every 7x7 window lying wholly inside `img`, a mode "I" image.

    Separable: seven shifted copies added across, then seven down. Integer
    arithmetic keeps the sums exact; the largest, 49 squared pixel values
    multiplied by 49 again later, stays far below 2**31.
    """
    window = _SSIM_WINDOW
    width, height = img.size
    across = {f"s{i}": img.crop((i, 0, i + width - window + 1, height))
              for i in range(window)}
    rows = ImageMath.lambda_eval(_add_shifted, **across)
    down = {f"s{i}": rows.crop((0, i, width - window + 1, i + height - window + 1))
            for i in range(window)}
    return ImageMath.lambda_eval(_add_shifted, **down)


def _ssim(reference: Image.Image, candidate: Image.Image) -> float:
    """Mean SSIM of `candidate` against `reference`, all bands averaged.

    The standard measure, computed with Pillow alone -- window sums in 32-bit
    integers, the final ratio in 32-bit floats -- so, like _psnr, it needs no
    numerical library. It agrees with scikit-image's
    structural_similarity(data_range=255) to about 1e-7.
    """
    candidate = candidate.convert(reference.mode)
    if min(reference.size) < _SSIM_WINDOW:
        return 1.0 if ImageChops.difference(reference, candidate).getbbox() is None else 0.0
    n = _SSIM_WINDOW * _SSIM_WINDOW
    mean_scale = 1.0 / (n * n)      # (window sum / n) squared
    var_scale = 1.0 / (n * (n - 1))  # sample (co)variance from window sums
    scores = []
    for x_band, y_band in zip(reference.split(), candidate.split()):
        x = x_band.convert("I")
        y = y_band.convert("I")
        sx = _window_sums(x)
        sy = _window_sums(y)
        sxx = _window_sums(ImageMath.lambda_eval(lambda a: a["x"] * a["x"], x=x))
        syy = _window_sums(ImageMath.lambda_eval(lambda a: a["y"] * a["y"], y=y))
        sxy = _window_sums(ImageMath.lambda_eval(lambda a: a["x"] * a["y"], x=x, y=y))
        # Exact numerators: sum(x)*sum(y), n*sum(xy) - sum(x)*sum(y), ...
        cross = ImageMath.lambda_eval(lambda a: a["sx"] * a["sy"], sx=sx, sy=sy)
        squares = ImageMath.lambda_eval(lambda a: a["sx"] * a["sx"] + a["sy"] * a["sy"],
                                        sx=sx, sy=sy)
        covariance = ImageMath.lambda_eval(lambda a: a["sxy"] * n - a["p"], sxy=sxy, p=cross)
        variances = ImageMath.lambda_eval(lambda a: (a["sxx"] + a["syy"]) * n - a["q"],
                                          sxx=sxx, syy=syy, q=squares)
        ssim_map = ImageMath.lambda_eval(
            lambda a: ((2 * mean_scale * a["p"] + _SSIM_C1)
                       * (2 * var_scale * a["c"] + _SSIM_C2))
            / ((mean_scale * a["q"] + _SSIM_C1) * (var_scale * a["v"] + _SSIM_C2)),
            p=cross.convert("F"), q=squares.convert("F"),
            c=covariance.convert("F"), v=variances.convert("F"),
        )
        values = array.array("f", ssim_map.tobytes())
        scores.append(sum(values) / len(values))
    return sum(scores) / len(scores)


def _matching_quality(img: Image.Image, encode, target_psnr: float,
                      target_ssim: float) -> bytes | None:
    """The lowest-quality encoding of `img` at least as faithful as the targets.

    Fidelity rises with the quality setting, so the setting is bisected. The
    PSNR, cheap, is searched first; the SSIM, which costs a second or two on a
    whole page, is then checked at the quality found, and searched upwards from
    there only when it falls short. Only an encoding that passes both tests is
    ever returned.
    """
    payloads = {}

    def decoded(quality: int) -> Image.Image:
        if quality not in payloads:
            payloads[quality] = encode(quality)
        image = Image.open(io.BytesIO(payloads[quality]))
        image.load()
        return image

    def lowest(passes, low: int) -> int | None:
        high, found = 100, None
        while low <= high:
            quality = (low + high) // 2
            if passes(quality):
                found, high = quality, quality - 1
            else:
                low = quality + 1
        return found

    def psnr_passes(quality: int) -> bool:
        return _psnr(img, decoded(quality)) >= target_psnr

    def both_pass(quality: int) -> bool:
        image = decoded(quality)
        return (_psnr(img, image) >= target_psnr
                and _ssim(img, image) >= target_ssim)

    quality = lowest(psnr_passes, 1)
    if quality is not None and _ssim(img, decoded(quality)) < target_ssim:
        quality = lowest(both_pass, quality + 1)
    return None if quality is None else payloads[quality]


@functools.lru_cache(maxsize=None)
def _codec_available(name: str) -> bool:
    """Whether this Pillow can read and write `name` ("avif" or "webp").

    The frozen build refuses to be made without both; running from source
    with an older Pillow, a compact archive is still written, without the
    codec that is missing.
    """
    try:
        return bool(_pil_features.check(name))
    except Exception:  # noqa: BLE001
        return False


def _compact_payload(img: Image.Image, payload: bytes, lzma_preset: int) -> bytes:
    """Offer a classic payload's image as AVIF or WebP; keep the smallest.

    `img` is the very image `payload` was encoded from, so the targets are
    the classic payload's own fidelity to it.
    """
    def stored(data: bytes) -> int:
        return len(psdi.pack_payload(data, lzma_preset)[1])

    best = (stored(payload), payload)
    kind = psdi.payload_kind(payload)
    if kind == "jpeg":
        decoded = Image.open(io.BytesIO(payload))
        decoded.load()
        target_psnr = _psnr(img, decoded)
        target_ssim = _ssim(img, decoded)
        pixels = img.width * img.height
        speed = _AVIF_SPEED if pixels <= _AVIF_PAGE_PIXELS else _AVIF_PAGE_SPEED
        encoders = []
        if _codec_available("avif"):
            encoders.append(lambda q: _image_bytes(img, format="AVIF", quality=q,
                                                   speed=speed))
        if pixels <= _WEBP_LOSSY_MAX_PIXELS and _codec_available("webp"):
            encoders.append(lambda q: _image_bytes(img, format="WEBP", quality=q,
                                                   method=6))
        for encode in encoders:
            # Quality 1 is the smallest a codec can make. When even that is no
            # smaller than the best so far -- AVIF's headers alone outweigh a
            # small JPEG -- no quality can win, and the search is skipped.
            if stored(encode(1)) >= best[0]:
                continue
            found = _matching_quality(img, encode, target_psnr, target_ssim)
            if found is not None and stored(found) < best[0]:
                best = (stored(found), found)
    elif kind == "png" and _codec_available("webp"):
        # The PNG's own pixels, palette resolved: lossless WebP keeps them
        # exactly, so the fidelity is the PNG's whatever the format.
        source = Image.open(io.BytesIO(payload))
        source.load()
        if source.mode not in ("L", "RGB"):
            source = source.convert("RGB")
        # Effort 4 at quality 100: on 740 PNG payloads from six documents,
        # 6.6 % below the PNGs. The slowest setting, method 6, saves about a
        # point and a half more and takes thirty-five times as long -- most of
        # a minute on a document full of diagrams.
        webp = _image_bytes(source, format="WEBP", lossless=True, quality=100, method=4)
        if stored(webp) < best[0]:
            best = (stored(webp), webp)
    return best[1]


def _encode_page(img: Image.Image, params: dict) -> bytes:
    """One page of a page-image archive: JPEG, or AVIF in a compact archive."""
    jpeg = _encode_jpeg(img, params["jpeg_quality"])
    if not params.get("compact"):
        return jpeg
    # _encode_jpeg drops the colour planes of a page without colour; the
    # reference must be the same grey image the JPEG was made from.
    reference = img.convert("L") if Image.open(io.BytesIO(jpeg)).mode == "L" else img
    return _compact_payload(reference, jpeg, params["lzma_preset"])


def _require_codecs(payloads) -> None:
    """Refuse up front an archive whose images this installation cannot decode.

    A damaged payload costs only its own image, skipped with a warning. A
    missing codec is different: it is the installation's fault, it would cost
    every photograph of the document, and the operator would get a PDF without
    them and a log line. Saying so before rebuilding anything points at the
    fix, which is a newer Pillow.
    """
    for payload in payloads:
        kind = psdi.payload_kind(payload)
        if kind in ("avif", "webp") and not _codec_available(kind):
            raise psdi.PsdiError(
                f"This installation cannot decode {kind.upper()} images; "
                "compact archives need Pillow 11.3 or later with AVIF and WebP "
                "support (the Windows ARM64 wheels have no AVIF)"
            )


def _pixmap_from(payload: bytes):
    """Decode an AVIF or WebP payload into a MuPDF pixmap, refusing bombs."""
    kind = psdi.payload_kind(payload)
    _require_codecs([payload])
    try:
        img = Image.open(io.BytesIO(payload))
    except Exception as exc:  # noqa: BLE001 - any parser failure is damage
        raise psdi.PsdiError(f"Unreadable {kind.upper()} image: {exc}") from exc
    if img.width * img.height > _MAX_DECODED_PIXELS:
        raise psdi.PsdiError(
            f"Image of {img.width}x{img.height} pixels, above the "
            f"{_MAX_DECODED_PIXELS} pixel ceiling"
        )
    try:
        img.load()
    except Exception as exc:  # noqa: BLE001 - a truncated stream is damage
        raise psdi.PsdiError(f"Unreadable {kind.upper()} image: {exc}") from exc
    if img.mode not in ("L", "RGB"):
        img = img.convert("RGB")
    colourspace = fitz.csGRAY if img.mode == "L" else fitz.csRGB
    return fitz.Pixmap(colourspace, img.width, img.height, img.tobytes(), False)


def _place_image(page, rect, payload: bytes) -> None:
    """Draw one image payload into `rect`.

    JPEG and PNG go to MuPDF as they are, exactly as before compact archives
    existed. AVIF and WebP, which MuPDF cannot read, are decoded here and
    handed over as pixels: the rebuilt PDF then stores them losslessly, so
    the page shows exactly the pixels whose fidelity the encoder measured.
    The rebuilt file never goes on the air; its size does not matter.
    """
    if psdi.payload_kind(payload) in ("avif", "webp"):
        page.insert_image(rect, pixmap=_pixmap_from(payload))
    else:
        page.insert_image(rect, stream=payload)


def _optimize_image(img_bytes: bytes, width: int, height: int, params: dict,
                    placed_pt: tuple[float, float] | None = None):
    """Downscale and re-encode one extracted image.

    Two ceilings apply and the smaller wins. The first depends on what the
    image appears to be: a large square is treated as a map and given more
    room, a long thin strip is a letterhead banner and is capped by height,
    and a small icon is shrunk hard because it contributes nothing at radio
    bandwidth.

    The second is the size the page actually shows it at. A 762x572 photo
    placed in a 249x187 pt frame needs 249x187 pixels at 72 DPI, not 400x300:
    everything beyond that is decoded, transmitted and discarded by the
    viewer. On a document heavy with photographs this is the largest single
    saving available, and it costs nothing visible because the pixels dropped
    were never displayed.
    """
    try:
        img = Image.open(io.BytesIO(img_bytes))
    except Exception:  # noqa: BLE001
        return None

    if img.mode == "RGBA":
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    if width > 1000 and height < 200:
        # Letterhead banner: constrain both axes explicitly.
        new_w = min(width, params["img_max_dim"])
        new_h = min(height, params["banner_max_h"])
        if (new_w, new_h) != (width, height):
            img = img.resize((new_w, new_h), Image.LANCZOS)
    else:
        if width > 900 and height > 900:
            max_dim = params["map_max_dim"]
        elif width < 300 and height < 300:
            max_dim = 100
        else:
            max_dim = params["img_max_dim"]
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)

    if placed_pt is not None:
        # Pixels needed to fill the placement at the preset's resolution. A
        # small margin covers viewers that upsample slightly when zoomed, and
        # the image is never enlarged: upscaling adds bytes, not detail.
        dpi = params["dpi"]
        need_w = int(round(placed_pt[0] * dpi / 72 * 1.1))
        need_h = int(round(placed_pt[1] * dpi / 72 * 1.1))
        if need_w > 0 and need_h > 0 and (img.width > need_w or img.height > need_h):
            scale = min(need_w / img.width, need_h / img.height, 1.0)
            target = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
            if target != img.size:
                img = img.resize(target, Image.LANCZOS)

    return (_encode_payload(img, params["jpeg_quality"], params["lzma_preset"],
                            params.get("compact", False)),
            width, height)


# Artwork regions are stored as ordinary images, so a decoder written against
# the original specification renders them without knowing anything new. Their
# identifiers are allocated from the top of the uint16 range the container
# uses for xrefs, where they cannot collide with a real one.
_ARTWORK_ID_TOP = 65500
_ARTWORK_MIN_SIDE = 24.0
_ARTWORK_MAX_PAGE_FRACTION = 0.55

# Text that a producer converted to outlines (Excel does this for chart axis
# labels) arrives as one small curve-only path per word. Each word is below
# the artwork size floor and the words sit too far apart to merge, so the
# whole axis vanished. A row or column of such paths is recognised as one
# region; a lone outline path is kept when it is long enough to be a label
# and curved enough to be glyphs rather than a flourish.
_GLYPH_RUN_MAX_GAP = 48.0
_GLYPH_RUN_MIN_COUNT = 3
_GLYPH_MIN_CURVES = 30
_GLYPH_MIN_SHORT_SIDE = 5.0


def _axis_lines_from_path_items(items, tolerance: float = 0.6) -> list:
    """Return the axis-aligned segments of a path as (x0, y0, x1, y1).

    A single horizontal rule under a title, the border of a table drawn as
    separate strokes, a divider between sections: all of these are one
    straight segment, which is not a rectangle and so used to be classified
    as artwork and rasterised into a JPEG tile. A 340 pt rule became a
    340x109 pt picture weighing three kilobytes, for something a dozen bytes
    describe exactly.
    """
    lines = []
    for item in items:
        if item[0] != "l":
            continue
        x0, y0 = item[1].x, item[1].y
        x1, y1 = item[2].x, item[2].y
        horizontal = abs(y1 - y0) <= tolerance and abs(x1 - x0) > tolerance
        vertical = abs(x1 - x0) <= tolerance and abs(y1 - y0) > tolerance
        if horizontal or vertical:
            lines.append((x0, y0, x1, y1))
    return _join_path_segments(lines)


def _join_path_segments(lines: list) -> list:
    """Join the collinear segments of one path that touch or overlap.

    One path is stroked once: where its own segments cross or retrace each
    other the ink goes down a single time. A closed two-point path -- a line
    and its return -- is the common case, and stored as two entries it was
    drawn twice, darker than the original. Separate paths are a different
    matter; see _is_thin.
    """
    horizontal: dict = {}
    vertical: dict = {}
    others = []
    for x0, y0, x1, y1 in lines:
        if y0 == y1:
            horizontal.setdefault(y0, []).append((min(x0, x1), max(x0, x1)))
        elif x0 == x1:
            vertical.setdefault(x0, []).append((min(y0, y1), max(y0, y1)))
        else:
            # Nearly but not exactly axis-aligned: only a retrace of the
            # very same segment is recognised, in either direction.
            segment = tuple(sorted(((x0, y0), (x1, y1))))
            if segment not in others:
                others.append(segment)
    others = [(a[0], a[1], b[0], b[1]) for a, b in others]

    def joined(spans):
        spans.sort()
        out = [list(spans[0])]
        for lo, hi in spans[1:]:
            if lo <= out[-1][1]:
                out[-1][1] = max(out[-1][1], hi)
            else:
                out.append([lo, hi])
        return out

    result = list(others)
    for y, spans in horizontal.items():
        result.extend((lo, y, hi, y) for lo, hi in joined(spans))
    for x, spans in vertical.items():
        result.extend((x, lo, x, hi) for lo, hi in joined(spans))
    return result


def _path_is_only_axis_lines(items, tolerance: float = 0.6) -> bool:
    """A path made solely of horizontal and vertical strokes is structure."""
    if not items or any(item[0] != "l" for item in items):
        return False
    for item in items:
        dx = abs(item[2].x - item[1].x)
        dy = abs(item[2].y - item[1].y)
        if not ((dy <= tolerance < dx) or (dx <= tolerance < dy)):
            return False
    return True


def _path_is_artwork(items) -> bool:
    """Report whether a vector path is picture rather than structure.

    Rectangles are structure: table cells, rules, fills. Anything else --
    curves, diagonals, glyph outlines drawn as paths -- is artwork, and the
    structured encoding has no way to describe it. A doughnut chart is the
    clearest case: it is made entirely of arcs, so recording rectangles for it
    records nothing at all.
    """
    if not items:
        return False
    if any(item[0] == "c" for item in items):
        return True
    # Pure horizontal/vertical strokes are rules and borders, never a chart.
    if _path_is_only_axis_lines(items):
        return False
    for item in items:
        if item[0] != "qu":
            continue
        quad = item[1]
        corners = [(p.x, p.y) for p in (quad.ul, quad.ur, quad.lr, quad.ll)]
        if not _is_axis_aligned_rectangle(corners):
            return True
    # Straight segments that do not close into rectangles: a line-chart
    # series, or the outline of a glyph the producer converted to paths.
    rects = _rects_from_path_items(items)
    segments = sum(1 for item in items if item[0] == "l")
    return segments > 0 and len(rects) * 4 < segments


# --- drawing entries -----------------------------------------------------------
# Both decoders, the original's and this one, draw every "dr" entry of type
# "rect" the same way: draw_rect(entry["r"]), then finish(color=c, fill=f,
# width=w if w > 0.1 else 0). Everything below emits only entries of that
# shape, so whatever it produces renders the same on a station running the
# original version.

# A filled rectangle thinner than this is a rule or a cell border, not an area.
_RULE_MAX_THICKNESS = 2.0
# Thinner than this, a rule stays a fill: both decoders draw a stroke of 0.1 pt
# or less as a one-pixel hairline, darker than the sliver the source paints.
_RULE_MIN_THICKNESS = 0.15
# Bucket size, in points, for the overlap queries made while compacting.
_DRAW_GRID = 32.0
# Each compaction pass can only remove entries; four reach the fixed point on
# every document measured, the last one finding nothing.
_COMPACT_PASSES = 4
_WHITE = "#ffffff"


def _stroke_width(width: float):
    """The "w" to store for a stroke of `width` points, or None for a hairline.

    Both decoders draw "w" only when it is above 0.1, and a one-pixel hairline
    otherwise. A tenth of a point is resolution enough for any stroke but the
    ones just above that threshold: 0.12 rounded to 0.1 used to come out as a
    hairline, several times darker at 150 dpi. Those keep two decimals.
    """
    if width <= 0.1:
        return None
    if width < 0.15:
        return max(round(width, 2), 0.11)
    return round(width, 1)


def _rule_entry(x0: float, y0: float, x1: float, y1: float, colour: str,
                width: float, precision: int) -> dict:
    """Describe a straight rule as a stroked rectangle of zero height or width.

    Rules used to be stored two ways, each lossy somewhere. As "line" entries
    they were invisible to the original decoder, which skips every entry that
    is not a "rect": a LibreOffice table reached a station running it without
    a single border. As thin filled rectangles they did not survive whole-
    point coordinates: an Excel border 0.84 pt thick rounded to either 1 pt or
    to nothing, and a fifth of the borders on a test sheet vanished at the two
    low presets.

    A rectangle with no height, stroked in the rule's colour and thickness,
    paints the band [x0, x1] x [y - w/2, y + w/2]: the same pixels as a butt-
    capped line, measured identical from 150 to 600 dpi. It is an ordinary
    "rect", so both decoders draw it. The thickness travels in "w" at a tenth
    of a point whatever the coordinate precision, so rounding the position can
    move a rule by half a point but can no longer thin it out of existence.
    """
    if abs(x1 - x0) >= abs(y1 - y0):
        centre = (y0 + y1) / 2
        box = (min(x0, x1), centre, max(x0, x1), centre)
    else:
        centre = (x0 + x1) / 2
        box = (centre, min(y0, y1), centre, max(y0, y1))
    entry = {"t": "rect", "r": _quantize_box(box, precision), "c": colour}
    stroke = _stroke_width(width)
    if stroke is not None:
        entry["w"] = stroke
    return entry


def _drawing_kind(entry: dict) -> str:
    """Classify a drawing entry: a plain fill, a rule, or anything else."""
    if entry.get("t") != "rect":
        return "other"
    x0, y0, x1, y1 = entry["r"]
    if "f" in entry and "c" not in entry:
        return "fill"
    if "c" in entry and "f" not in entry and (x0 == x1 or y0 == y1):
        return "rule"
    return "other"


def _drawing_footprint(entry: dict) -> tuple:
    """The area an entry can paint, widened by half its stroke if it has one.

    A hairline (no "w") is drawn one device pixel wide whatever the zoom, which
    at thumbnail scale is wider than a point; it is given a point either side.
    Generous margins only make the overlap tests below more cautious.
    """
    x0, y0, x1, y1 = entry["r"]
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    if "c" in entry or entry.get("t") == "line":
        pad = max(entry["w"] / 2, 0.5) if "w" in entry else 1.0
        return (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    return (x0, y0, x1, y1)


def _paints_nothing(kind: str, box) -> bool:
    """A fill or a rule that has shrunk to a point puts no ink on the page.

    A fill with width but no height is kept: MuPDF still draws it as a faint
    one-pixel line, much as the source drew the sliver it came from.
    """
    x0, y0, x1, y1 = box
    if kind in ("fill", "rule"):
        return x0 == x1 and y0 == y1
    return False


def _is_thin(kind: str, box) -> bool:
    """Report whether painting this entry twice would show on screen.

    Opaque paint applied twice looks the same as once -- except where it only
    partly covers a pixel. A rule or a fill under _RULE_MAX_THICKNESS is
    narrower than a pixel or two at screen zoom, so its whole length is that
    kind of pixel: drawn twice, it comes out visibly darker. LibreOffice draws
    every inner border twice, once per adjacent cell, and the original looks
    the darker for it; collapsing the copies made those borders a shade lighter
    than the original at 100 %. Thin entries are therefore only ever joined
    end to end, never folded over one another.
    """
    if kind == "rule":
        return True
    x0, y0, x1, y1 = box
    return min(x1 - x0, y1 - y0) < _RULE_MAX_THICKNESS


def _union_box(kind: str, a, b):
    """The union of two boxes of one kind when it is itself such a box.

    Fills join when they share a full edge or overlap along a row or column.
    Rules join when they lie on the same line and touch. Thin entries must
    meet exactly end to end; thicker ones may also overlap, which changes at
    most the anti-aliasing of a few edge pixels. Anything else returns None:
    the union would paint pixels neither entry painted, or paint once what
    the source painted twice.
    """
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    thin = _is_thin(kind, a) or _is_thin(kind, b)

    def joins(lo_a, hi_a, lo_b, hi_b) -> bool:
        if thin:
            return hi_a == lo_b or hi_b == lo_a
        return lo_a <= hi_b and lo_b <= hi_a

    if kind == "fill":
        if ay0 == by0 and ay1 == by1 and joins(ax0, ax1, bx0, bx1):
            return [min(ax0, bx0), ay0, max(ax1, bx1), ay1]
        if ax0 == bx0 and ax1 == bx1 and joins(ay0, ay1, by0, by1):
            return [ax0, min(ay0, by0), ax1, max(ay1, by1)]
        return None
    if ay0 == ay1 == by0 == by1 and joins(ax0, ax1, bx0, bx1):
        return [min(ax0, bx0), ay0, max(ax1, bx1), ay1]
    if ax0 == ax1 == bx0 == bx1 and joins(ay0, ay1, by0, by1):
        return [ax0, min(ay0, by0), ax1, max(ay1, by1)]
    return None


def _index_keys(kind: str, style: tuple, box) -> list:
    """Keys under which an entry can be found by a later one joining it.

    Thin entries only ever join end to end, so they are filed by their two
    ends: a later piece finds its neighbour in one lookup instead of testing
    every piece on the same line. Fills are also filed by row and column,
    where thicker ones may join overlapping.
    """
    x0, y0, x1, y1 = box
    if kind == "rule":
        if y0 == y1:
            return [(style, "h", y0, "end", x1), (style, "h", y0, "start", x0)]
        return [(style, "v", x0, "end", y1), (style, "v", x0, "start", y0)]
    return [
        (style, "row", y0, y1), (style, "col", x0, x1),
        (style, "row", y0, y1, "end", x1), (style, "row", y0, y1, "start", x0),
        (style, "col", x0, x1, "end", y1), (style, "col", x0, x1, "start", y0),
    ]


def _query_keys(kind: str, style: tuple, box) -> list:
    """Keys under which an entry looks for earlier ones it could join."""
    x0, y0, x1, y1 = box
    if kind == "rule":
        if y0 == y1:
            return [(style, "h", y0, "end", x0), (style, "h", y0, "start", x1)]
        return [(style, "v", x0, "end", y0), (style, "v", x0, "start", y1)]
    if _is_thin(kind, box):
        return [
            (style, "row", y0, y1, "end", x0), (style, "row", y0, y1, "start", x1),
            (style, "col", x0, x1, "end", y0), (style, "col", x0, x1, "start", y1),
        ]
    return [(style, "row", y0, y1), (style, "col", x0, x1)]


def _grid_cells(footprint, limits=None):
    """The grid cells a footprint touches, within `limits` when given.

    Only what lies on the page can be seen, so cells beyond it are never
    visited: an off-page fill a hundred thousand points wide would otherwise
    cost millions of cells. Two entries can only be told apart by what they
    paint on the page, where both are still indexed.
    """
    x0, y0, x1, y1 = footprint
    gx0, gy0 = int(x0 // _DRAW_GRID), int(y0 // _DRAW_GRID)
    gx1, gy1 = int(x1 // _DRAW_GRID), int(y1 // _DRAW_GRID)
    if limits is not None:
        lx0, ly0, lx1, ly1 = limits
        gx0, gy0 = max(gx0, lx0), max(gy0, ly0)
        gx1, gy1 = min(gx1, lx1), min(gy1, ly1)
    for cx in range(gx0, gx1 + 1):
        for cy in range(gy0, gy1 + 1):
            yield cx, cy


def _compact_once(entries: list, bounds=None) -> list:
    """One pass of `_compact_drawings`; see there."""
    out: list = []          # kept entries, None once superseded
    styles: list = []
    prints: list = []
    grid: dict = {}
    keys: dict = {}
    exact: dict = {}
    limits = None
    if bounds is not None:
        # One cell of margin: a stroke straddling the edge is still indexed.
        limits = (int(bounds[0] // _DRAW_GRID) - 1, int(bounds[1] // _DRAW_GRID) - 1,
                  int(bounds[2] // _DRAW_GRID) + 1, int(bounds[3] // _DRAW_GRID) + 1)

    def index_entry(i: int) -> None:
        for cell in _grid_cells(prints[i], limits):
            grid.setdefault(cell, []).append(i)
        kind = styles[i][0]
        if kind in ("fill", "rule"):
            box = out[i]["r"]
            for key in _index_keys(kind, styles[i], box):
                keys.setdefault(key, []).append(i)
            exact[(styles[i], tuple(box))] = i

    def painted_after(footprint, after: int, style=None) -> bool:
        """Is anything drawn after index `after` overlapping `footprint`?

        Entries in `style` are ignored: they paint the same colour, so the
        order between them and the one being moved cannot show. Touching
        edges count as overlap, to keep anti-aliased borders untouched.
        """
        seen = set()
        for cell in _grid_cells(footprint, limits):
            for j in grid.get(cell, ()):
                if j <= after or j in seen or out[j] is None:
                    continue
                seen.add(j)
                if style is not None and styles[j] == style:
                    continue
                o = prints[j]
                if (o[0] <= footprint[2] and footprint[0] <= o[2]
                        and o[1] <= footprint[3] and footprint[1] <= o[3]):
                    return True
        return False

    for entry in entries:
        kind = _drawing_kind(entry)
        box = list(entry["r"])
        style = (kind, entry.get("f"), entry.get("c"), entry.get("w"))

        if kind in ("fill", "rule"):
            if _paints_nothing(kind, box):
                continue
            footprint = _drawing_footprint(entry)

            # White painted on blank paper: the page is already that colour,
            # and text and images are drawn after every rectangle, so it
            # cannot hide anything either.
            if (kind == "fill" and entry["f"] == _WHITE
                    and not painted_after(footprint, -1)):
                continue

            # An identical entry drawn earlier is repainted by this one, so
            # one copy goes: this one if nothing came in between, otherwise
            # the earlier one, since this later copy is what ends up on top.
            # Not for thin entries, whose second coat shows (see _is_thin).
            previous = exact.get((style, tuple(box)))
            if (previous is not None and out[previous] is not None
                    and not _is_thin(kind, box)):
                if not painted_after(footprint, previous, style):
                    continue
                out[previous] = None

            # Grow an earlier entry of the same style instead of adding this
            # one, but only if moving this entry's paint down to that earlier
            # position cannot change what is visible: nothing of another
            # style drawn in between may overlap it. Candidates are tried
            # from the most recent; once one is blocked, every older one is.
            merged = False
            candidates = sorted(
                {i for key in _query_keys(kind, style, box)
                 for i in keys.get(key, ()) if out[i] is not None},
                reverse=True,
            )
            for i in candidates:
                union = _union_box(kind, out[i]["r"], box)
                if union is None:
                    continue
                if painted_after(footprint, i, style):
                    break
                exact.pop((style, tuple(out[i]["r"])), None)
                out[i]["r"] = union
                prints[i] = _drawing_footprint(out[i])
                index_entry(i)
                merged = True
                break
            if merged:
                continue
        else:
            footprint = _drawing_footprint(entry)

        kept = dict(entry)
        kept["r"] = box
        out.append(kept)
        styles.append(style)
        prints.append(footprint)
        index_entry(len(out) - 1)

    return [entry for entry in out if entry is not None]


def _compact_drawings(entries: list, bounds=None, precision: int = 1) -> list:
    """Shorten a page's drawing list without changing what it paints.

    On a spreadsheet export the drawing list is most of the archive: Excel
    draws every cell border as its own thin rectangle and LibreOffice every
    cell edge as its own stroke, so one table line arrives as a dozen
    abutting pieces. On two real situation sheets the drawings were 75 % of
    the archive for the Excel export and 52 % for the LibreOffice one.

    Three reductions, then a reordering:

    * pieces of one style that tile a longer rectangle or line are joined
      -- thin ones only end to end, see _is_thin;
    * a fill duplicated later supersedes the earlier copy;
    * white fills on blank paper, and fills or rules too small to put any
      ink down after rounding, are dropped;
    * runs of one colour are sorted by position, and four rules closing a
      rectangle become that rectangle, see _order_colour_runs.

    Draw order is what makes this delicate: entries paint over one another,
    so an entry may only be folded into an earlier one when nothing of a
    different style is painted in between over the part that moves. The
    check is conservative -- touching counts as overlapping -- and the
    result is still an ordinary list of "rect" entries any decoder reads.
    What may change is the anti-aliasing of the seam where two pieces met:
    joined, they no longer show the faint line the renderer leaves between
    abutting shapes.
    """
    current = entries
    for _ in range(_COMPACT_PASSES):
        compacted = _compact_once(current, bounds)
        if len(compacted) == len(current):
            break
        current = compacted
    else:
        compacted = current
    return _order_colour_runs(compacted, _frame_tolerance(precision))


def _drawing_sort_key(entry: dict) -> tuple:
    """Rules by width, then direction, then position; everything else by position."""
    x0, y0, x1, y1 = entry["r"]
    if "c" in entry and "f" not in entry:
        width = entry.get("w", 0)
        if y0 == y1:
            return (0, width, 0, y0, x0, x1)
        if x0 == x1:
            return (0, width, 1, x0, y0, y1)
        return (1, width, 2, y0, x0, y1, x1)
    return (2, 0, 2, y0, x0, y1, x1)


def _paint_colour(entry: dict):
    """The one colour an entry paints, or None when it paints two."""
    if entry.get("t") != "rect":
        return None
    if "f" in entry and "c" not in entry:
        return entry["f"]
    if "c" in entry and "f" not in entry:
        return entry["c"]
    return None


def _order_colour_runs(entries: list, frame_tolerance: float) -> list:
    """Order each run of consecutive single-colour entries by position.

    Paint of a single colour composites the same in any order -- coverage a
    then b gives 1 - (1 - a)(1 - b) either way -- whatever shape or stroke
    width lays it down, so entries can be reordered freely within a run that
    nothing of another colour interrupts. Sorted, consecutive entries repeat
    their row or column coordinate and step along the other, which LZMA codes
    far more cheaply than the producer's cell-by-cell order: 4 to 7 % off the
    whole archive on the two spreadsheet exports. Runs used to stop at a
    change of stroke width; on the LibreOffice export, whose borders alternate
    between three widths of black, letting them run on saved another 2 %.

    Within a run, four rules that close a rectangle become that rectangle,
    see _frames_from_rules.

    An entry with both a fill and a stroke paints two colours, and two such
    entries that overlap do not commute -- the second one's fill covers the
    first one's border. Those keep their place and break the run.
    """
    out: list = []
    run: list = []
    colour = None

    def flush() -> None:
        out.extend(sorted(_frames_from_rules(run, frame_tolerance),
                          key=_drawing_sort_key))

    for entry in entries:
        paint = _paint_colour(entry)
        if paint is None:
            flush()
            run = []
            colour = None
            out.append(entry)
            continue
        if run and paint != colour:
            flush()
            run = []
        run.append(entry)
        colour = paint
    flush()
    return out


def _frame_tolerance(precision: int) -> float:
    """How far four rules may miss a rectangle's outline and still become it.

    One rounding step at a tenth of a point. At whole points the rules' ends
    are rounded but their thickness is not, so an end meets the outline only
    to within half a stroke: 0.2 pt for the usual table border.
    """
    return 0.11 if precision >= 1 else 0.21


def _frames_from_rules(entries: list, tolerance: float) -> list:
    """Replace each set of four rules closing a rectangle with that rectangle.

    LibreOffice draws a cell's inner frame as four separate strokes; a stroked
    rectangle draws the same four sides, joined at the corners, in one entry
    with four numbers instead of four entries with twelve. On the LibreOffice
    situation sheet, 219 frames took 876 rules and 9 % off the archive.

    The sides keep their exact positions. Only the corners can differ: a side
    that stopped short of the outline or ran past it by up to `tolerance`
    now ends exactly on it, and the corner square is painted once where the
    two crossing rules painted it twice. Measured on the four pages of that
    sheet, the drawings render with a mean difference to the original under
    a thousandth of a grey level, and the two versions cannot be told apart
    at 1000 %.

    Every entry passed in must paint the same colour, so that the frame can
    stand anywhere in the list; the four rules must share a stroke width.

    Rules are indexed by where they start, so each lookup below inspects a
    handful of buckets of a tenth of a point, however many rules the page
    holds.
    """
    horizontal: dict = {}   # (width, y, x0) buckets -> indices
    vertical: dict = {}     # (width, x, y0) buckets -> indices
    tops: list = []
    for index, entry in enumerate(entries):
        if _drawing_kind(entry) != "rule":
            continue
        x0, y0, x1, y1 = entry["r"]
        width = entry.get("w")
        if y0 == y1 and x0 < x1:
            horizontal.setdefault((width, round(y0 * 10), round(x0 * 10)), []).append(index)
            tops.append(index)
        elif x0 == x1 and y0 < y1:
            vertical.setdefault((width, round(x0 * 10), round(y0 * 10)), []).append(index)
    if not tops or not vertical:
        return entries

    used: set = set()
    frames: dict = {}
    reach = int(tolerance * 10) + 1   # buckets to search either side
    span = range(-reach, reach + 1)

    def near(table: dict, width, along: float, start: float):
        """Unused rules of `width` in the buckets around (along, start)."""
        a, s = round(along * 10), round(start * 10)
        for da in span:
            for ds in span:
                for index in table.get((width, a + da, s + ds), ()):
                    if index not in used:
                        yield index

    tops.sort(key=lambda i: (entries[i]["r"][1], entries[i]["r"][0]))
    for top_index in tops:
        if top_index in used:
            continue
        a, top, b, _ = entries[top_index]["r"]
        width = entries[top_index].get("w")
        half = (width or 0) / 2
        best = None
        for left in near(vertical, width, a + half, top - half):
            lx, ly0, _, ly1 = entries[left]["r"]
            bottom = ly1 - half
            if (abs(lx - (a + half)) > tolerance or abs(ly0 - (top - half)) > tolerance
                    or bottom - top <= (width or 0)):
                continue
            for right in near(vertical, width, b - half, top - half):
                if right == left:
                    continue
                rx, ry0, _, ry1 = entries[right]["r"]
                if (abs(rx - (b - half)) > tolerance or abs(ry0 - (top - half)) > tolerance
                        or rx - lx <= (width or 0)):
                    continue
                for bottom_index in near(horizontal, width, bottom, a):
                    if bottom_index == top_index:
                        continue
                    ba, by, bb, _ = entries[bottom_index]["r"]
                    if (abs(ba - a) > tolerance or abs(bb - b) > tolerance
                            or abs(ly1 - (by + half)) > tolerance
                            or abs(ry1 - (by + half)) > tolerance
                            or by - top <= (width or 0)):
                        continue
                    if best is None or by < best[0]:
                        best = (by, bottom_index, left, right)
        if best is None:
            continue
        bottom, bottom_index, left, right = best
        frame = {"t": "rect",
                 "r": [entries[left]["r"][0], top, entries[right]["r"][0], bottom],
                 "c": entries[top_index]["c"]}
        if width is not None:
            frame["w"] = width
        used.update((top_index, bottom_index, left, right))
        frames[top_index] = frame

    if not frames:
        return entries
    return [frames.get(index, entry) for index, entry in enumerate(entries)
            if index not in used or index in frames]


def _merge_boxes(boxes: list, pad: float = 8.0) -> list:
    """Merge boxes that touch or nearly touch, repeating until stable."""
    merged: list = []
    for box in boxes:
        placed = False
        for index, existing in enumerate(merged):
            if (existing + (-pad, -pad, pad, pad)).intersects(box):
                merged[index] = existing | box
                placed = True
                break
        if not placed:
            merged.append(fitz.Rect(box))

    for _ in range(8):
        collapsed: list = []
        for box in merged:
            placed = False
            for index, existing in enumerate(collapsed):
                if (existing + (-pad, -pad, pad, pad)).intersects(box):
                    collapsed[index] = existing | box
                    placed = True
                    break
            if not placed:
                collapsed.append(fitz.Rect(box))
        if len(collapsed) == len(merged):
            break
        merged = collapsed
    return merged


def _fonts_without_unicode(page) -> set:
    """Return the names of fonts on this page whose text cannot be decoded.

    The decisive combination is a CID font with Identity encoding and no
    ToUnicode table. Identity means the bytes in the content stream are glyph
    identifiers, not character codes; ToUnicode is the only thing that maps
    them back to text, and without it every reader -- PyMuPDF included --
    can only guess. A simple font with WinAnsi or MacRoman encoding decodes
    on its own and is never on this list.

    Reading the font dictionary is more reliable than inspecting the text:
    a glyph id that happens to coincide with a printable ASCII code looks
    perfectly readable and would slip past any text-based test. The names
    are matched against the span's reported font, which PyMuPDF derives from
    the same BaseFont entry.
    """
    doc = page.parent
    names = set()
    for font in page.get_fonts(full=True):
        xref, _ext, subtype, basefont, _refname, encoding = font[:6]
        if subtype != "Type0" or not str(encoding).startswith("Identity"):
            continue
        try:
            has_table = doc.xref_get_key(xref, "ToUnicode")[0] != "null"
        except Exception:  # noqa: BLE001
            has_table = True   # unsure: do not rasterise on a guess
        if has_table:
            continue
        names.add(_font_key(basefont))
        raw = basefont.split("+")[-1].replace(" ", "").replace("-", "").lower()
        if raw:
            names.add(raw)
    return names


def _font_key(name: str) -> str:
    """Normalise a font name for comparison across PyMuPDF's two spellings.

    The font dictionary says "DejaVu Sans Book"; the span says "DejaVuSans".
    Subset prefixes ("ABCDEF+"), spaces, hyphens and the style words that
    PyMuPDF drops from span names are all stripped so both sides meet.
    """
    name = name.split("+")[-1]
    name = name.replace(" ", "").replace("-", "").replace("_", "")
    for style in ("BoldItalic", "BoldOblique", "Italic", "Oblique", "Bold",
                  "Regular", "Book", "Roman", "Medium", "Light"):
        if name.endswith(style):
            name = name[: -len(style)]
    return name.lower()


def _font_matches(span_font: str, bad_fonts: set) -> bool:
    """Match a span's font against the dictionary names, tolerating truncation.

    PDF producers cap BaseFont at 31 characters, so "DejaVu Sans Condensed
    Bold Oblique" is stored as "DejaVu Sans Condensed Bold Obli" while the
    span reports the full name. Neither normalised form is a prefix of the
    other once style words are stripped from one side only, so the raw
    lower-cased forms are compared as prefixes in both directions as well.
    """
    key = _font_key(span_font)
    if key in bad_fonts:
        return True
    raw = span_font.split("+")[-1].replace(" ", "").replace("-", "").lower()
    for bad in bad_fonts:
        if len(bad) >= 6 and (raw.startswith(bad) or bad.startswith(raw)
                              or key.startswith(bad) or bad.startswith(key)):
            return True
    return False


def _span_is_lost(span, bad_fonts: set) -> bool:
    """Decide whether a span's text was lost to a missing ToUnicode table."""
    font = span.get("font", "")
    if font and bad_fonts and _font_matches(font, bad_fonts):
        return True
    # Fallback for producers PyMuPDF reports oddly: the text itself is
    # mostly replacement or control characters.
    text = "".join(c["c"] for c in span.get("chars", [])) if "chars" in span \
        else span.get("text", "")
    return _is_unreadable(text)


def _unreadable_text_regions(page) -> list:
    """Return the boxes of text whose font carries no ToUnicode table.

    PyMuPDF reports every glyph it cannot map as U+FFFD. The characters are
    gone for good -- there is nothing to decode -- but the glyphs are still
    drawn on the page, so rasterising their box reproduces exactly what the
    operator typed. This is what keeps a callsign entered in Okular from
    coming out as "tititititi".

    These are not merged with the artwork regions and not subject to the
    page-fraction ceiling: a form with forty annotated fields is still a form,
    and every one of those fields has to survive.
    """
    bad_fonts = _fonts_without_unicode(page)
    boxes = []
    for block in page.get_text("rawdict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if not _span_is_lost(span, bad_fonts):
                    continue
                visible = [c for c in span["chars"] if c["c"].strip()]
                if not visible:
                    continue
                box = fitz.Rect(
                    min(c["bbox"][0] for c in visible),
                    min(c["bbox"][1] for c in visible),
                    max(c["bbox"][2] for c in visible),
                    max(c["bbox"][3] for c in visible),
                ) & page.rect
                if box.is_empty or box.width <= 0 or box.height <= 0:
                    continue
                # A little slack so the raster does not clip ascenders and
                # descenders that the glyph box reports tightly.
                boxes.append(box + (-1.5, -1.5, 1.5, 1.5))

    return _merge_boxes(boxes, pad=3.0) if boxes else []


def _glyph_neighbours(a, b) -> bool:
    """Report whether two small boxes sit side by side on one row or column.

    Neighbours share most of their extent across the run's axis and are
    separated along it by no more than a word gap. The overlap test is taken
    against the smaller box so that a short word next to a tall rotated one
    still counts.
    """
    v_overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
    h_gap = max(a.x0, b.x0) - min(a.x1, b.x1)
    if v_overlap >= 0.5 * min(a.height, b.height) and h_gap <= _GLYPH_RUN_MAX_GAP:
        return True
    h_overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
    v_gap = max(a.y0, b.y0) - min(a.y1, b.y1)
    return h_overlap >= 0.5 * min(a.width, b.width) and v_gap <= _GLYPH_RUN_MAX_GAP


def _glyph_regions(small: list, curve_counts: list) -> list:
    """Recover outlined text from boxes too small to pass as artwork alone.

    Two shapes qualify. A run of at least _GLYPH_RUN_MIN_COUNT boxes aligned
    on a row or a column -- the axis labels of a chart, one path per word --
    becomes a single region spanning the run. A lone box qualifies when its
    long side reaches the artwork floor and it is made of enough curves to be
    glyph outlines rather than a decorative stroke: a rotated axis title.

    Everything else stays dropped, exactly as before; the floor exists so
    that a page of tiny ornaments does not turn into hundreds of tiles.
    """
    count = len(small)
    parent = list(range(count))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(count):
        for j in range(i + 1, count):
            if _glyph_neighbours(small[i], small[j]):
                parent[find(i)] = find(j)

    clusters: dict[int, list[int]] = {}
    for i in range(count):
        clusters.setdefault(find(i), []).append(i)

    regions = []
    for members in clusters.values():
        union = fitz.Rect(small[members[0]])
        for index in members[1:]:
            union |= small[index]
        long_side = max(union.width, union.height)
        short_side = min(union.width, union.height)
        if long_side < _ARTWORK_MIN_SIDE or short_side < _GLYPH_MIN_SHORT_SIDE:
            continue
        curves = sum(curve_counts[index] for index in members)
        if len(members) >= _GLYPH_RUN_MIN_COUNT or curves >= _GLYPH_MIN_CURVES:
            # A point of slack: the path rectangle is the exact ink extent
            # and the raster must not shave the outermost pixels off.
            regions.append(union + (-1.0, -1.0, 1.0, 1.0))
    return regions


def _artwork_regions(page) -> list:
    """Return the page areas that must be rasterised to survive at all.

    Kept deliberately conservative: a region has to be big enough to be a
    figure rather than a stray flourish, and if the total would cover most of
    the page the document is really a picture and the whole page-image mode is
    the better answer, so nothing is rasterised here.
    """
    boxes = []
    paths = []
    for path in page.get_drawings():
        items = path.get("items", [])
        if not _path_is_artwork(items):
            continue
        rect = fitz.Rect(path["rect"]) & page.rect
        if rect.is_empty or rect.width <= 0 or rect.height <= 0:
            continue
        boxes.append(rect)
        paths.append((rect, sum(1 for item in items if item[0] == "c")))

    if not boxes:
        return []

    regions = []
    small = []
    for region in _merge_boxes(boxes):
        if region.width >= _ARTWORK_MIN_SIDE and region.height >= _ARTWORK_MIN_SIDE:
            regions.append(region)
        else:
            small.append(region)

    if small:
        curve_counts = [
            sum(curves for rect, curves in paths if rect in region)
            for region in small
        ]
        regions.extend(_glyph_regions(small, curve_counts))

    if not regions:
        return []

    # A chart's shadows and legend swatches are placed images that overlap the
    # vector artwork. Left outside the region they are extracted separately,
    # and any of them carrying a soft mask lands as a black rectangle over the
    # figure. Absorbing them into the region renders them in place instead.
    for info in page.get_image_info(xrefs=True):
        box = fitz.Rect(info.get("bbox", (0, 0, 0, 0))) & page.rect
        if box.is_empty or box.get_area() <= 0:
            continue
        for index, region in enumerate(regions):
            overlap = box & region
            if not overlap.is_empty and overlap.get_area() >= 0.25 * box.get_area():
                regions[index] = region | box
                break

    regions = _merge_boxes(regions)

    page_area = abs(page.rect.width * page.rect.height) or 1.0
    if sum(r.get_area() for r in regions) / page_area > _ARTWORK_MAX_PAGE_FRACTION:
        return []
    return regions


def _image_bytes_with_mask(doc, xref: int, raw: dict) -> bytes | None:
    """Return the image's pixels with its soft mask applied.

    extract_image returns the base image only. When the object carries a soft
    mask -- which is how a PDF stores a transparent PNG -- the base image is
    black wherever the mask makes it invisible, so a drop shadow becomes a
    black frame and a doughnut chart becomes a black square. The mask has to
    be fetched separately and combined before the pixels mean anything.
    """
    smask = raw.get("smask", 0)
    if not smask:
        return raw["image"]

    try:
        base = fitz.Pixmap(doc, xref)
        if base.alpha:
            # The pixmap may already carry an empty alpha channel, and MuPDF
            # refuses to attach a mask to a colour pixmap that has one.
            base = fitz.Pixmap(base, 0)
        mask = fitz.Pixmap(doc, smask)
        combined = fitz.Pixmap(base, mask)
        return combined.tobytes("png")
    except Exception:  # noqa: BLE001
        log.debug("Could not apply soft mask %d to image %d", smask, xref,
                  exc_info=True)
        return raw["image"]


# A gap wider than this fraction of the font size means the producer moved the
# text cursor rather than typed a space: a table cell boundary, or a justified
# column. 0.45 sits well clear of an ordinary inter-word space (~0.25) and well
# below the gaps real tables use (~0.75 and up).
_SPAN_SPLIT_RATIO = 0.45


def _split_span_runs(span) -> list:
    """Break a span wherever its glyphs jump horizontally.

    A PDF may lay out a whole table row as one text object, positioning each
    cell with an explicit cursor move and no glyph in between. The span then
    reads "128847 119323" with a single space, while the glyphs actually sit
    eighty points apart. Rebuilt from the span alone, the values collapse
    against the left edge of the row and every column after the first is
    wrong. Splitting on the real glyph positions restores the columns.
    """
    chars = span.get("chars") or []
    if not chars:
        return []

    threshold = max(1.0, _SPAN_SPLIT_RATIO * span.get("size", 8))

    runs = []
    current = []
    previous_end = None

    for char in chars:
        box = char["bbox"]
        if previous_end is not None and (box[0] - previous_end) > threshold:
            runs.append(current)
            current = []
        current.append(char)
        previous_end = box[2]

    runs.append(current)

    out = []
    for run in runs:
        if not any(char["c"].strip() for char in run):
            continue
        # The padding spaces are kept deliberately. A reader that concatenates
        # a line -- which is what the original implementation does -- then
        # still gets "36620 91,7" instead of "3662091,7"; only the column
        # positions are lost, which is no worse than before.
        text = "".join(char["c"] for char in run)
        visible = [char for char in run if char["c"].strip()]
        x0 = min(char["bbox"][0] for char in visible)
        x1 = max(char["bbox"][2] for char in visible)
        y0 = min(char["bbox"][1] for char in visible)
        y1 = max(char["bbox"][3] for char in visible)
        out.append((text, (x0, y0, x1, y1)))
    return out


# Span keys that make two spans render differently. Spans equal on all of them
# render as one run of text when nothing separates them.
_SPAN_STYLE_KEYS = ("f", "fa", "sz", "c")


# See _trusts_guessed_spaces. On the 51 documents of the 2.5.0 test bench,
# pages set by LaTeX carry at most 0.03 typed space per guessed one. Every
# other page with guessed spaces carries 0.18 or more -- htmldoc, slides from
# Mac OS X --, the LibreOffice sheet 18 or more once its fixed-pitch letters
# are read by their steps (see _settle_fixed_pitch), pages typed by Word a
# hundred or more.
_TYPED_PER_GUESSED_SPACE = 0.1


def _trusts_guessed_spaces(raw: dict) -> bool:
    """Whether the spaces MuPDF guessed on this page are word boundaries.

    MuPDF supplies a space wherever the next glyph sits far enough from the
    last, and marks it as guessed ("synthetic"). On a page whose producer
    placed every word by moving the cursor -- LaTeX does, between every two
    words -- the guessed spaces are the only word boundaries there are. On a
    page whose producer typed its spaces they are mostly noise: a
    LibreOffice sheet printed to PDF positions every glyph on its own, and
    MuPDF finds "spaces" inside "sinistrées". Guessed spaces are trusted
    where typed ones are rare against them.
    """
    typed = guessed = 0
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for char in span.get("chars") or []:
                    if char["c"] == " ":
                        if char.get("synthetic"):
                            guessed += 1
                        else:
                            typed += 1
    return guessed > 0 and typed <= _TYPED_PER_GUESSED_SPACE * guessed


def _is_horizontal(line: dict) -> bool:
    """Whether a rawdict line runs left to right along the page."""
    direction = line.get("dir", (1, 0))
    return line.get("wmode", 0) == 0 and direction[0] > 0.999


# See _fixed_pitch_fonts. In the fixed-pitch cells of the LibreOffice
# situation sheet, 96 to 100 % of the steps between two letters come within
# 5 % of the pitch, taken by 11 to 30 different letters per font and page,
# whose widths span 0.47 of the pitch or more. The nearest anything else on
# the 51-document bench came was a few capitals of similar width in a page
# of geometry, "PQ" and "AB": six letters at most, within a fifth of the
# step of one another.
_PITCH_MIN_PAIRS = 20
_PITCH_TOLERANCE = 0.05
_PITCH_SHARE = 0.8
_PITCH_MIN_LETTERS = 8
_PITCH_WIDTH_RANGE = 1 / 3


def _fixed_pitch_fonts(raw: dict) -> dict:
    """Fonts this page advances by a fixed step, whatever each letter's width.

    The LibreOffice situation sheet was printed that way. Its cells are laid
    out in the 0.6 em pitch of a monospaced font -- the Excel copy of the
    same sheet sets them in Courier New -- but drawn in DejaVu Sans, whose
    "i" is less than half the step wide and whose "m" is wider than it: one
    TJ per line moves each glyph on to the next step. Behind every narrow
    letter a gap of up to a third of an em remains, which MuPDF takes for a
    space: "Nbr e de pl aces r est ant es".

    A font qualifies when most steps between two letters come within
    _PITCH_TOLERANCE of one value, while the letters taking that step are
    many and of clearly different widths. Proportional text advances by each
    letter's own width and never meets the first condition; a monospaced
    font, whose letters are as wide as the step, leaves no gap to misread. A
    few capitals of similar width can share a step by chance -- "PQ" and
    "AB" in a page of geometry -- which the letter count rules out.

    Returns the pitch in ems, keyed by font name and size.
    """
    pairs: dict = {}
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            if not _is_horizontal(line):
                continue
            for span in line.get("spans", []):
                size = span.get("size") or 0
                if size <= 0:
                    continue
                glyphs = [char for char in span.get("chars") or [] if not char.get("synthetic")]
                for first, second in zip(glyphs, glyphs[1:]):
                    if not (first["c"].isalpha() and second["c"].isalpha()):
                        continue
                    step = (second["origin"][0] - first["origin"][0]) / size
                    if step > 0:
                        width = (first["bbox"][2] - first["bbox"][0]) / size
                        pairs.setdefault((span.get("font"), size), []).append(
                            (step, width, first["c"]))
    pitches = {}
    for key, found in pairs.items():
        if len(found) < _PITCH_MIN_PAIRS:
            continue
        pitch = statistics.median(step for step, _, _ in found)
        regular = [(width, letter) for step, width, letter in found
                   if abs(step - pitch) <= _PITCH_TOLERANCE * pitch]
        widths = [width for width, _ in regular]
        if (len(regular) >= _PITCH_SHARE * len(found)
                and len({letter for _, letter in regular}) >= _PITCH_MIN_LETTERS
                and max(widths) - min(widths) >= _PITCH_WIDTH_RANGE * pitch):
            pitches[key] = pitch
    return pitches


def _settle_fixed_pitch(raw: dict) -> None:
    """Read the letters of a font set at a fixed pitch by their steps.

    See _fixed_pitch_fonts: there the step settles what MuPDF could only
    guess from the gaps. A glyph takes one step per letter it stands for --
    two or three for a ligature -- and a word space takes one more, typed or
    left blank, as between "demande" and "de" on the situation sheet. So a
    space MuPDF guessed between glyphs a whole step further apart than that
    is kept, as certain as a typed one; between glyphs closer than half a
    step more, it goes, whatever gap their shapes leave.

    Across a single blank step, the words on either side are joined into one
    span, the kept space between them, as a typed space would join them.
    MuPDF hands such a space over in a span of its own, and the words, more
    than _SPAN_SPLIT_RATIO apart, would otherwise travel as separate runs --
    which the original decoder, concatenating the spans of a line, reads as
    "demandede". Two blank steps or more may part two cells, and stay apart.

    A ligature's box is widened by the steps it takes beyond its own glyph.
    Left as it is, the two-step gap after "ﬁ" in "traﬁc" or after "ﬀ" in
    "aﬀectés" reads as a cursor move between columns, and the word is split
    in two (see _split_span_runs).
    """
    pitches = _fixed_pitch_fonts(raw)
    if not pitches:
        return
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            if not _is_horizontal(line):
                continue
            dropped = set()
            joined = set()  # spans to append to the span before them
            last = None     # (span, char) of the last glyph that is not a space
            guessed = []    # (span, char) of the spaces MuPDF supplied since
            for span in line.get("spans", []):
                key = (span.get("font"), span.get("size"))
                pitch = pitches.get(key)
                for char in span.get("chars") or []:
                    if char["c"] == " ":
                        if char.get("synthetic"):
                            guessed.append((span, char))
                        continue
                    if (guessed and last is not None and pitch is not None
                            and key == (last[0].get("font"), last[0].get("size"))):
                        letters = len(_LIGATURES.get(last[1]["c"], last[1]["c"]))
                        steps = (char["origin"][0] - last[1]["origin"][0]) / span["size"] / pitch
                        if 0 < steps < letters + 0.5:
                            dropped.update(id(space) for _, space in guessed)
                        elif steps > 0:
                            for _, space in guessed:
                                space["synthetic"] = False
                            between = {id(owner): owner for owner, _ in guessed}
                            if (steps < letters + 1.5
                                    and id(last[0]) not in between and id(span) not in between
                                    and all(not c["c"].strip() for owner in between.values()
                                            for c in owner.get("chars") or [])
                                    and last[0].get("color") == span.get("color")
                                    and last[0].get("flags") == span.get("flags")):
                                joined.update(between)
                                joined.add(id(span))
                    if pitch is not None and char["c"] in _LIGATURES:
                        x0, y0, x1, y1 = char["bbox"]
                        extra = (len(_LIGATURES[char["c"]]) - 1) * pitch * span["size"]
                        char["bbox"] = (x0, y0, x1 + extra, y1)
                    guessed = []
                    last = (span, char)
            if dropped or joined:
                spans = []
                for span in line.get("spans", []):
                    chars = [char for char in span.get("chars") or [] if id(char) not in dropped]
                    if id(span) in joined and spans:
                        spans[-1]["chars"].extend(chars)
                    elif chars:
                        span["chars"] = chars
                        spans.append(span)
                line["spans"] = spans


def _space_overlooked_after(char: str) -> bool:
    """Whether MuPDF declines to guess a space after `char`, in a script that uses them.

    MuPDF guesses a space only after Basic Latin to Arabic, general
    punctuation, super- and subscripts and currency signs (may_add_space in
    its stext-device.c), so that text written without spaces between words
    -- Chinese, Japanese, Thai -- gets none. The rule leaves out the Latin
    ligatures, the Latin and Greek letters of the extended blocks, and the
    symbols: arrows, mathematical operators, technical and geometric signs,
    the private code points of symbol fonts and the mathematical alphabets.
    """
    if len(char) != 1:
        return False
    code = ord(char)
    return (0xFB00 <= code <= 0xFB06 or 0x1E00 <= code <= 0x1FFF
            or 0x20D0 <= code <= 0x2BFF or 0xE000 <= code <= 0xF8FF
            or 0x1D400 <= code <= 0x1D7FF)


# MuPDF guesses a space where the next glyph starts at least this far past the
# end of the last one, in ems of the next glyph's size (SPACE_DIST in its
# stext-device.c).
_GUESSED_SPACE_EM = 0.15


def _overlooked_space(char: dict, following: dict, size: float) -> dict | None:
    """The space MuPDF would have guessed between two glyphs, or None."""
    if (size <= 0 or not char["c"].strip() or not following["c"].strip()
            or not _space_overlooked_after(char["c"])
            or following["bbox"][0] - char["bbox"][2] < _GUESSED_SPACE_EM * size):
        return None
    return {
        "c": " ",
        "synthetic": True,
        "origin": (char["bbox"][2], char["origin"][1]),
        "bbox": (char["bbox"][2], char["bbox"][1], following["bbox"][0], char["bbox"][3]),
    }


def _add_overlooked_spaces(raw: dict) -> None:
    """Supply the spaces MuPDF declines to guess after ligatures and symbols.

    Only on pages whose word boundaries are MuPDF's guesses (see
    _trusts_guessed_spaces). There a word ending in a ligature runs into the
    next -- "trade offwith" in the codec2 specification -- and so does
    whatever follows a symbol LaTeX sets apart: "x ∈X". The space is guessed
    here as MuPDF would have, at its own distance, after the characters its
    rule leaves out (see _space_overlooked_after): inside the span, or as a
    span of its own where the next glyph starts another, which is how MuPDF
    hands over the spaces it guesses itself.
    """
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            if not _is_horizontal(line):
                continue
            original = line.get("spans", [])
            spans = []
            for index, span in enumerate(original):
                chars = span.get("chars") or []
                size = span.get("size") or 0
                filled = []
                for position, char in enumerate(chars):
                    filled.append(char)
                    if position + 1 < len(chars):
                        space = _overlooked_space(char, chars[position + 1], size)
                        if space is not None:
                            filled.append(space)
                span["chars"] = filled
                spans.append(span)
                following = original[index + 1] if index + 1 < len(original) else None
                if chars and following is not None and following.get("chars"):
                    space = _overlooked_space(chars[-1], following["chars"][0],
                                              following.get("size") or 0)
                    if space is not None:
                        spans.append({"font": following.get("font"),
                                      "size": following.get("size"),
                                      "color": following.get("color"), "chars": [space]})
            line["spans"] = spans


def _restore_space(previous: dict, text: str, box, size: float) -> None:
    """Put back the space MuPDF found between `previous` and the word `text`.

    Only where the decoders set the two in one run -- no further apart than
    _SPAN_SPLIT_RATIO of the font size, see _draw_line -- since that is where
    they would otherwise run together. Further apart, each is placed on its
    own and a trailing space would only widen the first. The space goes on
    the word before: the next one's box starts at its first glyph, and a
    leading space would push it right.
    """
    gap = box[0] - previous["b"][2]
    if (gap <= max(1.0, _SPAN_SPLIT_RATIO * size)
            and not previous["t"][-1:].isspace() and not text[:1].isspace()):
        previous["t"] += " "


def _merge_adjacent_spans(spans: list, line_box) -> list:
    """Join consecutive spans of one style that the reader sets as one run.

    LibreOffice draws the "ti" and "tt" ligatures from a separate font, so a
    word such as "Identité" reaches the extraction as three spans -- "Iden",
    "ti", "té" -- each with its own box. Both decoders put them back on the
    line side by side in one HTML box: the original concatenates every span
    of a line, and this one groups spans no further apart than
    _SPAN_SPLIT_RATIO of the font size (see _draw_line). Joined by that same
    rule they produce the same HTML less two tag boundaries, and the same
    page, measured pixel for pixel through both decoders. On the
    LibreOffice situation sheet the boxes of these fragments were a tenth of
    the archive.
    """
    merged: list = []
    previous_end = None
    for span in spans:
        box = span.get("b", line_box)
        size = span.get("sz", 8)
        # The reader measures the gap to the next span from the right edge of
        # the last one, so a fragment is only joined when it ends the run:
        # the joined box then ends where the last fragment did.
        if (merged and previous_end is not None
                and box[0] - previous_end <= max(1.0, _SPAN_SPLIT_RATIO * size)
                and box[2] >= merged[-1].get("b", line_box)[2]
                and all(merged[-1].get(key) == span.get(key)
                        for key in _SPAN_STYLE_KEYS)):
            last = merged[-1]
            last_box = last.get("b", line_box)
            last["t"] += span["t"]
            last["b"] = [min(last_box[0], box[0]), min(last_box[1], box[1]),
                         max(last_box[2], box[2]), max(last_box[3], box[3])]
        else:
            merged.append(dict(span))
        previous_end = box[2]
    return merged


# The base-14 faces behind the decoders' CSS families, by family and style key.
# MuPDF lays out "Helvetica" with Nimbus Sans, which is what these load, so
# Font.text_length gives the width the HTML layout will use: checked equal to
# the laid-out width on 600 lines of the four test documents.
_BASE14_FACES = {
    ("s", "R"): "helv", ("s", "B"): "hebo", ("s", "I"): "heit", ("s", "BI"): "hebi",
    ("t", "R"): "tiro", ("t", "B"): "tibo", ("t", "I"): "tiit", ("t", "BI"): "tibi",
    ("m", "R"): "cour", ("m", "B"): "cobo", ("m", "I"): "coit", ("m", "BI"): "cobi",
}
# Slack kept between the text and the box edge before the edge is declared
# irrelevant, in points.
_FIT_MARGIN = 0.01


def _text_width(faces: dict, key: tuple, text: str, size: float) -> float | None:
    """Width of `text` set in the base-14 face for `key`, or None if it lacks a glyph.

    Sums cached per-character advances: identical to Font.text_length, which
    costs a quarter of a millisecond per call, against microseconds here.
    """
    entry = faces.get(key)
    if entry is None:
        entry = faces[key] = (fitz.Font(_BASE14_FACES[key]), {})
    face, advances = entry
    total = 0.0
    for char in text:
        advance = advances.get(char)
        if advance is None:
            code = ord(char)
            advance = advances[char] = face.glyph_advance(code) if face.has_glyph(code) else -1.0
        if advance < 0:
            return None
        total += advance
    return total * size


def _open_line_box(line: dict, page_w: float, page_h: float, precision: int,
                   faces: dict) -> list:
    """The box to store for a line, opened to the page corner when it can be.

    Both decoders lay a line out in an HTML box built from its stored box:
    left and top place the text, and right and bottom only decide whether it
    fits -- when it does not, it is scaled down to fit. A line whose text
    already fits at full size is therefore drawn identically in any larger
    box, and the page's own bottom-right corner is the largest: one value
    repeated on every such line, which LZMA codes for almost nothing, instead
    of two fresh coordinates per line. On the situation sheets the right and
    bottom edges were a sixth of the archive.

    Only lines of a single span qualify, set in a face that has every glyph
    (a substituted font would lay out at another width), whose text fits the
    stored box at full size with _FIT_MARGIN to spare. Lines that are scaled
    down keep their exact box, since there the box sets the scale.
    """
    box = line["b"]
    spans = line.get("s", [])
    if len(spans) != 1 or "b" in spans[0]:
        return box
    span = spans[0]
    key = (span.get("fa", "s"), span.get("f", "R"))
    text = span.get("t", "")
    if key not in _BASE14_FACES or not text:
        return box
    needed = _text_width(faces, key, text, span.get("sz", 8))
    if needed is None:
        return box
    x0, y0, x1, y1 = box
    right = _quantize(page_w, precision)
    bottom = _quantize(page_h, precision)
    # Both decoders widen the box by half a point on each side. The new box
    # must also contain the old one: the original decoder clips each line to
    # its box, so a smaller one could cut off a descender.
    if (needed > x1 - x0 + 1.0 - _FIT_MARGIN
            or right < x1 or bottom < y1):
        return box
    return [x0, y0, right, bottom]


def _covered(box, regions) -> bool:
    """Report whether a box lies inside an already-rasterised region."""
    if not regions:
        return False
    rect = fitz.Rect(box)
    if rect.is_empty:
        return False
    for region in regions:
        overlap = rect & region
        if not overlap.is_empty and overlap.get_area() >= 0.7 * rect.get_area():
            return True
    return False


def _extract_page(page, page_idx: int, images_data: dict,
                  precision: int = 1, artwork: list | None = None,
                  bad_fonts: set | None = None, faces: dict | None = None) -> dict:
    """Build the manifest entry for one page.

    Content falling inside a rasterised artwork region is skipped: it is
    already in the picture, and drawing it a second time as text or as a
    rectangle produces a sharp copy sitting on top of a blurred one.
    """
    artwork = artwork or []
    bad_fonts = bad_fonts if bad_fonts is not None else _fonts_without_unicode(page)
    page_data: dict = {"pn": page_idx + 1, "tb": [], "ir": [], "dr": [], "ck": []}

    # --- text -------------------------------------------------------------
    # rawdict rather than dict: only per-character boxes reveal where a span's
    # glyphs actually sit, which is what table columns depend on.
    #
    # Every line of the page goes into one block. Neither decoder gives blocks
    # any meaning -- both draw line by line -- and each block costs its own
    # wrapper in the manifest: 1 to 2 % of the archive on the test documents.
    out_block: dict = {"l": []}
    # Base-14 faces and their glyph advances, shared by every page of a
    # document when the caller passes one dict along.
    faces = faces if faces is not None else {}
    raw = page.get_text("rawdict")
    # The spaces MuPDF guessed, corrected where they can be checked: settled
    # by the pitch where letters advance by a fixed step, then -- on a page
    # whose word boundaries are all guesses -- supplied where MuPDF's own
    # rule declines to guess one.
    _settle_fixed_pitch(raw)
    trust_guessed = _trusts_guessed_spaces(raw)
    if trust_guessed:
        _add_overlooked_spaces(raw)
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue

        for line in block.get("lines", []):
            if _covered(line["bbox"], artwork):
                continue
            out_line: dict = {"b": _quantize_box(line["bbox"], precision), "s": []}
            # Set by a span of spaces only, cleared by the next word.
            space_between = False
            for span in line.get("spans", []):
                chars = span.get("chars") or []
                if chars and not any(char["c"].strip() for char in chars):
                    # A span of spaces only: MuPDF gives a space a span of its
                    # own when it differs in style from its neighbours, or
                    # when it supplied the space itself (see
                    # _trusts_guessed_spaces). Dropped, the words on either
                    # side ran together -- "SystemRequirements" -- in both
                    # decoders and in the original's. It goes back on the
                    # word before, below.
                    if trust_guessed or any(not char.get("synthetic") for char in chars):
                        space_between = True
                    continue
                if _span_is_lost(span, bad_fonts):
                    # Lost to a missing ToUnicode table. The raster made from
                    # this span's box carries the real glyphs; a transcription
                    # here would paint a wrong string on the right picture.
                    continue
                font_key, family = _classify_font(span.get("font", ""))
                size = _quantize(span.get("size", 8), 1)
                colour = span.get("color", 0)

                for raw_text, box in _split_span_runs(span):
                    if _is_unreadable(raw_text):
                        # Lost to a missing ToUnicode table. The raster made
                        # from this span's box carries the real glyphs; a
                        # guessed transcription here would only paint a
                        # wrong string on top of the right picture.
                        continue
                    text = _normalize_text(raw_text)
                    if not text.strip():
                        continue
                    stored_box = _quantize_box(box, precision)
                    if space_between and out_line["s"]:
                        _restore_space(out_line["s"][-1], text, stored_box, size)
                    space_between = False
                    out_span = {
                        "t": text,
                        "f": font_key,
                        "fa": family,
                        "sz": size,
                        "c": colour,
                        "b": stored_box,
                    }
                    # Drop anything the reader reconstructs on its own.
                    for key, default in _SPAN_DEFAULTS.items():
                        if out_span[key] == default:
                            del out_span[key]
                    out_line["s"].append(out_span)

            if not out_line["s"]:
                continue
            # A lone span covers its whole line, and the reader falls back to
            # the line box when a span has none. Storing it twice is the
            # single largest redundancy in the manifest.
            if len(out_line["s"]) == 1:
                out_line["s"][0].pop("b", None)
            else:
                spans = _merge_adjacent_spans(out_line["s"], out_line["b"])
                # Joined into one, the span box goes too -- but only if it
                # spans the line exactly, since this reader then uses the
                # line box in its place.
                if (len(spans) == 1 and spans[0]["b"][0] == out_line["b"][0]
                        and spans[0]["b"][2] == out_line["b"][2]):
                    del spans[0]["b"]
                out_line["s"] = spans
            out_line["b"] = _open_line_box(out_line, page.rect.width,
                                           page.rect.height, precision, faces)
            out_block["l"].append(out_line)

    if out_block["l"]:
        page_data["tb"].append(out_block)

    # --- image placements -------------------------------------------------
    seen: set[str] = set()
    for info in page.get_image_info(xrefs=True):
        xref = info.get("xref", 0)
        if not xref or str(xref) not in images_data:
            continue
        bbox = info.get("bbox", (0, 0, 0, 0))
        if _covered(bbox, artwork):
            continue
        key = f"{xref}_{bbox[0]:.0f}_{bbox[1]:.0f}"
        if key in seen:
            continue
        seen.add(key)
        box = _quantize_box(bbox, precision)
        if box[2] <= box[0] or box[3] <= box[1]:
            # Narrower than the rounding: nothing to see, and insert_image
            # refuses an empty rectangle outright.
            continue
        page_data["ir"].append({"x": str(xref), "b": box})

    # --- vector rectangles and rules -----------------------------------------
    drawings: list = []
    page_area = abs(page.rect.width * page.rect.height) or 1.0
    for path in page.get_drawings():
        fill = path.get("fill")
        color = path.get("color")
        if fill is None and color is None:
            continue

        width = path.get("width", 0) or 0
        items = path.get("items", [])

        # Straight rules first: a path that is only axis-aligned strokes is a
        # set of borders or dividers, each stored as a stroked zero-height
        # rectangle (see _rule_entry), which every decoder draws. A filled
        # one that closes into rectangles goes through the rectangle branch
        # below, which keeps fill and stroke together. Any other filled
        # outline keeps its strokes; its fill has no rectangle to go in.
        if (color is not None and _path_is_only_axis_lines(items)
                and (fill is None or not _rects_from_path_items(items))):
            for x0, y0, x1, y1 in _axis_lines_from_path_items(items):
                seg = fitz.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                if _covered(seg + (-1, -1, 1, 1), artwork):
                    continue
                drawings.append(_rule_entry(
                    x0, y0, x1, y1, _color_to_hex(color), width, precision,
                ))
            continue

        for rect in _rects_from_path_items(items):
            rect = fitz.Rect(rect).normalize()
            if rect.is_infinite:
                continue
            if rect.is_empty:
                # Stroked, a rectangle of no height is a line, and the source
                # draws it; filled only, it paints nothing.
                if color is not None and not _covered(rect + (-1, -1, 1, 1), artwork):
                    drawings.append(_rule_entry(
                        rect.x0, rect.y0, rect.x1, rect.y1,
                        _color_to_hex(color), width, precision,
                    ))
                continue

            # A near-page-size dark fill is almost always a scanner ink layer
            # that was meant to be composited, not painted opaque.
            area = rect.width * rect.height
            if area > 0.8 * page_area and isinstance(fill, (list, tuple)) and len(fill) == 3:
                luminance = 0.299 * fill[0] + 0.587 * fill[1] + 0.114 * fill[2]
                if luminance < 0.3:
                    continue

            if _covered(rect, artwork):
                continue

            thickness = min(rect.width, rect.height)
            if (precision <= 0 and fill is not None and color is None
                    and _RULE_MIN_THICKNESS <= thickness < _RULE_MAX_THICKNESS):
                # A thin plain fill is a border. At whole-point coordinates
                # it is stored as a rule, so its thickness survives the
                # rounding (see _rule_entry). At a tenth of a point the fill
                # already keeps it, in fewer bytes and closer to the source:
                # measured on the Excel sheet, the rule form cost 7 % more
                # there and drifted further from the original rendering.
                drawings.append(_rule_entry(
                    rect.x0, rect.y0, rect.x1, rect.y1,
                    _color_to_hex(fill), thickness, precision,
                ))
                continue

            entry = {"t": "rect", "r": _quantize_box(rect, precision)}
            if fill is not None:
                entry["f"] = _color_to_hex(fill)
            if color is not None:
                entry["c"] = _color_to_hex(color)
                # A stroke width means nothing without a stroke colour.
                stroke = _stroke_width(width)
                if stroke is not None:
                    entry["w"] = stroke
            drawings.append(entry)

    # Compacted after rounding: at whole-point precision the rounding itself
    # makes many border pieces meet exactly, which lets them join. Measured
    # against compacting on a tenth-of-a-point grid first and rounding after,
    # this gave smaller archives and renderings closer to the source on all
    # four test documents.
    page_data["dr"] = _compact_drawings(drawings, tuple(page.rect), precision)

    # --- hyperlinks ---------------------------------------------------------
    # Stored under a page key the original decoder never reads, so it is
    # invisible to older stations and costs a few dozen bytes per link here.
    # Only external URIs are kept: an internal page jump needs the document's
    # own structure to resolve, which the rebuild does not reproduce.
    for link in page.get_links():
        uri = link.get("uri")
        if not uri or link.get("kind") != fitz.LINK_URI:
            continue
        box = link.get("from")
        if box is None or box.is_empty:
            continue
        page_data.setdefault("lk", []).append({
            "b": _quantize_box(box, precision),
            "u": uri,
        })

    # --- ticked form checkboxes -------------------------------------------
    # The tick lives in the widget's /AP /N appearance stream, which neither
    # get_text nor get_drawings reports. Without this, every box on a filled
    # form comes back empty.
    try:
        for widget in page.widgets() or []:
            type_str = str(getattr(widget, "field_type_string", "")).lower()
            is_check = (
                getattr(widget, "field_type", None) in (2, 5)
                or "checkbox" in type_str
                or "radio" in type_str
            )
            if not is_check:
                continue
            value = getattr(widget, "field_value", None)
            if value in (None, False, "", "Off", "off", "OFF", 0):
                continue
            r = widget.rect
            page_data["ck"].append(
                _quantize_box((r.x0, r.y0, r.x1, r.y1), precision)
            )
    except Exception:  # noqa: BLE001
        log.debug("Widget scan failed on page %d", page_idx + 1, exc_info=True)

    return page_data


def _render_region(page, region, params: dict) -> bytes | None:
    """Rasterise one page region at the preset resolution.

    Rendering through get_pixmap composites the region the way a viewer does,
    so transparency, soft masks and blend modes come out right -- which is
    what the object-by-object extraction cannot do for a chart.
    """
    try:
        dpi = params["dpi"]
        pix = page.get_pixmap(clip=region, matrix=fitz.Matrix(dpi / 72, dpi / 72))
        if pix.width < 2 or pix.height < 2:
            return None
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return _encode_payload(img, params["jpeg_quality"], params["lzma_preset"],
                               params.get("compact", False))
    except Exception:  # noqa: BLE001
        log.debug("Could not rasterise region %s", region, exc_info=True)
        return None


def _extract_placed_image(doc, xref: int, params: dict,
                          placed_pt: tuple[float, float]):
    """Extract one image object and encode it for the archive.

    Returns (payload, width, height), or None when the object cannot be
    read or yields nothing worth storing -- in which case the page simply
    has no image reference for it, as before.
    """
    try:
        raw = doc.extract_image(xref)
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    pixels = _image_bytes_with_mask(doc, xref, raw)
    if not pixels:
        return None
    return _optimize_image(pixels, raw["width"], raw["height"], params,
                           placed_pt=placed_pt)


def _compress_struct(pdf_path: str, params: dict, progress_cb: ProgressFn,
                     skip_images: bool) -> bytes:
    doc = fitz.open(pdf_path)
    try:
        images_data: dict[str, bytes] = {}
        image_meta: dict[str, dict] = {}
        page_count = doc.page_count

        # First pass: the largest frame each image is drawn into, across
        # every page. A logo repeated on eight pages is extracted once and
        # must be sized for its biggest appearance, not its first. Done
        # before any progress is reported so the image count is known and
        # the bar's scale can be fixed for the whole conversion.
        largest_pt: dict[int, tuple[float, float]] = {}
        if not skip_images:
            for page in doc:
                for info in page.get_image_info(xrefs=True):
                    xref = info.get("xref", 0)
                    if not xref:
                        continue
                    b = info.get("bbox", (0, 0, 0, 0))
                    w_pt, h_pt = abs(b[2] - b[0]), abs(b[3] - b[1])
                    prev = largest_pt.get(xref)
                    if prev is None or w_pt * h_pt > prev[0] * prev[1]:
                        largest_pt[xref] = (w_pt, h_pt)

        image_cost = (_COST_COMPRESS_IMAGE_COMPACT if params.get("compact")
                      else _COST_COMPRESS_IMAGE)
        progress = _Progress(
            progress_cb,
            len(largest_pt) * image_cost
            + page_count * (_COST_COMPRESS_PAGE + _COST_COMPRESS_WRITE_PER_PAGE),
        )

        if largest_pt:
            image_total = len(largest_pt)
            progress.phase(f"Extraction des images (0/{image_total})…")
            for n, xref in enumerate(sorted(largest_pt), start=1):
                optimized = _extract_placed_image(doc, xref, params,
                                                  largest_pt[xref])
                if optimized is not None:
                    payload, orig_w, orig_h = optimized
                    images_data[str(xref)] = payload
                    image_meta[str(xref)] = {
                        "w": orig_w,
                        "h": orig_h,
                        "s": len(payload),
                    }
                progress.advance(image_cost,
                                 f"Extraction des images ({n}/{image_total})…")

        progress.phase(f"Analyse des pages (0/{page_count})…")
        manifest = {
            "v": "1.0.6",
            "src": os.path.basename(pdf_path),
            "pages": [],
            "page_sizes": [],
            "images": image_meta,
        }

        # Artwork identifiers are handed out downwards from the top of the
        # range so they can never meet a real xref coming up from below.
        next_artwork_id = _ARTWORK_ID_TOP
        faces: dict = {}

        for page_idx, page in enumerate(doc):
            regions = [] if skip_images else _artwork_regions(page)
            # Unreadable text is rasterised whatever the image setting: the
            # operator chose to drop pictures, not to lose their own entries.
            regions = regions + _unreadable_text_regions(page)

            rendered = []
            for region in regions:
                tile = _render_region(page, region, params)
                if tile is None:
                    continue
                key = str(next_artwork_id)
                next_artwork_id -= 1
                images_data[key] = tile
                image_meta[key] = {
                    "w": int(region.width),
                    "h": int(region.height),
                    "s": len(tile),
                }
                rendered.append((key, region))

            page_data = _extract_page(page, page_idx, images_data,
                                      params.get("coord_precision", 1),
                                      [r for _, r in rendered], faces=faces)

            # Placed after extraction so the artwork sits above the fills and
            # below the text, exactly where the original drew it.
            for key, region in rendered:
                page_data["ir"].append({
                    "x": key,
                    "b": _quantize_box(region, params.get("coord_precision", 1)),
                })

            manifest["pages"].append(page_data)
            manifest["page_sizes"].append(
                {"w": page.rect.width, "h": page.rect.height}
            )
            progress.advance(_COST_COMPRESS_PAGE,
                             f"Analyse des pages ({page_idx + 1}/{page_count})…")

        progress.phase("Compression LZMA de l'archive…")
        writer = psdi.write_compact_struct if params.get("compact") else psdi.write_struct
        data = writer(manifest, images_data, params["lzma_preset"])
        progress.finish("Archive construite")
        return data
    finally:
        doc.close()


def _image_version(params: dict) -> int:
    """The container version of a page-image archive for these settings."""
    return (psdi.ARCHIVE_VERSION_COMPACT_IMAGE if params.get("compact")
            else psdi.ARCHIVE_VERSION_IMAGE)


def _compress_image(pdf_path: str, params: dict, progress_cb: ProgressFn) -> bytes:
    doc = fitz.open(pdf_path)
    try:
        dpi = params["dpi"]
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        total = doc.page_count
        progress = _Progress(
            progress_cb,
            total * (_COST_RENDER_PAGE + _COST_RENDER_WRITE_PER_PAGE),
        )

        pages: list[tuple[float, float, bytes]] = []
        progress.phase(f"Rendu des pages à {dpi} ppp (0/{total})…")
        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=matrix)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            pages.append((page.rect.width, page.rect.height,
                          _encode_page(img, params)))
            progress.advance(_COST_RENDER_PAGE,
                             f"Rendu des pages à {dpi} ppp ({i + 1}/{total})…")

        progress.phase("Écriture de l'archive…")
        data = psdi.write_image(pages, params["lzma_preset"], _image_version(params))
        progress.finish("Archive construite")
        return data
    finally:
        doc.close()


IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")

# A page is defined in points at 72 per inch. A phone photo of an A4 sheet is
# several thousand pixels on its long side; mapping that to A4 keeps the
# rebuilt page at a size every viewer and printer expects.
_A4_WIDTH_PT = 595.0
_A4_HEIGHT_PT = 842.0


def is_image_file(path: str) -> bool:
    return path.lower().endswith(IMAGE_EXTENSIONS)


def _page_size_for(width_px: int, height_px: int) -> tuple[float, float]:
    """Choose page dimensions in points for an image of this pixel size.

    The image's aspect ratio is kept exactly; only the scale is chosen, so
    that the longer side matches A4's longer side. Portrait and landscape are
    both handled by comparing the two orientations.
    """
    if width_px <= 0 or height_px <= 0:
        return _A4_WIDTH_PT, _A4_HEIGHT_PT
    landscape = width_px > height_px
    long_pt = _A4_HEIGHT_PT
    if landscape:
        return long_pt, long_pt * height_px / width_px
    return long_pt * width_px / height_px, long_pt


def _load_image_as_page(path: str) -> list[Image.Image]:
    """Open an image file as one page per frame.

    Multi-page TIFF scans are common from office copiers; each frame becomes
    a page. Everything else is a single page. EXIF orientation is applied so
    a phone photo taken sideways does not come out sideways -- a reader shows
    the pixels as stored, not as the camera meant them.
    """
    from PIL import ImageOps

    img = Image.open(path)
    frames: list[Image.Image] = []
    try:
        index = 0
        while True:
            img.seek(index)
            frame = ImageOps.exif_transpose(img.copy())
            if frame.mode in ("RGBA", "LA", "P"):
                # Flatten transparency onto white, the colour of paper.
                background = Image.new("RGB", frame.size, (255, 255, 255))
                rgba = frame.convert("RGBA")
                background.paste(rgba, mask=rgba.split()[3])
                frame = background
            elif frame.mode != "RGB":
                frame = frame.convert("RGB")
            frames.append(frame)
            index += 1
    except EOFError:
        pass
    finally:
        img.close()
    return frames


def _compress_image_file(path: str, params: dict, progress_cb: ProgressFn) -> bytes:
    """Compress an image file as a page-image archive.

    A photograph or scan has no text layer, so the structured encoding has
    nothing to work with; the page-image container is the natural fit, and
    it means the result rebuilds on any station that reads version 2
    archives, with nothing new to learn.

    The image is downsampled to the preset's DPI at the chosen page size, so
    a 12-megapixel photo and a 150-DPI scan of the same sheet come out the
    same size on the air.
    """
    frames = _load_image_as_page(path)
    if not frames:
        raise ValueError(f"No image data found in {path}")

    dpi = params["dpi"]
    pages: list[tuple[float, float, bytes]] = []
    total = len(frames)
    progress = _Progress(
        progress_cb, total * (_COST_RENDER_PAGE + _COST_RENDER_WRITE_PER_PAGE),
    )

    progress.phase(f"Préparation des images à {dpi} ppp (0/{total})…")
    for i, frame in enumerate(frames):
        width_pt, height_pt = _page_size_for(*frame.size)
        target = (max(1, int(round(width_pt * dpi / 72))),
                  max(1, int(round(height_pt * dpi / 72))))
        if frame.size != target:
            frame = frame.resize(target, Image.LANCZOS)
        pages.append((width_pt, height_pt,
                      _encode_page(frame, params)))
        progress.advance(_COST_RENDER_PAGE,
                         f"Préparation des images à {dpi} ppp ({i + 1}/{total})…")

    progress.phase("Écriture de l'archive…")
    data = psdi.write_image(pages, params["lzma_preset"], _image_version(params))
    progress.finish("Archive construite")
    return data


def pdf_to_archive(pdf_path: str, quality: str = QUALITY_MEDIUM,
                   mode: str = MODE_STRUCT, output_path: str | None = None,
                   progress_cb: ProgressFn = None,
                   skip_images: bool = False,
                   compact: bool = False) -> tuple[bytes, dict]:
    """Compress a PDF into a .psdi archive, or a compact .psdz one.

    A .psdi is what every implementation of the format reads, the original
    included, and is byte for byte what versions before 2.5.0 wrote. A .psdz
    (`compact`) is smaller and read only by this implementation from 2.5.0.

    Returns the archive bytes and a report covering size, ratio and the
    estimated on-air time per radio mode.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if quality not in QUALITY_PRESETS:
        raise ValueError(
            f"Invalid quality: {quality}. Choose from "
            f"{', '.join(QUALITY_PRESETS)}"
        )
    if mode not in (MODE_STRUCT, MODE_IMAGE):
        raise ValueError(f"Invalid mode: {mode}. Choose struct or image")

    params = QUALITY_PRESETS[quality]
    if compact:
        params = dict(params, compact=True)
    original_size = os.path.getsize(pdf_path)
    started = time.time()

    # Mode detection reads every page before any phase can report, so the
    # bar gets a label straight away rather than staying blank for it.
    _progress(progress_cb, 0, PROGRESS_SCALE,
              f"Analyse du document ({original_size // 1024} ko)…")

    auto_switch_reason = None
    if is_image_file(pdf_path):
        # Not a PDF at all: a photo or scan. Only the page-image encoding
        # applies, and the reason is recorded so the log says why.
        mode = MODE_IMAGE
        auto_switch_reason = "image"
    elif mode == MODE_STRUCT:
        detected, reason = detect_optimal_mode(pdf_path)
        # Text-only transfers deliberately discard images, so a page that is
        # merely image-heavy is not a reason to switch. A rotated page is:
        # the structured path works in unrotated coordinates and would
        # rebuild it sideways whatever the image setting.
        if detected != mode and (not skip_images or reason == "rotation"):
            auto_switch_reason = reason
            log.info("Auto-switch struct -> image (reason: %s)", reason)
            mode = detected

    if is_image_file(pdf_path):
        archive_bytes = _compress_image_file(pdf_path, params, progress_cb)
    elif mode == MODE_IMAGE:
        archive_bytes = _compress_image(pdf_path, params, progress_cb)
    else:
        archive_bytes = _compress_struct(pdf_path, params, progress_cb, skip_images)

    _progress(progress_cb, PROGRESS_SCALE, PROGRESS_SCALE, "Compression terminée")
    elapsed = time.time() - started

    if output_path:
        with open(output_path, "wb") as handle:
            handle.write(archive_bytes)

    info = {
        "source": os.path.basename(pdf_path),
        "original_size": original_size,
        "archive_size": len(archive_bytes),
        "ratio_percent": round(len(archive_bytes) / original_size * 100, 1),
        "compression_time": round(elapsed, 2),
        "quality": quality,
        "mode": mode,
        "auto_mode_switch": auto_switch_reason,
        "estimates": estimate_times(len(archive_bytes)),
        "compact": compact,
        "extension": psdi.COMPACT_EXTENSION if compact else psdi.EXTENSION,
    }
    log.info(
        "PDF -> archive: %s -> %s bytes (%s%%) in %.1fs [%s/%s%s]",
        f"{original_size:,}", f"{len(archive_bytes):,}",
        info["ratio_percent"], elapsed, quality, mode, "/compact" if compact else "",
    )
    return archive_bytes, info


def _rebuild_struct(archive_data: bytes, progress_cb: ProgressFn,
                    compact: bool = False) -> tuple[bytes, dict]:
    parsed = (psdi.read_compact_struct if compact else psdi.read_struct)(archive_data)
    manifest = parsed.manifest
    images = parsed.images
    if compact:
        _require_codecs(images.values())

    # Text lines are the unit of work: each one is laid out through a Story
    # and a temporary document, which is where nearly all the time goes. A
    # dense table page can hold three hundred of them against a dozen on a
    # cover page, so counting pages alone would still leave the bar sitting
    # still for seconds.
    page_total = len(manifest["pages"])
    line_counts = [
        sum(len(block.get("l", [])) for block in page_data.get("tb", []))
        for page_data in manifest["pages"]
    ]
    progress = _Progress(
        progress_cb,
        sum(line_counts) * (_COST_REBUILD_LINE + _COST_REBUILD_SAVE_PER_LINE)
        + page_total * _COST_REBUILD_PAGE,
    )

    doc = fitz.open()
    try:
        for page_no, page_data in enumerate(manifest["pages"], start=1):
            page_idx = page_data["pn"] - 1
            size = manifest["page_sizes"][page_idx]
            page = doc.new_page(width=size["w"], height=size["h"])
            label = f"Recomposition de la page {page_no}/{page_total}"
            progress.phase(f"{label}…")

            # Rectangles first: they are backgrounds and cell fills, and must
            # sit under everything else.
            shape = page.new_shape()
            for entry in page_data.get("dr", []):
                kind = entry.get("t")
                if kind == "rect":
                    line_width = entry.get("w", 0)
                    shape.draw_rect(fitz.Rect(entry["r"]))
                    shape.finish(
                        color=_hex_to_rgb(entry.get("c", "")),
                        fill=_hex_to_rgb(entry.get("f", "")),
                        width=line_width if line_width > 0.1 else 0,
                    )
                elif kind == "line":
                    x0, y0, x1, y1 = entry["r"]
                    shape.draw_line(fitz.Point(x0, y0), fitz.Point(x1, y1))
                    shape.finish(
                        color=_hex_to_rgb(entry.get("c", "")) or (0, 0, 0),
                        width=max(entry.get("w", 0.5), 0.3),
                    )
            shape.commit()

            for ref in page_data.get("ir", []):
                payload = images.get(ref["x"])
                rect = fitz.Rect(ref["b"])
                if not payload or rect.is_empty or rect.is_infinite:
                    # 2.2.x could round a sliver of an image to zero width,
                    # and insert_image raises on an empty rectangle: one
                    # invisible placement used to abort the whole rebuild.
                    continue
                try:
                    _place_image(page, rect, payload)
                except Exception:  # noqa: BLE001
                    # An undecodable payload costs that image, not the page.
                    log.warning("Image %s could not be placed on page %d",
                                ref["x"], page_no, exc_info=True)
            progress.advance(_COST_REBUILD_PAGE, f"{label}…")

            line_total = line_counts[page_no - 1]
            drawn = 0
            layer = _TextLayer(page.rect)
            for block in page_data.get("tb", []):
                for line in block.get("l", []):
                    _draw_line(page, line, layer)
                    drawn += 1
                    progress.advance(
                        _COST_REBUILD_LINE,
                        f"{label} — texte {drawn}/{line_total}…",
                    )
            layer.commit(page)

            _draw_checkmarks(page, page_data.get("ck", []))

            for link in page_data.get("lk", []):
                try:
                    page.insert_link({
                        "kind": fitz.LINK_URI,
                        "from": fitz.Rect(link["b"]),
                        "uri": link["u"],
                    })
                except Exception:  # noqa: BLE001
                    log.debug("Could not restore link %r", link.get("u"),
                              exc_info=True)

        progress.phase("Enregistrement du PDF…")
        buf = io.BytesIO()
        # garbage=4 merges duplicate resources: each page's text layer is a
        # form XObject with its own copy of the fonts, and without level 4
        # the file is several times larger. clean=True is deliberately
        # absent: it re-serialises every content stream for a file that
        # renders pixel for pixel the same, and this file never goes on the
        # air, so its size is of no consequence; the wait was.
        doc.save(buf, deflate=True, deflate_images=True, garbage=4)
        progress.finish("PDF enregistré")
    finally:
        doc.close()

    info = {
        "mode": MODE_STRUCT,
        "pages": len(manifest["pages"]),
        "crc_ok": parsed.crc_ok,
        "source": manifest.get("src", ""),
    }
    return buf.getvalue(), info


class _TextLayer:
    """Every text run of one page, laid out into a single form XObject.

    `Page.insert_htmlbox` builds a one-page document per run and grafts it
    onto the page with `show_pdf_page`, which first lists every XObject,
    image and font already on the page to pick an unused resource name. That
    listing grows with each run, so a page costs time quadratic in its line
    count: on a 294-line table page the last runs took 44 ms each against
    8 ms for the first, and that page alone took seven seconds.

    Here each run goes through the same Story layout and the same
    fit-to-box shrinking as insert_htmlbox -- the arithmetic is copied from
    it, so the glyphs land on the same pixels -- but is drawn into one
    DocumentWriter page the size of the target page, grafted once at the
    end. The rebuilt bulletin went from 15.5 s to under 3 s, and the saved
    PDF shrank, since the fonts are embedded once per page rather than once
    per run.
    """

    _BODY_MARGIN_CSS = "body {margin:1px;}"  # what insert_htmlbox prepends

    def __init__(self, page_rect):
        self._stream = io.BytesIO()
        self._writer = fitz.DocumentWriter(self._stream)
        self._device = self._writer.begin_page(
            fitz.Rect(0, 0, page_rect.width, page_rect.height)
        )
        self._runs = 0

    def add(self, rect, html: str, css: str, scale_low: float) -> bool:
        """Lay out one run inside `rect`; False when it fits at no scale."""
        story = fitz.Story(html=html, user_css=self._BODY_MARGIN_CSS + css)
        # insert_htmlbox fits by asking how much *larger* the box must grow
        # to hold the text, then draws the result shrunk by the inverse.
        temp_rect = fitz.Rect(0, 0, rect.width, rect.height)
        fit = story.fit_scale(
            temp_rect, scale_min=1, scale_max=1 / scale_low,
            flags=fitz.mupdf.FZ_PLACE_STORY_FLAG_NO_OVERFLOW,
        )
        if not fit.big_enough:
            return False
        scale = 1 / fit.parameter
        story.place(fit.rect)
        story.draw(self._device,
                   fitz.Matrix(scale, 0, 0, scale, rect.x0, rect.y0))
        self._runs += 1
        return True

    def commit(self, page) -> None:
        """Graft the layer onto `page`, above whatever is already drawn."""
        self._writer.end_page()
        self._writer.close()
        if not self._runs:
            return
        source = fitz.open(stream=self._stream.getvalue(), filetype="pdf")
        try:
            page.show_pdf_page(page.rect, source, 0)
        finally:
            source.close()


def _draw_line(page, line: dict, layer: _TextLayer | None = None) -> None:
    """Render one line of text, preserving the columns it was laid out in.

    Spans are grouped by position rather than simply concatenated. A line may
    hold a whole table row, with each cell placed by an explicit cursor move;
    concatenating those into one box collapses every column against the left
    edge. Spans that genuinely run on -- a bold word inside a sentence -- have
    no gap between them and stay in the same box, so inline styling still
    works.

    With a `layer`, runs are collected there and drawn in one go when the
    layer is committed; without one they are placed on the page directly,
    one insert_htmlbox each, which is the slow path kept for comparison.
    """
    spans = [span for span in line.get("s", []) if span.get("t")]
    if not spans:
        return

    line_box = line["b"]

    clusters: list[list[dict]] = []
    previous_end = None
    for span in spans:
        box = span.get("b", line_box)
        size = span.get("sz", 8)
        gap = None if previous_end is None else box[0] - previous_end
        if gap is None or gap <= max(1.0, _SPAN_SPLIT_RATIO * size):
            if clusters:
                clusters[-1].append(span)
            else:
                clusters.append([span])
        else:
            clusters.append([span])
        previous_end = box[2]

    for cluster in clusters:
        boxes = [span.get("b", line_box) for span in cluster]
        cluster_box = [
            min(b[0] for b in boxes), line_box[1],
            max(b[2] for b in boxes), line_box[3],
        ]
        _draw_cluster(page, cluster, cluster_box, layer)


def _draw_cluster(page, spans: list, bbox,
                  layer: _TextLayer | None = None) -> None:
    """Render one run of adjacent spans inside a single box.

    insert_htmlbox is used rather than insert_text because it handles mixed
    styling within a run and wraps sensibly. It also applies an internal
    margin, which on spreadsheets exported at ~4 pt leaves no room at all --
    hence the half-point of horizontal slack and the permission to shrink the
    font rather than truncate the string.
    """
    parts: list[str] = []
    for span in spans:
        text = span.get("t", "")
        if not text:
            continue
        escaped = (
            text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        color_int = span.get("c", 0)
        color_hex = (
            f"#{(color_int >> 16) & 0xFF:02x}"
            f"{(color_int >> 8) & 0xFF:02x}"
            f"{color_int & 0xFF:02x}"
        )
        style = (
            f"font-family:{_FAMILY_CSS.get(span.get('fa', 's'), _FAMILY_CSS['s'])};"
            f"font-size:{span.get('sz', 8)}pt;color:{color_hex};"
            "line-height:1;margin:0;padding:0;white-space:pre;"
        )
        key = span.get("f", "R")
        if key == "B":
            parts.append(f'<b style="{style}">{escaped}</b>')
        elif key == "I":
            parts.append(f'<i style="{style}">{escaped}</i>')
        elif key == "BI":
            parts.append(f'<b><i style="{style}">{escaped}</i></b>')
        else:
            parts.append(f'<span style="{style}">{escaped}</span>')

    if not parts:
        return

    line_height = bbox[3] - bbox[1]
    max_font = max((s.get("sz", 8) for s in spans), default=8)
    # Proportional, not a fixed floor: a fixed 12 pt minimum overlaps
    # consecutive lines on spreadsheets whose baselines are ~5.7 pt apart.
    height = max(line_height * 1.15, max_font * 1.2, 2)
    rect = fitz.Rect(bbox[0] - 0.5, bbox[1], bbox[2] + 0.5, bbox[1] + height)

    try:
        if layer is not None:
            fitted = layer.add(rect, "".join(parts), _HTML_BASE_CSS,
                               scale_low=0.5)
        else:
            fitted = page.insert_htmlbox(rect, "".join(parts),
                                         css=_HTML_BASE_CSS,
                                         scale_low=0.5)[0] >= 0
        if not fitted:
            # Both paths draw nothing when the text will not fit even at
            # half size; the original behaved the same, silently. Say so.
            log.debug("Run does not fit its box at half size, dropped: %r",
                      "".join(s.get("t", "") for s in spans)[:60])
    except Exception:  # noqa: BLE001
        # Fall back to plain text placement rather than dropping the line.
        for span in spans:
            text = span.get("t", "")
            if not text.strip():
                continue
            sb = span.get("b", bbox)
            try:
                page.insert_text(
                    fitz.Point(sb[0], sb[3]),
                    text,
                    fontname=_FONT_FALLBACK.get(span.get("f", "R"), "helv"),
                    fontsize=span.get("sz", 8),
                )
            except Exception:  # noqa: BLE001
                log.debug("Span fallback failed", exc_info=True)


def _draw_checkmarks(page, checks: list) -> None:
    """Draw ticks geometrically, with no font dependency.

    A polyline rather than two independent lines: below about 8 pt the two
    strokes of a V drawn separately visibly fail to meet.
    """
    if not checks:
        return

    shape = page.new_shape()
    drawn = False
    for box in checks:
        x0, y0, x1, y1 = box
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            continue
        shape.draw_polyline([
            fitz.Point(x0 + 0.20 * w, y0 + 0.55 * h),
            fitz.Point(x0 + 0.42 * w, y0 + 0.78 * h),
            fitz.Point(x0 + 0.82 * w, y0 + 0.18 * h),
        ])
        shape.finish(
            color=(0, 0, 0),
            fill=None,
            width=max(0.8, min(2.2, 0.16 * min(w, h))),
            closePath=False,
        )
        drawn = True

    if drawn:
        shape.commit()


def _rebuild_image(archive_data: bytes, progress_cb: ProgressFn) -> tuple[bytes, dict]:
    parsed = psdi.read_image(archive_data)
    total = len(parsed.pages)
    progress = _Progress(
        progress_cb,
        total * (_COST_REBUILD_PAGE + _COST_REBUILD_SAVE_PER_PAGE),
    )

    doc = fitz.open()
    try:
        progress.phase(f"Recomposition des pages (0/{total})…")
        for i, (width, height, jpeg) in enumerate(parsed.pages):
            page = doc.new_page(width=width, height=height)
            _place_image(page, page.rect, jpeg)
            progress.advance(_COST_REBUILD_PAGE,
                             f"Recomposition des pages ({i + 1}/{total})…")

        progress.phase("Enregistrement du PDF…")
        buf = io.BytesIO()
        doc.save(buf, deflate=True, garbage=4)
        progress.finish("PDF enregistré")
    finally:
        doc.close()

    return buf.getvalue(), {"mode": MODE_IMAGE, "pages": total, "crc_ok": True}


def archive_to_pdf(archive_data: bytes, output_path: str | None = None,
                   progress_cb: ProgressFn = None) -> tuple[bytes, dict]:
    """Rebuild a PDF from a received .psdi or .psdz archive.

    Every failure mode raises PsdiError, including a malformed container or a
    manifest that does not describe a coherent document, so the caller never
    has to interpret a struct or LZMA error from the parsing layer.
    """
    version = psdi.peek_version(archive_data)
    _progress(progress_cb, 0, PROGRESS_SCALE,
              f"Lecture de l'archive ({len(archive_data) // 1024} ko)…")

    if version in (psdi.ARCHIVE_VERSION_STRUCT, psdi.ARCHIVE_VERSION_COMPACT_STRUCT):
        pdf_bytes, info = _rebuild_struct(
            archive_data, progress_cb,
            compact=version == psdi.ARCHIVE_VERSION_COMPACT_STRUCT)
    elif version in (psdi.ARCHIVE_VERSION_IMAGE, psdi.ARCHIVE_VERSION_COMPACT_IMAGE):
        pdf_bytes, info = _rebuild_image(archive_data, progress_cb)
    else:
        raise psdi.PsdiError(f"Unsupported archive version: {version}")
    info["version"] = version
    info["compact"] = version in psdi.COMPACT_VERSIONS

    _progress(progress_cb, PROGRESS_SCALE, PROGRESS_SCALE, "Recomposition terminée")

    if output_path:
        with open(output_path, "wb") as handle:
            handle.write(pdf_bytes)

    info["pdf_size"] = len(pdf_bytes)
    return pdf_bytes, info
