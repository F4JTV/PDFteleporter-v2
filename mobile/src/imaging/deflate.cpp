// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

#include "deflate.h"

#include "image.h"

#include <zlib-ng.h>

#include <vector>

namespace pdft::img {

struct Deflater::State {
    zng_stream z{};
    std::vector<uint8_t> buffer;
    Sink sink;
    bool open = false;
};

Deflater::Deflater(int level, Strategy strategy, size_t bufsize, Sink sink) : state_(std::make_unique<State>()) {
    state_->buffer.resize(bufsize);
    state_->sink = std::move(sink);
    int32_t s = strategy == Strategy::Filtered ? Z_FILTERED : Z_DEFAULT_STRATEGY;
    if (zng_deflateInit2(&state_->z, level, Z_DEFLATED, 15, 9, s) != Z_OK)
        throw Error("PNG encoder configuration error");
    state_->open = true;
    state_->z.next_out = state_->buffer.data();
    state_->z.avail_out = static_cast<uint32_t>(bufsize);
}

Deflater::~Deflater() {
    if (state_->open)
        zng_deflateEnd(&state_->z);
}

void Deflater::write(const uint8_t *data, size_t size) {
    zng_stream &z = state_->z;
    z.next_in = data;
    z.avail_in = static_cast<uint32_t>(size);
    while (z.avail_in > 0) {
        if (zng_deflate(&z, Z_NO_FLUSH) < 0)
            throw Error("PNG encoder error");
        if (z.avail_out == 0) {
            state_->sink(state_->buffer.data(), state_->buffer.size());
            z.next_out = state_->buffer.data();
            z.avail_out = static_cast<uint32_t>(state_->buffer.size());
        }
    }
}

void Deflater::finish() {
    zng_stream &z = state_->z;
    for (;;) {
        int32_t err = zng_deflate(&z, Z_FINISH);
        if (err == Z_STREAM_END)
            break;
        if (err < 0)
            throw Error("PNG encoder error");
        if (z.avail_out == 0) {
            state_->sink(state_->buffer.data(), state_->buffer.size());
            z.next_out = state_->buffer.data();
            z.avail_out = static_cast<uint32_t>(state_->buffer.size());
        }
    }
    state_->sink(state_->buffer.data(), state_->buffer.size() - z.avail_out);
    zng_deflateEnd(&z);
    state_->open = false;
}

std::string zlib_compress(const std::string &data, int level) {
    size_t size = zng_compressBound(data.size());
    std::string packed(size, '\0');
    if (zng_compress2(reinterpret_cast<uint8_t *>(packed.data()), &size,
                      reinterpret_cast<const uint8_t *>(data.data()), data.size(), level) != Z_OK)
        throw Error("deflate failed");
    packed.resize(size);
    return packed;
}

} // namespace pdft::img
