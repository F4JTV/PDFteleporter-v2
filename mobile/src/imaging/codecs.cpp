// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

#include "codecs.h"

#include "deflate.h"

#include "jpeg_codec.h"
#include "mupdf_facade.h"
#include "libimaging.h"

#include <avif/avif.h>
#include <webp/decode.h>
#include <webp/demux.h>
#include <webp/encode.h>
#include <webp/mux.h>
#include <zlib.h>

extern "C" {
#include <mupdf/fitz.h>
}

#include <algorithm>
#include <cctype>
#include <cstring>
#include <memory>
#include <thread>


namespace pdft::img {

namespace {

// Image._decompression_bomb_check: an error past twice MAX_IMAGE_PIXELS.
constexpr long long kMaxImagePixels = 89478485;

bool bomb(long long w, long long h) { return std::max(1LL, w) * std::max(1LL, h) > 2 * kMaxImagePixels; }

uint32_t be16(const std::string &d, size_t at) {
    return (static_cast<uint32_t>(static_cast<uint8_t>(d[at])) << 8) | static_cast<uint8_t>(d[at + 1]);
}

uint32_t be32(const std::string &d, size_t at) { return (be16(d, at) << 16) | be16(d, at + 2); }

bool starts(const std::string &d, const char *prefix, size_t n, size_t at = 0) {
    return d.size() >= at + n && std::memcmp(d.data() + at, prefix, n) == 0;
}

Imaging new_image(ModeID mode, int w, int h) {
    Imaging im = ImagingNewDirty(mode, w, h);
    if (!im)
        throw Error(pdft_imaging_last_error());
    return im;
}

struct ImagingOwner {
    Imaging im = nullptr;
    ~ImagingOwner() {
        if (im)
            ImagingDelete(im);
    }
    Imaging release() {
        Imaging out = im;
        im = nullptr;
        return out;
    }
};

int default_max_threads() {
    unsigned n = std::thread::hardware_concurrency();
    return n ? static_cast<int>(n) : 1;
}

// --- JPEG ----------------------------------------------------------------------------

// What JpegImageFile._open finds before the first scan.
struct JpegHeader {
    Mode mode = Mode::L;
    int width = 0, height = 0;
    Info info;
};

// JpegImagePlugin's marker loop, failing where it raises.
std::optional<JpegHeader> parse_jpeg(const std::string &d) {
    JpegHeader h;
    bool have_mode = false, have_exif = false;
    std::vector<std::string> icclist;
    size_t pos = 3;  // past FF D8 FF; the loop starts on that last FF
    int s0 = 0xFF;
    auto read_byte = [&](int &out) {
        if (pos >= d.size())
            return false;
        out = static_cast<uint8_t>(d[pos++]);
        return true;
    };
    // n = i16(fp.read(2)) - 2; s = _safe_read(fp, n)
    auto read_segment = [&](std::string &s) {
        if (pos + 2 > d.size())
            return false;
        long n = static_cast<long>(be16(d, pos)) - 2;
        pos += 2;
        if (n <= 0) {
            s.clear();
            return true;
        }
        if (pos + static_cast<size_t>(n) > d.size())
            return false;
        s = d.substr(pos, static_cast<size_t>(n));
        pos += static_cast<size_t>(n);
        return true;
    };
    for (;;) {
        int i;
        if (s0 == 0xFF) {
            int next;
            if (!read_byte(next))
                return std::nullopt;  // IndexError on an empty read
            i = 0xFF00 | next;
        } else {
            if (!read_byte(s0))
                return std::nullopt;
            continue;
        }
        bool sof = (i >= 0xFFC0 && i <= 0xFFC3) || (i >= 0xFFC5 && i <= 0xFFC7) || (i >= 0xFFC9 && i <= 0xFFCB) ||
                   (i >= 0xFFCD && i <= 0xFFCF) || i == 0xFFDE;
        bool skip = i == 0xFFC4 || i == 0xFFCC || i == 0xFFDA || i == 0xFFDC || i == 0xFFDD || i == 0xFFDF;
        bool none = i == 0xFFC8 || (i >= 0xFFD0 && i <= 0xFFD9) || (i >= 0xFFF0 && i <= 0xFFFD);
        bool app = i >= 0xFFE0 && i <= 0xFFEF;
        if (sof || skip || none || app || i == 0xFFDB || i == 0xFFFE) {
            std::string s;
            if (!none && !read_segment(s))
                return std::nullopt;
            if (sof) {
                if (s.size() < 6)
                    return std::nullopt;
                h.height = static_cast<int>(be16(s, 1));
                h.width = static_cast<int>(be16(s, 3));
                if (static_cast<uint8_t>(s[0]) != 8)
                    return std::nullopt;  // "cannot handle N-bit layers"
                switch (static_cast<uint8_t>(s[5])) {
                case 1: h.mode = Mode::L; break;
                case 3: h.mode = Mode::RGB; break;
                case 4: h.mode = Mode::CMYK; break;
                default: return std::nullopt;
                }
                have_mode = true;
                if (!icclist.empty()) {
                    std::sort(icclist.begin(), icclist.end());
                    if (icclist[0].size() > 13 && static_cast<uint8_t>(icclist[0][13]) == icclist.size()) {
                        std::string profile;
                        for (const auto &p : icclist)
                            profile += p.size() > 14 ? p.substr(14) : std::string();
                        h.info.icc_profile = profile;
                    } else {
                        h.info.icc_profile = std::nullopt;  // wrong number of fragments
                    }
                    icclist.clear();
                }
            } else if (i == 0xFFDB) {
                // DQT: every table must be complete
                size_t at = 0;
                while (at < s.size()) {
                    int precision = (static_cast<uint8_t>(s[at]) / 16 == 0) ? 1 : 2;
                    size_t length = 1 + static_cast<size_t>(precision) * 64;
                    if (s.size() - at < length)
                        return std::nullopt;
                    at += length;
                }
            } else if (app) {
                if (i == 0xFFE0 && starts(s, "JFIF", 4)) {
                    if (s.size() < 7)
                        return std::nullopt;
                } else if (i == 0xFFE1 && starts(s, "Exif\0\0", 6)) {
                    if (!have_exif) {
                        h.info.exif = s;
                        have_exif = true;
                    }
                } else if (i == 0xFFE1 && starts(s, "http://ns.adobe.com/xap/1.0/\0", 29)) {
                    h.info.xmp = s.substr(29);
                } else if (i == 0xFFE2 && starts(s, "ICC_PROFILE\0", 12)) {
                    icclist.push_back(s);
                } else if (i == 0xFFED && starts(s, "Photoshop 3.0\0", 14)) {
                    size_t offset = 14;
                    while (s.compare(offset, 4, "8BIM") == 0 && offset + 4 <= s.size()) {
                        offset += 4;
                        if (offset + 2 > s.size())
                            break;  // struct.error
                        offset += 2;
                        if (offset >= s.size())
                            return std::nullopt;  // IndexError
                        size_t name_len = static_cast<uint8_t>(s[offset]);
                        offset += 1 + name_len;
                        offset += offset & 1;
                        if (offset + 4 > s.size())
                            break;  // struct.error
                        size_t size = be32(s, offset);
                        offset += 4 + size;
                        offset += offset & 1;
                        if (offset > s.size())
                            break;
                    }
                } else if (i == 0xFFEE && starts(s, "Adobe", 5)) {
                    if (s.size() < 7)
                        return std::nullopt;
                }
            } else if (i == 0xFFFE) {
                h.info.comment = s;
            }
            if (i == 0xFFDA) {
                if (!have_mode || h.width <= 0 || h.height <= 0)
                    return std::nullopt;
                return h;
            }
            if (!read_byte(s0))
                return std::nullopt;
        } else if (i == 0xFFFF) {
            s0 = 0xFF;
        } else if (i == 0xFF00) {
            if (!read_byte(s0))
                return std::nullopt;
        } else {
            return std::nullopt;  // "no marker found"
        }
    }
}

class JpegSource : public Source {
public:
    JpegSource(std::string data, const JpegHeader &h)
        : data_(std::move(data)), mode_(h.mode), width_(h.width), height_(h.height) {}
    Mode mode() const override { return mode_; }
    int width() const override { return width_; }
    int height() const override { return height_; }

    // JpegImageFile.draft(None, size)
    std::optional<std::array<double, 4>> draft(int w, int h) override {
        if (drafted_)
            return std::nullopt;
        int original_w = width_, original_h = height_;
        int scale = 1;
        if (w > 0 && h > 0) {
            int ratio = std::min(width_ / w, height_ / h);
            int s = 1;
            for (int candidate : {8, 4, 2, 1}) {
                s = candidate;
                if (ratio >= candidate)
                    break;
            }
            width_ = (width_ + s - 1) / s;
            height_ = (height_ + s - 1) / s;
            scale = s;
        }
        scale_ = scale;
        drafted_ = true;
        return std::array<double, 4>{0, 0, static_cast<double>(original_w) / scale,
                                     static_cast<double>(original_h) / scale};
    }

    ImagingMemoryInstance *load() override {
        char err[200] = "";
        pdft_jpeg_image out;
        if (pdft_jpeg_decode(reinterpret_cast<const unsigned char *>(data_.data()), data_.size(), scale_, &out,
                             err, sizeof err))
            throw Error(std::string("broken JPEG data: ") + err);
        std::unique_ptr<unsigned char, decltype(&free)> pixels(out.pixels, &free);
        int expected = mode_ == Mode::L ? 1 : mode_ == Mode::RGB ? 3 : 4;
        if (out.components != expected || out.width != width_ || out.height != height_)
            throw Error("JPEG decoder disagrees with the header");
        ImagingOwner owner;
        owner.im = new_image(mode_ == Mode::L ? IMAGING_MODE_L : mode_ == Mode::RGB ? IMAGING_MODE_RGB
                                                                                   : IMAGING_MODE_CMYK,
                             width_, height_);
        size_t stride = static_cast<size_t>(width_) * out.components;
        for (int y = 0; y < height_; ++y) {
            const unsigned char *in = out.pixels + static_cast<size_t>(y) * stride;
            auto *o = reinterpret_cast<uint8_t *>(owner.im->image[y]);
            if (mode_ == Mode::L) {
                std::memcpy(o, in, stride);
            } else if (mode_ == Mode::RGB) {
                for (int x = 0; x < width_; ++x) {
                    o[4 * x] = in[3 * x];
                    o[4 * x + 1] = in[3 * x + 1];
                    o[4 * x + 2] = in[3 * x + 2];
                    o[4 * x + 3] = 255;
                }
            } else {
                // rawmode "CMYK;I": Adobe's inverted CMYK
                for (size_t k = 0; k < stride; ++k)
                    o[k] = static_cast<uint8_t>(255 - in[k]);
            }
        }
        return owner.release();
    }

private:
    std::string data_;
    Mode mode_;
    int width_, height_;
    int scale_ = 1;
    bool drafted_ = false;
};

std::optional<Image> open_jpeg(const std::string &data) {
    auto header = parse_jpeg(data);
    if (!header || bomb(header->width, header->height))
        return std::nullopt;
    Info info = header->info;
    return Image::from_source(std::make_unique<JpegSource>(data, *header), std::move(info));
}

// --- PNG -----------------------------------------------------------------------------

struct PngMode {
    Mode mode;
    const char *rawmode;
};

std::optional<PngMode> png_mode(int depth, int colour) {
    switch (colour) {
    case 0:
        switch (depth) {
        case 1: return PngMode{Mode::One, "1"};
        case 2: return PngMode{Mode::L, "L;2"};
        case 4: return PngMode{Mode::L, "L;4"};
        case 8: return PngMode{Mode::L, "L"};
        case 16: return PngMode{Mode::I16, "I;16B"};
        }
        break;
    case 2:
        if (depth == 8)
            return PngMode{Mode::RGB, "RGB"};
        if (depth == 16)
            return PngMode{Mode::RGB, "RGB;16B"};
        break;
    case 3:
        switch (depth) {
        case 1: return PngMode{Mode::P, "P;1"};
        case 2: return PngMode{Mode::P, "P;2"};
        case 4: return PngMode{Mode::P, "P;4"};
        case 8: return PngMode{Mode::P, "P"};
        }
        break;
    case 4:
        if (depth == 8)
            return PngMode{Mode::LA, "LA"};
        if (depth == 16)
            return PngMode{Mode::RGBA, "LA;16B"};
        break;
    case 6:
        if (depth == 8)
            return PngMode{Mode::RGBA, "RGBA"};
        if (depth == 16)
            return PngMode{Mode::RGBA, "RGBA;16B"};
        break;
    }
    return std::nullopt;
}

int png_channels(int colour) {
    switch (colour) {
    case 2: return 3;
    case 4: return 2;
    case 6: return 4;
    default: return 1;
    }
}

// One pixel of the raw row, unpacked into the image's storage.
void png_unpack(const std::string &rawmode, const uint8_t *in, int x, uint8_t *out) {
    if (rawmode == "1") {
        out[0] = (in[x >> 3] & (0x80 >> (x & 7))) ? 255 : 0;
    } else if (rawmode == "L;2") {
        out[0] = static_cast<uint8_t>(((in[x >> 2] >> (6 - 2 * (x & 3))) & 3) * 0x55);
    } else if (rawmode == "L;4") {
        out[0] = static_cast<uint8_t>(((in[x >> 1] >> (4 - 4 * (x & 1))) & 15) * 0x11);
    } else if (rawmode == "L" || rawmode == "P") {
        out[0] = in[x];
    } else if (rawmode == "I;16B") {
        out[0] = in[2 * x + 1];
        out[1] = in[2 * x];
    } else if (rawmode == "RGB") {
        out[0] = in[3 * x];
        out[1] = in[3 * x + 1];
        out[2] = in[3 * x + 2];
        out[3] = 255;
    } else if (rawmode == "RGB;16B") {
        out[0] = in[6 * x];
        out[1] = in[6 * x + 2];
        out[2] = in[6 * x + 4];
        out[3] = 255;
    } else if (rawmode == "P;1") {
        out[0] = (in[x >> 3] >> (7 - (x & 7))) & 1;
    } else if (rawmode == "P;2") {
        out[0] = (in[x >> 2] >> (6 - 2 * (x & 3))) & 3;
    } else if (rawmode == "P;4") {
        out[0] = (in[x >> 1] >> (4 - 4 * (x & 1))) & 15;
    } else if (rawmode == "LA") {
        out[0] = out[1] = out[2] = in[2 * x];
        out[3] = in[2 * x + 1];
    } else if (rawmode == "LA;16B") {
        out[0] = out[1] = out[2] = in[4 * x];
        out[3] = in[4 * x + 2];
    } else if (rawmode == "RGBA") {
        std::memcpy(out, in + 4 * x, 4);
    } else if (rawmode == "RGBA;16B") {
        out[0] = in[8 * x];
        out[1] = in[8 * x + 2];
        out[2] = in[8 * x + 4];
        out[3] = in[8 * x + 6];
    }
}

// _safe_zlib_decompress: inflate, refusing more than MAX_TEXT_CHUNK (1 MiB).
std::optional<std::string> safe_inflate(const std::string &in) {
    z_stream z{};
    if (inflateInit(&z) != Z_OK)
        return std::nullopt;
    std::string out;
    char buffer[16384];
    z.next_in = reinterpret_cast<Bytef *>(const_cast<char *>(in.data()));
    z.avail_in = static_cast<uInt>(in.size());
    int ret;
    do {
        z.next_out = reinterpret_cast<Bytef *>(buffer);
        z.avail_out = sizeof buffer;
        ret = inflate(&z, Z_NO_FLUSH);
        if (ret != Z_OK && ret != Z_STREAM_END && ret != Z_BUF_ERROR) {
            inflateEnd(&z);
            return std::nullopt;
        }
        out.append(buffer, sizeof buffer - z.avail_out);
        if (out.size() > 1024 * 1024) {
            inflateEnd(&z);
            return std::nullopt;
        }
        if (ret == Z_BUF_ERROR && z.avail_in == 0)
            break;
    } while (ret != Z_STREAM_END);
    inflateEnd(&z);
    return out;
}

bool is_cid(const std::string &d, size_t at) {
    for (size_t k = 0; k < 4; ++k) {
        unsigned char c = static_cast<unsigned char>(d[at + k]);
        if (!(std::isalnum(c) || c == '_'))
            return false;
    }
    return true;
}

std::optional<Image> open_png(const std::string &d) {
    size_t pos = 8;
    int width = 0, height = 0, depth = 0, colour = 0, interlace = 0;
    std::optional<PngMode> mode;
    bool have_ihdr = false, in_idat = false, idat_closed = false;
    std::string palette, idat;
    Info info;
    while (pos + 8 <= d.size()) {
        uint32_t length = be32(d, pos);
        if (!is_cid(d, pos + 4))
            return std::nullopt;  // "broken PNG file"
        std::string cid = d.substr(pos + 4, 4);
        size_t at = pos + 8;
        bool before_idat = idat.empty() && !in_idat;
        if (length > d.size() - at) {
            if (cid == "IDAT" && !idat_closed) {
                idat.append(d, at, d.size() - at);  // truncated: decoding will fail short
                break;
            }
            if (before_idat)
                return std::nullopt;  // "Truncated File Read"
            break;
        }
        std::string s = d.substr(at, length);
        if (cid == "IDAT") {
            if (!have_ihdr || !mode)
                return std::nullopt;
            if (!idat_closed) {
                in_idat = true;
                idat += s;
            }
        } else {
            if (in_idat) {
                in_idat = false;
                idat_closed = true;
            }
            if (before_idat) {
                // PngStream.crc: checked up to the first IDAT
                uLong crc = crc32(0L, reinterpret_cast<const Bytef *>(d.data() + pos + 4), 4);
                crc = crc32(crc, reinterpret_cast<const Bytef *>(s.data()), static_cast<uInt>(s.size()));
                if (at + length + 4 > d.size() || crc != be32(d, at + length))
                    return std::nullopt;
            }
            if (cid == "IHDR") {
                if (length < 13)
                    return std::nullopt;
                width = static_cast<int>(be32(s, 0));
                height = static_cast<int>(be32(s, 4));
                depth = static_cast<uint8_t>(s[8]);
                colour = static_cast<uint8_t>(s[9]);
                mode = png_mode(depth, colour);
                interlace = static_cast<uint8_t>(s[12]);
                if (s[11])
                    return std::nullopt;  // "unknown filter category"
                have_ihdr = true;
            } else if (cid == "PLTE") {
                if (mode && mode->mode == Mode::P)
                    palette = s;
            } else if (cid == "tRNS" && mode) {
                if (mode->mode == Mode::P) {
                    // _simple_palette: one fully transparent entry, the rest opaque
                    std::string body = s;
                    if (!body.empty() && body.back() == '\n')
                        body.pop_back();
                    size_t zero = body.find('\0');
                    bool simple = zero != std::string::npos &&
                                  body.find_first_not_of('\xff') == zero &&
                                  body.find_first_not_of('\xff', zero + 1) == std::string::npos;
                    if (simple) {
                        info.transparency.kind = Transparency::Int;
                        info.transparency.value = static_cast<int>(s.find('\0'));
                    } else {
                        info.transparency.kind = Transparency::Bytes;
                        info.transparency.bytes = s;
                    }
                } else if (mode->mode == Mode::One || mode->mode == Mode::L || mode->mode == Mode::I16) {
                    if (s.size() < 2)
                        return std::nullopt;
                    info.transparency.kind = Transparency::Int;
                    info.transparency.value = static_cast<int>(be16(s, 0));
                } else if (mode->mode == Mode::RGB) {
                    if (s.size() < 6)
                        return std::nullopt;
                    info.transparency.kind = Transparency::Triple;
                    info.transparency.rgb = {static_cast<int>(be16(s, 0)), static_cast<int>(be16(s, 2)),
                                             static_cast<int>(be16(s, 4))};
                }
            } else if (cid == "iCCP") {
                size_t zero = s.find('\0');
                if (zero == std::string::npos || zero + 1 >= s.size()) {
                    if (before_idat)
                        return std::nullopt;
                } else if (s[zero + 1] != 0) {
                    if (before_idat)
                        return std::nullopt;  // "Unknown compression method"
                } else {
                    info.icc_profile = safe_inflate(s.substr(zero + 2));
                }
            } else if (cid == "eXIf") {
                info.exif = std::string("Exif\0\0", 6) + s;
            } else if (cid == "iTXt") {
                size_t zero = s.find('\0');
                if (zero != std::string::npos && s.size() - zero - 1 >= 2) {
                    std::string key = s.substr(0, zero);
                    unsigned cf = static_cast<uint8_t>(s[zero + 1]), cm = static_cast<uint8_t>(s[zero + 2]);
                    std::string rest = s.substr(zero + 3);
                    size_t a = rest.find('\0');
                    size_t b = a == std::string::npos ? a : rest.find('\0', a + 1);
                    if (b != std::string::npos && key == "XML:com.adobe.xmp") {
                        std::string value = rest.substr(b + 1);
                        if (cf != 0) {
                            if (cm == 0) {
                                auto inflated = safe_inflate(value);
                                if (inflated)
                                    info.xmp = *inflated;
                            }
                        } else {
                            info.xmp = value;
                        }
                    }
                }
            } else if (cid == "pHYs" && before_idat) {
                if (s.size() < 9)
                    return std::nullopt;
            } else if (cid == "gAMA" && before_idat) {
                if (s.size() < 4)
                    return std::nullopt;
            } else if (cid == "cHRM" && before_idat) {
                if (s.size() < 32)
                    return std::nullopt;
            } else if (cid == "sRGB" && before_idat) {
                if (s.empty())
                    return std::nullopt;
            } else if (cid == "IEND") {
                break;
            }
        }
        pos = at + length + 4;
    }
    if (!have_ihdr || !mode || width <= 0 || height <= 0 || idat.empty() || bomb(width, height))
        return std::nullopt;

    // Inflate as much as the image needs, then unfilter and unpack.
    int bits = depth * png_channels(colour);
    int bpp = (bits + 7) / 8;
    struct Pass {
        int x0, y0, dx, dy;
    };
    static const Pass kAdam7[] = {{0, 0, 8, 8}, {4, 0, 8, 8}, {0, 4, 4, 8}, {2, 0, 4, 4},
                                  {0, 2, 2, 4}, {1, 0, 2, 2}, {0, 1, 1, 2}};
    static const Pass kWhole[] = {{0, 0, 1, 1}};
    const std::vector<Pass> passes = interlace ? std::vector<Pass>(std::begin(kAdam7), std::end(kAdam7))
                                               : std::vector<Pass>(std::begin(kWhole), std::end(kWhole));
    size_t total = 0;
    for (const auto &p : passes) {
        size_t pw = width > p.x0 ? static_cast<size_t>((width - p.x0 + p.dx - 1) / p.dx) : 0;
        size_t ph = height > p.y0 ? static_cast<size_t>((height - p.y0 + p.dy - 1) / p.dy) : 0;
        if (pw && ph)
            total += ph * (1 + (pw * static_cast<size_t>(bits) + 7) / 8);
    }
    std::string raw(total, '\0');
    {
        z_stream z{};
        if (inflateInit(&z) != Z_OK)
            return std::nullopt;
        z.next_in = reinterpret_cast<Bytef *>(idat.data());
        z.avail_in = static_cast<uInt>(idat.size());
        z.next_out = reinterpret_cast<Bytef *>(raw.data());
        z.avail_out = static_cast<uInt>(raw.size());
        int ret = Z_OK;
        while (z.avail_out > 0 && ret == Z_OK)
            ret = inflate(&z, Z_NO_FLUSH);
        inflateEnd(&z);
        if (z.avail_out > 0)
            return std::nullopt;  // "image file is truncated" or broken data
    }
    Image out = Image::create(mode->mode, width, height);
    Imaging im = out.im();
    if (mode->mode == Mode::P) {
        size_t entries = palette.size() / 3;
        if (entries > 256)
            return std::nullopt;
        im->palette->size = static_cast<int>(entries);
        for (size_t i = 0; i < entries; ++i) {
            im->palette->palette[i * 4] = static_cast<uint8_t>(palette[3 * i]);
            im->palette->palette[i * 4 + 1] = static_cast<uint8_t>(palette[3 * i + 1]);
            im->palette->palette[i * 4 + 2] = static_cast<uint8_t>(palette[3 * i + 2]);
            im->palette->palette[i * 4 + 3] = 255;
        }
    }
    std::string rawmode = mode->rawmode;
    const uint8_t *in = reinterpret_cast<const uint8_t *>(raw.data());
    int pixelsize = im->pixelsize;
    for (const auto &p : passes) {
        int pw = width > p.x0 ? (width - p.x0 + p.dx - 1) / p.dx : 0;
        int ph = height > p.y0 ? (height - p.y0 + p.dy - 1) / p.dy : 0;
        if (!pw || !ph)
            continue;
        size_t row = (static_cast<size_t>(pw) * static_cast<size_t>(bits) + 7) / 8;
        std::vector<uint8_t> previous(row, 0), current(row);
        for (int r = 0; r < ph; ++r) {
            int filter = *in++;
            std::memcpy(current.data(), in, row);
            in += row;
            for (size_t k = 0; k < row; ++k) {
                int a = k >= static_cast<size_t>(bpp) ? current[k - static_cast<size_t>(bpp)] : 0;
                int b = previous[k];
                int c = k >= static_cast<size_t>(bpp) ? previous[k - static_cast<size_t>(bpp)] : 0;
                int v = current[k];
                switch (filter) {
                case 0: break;
                case 1: v += a; break;
                case 2: v += b; break;
                case 3: v += (a + b) / 2; break;
                case 4: {
                    int pa = std::abs(b - c), pb = std::abs(a - c), pc = std::abs(a + b - 2 * c);
                    v += (pa <= pb && pa <= pc) ? a : (pb <= pc) ? b : c;
                    break;
                }
                default: return std::nullopt;  // unknown filter: broken data stream
                }
                current[k] = static_cast<uint8_t>(v);
            }
            int y = p.y0 + r * p.dy;
            auto *line = reinterpret_cast<uint8_t *>(im->image[y]);
            for (int x = 0; x < pw; ++x)
                png_unpack(rawmode, current.data(), x, line + static_cast<size_t>(p.x0 + x * p.dx) * pixelsize);
            std::swap(previous, current);
        }
    }
    out.info() = std::move(info);
    return out;
}

// --- WebP ----------------------------------------------------------------------------

std::vector<Image> open_webp(const std::string &d, bool all_frames) {
    std::vector<Image> frames;
    if (!(starts(d, "VP8 ", 4, 12) || starts(d, "VP8X", 4, 12) || starts(d, "VP8L", 4, 12)))
        return frames;
    const auto *bytes = reinterpret_cast<const uint8_t *>(d.data());
    WebPBitstreamFeatures features;
    bool alpha = true;
    if (WebPGetFeatures(bytes, d.size(), &features) == VP8_STATUS_OK && !features.has_alpha)
        alpha = false;
    WebPData source = {bytes, d.size()};
    WebPAnimDecoder *decoder = WebPAnimDecoderNew(&source, nullptr);
    if (!decoder)
        return frames;
    WebPAnimInfo anim;
    if (!WebPAnimDecoderGetInfo(decoder, &anim) || bomb(anim.canvas_width, anim.canvas_height)) {
        WebPAnimDecoderDelete(decoder);
        return frames;
    }
    Info info;
    const WebPDemuxer *demux = WebPAnimDecoderGetDemuxer(decoder);
    auto chunk = [&](const char *fourcc) -> std::optional<std::string> {
        WebPChunkIterator it;
        if (!WebPDemuxGetChunk(demux, fourcc, 1, &it))
            return std::nullopt;
        std::string value(reinterpret_cast<const char *>(it.chunk.bytes), it.chunk.size);
        WebPDemuxReleaseChunkIterator(&it);
        if (value.empty())
            return std::nullopt;
        return value;
    };
    info.icc_profile = chunk("ICCP");
    info.exif = chunk("EXIF");
    info.xmp = chunk("XMP ");
    uint32_t count = all_frames ? anim.frame_count : std::min<uint32_t>(anim.frame_count, 1);
    int w = static_cast<int>(anim.canvas_width), h = static_cast<int>(anim.canvas_height);
    try {
        for (uint32_t i = 0; i < count; ++i) {
            uint8_t *buffer = nullptr;
            int timestamp = 0;
            if (!WebPAnimDecoderGetNext(decoder, &buffer, &timestamp)) {
                frames.clear();
                break;
            }
            ImagingOwner owner;
            owner.im = new_image(alpha ? IMAGING_MODE_RGBA : IMAGING_MODE_RGB, w, h);
            for (int y = 0; y < h; ++y)  // rawmode RGBA or RGBX: four bytes a pixel
                std::memcpy(owner.im->image[y], buffer + static_cast<size_t>(y) * w * 4, static_cast<size_t>(w) * 4);
            frames.push_back(Image::adopt(owner.release(), info));
        }
    } catch (...) {
        WebPAnimDecoderDelete(decoder);
        throw;
    }
    WebPAnimDecoderDelete(decoder);
    return frames;
}

// --- AVIF ----------------------------------------------------------------------------

std::optional<Image> open_avif(const std::string &d) {
    if (!(starts(d, "ftyp", 4, 4) && (starts(d, "avif", 4, 8) || starts(d, "avis", 4, 8) ||
                                     starts(d, "mif1", 4, 8) || starts(d, "msf1", 4, 8))))
        return std::nullopt;
    avifDecoder *decoder = avifDecoderCreate();
    if (!decoder)
        return std::nullopt;
    struct Guard {
        avifDecoder *d;
        ~Guard() { avifDecoderDestroy(d); }
    } guard{decoder};
    decoder->maxThreads = default_max_threads();
    decoder->strictFlags &= ~AVIF_STRICT_CLAP_VALID;
    decoder->strictFlags &= ~AVIF_STRICT_PIXI_REQUIRED;
    decoder->codecChoice = AVIF_CODEC_CHOICE_AUTO;
    if (avifDecoderSetIOMemory(decoder, reinterpret_cast<const uint8_t *>(d.data()), d.size()) != AVIF_RESULT_OK ||
        avifDecoderParse(decoder) != AVIF_RESULT_OK)
        return std::nullopt;
    avifImage *image = decoder->image;
    if (bomb(image->width, image->height))
        return std::nullopt;
    Info info;
    if (image->icc.size)
        info.icc_profile = std::string(reinterpret_cast<const char *>(image->icc.data), image->icc.size);
    if (image->exif.size)
        info.exif = std::string(reinterpret_cast<const char *>(image->exif.data), image->exif.size);
    if (image->xmp.size)
        info.xmp = std::string(reinterpret_cast<const char *>(image->xmp.data), image->xmp.size);
    if (avifDecoderNthImage(decoder, 0) != AVIF_RESULT_OK)
        throw Error("Failed to decode AVIF frame");
    image = decoder->image;
    avifRGBImage rgb;
    avifRGBImageSetDefaults(&rgb, image);
    rgb.depth = 8;
    Mode mode;
    if (decoder->alphaPresent) {
        rgb.format = AVIF_RGB_FORMAT_RGBA;
        mode = Mode::RGBA;
    } else if (image->yuvFormat == AVIF_PIXEL_FORMAT_YUV400) {
        rgb.format = AVIF_RGB_FORMAT_GRAY;
        mode = Mode::L;
    } else {
        rgb.format = AVIF_RGB_FORMAT_RGB;
        mode = Mode::RGB;
    }
    if (avifRGBImageAllocatePixels(&rgb) != AVIF_RESULT_OK)
        throw Error("AVIF pixel allocation failed");
    if (avifImageYUVToRGB(image, &rgb) != AVIF_RESULT_OK) {
        avifRGBImageFreePixels(&rgb);
        throw Error("AVIF conversion from YUV failed");
    }
    Image out;
    try {
        size_t channels = mode == Mode::L ? 1 : mode == Mode::RGB ? 3 : 4;
        std::string packed;
        packed.reserve(static_cast<size_t>(rgb.width) * rgb.height * channels);
        for (uint32_t y = 0; y < rgb.height; ++y)
            packed.append(reinterpret_cast<const char *>(rgb.pixels) + static_cast<size_t>(y) * rgb.rowBytes,
                          static_cast<size_t>(rgb.width) * channels);
        out = Image::frombytes(mode, static_cast<int>(rgb.width), static_cast<int>(rgb.height), packed.data(),
                               packed.size());
    } catch (...) {
        avifRGBImageFreePixels(&rgb);
        throw;
    }
    avifRGBImageFreePixels(&rgb);
    out.info() = std::move(info);
    return out;
}

// --- through MuPDF ---------------------------------------------------------------------

// Jpeg2KImageFile._parse_comment: the first COM segment after SIZ, before
// any tile.
std::optional<std::string> j2k_comment(const std::string &d, size_t pos) {
    if (pos + 2 > d.size())
        return std::nullopt;
    pos += be16(d, pos);  // Lsiz counts its own two bytes
    for (;;) {
        if (pos + 2 > d.size())
            return std::nullopt;
        int type = static_cast<uint8_t>(d[pos + 1]);
        pos += 2;
        if (type == 0x90 || type == 0xD9)
            return std::nullopt;
        if (pos + 2 > d.size())
            return std::nullopt;
        size_t length = be16(d, pos);
        pos += 2;
        size_t body = length >= 2 ? length - 2 : 0;
        if (type == 0x64) {
            std::string comment = d.substr(pos, std::min(body, d.size() - pos));
            return comment.size() > 2 ? comment.substr(2) : std::string();
        }
        pos += body;
    }
}

// The comment Pillow reads from a JPEG 2000 file: a raw codestream, or a JP2
// whose codestream box follows the header box at once.
std::optional<std::string> jpx_comment(const std::string &d) {
    if (starts(d, "\xff\x4f\xff\x51", 4))
        return j2k_comment(d, 4);
    size_t pos = 0;
    while (pos + 8 <= d.size()) {
        uint64_t length = be32(d, pos);
        std::string type = d.substr(pos + 4, 4);
        size_t header = 8;
        if (length == 1) {
            if (pos + 16 > d.size())
                return std::nullopt;
            length = (static_cast<uint64_t>(be32(d, pos + 8)) << 32) | be32(d, pos + 12);
            header = 16;
        } else if (length == 0) {
            length = d.size() - pos;
        }
        if (length < header || length > d.size() - pos)
            return std::nullopt;
        pos += static_cast<size_t>(length);
        if (type == "jp2h") {
            if (pos + 12 <= d.size() && d.compare(pos + 4, 8, "jp2c\xff\x4f\xff\x51") == 0)
                return j2k_comment(d, pos + 12);
            return std::nullopt;
        }
    }
    return std::nullopt;
}

// `opaque_rgb`: a pixmap with an alpha channel that is opaque everywhere is
// read as colour alone (MuPDF reads every BMP with alpha, Pillow reads a
// 24-bit BMP as RGB).
Image from_pixmap(fz_context *ctx, fz_pixmap *pix, bool opaque_rgb = false) {
    fz_pixmap *converted = nullptr;
    fz_colorspace *cs = fz_pixmap_colorspace(ctx, pix);
    if (!cs || (!fz_colorspace_is_gray(ctx, cs) && !fz_colorspace_is_rgb(ctx, cs))) {
        bool failed = false;
        fz_var(converted);
        fz_var(failed);
        fz_var(pix);
        fz_try(ctx) {
            converted = fz_convert_pixmap(ctx, pix, fz_device_rgb(ctx), nullptr, nullptr, fz_default_color_params, 1);
        }
        fz_catch(ctx) { failed = true; }
        if (failed)
            throw Error("cannot convert the image to RGB");
        pix = converted;
    }
    int w = fz_pixmap_width(ctx, pix), h = fz_pixmap_height(ctx, pix);
    int n = fz_pixmap_components(ctx, pix);
    bool alpha = fz_pixmap_alpha(ctx, pix) != 0;
    int colorants = n - (alpha ? 1 : 0);
    const unsigned char *samples = fz_pixmap_samples(ctx, pix);
    size_t stride = static_cast<size_t>(fz_pixmap_stride(ctx, pix));
    if (alpha && opaque_rgb) {
        bool opaque = true;
        for (int y = 0; y < h && opaque; ++y) {
            const unsigned char *s = samples + static_cast<size_t>(y) * stride;
            for (int x = 0; x < w; ++x)
                if (s[static_cast<size_t>(x) * n + static_cast<size_t>(colorants)] != 255) {
                    opaque = false;
                    break;
                }
        }
        if (opaque) {
            std::string packed(static_cast<size_t>(w) * h * colorants, '\0');
            for (int y = 0; y < h; ++y) {
                const unsigned char *s = samples + static_cast<size_t>(y) * stride;
                auto *o = reinterpret_cast<uint8_t *>(packed.data()) + static_cast<size_t>(y) * w * colorants;
                for (int x = 0; x < w; ++x)
                    std::memcpy(o + static_cast<size_t>(x) * colorants, s + static_cast<size_t>(x) * n,
                                static_cast<size_t>(colorants));
            }
            if (converted)
                fz_drop_pixmap(ctx, converted);
            return Image::frombytes(colorants == 1 ? Mode::L : Mode::RGB, w, h, packed.data(), packed.size());
        }
    }
    Mode mode = colorants == 1 ? (alpha ? Mode::LA : Mode::L) : (alpha ? Mode::RGBA : Mode::RGB);
    std::string packed(static_cast<size_t>(w) * h * n, '\0');
    for (int y = 0; y < h; ++y) {
        const unsigned char *s = samples + static_cast<size_t>(y) * stride;
        auto *o = reinterpret_cast<uint8_t *>(packed.data()) + static_cast<size_t>(y) * w * n;
        for (int x = 0; x < w; ++x, s += n, o += n) {
            if (!alpha) {
                std::memcpy(o, s, static_cast<size_t>(n));
                continue;
            }
            int a = s[colorants];
            for (int k = 0; k < colorants; ++k)
                o[k] = a == 0 ? 0 : static_cast<uint8_t>(std::min(255, (s[k] * 255 + a / 2) / a));
            o[colorants] = static_cast<uint8_t>(a);
        }
    }
    if (converted)
        fz_drop_pixmap(ctx, converted);
    return Image::frombytes(mode, w, h, packed.data(), packed.size());
}

std::vector<Image> open_with_mupdf(mu::Context &context, const std::string &d, Format format, bool all_frames) {
    fz_context *ctx = context.get();
    std::vector<fz_pixmap *> pixmaps;
    const auto *bytes = reinterpret_cast<const unsigned char *>(d.data());
    fz_buffer *buf = nullptr;
    fz_image *image = nullptr;
    fz_var(buf);
    fz_var(image);
    fz_var(pixmaps);
    bool failed = false;
    fz_var(failed);
    fz_try(ctx) {
        if (format == Format::Tiff) {
            int count = all_frames ? fz_load_tiff_subimage_count(ctx, bytes, d.size()) : 1;
            for (int i = 0; i < count; ++i)
                pixmaps.push_back(fz_load_tiff_subimage(ctx, bytes, d.size(), i));
        } else {
            buf = fz_new_buffer_from_copied_data(ctx, bytes, d.size());
            image = fz_new_image_from_buffer(ctx, buf);
            if (bomb(image->w, image->h))
                fz_throw(ctx, FZ_ERROR_LIMIT, "image too large");
            pixmaps.push_back(fz_get_pixmap_from_image(ctx, image, nullptr, nullptr, nullptr, nullptr));
        }
    }
    fz_always(ctx) {
        fz_drop_image(ctx, image);
        fz_drop_buffer(ctx, buf);
    }
    fz_catch(ctx) { failed = true; }
    std::vector<Image> frames;
    try {
        if (!failed)
            for (fz_pixmap *pix : pixmaps)
                frames.push_back(from_pixmap(ctx, pix, format == Format::Bmp));
        if (format == Format::Jpeg2000 && !frames.empty())
            frames.front().info().comment = jpx_comment(d);
    } catch (...) {
        frames.clear();
    }
    for (fz_pixmap *pix : pixmaps)
        fz_drop_pixmap(ctx, pix);
    if (failed)
        frames.clear();
    return frames;
}

std::vector<Image> open_any(const std::string &data, mu::Context *mupdf, bool all_frames) {
    std::vector<Image> frames;
    Format format = sniff(data);
    try {
    switch (format) {
    case Format::Jpeg:
        if (auto image = open_jpeg(data))
            frames.push_back(*image);
        break;
    case Format::Png:
        if (auto image = open_png(data))
            frames.push_back(*image);
        break;
    case Format::Webp:
        frames = open_webp(data, all_frames);
        break;
    case Format::Avif:
        if (auto image = open_avif(data))
            frames.push_back(*image);
        break;
    case Format::Jpeg2000:
    case Format::Bmp:
    case Format::Tiff:
    case Format::Gif:
    case Format::Pnm:
        if (mupdf)
            frames = open_with_mupdf(*mupdf, data, format, all_frames);
        break;
    case Format::Unknown:
        break;
    }
    } catch (const Error &) {
        frames.clear();
    }
    return frames;
}

} // namespace

Format sniff(const std::string &d) {
    if (starts(d, "\xff\xd8\xff", 3))
        return Format::Jpeg;
    if (starts(d, "\x89PNG\r\n\x1a\n", 8))
        return Format::Png;
    if (starts(d, "RIFF", 4) && starts(d, "WEBP", 4, 8))
        return Format::Webp;
    if (starts(d, "ftyp", 4, 4))
        return Format::Avif;
    if (starts(d, "\xff\x4f\xff\x51", 4) || starts(d, "\x00\x00\x00\x0cjP  \x0d\x0a\x87\x0a", 12))
        return Format::Jpeg2000;
    if (starts(d, "BM", 2))
        return Format::Bmp;
    if (starts(d, "MM\x00\x2a", 4) || starts(d, "II\x2a\x00", 4) || starts(d, "MM\x2a\x00", 4) ||
        starts(d, "II\x00\x2a", 4) || starts(d, "MM\x00\x2b", 4) || starts(d, "II\x2b\x00", 4))
        return Format::Tiff;
    if (starts(d, "GIF87a", 6) || starts(d, "GIF89a", 6))
        return Format::Gif;
    if (d.size() >= 2 && d[0] == 'P' && std::strchr("0123456fy", d[1]) && d[1])
        return Format::Pnm;
    return Format::Unknown;
}

std::optional<Image> open(const std::string &data, mu::Context *mupdf) {
    std::vector<Image> frames = open_any(data, mupdf, false);
    if (frames.empty())
        return std::nullopt;
    return frames.front();
}

std::vector<Image> open_frames(const std::string &data, mu::Context *mupdf) { return open_any(data, mupdf, true); }

// --- writers -------------------------------------------------------------------------

std::string save_jpeg(const Image &image, int quality, bool optimize, bool progressive) {
    image.load();
    if (image.width() == 0 || image.height() == 0)
        throw Error("cannot write empty image as JPEG");
    int components;
    std::string raw;
    switch (image.mode()) {
    case Mode::L:
        components = 1;
        raw = image.tobytes();
        break;
    case Mode::One: {
        // rawmode "L": the bilevel image's bytes, 0 or 255
        components = 1;
        Imaging im = image.im();
        raw.resize(static_cast<size_t>(im->xsize) * im->ysize);
        for (int y = 0; y < im->ysize; ++y)
            std::memcpy(&raw[static_cast<size_t>(y) * im->xsize], im->image[y], static_cast<size_t>(im->xsize));
        break;
    }
    case Mode::RGB:
    case Mode::RGBX: {
        components = 3;
        Imaging im = image.im();
        raw.resize(static_cast<size_t>(im->xsize) * im->ysize * 3);
        for (int y = 0; y < im->ysize; ++y) {
            const auto *p = reinterpret_cast<const uint8_t *>(im->image[y]);
            auto *o = reinterpret_cast<uint8_t *>(&raw[static_cast<size_t>(y) * im->xsize * 3]);
            for (int x = 0; x < im->xsize; ++x) {
                o[3 * x] = p[4 * x];
                o[3 * x + 1] = p[4 * x + 1];
                o[3 * x + 2] = p[4 * x + 2];
            }
        }
        break;
    }
    default:
        throw Error(std::string("cannot write mode ") + mode_name(image.mode()) + " as JPEG");
    }
    const auto &comment = image.info().comment;
    unsigned char *out = nullptr;
    size_t out_len = 0;
    char err[200] = "";
    if (pdft_jpeg_encode(reinterpret_cast<const unsigned char *>(raw.data()), image.width(), image.height(),
                         components, quality, progressive ? 1 : 0, optimize ? 1 : 0,
                         comment ? reinterpret_cast<const unsigned char *>(comment->data()) : nullptr,
                         comment ? comment->size() : 0, &out, &out_len, err, sizeof err))
        throw Error(std::string("JPEG encoder error: ") + err);
    std::string result(reinterpret_cast<const char *>(out), out_len);
    std::free(out);
    return result;
}

namespace {

void put_chunk(std::string &out, const char *cid, const std::string &data) {
    auto put32 = [&](uint32_t v) {
        out += static_cast<char>((v >> 24) & 0xFF);
        out += static_cast<char>((v >> 16) & 0xFF);
        out += static_cast<char>((v >> 8) & 0xFF);
        out += static_cast<char>(v & 0xFF);
    };
    put32(static_cast<uint32_t>(data.size()));
    out.append(cid, 4);
    out += data;
    uLong crc = crc32(0L, reinterpret_cast<const Bytef *>(cid), 4);
    crc = crc32(crc, reinterpret_cast<const Bytef *>(data.data()), static_cast<uInt>(data.size()));
    put32(static_cast<uint32_t>(crc));
}

std::string o32(uint32_t v) {
    std::string s(4, '\0');
    s[0] = static_cast<char>(v >> 24);
    s[1] = static_cast<char>(v >> 16);
    s[2] = static_cast<char>(v >> 8);
    s[3] = static_cast<char>(v);
    return s;
}

std::string o16(uint32_t v) {
    std::string s(2, '\0');
    s[0] = static_cast<char>(v >> 8);
    s[1] = static_cast<char>(v);
    return s;
}

// Pack.c: one row of the image in the PNG raw mode.
void png_pack(const std::string &rawmode, const uint8_t *in, int pixels, uint8_t *out) {
    if (rawmode == "1" || rawmode == "P;1") {
        int b = 0, m = 128;
        for (int i = 0; i < pixels; ++i) {
            if (in[i] != 0)
                b |= m;
            m >>= 1;
            if (m == 0) {
                *out++ = static_cast<uint8_t>(b);
                b = 0;
                m = 128;
            }
        }
        if (m != 128)
            *out++ = static_cast<uint8_t>(b);
    } else if (rawmode == "P;2") {
        int i = 0;
        for (; pixels - i >= 4; i += 4)
            *out++ = static_cast<uint8_t>((in[i] << 6) | ((in[i + 1] & 3) << 4) | ((in[i + 2] & 3) << 2) |
                                          (in[i + 3] & 3));
        switch (pixels - i) {
        case 3: *out = static_cast<uint8_t>((in[i] << 6) | ((in[i + 1] & 3) << 4) | ((in[i + 2] & 3) << 2)); break;
        case 2: *out = static_cast<uint8_t>((in[i] << 6) | ((in[i + 1] & 3) << 4)); break;
        case 1: *out = static_cast<uint8_t>(in[i] << 6); break;
        default: break;
        }
    } else if (rawmode == "P;4") {
        int i = 0;
        for (; pixels - i >= 2; i += 2)
            *out++ = static_cast<uint8_t>((in[i] << 4) | (in[i + 1] & 15));
        if (pixels - i)
            *out = static_cast<uint8_t>(in[i] << 4);
    } else if (rawmode == "L" || rawmode == "P") {
        std::memcpy(out, in, static_cast<size_t>(pixels));
    } else if (rawmode == "LA") {
        for (int i = 0; i < pixels; ++i) {
            out[2 * i] = in[4 * i];
            out[2 * i + 1] = in[4 * i + 3];
        }
    } else if (rawmode == "I;16B") {
        for (int i = 0; i < pixels; ++i) {
            out[2 * i] = in[2 * i + 1];
            out[2 * i + 1] = in[2 * i];
        }
    } else if (rawmode == "RGB") {
        for (int i = 0; i < pixels; ++i) {
            out[3 * i] = in[4 * i];
            out[3 * i + 1] = in[4 * i + 1];
            out[3 * i + 2] = in[4 * i + 2];
        }
    } else if (rawmode == "RGBA") {
        std::memcpy(out, in, static_cast<size_t>(pixels) * 4);
    }
}

// ImagingZipEncode driven as ImageFile._save drives it: the image row by
// row, each row under the filter LIBPNG's heuristic picks (none for 8-bit
// palette rows), the deflated stream cut into IDAT chunks of `bufsize` bytes.
void png_idat(std::string &png, Imaging im, const std::string &rawmode, int bits, int level, bool palette) {
    int xsize = im->xsize, ysize = im->ysize;
    size_t bytes = (static_cast<size_t>(xsize) * static_cast<size_t>(bits) + 7) / 8;
    int bpp = (bits + 7) / 8;
    size_t bufsize = std::max<size_t>(65536, static_cast<size_t>(xsize) * 4);
    std::vector<uint8_t> buffer(bytes + 1), previous(bytes + 1, 0), prior(bytes + 1), up(bytes + 1),
        paeth(bytes + 1);
    buffer[0] = 0;
    prior[0] = 1;
    up[0] = 2;
    paeth[0] = 4;
    Deflater z(level, palette ? Deflater::Strategy::Default : Deflater::Strategy::Filtered, bufsize,
               [&png](const uint8_t *data, size_t size) {
                   put_chunk(png, "IDAT", std::string(reinterpret_cast<const char *>(data), size));
               });
    for (int y = 0; y < ysize; ++y) {
        png_pack(rawmode, reinterpret_cast<const uint8_t *>(im->image[y]), xsize, buffer.data() + 1);
        uint8_t *output = buffer.data();
        if (!palette) {
            auto cost = [](uint8_t v) { return v < 128 ? v : 256 - v; };
            int sum = 0, s;
            for (size_t i = 1; i <= bytes; ++i)
                sum += cost(buffer[i]);
            if (sum > 0) {
                s = 0;
                for (size_t i = 1; i <= bytes; ++i) {
                    up[i] = static_cast<uint8_t>(buffer[i] - previous[i]);
                    s += cost(up[i]);
                }
                if (s < sum) {
                    output = up.data();
                    sum = s;
                }
            }
            if (sum > 0) {
                s = 0;
                size_t i = 1;
                for (; i <= static_cast<size_t>(bpp) && i <= bytes; ++i) {
                    prior[i] = buffer[i];
                    s += cost(prior[i]);
                }
                for (; i <= bytes; ++i) {
                    prior[i] = static_cast<uint8_t>(buffer[i] - buffer[i - static_cast<size_t>(bpp)]);
                    s += cost(prior[i]);
                }
                if (s < sum) {
                    output = prior.data();
                    sum = s;
                }
            }
            // The average filter runs only with optimize, which the engine never sets.
            if (sum > 0) {
                s = 0;
                size_t i = 1;
                for (; i <= static_cast<size_t>(bpp) && i <= bytes; ++i) {
                    paeth[i] = static_cast<uint8_t>(buffer[i] - previous[i]);
                    s += cost(paeth[i]);
                }
                for (; i <= bytes; ++i) {
                    int a = buffer[i - static_cast<size_t>(bpp)];
                    int b = previous[i];
                    int c = previous[i - static_cast<size_t>(bpp)];
                    int pa = std::abs(b - c), pb = std::abs(a - c), pc = std::abs(a + b - 2 * c);
                    paeth[i] = static_cast<uint8_t>(buffer[i] - ((pa <= pb && pa <= pc) ? a : (pb <= pc) ? b : c));
                    s += cost(paeth[i]);
                }
                if (s < sum) {
                    output = paeth.data();
                    sum = s;
                }
            }
        }
        z.write(output, bytes + 1);
        std::swap(buffer, previous);
        buffer[0] = 0;
    }
    z.finish();
}

} // namespace

std::string save_png(const Image &image, int compress_level) {
    Imaging im = image.im();
    Mode mode = image.mode();
    std::string rawmode;
    int bits = 0, bit_depth = 8, colour_type = 0;
    std::vector<uint8_t> palette;
    int colors = 0;
    switch (mode) {
    case Mode::One: rawmode = "1"; bits = 1; bit_depth = 1; colour_type = 0; break;
    case Mode::L: rawmode = "L"; bits = 8; colour_type = 0; break;
    case Mode::LA: rawmode = "LA"; bits = 16; colour_type = 4; break;
    case Mode::I16: rawmode = "I;16B"; bits = 16; bit_depth = 16; colour_type = 0; break;
    case Mode::RGB: rawmode = "RGB"; bits = 24; colour_type = 2; break;
    case Mode::RGBA: rawmode = "RGBA"; bits = 32; colour_type = 6; break;
    case Mode::P: {
        palette = image.palette_rgb();
        colors = std::max(1, std::min(256, static_cast<int>(palette.size() / 3)));
        colour_type = 3;
        if (colors <= 2) {
            rawmode = "P;1"; bits = bit_depth = 1;
        } else if (colors <= 4) {
            rawmode = "P;2"; bits = bit_depth = 2;
        } else if (colors <= 16) {
            rawmode = "P;4"; bits = bit_depth = 4;
        } else {
            rawmode = "P"; bits = 8; bit_depth = 8;
        }
        break;
    }
    default:
        throw Error(std::string("cannot write mode ") + mode_name(mode) + " as PNG");
    }
    std::string png("\x89PNG\r\n\x1a\n", 8);
    std::string ihdr = o32(static_cast<uint32_t>(im->xsize)) + o32(static_cast<uint32_t>(im->ysize));
    ihdr += static_cast<char>(bit_depth);
    ihdr += static_cast<char>(colour_type);
    ihdr += std::string(3, '\0');
    put_chunk(png, "IHDR", ihdr);
    const Info &info = image.info();
    if (info.icc_profile && !info.icc_profile->empty()) {
        // zlib.compress(icc): level 6
        put_chunk(png, "iCCP", std::string("ICC Profile\0\0", 13) + zlib_compress(*info.icc_profile, 6));
    }
    if (mode == Mode::P) {
        std::string plte;
        if (palette.empty())
            plte = std::string(3, '\0');
        else
            plte.assign(reinterpret_cast<const char *>(palette.data()),
                        std::min(palette.size(), static_cast<size_t>(colors) * 3));
        put_chunk(png, "PLTE", plte);
    }
    const Transparency &t = info.transparency;
    if (t.kind != Transparency::None) {
        if (mode == Mode::P) {
            if (t.kind == Transparency::Bytes) {
                put_chunk(png, "tRNS", t.bytes.substr(0, static_cast<size_t>(colors)));
            } else if (t.kind == Transparency::Int) {
                int index = std::max(0, std::min(255, t.value));
                std::string alpha(static_cast<size_t>(index), '\xff');
                alpha += '\0';
                put_chunk(png, "tRNS", alpha.substr(0, static_cast<size_t>(colors)));
            }
        } else if ((mode == Mode::One || mode == Mode::L || mode == Mode::I16) && t.kind == Transparency::Int) {
            put_chunk(png, "tRNS", o16(static_cast<uint32_t>(std::max(0, std::min(65535, t.value)))));
        } else if (mode == Mode::RGB && t.kind == Transparency::Triple) {
            put_chunk(png, "tRNS", o16(static_cast<uint32_t>(t.rgb[0])) + o16(static_cast<uint32_t>(t.rgb[1])) +
                                       o16(static_cast<uint32_t>(t.rgb[2])));
        }
    }
    // Filtering is off for 8-bit palette rows alone (rawmode "P"; Pillow's
    // encode.c): packed palette rows (P;1, P;2, P;4) are filtered like the rest.
    png_idat(png, im, rawmode, bits, compress_level, rawmode == "P");
    put_chunk(png, "IEND", std::string());
    return png;
}

std::string save_webp(const Image &image, bool lossless, double quality, int method) {
    Image frame = image;
    Mode m = frame.mode();
    if (m != Mode::RGBX && m != Mode::RGBA && m != Mode::RGB) {
        bool transparency = m == Mode::LA || m == Mode::La || m == Mode::PA || m == Mode::RGBA || m == Mode::RGBa ||
                            frame.info().transparency.kind != Transparency::None;
        frame = frame.convert(transparency ? Mode::RGBA : Mode::RGB);
    }
    Imaging im = frame.im();
    WebPConfig config;
    if (!WebPConfigInit(&config))
        throw Error("failed to initialize config!");
    config.lossless = lossless ? 1 : 0;
    config.quality = static_cast<float>(quality);
    config.alpha_quality = 100.0f;
    config.method = method;
    config.exact = 0;
    if (!WebPValidateConfig(&config))
        throw Error("invalid configuration");
    WebPPicture pic;
    if (!WebPPictureInit(&pic))
        throw Error("could not initialise picture");
    pic.width = im->xsize;
    pic.height = im->ysize;
    pic.use_argb = 1;
    if (!WebPPictureAlloc(&pic))
        throw Error("can't allocate picture frame");
    bool ignore_fourth = frame.mode() != Mode::RGBA;
    for (int y = 0; y < im->ysize; ++y) {
        const auto *src = reinterpret_cast<const uint8_t *>(im->image[y]);
        uint32_t *dst = pic.argb + static_cast<size_t>(pic.argb_stride) * y;
        for (int x = 0; x < im->xsize; ++x)
            dst[x] = static_cast<uint32_t>(src[x * 4 + 2]) | (static_cast<uint32_t>(src[x * 4 + 1]) << 8) |
                     (static_cast<uint32_t>(src[x * 4]) << 16) |
                     (ignore_fourth ? 0xFF000000u : static_cast<uint32_t>(src[x * 4 + 3]) << 24);
    }
    WebPMemoryWriter writer;
    WebPMemoryWriterInit(&writer);
    pic.writer = WebPMemoryWrite;
    pic.custom_ptr = &writer;
    int ok = WebPEncode(&config, &pic);
    int error_code = pic.error_code;
    WebPPictureFree(&pic);
    if (!ok) {
        WebPMemoryWriterClear(&writer);
        throw Error("WebP encoding error " + std::to_string(error_code));
    }
    WebPData encoded = {writer.mem, writer.size};
    WebPData assembled = {nullptr, 0};
    WebPMux *mux = WebPMuxNew();
    if (!mux) {
        WebPMemoryWriterClear(&writer);
        throw Error("could not create mux object");
    }
    WebPMuxSetImage(mux, &encoded, 0);
    WebPMuxAssemble(mux, &assembled);
    WebPMuxDelete(mux);
    WebPMemoryWriterClear(&writer);
    if (!assembled.size)
        throw Error("cannot write file as WebP (encoder returned None)");
    std::string out(reinterpret_cast<const char *>(assembled.bytes), assembled.size);
    WebPDataClear(&assembled);
    return out;
}

std::string save_avif(const Image &image, int quality, int speed) {
    if (quality < 0 || quality > 100)
        throw Error("Invalid quality setting");
    Image frame = image;
    Mode m = frame.mode();
    bool grayscale = m == Mode::One || m == Mode::L || m == Mode::I || m == Mode::I16 || m == Mode::F;
    if (m != Mode::L && m != Mode::RGB && m != Mode::RGBA) {
        bool transparency = m == Mode::LA || m == Mode::La || m == Mode::PA || m == Mode::RGBa ||
                            frame.info().transparency.kind != Transparency::None;
        frame = frame.convert(transparency ? Mode::RGBA : grayscale ? Mode::L : Mode::RGB);
    }
    frame.load();
    if (frame.width() == 0 || frame.height() == 0)
        throw Error("invalid image size");
    avifImage *avif = avifImageCreateEmpty();
    avifEncoder *encoder = avifEncoderCreate();
    struct Guard {
        avifImage *image;
        avifEncoder *encoder;
        avifRWData output = AVIF_DATA_EMPTY;
        ~Guard() {
            avifRWDataFree(&output);
            if (encoder)
                avifEncoderDestroy(encoder);
            if (image)
                avifImageDestroy(image);
        }
    } guard{avif, encoder};
    if (!avif || !encoder)
        throw Error("cannot create the AVIF encoder");
    avif->yuvRange = AVIF_RANGE_FULL;
    avif->yuvFormat = grayscale ? AVIF_PIXEL_FORMAT_YUV400 : AVIF_PIXEL_FORMAT_YUV420;
    avif->width = static_cast<uint32_t>(frame.width());
    avif->height = static_cast<uint32_t>(frame.height());
    avif->depth = 8;
    avif->alphaPremultiplied = AVIF_FALSE;
    int threads = default_max_threads();
    encoder->maxThreads = threads > 64 ? 64 : threads;
    encoder->quality = quality;
    encoder->codecChoice = AVIF_CODEC_CHOICE_AUTO;
    encoder->speed = std::max(AVIF_SPEED_SLOWEST, std::min(AVIF_SPEED_FASTEST, speed));
    encoder->timescale = 1000;
    encoder->autoTiling = AVIF_TRUE;
    const auto &icc = frame.info().icc_profile;
    if (icc && !icc->empty()) {
        if (avifImageSetProfileICC(avif, reinterpret_cast<const uint8_t *>(icc->data()), icc->size()) !=
            AVIF_RESULT_OK)
            throw Error("cannot set the AVIF ICC profile");
        avif->colorPrimaries = AVIF_COLOR_PRIMARIES_UNSPECIFIED;
        avif->transferCharacteristics = AVIF_TRANSFER_CHARACTERISTICS_UNSPECIFIED;
    } else {
        avif->colorPrimaries = AVIF_COLOR_PRIMARIES_BT709;
        avif->transferCharacteristics = AVIF_TRANSFER_CHARACTERISTICS_SRGB;
    }
    avif->matrixCoefficients = AVIF_MATRIX_COEFFICIENTS_BT601;

    avifRGBImage rgb;
    avifRGBImageSetDefaults(&rgb, avif);
    Mode raw_mode = frame.mode();
    rgb.format = raw_mode == Mode::RGBA ? AVIF_RGB_FORMAT_RGBA : raw_mode == Mode::L ? AVIF_RGB_FORMAT_GRAY
                                                                                     : AVIF_RGB_FORMAT_RGB;
    if (avifRGBImageAllocatePixels(&rgb) != AVIF_RESULT_OK)
        throw Error("AVIF pixel allocation failed");
    std::string raw = frame.tobytes();
    if (static_cast<size_t>(rgb.rowBytes) * rgb.height != raw.size()) {
        avifRGBImageFreePixels(&rgb);
        throw Error("AVIF image data size mismatch");
    }
    std::memcpy(rgb.pixels, raw.data(), raw.size());
    avifResult result = avifImageRGBToYUV(avif, &rgb);
    avifRGBImageFreePixels(&rgb);
    if (result != AVIF_RESULT_OK)
        throw Error(std::string("Conversion to YUV failed: ") + avifResultToString(result));
    result = avifEncoderAddImage(encoder, avif, 0, AVIF_ADD_IMAGE_FLAG_SINGLE);
    if (result != AVIF_RESULT_OK)
        throw Error(std::string("Failed to encode image: ") + avifResultToString(result));
    result = avifEncoderFinish(encoder, &guard.output);
    if (result != AVIF_RESULT_OK)
        throw Error(std::string("Failed to finish encoding: ") + avifResultToString(result));
    return std::string(reinterpret_cast<const char *>(guard.output.data), guard.output.size);
}

} // namespace pdft::img
