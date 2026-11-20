// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// Deflate as Pillow's wheels compress: zlib-ng 2.3.3 (its zlib-compatible
// build there, its own API here, which is the same code under prefixed
// names). The streams decide which PNG candidate is smallest, so a
// different deflate would sometimes choose another one. Inflating needs no
// such care and stays with MuPDF's zlib.
#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>

namespace pdft::img {

// One deflate stream with deflateInit2(level, Z_DEFLATED, 15, 9, strategy),
// as ImagingZipEncode opens it for a PNG.
class Deflater {
public:
    enum class Strategy { Default, Filtered };
    // Receives each output buffer as it fills (`bufsize` bytes), then the rest.
    using Sink = std::function<void(const uint8_t *data, size_t size)>;

    Deflater(int level, Strategy strategy, size_t bufsize, Sink sink);
    ~Deflater();
    Deflater(const Deflater &) = delete;
    Deflater &operator=(const Deflater &) = delete;

    void write(const uint8_t *data, size_t size);
    void finish();

private:
    struct State;
    std::unique_ptr<State> state_;
};

// zlib.compress(data, level)
std::string zlib_compress(const std::string &data, int level);

} // namespace pdft::img
