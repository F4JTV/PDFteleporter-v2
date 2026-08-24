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
