// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// Raw LZMA2 streams, as Python's lzma module makes and reads them.
#pragma once

#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <string_view>

namespace pdft::lzma {

// liblzma's LZMA_PRESET_EXTREME, to be or'ed into a preset.
constexpr uint32_t PRESET_EXTREME = 0x80000000u;

class Error : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

// The decompressed data would pass the caller's ceiling.
class LimitError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

// lzma.compress(data, format=FORMAT_RAW, filters=[{"id": FILTER_LZMA2,
// "preset": preset, "dict_size": dict_size[, "pb": pb]}]); pb < 0 keeps the
// preset's own.
std::string compress_raw(std::string_view data, uint32_t preset, uint32_t dict_size, int pb = -1);

// LZMADecompressor(FORMAT_RAW, filters=[{"id": FILTER_LZMA2}]) fed the whole
// stream and drained a megabyte at a time, refusing to go past `limit`.
// A stream cut short yields what it holds, as Python's does.
std::string decompress_raw(std::string_view data, size_t limit);

} // namespace pdft::lzma
