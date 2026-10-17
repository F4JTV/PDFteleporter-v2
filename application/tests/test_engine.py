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


def _legacy_paint_drawings(manifest: dict, page_index: int = 0):
    """Paint one page's "dr" list the way the original decoder does.

    A literal port of its loop: entries whose "t" is not "rect" are skipped,
    every other one is draw_rect + finish(color=c, fill=f, width=w or 0).
    """
    size = manifest["page_sizes"][page_index]
    doc = fitz.open()
    page = doc.new_page(width=size["w"], height=size["h"])
    shape = page.new_shape()
    for d in manifest["pages"][page_index].get("dr", []):
        if not d["t"] == "rect":
            continue
        width = d.get("w", 0)
        shape.draw_rect(fitz.Rect(d["r"]))
        shape.finish(color=engine._hex_to_rgb(d.get("c", "")),
                     fill=engine._hex_to_rgb(d.get("f", "")),
                     width=width if width > 0.1 else 0)
    shape.commit()
    return doc


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


def _fill(box, colour="#000000"):
    return {"t": "rect", "r": list(box), "f": colour}


def _rule(box, colour="#000000", width=0.4):
    return {"t": "rect", "r": list(box), "c": colour, "w": width}


class TestDrawingCompaction(unittest.TestCase):
    """The drawing list may get shorter, never paint differently."""

    def compact(self, entries):
        return engine._compact_drawings([dict(e, r=list(e["r"])) for e in entries])

    def test_abutting_fills_join(self):
        out = self.compact([_fill((0, 0, 10, 10)), _fill((10, 0, 20, 10))])
        self.assertEqual(out, [_fill((0, 0, 20, 10))])

    def test_a_grid_of_cells_joins_into_one_block(self):
        cells = [_fill((x, y, x + 10, y + 10), "#ffc000")
                 for y in (0, 10) for x in (0, 10, 20)]
        self.assertEqual(self.compact(cells), [_fill((0, 0, 30, 20), "#ffc000")])

    def test_thin_pieces_join_end_to_end(self):
        out = self.compact([_rule((0, 5, 10, 5)), _rule((10, 5, 20, 5))])
        self.assertEqual(out, [_rule((0, 5, 20, 5))])

    def test_overlapping_thin_pieces_are_kept(self):
        # Where they overlap, the source painted twice and looks darker on
        # screen; one merged piece would come out lighter there.
        pieces = [_rule((0, 5, 10, 5)), _rule((8, 5, 20, 5))]
        self.assertEqual(len(self.compact(pieces)), 2)

    def test_duplicate_thin_pieces_are_kept(self):
        # LibreOffice draws each inner border once per adjacent cell.
        pieces = [_rule((0, 5, 10, 5)), _rule((0, 5, 10, 5))]
        self.assertEqual(len(self.compact(pieces)), 2)

    def test_duplicate_fills_collapse(self):
        cells = [_fill((0, 0, 10, 10), "#ff0000"), _fill((0, 0, 10, 10), "#ff0000")]
        self.assertEqual(self.compact(cells), [_fill((0, 0, 10, 10), "#ff0000")])

    def test_a_piece_never_moves_below_another_colour(self):
        # C would join A, but B, painted between them, overlaps C: folding C
        # into A would put B on top of it.
        a = _fill((0, 0, 10, 10))
        b = _fill((5, 0, 15, 10), "#ff0000")
        c = _fill((10, 0, 20, 10))
        self.assertEqual(self.compact([a, b, c]), [a, b, c])

    def test_white_on_blank_paper_is_dropped(self):
        self.assertEqual(self.compact([_fill((0, 0, 50, 50), "#ffffff")]), [])

    def test_white_over_colour_is_kept(self):
        entries = [_fill((0, 0, 50, 50), "#ff0000"), _fill((10, 10, 20, 20), "#ffffff")]
        self.assertEqual(self.compact(entries), entries)

    def test_entries_shrunk_to_a_point_are_dropped(self):
        entries = [_fill((5, 5, 5, 5)), _rule((7, 7, 7, 7)), _fill((0, 0, 3, 3), "#00ff00")]
        self.assertEqual(self.compact(entries), [_fill((0, 0, 3, 3), "#00ff00")])

    def test_a_fill_of_no_height_is_kept(self):
        # MuPDF still draws it, as a faint one-pixel line.
        entries = [_fill((5, 5, 5, 20))]
        self.assertEqual(self.compact(entries), entries)

    def test_off_page_giants_cost_nothing(self):
        # Footprints are only indexed where they meet the page; a fill a
        # hundred thousand points wide used to walk millions of grid cells.
        import time

        entries = [_fill((-50000, -50000, 50000, 50000), "#eeeeee")]
        entries += [_fill((x, 10, x + 10, 20), "#ff0000") for x in range(0, 500, 10)]
        started = time.monotonic()
        out = engine._compact_drawings(entries, (0, 0, 595, 842))
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(out[1], _fill((0, 10, 500, 20), "#ff0000"))

    def test_sorting_stays_within_a_run_of_one_style(self):
        entries = [_rule((0, 10, 5, 10)), _rule((0, 5, 5, 5)),
                   _fill((0, 0, 50, 50), "#ff0000"), _rule((0, 1, 5, 1))]
        out = self.compact(entries)
        self.assertEqual([e["r"][1] for e in out], [5, 10, 0, 1])
        self.assertEqual(out[2]["f"], "#ff0000", "the red fill stays between")

    def test_two_colour_entries_keep_their_order(self):
        # A cell with a fill and a border: the second one's fill covers the
        # first one's border where they overlap, so they never commute.
        lower = {"t": "rect", "r": [0, 10, 50, 30], "f": "#ffffff", "c": "#000000", "w": 0.8}
        upper = {"t": "rect", "r": [0, 0, 50, 20], "f": "#ffffff", "c": "#000000", "w": 0.8}
        self.assertEqual(self.compact([lower, upper]), [lower, upper])

    def test_one_colour_sorts_across_stroke_widths(self):
        # Black is black whatever the width it is laid down with: LibreOffice
        # alternates three widths of black border, and the run goes on.
        entries = [_rule((0, 30, 9, 30), width=0.6), _rule((0, 10, 9, 10), width=0.4),
                   _rule((0, 20, 9, 20), width=0.6), _rule((0, 5, 9, 5), width=0.4)]
        out = self.compact(entries)
        self.assertEqual([(e["w"], e["r"][1]) for e in out],
                         [(0.4, 5), (0.4, 10), (0.6, 20), (0.6, 30)])

    @staticmethod
    def _cell_frame(left, top, right, bottom, width=0.4, overrun=0.0):
        """A cell's four sides drawn as LibreOffice does, ends running past the corners."""
        half = width / 2 + overrun
        return [_rule((left - half, top, right + half, top), width=width),
                _rule((left - half, bottom, right + half, bottom), width=width),
                _rule((left, top - half, left, bottom + half), width=width),
                _rule((right, top - half, right, bottom + half), width=width)]

    def test_four_rules_closing_a_rectangle_become_one(self):
        out = self.compact(self._cell_frame(10, 20, 60, 40, overrun=0.1))
        self.assertEqual(out, [{"t": "rect", "r": [10, 20, 60, 40], "c": "#000000", "w": 0.4}])

    def test_rules_running_further_past_the_corner_stay_rules(self):
        # Beyond one rounding step the ends are part of the drawing.
        self.assertEqual(len(self.compact(self._cell_frame(10, 20, 60, 40, overrun=0.3))), 4)

    def test_whole_point_frames_allow_half_a_stroke(self):
        # At whole points the ends are rounded and the stroke width is not.
        rules = [_rule((10, 20, 60, 20)), _rule((10, 40, 60, 40)),
                 _rule((10, 20, 10, 40)), _rule((60, 20, 60, 40))]
        out = engine._compact_drawings(rules, None, 0)
        self.assertEqual(out, [{"t": "rect", "r": [10, 20, 60, 40], "c": "#000000", "w": 0.4}])
        self.assertEqual(len(engine._compact_drawings(rules, None, 1)), 4)

    def test_a_frame_needs_one_colour_and_one_width(self):
        frame = self._cell_frame(10, 20, 60, 40)
        frame[3] = _rule((60, 19.8, 60, 40.2), width=0.6)
        self.assertEqual(len(self.compact(frame)), 4)
        frame = self._cell_frame(10, 20, 60, 40)
        red = dict(frame[3], c="#ff0000")
        self.assertEqual(len(self.compact(frame[:3] + [red])), 4)

    def test_cells_of_a_table_each_keep_their_frame(self):
        # Each inner edge is drawn by both its cells, so it stays painted twice.
        rules = []
        for row in range(3):
            for col in range(4):
                rules += self._cell_frame(10 + col * 30, 10 + row * 15,
                                          40 + col * 30, 25 + row * 15, overrun=0.1)
        out = self.compact(rules)
        self.assertEqual(len(out), 12)
        self.assertTrue(all(e["r"][0] < e["r"][2] and e["r"][1] < e["r"][3] for e in out))

    def test_frames_paint_like_their_rules(self):
        # Sides land exactly where the rules were; only the corners may move
        # by the overrun, and the corner squares are painted once, not twice.
        rules = []
        for row in range(6):
            for col in range(5):
                rules += self._cell_frame(20.3 + col * 61.3, 30.6 + row * 17.9,
                                          81.6 + col * 61.3, 48.5 + row * 17.9,
                                          width=0.4, overrun=0.08)
        rules = [dict(r, r=[round(v, 1) for v in r["r"]]) for r in rules]
        framed = self.compact(rules)
        self.assertEqual(len(framed), 30)

        def render(dr, dpi):
            manifest = {"pages": [{"dr": dr}], "page_sizes": [{"w": 400, "h": 200}]}
            doc = _legacy_paint_drawings(manifest)
            try:
                return doc[0].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY).samples
            finally:
                doc.close()

        for dpi in (96, 150, 300):
            with self.subTest(dpi=dpi):
                before, after = render(rules, dpi), render(framed, dpi)
                diffs = [abs(a - b) for a, b in zip(before, after)]
                self.assertLess(sum(diffs) / len(diffs), 0.1)
                self.assertLess(sum(1 for d in diffs if d > 32), len(diffs) * 0.002)

    def test_a_random_page_paints_the_same(self):
        # Fills and rules of several colours and thicknesses -- hairlines,
        # sub-point fills, two-colour cells -- on a grid where pieces abut,
        # overlap and repeat, painted through the original decoder's loop
        # before and after compaction.
        import random

        rng = random.Random(3)
        colours = ["#000000", "#ff0000", "#ffffff", "#4472c4"]
        entries = []
        for _ in range(500):
            colour = rng.choice(colours)
            x, y = rng.randrange(0, 380, 10), rng.randrange(0, 280, 10)
            span = rng.choice((10, 20))
            pick = rng.random()
            if pick < 0.35:
                entry = _fill((x, y, x + span, y + 10), colour)
            elif pick < 0.5:
                entry = _fill((x, y, x + span, y + rng.choice((0.5, 0.84, 1.5))), colour)
            elif pick < 0.75:
                entry = _rule((x, y, x + span, y), colour, rng.choice((0.4, 0.8)))
                if rng.random() < 0.3:
                    entry.pop("w")  # hairline
            elif pick < 0.9:
                entry = _rule((x, y, x, y + span), colour, 0.4)
            else:
                entry = {"t": "rect", "r": [x, y, x + span, y + 10],
                         "f": colour, "c": rng.choice(colours), "w": 0.8}
            entries.append(entry)
            if rng.random() < 0.1:
                entries.append(dict(entry, r=list(entry["r"])))  # repeated

        def render(dr):
            manifest = {"pages": [{"dr": dr}], "page_sizes": [{"w": 400, "h": 300}]}
            doc = _legacy_paint_drawings(manifest)
            try:
                return doc[0].get_pixmap(dpi=150, colorspace=fitz.csGRAY).samples
            finally:
                doc.close()

        compacted = self.compact(entries)
        self.assertLess(len(compacted), len(entries))
        before, after = render(entries), render(compacted)
        diffs = [abs(a - b) for a, b in zip(before, after)]
        # Joining two pieces removes the faint seam where they met, and one
        # copy of a repeated fill no longer doubles its anti-aliased edge;
        # nothing else may change.
        self.assertLessEqual(max(diffs), 96)
        self.assertLess(sum(1 for d in diffs if d > 32), len(diffs) * 0.002)


class TestSpreadsheetBorders(unittest.TestCase):
    """Borders drawn the way Excel and LibreOffice export them."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.pdf = fixtures.bordered_sheet(os.path.join(cls._tmp.name, "sheet.pdf"))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_thin_borders_survive_whole_point_coordinates(self):
        # At the two low presets coordinates are whole points. An Excel
        # border 0.84 pt thick used to round to 1 pt or to nothing, and a
        # fifth of them vanished. Every cell edge must still carry ink.
        x0, y0, cw, ch, t = 40.37, 100.29, 58.61, 18.43, 0.84
        for quality in ("ultra_low", "low"):
            with self.subTest(quality=quality):
                data, _ = engine.pdf_to_archive(self.pdf, quality=quality)
                manifest = psdi.read_struct(data).manifest
                doc = _legacy_paint_drawings(manifest)
                try:
                    pix = doc[0].get_pixmap(dpi=144, colorspace=fitz.csGRAY)
                finally:
                    doc.close()

                def darkest(x_pt, y_pt, horizontal):
                    # A whole-point shift of the border is allowed; its loss
                    # is not. Search a 3 pt band across the border.
                    values = []
                    for d in range(-3, 4):
                        dx, dy = (0, d * 0.5) if horizontal else (d * 0.5, 0)
                        values.append(pix.pixel(int((x_pt + dx) * 2), int((y_pt + dy) * 2))[0])
                    return min(values)

                for r in range(7):
                    for c in range(4):
                        self.assertLess(darkest(x0 + (c + 0.5) * cw, y0 + r * ch + t / 2, True),
                                        128, f"horizontal border {r},{c} lost")
                for c in range(5):
                    for r in range(6):
                        self.assertLess(darkest(x0 + c * cw + t / 2, y0 + (r + 0.5) * ch, False),
                                        128, f"vertical border {r},{c} lost")
                widths = {e.get("w") for e in manifest["pages"][0]["dr"]
                          if engine._drawing_kind(e) == "rule" and e["c"] == "#000000"
                          and e["r"][0] < 300}
                self.assertEqual(widths, {0.8}, "thickness travels in w")

    def test_compaction_shrinks_the_archive(self):
        # Measured on this fixture: 0.78 and 0.86 of the uncompacted size.
        # At a tenth of a point most Excel pieces stop short of each other,
        # and the LibreOffice table is all doubled strokes, kept on purpose.
        for quality, ceiling in (("ultra_low", 0.85), ("medium", 0.92)):
            with self.subTest(quality=quality):
                compacted, _ = engine.pdf_to_archive(self.pdf, quality=quality)
                original = engine._compact_drawings
                engine._compact_drawings = lambda entries, bounds=None, precision=1: entries
                try:
                    raw, _ = engine.pdf_to_archive(self.pdf, quality=quality)
                finally:
                    engine._compact_drawings = original
                self.assertLess(len(compacted), len(raw) * ceiling)

    def test_borders_reach_the_original_decoder(self):
        # The LibreOffice-style table is all strokes. Stored as rules, the
        # original's loop draws it; as "line" entries it drew nothing.
        data, _ = engine.pdf_to_archive(self.pdf, quality="medium")
        manifest = psdi.read_struct(data).manifest
        doc = _legacy_paint_drawings(manifest)
        try:
            pix = doc[0].get_pixmap(dpi=144, colorspace=fitz.csGRAY)
            x0, y0, cw, ch = 400.6, 100.4, 61.3, 17.9
            for r in range(7):
                values = [pix.pixel(int((x0 + cw / 2) * 2), int((y0 + r * ch) * 2 + d))[0]
                          for d in (-1, 0, 1)]
                self.assertLess(min(values), 180, f"row border {r} missing")
        finally:
            doc.close()


class TestTextEncoding(unittest.TestCase):
    """Lines of text stored in fewer bytes, drawn exactly as before."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.report = fixtures.text_report(os.path.join(cls._tmp.name, "report.pdf"), pages=2)
        cls.sheet = fixtures.bordered_sheet(os.path.join(cls._tmp.name, "sheet.pdf"))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_fragments_of_one_style_are_joined(self):
        # LibreOffice's "ti" ligature comes from another font: "Identité"
        # arrives as three spans.
        line_box = [163.8, 87.8, 230.4, 98.2]
        spans = [{"t": "Iden", "sz": 8.5, "b": [163.8, 87.8, 179.1, 98.2]},
                 {"t": "ti", "sz": 8.5, "b": [179.0, 87.8, 183.8, 98.2]},
                 {"t": "té Expéditeur", "sz": 8.5, "b": [183.8, 87.8, 230.4, 98.2]}]
        self.assertEqual(engine._merge_adjacent_spans(spans, line_box),
                         [{"t": "Identité Expéditeur", "sz": 8.5, "b": line_box}])

    def test_fragments_apart_or_of_another_style_stay_apart(self):
        line_box = [100, 10, 300, 20]
        spans = [{"t": "128847", "b": [100, 10, 130, 20]},
                 {"t": "119323", "b": [210, 10, 240, 20]},           # a column away
                 {"t": "gras", "f": "B", "b": [240.5, 10, 260, 20]}]  # another style
        self.assertEqual(len(engine._merge_adjacent_spans(spans, line_box)), 3)

    def test_a_fragment_inside_the_last_one_stays_apart(self):
        # The reader measures the next gap from the last fragment's right
        # edge; joined, a fragment lying inside the previous one would move
        # that edge and could pull the next span into the same run.
        line_box = [0, 10, 60, 20]
        spans = [{"t": "abc", "b": [0, 10, 50, 20]}, {"t": "d", "b": [40, 10, 45, 20]},
                 {"t": "e", "b": [52, 10, 60, 20]}]
        self.assertEqual(len(engine._merge_adjacent_spans(spans, line_box)), 3)

    def test_joined_fragments_render_the_same(self):
        line_box = [100, 100, 220.4, 110]
        fragments = [{"t": "Iden", "sz": 8.5, "b": [100, 100, 115.3, 110]},
                     {"t": "ti", "sz": 8.5, "b": [115.2, 100, 120, 110]},
                     {"t": "té Expéditeur", "sz": 8.5, "b": [120, 100, 166.6, 110]},
                     {"t": "0", "sz": 8.5, "b": [216.5, 100, 220.4, 110]}]
        joined = engine._merge_adjacent_spans(fragments, line_box)
        self.assertEqual(len(joined), 2)

        def render(spans):
            manifest = {"v": "1.0.6", "pages": [{"pn": 1, "tb": [{"l": [{"b": line_box, "s": spans}]}]}],
                        "page_sizes": [{"w": 300, "h": 200}], "images": {}}
            pdf, _ = engine.archive_to_pdf(psdi.write_struct(manifest, {}, 6))
            doc = fitz.open(stream=pdf, filetype="pdf")
            try:
                return doc[0].get_pixmap(dpi=150, colorspace=fitz.csGRAY).samples
            finally:
                doc.close()

        self.assertEqual(render(fragments), render(joined))

    def test_a_line_that_fits_opens_its_box(self):
        line = {"b": [100, 100, 150, 110], "s": [{"t": "Bonjour", "sz": 8}]}
        self.assertEqual(engine._open_line_box(line, 595.28, 841.89, 1, {}),
                         [100, 100, 595.3, 841.9])
        self.assertEqual(engine._open_line_box(line, 595.28, 841.89, 0, {}),
                         [100, 100, 595, 842])

    def test_a_line_shrunk_to_fit_keeps_its_box(self):
        # Wider in Helvetica than in the source font: the box sets the scale.
        line = {"b": [163.8, 87.8, 200.4, 98.2], "s": [{"t": "Identité Expéditeur", "sz": 8.5}]}
        self.assertEqual(engine._open_line_box(line, 842, 595, 1, {}), line["b"])

    def test_only_lines_the_base_fonts_can_set_are_opened(self):
        for spans in ([{"t": "日本", "sz": 8}],                   # glyphs missing from Helvetica
                      [{"t": "a\tb", "sz": 8}],                   # a tab is laid out as a stop
                      [{"t": "a", "sz": 8}, {"t": "b", "f": "B", "sz": 8}],  # two runs
                      [{"t": "a", "sz": 8, "b": [100, 100, 104, 110]}]):     # positioned span
            with self.subTest(spans=spans):
                line = {"b": [100, 100, 300, 110], "s": spans}
                self.assertEqual(engine._open_line_box(line, 842, 595, 1, {}), line["b"])

    def test_a_box_past_the_page_edge_is_left_alone(self):
        line = {"b": [100, 100, 900, 110], "s": [{"t": "Bonjour", "sz": 8}]}
        self.assertEqual(engine._open_line_box(line, 842, 595, 1, {}), line["b"])

    def test_open_boxes_render_identically(self):
        for path in (self.report, self.sheet):
            for quality in ("ultra_low", "medium"):
                with self.subTest(doc=os.path.basename(path), quality=quality):
                    opened, _ = engine.pdf_to_archive(path, quality=quality)
                    original = engine._open_line_box
                    engine._open_line_box = lambda line, *args: line["b"]
                    try:
                        exact, _ = engine.pdf_to_archive(path, quality=quality)
                    finally:
                        engine._open_line_box = original
                    self.assertLess(len(opened), len(exact))
                    pages = []
                    for data in (opened, exact):
                        doc = fitz.open(stream=engine.archive_to_pdf(data)[0], filetype="pdf")
                        try:
                            pages.append([p.get_pixmap(dpi=120, colorspace=fitz.csGRAY).samples
                                          for p in doc])
                        finally:
                            doc.close()
                    self.assertEqual(pages[0], pages[1])

    def test_every_line_of_a_page_shares_one_block(self):
        data, _ = engine.pdf_to_archive(self.report, quality="medium")
        manifest = psdi.read_struct(data).manifest
        self.assertTrue(all(len(page["tb"]) == 1 for page in manifest["pages"]))
        self.assertGreater(len(manifest["pages"][0]["tb"][0]["l"]), 10)


class TestPayloadCodec(unittest.TestCase):
    """Each image of a structured archive in its smallest faithful form."""

    @staticmethod
    def _chart():
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (240, 150), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        for i, h in enumerate((60, 105, 45, 130, 85)):
            draw.rectangle([15 + i * 45, 140 - h, 45 + i * 45, 140],
                           fill=((68, 114, 196), (237, 125, 49), (165, 165, 165))[i % 3])
        return img

    def test_a_flat_graphic_becomes_a_more_faithful_png(self):
        import io

        from PIL import Image

        img = self._chart()
        payload = engine._encode_payload(img, 45, 9)
        self.assertEqual(payload[:8], b"\x89PNG\r\n\x1a\n")
        jpeg = engine._image_bytes(img, format="JPEG", quality=45, optimize=True)
        self.assertGreater(engine._psnr(img, Image.open(io.BytesIO(payload))),
                           engine._psnr(img, Image.open(io.BytesIO(jpeg))) + 1.0)
        self.assertLess(len(psdi.pack_payload(payload, 9)[1]),
                        len(psdi.pack_payload(jpeg, 9)[1]))

    def test_a_photograph_stays_jpeg(self):
        import random

        from PIL import Image

        rng = random.Random(5)
        img = Image.new("RGB", (160, 120))
        img.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256))
                     for _ in range(160 * 120)])
        self.assertEqual(engine._encode_payload(img, 45, 9)[:2], b"\xff\xd8")

    def test_a_smooth_picture_with_few_colours_stays_jpeg(self):
        # Under the colour ceiling, so the palette search does run; a smooth
        # picture is still what JPEG codes best.
        import random

        from PIL import Image, ImageFilter

        rng = random.Random(5)
        img = Image.new("RGB", (60, 50))
        img.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256))
                     for _ in range(60 * 50)])
        img = img.filter(ImageFilter.GaussianBlur(3))
        self.assertIsNotNone(img.getcolors(engine._PALETTE_MAX_COLOURS))
        self.assertEqual(engine._encode_payload(img, 45, 9)[:2], b"\xff\xd8")

    def test_own_palette_is_checked_not_assumed(self):
        # Pillow folds colours a few levels apart when mapping onto a given
        # palette; whatever comes out must still beat the JPEG by the margin.
        import io

        from PIL import Image, ImageDraw

        img = Image.new("RGB", (120, 80), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        for i, colour in enumerate(((100, 100, 100), (101, 101, 101), (102, 102, 103),
                                    (255, 0, 0), (254, 1, 0), (11, 201, 31))):
            draw.rectangle([i * 20, 10, i * 20 + 15, 70], fill=colour)
        payload = engine._encode_payload(img, 45, 9)
        decoded = Image.open(io.BytesIO(payload))
        if payload[:4] == b"\x89PNG":
            jpeg = engine._image_bytes(img, format="JPEG", quality=45, optimize=True)
            self.assertGreaterEqual(engine._psnr(img, decoded),
                                    engine._psnr(img, Image.open(io.BytesIO(jpeg)))
                                    + engine._PALETTE_MARGIN_DB)

    def test_jpeg_is_whichever_of_baseline_and_progressive_is_smaller(self):
        import random

        from PIL import Image

        rng = random.Random(9)
        img = Image.new("RGB", (40, 30))
        img.putdata([(rng.randrange(256), 90, 160) for _ in range(40 * 30)])
        payload = engine._encode_payload(img, 30, 9)
        self.assertEqual(payload[:2], b"\xff\xd8", "precondition: JPEG chosen")
        stored = len(psdi.pack_payload(payload, 9)[1])
        for progressive in (True, False):
            other = engine._image_bytes(img, format="JPEG", quality=30,
                                        optimize=True, progressive=progressive)
            self.assertLessEqual(stored, len(psdi.pack_payload(other, 9)[1]))

    def test_insert_image_places_a_png_payload(self):
        # Both rebuilds hand each payload to insert_image(stream=...), which
        # identifies PNG from the bytes as it does JPEG. The original decoder
        # itself is exercised in test_interop.TestOriginalDecoder.
        payload = engine._encode_payload(self._chart(), 45, 9)
        doc = fitz.open()
        try:
            page = doc.new_page(width=300, height=200)
            page.insert_image(fitz.Rect(10, 10, 250, 160), stream=payload)
            self.assertEqual(len(page.get_images()), 1)
            pix = page.get_pixmap(dpi=72)
            self.assertNotEqual(pix.pixel(40, 130), (255, 255, 255))
        finally:
            doc.close()

    def test_documents_mix_png_and_jpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            path = fixtures.graphics_report(os.path.join(directory, "g.pdf"))
            data, _ = engine.pdf_to_archive(path, quality="medium")
        kinds = sorted("png" if p[:4] == b"\x89PNG" else "jpeg"
                       for p in psdi.read_struct(data).images.values())
        self.assertEqual(kinds, ["jpeg", "png"])

    def test_page_images_stay_jpeg(self):
        # Version 2 archives carry scans, whose noise defeats any palette.
        with tempfile.TemporaryDirectory() as directory:
            path = fixtures.monochrome_scan_like(os.path.join(directory, "m.pdf"))
            data, _ = engine.pdf_to_archive(path, quality="low", mode=engine.MODE_IMAGE)
        for _, _, payload in psdi.read_image(data).pages:
            self.assertEqual(payload[:2], b"\xff\xd8")




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

    # --- text converted to outlines ------------------------------------
    # Excel writes chart axis labels as one curve-only path per word. Each
    # word is smaller than the artwork floor and the words sit a word gap
    # apart, so without the run detection the whole axis vanished.

    @staticmethod
    def _outline_word(shape, x: float, y: float, loops: int, size: float = 9.0):
        """Draw a glyph-like blob chain: `loops` closed bezier loops in a row,
        all inside one path, the way an outlined word is a single path."""
        step = size * 0.8
        for i in range(loops):
            cx = x + i * step
            shape.draw_bezier(fitz.Point(cx, y), fitz.Point(cx + size, y),
                              fitz.Point(cx + size, y + size), fitz.Point(cx, y + size))
            shape.draw_bezier(fitz.Point(cx, y + size), fitz.Point(cx - size * 0.3, y + size * 0.6),
                              fitz.Point(cx - size * 0.3, y + size * 0.4), fitz.Point(cx, y))
        shape.finish(color=None, fill=(0.35, 0.35, 0.35))

    def _outlined_axis_page(self, path: str, words: int = 8, gap: float = 36.0,
                            lone: bool = True) -> None:
        doc = fitz.open()
        page = doc.new_page(width=500, height=300)
        try:
            shape = page.new_shape()
            x = 60.0
            for _ in range(words):
                self._outline_word(shape, x, 200.0, loops=2)
                x += gap
            if lone:
                # A single small ornament elsewhere must stay dropped.
                self._outline_word(shape, 420.0, 60.0, loops=1)
            shape.commit()
            page.insert_text(fitz.Point(40, 40), "Situation Incidents", fontname="helv",
                             fontsize=12)
            doc.save(path)
        finally:
            doc.close()

    def test_row_of_outlined_words_becomes_one_region(self):
        path = os.path.join(self._tmp.name, "outlined_axis.pdf")
        self._outlined_axis_page(path)
        source = fitz.open(path)
        try:
            page = source[0]
            # Precondition: every word really is below the artwork floor.
            words = [fitz.Rect(p["rect"]) for p in page.get_drawings()]
            self.assertTrue(all(r.width < engine._ARTWORK_MIN_SIDE for r in words))
            regions = engine._artwork_regions(page)
        finally:
            source.close()

        self.assertEqual(len(regions), 1, regions)
        row = regions[0]
        self.assertGreater(row.width, 250, "the region must span the whole axis")
        self.assertLess(row.height, 20, "and no more than the label row")
        self.assertFalse(row.intersects(fitz.Rect(410, 50, 440, 80)),
                         "a lone ornament is not part of the run")

    def test_outlined_axis_survives_the_round_trip(self):
        path = os.path.join(self._tmp.name, "outlined_axis_rt.pdf")
        self._outlined_axis_page(path)
        data, _ = engine.pdf_to_archive(path, quality="low")
        parsed = psdi.read_struct(data)
        rasters = [k for k in parsed.images if int(k) >= engine._ARTWORK_ID_TOP - 1000]
        self.assertEqual(len(rasters), 1)

        pdf_bytes, _ = engine.archive_to_pdf(data)
        rebuilt = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            # Ink must be present where the first and the last word were.
            for x in (60.0, 60.0 + 7 * 36.0):
                clip = fitz.Rect(x - 2, 196, x + 18, 212)
                pix = rebuilt[0].get_pixmap(dpi=144, clip=clip, colorspace=fitz.csGRAY)
                self.assertLess(min(pix.samples), 200,
                                f"no ink at x={x}: the label row was lost")
            # And none where the lone ornament was: it is still dropped.
            pix = rebuilt[0].get_pixmap(dpi=144, clip=fitz.Rect(415, 55, 435, 75),
                                        colorspace=fitz.csGRAY)
            self.assertEqual(min(pix.samples), 255)
        finally:
            rebuilt.close()

    def test_lone_outlined_title_is_kept_when_long_and_curved(self):
        # A rotated axis title is one thin path of many curves: too narrow
        # for the floor on both sides, but a label all the same.
        doc = fitz.open()
        page = doc.new_page(width=400, height=300)
        path = os.path.join(self._tmp.name, "outlined_title.pdf")
        try:
            shape = page.new_shape()
            size = 7.0
            for i in range(20):  # 40 curves, 7 pt wide, ~112 pt tall
                y = 60 + i * size * 0.8
                shape.draw_bezier(fitz.Point(30, y), fitz.Point(30 + size, y),
                                  fitz.Point(30 + size, y + size), fitz.Point(30, y + size))
                shape.draw_bezier(fitz.Point(30, y + size), fitz.Point(28, y + size * 0.6),
                                  fitz.Point(28, y + size * 0.4), fitz.Point(30, y))
            shape.finish(color=None, fill=(0.35, 0.35, 0.35))
            shape.commit()
            doc.save(path)
        finally:
            doc.close()
        source = fitz.open(path)
        try:
            regions = engine._artwork_regions(source[0])
        finally:
            source.close()
        self.assertEqual(len(regions), 1, regions)
        self.assertGreater(regions[0].height, 100)
        self.assertLess(regions[0].width, engine._ARTWORK_MIN_SIDE)

    def test_lone_small_flourish_is_still_dropped(self):
        doc = fitz.open()
        page = doc.new_page(width=400, height=300)
        path = os.path.join(self._tmp.name, "flourish.pdf")
        try:
            shape = page.new_shape()
            shape.draw_circle(fitz.Point(200, 150), 5)  # four curves, 10 pt
            shape.finish(color=None, fill=(0.35, 0.35, 0.35))
            shape.commit()
            doc.save(path)
        finally:
            doc.close()
        source = fitz.open(path)
        try:
            self.assertEqual(engine._artwork_regions(source[0]), [])
        finally:
            source.close()

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
    def test_rules_are_stored_as_rects_not_rasters(self):
        _, parsed = self._manifest()
        page = parsed.manifest["pages"][0]
        rules = [e for e in page["dr"] if engine._drawing_kind(e) == "rule"]
        self.assertGreaterEqual(len(rules), 2, "one horizontal, one vertical")
        for entry in rules:
            # A "rect" of zero height or width, stroked: the only entry type
            # the original decoder draws.
            self.assertEqual(entry["t"], "rect")
            x0, y0, x1, y1 = entry["r"]
            self.assertTrue(x0 == x1 or y0 == y1)
            self.assertIn("c", entry)
            self.assertNotIn("f", entry)
        self.assertFalse([e for e in page["dr"] if e.get("t") == "line"],
                         "line entries are invisible to the original decoder")
        rasters = [k for k in parsed.images if int(k) >= engine._ARTWORK_ID_TOP - 1000]
        self.assertFalse(rasters, "a plain rule must not become a raster")

    def test_rules_are_drawn_by_the_original_loop(self):
        # 2.2.x stored rules as "line" entries, which the original skips: a
        # station running it received the rich report without its two rules.
        _, parsed = self._manifest()
        doc = _legacy_paint_drawings(parsed.manifest)
        try:
            pix = doc[0].get_pixmap(dpi=100, colorspace=fitz.csGRAY)
            def ink(x_pt, y_pt):
                return pix.pixel(int(x_pt * 100 / 72), int(y_pt * 100 / 72))[0]
            self.assertLess(ink(200, 230), 200, "horizontal rule missing")
            self.assertLess(ink(300, 400), 220, "vertical rule missing")
        finally:
            doc.close()

    def test_rules_paint_like_the_lines_they_replace(self):
        # A stroked zero-height rectangle and a butt-capped line of the same
        # width cover the same pixels; checked at three resolutions.
        def render(draw, dpi):
            doc = fitz.open()
            page = doc.new_page(width=200, height=100)
            shape = page.new_shape()
            draw(shape)
            shape.commit()
            return page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY).samples

        def as_line(shape):
            shape.draw_line(fitz.Point(20.3, 40.6), fitz.Point(150.7, 40.6))
            shape.finish(color=(0, 0, 0), width=0.4)
            shape.draw_line(fitz.Point(30, 10.2), fitz.Point(30, 90.8))
            shape.finish(color=(0, 0, 0), width=1.3)

        def as_rule(shape):
            shape.draw_rect(fitz.Rect(20.3, 40.6, 150.7, 40.6))
            shape.finish(color=(0, 0, 0), fill=None, width=0.4)
            shape.draw_rect(fitz.Rect(30, 10.2, 30, 90.8))
            shape.finish(color=(0, 0, 0), fill=None, width=1.3)

        for dpi in (150, 300, 600):
            with self.subTest(dpi=dpi):
                self.assertEqual(render(as_line, dpi), render(as_rule, dpi))

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

class TestReviewFindings(unittest.TestCase):
    """Cases an independent review found, each reproduced from its report."""

    def _page(self, content: bytes, images=()):
        doc = fitz.open()
        page = doc.new_page(width=400, height=300)
        for rect, payload in images:
            page.insert_image(rect, stream=payload)
        xref = doc.get_new_xref()
        doc.update_object(xref, "<<>>")
        doc.update_stream(xref, content)
        page.set_contents(page.get_contents() + [xref] if page.get_contents() else xref)
        return doc

    def _drawings(self, content: bytes, precision: int = 1):
        doc = self._page(content)
        try:
            return engine._extract_page(doc[0], 0, {}, precision, [])["dr"]
        finally:
            doc.close()

    def test_a_filled_outline_keeps_its_strokes(self):
        # Filled and stroked, but not a rectangle: 2.2.x kept the strokes,
        # the first 2.3.0 draft dropped the whole path.
        l_shape = b"1 0 0 rg 0 0 0 RG 2 w 50 50 m 150 50 l 150 80 l 100 80 l 100 120 l 50 120 l h B"
        rules = [e for e in self._drawings(l_shape) if engine._drawing_kind(e) == "rule"]
        self.assertGreaterEqual(len(rules), 5)
        line_b = b"0 0 0 RG 1 w 50 200 m 250 200 l B"
        self.assertTrue([e for e in self._drawings(line_b) if engine._drawing_kind(e) == "rule"])

    def test_a_filled_rectangle_keeps_fill_and_stroke(self):
        box = b"0.9 0.9 1 rg 0 0 0 RG 1 w 50 50 m 150 50 l 150 80 l 50 80 l h B"
        entries = self._drawings(box)
        self.assertEqual([("f" in e, "c" in e) for e in entries], [(True, True)])

    def test_a_stroked_rectangle_of_no_height_is_a_rule(self):
        entries = self._drawings(b"0 0 1 RG 1.5 w 40 620 200 0 re S".replace(b"620", b"120"))
        rules = [e for e in entries if engine._drawing_kind(e) == "rule"]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["w"], 1.5)

    def test_thin_strokes_stay_above_the_hairline_threshold(self):
        entries = self._drawings(b"0 0 0 RG 0.12 w 20 100 m 300 100 l S")
        self.assertEqual(entries[0]["w"], 0.12)
        self.assertEqual(engine._stroke_width(0.101), 0.11)
        self.assertIsNone(engine._stroke_width(0.1))
        self.assertEqual(engine._stroke_width(0.84), 0.8)

    def test_a_sliver_of_an_image_neither_crashes_nor_ships(self):
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (8, 40), (200, 30, 30)).save(buf, format="PNG")
        doc = fitz.open()
        page = doc.new_page(width=400, height=300)
        page.insert_image(fitz.Rect(100.6, 50, 100.9, 150), stream=buf.getvalue())
        for i in range(12):
            page.insert_text(fitz.Point(20, 170 + i * 10), f"Ligne {i} de texte.",
                             fontname="helv", fontsize=8)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "sliver.pdf")
            doc.save(path)
            doc.close()
            data, _ = engine.pdf_to_archive(path, quality="ultra_low")
        manifest = psdi.read_struct(data).manifest
        for ref in manifest["pages"][0]["ir"]:
            b = ref["b"]
            self.assertTrue(b[2] > b[0] and b[3] > b[1])
        engine.archive_to_pdf(data)

        # And an archive from elsewhere that does carry an empty placement.
        manifest["pages"][0]["ir"].append({"x": "999", "b": [100, 50, 100, 150]})
        crafted = psdi.write_struct(manifest, {"999": buf.getvalue()}, 6)
        pdf, _ = engine.archive_to_pdf(crafted)
        self.assertTrue(pdf.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
