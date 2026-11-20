// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// Reading and writing image files as Pillow does it for the engine:
// Image.open() and img.save(format=...).
//
// JPEG goes through MuPDF's libjpeg, PNG through MuPDF's zlib, WebP through
// libwebp, AVIF through libavif with libaom. What Pillow reads with codecs
// this build does not carry (JPEG 2000, BMP, TIFF, GIF, PNM) is decoded by
// MuPDF instead.
#pragma once

#include "image.h"

#include <optional>
#include <string>
#include <vector>

namespace pdft::mu {
class Context;
}

namespace pdft::img {

enum class Format { Unknown, Jpeg, Png, Webp, Avif, Jpeg2000, Bmp, Tiff, Gif, Pnm };

// The format Image.open() recognises from the first bytes.
Format sniff(const std::string &data);

// Image.open(BytesIO(data)): nullopt where Pillow would fail to open it. A
// JPEG keeps its pixels undecoded until they are needed, so that a
// thumbnail() can still ask the decoder for a reduced size, as in Pillow;
// the other formats decode at once. `mupdf` decodes the formats left to
// MuPDF; without it they are refused.
std::optional<Image> open(const std::string &data, mu::Context *mupdf = nullptr);

// The frames `for index in range(img.n_frames): img.seek(index)` visits:
// several for an animated WebP or a multi-page TIFF. Empty if unreadable.
std::vector<Image> open_frames(const std::string &data, mu::Context *mupdf = nullptr);

// img.save(buf, format="JPEG", quality=, optimize=, progressive=)
std::string save_jpeg(const Image &image, int quality, bool optimize, bool progressive);
// img.save(buf, format="PNG", compress_level=)
std::string save_png(const Image &image, int compress_level);
// img.save(buf, format="WEBP", lossless=, quality=, method=)
std::string save_webp(const Image &image, bool lossless, double quality, int method);
// img.save(buf, format="AVIF", quality=, speed=)
std::string save_avif(const Image &image, int quality, int speed);

} // namespace pdft::img
