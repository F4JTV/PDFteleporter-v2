// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// PDF <-> .psdi conversion: the desktop engine (engine.py), in C++.
//
// Two encodings share one container. *Structured* keeps text as text: each
// span carries its box, size, colour and a coarse font class; images are
// extracted once and placed by reference. *Image* renders every page to a
// single picture, the only thing that works for a scan. A compact archive
// (.psdz) is the same with AVIF and WebP pictures and a columnar manifest.
#pragma once

#include <atomic>
#include <functional>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace pdft::engine {

constexpr const char *MODE_STRUCT = "struct";
constexpr const char *MODE_IMAGE = "image";

using ProgressFn = std::function<void(int step, int total, const std::string &message)>;

struct CompressOptions {
    std::string quality = "medium";
    std::string mode = MODE_STRUCT;
    bool skip_images = false;
    bool compact = false;
    // The document's file name, as the manifest records it ("src"); the
    // base name of the path when empty.
    std::string source_name;
};

struct CompressReport {
    std::string archive;
    std::string source;
    long long original_size = 0;
    long long archive_size = 0;
    double ratio_percent = 0;
    double compression_time = 0;
    std::string quality;
    std::string mode;
    std::optional<std::string> auto_mode_switch;   // "rotation", "scan", "image"
    std::vector<std::pair<std::string, double>> estimates;
    bool compact = false;
    std::string extension;   // ".psdi" or ".psdz"
};

// Raised by the conversions when `cancel` turns true.
class Cancelled : public std::runtime_error {
public:
    Cancelled() : std::runtime_error("cancelled") {}
};

// pdf_to_archive(): a PDF, or an image file (see is_image_file), compressed.
CompressReport pdf_to_archive(const std::string &path, const CompressOptions &options, const ProgressFn &progress,
                              const std::atomic<bool> *cancel = nullptr);

// detect_optimal_mode(): ("struct", none), or ("image", "rotation" | "scan").
std::pair<std::string, std::optional<std::string>> detect_optimal_mode(const std::string &pdf_path);

bool is_image_file(const std::string &path);

struct RebuildReport {
    std::string pdf;
    int version = 0;
    long pages = 0;
    bool compact = false;
    std::string mode;          // "struct" or "image"
    bool crc_ok = true;        // the manifest's checksum held
    std::string source;        // the document's name as the sender gave it
    double rebuild_time = 0;
};

// archive_to_pdf(): an archive of any version (its bytes), rebuilt. Raises
// psdi::PsdiError for anything that is not a sound archive.
RebuildReport archive_to_pdf(const std::string &archive, const ProgressFn &progress,
                             const std::atomic<bool> *cancel = nullptr);

} // namespace pdft::engine
