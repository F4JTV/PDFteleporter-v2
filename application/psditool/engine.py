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


def _normalize_text(text: str) -> str:
    """Undo the ligature damage described above.

    Where the source glyph is already lost and only U+FFFD remains, the
    substitution is guessed from context: "tt" before an accented e or after
    "lu", "ti" otherwise. That covers the French vocabulary this is used on
    (situation, lutte, quitté, pollution) without a dictionary.
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


def _rects_from_path_items(items) -> list[fitz.Rect]:
    """Recover axis-aligned rectangles from a vector path.

    Two shapes turn up in practice. Microsoft's PDF chain emits native "re"
    rectangles. LibreOffice draws the same table cell as four separate line
    segments, and MuPDF reports it that way. Taking the path's overall
    bounding box instead of walking its sub-items is what used to paint a
    full-page black rectangle over documents built from multi-segment frames.
    """
    rects: list[fitz.Rect] = []
    points: list[tuple[float, float]] = []

    def flush() -> None:
        if len(points) >= 4:
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            rects.append(fitz.Rect(min(xs), min(ys), max(xs), max(ys)))
        points.clear()

    for item in items:
        kind = item[0]
        if kind == "re":
            flush()
            rects.append(fitz.Rect(item[1]))
        elif kind == "l":
            points.append((item[1].x, item[1].y))
            points.append((item[2].x, item[2].y))
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
    pixels = sample.getdata()
    coloured = sum(
        1 for r, g, b in pixels if max(r, g, b) - min(r, g, b) > 18
    )
    return coloured / len(sample.getdata()) <= threshold


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


def _optimize_image(img_bytes: bytes, width: int, height: int, params: dict):
    """Downscale and re-encode one extracted image.

    The size ceiling depends on what the image appears to be: a large square
    is treated as a map and given more room, a long thin strip is a letterhead
    banner and is capped by height, and a small icon is shrunk hard because it
    contributes nothing at radio bandwidth.
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

    return _encode_jpeg(img, params["jpeg_quality"]), width, height


def _extract_page(page, page_idx: int, images_data: dict,
                  precision: int = 1) -> dict:
    """Build the manifest entry for one page."""
    page_data: dict = {"pn": page_idx + 1, "tb": [], "ir": [], "dr": [], "ck": []}

    # --- text -------------------------------------------------------------
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue

        out_block: dict = {"l": []}
        for line in block.get("lines", []):
            out_line: dict = {"b": _quantize_box(line["bbox"], precision), "s": []}
            for span in line.get("spans", []):
                text = _normalize_text(span.get("text", ""))
                if not text.strip():
                    continue
                font_key, family = _classify_font(span.get("font", ""))
                out_span = {
                    "t": text,
                    "f": font_key,
                    "fa": family,
                    "sz": _quantize(span.get("size", 8), 1),
                    "c": span.get("color", 0),
                    "b": _quantize_box(span["bbox"], precision),
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
        key = f"{xref}_{bbox[0]:.0f}_{bbox[1]:.0f}"
        if key in seen:
            continue
        seen.add(key)
        page_data["ir"].append(
            {"x": str(xref), "b": _quantize_box(bbox, precision)}
        )

    # --- vector rectangles ------------------------------------------------
    page_area = abs(page.rect.width * page.rect.height) or 1.0
    for path in page.get_drawings():
        fill = path.get("fill")
        color = path.get("color")
        if fill is None and color is None:
            continue

        width = path.get("width", 0) or 0
        for rect in _rects_from_path_items(path.get("items", [])):
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

            entry = {"t": "rect", "r": _quantize_box(rect, precision)}
            if fill is not None:
                entry["f"] = _color_to_hex(fill)
            if color is not None:
                entry["c"] = _color_to_hex(color)
            if width > 0.1:
                entry["w"] = round(width, 1)
            page_data["dr"].append(entry)

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


def _compress_struct(pdf_path: str, params: dict, progress_cb: ProgressFn,
                     skip_images: bool) -> bytes:
    doc = fitz.open(pdf_path)
    try:
        images_data: dict[str, bytes] = {}
        image_meta: dict[str, dict] = {}

        if not skip_images:
            _progress(progress_cb, 1, 3, "Extraction des images…")
            seen_xrefs: set[int] = set()
            for page in doc:
                for info in page.get_image_info(xrefs=True):
                    xref = info.get("xref", 0)
                    if not xref or xref in seen_xrefs:
                        continue
                    seen_xrefs.add(xref)
                    try:
                        raw = doc.extract_image(xref)
                    except Exception:  # noqa: BLE001
                        continue
                    if not raw:
                        continue
                    optimized = _optimize_image(
                        raw["image"], raw["width"], raw["height"], params
                    )
                    if not optimized:
                        continue
                    payload, orig_w, orig_h = optimized
                    images_data[str(xref)] = payload
                    image_meta[str(xref)] = {
                        "w": orig_w,
                        "h": orig_h,
                        "s": len(payload),
                    }

        _progress(progress_cb, 2, 3, "Construction du manifeste…")
        manifest = {
            "v": "1.0.6",
            "src": os.path.basename(pdf_path),
            "pages": [],
            "page_sizes": [],
            "images": image_meta,
        }

        for page_idx, page in enumerate(doc):
            manifest["pages"].append(
                _extract_page(page, page_idx, images_data,
                              params.get("coord_precision", 1))
            )
            manifest["page_sizes"].append(
                {"w": page.rect.width, "h": page.rect.height}
            )

        return psdi.write_struct(manifest, images_data, params["lzma_preset"])
    finally:
        doc.close()


def _compress_image(pdf_path: str, params: dict, progress_cb: ProgressFn) -> bytes:
    doc = fitz.open(pdf_path)
    try:
        dpi = params["dpi"]
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        total = doc.page_count

        pages: list[tuple[float, float, bytes]] = []
        for i, page in enumerate(doc):
            _progress(progress_cb, i + 1, total + 1,
                      f"Rendu de la page {i + 1}/{total} ({dpi} ppp)…")
            pix = page.get_pixmap(matrix=matrix)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            pages.append((page.rect.width, page.rect.height,
                          _encode_jpeg(img, params["jpeg_quality"])))

        return psdi.write_image(pages, params["lzma_preset"])
    finally:
        doc.close()


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

    auto_switch_reason = None
    if mode == MODE_STRUCT:
        detected, reason = detect_optimal_mode(pdf_path)
        # Text-only transfers deliberately discard images, so a page that is
        # merely image-heavy is not a reason to switch. A rotated page is:
        # the structured path works in unrotated coordinates and would
        # rebuild it sideways whatever the image setting.
        if detected != mode and (not skip_images or reason == "rotation"):
            auto_switch_reason = reason
            log.info("Auto-switch struct -> image (reason: %s)", reason)
            mode = detected

    _progress(progress_cb, 0, 3, f"Ouverture du PDF ({original_size // 1024} ko)…")

    if mode == MODE_IMAGE:
        archive_bytes = _compress_image(pdf_path, params, progress_cb)
    else:
        archive_bytes = _compress_struct(pdf_path, params, progress_cb, skip_images)

    _progress(progress_cb, 3, 3, "Compression terminée")
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

    _progress(progress_cb, 1, 2, "Recomposition des pages…")

    doc = fitz.open()
    try:
        for page_data in manifest["pages"]:
            page_idx = page_data["pn"] - 1
            size = manifest["page_sizes"][page_idx]
            page = doc.new_page(width=size["w"], height=size["h"])

            # Rectangles first: they are backgrounds and cell fills, and must
            # sit under everything else.
            shape = page.new_shape()
            for entry in page_data.get("dr", []):
                if entry.get("t") != "rect":
                    continue
                line_width = entry.get("w", 0)
                shape.draw_rect(fitz.Rect(entry["r"]))
                shape.finish(
                    color=_hex_to_rgb(entry.get("c", "")),
                    fill=_hex_to_rgb(entry.get("f", "")),
                    width=line_width if line_width > 0.1 else 0,
                )
            shape.commit()

            for ref in page_data.get("ir", []):
                payload = images.get(ref["x"])
                if payload:
                    page.insert_image(fitz.Rect(ref["b"]), stream=payload)

            for block in page_data.get("tb", []):
                for line in block.get("l", []):
                    _draw_line(page, line)

            _draw_checkmarks(page, page_data.get("ck", []))

        buf = io.BytesIO()
        doc.save(buf, deflate=True, deflate_images=True, garbage=4, clean=True)
    finally:
        doc.close()

    info = {
        "mode": MODE_STRUCT,
        "pages": len(manifest["pages"]),
        "crc_ok": parsed.crc_ok,
        "source": manifest.get("src", ""),
    }
    return buf.getvalue(), info


def _draw_line(page, line: dict) -> None:
    """Render one line of text through the HTML engine.

    insert_htmlbox is used rather than insert_text because it handles mixed
    styling within a line and wraps sensibly. It also applies an internal
    margin, which on spreadsheets exported at ~4 pt leaves no room at all --
    hence the half-point of horizontal slack and the permission to shrink the
    font rather than truncate the string.
    """
    spans = line.get("s", [])
    if not spans:
        return

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
            "line-height:1;margin:0;padding:0;"
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

    bbox = line["b"]
    line_height = bbox[3] - bbox[1]
    max_font = max((s.get("sz", 8) for s in spans), default=8)
    # Proportional, not a fixed floor: a fixed 12 pt minimum overlaps
    # consecutive lines on spreadsheets whose baselines are ~5.7 pt apart.
    height = max(line_height * 1.15, max_font * 1.2, 2)
    rect = fitz.Rect(bbox[0] - 0.5, bbox[1], bbox[2] + 0.5, bbox[1] + height)

    try:
        page.insert_htmlbox(rect, "".join(parts), css=_HTML_BASE_CSS, scale_low=0.5)
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

    doc = fitz.open()
    try:
        for i, (width, height, jpeg) in enumerate(parsed.pages):
            _progress(progress_cb, i + 1, total + 1,
                      f"Recomposition de la page {i + 1}/{total}…")
            page = doc.new_page(width=width, height=height)
            page.insert_image(page.rect, stream=jpeg)

        buf = io.BytesIO()
        doc.save(buf, deflate=True, garbage=4, clean=True)
    finally:
        doc.close()

    return buf.getvalue(), {"mode": MODE_IMAGE, "pages": total, "crc_ok": True}


def archive_to_pdf(archive_data: bytes, output_path: str | None = None,
                   progress_cb: ProgressFn = None) -> tuple[bytes, dict]:
    """Rebuild a PDF from a received .psdi archive."""
    version = psdi.peek_version(archive_data)

    if version == psdi.ARCHIVE_VERSION_STRUCT:
        pdf_bytes, info = _rebuild_struct(archive_data, progress_cb)
    elif version == psdi.ARCHIVE_VERSION_IMAGE:
        pdf_bytes, info = _rebuild_image(archive_data, progress_cb)
    else:
        raise psdi.PsdiError(f"Unsupported archive version: {version}")

    _progress(progress_cb, 2, 2, "Recomposition terminée")

    if output_path:
        with open(output_path, "wb") as handle:
            handle.write(pdf_bytes)

    info["pdf_size"] = len(pdf_bytes)
    return pdf_bytes, info
