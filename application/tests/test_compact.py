# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Compact archives (.psdz): container versions 3 and 4.

Three promises are under test. The columnar manifest gives back exactly the
manifest it was made from, or the writer does not use it. A re-coded image is
never less faithful than the JPEG or PNG it replaces, by PSNR and by SSIM. And
a damaged or hostile .psdz is refused with a PsdiError naming the problem, the
same discipline as the version 1 reader.
"""

from __future__ import annotations

import contextlib
import io
import json
import lzma
import os
import struct
import tempfile
import unittest
import zlib
from unittest import mock

from PIL import Image, ImageChops, ImageDraw

from psditool import cli, engine, format as psdi

from . import fixtures

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz


def _manifest(pages: list, sizes: list | None = None, **extra) -> dict:
    manifest = {
        "v": "1.0.6",
        "src": "test.pdf",
        "pages": pages,
        "page_sizes": sizes or [{"w": 595.0, "h": 842.0}] * len(pages),
        "images": {},
    }
    manifest.update(extra)
    return manifest


def _text_page(number: int = 1, **keys) -> dict:
    page = {"pn": number, "tb": [{"l": [
        {"b": [56.0, 60.5, 595.0, 842.0], "s": [{"t": "Titre", "sz": 16.0, "f": "B"}]},
        {"b": [56.0, 80.0, 300.5, 92.0], "s": [
            {"t": "Gauche", "sz": 10.0, "b": [56.0, 80.0, 100.0, 92.0]},
            {"t": "droite", "sz": 10.0, "c": 255, "b": [200.0, 80.0, 300.5, 92.0]},
        ]},
    ]}], "ir": [], "dr": [
        {"t": "rect", "r": [50.0, 100.0, 545.0, 100.0], "c": "#000000", "w": 0.4},
        {"t": "rect", "r": [50.0, 120.0, 545.0, 120.0], "c": "#000000", "w": 0.4},
        {"t": "rect", "r": [50.0, 130.0, 290.0, 154.0], "f": "#d9e1f2"},
    ], "ck": []}
    page.update(keys)
    return page


def _sections(columns: bytes) -> list[bytes]:
    out, pos = [], 0
    while pos < len(columns):
        size = struct.unpack_from("<I", columns, pos)[0]
        out.append(columns[pos + 4:pos + 4 + size])
        pos += 4 + size
    return out


def _assemble(sections: list[bytes]) -> bytes:
    return b"".join(struct.pack("<I", len(s)) + s for s in sections)


def _raw_compact(body: bytes, checksum: int | None = None) -> bytes:
    """A version 3 container around an arbitrary body, no writer checks."""
    compressed = lzma.compress(body, format=lzma.FORMAT_RAW,
                               filters=[{"id": lzma.FILTER_LZMA2, "preset": 6}])
    if checksum is None:
        checksum = zlib.crc32(body) & 0xFFFFFFFF
    return (psdi.ARCHIVE_MAGIC
            + struct.pack("<H", psdi.ARCHIVE_VERSION_COMPACT_STRUCT)
            + struct.pack("<II", checksum, len(compressed))
            + compressed + struct.pack("<H", 0))


def _words(pdf_bytes: bytes) -> list[str]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return [w[4] for page in doc for w in page.get_text("words")]
    finally:
        doc.close()


def _decoded(payload: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(payload))
    image.load()
    return image


class TestColumnarManifest(unittest.TestCase):
    """The columns give back the very manifest they were made from."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.archives = {}
        for factory in ("text_report", "tiny_table", "bordered_sheet",
                        "rich_report", "graphics_report"):
            path = getattr(fixtures, factory)(os.path.join(cls._tmp.name, f"{factory}.pdf"))
            for quality in ("ultra_low", "medium"):
                cls.archives[(factory, quality)] = engine.pdf_to_archive(path, quality=quality)[0]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _round_trip(self, manifest: dict, images: dict | None = None) -> bytes:
        data = psdi.write_compact_struct(manifest, images or {}, 6)
        parsed = psdi.read_compact_struct(data)
        self.assertEqual(parsed.manifest, manifest)
        self.assertEqual(parsed.images, images or {})
        self.assertTrue(parsed.crc_ok)
        return data

    def test_engine_manifests_take_the_columns_and_come_back_equal(self):
        # The columnar path must be the one exercised: a manifest the
        # engine writes and the columns cannot hold would silently fall
        # back to JSON and lose the saving.
        for key, archive in self.archives.items():
            with self.subTest(case=key):
                classic = psdi.read_struct(archive)
                data = self._round_trip(classic.manifest, classic.images)
                self.assertEqual(psdi.compact_body_kind(data), "columns")

    def test_columns_are_smaller_than_the_json_manifest(self):
        for key in (("text_report", "medium"), ("bordered_sheet", "ultra_low"),
                    ("tiny_table", "medium")):
            with self.subTest(case=key):
                classic = psdi.read_struct(self.archives[key])
                compact = psdi.write_compact_struct(classic.manifest, classic.images, 9)
                self.assertLess(len(compact), len(self.archives[key]))

    def test_the_coarsest_fitting_scale_is_chosen(self):
        def scale(manifest):
            header = _sections(psdi._encode_columns(manifest))[0]
            return json.loads(header)["q"]

        whole = _manifest([{"pn": 1, "dr": [{"t": "rect", "r": [1, 2, 30, 40]}]}])
        tenth = _manifest([{"pn": 1, "dr": [{"t": "rect", "r": [1.5, 2, 30, 40]}]}])
        hundredth = _manifest([{"pn": 1, "dr": [{"t": "rect", "r": [1.25, 2, 30, 40]}]}])
        self.assertEqual(scale(whole), 1)
        self.assertEqual(scale(tenth), 10)
        self.assertEqual(scale(hundredth), 100)
        for manifest in (whole, tenth, hundredth):
            # Tested on the columns themselves: the writer may keep the JSON
            # of a manifest this small (see the next test).
            self.assertEqual(psdi._decode_columns(psdi._encode_columns(manifest)), manifest)
            self._round_trip(manifest)

    def test_the_smaller_body_is_kept(self):
        # One rectangle: the columns' section table outweighs the JSON, and
        # the compact archive must not come out larger than the classic one
        # by more than its body-kind byte.
        tiny = _manifest([{"pn": 1, "dr": [{"t": "rect", "r": [1, 2, 30, 40]}]}])
        data = self._round_trip(tiny)
        self.assertEqual(psdi.compact_body_kind(data), "json")
        self.assertLessEqual(len(data), len(psdi.write_struct(tiny, {})) + 1)
        # A real page keeps the columns, which are far smaller.
        classic = psdi.read_struct(self.archives[("text_report", "medium")])
        data = self._round_trip(classic.manifest, classic.images)
        self.assertEqual(psdi.compact_body_kind(data), "columns")

    def test_what_the_columns_cannot_hold_goes_to_json_unchanged(self):
        cases = {
            "finer coordinates": _manifest([{"pn": 1, "dr": [
                {"t": "rect", "r": [1.2345, 2, 30, 40]}]}]),
            "unknown page key": _manifest([_text_page(zz=[1, 2])]),
            "unknown span key": _manifest([{"pn": 1, "tb": [{"l": [
                {"b": [1, 2, 3, 4], "s": [{"t": "x", "zz": 1}]}]}]}]),
            "unknown drawing key": _manifest([{"pn": 1, "dr": [
                {"t": "rect", "r": [1, 2, 3, 4], "zz": True}]}]),
            "nested style value": _manifest([{"pn": 1, "dr": [
                {"t": "rect", "r": [1, 2, 3, 4], "f": [1, 2]}]}]),
            "mixed reference types": _manifest([{"pn": 1, "ir": [
                {"x": "12", "b": [1, 2, 3, 4]}, {"x": 13, "b": [1, 2, 3, 4]}]}]),
            "non-canonical reference": _manifest([{"pn": 1, "ir": [
                {"x": "012", "b": [1, 2, 3, 4]}]}]),
        }
        for name, manifest in cases.items():
            with self.subTest(case=name):
                self.assertEqual(psdi.compact_body_kind(self._round_trip(manifest)), "json")

    def test_every_page_feature_survives(self):
        many = [{"t": f"m{i}", "sz": 8.0, "b": [10.0 * i, 0.0, 10.0 * i + 9, 9.0]}
                for i in range(20)]
        page = _text_page(
            ir=[{"x": "65500", "b": [10.0, 20.0, 110.0, 90.0]}],
            ck=[[100.0, 200.0, 108.0, 208.0]],
            lk=[{"b": [56.0, 80.0, 300.5, 92.0], "u": "https://example.org/a?b=1&c=é"}],
        )
        page["tb"][0]["l"].append({"b": [0.0, 300.0, 200.0, 310.0], "s": many})
        second = {"pn": 2, "tb": [], "ir": [], "dr": [], "ck": []}
        manifest = _manifest([page, second],
                             images={"65500": {"w": 100, "h": 70, "s": 1234}})
        data = self._round_trip(manifest)
        self.assertEqual(psdi.compact_body_kind(data), "columns")

    def test_integer_references_stay_integers(self):
        manifest = _manifest([{"pn": 1, "ir": [{"x": 12, "b": [1, 2, 3, 4]}]}])
        self._round_trip(manifest)
        parsed = psdi.read_compact_struct(psdi.write_compact_struct(manifest, {}, 6))
        self.assertIsInstance(parsed.manifest["pages"][0]["ir"][0]["x"], int)

    def test_any_text_survives(self):
        strings = ["é à ç œ", "日本語", "😀 emoji", "nul\x00inside", "", " ",
                   "‮right-to-left", "tab\tand\nnewline"]
        lines = [{"b": [10.0, 10.0 * i, 595.0, 842.0], "s": [{"t": text}]}
                 for i, text in enumerate(strings)]
        self._round_trip(_manifest([{"pn": 1, "tb": [{"l": lines}]}]))


class TestCompactContainerRejectsDamage(unittest.TestCase):
    """A .psdz from the air is untrusted input, like a .psdi."""

    def setUp(self):
        self.manifest = _manifest([_text_page(lk=[{"b": [1.0, 2.0, 3.0, 4.0], "u": "u"}])])
        self.columns = psdi._encode_columns(self.manifest)
        self.archive = psdi.write_compact_struct(self.manifest, {"7": b"payload"}, 6)

    def _body(self, sections: list[bytes]) -> bytes:
        return _raw_compact(bytes([psdi._BODY_COLUMNS]) + _assemble(sections))

    def _refused(self, data: bytes) -> str:
        with self.assertRaises(psdi.PsdiError) as caught:
            psdi.read_compact_struct(data)
        report = psdi.validate(data)
        self.assertFalse(report["valid"])
        return str(caught.exception)

    def test_the_untouched_archive_reads(self):
        parsed = psdi.read_compact_struct(self.archive)
        self.assertEqual(parsed.manifest, self.manifest)
        self.assertEqual(parsed.images, {"7": b"payload"})

    def test_every_prefix_is_refused_cleanly(self):
        for length in range(0, len(self.archive) - 1, 3):
            with self.subTest(length=length):
                data = self.archive[:length]
                report = psdi.validate(data)
                self.assertFalse(report["valid"])
                if length >= 6:
                    with self.assertRaises(psdi.PsdiError):
                        psdi.read_compact_struct(data)

    def test_unknown_body_encoding(self):
        self.assertIn("encoding", self._refused(_raw_compact(b"\x07" + self.columns)))

    def test_section_running_past_the_end(self):
        bad = struct.pack("<I", 10_000) + b"{}"
        self._refused(_raw_compact(bytes([psdi._BODY_COLUMNS]) + bad))

    def test_oversized_number(self):
        sections = _sections(self.columns)
        sections[1] = b"\x80" * 11 + b"\x00"
        self.assertIn("oversized", self._refused(self._body(sections)))

    def test_stream_ending_early(self):
        sections = _sections(self.columns)
        sections[1] = sections[1][:2]
        self.assertIn("ends early", self._refused(self._body(sections)))

    def test_style_index_out_of_range(self):
        sections = _sections(self.columns)
        sections[4] = b"\x7f" + sections[4][1:]
        self.assertIn("does not exist", self._refused(self._body(sections)))

    def test_data_left_over(self):
        sections = _sections(self.columns)
        sections[2] = sections[2] + b"\x00"
        self.assertIn("left over", self._refused(self._body(sections)))

    def test_text_that_is_not_utf8(self):
        sections = _sections(self.columns)
        sections[-1] = b"\xff" * len(sections[-1])
        self.assertIn("UTF-8", self._refused(self._body(sections)))

    def test_malformed_header(self):
        sections = _sections(self.columns)
        header = json.loads(sections[0])
        for name, change in (("not JSON", b"{"),
                             ("scale", dict(header, q=3)),
                             ("page count", dict(header, n=psdi.MAX_PAGES + 1)),
                             ("style", dict(header, ss=[{"zz": 1}])),
                             ("pages twice", dict(header, m=dict(header["m"], pages=[])))):
            with self.subTest(case=name):
                altered = list(sections)
                altered[0] = (change if isinstance(change, bytes)
                              else json.dumps(change).encode())
                self._refused(self._body(altered))

    def test_declared_size_above_the_ceiling(self):
        data = bytearray(self.archive)
        struct.pack_into("<I", data, 10, psdi.MAX_MANIFEST_SIZE + 1)
        self.assertIn("ceiling", self._refused(bytes(data)))

    def test_bad_crc_is_reported_not_fatal(self):
        body = bytes([psdi._BODY_COLUMNS]) + self.columns
        parsed = psdi.read_compact_struct(_raw_compact(body, checksum=1))
        self.assertFalse(parsed.crc_ok)
        self.assertEqual(parsed.manifest, self.manifest)

    def test_validate_names_the_compact_versions(self):
        report = psdi.validate(self.archive)
        self.assertTrue(report["valid"], report["error"])
        self.assertEqual(report["version"], psdi.ARCHIVE_VERSION_COMPACT_STRUCT)
        pages = psdi.write_image([(595.0, 842.0, b"\xff\xd8\xff\xe0jpeg")], 6,
                                 psdi.ARCHIVE_VERSION_COMPACT_IMAGE)
        report = psdi.validate(pages)
        self.assertTrue(report["valid"], report["error"])
        self.assertEqual(report["version"], psdi.ARCHIVE_VERSION_COMPACT_IMAGE)

    def test_extensions_follow_the_version(self):
        self.assertEqual(psdi.extension_for(psdi.ARCHIVE_VERSION_STRUCT), ".psdi")
        self.assertEqual(psdi.extension_for(psdi.ARCHIVE_VERSION_IMAGE), ".psdi")
        self.assertEqual(psdi.extension_for(psdi.ARCHIVE_VERSION_COMPACT_STRUCT), ".psdz")
        self.assertEqual(psdi.extension_for(psdi.ARCHIVE_VERSION_COMPACT_IMAGE), ".psdz")


class TestPayloadKind(unittest.TestCase):
    def test_signatures(self):
        image = Image.new("RGB", (16, 12), (200, 30, 40))
        for fmt, options, kind in (
            ("JPEG", {}, "jpeg"), ("PNG", {}, "png"),
            ("WEBP", {"quality": 50}, "webp"), ("WEBP", {"lossless": True}, "webp"),
            ("AVIF", {"quality": 50}, "avif"),
        ):
            with self.subTest(fmt=fmt, options=options):
                self.assertEqual(psdi.payload_kind(engine._image_bytes(
                    image, format=fmt, **options)), kind)

    def test_brands_and_junk(self):
        def ftyp(major: bytes, *compatible: bytes) -> bytes:
            box = b"ftyp" + major + b"\x00\x00\x00\x00" + b"".join(compatible)
            return struct.pack(">I", len(box) + 4) + box + b"rest"

        self.assertEqual(psdi.payload_kind(ftyp(b"mif1", b"avif", b"miaf")), "avif")
        self.assertIsNone(psdi.payload_kind(ftyp(b"heic", b"mif1")))
        self.assertIsNone(psdi.payload_kind(b""))
        self.assertIsNone(psdi.payload_kind(b"RIFF\x00\x00\x00\x00WAVE"))


class TestSsim(unittest.TestCase):
    def test_identity_and_degradation(self):
        base = Image.new("RGB", (64, 48))
        draw = ImageDraw.Draw(base)
        for i in range(0, 64, 4):
            draw.line([i, 0, 63 - i, 47], fill=(i * 4, 255 - i * 4, 128), width=2)
        self.assertAlmostEqual(engine._ssim(base, base), 1.0, places=5)
        blurred = _decoded(engine._image_bytes(base, format="JPEG", quality=10))
        self.assertLess(engine._ssim(base, blurred), 0.99)
        grey = base.convert("L")
        self.assertAlmostEqual(engine._ssim(grey, grey), 1.0, places=5)

    def test_images_smaller_than_the_window(self):
        tiny = Image.new("L", (5, 5), 100)
        self.assertEqual(engine._ssim(tiny, tiny), 1.0)
        self.assertEqual(engine._ssim(tiny, Image.new("L", (5, 5), 101)), 0.0)

    @staticmethod
    def _textbook_ssim(reference, candidate):
        """SSIM by the definition: every 7x7 window, sample statistics, floats."""
        c1, c2, n = (0.01 * 255) ** 2, (0.03 * 255) ** 2, 49
        width, height = reference.size
        scores = []
        for x_band, y_band in zip(reference.split(),
                                  candidate.convert(reference.mode).split()):
            xs_all, ys_all = list(x_band.getdata()), list(y_band.getdata())
            total = count = 0
            for top in range(height - 6):
                for left in range(width - 6):
                    xs = [xs_all[(top + j) * width + left + i] for j in range(7) for i in range(7)]
                    ys = [ys_all[(top + j) * width + left + i] for j in range(7) for i in range(7)]
                    mx, my = sum(xs) / n, sum(ys) / n
                    vx = sum((v - mx) ** 2 for v in xs) / (n - 1)
                    vy = sum((v - my) ** 2 for v in ys) / (n - 1)
                    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / (n - 1)
                    total += (((2 * mx * my + c1) * (2 * cov + c2))
                              / ((mx * mx + my * my + c1) * (vx + vy + c2)))
                    count += 1
            scores.append(total / count)
        return sum(scores) / len(scores)

    def test_matches_the_standard_definition(self):
        # The measure is the encoder's promise, so it must be the standard
        # one: an approximation over non-overlapping windows disagreed with
        # it on small and thin images and let worse encodings through.
        photo = Image.new("RGB", (23, 17))
        draw = ImageDraw.Draw(photo)
        for i in range(0, 23, 3):
            draw.line([i, 0, 22 - i, 16], fill=(i * 11, 200 - i * 7, 90 + i * 5), width=1)
        thin = photo.convert("L").resize((60, 9))
        for reference in (photo, thin):
            for quality in (5, 40):
                candidate = _decoded(engine._image_bytes(reference, format="WEBP",
                                                         quality=quality))
                with self.subTest(size=reference.size, mode=reference.mode, quality=quality):
                    self.assertAlmostEqual(engine._ssim(reference, candidate),
                                           self._textbook_ssim(reference, candidate),
                                           delta=1e-6)


class TestCompactImages(unittest.TestCase):
    """Re-coded images are never less faithful, and rebuild where they belong."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.rich = fixtures.rich_report(os.path.join(cls._tmp.name, "rich.pdf"))
        cls.graphics = fixtures.graphics_report(os.path.join(cls._tmp.name, "graphics.pdf"))
        cls.choices = []
        original = engine._compact_payload

        def spy(img, payload, lzma_preset):
            out = original(img, payload, lzma_preset)
            cls.choices.append((img.copy(), payload, out, lzma_preset))
            return out

        engine._compact_payload = spy
        try:
            cls.compact = {}
            for name, path in (("rich", cls.rich), ("graphics", cls.graphics)):
                for quality in ("ultra_low", "medium", "high"):
                    cls.compact[(name, quality)] = engine.pdf_to_archive(
                        path, quality=quality, compact=True)[0]
        finally:
            engine._compact_payload = original

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_some_images_were_re_coded(self):
        kinds = {psdi.payload_kind(after) for _, _, after, _ in self.choices}
        self.assertIn("avif", kinds)
        self.assertIn("webp", kinds)

    def test_a_re_coded_image_is_smaller_and_no_less_faithful(self):
        for img, before, after, lzma_preset in self.choices:
            if after == before:
                continue
            with self.subTest(size=img.size, before=psdi.payload_kind(before),
                              after=psdi.payload_kind(after)):
                stored_before = len(psdi.pack_payload(before, lzma_preset)[1])
                stored_after = len(psdi.pack_payload(after, lzma_preset)[1])
                self.assertLess(stored_after, stored_before)
                old, new = _decoded(before), _decoded(after)
                self.assertGreaterEqual(engine._psnr(img, new), engine._psnr(img, old))
                self.assertGreaterEqual(engine._ssim(img, new), engine._ssim(img, old))

    def test_graphics_keep_their_exact_pixels(self):
        checked = 0
        for _, before, after, _ in self.choices:
            if psdi.payload_kind(before) == "png" and psdi.payload_kind(after) == "webp":
                old = _decoded(before).convert("RGB")
                new = _decoded(after).convert("RGB")
                self.assertIsNone(ImageChops.difference(old, new).getbbox())
                checked += 1
        self.assertGreater(checked, 0, "no graphic was re-coded; the test saw nothing")

    def test_photographs_shrink(self):
        classic, _ = engine.pdf_to_archive(self.rich, quality="medium")
        self.assertLess(len(self.compact[("rich", "medium")]), len(classic) * 0.6)

    def test_rebuild_gives_the_same_text_as_the_classic_archive(self):
        for name, path in (("rich", self.rich), ("graphics", self.graphics)):
            with self.subTest(document=name):
                classic, _ = engine.pdf_to_archive(path, quality="medium")
                compact = self.compact[(name, "medium")]
                pdf_classic, _ = engine.archive_to_pdf(classic)
                pdf_compact, info = engine.archive_to_pdf(compact)
                self.assertEqual(_words(pdf_compact), _words(pdf_classic))
                self.assertTrue(info["compact"])
                self.assertEqual(info["version"], psdi.ARCHIVE_VERSION_COMPACT_STRUCT)

    def test_rebuilt_page_shows_the_decoded_pixels(self):
        archive = self.compact[("rich", "medium")]
        parsed = psdi.read_compact_struct(archive)
        avif = [p for p in parsed.images.values() if psdi.payload_kind(p) == "avif"]
        self.assertTrue(avif)
        expected = _decoded(avif[0]).convert("RGB")
        pdf_bytes, _ = engine.archive_to_pdf(archive)
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            sizes = []
            for xref, *_ in doc[0].get_images(full=True):
                raw = doc.extract_image(xref)
                placed = Image.open(io.BytesIO(raw["image"])).convert("RGB")
                sizes.append(placed.size)
                if placed.size == expected.size:
                    self.assertIsNone(ImageChops.difference(placed, expected).getbbox())
                    return
            self.fail(f"no placed image of {expected.size}; found {sizes}")
        finally:
            doc.close()

    def test_a_damaged_image_costs_that_image_not_the_page(self):
        parsed = psdi.read_compact_struct(self.compact[("rich", "medium")])
        images = {key: (payload[:40] if psdi.payload_kind(payload) == "avif" else payload)
                  for key, payload in parsed.images.items()}
        damaged = psdi.write_compact_struct(parsed.manifest, images, 6)
        with self.assertLogs("psditool.engine", level="WARNING"):
            pdf_bytes, info = engine.archive_to_pdf(damaged)
        self.assertEqual(info["pages"], 1)
        self.assertIn("Moyens", " ".join(_words(pdf_bytes)))

    def test_a_missing_codec_is_said_before_anything_is_rebuilt(self):
        # Skipped one by one like damaged payloads, the photographs would all
        # be missing from the PDF with only a log line to say why. A station
        # whose Pillow cannot read AVIF is told to update it instead.
        archive = self.compact[("rich", "medium")]
        kinds = {psdi.payload_kind(p)
                 for p in psdi.read_compact_struct(archive).images.values()}
        self.assertIn("avif", kinds)
        with mock.patch.object(engine, "_codec_available", lambda name: name != "avif"):
            with self.assertRaises(psdi.PsdiError) as caught:
                engine.archive_to_pdf(archive)
        self.assertIn("Pillow 11.3", str(caught.exception))
        # A classic archive of the same document needs no such codec.
        classic, _ = engine.pdf_to_archive(self.rich, quality="medium")
        with mock.patch.object(engine, "_codec_available", lambda name: False):
            self.assertTrue(engine.archive_to_pdf(classic)[0].startswith(b"%PDF"))

    def test_decoded_size_ceiling(self):
        payload = engine._image_bytes(Image.new("RGB", (40, 40)), format="WEBP")
        saved = engine._MAX_DECODED_PIXELS
        engine._MAX_DECODED_PIXELS = 1000
        try:
            with self.assertRaises(psdi.PsdiError):
                engine._pixmap_from(payload)
        finally:
            engine._MAX_DECODED_PIXELS = saved

    def test_a_pillow_without_avif_still_writes_a_compact_archive(self):
        saved = engine._codec_available
        engine._codec_available = lambda name: name != "avif"
        try:
            data, _ = engine.pdf_to_archive(self.rich, quality="medium", compact=True)
            parsed = psdi.read_compact_struct(data)
            self.assertNotIn("avif", {psdi.payload_kind(p) for p in parsed.images.values()})
            avif = engine._image_bytes(Image.new("RGB", (8, 8)), format="AVIF")
            with self.assertRaises(psdi.PsdiError) as caught:
                engine._pixmap_from(avif)
            self.assertIn("11.3", str(caught.exception))
        finally:
            engine._codec_available = saved

    def test_matching_never_returns_a_failing_encoding(self):
        img = Image.new("RGB", (48, 48))
        ImageDraw.Draw(img).ellipse([4, 4, 44, 44], fill=(250, 120, 30))
        unreachable = engine._matching_quality(
            img, lambda q: engine._image_bytes(img, format="WEBP", quality=q), 200.0, 1.0)
        self.assertIsNone(unreachable)


class TestCompactPageImages(unittest.TestCase):
    """Scans and photos: page-image archives, version 4."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.rotated = fixtures.rotated_report(os.path.join(cls._tmp.name, "rot.pdf"))
        base = Image.new("RGB", (900, 1200), (236, 233, 226))
        draw = ImageDraw.Draw(base)
        for i in range(14):
            draw.text((80, 80 + i * 70), f"Ligne {i} du formulaire", fill=(30, 30, 40))
        cls.photo = os.path.join(cls._tmp.name, "scan.jpg")
        base.save(cls.photo, quality=90)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_rotated_document_becomes_a_smaller_version_4(self):
        classic, _ = engine.pdf_to_archive(self.rotated, quality="low")
        compact, info = engine.pdf_to_archive(self.rotated, quality="low", compact=True)
        self.assertEqual(info["mode"], engine.MODE_IMAGE)
        self.assertEqual(psdi.peek_version(compact), psdi.ARCHIVE_VERSION_COMPACT_IMAGE)
        self.assertLess(len(compact), len(classic))
        pages = psdi.read_image(compact).pages
        self.assertEqual({psdi.payload_kind(p) for _, _, p in pages}, {"avif"})

        pdf_classic, _ = engine.archive_to_pdf(classic)
        pdf_compact, rebuilt = engine.archive_to_pdf(compact)
        self.assertEqual(rebuilt["version"], psdi.ARCHIVE_VERSION_COMPACT_IMAGE)
        a = fitz.open(stream=pdf_classic, filetype="pdf")
        b = fitz.open(stream=pdf_compact, filetype="pdf")
        try:
            self.assertEqual([p.rect for p in a], [p.rect for p in b])
        finally:
            a.close()
            b.close()

    def test_a_missing_codec_refuses_a_page_image_archive(self):
        compact, _ = engine.pdf_to_archive(self.photo, quality="low", compact=True)
        with mock.patch.object(engine, "_codec_available", lambda name: name != "avif"):
            with self.assertRaises(psdi.PsdiError) as caught:
                engine.archive_to_pdf(compact)
        self.assertIn("Pillow 11.3", str(caught.exception))

    def test_image_file_becomes_version_4(self):
        compact, info = engine.pdf_to_archive(self.photo, quality="low", compact=True)
        self.assertEqual(psdi.peek_version(compact), psdi.ARCHIVE_VERSION_COMPACT_IMAGE)
        self.assertEqual(info["extension"], ".psdz")
        self.assertTrue(engine.archive_to_pdf(compact)[0].startswith(b"%PDF"))

    def test_classic_archives_are_untouched_by_the_option(self):
        a, info = engine.pdf_to_archive(self.photo, quality="low")
        b, _ = engine.pdf_to_archive(self.photo, quality="low", compact=False)
        self.assertEqual(a, b)
        self.assertFalse(info["compact"])
        self.assertEqual(info["extension"], ".psdi")
        self.assertEqual(psdi.peek_version(a), psdi.ARCHIVE_VERSION_IMAGE)


class TestCompactCommandLine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.report = fixtures.text_report(os.path.join(cls._tmp.name, "report.pdf"), pages=1)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_default_names(self):
        self.assertEqual(cli.default_archive_path("dir/a.pdf", False), "dir/a.psdi")
        self.assertEqual(cli.default_archive_path("dir/a.pdf", True), "dir/a.psdz")

    def test_compress_compact_writes_a_psdz(self):
        target = os.path.splitext(self.report)[0] + ".psdz"
        self.assertEqual(cli.cmd_compress(self.report, "low", None, False, True,
                                          compact=True), 0)
        with open(target, "rb") as handle:
            self.assertEqual(psdi.peek_version(handle.read()),
                             psdi.ARCHIVE_VERSION_COMPACT_STRUCT)

    def test_subcommand_flag(self):
        target = os.path.join(self._tmp.name, "named.psdz")
        self.assertEqual(cli.main(["--quiet", "compress", self.report, "--compact",
                                   "-o", target]), 0)
        self.assertTrue(os.path.exists(target))

    def test_context_menu_shorthand_honours_compact(self):
        copy = os.path.join(self._tmp.name, "menu.pdf")
        with open(self.report, "rb") as src, open(copy, "wb") as dst:
            dst.write(src.read())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["--compress", copy, "--quality", "low",
                                       "--compact"]), 0)
        self.assertIn(".psdz", out.getvalue())
        self.assertTrue(os.path.exists(os.path.join(self._tmp.name, "menu.psdz")))
        self.assertFalse(os.path.exists(os.path.join(self._tmp.name, "menu.psdi")))

    def test_inspect_names_the_format(self):
        target = os.path.join(self._tmp.name, "inspect.psdz")
        engine.pdf_to_archive(self.report, quality="low", compact=True, output_path=target)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.cmd_inspect(target), 0)
        self.assertIn(".psdz", out.getvalue())


if __name__ == "__main__":
    unittest.main()
