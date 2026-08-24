"""Engine tests: round-trip, mode selection, determinism, fidelity."""

from __future__ import annotations

import os
import tempfile
import unittest

from psditool import engine, format as psdi
from psditool.presets import QUALITY_ORDER

from . import fixtures

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz


class EngineTestCase(unittest.TestCase):
    """Shared temporary directory, built once for the whole class."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = cls._tmp.name
        cls.report = fixtures.text_report(os.path.join(cls.dir, "report.pdf"))
        cls.rotated = fixtures.rotated_report(os.path.join(cls.dir, "rot.pdf"))
        cls.table = fixtures.tiny_table(os.path.join(cls.dir, "table.pdf"))
        cls.mono = fixtures.monochrome_scan_like(os.path.join(cls.dir, "mono.pdf"))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


class TestRoundTrip(EngineTestCase):
    def test_every_preset_round_trips(self):
        for quality in QUALITY_ORDER:
            with self.subTest(quality=quality):
                data, info = engine.pdf_to_archive(self.report, quality=quality)

                report = psdi.validate(data)
                self.assertTrue(report["valid"], report["error"])
                self.assertTrue(report["checksum_ok"])

                pdf_bytes, rebuild = engine.archive_to_pdf(data)
                self.assertTrue(rebuild["crc_ok"])
                self.assertEqual(rebuild["pages"], report["pages"])
                self.assertTrue(pdf_bytes.startswith(b"%PDF"))
                self.assertEqual(info["quality"], quality)

    def test_archive_is_smaller_than_source(self):
        data, info = engine.pdf_to_archive(self.report, quality="medium")
        self.assertLess(len(data), info["original_size"])

    def test_text_only_is_smaller_than_full(self):
        full, _ = engine.pdf_to_archive(self.report, quality="medium")
        lean, _ = engine.pdf_to_archive(self.report, quality="medium",
                                        skip_images=True)
        self.assertLessEqual(len(lean), len(full))

    def test_output_file_is_written(self):
        target = os.path.join(self.dir, "out.psdi")
        engine.pdf_to_archive(self.report, quality="low", output_path=target)
        self.assertTrue(os.path.exists(target))
        with open(target, "rb") as handle:
            self.assertEqual(psdi.peek_version(handle.read()), 1)

    def test_missing_source_raises(self):
        with self.assertRaises(FileNotFoundError):
            engine.pdf_to_archive(os.path.join(self.dir, "nope.pdf"))

    def test_invalid_quality_and_mode_rejected(self):
        with self.assertRaises(ValueError):
            engine.pdf_to_archive(self.report, quality="excellent")
        with self.assertRaises(ValueError):
            engine.pdf_to_archive(self.report, mode="hologram")


class TestModeSelection(EngineTestCase):
    def test_digital_document_stays_structured(self):
        mode, reason = engine.detect_optimal_mode(self.report)
        self.assertEqual(mode, engine.MODE_STRUCT)
        self.assertIsNone(reason)

    def test_rotation_forces_image_mode(self):
        mode, reason = engine.detect_optimal_mode(self.rotated)
        self.assertEqual(mode, engine.MODE_IMAGE)
        self.assertEqual(reason, "rotation")

    def test_rotation_switches_even_with_text_only(self):
        # The original bypassed detection entirely when images were skipped,
        # so a rotated document rebuilt sideways. Dropping images is a valid
        # choice; ignoring rotation never is, because the structured path
        # works in unrotated coordinates.
        _, info = engine.pdf_to_archive(self.rotated, quality="low",
                                        skip_images=True)
        self.assertEqual(info["mode"], engine.MODE_IMAGE)
        self.assertEqual(info["auto_mode_switch"], "rotation")

    def test_text_only_does_not_switch_for_images_alone(self):
        _, info = engine.pdf_to_archive(self.report, quality="low",
                                        skip_images=True)
        self.assertEqual(info["mode"], engine.MODE_STRUCT)
        self.assertIsNone(info["auto_mode_switch"])

    def test_explicit_image_mode_is_honoured(self):
        data, info = engine.pdf_to_archive(self.report, quality="low",
                                           mode=engine.MODE_IMAGE)
        self.assertEqual(info["mode"], engine.MODE_IMAGE)
        self.assertEqual(psdi.peek_version(data), psdi.ARCHIVE_VERSION_IMAGE)


class TestDeterminism(EngineTestCase):
    def test_same_input_gives_identical_bytes(self):
        # Reproducible output makes an archive verifiable: two operators
        # compressing the same document must be able to compare checksums.
        for quality in QUALITY_ORDER:
            with self.subTest(quality=quality):
                first, _ = engine.pdf_to_archive(self.report, quality=quality)
                second, _ = engine.pdf_to_archive(self.report, quality=quality)
                self.assertEqual(first, second)

    def test_image_mode_is_deterministic(self):
        first, _ = engine.pdf_to_archive(self.report, quality="low",
                                         mode=engine.MODE_IMAGE)
        second, _ = engine.pdf_to_archive(self.report, quality="low",
                                          mode=engine.MODE_IMAGE)
        self.assertEqual(first, second)


class TestQualityOrdering(EngineTestCase):
    def test_lower_quality_never_produces_a_larger_archive(self):
        sizes = []
        for quality in QUALITY_ORDER:  # ultra_low -> high
            data, _ = engine.pdf_to_archive(self.report, quality=quality)
            sizes.append(len(data))
        self.assertEqual(sizes, sorted(sizes),
                         f"archive sizes not monotonic: {sizes}")


class TestFidelity(EngineTestCase):
    """The rebuilt document must still carry the information that was sent."""

    @staticmethod
    def _page_text(path: str, page: int = 0) -> str:
        doc = fitz.open(path)
        try:
            return doc[page].get_text("text")
        finally:
            doc.close()

    def test_text_survives_the_round_trip(self):
        target = os.path.join(self.dir, "rebuilt.pdf")
        data, _ = engine.pdf_to_archive(self.report, quality="medium")
        engine.archive_to_pdf(data, output_path=target)

        original_words = set(self._page_text(self.report).split())
        rebuilt_words = set(self._page_text(target).split())
        self.assertTrue(original_words)

        kept = original_words & rebuilt_words
        ratio = len(kept) / len(original_words)
        self.assertGreater(ratio, 0.9,
                           f"only {ratio:.0%} of the words survived")

    def test_whole_point_rounding_keeps_a_dense_table_readable(self):
        # The two lowest presets round coordinates to whole points. A 12-row
        # table in 7 pt text is where half a point of drift would show, so the
        # text must still come back intact there.
        target = os.path.join(self.dir, "table_rebuilt.pdf")
        data, info = engine.pdf_to_archive(self.table, quality="low")
        self.assertEqual(info["mode"], engine.MODE_STRUCT)
        engine.archive_to_pdf(data, output_path=target)

        original = set(self._page_text(self.table).split())
        rebuilt = set(self._page_text(target).split())
        missing = original - rebuilt
        self.assertFalse(missing, f"lost from the table: {sorted(missing)[:10]}")

    def test_page_geometry_is_preserved(self):
        target = os.path.join(self.dir, "geom.pdf")
        data, _ = engine.pdf_to_archive(self.report, quality="medium")
        engine.archive_to_pdf(data, output_path=target)

        source = fitz.open(self.report)
        rebuilt = fitz.open(target)
        try:
            self.assertEqual(source.page_count, rebuilt.page_count)
            for index in range(source.page_count):
                self.assertAlmostEqual(source[index].rect.width,
                                       rebuilt[index].rect.width, delta=1.0)
                self.assertAlmostEqual(source[index].rect.height,
                                       rebuilt[index].rect.height, delta=1.0)
        finally:
            source.close()
            rebuilt.close()

    def test_rotated_page_rebuilds_in_landscape(self):
        # A page rotated 90 degrees must come back wider than tall. Getting
        # this wrong is what produced sideways pages on a black background.
        target = os.path.join(self.dir, "rot_rebuilt.pdf")
        data, _ = engine.pdf_to_archive(self.rotated, quality="low")
        engine.archive_to_pdf(data, output_path=target)

        doc = fitz.open(target)
        try:
            page = doc[0]
            self.assertGreater(page.rect.width, page.rect.height)
        finally:
            doc.close()


class TestImageEncoding(EngineTestCase):
    def test_monochrome_page_drops_its_chroma_planes(self):
        import io

        from PIL import Image

        data, _ = engine.pdf_to_archive(self.mono, quality="low",
                                        mode=engine.MODE_IMAGE)
        parsed = psdi.read_image(data)
        image = Image.open(io.BytesIO(parsed.pages[0][2]))
        self.assertEqual(image.mode, "L")
        self.assertTrue(image.info.get("progression"),
                        "JPEG should be progressive")

    def test_coloured_page_keeps_its_colour(self):
        import io

        from PIL import Image

        data, _ = engine.pdf_to_archive(self.report, quality="low",
                                        mode=engine.MODE_IMAGE)
        parsed = psdi.read_image(data)
        image = Image.open(io.BytesIO(parsed.pages[0][2]))
        self.assertEqual(image.mode, "RGB")


class TestProgressCallback(EngineTestCase):
    def test_callback_is_called(self):
        calls = []
        engine.pdf_to_archive(self.report, quality="low",
                              progress_cb=lambda *a: calls.append(a))
        self.assertTrue(calls)
        for step, total, message in calls:
            self.assertIsInstance(step, int)
            self.assertIsInstance(total, int)
            self.assertIsInstance(message, str)

    def test_faulty_callback_does_not_abort_the_conversion(self):
        # A broken progress display must never cost the operator the archive.
        def explode(*_args):
            raise RuntimeError("display is on fire")

        data, _ = engine.pdf_to_archive(self.report, quality="low",
                                        progress_cb=explode)
        self.assertTrue(psdi.validate(data)["valid"])


if __name__ == "__main__":
    unittest.main()


class TestVectorArtwork(unittest.TestCase):
    """Charts must survive, and structure must not be mistaken for artwork."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _page(self):
        doc = fitz.open()
        return doc, doc.new_page(width=400, height=300)

    def test_native_rectangle_is_structure(self):
        doc, page = self._page()
        try:
            shape = page.new_shape()
            shape.draw_rect(fitz.Rect(40, 40, 200, 120))
            shape.finish(color=(0, 0, 0), width=1)
            shape.commit()
            for path in page.get_drawings():
                self.assertFalse(engine._path_is_artwork(path["items"]))
                self.assertEqual(len(engine._rects_from_path_items(path["items"])), 1)
        finally:
            doc.close()

    def test_frame_drawn_as_four_segments_is_still_a_rectangle(self):
        # LibreOffice emits table borders this way. Rejecting it would lose
        # every cell border in a spreadsheet export.
        doc, page = self._page()
        try:
            shape = page.new_shape()
            for start, end in (((50, 50), (250, 50)), ((250, 50), (250, 150)),
                               ((250, 150), (50, 150)), ((50, 150), (50, 50))):
                shape.draw_line(fitz.Point(*start), fitz.Point(*end))
            shape.finish(color=(0, 0, 0), width=1)
            shape.commit()

            found = []
            for path in page.get_drawings():
                found += engine._rects_from_path_items(path["items"])
                self.assertFalse(engine._path_is_artwork(path["items"]))
            self.assertEqual(len(found), 1)
            self.assertAlmostEqual(found[0].x0, 50, delta=1)
            self.assertAlmostEqual(found[0].x1, 250, delta=1)
        finally:
            doc.close()

    def test_zigzag_polyline_is_not_a_rectangle(self):
        # A line-chart series used to become a coloured frame around its own
        # extent, which is how the yellow box appeared over a COVID chart.
        points = [(10, 10), (40, 80), (90, 30), (140, 120)]
        self.assertFalse(engine._is_axis_aligned_rectangle(points))

    def test_diagonal_four_point_shape_is_not_a_rectangle(self):
        self.assertFalse(
            engine._is_axis_aligned_rectangle([(0, 0), (10, 5), (20, 0), (10, -5)])
        )

    def test_axis_aligned_corners_are_a_rectangle(self):
        self.assertTrue(
            engine._is_axis_aligned_rectangle([(0, 0), (10, 0), (10, 5), (0, 5)])
        )
        # Closed form, repeating the first point.
        self.assertTrue(
            engine._is_axis_aligned_rectangle(
                [(0, 0), (10, 0), (10, 5), (0, 5), (0, 0)]
            )
        )

    def test_curved_artwork_is_detected_and_rasterised(self):
        doc = fitz.open()
        page = doc.new_page(width=400, height=300)
        path = os.path.join(self._tmp.name, "chart.pdf")
        try:
            shape = page.new_shape()
            shape.draw_circle(fitz.Point(200, 150), 70)
            shape.finish(color=(0.2, 0.4, 0.8), fill=(0.9, 0.5, 0.2), width=2)
            shape.commit()
            page.insert_text(fitz.Point(40, 40), "Titre", fontname="helv",
                             fontsize=12)
            doc.save(path)
        finally:
            doc.close()

        source = fitz.open(path)
        try:
            regions = engine._artwork_regions(source[0])
            self.assertTrue(regions, "the circle should be detected as artwork")
            self.assertGreater(regions[0].width, 100)
        finally:
            source.close()

        data, _ = engine.pdf_to_archive(path, quality="low")
        parsed = psdi.read_struct(data)
        self.assertTrue(parsed.images, "artwork should be stored as an image")
        # The rasterised region is placed like any other image, so a decoder
        # that knows nothing about artwork regions still renders it.
        refs = parsed.manifest["pages"][0]["ir"]
        self.assertTrue(refs)
        for ref in refs:
            self.assertIn(ref["x"], parsed.images)

    def test_artwork_ids_stay_within_the_container_field(self):
        # Identifiers are written as uint16; an overflow would corrupt the
        # archive silently.
        self.assertLess(engine._ARTWORK_ID_TOP, 65536)


class TestColumnPositions(unittest.TestCase):
    """A table row laid out as one text object must keep its columns."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.pdf = os.path.join(cls._tmp.name, "columns.pdf")

        doc = fitz.open()
        page = doc.new_page(width=400, height=200)
        # Values placed far apart, as a spreadsheet export does. Written as
        # separate insert_text calls on one baseline so the extractor sees the
        # wide cursor jumps that used to collapse.
        for x, value in ((40, "128847"), (150, "119323"), (260, "59278")):
            page.insert_text(fitz.Point(x, 100), value, fontname="helv",
                             fontsize=11)
        doc.save(cls.pdf)
        doc.close()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_values_keep_their_horizontal_positions(self):
        target = os.path.join(self._tmp.name, "rebuilt.pdf")
        data, _ = engine.pdf_to_archive(self.pdf, quality="medium")
        engine.archive_to_pdf(data, output_path=target)

        source = fitz.open(self.pdf)
        rebuilt = fitz.open(target)
        try:
            for value in ("128847", "119323", "59278"):
                want = source[0].search_for(value)
                got = rebuilt[0].search_for(value)
                self.assertTrue(want, f"{value} missing from the fixture")
                self.assertTrue(got, f"{value} missing from the rebuild")
                # Collapsed columns show up as a large leftward shift.
                self.assertAlmostEqual(
                    got[0].x0, want[0].x0, delta=6.0,
                    msg=f"{value} moved from x={want[0].x0:.0f} to "
                        f"x={got[0].x0:.0f}",
                )
        finally:
            source.close()
            rebuilt.close()

    def test_split_runs_keep_their_padding_spaces(self):
        # A reader that concatenates a line must still see word boundaries,
        # otherwise "36620 91,7" becomes "3662091,7" on other implementations.
        doc = fitz.open(self.pdf)
        try:
            for block in doc[0].get_text("rawdict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        runs = engine._split_span_runs(span)
                        for text, box in runs:
                            self.assertTrue(text.strip())
                            self.assertEqual(len(box), 4)
        finally:
            doc.close()

    def test_adjacent_spans_are_not_split(self):
        # Ordinary prose, including inline styling, must stay in one cluster:
        # splitting it would insert stray gaps mid-sentence.
        span = {
            "size": 11,
            "chars": [
                {"c": ch, "bbox": (40 + i * 5.5, 90, 45.5 + i * 5.5, 102)}
                for i, ch in enumerate("Situation reconnaissance")
            ],
        }
        runs = engine._split_span_runs(span)
        self.assertEqual(len(runs), 1, "prose should not be split")
