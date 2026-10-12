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
    """The bar must move with the work, not sit at one step until the end.

    Before these tests the rebuild reported 50 % before its page loop and
    100 % after it, so a fifteen-second recomposition looked frozen. Every
    conversion now reports on one fixed scale, monotonically, with
    intermediate values during the long phase.
    """

    def _trace(self, run) -> list[tuple[int, int, str]]:
        calls: list[tuple[int, int, str]] = []
        # Disable the rate limit so every unit of work is observable.
        saved = engine._PROGRESS_MIN_INTERVAL
        engine._PROGRESS_MIN_INTERVAL = 0.0
        try:
            run(lambda *a: calls.append(a))
        finally:
            engine._PROGRESS_MIN_INTERVAL = saved
        return calls

    def _assert_well_formed(self, calls):
        self.assertTrue(calls)
        for step, total, message in calls:
            self.assertIsInstance(step, int)
            self.assertIsInstance(total, int)
            self.assertIsInstance(message, str)
            self.assertEqual(total, engine.PROGRESS_SCALE,
                             "the scale must not change mid-conversion")
            self.assertTrue(0 <= step <= total)
        steps = [step for step, _, _ in calls]
        self.assertEqual(steps, sorted(steps), "progress went backwards")
        self.assertEqual(calls[0][0], 0, "must start at zero")
        self.assertEqual(calls[-1][0], engine.PROGRESS_SCALE,
                         "must end at 100 %")

    def test_compression_reports_intermediate_progress(self):
        calls = self._trace(lambda cb: engine.pdf_to_archive(
            self.report, quality="low", progress_cb=cb))
        self._assert_well_formed(calls)
        inner = {step for step, _, _ in calls if 0 < step < engine.PROGRESS_SCALE}
        self.assertGreaterEqual(len(inner), 3,
                                "no intermediate values: the bar would jump")
        self.assertTrue(any("page" in m for _, _, m in calls),
                        "per-page phase labels are missing")

    def test_rebuild_reports_every_page_and_text_line(self):
        data, _ = engine.pdf_to_archive(self.report, quality="low")
        calls = self._trace(lambda cb: engine.archive_to_pdf(data, progress_cb=cb))
        self._assert_well_formed(calls)

        manifest = psdi.read_struct(data).manifest
        pages = len(manifest["pages"])
        lines = sum(len(block.get("l", []))
                    for page in manifest["pages"]
                    for block in page.get("tb", []))
        text_reports = [m for _, _, m in calls if "texte" in m]
        self.assertEqual(len(text_reports), lines,
                         "one report per text line is what keeps the bar "
                         "moving on a dense table page")
        for n in range(1, pages + 1):
            self.assertTrue(
                any(f"page {n}/{pages}" in m for _, _, m in calls),
                f"page {n} never reported",
            )
        # The long phase must not collapse into a single jump.
        inner = {step for step, _, _ in calls if 0 < step < engine.PROGRESS_SCALE}
        self.assertGreater(len(inner), pages)

    def test_rebuild_of_page_images_reports_per_page(self):
        data, _ = engine.pdf_to_archive(self.report, quality="low",
                                        mode=engine.MODE_IMAGE)
        calls = self._trace(lambda cb: engine.archive_to_pdf(data, progress_cb=cb))
        self._assert_well_formed(calls)
        pages = len(psdi.read_image(data).pages)
        for n in range(1, pages + 1):
            self.assertTrue(any(f"({n}/{pages})" in m for _, _, m in calls))

    def test_updates_are_rate_limited(self):
        # Hundreds of signals a second would flood the GUI thread. With the
        # real interval, a conversion of a few seconds must stay well under
        # one update per 50 ms on average; the labels still arrive.
        import time as _time

        calls: list[tuple[int, int, str]] = []
        started = _time.monotonic()
        engine.pdf_to_archive(self.report, quality="low",
                              progress_cb=lambda *a: calls.append(a))
        elapsed = _time.monotonic() - started
        # Forced reports (phase starts, final) are a handful per conversion;
        # anything beyond that is throttled.
        allowed = elapsed / engine._PROGRESS_MIN_INTERVAL + 16
        self.assertLessEqual(len(calls), allowed)

    def test_faulty_callback_does_not_abort_the_conversion(self):
        # A broken progress display must never cost the operator the archive.
        def explode(*_args):
            raise RuntimeError("display is on fire")

        data, _ = engine.pdf_to_archive(self.report, quality="low",
                                        progress_cb=explode)
        self.assertTrue(psdi.validate(data)["valid"])


if __name__ == "__main__":
    unittest.main()


class TestTextLayer(EngineTestCase):
    """The per-page text layer must place text exactly where per-run
    insert_htmlbox did.

    The layer exists for speed -- insert_htmlbox is quadratic in the number
    of runs on a page -- and is only acceptable if it changes nothing on the
    page. The two paths are compared through the text the PDF reports, which
    is exact and independent of the rasteriser.
    """

    def _words(self, pdf_bytes):
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            return [page.get_text("words") for page in doc]
        finally:
            doc.close()

    def _rebuild_both_ways(self, data):
        layered, _ = engine.archive_to_pdf(data)

        original = engine._draw_line

        def per_run(page, line, layer=None):
            return original(page, line, None)

        engine._draw_line = per_run
        try:
            direct, _ = engine.archive_to_pdf(data)
        finally:
            engine._draw_line = original
        return layered, direct

    def test_layer_places_text_exactly_like_insert_htmlbox(self):
        for source in (self.report, self.table):
            with self.subTest(source=os.path.basename(source)):
                data, _ = engine.pdf_to_archive(source, quality="medium")
                layered, direct = self._rebuild_both_ways(data)

                pages_a, pages_b = self._words(layered), self._words(direct)
                self.assertEqual(len(pages_a), len(pages_b))
                compared = 0
                for words_a, words_b in zip(pages_a, pages_b):
                    self.assertEqual(len(words_a), len(words_b))
                    for wa, wb in zip(words_a, words_b):
                        self.assertEqual(wa[4], wb[4])
                        for i in range(4):
                            # Floating-point noise from composing the
                            # matrices differently; a hundredth of a point
                            # is far below anything a viewer can show.
                            self.assertAlmostEqual(wa[i], wb[i], delta=0.01)
                        compared += 1
                self.assertGreater(compared, 50)

    def test_layer_embeds_fonts_once_per_page(self):
        # One form XObject per page instead of one per run: the object count
        # no longer grows with the number of lines. This is what keeps the
        # save fast and the file small.
        data, _ = engine.pdf_to_archive(self.table, quality="medium")
        layered, direct = self._rebuild_both_ways(data)
        doc_a = fitz.open(stream=layered, filetype="pdf")
        doc_b = fitz.open(stream=direct, filetype="pdf")
        try:
            self.assertLess(doc_a.xref_length() * 4, doc_b.xref_length())
            self.assertLess(len(layered), len(direct))
        finally:
            doc_a.close()
            doc_b.close()


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



class TestMissingToUnicode(unittest.TestCase):
    """Text in a font without a ToUnicode table must be rasterised, not guessed.

    The original turned every unmapped glyph into a guessed ligature, so a
    callsign typed into a form under Okular came out as "tititititi" -- and
    nothing flagged it. The labels around it were perfect, which is what made
    the loss invisible.

    Every available font is exercised, not just one: the first fix matched
    font names and passed on DejaVuSans while failing on Arial.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.pdfs = []
        for index, ttf in enumerate(fixtures.find_truetype_fonts()):
            path = fixtures.annotated_without_tounicode(
                os.path.join(cls._tmp.name, f"okular_{index}.pdf"), ttf
            )
            if path:
                cls.pdfs.append((os.path.basename(ttf), path))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        if not self.pdfs:
            self.skipTest("no TrueType font available to embed")

    def _texts(self, data):
        manifest = psdi.read_struct(data).manifest
        return [span["t"] for page in manifest["pages"]
                for block in page["tb"] for line in block["l"]
                for span in line["s"]]

    def test_fixture_really_lacks_the_table(self):
        for font, pdf in self.pdfs:
            with self.subTest(font=font):
                doc = fitz.open(pdf)
                try:
                    bad = engine._fonts_without_unicode(doc[0])
                    text = doc[0].get_text("text")
                finally:
                    doc.close()
                self.assertTrue(bad, "the fixture should expose a font without ToUnicode")
                self.assertNotIn("F4LTV", text, "the extractor should not see the entry")

    def test_entries_are_never_transcribed(self):
        for font, pdf in self.pdfs:
            with self.subTest(font=font):
                data, _ = engine.pdf_to_archive(pdf, quality="medium")
                for text in self._texts(data):
                    self.assertNotIn("titi", text, f"guessed ligatures leaked: {text!r}")
                    for char in text:
                        self.assertGreaterEqual(
                            ord(char), 0x20, f"raw glyph id leaked into text: {text!r}"
                        )

    def test_readable_labels_stay_text(self):
        for font, pdf in self.pdfs:
            with self.subTest(font=font):
                data, _ = engine.pdf_to_archive(pdf, quality="medium")
                joined = " ".join(self._texts(data))
                self.assertIn("Indicatif", joined)
                self.assertIn("lutte", joined)   # the genuine ligature path still works

    def test_entries_survive_as_raster(self):
        for font, pdf in self.pdfs:
            with self.subTest(font=font):
                data, _ = engine.pdf_to_archive(pdf, quality="medium")
                parsed = psdi.read_struct(data)
                refs = parsed.manifest["pages"][0]["ir"]
                self.assertGreaterEqual(len(refs), 2, "one raster per lost entry")
                for ref in refs:
                    self.assertIn(ref["x"], parsed.images)

    def test_text_only_mode_still_keeps_the_entries(self):
        # Dropping pictures is a choice the operator makes; losing their own
        # entries is not. The rasters must be produced regardless.
        for font, pdf in self.pdfs:
            with self.subTest(font=font):
                data, _ = engine.pdf_to_archive(pdf, quality="low", skip_images=True)
                self.assertGreaterEqual(len(psdi.read_struct(data).images), 2)

    def test_genuine_ligature_heuristic_is_unaffected(self):
        self.assertEqual(engine._normalize_text("situa\ufffdon"), "situation")
        self.assertEqual(engine._normalize_text("lu\ufffde"), "lutte")
        self.assertFalse(engine._is_unreadable("situa\ufffdon"))
        self.assertTrue(engine._is_unreadable("\ufffd\ufffd\ufffd\ufffd\ufffd"))

    def test_a_single_control_character_condemns_a_span(self):
        # ")\x17/79" is Arial's rendering of F4LTV: four printable glyph ids
        # and one control character. Judged by share it looks 80% readable.
        self.assertTrue(engine._is_unreadable(")\x17/79"))
        self.assertFalse(engine._is_unreadable("(voir p. 4)"))

    def test_truncated_basefont_still_matches(self):
        # BaseFont is capped at 31 characters by producers; the span reports
        # the full name. Both must resolve to the same font.
        bad = {engine._font_key("DejaVu Sans Condensed Bold Obli"),
               "dejavusanscondensedboldobli"}
        self.assertTrue(engine._font_matches("DejaVuSansCondensed-BoldOblique", bad))
        self.assertFalse(engine._font_matches("Helvetica", bad))


class TestImageInput(unittest.TestCase):
    """A photo or scan is a page with no text layer: page-image mode, as is."""

    @classmethod
    def setUpClass(cls):
        from PIL import Image, ImageDraw

        cls._tmp = tempfile.TemporaryDirectory()
        base = Image.new("RGB", (1600, 2200), (236, 233, 226))
        draw = ImageDraw.Draw(base)
        for i in range(20):
            draw.text((120, 120 + i * 90), f"Ligne {i} du formulaire", fill=(30, 30, 40))

        cls.jpeg = os.path.join(cls._tmp.name, "scan.jpg")
        base.save(cls.jpeg, quality=90)

        cls.png_alpha = os.path.join(cls._tmp.name, "scan.png")
        base.convert("RGBA").save(cls.png_alpha)

        cls.landscape = os.path.join(cls._tmp.name, "wide.jpg")
        base.transpose(Image.ROTATE_90).save(cls.landscape, quality=90)

        cls.multipage = os.path.join(cls._tmp.name, "scan.tif")
        # Uncompressed on purpose: the multi-page structure is what is under
        # test, and naming a compressor would make the fixture depend on
        # which codecs the installed Pillow was built with.
        base.save(cls.multipage, save_all=True, append_images=[base.rotate(180)])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_extension_detection(self):
        for path in (self.jpeg, self.png_alpha, self.landscape, self.multipage):
            self.assertTrue(engine.is_image_file(path), path)
        self.assertFalse(engine.is_image_file("report.pdf"))
        self.assertFalse(engine.is_image_file("archive.psdi"))

    def test_image_becomes_a_version_2_archive(self):
        for path in (self.jpeg, self.png_alpha):
            with self.subTest(path=os.path.basename(path)):
                data, info = engine.pdf_to_archive(path, quality="low")
                self.assertEqual(psdi.peek_version(data), psdi.ARCHIVE_VERSION_IMAGE)
                self.assertEqual(info["mode"], engine.MODE_IMAGE)
                self.assertEqual(info["auto_mode_switch"], "image")
                self.assertTrue(psdi.validate(data)["valid"])

    def test_page_size_keeps_aspect_and_fits_a4(self):
        portrait = engine._page_size_for(1600, 2200)
        self.assertAlmostEqual(portrait[1], 842.0)
        self.assertAlmostEqual(portrait[0] / portrait[1], 1600 / 2200, places=3)

        landscape = engine._page_size_for(2200, 1600)
        self.assertAlmostEqual(landscape[0], 842.0)
        self.assertGreater(landscape[0], landscape[1])

    def test_landscape_image_rebuilds_landscape(self):
        target = os.path.join(self._tmp.name, "wide.pdf")
        data, _ = engine.pdf_to_archive(self.landscape, quality="low")
        engine.archive_to_pdf(data, output_path=target)
        doc = fitz.open(target)
        try:
            self.assertGreater(doc[0].rect.width, doc[0].rect.height)
        finally:
            doc.close()

    def test_multipage_tiff_gives_one_page_per_frame(self):
        data, _ = engine.pdf_to_archive(self.multipage, quality="low")
        self.assertEqual(len(psdi.read_image(data).pages), 2)

    def test_transparent_png_is_flattened_on_white(self):
        import io

        from PIL import Image

        data, _ = engine.pdf_to_archive(self.png_alpha, quality="low")
        payload = psdi.read_image(data).pages[0][2]
        image = Image.open(io.BytesIO(payload))
        self.assertIn(image.mode, ("RGB", "L"))

    def test_output_is_downsampled_to_the_preset(self):
        # A 12-megapixel photo and a 150-DPI scan of the same sheet must come
        # out the same size on the air.
        small, _ = engine.pdf_to_archive(self.jpeg, quality="ultra_low")
        large, _ = engine.pdf_to_archive(self.jpeg, quality="high")
        self.assertLess(len(small), len(large))
        self.assertLess(len(small), 60_000, "ultra_low should fit a Packet transfer")

    def test_explicit_struct_mode_is_overridden_for_images(self):
        # There is no text layer to structure; asking for it must not fail.
        data, info = engine.pdf_to_archive(self.jpeg, quality="low",
                                           mode=engine.MODE_STRUCT)
        self.assertEqual(info["mode"], engine.MODE_IMAGE)


class TestInteropSafeAdditions(unittest.TestCase):
    """Placement-aware images, vector rules and links: all format-compatible."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.pdf = fixtures.rich_report(os.path.join(cls._tmp.name, "rich.pdf"))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _manifest(self, quality="medium"):
        data, _ = engine.pdf_to_archive(self.pdf, quality=quality)
        return data, psdi.read_struct(data)

    # --- placement-aware downscaling -------------------------------------
    def test_image_is_not_stored_larger_than_its_placement(self):
        import io

        from PIL import Image

        from psditool.presets import QUALITY_PRESETS

        for quality in ("ultra_low", "medium"):
            with self.subTest(quality=quality):
                _, parsed = self._manifest(quality)
                dpi = QUALITY_PRESETS[quality]["dpi"]
                refs = [r for p in parsed.manifest["pages"] for r in p["ir"]]
                self.assertTrue(refs)
                for ref in refs:
                    if int(ref["x"]) >= engine._ARTWORK_ID_TOP - 1000:
                        continue  # artwork rasters are sized by region
                    b = ref["b"]
                    img = Image.open(io.BytesIO(parsed.images[ref["x"]]))
                    need_w = (b[2] - b[0]) * dpi / 72 * 1.1 + 2
                    need_h = (b[3] - b[1]) * dpi / 72 * 1.1 + 2
                    self.assertLessEqual(img.width, need_w + 1,
                                         f"{img.size} stored for {b} at {dpi} DPI")
                    self.assertLessEqual(img.height, need_h + 1)

    def test_image_is_never_upscaled(self):
        # A tiny icon placed large must keep its original pixels, not grow.
        import io

        from PIL import Image

        doc = fitz.open()
        page = doc.new_page(width=300, height=300)
        icon = Image.new("RGB", (16, 16), (200, 30, 30))
        buf = io.BytesIO(); icon.save(buf, format="PNG")
        page.insert_image(fitz.Rect(20, 20, 120, 120), stream=buf.getvalue())
        # Enough prose that mode detection keeps the structured path; a page
        # that is mostly one image is legitimately treated as a scan.
        for i in range(12):
            page.insert_text(fitz.Point(20, 150 + i * 12),
                             f"Ligne {i} de texte pour rester en mode structuré.",
                             fontname="helv", fontsize=8)
        path = os.path.join(self._tmp.name, "icon.pdf")
        doc.save(path); doc.close()

        data, _ = engine.pdf_to_archive(path, quality="high")
        self.assertEqual(psdi.peek_version(data), psdi.ARCHIVE_VERSION_STRUCT)
        parsed = psdi.read_struct(data)
        for payload in parsed.images.values():
            img = Image.open(io.BytesIO(payload))
            self.assertLessEqual(max(img.size), 16)

    def test_repeated_image_sized_for_its_largest_placement(self):
        import io

        from PIL import Image

        doc = fitz.open()
        img = Image.new("RGB", (400, 400), (30, 90, 200))
        buf = io.BytesIO(); img.save(buf, format="JPEG")
        # Same image: small on page 1, large on page 2.
        # Each page is finished before the next is created: PyMuPDF page
        # handles do not survive a later new_page() call.
        for frame in (fitz.Rect(10, 10, 50, 50), fitz.Rect(10, 10, 210, 210)):
            page = doc.new_page(width=400, height=600)
            page.insert_image(frame, stream=buf.getvalue())
            for i in range(14):
                page.insert_text(fitz.Point(20, 260 + i * 14),
                                 f"Ligne {i} de texte, mode structuré conservé.",
                                 fontname="helv", fontsize=9)
        path = os.path.join(self._tmp.name, "repeat.pdf")
        doc.save(path); doc.close()

        data, _ = engine.pdf_to_archive(path, quality="medium")
        self.assertEqual(psdi.peek_version(data), psdi.ARCHIVE_VERSION_STRUCT)
        parsed = psdi.read_struct(data)
        self.assertEqual(len(parsed.images), 1, "extracted once")
        stored = Image.open(io.BytesIO(next(iter(parsed.images.values()))))
        # 200 pt at 120 DPI = 333 px (+10%), never upscaled beyond 400. Must
        # be far larger than the 40 pt placement on page 1 would allow.
        self.assertGreater(max(stored.size), 200)

    # --- vector rules ------------------------------------------------------
    def test_rules_are_stored_as_lines_not_rasters(self):
        _, parsed = self._manifest()
        page = parsed.manifest["pages"][0]
        lines = [e for e in page["dr"] if e.get("t") == "line"]
        self.assertGreaterEqual(len(lines), 2, "one horizontal, one vertical")
        for entry in lines:
            self.assertEqual(len(entry["r"]), 4)
            self.assertIn("c", entry)
        rasters = [k for k in parsed.images if int(k) >= engine._ARTWORK_ID_TOP - 1000]
        self.assertFalse(rasters, "a plain rule must not become a raster")

    def test_lines_rebuild_as_drawings(self):
        data, _ = self._manifest()
        target = os.path.join(self._tmp.name, "rich_rebuilt.pdf")
        engine.archive_to_pdf(data, output_path=target)
        doc = fitz.open(target)
        try:
            segs = [path for path in doc[0].get_drawings()
                    if path.get("items") and all(it[0] == "l" for it in path["items"])]
            self.assertGreaterEqual(len(segs), 2)
        finally:
            doc.close()

    def test_a_chart_is_still_artwork(self):
        # Only *pure* axis-aligned stroke paths are structure; a diagonal
        # series or a curve must still be rasterised.
        self.assertFalse(engine._path_is_artwork([("l", fitz.Point(0, 0), fitz.Point(100, 0))]))
        self.assertTrue(engine._path_is_artwork([("l", fitz.Point(0, 0), fitz.Point(60, 40))]))

    # --- hyperlinks ----------------------------------------------------------
    def test_links_round_trip(self):
        data, parsed = self._manifest()
        links = [l for p in parsed.manifest["pages"] for l in p.get("lk", [])]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["u"], "https://example.org/fiche-reflexe")

        target = os.path.join(self._tmp.name, "links_rebuilt.pdf")
        engine.archive_to_pdf(data, output_path=target)
        doc = fitz.open(target)
        try:
            uris = [l["uri"] for l in doc[0].get_links() if l.get("uri")]
        finally:
            doc.close()
        self.assertEqual(uris, ["https://example.org/fiche-reflexe"])

    # --- the one thing that matters: the original decoder still reads it ---
    def test_legacy_decoder_steps_over_lines_and_links(self):
        # The original walks "dr" with `if d['t'] != 'rect': continue` and
        # never reads "lk". Reproduce that loop literally.
        data, parsed = self._manifest()
        drawn = 0
        for page in parsed.manifest["pages"]:
            for d in page.get("dr", []):
                if not d["t"] == "rect":
                    continue
                fitz.Rect(d["r"])    # must be a valid rect for the original
                drawn += 1
        self.assertGreaterEqual(drawn, 1)
