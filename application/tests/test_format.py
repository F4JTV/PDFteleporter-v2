"""Container-level tests.

The point of this file is the receive path. An archive arriving over the air is
untrusted input: every length in it is attacker-controlled and LZMA expands
aggressively. These tests assert that malformed input produces a PsdiError
naming the problem, never a struct.error, an LZMAError, an IndexError, or a
multi-hundred-megabyte allocation.
"""

from __future__ import annotations

import json
import lzma
import struct
import unittest
import zlib

from psditool import format as psdi


def _manifest_archive(manifest: dict, images: dict | None = None) -> bytes:
    """Build a version 1 archive around an arbitrary manifest.

    Uses the real writer so the container is well-formed; only the manifest
    contents are under test.
    """
    return psdi.write_struct(manifest, images or {}, 6)


def _raw_struct(manifest_bytes: bytes, checksum: int | None = None) -> bytes:
    """Assemble a version 1 archive by hand, bypassing every writer check."""
    compressed = lzma.compress(
        manifest_bytes, format=lzma.FORMAT_RAW,
        filters=[{"id": lzma.FILTER_LZMA2, "preset": 6}],
    )
    if checksum is None:
        checksum = zlib.crc32(manifest_bytes) & 0xFFFFFFFF
    return (
        psdi.ARCHIVE_MAGIC
        + struct.pack("<H", psdi.ARCHIVE_VERSION_STRUCT)
        + struct.pack("<II", checksum, len(compressed))
        + compressed
        + struct.pack("<H", 0)
    )


VALID_MANIFEST = {
    "v": "1.0.6",
    "src": "test.pdf",
    "pages": [{"pn": 1, "tb": [], "ir": [], "dr": [], "ck": []}],
    "page_sizes": [{"w": 595.0, "h": 842.0}],
    "images": {},
}


class TestSignature(unittest.TestCase):
    def test_empty(self):
        with self.assertRaises(psdi.PsdiError):
            psdi.peek_version(b"")

    def test_wrong_magic(self):
        with self.assertRaises(psdi.PsdiError):
            psdi.peek_version(b"XXXX\x01\x00")

    def test_too_large(self):
        oversized = psdi.ARCHIVE_MAGIC + b"\x00" * (psdi.MAX_ARCHIVE_SIZE + 1)
        with self.assertRaises(psdi.PsdiError):
            psdi.peek_version(oversized)

    def test_valid(self):
        self.assertEqual(psdi.peek_version(_raw_struct(b"{}")), 1)


class TestTruncation(unittest.TestCase):
    """Every prefix of a good archive must be rejected, never crash."""

    def setUp(self):
        self.good = _manifest_archive(VALID_MANIFEST)

    def test_every_prefix_is_handled(self):
        for cut in range(0, len(self.good), 7):
            with self.subTest(bytes=cut):
                report = psdi.validate(self.good[:cut])
                self.assertFalse(report["valid"])
                self.assertIsNotNone(report["error"])

    def test_prefix_raises_psdi_error_only(self):
        for cut in range(6, len(self.good), 13):
            with self.subTest(bytes=cut):
                with self.assertRaises(psdi.PsdiError):
                    psdi.read_struct(self.good[:cut])

    def test_full_archive_reads(self):
        parsed = psdi.read_struct(self.good)
        self.assertTrue(parsed.crc_ok)
        self.assertEqual(len(parsed.manifest["pages"]), 1)


class TestDecompressionLimits(unittest.TestCase):
    def test_bomb_is_refused_without_allocating(self):
        # 200 MB of zeroes compresses to a few tens of kilobytes. Before the
        # limit existed, this expanded in full before parsing rejected it.
        bomb = lzma.compress(
            b"\x00" * (200 * 1024 * 1024), format=lzma.FORMAT_RAW,
            filters=[{"id": lzma.FILTER_LZMA2, "preset": 9}],
        )
        self.assertLess(len(bomb), 100 * 1024, "fixture is not a bomb")
        archive = (
            psdi.ARCHIVE_MAGIC + struct.pack("<H", 1)
            + struct.pack("<II", 0, len(bomb)) + bomb
        )
        with self.assertRaises(psdi.PsdiError):
            psdi.read_struct(archive)

    def test_declared_manifest_size_above_ceiling(self):
        archive = (
            psdi.ARCHIVE_MAGIC + struct.pack("<H", 1)
            + struct.pack("<II", 0, psdi.MAX_MANIFEST_SIZE + 1)
        )
        with self.assertRaises(psdi.PsdiError):
            psdi.read_struct(archive)

    def test_legitimate_large_payload_still_decompresses(self):
        # The ceilings must not interfere with real traffic.
        payload = b"A" * (3 * 1024 * 1024)
        compressed = lzma.compress(
            payload, format=lzma.FORMAT_RAW,
            filters=[{"id": lzma.FILTER_LZMA2, "preset": 9}],
        )
        self.assertEqual(psdi._lzma_decompress(compressed), payload)

    def test_corrupt_stream_reported_as_psdi_error(self):
        with self.assertRaises(psdi.PsdiError):
            psdi._lzma_decompress(b"not an lzma stream at all")


class TestManifestConsistency(unittest.TestCase):
    """A manifest can be internally consistent and still describe nothing."""

    def test_more_pages_than_sizes(self):
        # This used to pass validation with a correct CRC, then raise
        # IndexError from inside the renderer.
        manifest = dict(VALID_MANIFEST)
        manifest["pages"] = [{"pn": 1}, {"pn": 2}]
        manifest["page_sizes"] = [{"w": 595, "h": 842}]
        with self.assertRaises(psdi.PsdiError):
            psdi.read_struct(_manifest_archive(manifest))

    def test_missing_pages_key(self):
        with self.assertRaises(psdi.PsdiError):
            psdi.read_struct(_raw_struct(b'{"src":"x"}'))

    def test_manifest_not_an_object(self):
        with self.assertRaises(psdi.PsdiError):
            psdi.read_struct(_raw_struct(b'[1,2,3]'))

    def test_manifest_not_json(self):
        with self.assertRaises(psdi.PsdiError):
            psdi.read_struct(_raw_struct(b'this is not json'))

    def test_page_count_above_ceiling(self):
        manifest = dict(VALID_MANIFEST)
        count = psdi.MAX_PAGES + 1
        manifest["pages"] = [{"pn": i + 1} for i in range(count)]
        manifest["page_sizes"] = [{"w": 595, "h": 842}] * count
        with self.assertRaises(psdi.PsdiError):
            psdi.read_struct(_manifest_archive(manifest))

    def test_implausible_page_dimension(self):
        for width in (0, -1, 1e9):
            with self.subTest(width=width):
                manifest = dict(VALID_MANIFEST)
                manifest["page_sizes"] = [{"w": width, "h": 842}]
                with self.assertRaises(psdi.PsdiError):
                    psdi.read_struct(_manifest_archive(manifest))

    def test_bad_page_number(self):
        for number in (0, -3, "one", None):
            with self.subTest(pn=number):
                manifest = dict(VALID_MANIFEST)
                manifest["pages"] = [{"pn": number}]
                with self.assertRaises(psdi.PsdiError):
                    psdi.read_struct(_manifest_archive(manifest))


class TestChecksum(unittest.TestCase):
    def test_bad_crc_is_reported_not_fatal(self):
        # A CRC mismatch means the transmission was damaged, but the content
        # may still be usable. It must be reported, not raised: the operator
        # decides whether to rebuild anyway.
        archive = _raw_struct(json.dumps(VALID_MANIFEST).encode(),
                             checksum=0xDEADBEEF)
        parsed = psdi.read_struct(archive)
        self.assertFalse(parsed.crc_ok)
        self.assertEqual(psdi.validate(archive)["checksum_ok"], False)
        self.assertTrue(psdi.validate(archive)["valid"])


class TestImageArchive(unittest.TestCase):
    def test_implausible_page_count(self):
        archive = psdi.ARCHIVE_MAGIC + struct.pack("<HH", 2, 65535)
        with self.assertRaises(psdi.PsdiError):
            psdi.read_image(archive)

    def test_zero_pages(self):
        archive = psdi.ARCHIVE_MAGIC + struct.pack("<HH", 2, 0)
        with self.assertRaises(psdi.PsdiError):
            psdi.read_image(archive)

    def test_implausible_dimension(self):
        archive = (
            psdi.ARCHIVE_MAGIC + struct.pack("<HH", 2, 1)
            + struct.pack("<ffBI", 1e9, 842.0, 0, 0)
        )
        with self.assertRaises(psdi.PsdiError):
            psdi.read_image(archive)

    def test_payload_beyond_end(self):
        archive = (
            psdi.ARCHIVE_MAGIC + struct.pack("<HH", 2, 1)
            + struct.pack("<ffBI", 595.0, 842.0, 0, 10_000)
        )
        with self.assertRaises(psdi.PsdiError):
            psdi.read_image(archive)

    def test_round_trip(self):
        pages = [(595.0, 842.0, b"\xff\xd8fake jpeg\xff\xd9")]
        parsed = psdi.read_image(psdi.write_image(pages))
        self.assertEqual(len(parsed.pages), 1)
        self.assertEqual(parsed.pages[0][2], pages[0][2])


class TestValidateNeverRaises(unittest.TestCase):
    """validate() is the operator-facing check; it must always return."""

    CASES = [
        b"",
        b"PSDI",
        b"PSDI\x01\x00",
        b"PSDI\x63\x00",                       # unsupported version
        b"PSDI\x01\x00" + b"\xff" * 32,
        b"PSDI\x02\x00" + b"\x00" * 64,
        bytes(range(256)),
    ]

    def test_all_cases_return_a_report(self):
        for case in self.CASES:
            with self.subTest(prefix=case[:8]):
                report = psdi.validate(case)
                self.assertIn("valid", report)
                self.assertIsInstance(report["valid"], bool)
                if not report["valid"]:
                    self.assertIsNotNone(report["error"])


class TestPayloadPacking(unittest.TestCase):
    def test_incompressible_payload_stored_raw(self):
        # JPEG data is already entropy-coded; LZMA inflates it, so the writer
        # must store it verbatim and clear the flag.
        import os

        noise = os.urandom(4096)
        flags, packed = psdi.pack_payload(noise, 6)
        self.assertEqual(flags, 0)
        self.assertEqual(packed, noise)
        self.assertEqual(psdi.unpack_payload(flags, packed), noise)

    def test_compressible_payload_stored_compressed(self):
        text = b"situation reconnaissance secteur " * 200
        flags, packed = psdi.pack_payload(text, 6)
        self.assertEqual(flags, psdi.FLAG_LZMA)
        self.assertLess(len(packed), len(text))
        self.assertEqual(psdi.unpack_payload(flags, packed), text)


if __name__ == "__main__":
    unittest.main()
