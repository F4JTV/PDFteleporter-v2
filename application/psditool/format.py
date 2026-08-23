"""The .psdi container format.

Layout, little-endian throughout:

    [0:4]   magic b'PSDI'
    [4:6]   uint16  version

Version 1 (STRUCT) -- text, vector rectangles and images kept separate:

    uint32  crc32 of the *uncompressed* manifest
    uint32  compressed manifest length
    bytes   manifest, JSON, LZMA raw stream with a single LZMA2 filter
    uint16  image count
    per image:
        uint16  PDF xref id
        uint8   flags, bit 0 set when the payload is LZMA compressed
        uint32  payload length
        bytes   payload

Version 2 (IMAGE) -- every page flattened to a single JPEG:

    uint16  page count
    per page:
        float32 page width in points
        float32 page height in points
        uint8   flags, bit 0 set when the payload is LZMA compressed
        uint32  payload length
        bytes   payload

This layout is an interoperability contract with other .psdi implementations
on the air. Do not reorder or resize fields.
"""

from __future__ import annotations

import io
import json
import lzma
import struct
import zlib
from dataclasses import dataclass, field

ARCHIVE_MAGIC = b"PSDI"
ARCHIVE_VERSION_STRUCT = 1
ARCHIVE_VERSION_IMAGE = 2

FLAG_LZMA = 0x01

_LZMA_DECODE_FILTERS = [{"id": lzma.FILTER_LZMA2}]

# The manifest is JSON: byte-aligned text with no 4-byte structure, so the
# default position bits only dilute the range coder's context. Setting pb=0
# and asking for the extreme match finder costs encode time and buys a couple
# of percent. LZMA2 records these in its chunk headers, so a decoder using the
# plain filter spec above still reads the result -- the format stays
# interoperable.
_LZMA_TEXT_FILTERS = [
    {"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME, "pb": 0}
]


class PsdiError(Exception):
    """Raised when an archive cannot be parsed or is internally inconsistent."""


def _lzma_compress(data: bytes, preset: int) -> bytes:
    return lzma.compress(
        data,
        format=lzma.FORMAT_RAW,
        filters=[{"id": lzma.FILTER_LZMA2, "preset": preset}],
    )


def _lzma_compress_text(data: bytes) -> bytes:
    """Compress manifest JSON with parameters tuned for text."""
    return lzma.compress(data, format=lzma.FORMAT_RAW,
                         filters=_LZMA_TEXT_FILTERS)


def _lzma_decompress(data: bytes) -> bytes:
    return lzma.decompress(data, format=lzma.FORMAT_RAW, filters=_LZMA_DECODE_FILTERS)


def pack_payload(raw: bytes, preset: int) -> tuple[int, bytes]:
    """Compress a payload only when compression actually pays off.

    JPEG data is already entropy-coded, so LZMA usually inflates it. The flag
    byte records which of the two we chose, so the reader never has to guess.
    """
    packed = _lzma_compress(raw, preset)
    if len(packed) < len(raw):
        return FLAG_LZMA, packed
    return 0, raw


def unpack_payload(flags: int, data: bytes) -> bytes:
    return _lzma_decompress(data) if flags & FLAG_LZMA else data


@dataclass
class StructArchive:
    """The parsed contents of a version 1 archive."""

    manifest: dict
    images: dict[str, bytes] = field(default_factory=dict)
    crc_ok: bool = True


@dataclass
class ImageArchive:
    """The parsed contents of a version 2 archive."""

    pages: list[tuple[float, float, bytes]] = field(default_factory=list)


def peek_version(data: bytes) -> int:
    """Return the archive version, raising if this is not a .psdi at all."""
    if len(data) < 6:
        raise PsdiError("Archive too short")
    if data[:4] != ARCHIVE_MAGIC:
        raise PsdiError("PSDI signature missing")
    return struct.unpack_from("<H", data, 4)[0]


def validate(data: bytes) -> dict:
    """Check an archive without fully decoding it.

    Returns a report rather than raising, because the caller is usually
    showing the operator whether a file that just arrived over the air is
    intact enough to be worth rebuilding.
    """
    report = {
        "valid": False,
        "version": None,
        "checksum_ok": False,
        "pages": None,
        "error": None,
    }
    try:
        version = peek_version(data)
    except PsdiError as exc:
        report["error"] = str(exc)
        return report

    report["version"] = version

    try:
        if version == ARCHIVE_VERSION_STRUCT:
            checksum, manifest_size = struct.unpack_from("<II", data, 6)
            manifest_bytes = _lzma_decompress(data[14 : 14 + manifest_size])
            report["checksum_ok"] = (
                zlib.crc32(manifest_bytes) & 0xFFFFFFFF
            ) == checksum
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            report["pages"] = len(manifest.get("pages", []))
            report["valid"] = True
        elif version == ARCHIVE_VERSION_IMAGE:
            nb_pages = struct.unpack_from("<H", data, 6)[0]
            report["pages"] = nb_pages
            # There is no manifest to checksum in image mode.
            report["checksum_ok"] = True
            report["valid"] = 0 < nb_pages < 1000
            if not report["valid"]:
                report["error"] = f"Implausible page count: {nb_pages}"
        else:
            report["error"] = f"Unsupported version: {version}"
    except Exception as exc:  # noqa: BLE001 - report, never propagate
        report["error"] = f"Validation failed: {exc}"

    return report


def write_struct(manifest: dict, images: dict[str, bytes],
                 lzma_preset: int = 6) -> bytes:
    """Serialise a version 1 archive."""
    manifest_json = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    manifest_bytes = manifest_json.encode("utf-8")
    manifest_compressed = _lzma_compress_text(manifest_bytes)

    out = io.BytesIO()
    out.write(ARCHIVE_MAGIC)
    out.write(struct.pack("<H", ARCHIVE_VERSION_STRUCT))
    out.write(struct.pack("<I", zlib.crc32(manifest_bytes) & 0xFFFFFFFF))
    out.write(struct.pack("<I", len(manifest_compressed)))
    out.write(manifest_compressed)

    out.write(struct.pack("<H", len(images)))
    # Sorted numerically so two runs over the same document are byte-identical.
    for xref_str in sorted(images, key=int):
        flags, payload = pack_payload(images[xref_str], lzma_preset)
        out.write(struct.pack("<H", int(xref_str)))
        out.write(struct.pack("<B", flags))
        out.write(struct.pack("<I", len(payload)))
        out.write(payload)

    return out.getvalue()


def read_struct(data: bytes) -> StructArchive:
    """Parse a version 1 archive."""
    pos = 6
    checksum, manifest_size = struct.unpack_from("<II", data, pos)
    pos += 8

    manifest_bytes = _lzma_decompress(data[pos : pos + manifest_size])
    pos += manifest_size

    crc_ok = (zlib.crc32(manifest_bytes) & 0xFFFFFFFF) == checksum
    manifest = json.loads(manifest_bytes.decode("utf-8"))

    nb_images = struct.unpack_from("<H", data, pos)[0]
    pos += 2

    images: dict[str, bytes] = {}
    for _ in range(nb_images):
        xref_id = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        flags = struct.unpack_from("<B", data, pos)[0]
        pos += 1
        size = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        images[str(xref_id)] = unpack_payload(flags, data[pos : pos + size])
        pos += size

    return StructArchive(manifest=manifest, images=images, crc_ok=crc_ok)


def write_image(pages: list[tuple[float, float, bytes]],
                lzma_preset: int = 6) -> bytes:
    """Serialise a version 2 archive."""
    out = io.BytesIO()
    out.write(ARCHIVE_MAGIC)
    out.write(struct.pack("<H", ARCHIVE_VERSION_IMAGE))
    out.write(struct.pack("<H", len(pages)))

    for width, height, jpeg in pages:
        flags, payload = pack_payload(jpeg, lzma_preset)
        out.write(struct.pack("<f", width))
        out.write(struct.pack("<f", height))
        out.write(struct.pack("<B", flags))
        out.write(struct.pack("<I", len(payload)))
        out.write(payload)

    return out.getvalue()


def read_image(data: bytes) -> ImageArchive:
    """Parse a version 2 archive."""
    pos = 6
    nb_pages = struct.unpack_from("<H", data, pos)[0]
    pos += 2

    pages: list[tuple[float, float, bytes]] = []
    for _ in range(nb_pages):
        width = struct.unpack_from("<f", data, pos)[0]
        pos += 4
        height = struct.unpack_from("<f", data, pos)[0]
        pos += 4
        flags = struct.unpack_from("<B", data, pos)[0]
        pos += 1
        size = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        pages.append((width, height, unpack_payload(flags, data[pos : pos + size])))
        pos += size

    return ImageArchive(pages=pages)
