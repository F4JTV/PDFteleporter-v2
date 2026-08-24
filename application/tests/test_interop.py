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
