# Copyright (C) 2026 The PDF Teleporter contributors
# Portions derived from PDF Teleporter by F1GBD (Jean-Louis Naudin),
# ADRASEC 77 - FNRASEC, author of the original program and of the .psdi
# format.
# SPDX-License-Identifier: GPL-3.0-only
"""Interoperability tests.

The `.psdi` format is a contract with stations already on the air, running
another implementation. These tests exist to make a compatibility break loud:
if a future change alters the bytes an archive is made of, or stores something
a decoder written against the original specification cannot read, one of these
fails.

Two mechanisms:

*Golden vectors* pin the exact archives produced from fixed inputs. They are
regenerated with `python -m tests.test_interop --update`, which must only ever
be done deliberately, with an explanation of why the format output changed.

*Legacy decoding* reimplements the reading path the way the original did --
plain `struct` and `json`, with `.get(key, default)` for every optional field
-- and asserts it recovers the document. This is what proves the compression
work stayed compatible: keys are omitted from the manifest when they equal
their default, and that is only safe if the reader supplies those defaults.
"""

from __future__ import annotations

import hashlib
import json
import lzma
import os
import struct
import sys
import tempfile
import unittest
import zlib

from psditool import engine, format as psdi

from . import fixtures

VECTOR_FILE = os.path.join(os.path.dirname(__file__), "vectors",
                           "golden.json")

# Fixed inputs, one per interesting shape. Adding a case is fine; changing an
# existing one invalidates its vector and must be done consciously.
CASES = [
    ("report_3p", "text_report", {"quality": "ultra_low"}),
    ("report_3p", "text_report", {"quality": "low"}),
    ("report_3p", "text_report", {"quality": "medium"}),
    ("report_3p", "text_report", {"quality": "high"}),
    ("report_3p", "text_report", {"quality": "medium", "skip_images": True}),
    ("report_3p", "text_report", {"quality": "low", "mode": "image"}),
    ("table_12r", "tiny_table", {"quality": "low"}),
    ("table_12r", "tiny_table", {"quality": "medium"}),
    ("rotated", "rotated_report", {"quality": "low"}),
    ("mono", "monochrome_scan_like", {"quality": "low", "mode": "image"}),
    ("rich", "rich_report", {"quality": "ultra_low"}),
    ("rich", "rich_report", {"quality": "medium"}),
    ("sheet", "bordered_sheet", {"quality": "ultra_low"}),
    ("sheet", "bordered_sheet", {"quality": "medium"}),
    ("graphics", "graphics_report", {"quality": "ultra_low"}),
    ("graphics", "graphics_report", {"quality": "medium"}),
]


def _case_key(name: str, options: dict) -> str:
    parts = [name] + [f"{k}={v}" for k, v in sorted(options.items())]
    return "|".join(parts)


def _build_all(directory: str) -> dict[str, dict]:
    """Produce every case, returning size and digest keyed by case."""
    built: dict[str, str] = {}
    results: dict[str, dict] = {}

    for name, factory, options in CASES:
        if name not in built:
            built[name] = getattr(fixtures, factory)(
                os.path.join(directory, f"{name}.pdf")
            )
        data, _ = engine.pdf_to_archive(built[name], **options)
        results[_case_key(name, options)] = {
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    return results


class TestGoldenVectors(unittest.TestCase):
    def setUp(self):
        if not os.path.exists(VECTOR_FILE):
            self.skipTest(
                f"no vectors yet; create them with "
                f"'python -m tests.test_interop --update'"
            )
        with open(VECTOR_FILE, encoding="utf-8") as handle:
            self.expected = json.load(handle)["vectors"]

    def test_archives_match_the_recorded_vectors(self):
        with tempfile.TemporaryDirectory() as directory:
            actual = _build_all(directory)

        self.assertEqual(
            sorted(actual), sorted(self.expected),
            "the set of cases changed; regenerate the vectors deliberately",
        )

        changed = [
            key for key in self.expected
            if actual[key]["sha256"] != self.expected[key]["sha256"]
        ]
        if changed:
            detail = "\n".join(
                f"  {key}: {self.expected[key]['size']} B "
                f"{self.expected[key]['sha256'][:16]} -> "
                f"{actual[key]['size']} B {actual[key]['sha256'][:16]}"
                for key in changed
            )
            self.fail(
                "archive bytes changed, which breaks interoperability with "
                "stations running another implementation:\n" + detail
            )


class TestLegacyDecoder(unittest.TestCase):
    """Decode the way an implementation written on the original spec would."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.pdf = fixtures.text_report(
            os.path.join(cls._tmp.name, "report.pdf"), pages=2
        )

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @staticmethod
    def _legacy_read(data: bytes) -> dict:
        """A deliberately naive reader: no limits, no cross-checks.

        This mirrors the original implementation, so anything it cannot parse
        is a compatibility break regardless of what our own reader accepts.
        """
        assert data[:4] == b"PSDI", "signature"
        version = struct.unpack_from("<H", data, 4)[0]
        assert version == 1, f"expected version 1, got {version}"

        checksum, manifest_size = struct.unpack_from("<II", data, 6)
        manifest_bytes = lzma.decompress(
            data[14 : 14 + manifest_size], format=lzma.FORMAT_RAW,
            filters=[{"id": lzma.FILTER_LZMA2}],
        )
        assert (zlib.crc32(manifest_bytes) & 0xFFFFFFFF) == checksum, "crc"
        return json.loads(manifest_bytes.decode("utf-8"))

    def test_legacy_reader_parses_our_archives(self):
        for quality in ("ultra_low", "low", "medium", "high"):
            with self.subTest(quality=quality):
                data, _ = engine.pdf_to_archive(self.pdf, quality=quality)
                manifest = self._legacy_read(data)
                self.assertIn("pages", manifest)
                self.assertIn("page_sizes", manifest)
                self.assertEqual(len(manifest["pages"]), 2)

    def test_omitted_keys_are_recovered_from_defaults(self):
        # Span keys equal to their default are left out to save bytes. That is
        # only safe because every reader supplies the default; assert that the
        # values a legacy reader recovers are the real ones.
        data, _ = engine.pdf_to_archive(self.pdf, quality="medium")
        manifest = self._legacy_read(data)

        saw_omitted_font = False
        saw_omitted_box = False

        for page in manifest["pages"]:
            for block in page.get("tb", []):
                for line in block.get("l", []):
                    line_box = line["b"]
                    self.assertEqual(len(line_box), 4)
                    for span in line["s"]:
                        # The defaults the format has always specified.
                        font = span.get("f", "R")
                        family = span.get("fa", "s")
                        colour = span.get("c", 0)
                        box = span.get("b", line_box)

                        self.assertIn(font, ("R", "B", "I", "BI"))
                        self.assertIn(family, ("s", "t", "m"))
                        self.assertIsInstance(colour, int)
                        self.assertEqual(len(box), 4)
                        self.assertIn("t", span, "text is never omitted")
                        self.assertIn("sz", span, "size is never omitted")

                        if "f" not in span or "fa" not in span:
                            saw_omitted_font = True
                        if "b" not in span:
                            saw_omitted_box = True

        self.assertTrue(saw_omitted_font,
                        "no defaulted key was omitted; the optimisation is not "
                        "being exercised")
        self.assertTrue(saw_omitted_box,
                        "no single-span line dropped its box; the optimisation "
                        "is not being exercised")

    def test_tuned_lzma_still_decodes_with_the_plain_filter_spec(self):
        # The manifest is compressed with pb=0 and the extreme match finder.
        # LZMA2 records those in its chunk headers, so a decoder using the
        # bare filter specification must still read it. If that ever stops
        # being true, every other implementation breaks at once.
        data, _ = engine.pdf_to_archive(self.pdf, quality="medium")
        self.assertIsInstance(self._legacy_read(data), dict)

    def test_image_mode_payloads_are_plain_jpeg(self):
        data, _ = engine.pdf_to_archive(self.pdf, quality="low",
                                        mode=engine.MODE_IMAGE)
        pos = 8
        nb_pages = struct.unpack_from("<H", data, 6)[0]
        self.assertGreater(nb_pages, 0)
        for _ in range(nb_pages):
            pos += 8  # width, height
            flags = struct.unpack_from("<B", data, pos)[0]
            pos += 1
            size = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            payload = data[pos : pos + size]
            pos += size
            if not flags & 1:
                # Stored raw, so it must be a JPEG a plain decoder can open.
                self.assertEqual(payload[:2], b"\xff\xd8",
                                 "raw payload is not a JPEG")


class TestFormatConstants(unittest.TestCase):
    """The wire constants are the contract; a typo here breaks every station."""

    def test_constants(self):
        self.assertEqual(psdi.ARCHIVE_MAGIC, b"PSDI")
        self.assertEqual(psdi.ARCHIVE_VERSION_STRUCT, 1)
        self.assertEqual(psdi.ARCHIVE_VERSION_IMAGE, 2)
        self.assertEqual(psdi.FLAG_LZMA, 0x01)

    def test_header_layout(self):
        # Field widths are fixed by the specification.
        self.assertEqual(struct.calcsize("<H"), 2)
        self.assertEqual(struct.calcsize("<I"), 4)
        self.assertEqual(struct.calcsize("<f"), 4)

    def test_preset_values_are_pinned(self):
        # These numbers are part of the contract: a receiving station's
        # expectations about quality are built on them.
        from psditool.presets import QUALITY_PRESETS

        expected = {
            "ultra_low": (72, 20, 6),
            "low": (90, 30, 9),
            "medium": (120, 45, 9),
            "high": (150, 55, 9),
        }
        for name, (dpi, jpeg, lzma_preset) in expected.items():
            preset = QUALITY_PRESETS[name]
            self.assertEqual(preset["dpi"], dpi, name)
            self.assertEqual(preset["jpeg_quality"], jpeg, name)
            self.assertEqual(preset["lzma_preset"], lzma_preset, name)


def _original_engine():
    """F1GBD's psdi_lib 2.1.0, run from the bytecode kept in reference/.

    Returns None when the bytecode is not next to this tree or does not match
    the running interpreter -- a .pyc only loads into the Python version that
    wrote it, 3.13 here. PSDI_REFERENCE_PYC may point at it explicitly.
    """
    import importlib.util
    import logging
    import marshal
    import types

    candidates = [
        os.environ.get("PSDI_REFERENCE_PYC"),
        os.path.join(os.path.dirname(__file__), "..", "..", "reference",
                     "disasm", "psdi_lib.pyc"),
    ]
    path = next((c for c in candidates if c and os.path.exists(c)), None)
    if path is None:
        return None
    with open(path, "rb") as handle:
        if handle.read(16)[:4] != importlib.util.MAGIC_NUMBER:
            return None
        code = marshal.load(handle)
    module = types.ModuleType("psdi_lib")
    module.__file__ = "psdi_lib.py"
    logging.disable(logging.CRITICAL)
    try:
        exec(code, module.__dict__)
    finally:
        logging.disable(logging.NOTSET)
    return module


class TestOriginalDecoder(unittest.TestCase):
    """Our archives through F1GBD's own decoder, executed from its bytecode.

    The legacy-decoder tests above reimplement the original from its
    recovered source; this runs the real thing, so a misreading of that
    source cannot hide a break. Skipped where the bytecode is absent.
    """

    DOCS = ("text_report", "tiny_table", "rich_report", "bordered_sheet",
            "graphics_report")

    @classmethod
    def setUpClass(cls):
        cls.original = _original_engine()
        if cls.original is None:
            raise unittest.SkipTest("reference/disasm/psdi_lib.pyc unavailable "
                                    "for this interpreter")
        cls._tmp = tempfile.TemporaryDirectory()
        cls.paths = {name: getattr(fixtures, name)(os.path.join(cls._tmp.name, name + ".pdf"))
                     for name in cls.DOCS}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @staticmethod
    def _words(pdf_bytes):
        import pymupdf

        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        try:
            return [w[4] for page in doc for w in page.get_text("words")]
        finally:
            doc.close()

    def test_every_archive_rebuilds_with_the_same_text(self):
        for name, path in self.paths.items():
            for quality in ("ultra_low", "medium", "high"):
                with self.subTest(doc=name, quality=quality):
                    data, _ = engine.pdf_to_archive(path, quality=quality)
                    theirs, info = self.original.archive_to_pdf(data)
                    ours, _ = engine.archive_to_pdf(data)
                    self.assertEqual(self._words(theirs), self._words(ours))

    def _render(self, pdf_bytes):
        import pymupdf

        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        try:
            return doc[0].get_pixmap(dpi=144, colorspace=pymupdf.csGRAY)
        finally:
            doc.close()

    def test_borders_reach_its_stations(self):
        data, _ = engine.pdf_to_archive(self.paths["bordered_sheet"], quality="medium")
        pix = self._render(self.original.archive_to_pdf(data)[0])
        x0, y0, cw, ch = 400.6, 100.4, 61.3, 17.9
        for r in range(7):
            values = [pix.pixel(int((x0 + cw / 2) * 2), int((y0 + r * ch) * 2 + d))[0]
                      for d in (-1, 0, 1)]
            self.assertLess(min(values), 180, f"row border {r} missing")

    @staticmethod
    def _glyphs(pdf_bytes):
        import pymupdf

        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        try:
            out = []
            for number, page in enumerate(doc):
                for block in page.get_text("rawdict")["blocks"]:
                    for line in block.get("lines", []):
                        for span in line["spans"]:
                            out.extend((number, char["c"], char["origin"], span["size"])
                                       for char in span["chars"])
            return sorted(out, key=lambda g: (g[0], round(g[2][1]), round(g[2][0], 1), g[1]))
        finally:
            doc.close()

    def test_open_line_boxes_leave_every_glyph_in_place(self):
        # A line that fits its box is stored with the page corner as its
        # bottom-right. The original draws each line through a temporary page
        # of the box's size, so the box enters its matrices: a glyph may move
        # by float rounding, never by more.
        for name in ("text_report", "bordered_sheet"):
            for quality in ("ultra_low", "medium"):
                with self.subTest(doc=name, quality=quality):
                    opened, _ = engine.pdf_to_archive(self.paths[name], quality=quality)
                    original = engine._open_line_box
                    engine._open_line_box = lambda line, *args: line["b"]
                    try:
                        exact, _ = engine.pdf_to_archive(self.paths[name], quality=quality)
                    finally:
                        engine._open_line_box = original
                    a = self._glyphs(self.original.archive_to_pdf(opened)[0])
                    b = self._glyphs(self.original.archive_to_pdf(exact)[0])
                    self.assertEqual([(g[0], g[1]) for g in a], [(g[0], g[1]) for g in b])
                    for ga, gb in zip(a, b):
                        self.assertAlmostEqual(ga[2][0], gb[2][0], delta=0.001)
                        self.assertAlmostEqual(ga[2][1], gb[2][1], delta=0.001)
                        self.assertAlmostEqual(ga[3], gb[3], delta=0.001)

    def test_png_payloads_are_placed(self):
        data, _ = engine.pdf_to_archive(self.paths["graphics_report"], quality="medium")
        theirs = self.original.archive_to_pdf(data)[0]
        import pymupdf

        doc = pymupdf.open(stream=theirs, filetype="pdf")
        try:
            self.assertEqual(len(doc[0].get_images()), 2)
        finally:
            doc.close()
        pix = self._render(theirs)
        # Inside the tallest bar of the chart.
        self.assertLess(pix.pixel(int(200 * 2), int(200 * 2))[0], 230)


def _update_vectors() -> int:
    """Regenerate the golden vectors. Run deliberately, never automatically."""
    os.makedirs(os.path.dirname(VECTOR_FILE), exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        vectors = _build_all(directory)

    payload = {
        "comment": (
            "Golden archive digests. A change here means the bytes on the air "
            "changed, which breaks interoperability with other .psdi "
            "implementations. Regenerate only deliberately."
        ),
        "vectors": vectors,
    }
    with open(VECTOR_FILE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, sort_keys=True)
        handle.write("\n")

    print(f"wrote {len(vectors)} vectors to {VECTOR_FILE}")
    for key in sorted(vectors):
        print(f"  {key:<44} {vectors[key]['size']:>7} B  "
              f"{vectors[key]['sha256'][:16]}")
    return 0


if __name__ == "__main__":
    if "--update" in sys.argv:
        raise SystemExit(_update_vectors())
    unittest.main()
