// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
#include "lzma_codec.h"

#include <lzma.h>

#include <vector>

namespace pdft::lzma {

namespace {

const char *describe(lzma_ret ret) {
    switch (ret) {
    case LZMA_MEM_ERROR: return "Memory error";
    case LZMA_MEMLIMIT_ERROR: return "Memory usage limit was reached";
    case LZMA_FORMAT_ERROR: return "Input format not supported by decoder";
    case LZMA_OPTIONS_ERROR: return "Invalid or unsupported options";
    case LZMA_DATA_ERROR: return "Corrupt input data";
    case LZMA_BUF_ERROR: return "Insufficient buffer space";
    case LZMA_PROG_ERROR: return "Internal error";
    default: return "Unrecognized error from liblzma";
    }
}

struct Stream {
    lzma_stream s = LZMA_STREAM_INIT;
    ~Stream() { lzma_end(&s); }
};

} // namespace

std::string compress_raw(std::string_view data, uint32_t preset, uint32_t dict_size, int pb) {
    lzma_options_lzma options;
    if (lzma_lzma_preset(&options, preset))
        throw Error("Invalid compression preset");
    options.dict_size = dict_size;
    if (pb >= 0)
        options.pb = static_cast<uint32_t>(pb);
    lzma_filter filters[2];
    filters[0].id = LZMA_FILTER_LZMA2;
    filters[0].options = &options;
    filters[1].id = LZMA_VLI_UNKNOWN;
    filters[1].options = nullptr;

    Stream stream;
    lzma_ret ret = lzma_raw_encoder(&stream.s, filters);
    if (ret != LZMA_OK)
        throw Error(describe(ret));

    std::string out;
    std::vector<uint8_t> chunk(64 * 1024);
    stream.s.next_in = reinterpret_cast<const uint8_t *>(data.data());
    stream.s.avail_in = data.size();
    while (true) {
        stream.s.next_out = chunk.data();
        stream.s.avail_out = chunk.size();
        ret = lzma_code(&stream.s, LZMA_FINISH);
        out.append(reinterpret_cast<const char *>(chunk.data()), chunk.size() - stream.s.avail_out);
        if (ret == LZMA_STREAM_END)
            break;
        if (ret != LZMA_OK)
            throw Error(describe(ret));
    }
    return out;
}

std::string decompress_raw(std::string_view data, size_t limit) {
    lzma_options_lzma options;
    lzma_lzma_preset(&options, LZMA_PRESET_DEFAULT);
    lzma_filter filters[2];
    filters[0].id = LZMA_FILTER_LZMA2;
    filters[0].options = &options;
    filters[1].id = LZMA_VLI_UNKNOWN;
    filters[1].options = nullptr;

    Stream stream;
    lzma_ret ret = lzma_raw_decoder(&stream.s, filters);
    if (ret != LZMA_OK)
        throw Error(describe(ret));

    constexpr size_t CHUNK = 1024 * 1024;
    std::string out;
    std::vector<uint8_t> chunk(CHUNK);
    stream.s.next_in = reinterpret_cast<const uint8_t *>(data.data());
    stream.s.avail_in = data.size();
    while (true) {
        stream.s.next_out = chunk.data();
        stream.s.avail_out = chunk.size();
        ret = lzma_code(&stream.s, LZMA_RUN);
        size_t produced = chunk.size() - stream.s.avail_out;
        if (ret != LZMA_OK && ret != LZMA_STREAM_END && ret != LZMA_BUF_ERROR)
            throw Error(describe(ret));
        if (out.size() + produced > limit)
            throw LimitError("Decompressed data exceeds " + std::to_string(limit) +
                             " bytes; refusing to continue");
        out.append(reinterpret_cast<const char *>(chunk.data()), produced);
        if (ret == LZMA_STREAM_END)
            break;
        // Input used up and the output not full: the decoder waits for
        // more, which there is not -- Python's needs_input.
        if (stream.s.avail_in == 0 && stream.s.avail_out > 0)
            break;
        if (ret == LZMA_BUF_ERROR)
            break;
    }
    return out;
}

} // namespace pdft::lzma
