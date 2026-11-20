// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// Quality presets and transfer estimates (presets.py).
//
// The preset values are fixed by the .psdi interoperability contract: a
// receiving station running another implementation must be able to rebuild
// an archive produced here, so they must not be changed casually.
#pragma once

#include <string>
#include <utility>
#include <vector>

namespace pdft::engine {

struct Preset {
    std::string id;           // "ultra_low", "low", "medium", "high"
    int dpi;
    int jpeg_quality;
    int img_max_dim;
    int map_max_dim;
    int banner_max_h;
    int lzma_preset;
    int coord_precision;
    std::string description;  // French, for the operator
};

// QUALITY_ORDER: ultra_low, low, medium, high.
const std::vector<Preset> &presets();
const Preset *find_preset(const std::string &id);
const char *quality_label(const std::string &id);  // QUALITY_LABELS

struct RadioMode {
    std::string id;
    int bitrate;
    std::string label;
};
const std::vector<RadioMode> &radio_modes();

// estimate_times(size): seconds on the air per radio mode, at 70 % of the
// nominal rate, rounded to a tenth.
std::vector<std::pair<std::string, double>> estimate_times(long long size_bytes);

constexpr long long WINLINK_MAX_ATTACHMENT = 120 * 1024;

// The operator-facing formats of presets.py.
std::string format_bytes(long long size);
std::string format_percent(double value);
std::string format_count(long long value);
std::string format_duration(double seconds);

} // namespace pdft::engine
