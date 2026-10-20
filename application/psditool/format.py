# Copyright (C) 2026 The PDF Teleporter contributors
# Portions derived from PDF Teleporter by F1GBD (Jean-Louis Naudin),
# ADRASEC 77 - FNRASEC, author of the original program and of the .psdi
# format.
# SPDX-License-Identifier: GPL-3.0-only
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

Versions 3 and 4 are the compact archives, saved as .psdz. They are read only
by this implementation, from 2.5.0 on; any other reader, the original
included, refuses them by version number instead of misreading them.

Version 3 (COMPACT STRUCT) -- the version 1 layout, with the JSON manifest
replaced by a columnar body:

    uint32  crc32 of the *uncompressed* body
    uint32  compressed body length
    bytes   body, LZMA raw stream with a single LZMA2 filter
    uint16  image count, then the images exactly as in version 1

The body starts with one byte. 1 means the rest is the JSON manifest of a
version 1 archive, written for a manifest the columns cannot describe. 0
means the rest is eleven sections, each a uint32 length and its bytes:

    header   JSON: the manifest's top-level keys other than "pages" ("m"),
             the page count ("n"), the coordinate scale ("q": 1, 10 or 100),
             the span and drawing style tables ("ss", "ds"), and whether
             image references were strings ("xs")
    pages    per page: key mask, page number, counts, open-box corner
    lines    per line: span count and two flags
    lboxes   line boxes, delta-coded from the previous line
    spans    per span: index into the span style table
    sboxes   span boxes, relative to their line
    dstyles  per drawing: index into the drawing style table
    dboxes   drawing boxes, delta-coded from the previous drawing of the
             same style
    extras   image placements, ticked boxes and link boxes
    lengths  byte length of every string, in order
    text     every string, UTF-8, concatenated

Numbers in the integer sections are LEB128 varints, signed ones zigzag-
encoded; coordinates are integers in units of 1/scale point. Decoding gives
back a manifest equal to the one encoded, which the writer verifies before
it commits to the columns.

Version 4 (COMPACT IMAGE) -- the version 2 layout unchanged.

Image payloads in versions 3 and 4 may also be AVIF or WebP, identified by
their signature like JPEG and PNG.
"""

from __future__ import annotations

import io
import json
import lzma
import math
import re
import struct
import zlib
from collections import Counter
from dataclasses import dataclass, field

ARCHIVE_MAGIC = b"PSDI"
ARCHIVE_VERSION_STRUCT = 1
ARCHIVE_VERSION_IMAGE = 2
# The compact archives keep the magic and the version field, so a reader that
# predates them -- the original included -- refuses them by number
# ("unsupported version") instead of misreading them.
ARCHIVE_VERSION_COMPACT_STRUCT = 3
ARCHIVE_VERSION_COMPACT_IMAGE = 4
COMPACT_VERSIONS = (ARCHIVE_VERSION_COMPACT_STRUCT, ARCHIVE_VERSION_COMPACT_IMAGE)

# Readers go by the version inside the file. The extension is for people and
# for Explorer: it tells an operator, before sending, whether a station
# running another implementation will be able to open the file.
EXTENSION = ".psdi"
COMPACT_EXTENSION = ".psdz"
ARCHIVE_EXTENSIONS = (EXTENSION, COMPACT_EXTENSION)


def extension_for(version: int) -> str:
    """The file extension an archive of this version is saved under."""
    return COMPACT_EXTENSION if version in COMPACT_VERSIONS else EXTENSION


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


# A raw LZMA2 stream does not record its dictionary size: the decoder brings
# its own, and one built on the plain filter spec -- the original included --
# gets liblzma's default of 8 MiB. Matches further back than that would fail
# to decode there, so the encoder never looks further.
_LZMA_MAX_DICT = 8 << 20
_LZMA_MIN_DICT = 4 << 10


def _dict_size_for(length: int) -> int:
    """The smallest dictionary that still reaches the start of the data.

    A dictionary larger than the input finds no extra match, so the output is
    the same; but preset 9 asks for 64 MiB, and allocating it cost 38 ms per
    call -- most of the compression time on a document of small images, each
    payload compressed on its own.
    """
    size = _LZMA_MIN_DICT
    while size < length and size < _LZMA_MAX_DICT:
        size <<= 1
    return size


def _lzma_compress(data: bytes, preset: int) -> bytes:
    return lzma.compress(
        data,
        format=lzma.FORMAT_RAW,
        filters=[{"id": lzma.FILTER_LZMA2, "preset": preset,
                  "dict_size": _dict_size_for(len(data))}],
    )


def _lzma_compress_text(data: bytes) -> bytes:
    """Compress manifest JSON with parameters tuned for text."""
    filters = [dict(_LZMA_TEXT_FILTERS[0], dict_size=_dict_size_for(len(data)))]
    return lzma.compress(data, format=lzma.FORMAT_RAW, filters=filters)


# A coordinate array of the manifest -- a box "b" or a rectangle "r" -- holding
# numbers only. A quote inside a JSON string is always escaped, so '"b":[' can
# only ever be a key.
_COORDINATE_ARRAY = re.compile(r'"([br])":\[([^\[\]"]*)\]')
_PLAIN_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
# Characters per coordinate: 1190.5, the width of an A3 landscape page, at a
# tenth of a point; four digits at whole points.
_DECIMAL_WIDTH = 6
_INTEGER_WIDTH = 4


def _manifest_json(manifest: dict) -> str:
    """The manifest as JSON, its coordinates written at a fixed width.

    JSON reads 84.100 as 84.1 and ignores the spaces in [  84, 102], so every
    decoder parses the same numbers, and the manifest stays valid JSON. What
    changes is LZMA's view of it: with every rectangle the same length, the
    next one starts at the same distance from the last, and LZMA codes a
    repeat at an unchanged distance far more cheaply than one at a new
    distance. Rows of table borders differ from one another by a coordinate
    or two; on the two situation sheets, writing them at a fixed width took
    11 to 13 % off the compressed manifest at a tenth of a point, and 2 to
    3 % at whole points. Decimal coordinates are padded with trailing zeros,
    whole ones with leading spaces -- trailing zeros would add a decimal
    point to every one of them.

    The text is checked to parse back to the same manifest; if it ever did
    not, the plain encoding is used.
    """
    text = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    arrays = _COORDINATE_ARRAY.findall(text)
    if not arrays:
        return text
    decimal = any("." in body for _, body in arrays)

    def pad(token: str) -> str:
        if not _PLAIN_NUMBER.fullmatch(token):
            return token
        if decimal:
            if "." not in token:
                token += ".0"
            return token + "0" * (_DECIMAL_WIDTH - len(token))
        return " " * (_INTEGER_WIDTH - len(token)) + token

    aligned = _COORDINATE_ARRAY.sub(
        lambda match: '"%s":[%s]' % (
            match.group(1), ",".join(pad(token) for token in match.group(2).split(","))),
        text,
    )
    try:
        if json.loads(aligned) != json.loads(text):
            return text
    except ValueError:
        return text
    return aligned


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


def payload_kind(data: bytes) -> str | None:
    """Name the image codec of a payload from its signature, or None.

    JPEG and PNG are what every decoder of the format hands to MuPDF; AVIF
    and WebP occur only in compact archives, and MuPDF reads neither, so the
    rebuild has to recognise them and decode them itself.
    """
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:8] == b"ftyp":
        # An ISO-BMFF file type box: its major and compatible brands follow
        # the minor version. AVIF announces itself as "avif" (or "avis" for
        # a sequence) in either place.
        size = int.from_bytes(data[:4], "big")
        brands = data[8:min(size, 64, len(data))]
        names = {brands[i:i + 4] for i in range(0, len(brands) - 3, 4)}
        if names & {b"avif", b"avis"}:
            return "avif"
    return None


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
        if version in (ARCHIVE_VERSION_STRUCT, ARCHIVE_VERSION_COMPACT_STRUCT):
            # Parsing the whole thing is what the caller will do next anyway,
            # and it is the only way to catch a manifest that is internally
            # consistent but references pages that do not exist.
            reader = (read_struct if version == ARCHIVE_VERSION_STRUCT
                      else read_compact_struct)
            parsed = reader(data)
            report["checksum_ok"] = parsed.crc_ok
            report["pages"] = len(parsed.manifest["pages"])
            report["valid"] = True
        elif version in (ARCHIVE_VERSION_IMAGE, ARCHIVE_VERSION_COMPACT_IMAGE):
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
    manifest_bytes = _manifest_json(manifest).encode("utf-8")
    manifest_compressed = _lzma_compress_text(manifest_bytes)

    out = io.BytesIO()
    out.write(ARCHIVE_MAGIC)
    out.write(struct.pack("<H", ARCHIVE_VERSION_STRUCT))
    out.write(struct.pack("<I", zlib.crc32(manifest_bytes) & 0xFFFFFFFF))
    out.write(struct.pack("<I", len(manifest_compressed)))
    out.write(manifest_compressed)
    _write_images(out, images, lzma_preset)
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

    return StructArchive(manifest=manifest, images=_read_images(data, pos),
                         crc_ok=crc_ok)


def _write_images(out: io.BytesIO, images: dict[str, bytes], lzma_preset: int) -> None:
    """The image records shared by versions 1 and 3."""
    out.write(struct.pack("<H", len(images)))
    # Sorted numerically so two runs over the same document are byte-identical.
    for xref_str in sorted(images, key=int):
        flags, payload = pack_payload(images[xref_str], lzma_preset)
        out.write(struct.pack("<H", int(xref_str)))
        out.write(struct.pack("<B", flags))
        out.write(struct.pack("<I", len(payload)))
        out.write(payload)


def _read_images(data: bytes, pos: int) -> dict[str, bytes]:
    """Parse the image records shared by versions 1 and 3."""
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

    return images


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


# --- Version 3: the columnar manifest -----------------------------------------
# The version 1 manifest is JSON, compressed by LZMA. Measured on the test
# documents, no general-purpose compressor does better on that JSON -- zstd,
# brotli, bzip2 and PPMd range from 3 % smaller to 16 % larger. What does
# better is writing the same content differently: every key name disappears,
# coordinates become small integers coded as the change from the previous
# box, styles repeat as an index into a table, and the strings sit together.
# LZMA then sees long runs of like data instead of interleaved text and
# numbers: 20 to 47 % off the compressed manifest of the real documents.

_BODY_COLUMNS = 0
_BODY_JSON = 1

# Integer streams of a columnar body, in the order they are stored; the
# header comes first and the text last.
_STREAMS = ("pages", "lines", "lboxes", "spans", "sboxes", "dstyles", "dboxes",
            "extras", "lengths")
_PAGE_KEYS = ("pn", "tb", "ir", "dr", "ck", "lk")
_SPAN_STYLE_KEYS = ("sz", "c", "f", "fa")
_DRAW_STYLE_KEYS = ("t", "f", "c", "w")
# Coordinates are stored as integers of 1/scale point. 1 and 10 are what the
# presets produce; 100 covers a manifest written with two decimals.
_SCALES = (1, 10, 100)
# A line's span count is stored in four bits of its flags; above 14, the
# count follows in full.
_INLINE_SPANS = 15
# Ten bytes of seven bits hold any 64-bit value; a longer varint is damage.
_MAX_VARINT_BYTES = 10


class _Unsupported(Exception):
    """The manifest holds something the columnar layout does not describe."""


def _zigzag(value: int) -> int:
    return value * 2 if value >= 0 else -value * 2 - 1


def _unzigzag(value: int) -> int:
    return value >> 1 if not value & 1 else -((value + 1) >> 1)


def _is_number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


class _Varints:
    """Unsigned LEB128 varints, appended one at a time."""

    def __init__(self) -> None:
        self.data = bytearray()

    def u(self, value: int) -> None:
        if value < 0:
            raise _Unsupported("negative count")
        while value >= 0x80:
            self.data.append((value & 0x7F) | 0x80)
            value >>= 7
        self.data.append(value)

    def s(self, value: int) -> None:
        self.u(_zigzag(value))


class _VarintReader:
    """Reads one stream of a columnar body, failing cleanly at its end."""

    def __init__(self, data: bytes, name: str) -> None:
        self.data = data
        self.name = name
        self.pos = 0

    def u(self) -> int:
        value = 0
        shift = 0
        for _ in range(_MAX_VARINT_BYTES):
            if self.pos >= len(self.data):
                raise PsdiError(f"Compact manifest: the {self.name} stream ends early")
            byte = self.data[self.pos]
            self.pos += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value
            shift += 7
        raise PsdiError(f"Compact manifest: oversized number in the {self.name} stream")

    def s(self) -> int:
        return _unzigzag(self.u())

    @property
    def exhausted(self) -> bool:
        return self.pos == len(self.data)


class _ColumnWriter:
    """Encodes one manifest at one coordinate scale."""

    def __init__(self, scale: int) -> None:
        self.scale = scale
        self.out = {name: _Varints() for name in _STREAMS}
        self.text = bytearray()
        self.span_styles: dict[tuple, int] = {}
        self.draw_styles: dict[tuple, int] = {}
        self.string_refs: set[bool] = set()

    def coord(self, value) -> int:
        if not _is_number(value):
            raise _Unsupported("coordinate")
        units = round(value * self.scale)
        if (units / self.scale if self.scale != 1 else units) != value:
            raise _Unsupported("coordinate finer than the scale")
        return units

    def box(self, value) -> list[int]:
        if not isinstance(value, list) or len(value) != 4:
            raise _Unsupported("box")
        return [self.coord(v) for v in value]

    def string(self, value) -> None:
        if not isinstance(value, str):
            raise _Unsupported("string")
        encoded = value.encode("utf-8")
        self.out["lengths"].u(len(encoded))
        self.text += encoded

    @staticmethod
    def _style(entry: dict, keys: tuple, table: dict) -> int:
        # Values must be hashable scalars; a list or a dict raises TypeError,
        # which sends the whole manifest to the JSON body.
        style = tuple((key, entry[key]) for key in keys if key in entry)
        for _, value in style:
            if isinstance(value, (list, dict)):
                raise _Unsupported("style value")
        return table.setdefault(style, len(table))

    def page(self, index: int, page: dict) -> None:
        if not isinstance(page, dict) or set(page) - set(_PAGE_KEYS):
            raise _Unsupported("page keys")
        head = self.out["pages"]
        head.u(sum(1 << bit for bit, key in enumerate(_PAGE_KEYS) if key in page))
        if "pn" in page:
            number = page["pn"]
            if not isinstance(number, int) or isinstance(number, bool):
                raise _Unsupported("page number")
            head.s(number - (index + 1))
        if "tb" in page:
            self.text_blocks(page["tb"])
        if "ir" in page:
            refs = page["ir"]
            head.u(len(refs))
            for ref in refs:
                if not isinstance(ref, dict) or set(ref) != {"x", "b"}:
                    raise _Unsupported("image placement")
                key = ref["x"]
                if isinstance(key, str):
                    if not (key.isascii() and key.isdigit()) or str(int(key)) != key:
                        raise _Unsupported("image reference")
                    self.string_refs.add(True)
                    key = int(key)
                elif isinstance(key, int) and not isinstance(key, bool):
                    self.string_refs.add(False)
                else:
                    raise _Unsupported("image reference")
                self.out["extras"].u(key)
                for value in self.box(ref["b"]):
                    self.out["extras"].s(value)
        if "dr" in page:
            self.drawings(page["dr"])
        if "ck" in page:
            checks = page["ck"]
            head.u(len(checks))
            for check in checks:
                for value in self.box(check):
                    self.out["extras"].s(value)
        if "lk" in page:
            links = page["lk"]
            head.u(len(links))
            for link in links:
                if not isinstance(link, dict) or set(link) != {"b", "u"}:
                    raise _Unsupported("link")
                for value in self.box(link["b"]):
                    self.out["extras"].s(value)
                self.string(link["u"])

    def text_blocks(self, blocks: list) -> None:
        head = self.out["pages"]
        head.u(len(blocks))
        lines_of = []
        for block in blocks:
            if not isinstance(block, dict) or set(block) != {"l"}:
                raise _Unsupported("text block")
            lines_of.append(block["l"])

        # A line that fits at full size keeps its box open to the page corner
        # (see the engine's _open_line_box), so most lines of a page share
        # one bottom-right corner: stored once per page, then one flag bit.
        corners = Counter()
        for lines in lines_of:
            for line in lines:
                if not isinstance(line, dict) or set(line) != {"b", "s"}:
                    raise _Unsupported("line")
                box = self.box(line["b"])
                corners[(box[2], box[3])] += 1
        corner = corners.most_common(1)[0][0] if corners else (0, 0)
        head.s(corner[0])
        head.s(corner[1])

        x_prev = y_prev = 0
        for lines in lines_of:
            head.u(len(lines))
            for line in lines:
                box = self.box(line["b"])
                spans = line["s"]
                if not isinstance(spans, list):
                    raise _Unsupported("spans")
                is_open = (box[2], box[3]) == corner
                has_boxes = any(isinstance(span, dict) and "b" in span for span in spans)
                count = len(spans)
                self.out["lines"].u(min(count, _INLINE_SPANS) | is_open << 4
                                    | has_boxes << 5)
                if count >= _INLINE_SPANS:
                    self.out["lines"].u(count)
                self.out["lboxes"].s(box[0] - x_prev)
                self.out["lboxes"].s(box[1] - y_prev)
                if not is_open:
                    self.out["lboxes"].s(box[2] - box[0])
                    self.out["lboxes"].s(box[3] - box[1])
                x_prev, y_prev = box[0], box[1]
                for span in spans:
                    self.span(span, box, has_boxes)

    def span(self, span, line_box: list[int], has_boxes: bool) -> None:
        if (not isinstance(span, dict) or "t" not in span
                or set(span) - {"t", "b", *_SPAN_STYLE_KEYS}):
            raise _Unsupported("span")
        self.out["spans"].u(self._style(span, _SPAN_STYLE_KEYS, self.span_styles))
        self.string(span["t"])
        if not has_boxes:
            return
        if "b" not in span:
            self.out["sboxes"].u(0)
            return
        box = self.box(span["b"])
        boxes = self.out["sboxes"]
        boxes.u(1)
        boxes.s(box[0] - line_box[0])
        boxes.s(box[1] - line_box[1])
        boxes.s(box[2] - box[0])
        boxes.s(box[3] - box[1])

    def drawings(self, entries: list) -> None:
        self.out["pages"].u(len(entries))
        # Each style's run is sorted by position (the engine's
        # _order_colour_runs), so a box is coded against the previous box of
        # its own style: the same width and height, a little further on.
        previous: dict[int, tuple] = {}
        for entry in entries:
            if (not isinstance(entry, dict) or "r" not in entry
                    or set(entry) - {"r", *_DRAW_STYLE_KEYS}):
                raise _Unsupported("drawing")
            style = self._style(entry, _DRAW_STYLE_KEYS, self.draw_styles)
            self.out["dstyles"].u(style)
            x0, y0, x1, y1 = self.box(entry["r"])
            px, py, pw, ph = previous.get(style, (0, 0, 0, 0))
            boxes = self.out["dboxes"]
            boxes.s(x0 - px)
            boxes.s(y0 - py)
            boxes.s((x1 - x0) - pw)
            boxes.s((y1 - y0) - ph)
            previous[style] = (x0, y0, x1 - x0, y1 - y0)

    def body(self, manifest: dict) -> bytes:
        if not isinstance(manifest, dict) or not isinstance(manifest.get("pages"), list):
            raise _Unsupported("manifest")
        pages = manifest["pages"]
        for index, page in enumerate(pages):
            self.page(index, page)
        if len(self.string_refs) > 1:
            raise _Unsupported("mixed image reference types")
        header = {
            "m": {key: value for key, value in manifest.items() if key != "pages"},
            "n": len(pages),
            "q": self.scale,
            "ss": [dict(style) for style in self.span_styles],
            "ds": [dict(style) for style in self.draw_styles],
            "xs": bool(self.string_refs) and self.string_refs == {True},
        }
        sections = [json.dumps(header, ensure_ascii=False,
                               separators=(",", ":")).encode("utf-8")]
        sections += [bytes(self.out[name].data) for name in _STREAMS]
        sections.append(bytes(self.text))
        return b"".join(struct.pack("<I", len(s)) + s for s in sections)


def _encode_columns(manifest: dict) -> bytes | None:
    """The columnar body of a manifest, or None if the columns cannot hold it.

    Every scale is tried from the coarsest, which is the one the presets'
    whole-point coordinates fit; anything else the layout does not describe
    -- an unknown key, a nested value where a scalar belongs -- returns None,
    and the caller writes JSON instead.
    """
    for scale in _SCALES:
        try:
            return _ColumnWriter(scale).body(manifest)
        except (_Unsupported, AttributeError, KeyError, OverflowError,
                TypeError, ValueError):
            continue
    return None


def _scalar_table(value, keys: tuple, what: str) -> list[dict]:
    """Validate one style table of a columnar header."""
    if not isinstance(value, list):
        raise PsdiError(f"Compact manifest: malformed {what} table")
    for style in value:
        if (not isinstance(style, dict) or set(style) - set(keys)
                or any(isinstance(v, (list, dict)) for v in style.values())):
            raise PsdiError(f"Compact manifest: malformed {what} style")
    return value


def _decode_columns(body: bytes) -> dict:
    """Rebuild the manifest from a columnar body, refusing anything damaged."""
    sections = []
    pos = 0
    for _ in range(len(_STREAMS) + 2):
        if pos + 4 > len(body):
            raise PsdiError("Compact manifest: truncated section table")
        size = struct.unpack_from("<I", body, pos)[0]
        pos += 4
        if pos + size > len(body):
            raise PsdiError("Compact manifest: section runs past the end")
        sections.append(body[pos:pos + size])
        pos += size
    if pos != len(body):
        raise PsdiError("Compact manifest: trailing data after the sections")

    try:
        header = json.loads(sections[0].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PsdiError(f"Compact manifest: header is not valid JSON: {exc}") from exc
    if not isinstance(header, dict) or not isinstance(header.get("m"), dict):
        raise PsdiError("Compact manifest: malformed header")
    rest = header["m"]
    count = header.get("n")
    scale = header.get("q")
    if "pages" in rest:
        raise PsdiError("Compact manifest: pages stored twice")
    if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= MAX_PAGES:
        raise PsdiError(f"Compact manifest: implausible page count {count!r}")
    if scale not in _SCALES or isinstance(scale, bool):
        raise PsdiError(f"Compact manifest: unknown coordinate scale {scale!r}")
    span_styles = _scalar_table(header.get("ss"), _SPAN_STYLE_KEYS, "span")
    draw_styles = _scalar_table(header.get("ds"), _DRAW_STYLE_KEYS, "drawing")
    string_refs = header.get("xs") is True

    streams = {name: _VarintReader(data, name)
               for name, data in zip(_STREAMS, sections[1:-1])}
    text = sections[-1]
    text_pos = 0

    def string() -> str:
        nonlocal text_pos
        size = streams["lengths"].u()
        if text_pos + size > len(text):
            raise PsdiError("Compact manifest: a string runs past the text")
        try:
            value = text[text_pos:text_pos + size].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PsdiError(f"Compact manifest: text is not UTF-8: {exc}") from exc
        text_pos += size
        return value

    def coord(units: int):
        return units / scale if scale != 1 else units

    def style(table: list, index: int, what: str) -> dict:
        if index >= len(table):
            raise PsdiError(f"Compact manifest: {what} style {index} does not exist")
        return dict(table[index])

    head = streams["pages"]
    extras = streams["extras"]
    pages = []
    for index in range(count):
        mask = head.u()
        if mask >> len(_PAGE_KEYS):
            raise PsdiError("Compact manifest: unknown page key")
        present = {key for bit, key in enumerate(_PAGE_KEYS) if mask >> bit & 1}
        page: dict = {}
        if "pn" in present:
            page["pn"] = head.s() + index + 1
        if "tb" in present:
            block_count = head.u()
            corner = (head.s(), head.s())
            x_prev = y_prev = 0
            blocks = []
            for _ in range(block_count):
                line_count = head.u()
                lines = []
                for _ in range(line_count):
                    flags = streams["lines"].u()
                    if flags >> 6:
                        raise PsdiError("Compact manifest: unknown line flags")
                    span_count = flags & 0x0F
                    if span_count == _INLINE_SPANS:
                        span_count = streams["lines"].u()
                    x0 = x_prev + streams["lboxes"].s()
                    y0 = y_prev + streams["lboxes"].s()
                    if flags & 0x10:
                        x1, y1 = corner
                    else:
                        x1 = x0 + streams["lboxes"].s()
                        y1 = y0 + streams["lboxes"].s()
                    x_prev, y_prev = x0, y0
                    spans = []
                    for _ in range(span_count):
                        span = style(span_styles, streams["spans"].u(), "span")
                        span["t"] = string()
                        if flags & 0x20 and streams["sboxes"].u():
                            sx0 = x0 + streams["sboxes"].s()
                            sy0 = y0 + streams["sboxes"].s()
                            sx1 = sx0 + streams["sboxes"].s()
                            sy1 = sy0 + streams["sboxes"].s()
                            span["b"] = [coord(v) for v in (sx0, sy0, sx1, sy1)]
                        spans.append(span)
                    lines.append({"b": [coord(v) for v in (x0, y0, x1, y1)],
                                  "s": spans})
                blocks.append({"l": lines})
            page["tb"] = blocks
        if "ir" in present:
            refs = []
            for _ in range(head.u()):
                key = extras.u()
                box = [coord(extras.s()) for _ in range(4)]
                refs.append({"x": str(key) if string_refs else key, "b": box})
            page["ir"] = refs
        if "dr" in present:
            entries = []
            previous: dict[int, tuple] = {}
            for _ in range(head.u()):
                index_style = streams["dstyles"].u()
                entry = style(draw_styles, index_style, "drawing")
                px, py, pw, ph = previous.get(index_style, (0, 0, 0, 0))
                x0 = px + streams["dboxes"].s()
                y0 = py + streams["dboxes"].s()
                width = pw + streams["dboxes"].s()
                height = ph + streams["dboxes"].s()
                previous[index_style] = (x0, y0, width, height)
                entry["r"] = [coord(v) for v in (x0, y0, x0 + width, y0 + height)]
                entries.append(entry)
            page["dr"] = entries
        if "ck" in present:
            page["ck"] = [[coord(extras.s()) for _ in range(4)]
                          for _ in range(head.u())]
        if "lk" in present:
            links = []
            for _ in range(head.u()):
                box = [coord(extras.s()) for _ in range(4)]
                links.append({"b": box, "u": string()})
            page["lk"] = links
        pages.append(page)

    leftover = [name for name, reader in streams.items() if not reader.exhausted]
    if leftover or text_pos != len(text):
        raise PsdiError("Compact manifest: data left over after the last page "
                        f"({', '.join(leftover) or 'text'})")
    manifest = dict(rest)
    manifest["pages"] = pages
    return manifest


# Compressed column bodies smaller than this are also compared with the JSON.
_JSON_RETRY_BELOW = 4096


def write_compact_struct(manifest: dict, images: dict[str, bytes],
                         lzma_preset: int = 6) -> bytes:
    """Serialise a version 3 archive.

    The columns are decoded again before they are trusted: if the result is
    not equal to the manifest -- a key the layout does not know, a value it
    would round -- the body falls back to the version 1 JSON, so nothing is
    ever lost to the compact form. The JSON is also kept when it is the
    smaller, which only happens on near-empty documents.
    """
    columns = _encode_columns(manifest)
    if columns is not None:
        try:
            faithful = _decode_columns(columns) == manifest
        except PsdiError:
            faithful = False
        if not faithful:
            columns = None
    json_body = bytes([_BODY_JSON]) + _manifest_json(manifest).encode("utf-8")
    if columns is None:
        body, compressed = json_body, _lzma_compress_text(json_body)
    else:
        body = bytes([_BODY_COLUMNS]) + columns
        compressed = _lzma_compress_text(body)
        # On a page holding a dozen words the section table can cost more
        # than the JSON it replaces; keep whichever is smaller. Beyond a few
        # kilobytes the columns win by a fifth or more, and compressing the
        # JSON as well would only cost time -- most of a second on a book.
        if len(compressed) < _JSON_RETRY_BELOW:
            alternative = _lzma_compress_text(json_body)
            if len(alternative) < len(compressed):
                body, compressed = json_body, alternative

    out = io.BytesIO()
    out.write(ARCHIVE_MAGIC)
    out.write(struct.pack("<H", ARCHIVE_VERSION_COMPACT_STRUCT))
    out.write(struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF))
    out.write(struct.pack("<I", len(compressed)))
    out.write(compressed)
    _write_images(out, images, lzma_preset)
    return out.getvalue()


def compact_body_kind(data: bytes) -> str:
    """Report how a version 3 archive stores its manifest: columns or JSON."""
    body = _compact_body(data)[0]
    return "columns" if body[:1] == bytes([_BODY_COLUMNS]) else "json"


def _compact_body(data: bytes) -> tuple[bytes, int, int]:
    """The decompressed body of a version 3 archive, its CRC and the offset after it."""
    pos = 6
    _need(data, pos, 8, "manifest header")
    checksum, body_size = struct.unpack_from("<II", data, pos)
    pos += 8
    if body_size > MAX_MANIFEST_SIZE:
        raise PsdiError(
            f"Manifest claims {body_size} compressed bytes, above the "
            f"{MAX_MANIFEST_SIZE} byte ceiling"
        )
    _need(data, pos, body_size, "manifest")
    body = _lzma_decompress(data[pos:pos + body_size], MAX_MANIFEST_SIZE)
    if not body:
        raise PsdiError("Compact manifest is empty")
    return body, checksum, pos + body_size


def read_compact_struct(data: bytes) -> StructArchive:
    """Parse a version 3 archive, rejecting anything inconsistent."""
    body, checksum, pos = _compact_body(data)
    crc_ok = (zlib.crc32(body) & 0xFFFFFFFF) == checksum
    kind, content = body[0], body[1:]
    if kind == _BODY_COLUMNS:
        manifest = _decode_columns(content)
    elif kind == _BODY_JSON:
        try:
            manifest = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PsdiError(f"Manifest is not valid JSON: {exc}") from exc
    else:
        raise PsdiError(f"Unknown compact manifest encoding: {kind}")
    _check_manifest(manifest)
    return StructArchive(manifest=manifest, images=_read_images(data, pos),
                         crc_ok=crc_ok)


def write_image(pages: list[tuple[float, float, bytes]],
                lzma_preset: int = 6,
                version: int = ARCHIVE_VERSION_IMAGE) -> bytes:
    """Serialise a version 2 archive, or a version 4 one: the same layout."""
    if version not in (ARCHIVE_VERSION_IMAGE, ARCHIVE_VERSION_COMPACT_IMAGE):
        raise ValueError(f"Not a page-image version: {version}")
    out = io.BytesIO()
    out.write(ARCHIVE_MAGIC)
    out.write(struct.pack("<H", version))
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
    """Parse a version 2 or 4 archive, rejecting anything inconsistent."""
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
