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


# --- Limits on untrusted input ---------------------------------------------
# An archive arriving over the air is untrusted input. Every length in the
# container is attacker-controlled, and LZMA expands very aggressively: a
# 30 kB file can decompress to 200 MB, which was allocated in full before any
# parsing rejected it. The ceilings below are far above anything a real
# document produces -- a 500-page SITREP manifest compresses to a few hundred
# kilobytes -- so they never interfere with legitimate traffic.
#
# These are decode-side only. The encoder is untouched, so archives produced
# here are byte-identical to before and remain readable by other
# implementations.
MAX_ARCHIVE_SIZE = 64 * 1024 * 1024
MAX_MANIFEST_SIZE = 32 * 1024 * 1024
MAX_PAYLOAD_SIZE = 32 * 1024 * 1024
MAX_PAGES = 2000
MAX_IMAGES = 5000


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


def _lzma_decompress(data: bytes, limit: int = MAX_PAYLOAD_SIZE) -> bytes:
    """Decompress a raw LZMA2 stream, refusing to exceed `limit` bytes.

    LZMADecompressor is used rather than lzma.decompress because it can be
    fed incrementally and stopped: decompress(max_length=...) never allocates
    more than asked, so a decompression bomb is rejected after one chunk
    instead of after the whole expansion.
    """
    decompressor = lzma.LZMADecompressor(
        format=lzma.FORMAT_RAW, filters=_LZMA_DECODE_FILTERS
    )
    chunks: list[bytes] = []
    total = 0
    try:
        while not decompressor.eof:
            chunk = decompressor.decompress(b"" if chunks else data,
                                            max_length=1024 * 1024)
            if not chunk and not decompressor.needs_input:
                break
            total += len(chunk)
            if total > limit:
                raise PsdiError(
                    f"Decompressed data exceeds {limit} bytes; "
                    f"refusing to continue"
                )
            chunks.append(chunk)
            if decompressor.needs_input:
                break
    except lzma.LZMAError as exc:
        raise PsdiError(f"Corrupt compressed stream: {exc}") from exc
    return b"".join(chunks)


def _need(data: bytes, offset: int, length: int, what: str) -> None:
    """Refuse to read past the end of the archive."""
    if offset < 0 or length < 0 or offset + length > len(data):
        raise PsdiError(
            f"Truncated archive: {what} needs {length} bytes at offset "
            f"{offset}, only {max(0, len(data) - offset)} available"
        )


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
    if len(data) > MAX_ARCHIVE_SIZE:
        raise PsdiError(
            f"Archive is {len(data)} bytes, above the "
            f"{MAX_ARCHIVE_SIZE} byte ceiling"
        )
    if len(data) < 6:
        raise PsdiError("Archive too short")
    if data[:4] != ARCHIVE_MAGIC:
        raise PsdiError("PSDI signature missing")
    return struct.unpack_from("<H", data, 4)[0]


def validate(data: bytes) -> dict:
    """Check an archive without fully decoding it.

    Returns a report rather than raising, because the caller is usually
    showing the operator whether a file that just arrived over the air is
    intact enough to be worth rebuilding. Every rejection reason is a
    PsdiError message, so the report says what is wrong rather than surfacing
    an LZMAError or a struct.error.
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
            # Parsing the whole thing is what the caller will do next anyway,
            # and it is the only way to catch a manifest that is internally
            # consistent but references pages that do not exist.
            parsed = read_struct(data)
            report["checksum_ok"] = parsed.crc_ok
            report["pages"] = len(parsed.manifest["pages"])
            report["valid"] = True
        elif version == ARCHIVE_VERSION_IMAGE:
            parsed_image = read_image(data)
            report["pages"] = len(parsed_image.pages)
            # There is no manifest to checksum in image mode.
            report["checksum_ok"] = True
            report["valid"] = True
        else:
            report["error"] = f"Unsupported version: {version}"
    except PsdiError as exc:
        report["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - report, never propagate
        report["error"] = f"Validation failed: {type(exc).__name__}: {exc}"

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
    """Parse a version 1 archive, rejecting anything inconsistent."""
    pos = 6
    _need(data, pos, 8, "manifest header")
    checksum, manifest_size = struct.unpack_from("<II", data, pos)
    pos += 8

    if manifest_size > MAX_MANIFEST_SIZE:
        raise PsdiError(
            f"Manifest claims {manifest_size} compressed bytes, above the "
            f"{MAX_MANIFEST_SIZE} byte ceiling"
        )
    _need(data, pos, manifest_size, "manifest")

    manifest_bytes = _lzma_decompress(data[pos : pos + manifest_size],
                                      MAX_MANIFEST_SIZE)
    pos += manifest_size

    crc_ok = (zlib.crc32(manifest_bytes) & 0xFFFFFFFF) == checksum

    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PsdiError(f"Manifest is not valid JSON: {exc}") from exc

    _check_manifest(manifest)

    _need(data, pos, 2, "image count")
    nb_images = struct.unpack_from("<H", data, pos)[0]
    pos += 2

    if nb_images > MAX_IMAGES:
        raise PsdiError(f"Implausible image count: {nb_images}")

    images: dict[str, bytes] = {}
    for index in range(nb_images):
        _need(data, pos, 7, f"image {index} header")
        xref_id = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        flags = struct.unpack_from("<B", data, pos)[0]
        pos += 1
        size = struct.unpack_from("<I", data, pos)[0]
        pos += 4

        if size > MAX_PAYLOAD_SIZE:
            raise PsdiError(
                f"Image {xref_id} claims {size} bytes, above the "
                f"{MAX_PAYLOAD_SIZE} byte ceiling"
            )
        _need(data, pos, size, f"image {xref_id} payload")

        images[str(xref_id)] = unpack_payload(flags, data[pos : pos + size])
        pos += size

    return StructArchive(manifest=manifest, images=images, crc_ok=crc_ok)


def _check_manifest(manifest) -> None:
    """Cross-check the manifest before anything draws from it.

    The rebuild loop indexes page_sizes by page number. A manifest with more
    pages than sizes passes the CRC check -- it is internally consistent, just
    wrong -- and then raises IndexError halfway through building a document.
    Catching it here means a damaged transmission is reported as such instead
    of surfacing as a stack trace from deep inside the renderer.
    """
    if not isinstance(manifest, dict):
        raise PsdiError("Manifest is not an object")

    pages = manifest.get("pages")
    sizes = manifest.get("page_sizes")
    if not isinstance(pages, list) or not isinstance(sizes, list):
        raise PsdiError("Manifest is missing its pages or page_sizes list")

    if len(pages) > MAX_PAGES:
        raise PsdiError(f"Implausible page count: {len(pages)}")

    for page in pages:
        if not isinstance(page, dict):
            raise PsdiError("Manifest contains a malformed page entry")
        number = page.get("pn")
        if not isinstance(number, int) or not 1 <= number <= len(sizes):
            raise PsdiError(
                f"Page number {number!r} has no matching entry in page_sizes "
                f"({len(sizes)} present)"
            )

    for size in sizes:
        if not isinstance(size, dict):
            raise PsdiError("Manifest contains a malformed page size")
        width, height = size.get("w"), size.get("h")
        for value in (width, height):
            if not isinstance(value, (int, float)) or not 0 < value <= 20000:
                raise PsdiError(f"Implausible page dimension: {value!r}")


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
    """Parse a version 2 archive, rejecting anything inconsistent."""
    pos = 6
    _need(data, pos, 2, "page count")
    nb_pages = struct.unpack_from("<H", data, pos)[0]
    pos += 2

    if not 0 < nb_pages <= MAX_PAGES:
        raise PsdiError(f"Implausible page count: {nb_pages}")

    pages: list[tuple[float, float, bytes]] = []
    for index in range(nb_pages):
        _need(data, pos, 13, f"page {index + 1} header")
        width = struct.unpack_from("<f", data, pos)[0]
        pos += 4
        height = struct.unpack_from("<f", data, pos)[0]
        pos += 4
        flags = struct.unpack_from("<B", data, pos)[0]
        pos += 1
        size = struct.unpack_from("<I", data, pos)[0]
        pos += 4

        for value in (width, height):
            if not 0 < value <= 20000:
                raise PsdiError(
                    f"Page {index + 1} has an implausible dimension: {value}"
                )
        if size > MAX_PAYLOAD_SIZE:
            raise PsdiError(
                f"Page {index + 1} claims {size} bytes, above the "
                f"{MAX_PAYLOAD_SIZE} byte ceiling"
            )
        _need(data, pos, size, f"page {index + 1} payload")

        pages.append((width, height, unpack_payload(flags, data[pos : pos + size])))
        pos += size

    return ImageArchive(pages=pages)
