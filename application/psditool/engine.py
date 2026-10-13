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

import io
import logging
import os
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

from PIL import Image

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

    return _encode_jpeg(img, params["jpeg_quality"]), width, height


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
    return lines


def _path_is_only_axis_lines(items) -> bool:
    """A path made solely of horizontal and vertical strokes is structure."""
    if not items or any(item[0] != "l" for item in items):
        return False
    return len(_axis_lines_from_path_items(items)) == len(items)


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
                  bad_fonts: set | None = None) -> dict:
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
    for block in page.get_text("rawdict").get("blocks", []):
        if block.get("type") != 0:
            continue

        out_block: dict = {"l": []}
        for line in block.get("lines", []):
            if _covered(line["bbox"], artwork):
                continue
            out_line: dict = {"b": _quantize_box(line["bbox"], precision), "s": []}
            for span in line.get("spans", []):
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
                    out_span = {
                        "t": text,
                        "f": font_key,
                        "fa": family,
                        "sz": size,
                        "c": colour,
                        "b": _quantize_box(box, precision),
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
        page_data["ir"].append(
            {"x": str(xref), "b": _quantize_box(bbox, precision)}
        )

    # --- vector rectangles and rules -----------------------------------------
    page_area = abs(page.rect.width * page.rect.height) or 1.0
    for path in page.get_drawings():
        fill = path.get("fill")
        color = path.get("color")
        if fill is None and color is None:
            continue

        width = path.get("width", 0) or 0
        items = path.get("items", [])

        # Straight rules first: a path that is only axis-aligned strokes is
        # emitted as "line" entries. Stored in the same "dr" list as the
        # rectangles, with a different "t", because the original decoder
        # walks that list with `if d['t'] != 'rect': continue` -- it steps
        # over these cleanly instead of failing on an unknown page key.
        if color is not None and _path_is_only_axis_lines(items):
            for x0, y0, x1, y1 in _axis_lines_from_path_items(items):
                seg = fitz.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                if _covered(seg + (-1, -1, 1, 1), artwork):
                    continue
                entry = {
                    "t": "line",
                    "r": _quantize_box((x0, y0, x1, y1), precision),
                    "c": _color_to_hex(color),
                }
                if width > 0.1:
                    entry["w"] = round(width, 1)
                page_data["dr"].append(entry)
            continue

        for rect in _rects_from_path_items(items):
            rect = fitz.Rect(rect).normalize()
            if rect.is_empty or rect.is_infinite:
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

            entry = {"t": "rect", "r": _quantize_box(rect, precision)}
            if fill is not None:
                entry["f"] = _color_to_hex(fill)
            if color is not None:
                entry["c"] = _color_to_hex(color)
            if width > 0.1:
                entry["w"] = round(width, 1)
            page_data["dr"].append(entry)

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
        return _encode_jpeg(img, params["jpeg_quality"])
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

        progress = _Progress(
            progress_cb,
            len(largest_pt) * _COST_COMPRESS_IMAGE
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
                progress.advance(_COST_COMPRESS_IMAGE,
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
                                      [r for _, r in rendered])

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
        data = psdi.write_struct(manifest, images_data, params["lzma_preset"])
        progress.finish("Archive construite")
        return data
    finally:
        doc.close()


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
                          _encode_jpeg(img, params["jpeg_quality"])))
            progress.advance(_COST_RENDER_PAGE,
                             f"Rendu des pages à {dpi} ppp ({i + 1}/{total})…")

        progress.phase("Écriture de l'archive…")
        data = psdi.write_image(pages, params["lzma_preset"])
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
                      _encode_jpeg(frame, params["jpeg_quality"])))
        progress.advance(_COST_RENDER_PAGE,
                         f"Préparation des images à {dpi} ppp ({i + 1}/{total})…")

    progress.phase("Écriture de l'archive…")
    data = psdi.write_image(pages, params["lzma_preset"])
    progress.finish("Archive construite")
    return data


def pdf_to_archive(pdf_path: str, quality: str = QUALITY_MEDIUM,
                   mode: str = MODE_STRUCT, output_path: str | None = None,
                   progress_cb: ProgressFn = None,
                   skip_images: bool = False) -> tuple[bytes, dict]:
    """Compress a PDF into a .psdi archive.

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
    }
    log.info(
        "PDF -> archive: %s -> %s bytes (%s%%) in %.1fs [%s/%s]",
        f"{original_size:,}", f"{len(archive_bytes):,}",
        info["ratio_percent"], elapsed, quality, mode,
    )
    return archive_bytes, info


def _rebuild_struct(archive_data: bytes, progress_cb: ProgressFn) -> tuple[bytes, dict]:
    parsed = psdi.read_struct(archive_data)
    manifest = parsed.manifest
    images = parsed.images

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
                if payload:
                    page.insert_image(fitz.Rect(ref["b"]), stream=payload)
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
            page.insert_image(page.rect, stream=jpeg)
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
    """Rebuild a PDF from a received .psdi archive.

    Every failure mode raises PsdiError, including a malformed container or a
    manifest that does not describe a coherent document, so the caller never
    has to interpret a struct or LZMA error from the parsing layer.
    """
    version = psdi.peek_version(archive_data)
    _progress(progress_cb, 0, PROGRESS_SCALE,
              f"Lecture de l'archive ({len(archive_data) // 1024} ko)…")

    if version == psdi.ARCHIVE_VERSION_STRUCT:
        pdf_bytes, info = _rebuild_struct(archive_data, progress_cb)
    elif version == psdi.ARCHIVE_VERSION_IMAGE:
        pdf_bytes, info = _rebuild_image(archive_data, progress_cb)
    else:
        raise psdi.PsdiError(f"Unsupported archive version: {version}")

    _progress(progress_cb, PROGRESS_SCALE, PROGRESS_SCALE, "Recomposition terminée")

    if output_path:
        with open(output_path, "wb") as handle:
            handle.write(pdf_bytes)

    info["pdf_size"] = len(pdf_bytes)
    return pdf_bytes, info
