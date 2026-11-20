// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

#include "presets.h"

#include "pyutil.h"

#include <charconv>
#include <cstdio>

namespace pdft::engine {

const std::vector<Preset> &presets() {
    static const std::vector<Preset> table = {
        {"ultra_low", 72, 20, 400, 300, 25, 6, 0, "Urgence (Packet 1200 bauds)"},
        {"low", 90, 30, 600, 400, 30, 9, 0, "Qualité réduite (Packet 9600 / VARA HF lent)"},
        {"medium", 120, 45, 800, 600, 40, 9, 1, "Qualité moyenne (VARA HF / FM)"},
        {"high", 150, 55, 900, 700, 50, 9, 1, "Haute qualité (VARA FM rapide)"},
    };
    return table;
}

const Preset *find_preset(const std::string &id) {
    for (const auto &p : presets())
        if (p.id == id)
            return &p;
    return nullptr;
}

const char *quality_label(const std::string &id) {
    if (id == "ultra_low")
        return "Très basse";
    if (id == "low")
        return "Basse";
    if (id == "medium")
        return "Moyenne";
    if (id == "high")
        return "Haute";
    return "";
}

const std::vector<RadioMode> &radio_modes() {
    static const std::vector<RadioMode> table = {
        {"vara_hf", 2400, "VARA HF (2400 bps)"},
        {"vara_hf_turbo", 4800, "VARA HF turbo (4800 bps)"},
        {"vara_fm_narrow", 9600, "VARA FM étroit (9600 bps)"},
        {"vara_fm_wide", 25000, "VARA FM large (25000 bps)"},
        {"packet_9600", 9600, "Packet 9600 bps"},
        {"packet_1200", 1200, "Packet 1200 bps"},
        {"ardop", 2000, "ARDOP (2000 bps)"},
        {"winlink_hf", 3200, "Winlink VARA HF"},
        {"lora", 1200, "LoRa (~1200 bps)"},
    };
    return table;
}

std::vector<std::pair<std::string, double>> estimate_times(long long size_bytes) {
    std::vector<std::pair<std::string, double>> out;
    long long bits = size_bytes * 8;
    for (const auto &mode : radio_modes())
        out.emplace_back(mode.id, py::round_to(static_cast<double>(bits) / (mode.bitrate * 0.7), 1));
    return out;
}

namespace {

// f"{value:.{digits}f}": correctly rounded, as Python formats it.
std::string fixed(double value, int digits) {
    char buf[64];
    auto res = std::to_chars(buf, buf + sizeof buf, value, std::chars_format::fixed, digits);
    return std::string(buf, res.ptr);
}

std::string decimal_comma(std::string text) {
    for (char &c : text)
        if (c == '.')
            c = ',';
    return text;
}

} // namespace

std::string format_bytes(long long size) {
    if (size < 1024)
        return std::to_string(size) + " o";
    if (size < 100 * 1024)
        return decimal_comma(fixed(static_cast<double>(size) / 1024, 1)) + " ko";
    if (size < 1024 * 1024)
        return fixed(static_cast<double>(size) / 1024, 0) + " ko";
    return decimal_comma(fixed(static_cast<double>(size) / (1024 * 1024), 1)) + " Mo";
}

std::string format_percent(double value) { return decimal_comma(fixed(value, 1)) + " %"; }

std::string format_count(long long value) {
    std::string digits = std::to_string(value < 0 ? -value : value);
    std::string out;
    int count = 0;
    for (auto it = digits.rbegin(); it != digits.rend(); ++it) {
        if (count && count % 3 == 0)
            out.insert(0, " ");
        out.insert(out.begin(), *it);
        ++count;
    }
    return value < 0 ? "-" + out : out;
}

std::string format_duration(double seconds) {
    if (seconds < 60)
        return fixed(seconds, 0) + " s";
    long long total = static_cast<long long>(seconds);
    long long minutes = total / 60, secs = total % 60;
    char buf[64];
    if (minutes < 60) {
        std::snprintf(buf, sizeof buf, "%lld min %02lld s", minutes, secs);
        return buf;
    }
    std::snprintf(buf, sizeof buf, "%lld h %02lld min", minutes / 60, minutes % 60);
    return buf;
}

} // namespace pdft::engine
