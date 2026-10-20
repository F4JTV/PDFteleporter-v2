# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Deterministic test documents.

Built programmatically rather than committed as PDF files. A PDF carries a
creation timestamp and an object layout that varies between PyMuPDF releases,
so a committed file would either drift or hide drift. What must stay stable is
the *archive*, and the archive depends only on the text, geometry and images
the extractor sees -- never on PDF metadata. Generating the source document
each run therefore tests exactly the property the interoperability contract
depends on.
"""

from __future__ import annotations

import random

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz

WORDS = (
    "situation reconnaissance secteur equipe balise detresse coordination "
    "prefecture moyens engages liaison radio frequence relais operateur "
    "mission terrain vehicule autonomie batterie antenne portable exercice"
).split()


def text_report(path: str, pages: int = 3, seed: int = 7) -> str:
    """A dense multi-page report: headed frame, prose, and a gridded table.

    Mirrors the shape of a real SITREP, which is what the structured encoding
    is optimised for.
    """
    rng = random.Random(seed)
    doc = fitz.open()
    for page_no in range(pages):
        page = doc.new_page(width=595, height=842)

        shape = page.new_shape()
        shape.draw_rect(fitz.Rect(40, 35, 555, 80))
        shape.finish(color=(0.1, 0.3, 0.6), fill=(0.87, 0.91, 0.97), width=1.2)
        shape.commit()
        page.insert_text(fitz.Point(50, 65), f"SITREP - PAGE {page_no + 1}",
                         fontname="hebo", fontsize=16, color=(0.05, 0.2, 0.5))

        y = 110
        for _ in range(8):
            line = " ".join(rng.choice(WORDS) for _ in range(9)).capitalize()
            page.insert_text(fitz.Point(50, y), line + ".",
                             fontname="helv", fontsize=10)
            y += 16

        shape = page.new_shape()
        for row in range(10):
            for col in range(5):
                shape.draw_rect(fitz.Rect(50 + col * 100, 270 + row * 26,
                                          50 + (col + 1) * 100,
                                          270 + (row + 1) * 26))
        shape.finish(color=(0.45, 0.45, 0.45), fill=(0.95, 0.95, 0.95), width=0.6)
        shape.commit()
        for row in range(10):
            for col in range(5):
                page.insert_text(fitz.Point(56 + col * 100, 287 + row * 26),
                                 f"{rng.choice(WORDS)[:8]} {row}{col}",
                                 fontname="helv", fontsize=7)

    doc.save(path, deflate=True)
    doc.close()
    return path


def rotated_report(path: str) -> str:
    """A report whose first page is rotated, forcing page-image mode."""
    source = text_report(path + ".tmp", pages=2)
    doc = fitz.open(source)
    doc[0].set_rotation(90)
    doc.save(path)
    doc.close()
    return path


def tiny_table(path: str) -> str:
    """A twelve-row table in 7 pt text.

    This is the shape that whole-point coordinate rounding could plausibly
    break: baselines are close enough together that half a point of drift
    would show as overlap.
    """
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    shape = page.new_shape()
    for row in range(12):
        for col in range(4):
            shape.draw_rect(fitz.Rect(40 + col * 120, 60 + row * 14,
                                      40 + (col + 1) * 120, 60 + (row + 1) * 14))
    shape.finish(color=(0.4, 0.4, 0.4), width=0.4)
    shape.commit()
    for row in range(12):
        for col in range(4):
            page.insert_text(fitz.Point(44 + col * 120, 70 + row * 14),
                             f"cellule {row}-{col}", fontname="helv", fontsize=7)
    doc.save(path)
    doc.close()
    return path


def monochrome_scan_like(path: str) -> str:
    """A page with no colour at all, to exercise the grayscale path."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(fitz.Point(50, 60), "ARRETE PREFECTORAL",
                     fontname="hebo", fontsize=16)
    y = 100
    for i in range(25):
        page.insert_text(
            fitz.Point(50, y),
            f"Article {i} - les dispositions du present arrete s'appliquent.",
            fontname="helv", fontsize=10,
        )
        y += 20
    doc.save(path)
    doc.close()
    return path


def find_truetype_fonts(limit: int = 4) -> list[str]:
    """Locate a few embeddable TrueType files, preferring long-named ones.

    Several fonts rather than one, because the failure this fixture guards
    against depended on the font: a BaseFont longer than 31 characters is
    truncated by the producer, and a matcher that passed on "DejaVuSans"
    failed on "DejaVu Sans Condensed Bold Oblique" -- and on Arial, which is
    what every Windows build machine actually has.
    """
    import glob

    patterns = (
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/**/DejaVuSansCondensed-BoldOblique.ttf",
        "/usr/share/fonts/**/DejaVuSans.ttf",
        "/usr/share/fonts/**/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    )
    found: list[str] = []
    for pattern in patterns:
        for hit in glob.glob(pattern, recursive=True):
            if hit not in found:
                found.append(hit)
            if len(found) >= limit:
                return found
    return found


def _find_truetype() -> str | None:
    fonts = find_truetype_fonts(limit=1)
    return fonts[0] if fonts else None


def annotated_without_tounicode(path: str, ttf: str | None = None) -> str | None:
    """A form whose operator entries use a font with no ToUnicode table.

    This is what Okular and several other annotation tools produce: an
    embedded CID font with Identity encoding and no mapping back to text.
    The labels stay in a base-14 font and decode normally; only the entries
    are affected, which is exactly the shape of the real-world failure.

    Returns None when no TrueType font is available to embed.
    """
    ttf = ttf or _find_truetype()
    if ttf is None:
        return None

    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text(fitz.Point(30, 50), "Indicatif :", fontname="helv", fontsize=12)
    page.insert_text(fitz.Point(30, 80), "Fréquence :", fontname="helv", fontsize=12)
    page.insert_text(fitz.Point(30, 110), "Situation stable, lutte en cours.",
                     fontname="helv", fontsize=12)
    page.insert_text(fitz.Point(120, 50), "F4LTV", fontname="entry",
                     fontfile=ttf, fontsize=12)
    page.insert_text(fitz.Point(120, 80), "145.500", fontname="entry",
                     fontfile=ttf, fontsize=12)
    doc.save(path)
    doc.close()

    # Strip the ToUnicode table from every CID font, as such tools do.
    doc = fitz.open(path)
    for xref in range(1, doc.xref_length()):
        try:
            if doc.xref_get_key(xref, "Type")[1] != "/Font":
                continue
        except Exception:  # noqa: BLE001
            continue
        if doc.xref_get_key(xref, "ToUnicode")[0] != "null":
            doc.xref_set_key(xref, "ToUnicode", "null")
    doc.save(path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    doc.close()
    return path


def rich_report(path: str) -> str:
    """A page with every structured feature the encoder knows about.

    One of each: a placed raster image drawn smaller than its pixel size (so
    placement-aware downscaling has something to act on), a horizontal rule,
    a vertical rule, a hyperlink, a filled table cell and prose. Built for
    the golden vectors, whose earlier fixtures held text and rectangles only
    and therefore could not notice a change to how images, lines or links
    are encoded.
    """
    from PIL import Image, ImageDraw
    import io

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)

    page.insert_text(fitz.Point(50, 60), "Rapport illustré", fontname="hebo", fontsize=16)

    # A 600x400 photo-like image placed in a 200x133 pt frame: 3x more
    # pixels than the page shows.
    img = Image.new("RGB", (600, 400))
    draw = ImageDraw.Draw(img)
    for i in range(0, 600, 20):
        draw.rectangle([i, 0, i + 10, 400], fill=((i * 7) % 255, (i * 3) % 255, 120))
    draw.ellipse([150, 80, 450, 320], fill=(240, 200, 60))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    page.insert_image(fitz.Rect(50, 80, 250, 213), stream=buf.getvalue())

    # Rules: a title underline and a column divider.
    shape = page.new_shape()
    shape.draw_line(fitz.Point(50, 230), fitz.Point(545, 230))
    shape.finish(color=(0.4, 0.4, 0.4), width=0.8)
    shape.draw_line(fitz.Point(300, 250), fitz.Point(300, 500))
    shape.finish(color=(0.6, 0.6, 0.6), width=0.5)
    shape.commit()

    # A filled cell with a border, as a table header.
    shape = page.new_shape()
    shape.draw_rect(fitz.Rect(50, 250, 290, 274))
    shape.finish(color=(0.2, 0.3, 0.5), fill=(0.85, 0.9, 0.97), width=0.8)
    shape.commit()
    page.insert_text(fitz.Point(56, 267), "Moyens engagés", fontname="hebo", fontsize=10)

    y = 300
    for line in ("Situation stable sur le secteur nord.",
                 "Liaison VARA HF établie avec la préfecture.",
                 "Consulter la fiche réflexe en ligne."):
        page.insert_text(fitz.Point(56, y), line, fontname="helv", fontsize=10)
        y += 16

    # A hyperlink over the last line.
    page.insert_link({
        "kind": fitz.LINK_URI,
        "from": fitz.Rect(56, 320, 260, 334),
        "uri": "https://example.org/fiche-reflexe",
    })

    doc.save(path, deflate=True)
    doc.close()
    return path


def bordered_sheet(path: str) -> str:
    """Two small tables bordered the way spreadsheet exports draw them.

    Left, as Excel does: every cell border is its own thin filled rectangle,
    0.84 pt thick at fractional positions, with a gap at each crossing where
    the perpendicular border passes; white and coloured cell fills under the
    borders. Right, as LibreOffice does: every cell edge is its own stroke,
    each inner edge drawn twice -- once per adjacent cell -- and neighbouring
    pieces overlapping slightly at the corners.

    Built for the drawing compaction: it must join what tiles, keep what is
    painted twice, and survive whole-point coordinates without losing a
    border.
    """
    doc = fitz.open()
    page = doc.new_page(width=842, height=595)
    page.insert_text(fitz.Point(40, 60), "Point de situation", fontname="hebo",
                     fontsize=14)

    # --- Excel style -------------------------------------------------------
    x0, y0, cw, ch, t = 40.37, 100.29, 58.61, 18.43, 0.84
    rows, cols = 6, 4
    shape = page.new_shape()
    for r in range(rows):
        for c in range(cols):
            cell = fitz.Rect(x0 + c * cw, y0 + r * ch, x0 + (c + 1) * cw, y0 + (r + 1) * ch)
            shape.draw_rect(cell)
            shape.finish(color=None, fill=(1, 0.75, 0) if r == 0 else (1, 1, 1), width=0)
    shape.commit()
    shape = page.new_shape()
    for r in range(rows + 1):
        y = y0 + r * ch
        for c in range(cols):
            # Each horizontal piece stops short of the vertical borders.
            shape.draw_rect(fitz.Rect(x0 + c * cw + t, y, x0 + (c + 1) * cw, y + t))
            shape.finish(color=None, fill=(0, 0, 0), width=0)
    for c in range(cols + 1):
        x = x0 + c * cw
        for r in range(rows):
            shape.draw_rect(fitz.Rect(x, y0 + r * ch, x + t, y0 + (r + 1) * ch + t))
            shape.finish(color=None, fill=(0, 0, 0), width=0)
    shape.commit()

    # --- LibreOffice style ---------------------------------------------------
    x0, y0, cw, ch, w = 400.6, 100.4, 61.3, 17.9, 0.36
    shape = page.new_shape()
    for r in range(rows):
        for c in range(cols):
            left, top = x0 + c * cw, y0 + r * ch
            right, bottom = left + cw, top + ch
            for a, b in (((left - 0.3, top), (right + 0.3, top)),
                         ((left - 0.3, bottom), (right + 0.3, bottom)),
                         ((left, top - 0.3), (left, bottom + 0.3)),
                         ((right, top - 0.3), (right, bottom + 0.3))):
                shape.draw_line(fitz.Point(*a), fitz.Point(*b))
                shape.finish(color=(0, 0, 0), width=w)
    shape.commit()

    for r in range(rows):
        for c in range(cols):
            page.insert_text(fitz.Point(46 + c * 58.61, 113 + r * 18.43),
                             f"Ligne {r} col {c}", fontname="helv", fontsize=6)
            page.insert_text(fitz.Point(406 + c * 61.3, 113 + r * 17.9),
                             f"Ligne {r} col {c}", fontname="helv", fontsize=6)

    doc.save(path, deflate=True)
    doc.close()
    return path


def graphics_report(path: str) -> str:
    """A flat-colour chart and a photograph on one structured page.

    The chart is a few solid colours with hard edges -- what a palette PNG
    stores best and JPEG worst. The photograph is noise, which nothing but
    JPEG stores well.
    """
    from PIL import Image, ImageDraw
    import io

    rng = random.Random(11)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(fitz.Point(50, 60), "Bilan chiffré", fontname="hebo", fontsize=16)

    chart = Image.new("RGB", (480, 300), (255, 255, 255))
    draw = ImageDraw.Draw(chart)
    for i, h in enumerate((120, 210, 90, 260, 170, 40)):
        colour = ((68, 114, 196), (237, 125, 49), (165, 165, 165))[i % 3]
        draw.rectangle([30 + i * 75, 280 - h, 85 + i * 75, 280], fill=colour)
    draw.line([20, 280, 470, 280], fill=(0, 0, 0), width=2)
    buf = io.BytesIO()
    chart.save(buf, format="PNG")
    page.insert_image(fitz.Rect(50, 80, 290, 230), stream=buf.getvalue())

    photo = Image.new("RGB", (300, 200))
    photo.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256))
                   for _ in range(300 * 200)])
    buf = io.BytesIO()
    photo.save(buf, format="JPEG", quality=90)
    page.insert_image(fitz.Rect(320, 80, 545, 230), stream=buf.getvalue())

    y = 270
    for _ in range(12):
        line = " ".join(rng.choice(WORDS) for _ in range(9)).capitalize()
        page.insert_text(fitz.Point(50, y), line + ".", fontname="helv", fontsize=10)
        y += 16

    doc.save(path, deflate=True)
    doc.close()
    return path


def _set_words(page, y: float, words, x: float = 50, size: float = 10.0,
               gap: float = 0.33) -> float:
    """Place each word on its own, `gap` em after the last: no space typed."""
    for word, face in words:
        page.insert_text(fitz.Point(x, y), word, fontname=face, fontsize=size)
        x += fitz.Font(face).text_length(word, fontsize=size) + gap * size
    return x


def cursor_spaced(path: str, seed: int = 5) -> str:
    """Prose whose words are placed by cursor moves, the way LaTeX sets them.

    The PDF holds no space character at all: MuPDF guesses every one of them
    and hands each over as a span of its own. A bold word inside a sentence,
    a bullet and a table row whose values sit a column apart cover the places
    where the gap decides whether two words share a run.
    """
    rng = random.Random(seed)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    _set_words(page, 70, [("System", "hebo"), ("Requirements", "hebo")], size=14)
    y = 100
    for _ in range(8):
        _set_words(page, y, [(rng.choice(WORDS), "helv") for _ in range(8)])
        y += 15
    _set_words(page, y, [("Le", "helv"), ("relais", "hebo"), ("est", "helv"),
                         ("actif", "helv")])
    y += 15
    # 0.6 em: MuPDF still guesses a space and keeps one line, but the
    # decoders set the two sides as separate runs.
    x = _set_words(page, y, [("·", "helv")], x=60, gap=0.6)
    _set_words(page, y, [("Windows:", "helv"), ("Windows", "helv"), ("10", "helv")], x=x)
    y += 15
    _set_words(page, y, [("128847", "helv"), ("119323", "helv")], gap=0.6)
    doc.save(path, deflate=True)
    doc.close()
    return path


def typed_spaces_with_noise(path: str) -> str:
    """A page whose producer types its spaces, with two traps.

    One word is set glyph by glyph at irregular intervals: MuPDF guesses
    spaces inside it, which are noise. And one typed space sits in a span of
    its own, in another style than the words around it: a real word
    boundary. (The LibreOffice sheet's own noise comes from a fixed pitch;
    see fixed_pitch_cells.)
    """
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(fitz.Point(50, 70), "Les communes sont toutes en vigilance "
                     "rouge depuis hier soir.", fontname="helv", fontsize=10)
    helv = fitz.Font("helv")
    x = 50
    for char, gap in zip("sinistrées", (0.09, 0.31, -0.03, 0.33, 0.07, 0.22, 0.18,
                                        -0.02, 0.0, 0.0)):
        page.insert_text(fitz.Point(x, 100), char, fontname="helv", fontsize=10)
        x += helv.text_length(char, fontsize=10) + gap * 10
    x = 50
    for text, face in (("maladies", "helv"), (" ", "hebo"), ("émergentes", "helv")):
        page.insert_text(fitz.Point(x, 130), text, fontname=face, fontsize=10)
        x += fitz.Font(face).text_length(text, fontsize=10)
    doc.save(path, deflate=True)
    doc.close()
    return path


def _raw_page(doc, fonts: dict, content: str, width: float = 595,
              height: float = 842):
    """A page drawn by a hand-written content stream.

    insert_text sets whole strings in an encoding of its own choosing. The
    producers the fixtures below stand in for move glyph by glyph inside one
    TJ, and reach ligatures and symbols through their fonts' encodings.
    """
    page = doc.new_page(width=width, height=height)
    refs = []
    for name, definition in fonts.items():
        xref = doc.get_new_xref()
        doc.update_object(xref, definition)
        refs.append(f"/{name} {xref} 0 R")
    doc.xref_set_key(page.xref, "Resources", "<</Font<<" + "".join(refs) + ">>>>")
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, content.encode("latin-1"))
    doc.xref_set_key(page.xref, "Contents", f"{xref} 0 R")
    return page


# Helvetica drawing the "fi" ligature at code 128, where WinAnsi has the euro.
_FI = "\x80"
_HELVETICA_FI = ("<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding"
                 "<</Type/Encoding/BaseEncoding/WinAnsiEncoding"
                 "/Differences[128/fi]>>>>")
_SYMBOL = "<</Type/Font/Subtype/Type1/BaseFont/Symbol>>"


def _pitch_tj(segments, pitch: float = 0.6) -> str:
    """One TJ moving each glyph on by `pitch` em per letter it draws.

    `segments` are (text, blank steps after it). The ligature takes two
    steps, like the two letters it stands for.
    """
    helv = fitz.Font("helv")
    parts = []
    for text, blank in segments:
        for index, char in enumerate(text):
            steps = 2 if char == _FI else 1
            if index == len(text) - 1:
                steps += blank
            width = helv.glyph_advance(0xFB01 if char == _FI else ord(char)) * 1000
            parts += [f"<{ord(char):02X}>", f"{width - steps * pitch * 1000:.0f}"]
    return "[" + "".join(parts[:-1]) + "] TJ"


def fixed_pitch_cells(path: str) -> str:
    """Cells laid out at a fixed pitch, drawn in a proportional font.

    How the LibreOffice situation sheet came out of Microsoft Print to PDF:
    its cells are laid out in the 0.6 em pitch of a monospaced font but drawn
    in DejaVu Sans, one TJ per line moving each glyph on to the next step.
    MuPDF finds a space behind every narrow letter: "Nbr e de pl aces r est
    ant es". Words are separated by typed spaces, except on one line where a
    blank step stands between "demande" and "de"; "Trafic" is drawn with the
    "fi" ligature, which takes two steps.
    """
    doc = fitz.open()
    lines = (
        [("Nombre de personnes sinistrées", 0)],
        [("Nbre de places restantes", 0)],
        [("Activation du plan communal", 0)],
        [("Nombre de demande", 1), ("de renfort", 0)],
        [(f"Tra{_FI}c routier perturbé", 0)],
    )
    ops = [f"BT /F1 8 Tf 50 {780 - 12 * index} Td {_pitch_tj(line)} ET"
           for index, line in enumerate(lines)]
    _raw_page(doc, {"F1": _HELVETICA_FI}, "\n".join(ops))
    doc.save(path, deflate=True)
    doc.close()
    return path


def overlooked_spaces(path: str) -> str:
    """LaTeX-like prose with a ligature and symbols at word boundaries.

    No space is typed: every word is placed by a cursor move and MuPDF
    guesses the spaces, except after the "fi" ligature ending "défi" and
    after the symbols, where its rule declines to guess any. The relations
    stand a thick space from their neighbours and the binary minus a medium
    one, as in LaTeX; the minus of "−1" is set against its digit, as a sign
    is, and needs no space.
    """
    helv, symb = fitz.Font("helv"), fitz.Font("symb")
    doc = fitz.open()
    ops = []
    x = 50
    for word in ("Le", f"dé{_FI}", "est", "relevé."):
        ops.append(f"BT /F1 10 Tf {x:.2f} 780 Td <{word.encode('latin-1').hex()}> Tj ET")
        x += sum(helv.glyph_advance(0xFB01 if c == _FI else ord(c)) for c in word) * 10 + 3.3
    x = 50
    # (font, code, glyph, gap after in em)
    for font, code, glyph, gap in (
            ("F1", "Soit", "Soit", 0.33), ("F1", "x", "x", 0.278), ("F2", "\xce", "∈", 0.278),
            ("F1", "X", "X", 0.33), ("F1", "et", "et", 0.33), ("F1", "a", "a", 0.222),
            ("F2", "-", "−", 0.222), ("F1", "b", "b", 0.278), ("F1", "=", "=", 0.278),
            ("F2", "-", "−", 0.0), ("F1", "1.", "1.", 0.0)):
        ops.append(f"BT /{font} 10 Tf {x:.2f} 760 Td <{code.encode('latin-1').hex()}> Tj ET")
        face = symb if font == "F2" else helv
        x += sum(face.glyph_advance(ord(c)) for c in glyph) * 10 + gap * 10
    _raw_page(doc, {"F1": _HELVETICA_FI, "F2": _SYMBOL}, "\n".join(ops))
    doc.save(path, deflate=True)
    doc.close()
    return path
