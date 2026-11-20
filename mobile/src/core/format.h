// Copyright (C) 2026 The PDF Teleporter contributors
// Portions derived from PDF Teleporter by F1GBD (Jean-Louis Naudin),
// ADRASEC 77 - FNRASEC, author of the original program and of the .psdi
// format.
// SPDX-License-Identifier: GPL-3.0-only
//
// The .psdi container format -- a port of the desktop version's
// psditool/format.py, which documents the layout. Versions 1 and 2 are the
// interoperability contract with every other implementation on the air,
// the original included; versions 3 and 4 are the compact .psdz archives.
#pragma once

#include "json.h"

#include <cstdint>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace pdft::psdi {

using Bytes = std::string;

constexpr std::string_view ARCHIVE_MAGIC = "PSDI";
constexpr int ARCHIVE_VERSION_STRUCT = 1;
constexpr int ARCHIVE_VERSION_IMAGE = 2;
constexpr int ARCHIVE_VERSION_COMPACT_STRUCT = 3;
constexpr int ARCHIVE_VERSION_COMPACT_IMAGE = 4;

inline bool is_compact_version(int v) {
    return v == ARCHIVE_VERSION_COMPACT_STRUCT || v == ARCHIVE_VERSION_COMPACT_IMAGE;
}

constexpr std::string_view EXTENSION = ".psdi";
constexpr std::string_view COMPACT_EXTENSION = ".psdz";
std::string extension_for(int version);

constexpr uint8_t FLAG_LZMA = 0x01;

constexpr size_t MAX_ARCHIVE_SIZE = 64u * 1024 * 1024;
constexpr size_t MAX_MANIFEST_SIZE = 32u * 1024 * 1024;
constexpr size_t MAX_PAYLOAD_SIZE = 32u * 1024 * 1024;
constexpr size_t MAX_PAGES = 2000;
constexpr size_t MAX_IMAGES = 5000;

class PsdiError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

struct Packed {
    uint8_t flags = 0;
    Bytes data;
};

// Compress a payload only when compression pays off.
Packed pack_payload(std::string_view raw, int preset);
Bytes unpack_payload(uint8_t flags, std::string_view data);

// "jpeg", "png", "webp", "avif", or "" for anything else.
std::string payload_kind(std::string_view data);

// Image payloads by key ("1234"), in the order of the archive.
using ImageList = std::vector<std::pair<std::string, Bytes>>;

struct StructArchive {
    json::Value manifest;
    ImageList images;
    bool crc_ok = true;
    const Bytes *image(const std::string &key) const;
};

struct ImagePage {
    double width = 0;
    double height = 0;
    Bytes payload;
};

struct ImageArchive {
    std::vector<ImagePage> pages;
};

int peek_version(std::string_view data);

// The report validate() returns in Python.
struct Validation {
    bool valid = false;
    int version = -1;          // -1: none
    bool checksum_ok = false;
    long pages = -1;           // -1: none
    std::string error;
};
Validation validate(std::string_view data);

// Images to write, keyed by their decimal xref; written in numeric order.
using ImageMap = std::map<std::string, Bytes>;

Bytes write_struct(const json::Value &manifest, const ImageMap &images, int lzma_preset = 6);
StructArchive read_struct(std::string_view data);

Bytes write_compact_struct(const json::Value &manifest, const ImageMap &images, int lzma_preset = 6);
StructArchive read_compact_struct(std::string_view data);
// "columns" or "json"
std::string compact_body_kind(std::string_view data);

Bytes write_image(const std::vector<ImagePage> &pages, int lzma_preset = 6,
                  int version = ARCHIVE_VERSION_IMAGE);
ImageArchive read_image(std::string_view data);

// The manifest as JSON, coordinates at a fixed width (see format.py).
std::string manifest_json(const json::Value &manifest);

// Exposed for the tests.
std::optional<Bytes> encode_columns(const json::Value &manifest);
json::Value decode_columns(std::string_view body);

} // namespace pdft::psdi
