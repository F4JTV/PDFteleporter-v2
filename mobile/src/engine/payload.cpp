// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// Image payloads: the smallest faithful JPEG or PNG for a .psdi, AVIF and
// WebP for a .psdz, and the images and regions they are made from
// (engine.py, "_is_effectively_monochrome" to "_optimize_image").

#include "internal.h"

#include "codecs.h"
#include "image.h"
#include "metrics.h"
#include "pyutil.h"

#include <algorithm>
#include <cmath>
#include <map>

namespace pdft::engine {

namespace {

using img::Image;
using img::Mode;

constexpr int kPaletteSizes[] = {4, 8, 16, 32, 64, 128, 256};
constexpr int kPaletteMaxColours = 4096;
constexpr double kPaletteMarginDb = 1.0;
constexpr long long kPngDeflateMaxPixels = 65536;
constexpr long long kPngMaxPixels = 8000000;
constexpr int kPaletteKmeans = 2;
constexpr int kAvifSpeed = 6;
constexpr int kAvifPageSpeed = 7;
constexpr long long kAvifPagePixels = 1000000;
constexpr long long kWebpLossyMaxPixels = 20000;

long long pixels(const Image &image) { return static_cast<long long>(image.width()) * image.height(); }

size_t stored(const std::string &payload, int lzma_preset) {
    return psdi::pack_payload(payload, lzma_preset).data.size();
}

bool is_effectively_monochrome(const Image &image, double threshold = 0.002) {
    std::string raw = image.resize(64, 64).tobytes();
    size_t total = raw.size() / 3;
    size_t coloured = 0;
    for (size_t offset = 0; offset < total * 3; offset += 3) {
        auto a = static_cast<uint8_t>(raw[offset]), b = static_cast<uint8_t>(raw[offset + 1]),
             c = static_cast<uint8_t>(raw[offset + 2]);
        if (std::max({a, b, c}) - std::min({a, b, c}) > 18)
            ++coloured;
    }
    return total ? static_cast<double>(coloured) / static_cast<double>(total) <= threshold : true;
}

std::string encode_jpeg(Image image, int quality, bool allow_grayscale = true) {
    if (allow_grayscale && image.mode() == Mode::RGB && is_effectively_monochrome(image))
        image = image.convert(Mode::L);
    return img::save_jpeg(image, quality, true, true);
}

Image decoded(const std::string &payload) {
    auto image = img::open(payload);
    if (!image)
        throw img::Error("cannot read an encoded payload back");
    image->load();
    return *image;
}

Image own_palette(const Image &image, const std::vector<std::pair<long, std::vector<int>>> &colours) {
    std::vector<uint8_t> palette;
    for (const auto &[count, rgb] : colours)
        for (int v : rgb)
            palette.push_back(static_cast<uint8_t>(v));
    if (palette.size() < 768)
        palette.resize(768, 0);
    return image.quantize_to(palette);
}

// _exact_palette: an RGB image of at most 256 colours on a palette of
// exactly those, which PNG packs at 1, 2 or 4 bits a pixel; nullopt when the
// mapping, through the same cache as own_palette's, is not exact.
std::optional<Image> exact_palette(const Image &image,
                                   const std::vector<std::pair<long, std::vector<int>>> &colours) {
    std::vector<uint8_t> palette;
    for (const auto &[count, rgb] : colours)
        for (int v : rgb)
            palette.push_back(static_cast<uint8_t>(v));
    Image mapped = image.quantize_to(palette);
    if (img::difference(image, mapped.convert(Mode::RGB)).getbbox())
        return std::nullopt;
    return mapped;
}

std::vector<Image> palettes_at(const Image &base, int size, double target) {
    Image quantised = base.quantize(size, kPaletteKmeans);
    if (img::psnr(base, quantised) >= target)
        return {quantised};
    return {};
}

std::vector<Image> smallest_palettes(const Image &base, int n_colours, double target) {
    std::vector<int> sizes;
    for (int size : kPaletteSizes)
        if (size < n_colours)
            sizes.push_back(size);
    if (sizes.empty())
        return {};
    size_t index = std::min<size_t>(1, sizes.size() - 1);
    std::vector<Image> found = palettes_at(base, sizes[index], target);
    if (!found.empty()) {
        while (index > 0) {
            std::vector<Image> lower = palettes_at(base, sizes[index - 1], target);
            if (lower.empty())
                break;
            --index;
            found = lower;
        }
    } else {
        while (found.empty() && index < sizes.size() - 1) {
            ++index;
            found = palettes_at(base, sizes[index], target);
        }
    }
    return found;
}

// _smallest_jpeg: progressive or baseline, whichever the container stores
// smaller.
std::string smallest_jpeg(const Image &image, int quality, int lzma_preset) {
    std::optional<std::pair<size_t, std::string>> best;
    for (bool progressive : {true, false}) {
        std::string payload = img::save_jpeg(image, quality, true, progressive);
        size_t size = stored(payload, lzma_preset);
        if (!best || size < best->first)
            best = std::make_pair(size, payload);
    }
    return best->second;
}

std::string smallest_classic_payload(const Image &image, int quality, int lzma_preset) {
    std::string jpeg = smallest_jpeg(image, quality, lzma_preset);
    std::optional<std::pair<size_t, std::string>> best = std::make_pair(stored(jpeg, lzma_preset), jpeg);
    if (pixels(image) > kPngMaxPixels)
        return best->second;
    auto colours = image.getcolors(kPaletteMaxColours);
    if (!colours)
        return best->second;

    double target = img::psnr(image, decoded(best->second)) + kPaletteMarginDb;
    std::vector<Image> candidates;
    if (image.mode() == Mode::L) {
        candidates.push_back(image);
    } else if (colours->size() <= 256) {
        Image own = own_palette(image, *colours);
        if (img::psnr(image, own) >= target)
            candidates.push_back(own);
    }
    for (Image &p : smallest_palettes(image, static_cast<int>(colours->size()), target))
        candidates.push_back(p);

    std::vector<int> levels = pixels(image) <= kPngDeflateMaxPixels ? std::vector<int>{0, 9} : std::vector<int>{0};
    for (const Image &candidate : candidates)
        for (int level : levels) {
            std::string payload = img::save_png(candidate, level);
            size_t size = stored(payload, lzma_preset);
            if (size < best->first)
                best = std::make_pair(size, payload);
        }
    return best->second;
}

using Encoder = std::function<std::string(int quality)>;

std::optional<std::string> matching_quality(const Image &image, const Encoder &encode, double target_psnr,
                                            double target_ssim) {
    std::map<int, std::string> payloads;
    auto decode = [&](int quality) {
        auto it = payloads.find(quality);
        if (it == payloads.end())
            it = payloads.emplace(quality, encode(quality)).first;
        return decoded(it->second);
    };
    auto lowest = [&](const std::function<bool(int)> &passes, int low) -> std::optional<int> {
        int high = 100;
        std::optional<int> found;
        while (low <= high) {
            int quality = (low + high) / 2;
            if (passes(quality)) {
                found = quality;
                high = quality - 1;
            } else {
                low = quality + 1;
            }
        }
        return found;
    };
    auto psnr_passes = [&](int quality) { return img::psnr(image, decode(quality)) >= target_psnr; };
    auto both_pass = [&](int quality) {
        Image d = decode(quality);
        return img::psnr(image, d) >= target_psnr && img::ssim(image, d) >= target_ssim;
    };
    std::optional<int> quality = lowest(psnr_passes, 1);
    if (quality && img::ssim(image, decode(*quality)) < target_ssim)
        quality = lowest(both_pass, *quality + 1);
    if (!quality)
        return std::nullopt;
    return payloads.at(*quality);
}

std::string compact_payload(const Image &image, const std::string &payload, int lzma_preset) {
    std::pair<size_t, std::string> best{stored(payload, lzma_preset), payload};
    std::string kind = psdi::payload_kind(payload);
    if (kind == "jpeg") {
        Image d = decoded(payload);
        double target_psnr = img::psnr(image, d);
        double target_ssim = img::ssim(image, d);
        long long px = pixels(image);
        int speed = px <= kAvifPagePixels ? kAvifSpeed : kAvifPageSpeed;
        std::vector<Encoder> encoders;
        encoders.push_back([&](int q) { return img::save_avif(image, q, speed); });
        if (px <= kWebpLossyMaxPixels)
            encoders.push_back([&](int q) { return img::save_webp(image, false, q, 6); });
        for (const Encoder &encode : encoders) {
            if (stored(encode(1), lzma_preset) >= best.first)
                continue;
            auto found = matching_quality(image, encode, target_psnr, target_ssim);
            if (found && stored(*found, lzma_preset) < best.first)
                best = {stored(*found, lzma_preset), *found};
        }
    } else if (kind == "png") {
        Image source = decoded(payload);
        if (source.mode() != Mode::L && source.mode() != Mode::RGB)
            source = source.convert(Mode::RGB);
        std::string webp = img::save_webp(source, true, 100, 4);
        if (stored(webp, lzma_preset) < best.first)
            best = {stored(webp, lzma_preset), webp};
    }
    return best.second;
}

std::string encode_payload(Image image, int quality, int lzma_preset, bool compact) {
    if (image.mode() == Mode::RGB && is_effectively_monochrome(image))
        image = image.convert(Mode::L);
    std::string payload = smallest_classic_payload(image, quality, lzma_preset);
    if (compact)
        payload = compact_payload(image, payload, lzma_preset);
    return payload;
}

Image raster_image(const mu::Raster &r) {
    if (r.n != 3 || r.alpha)
        throw img::Error("expected an RGB raster");
    return Image::frombytes(Mode::RGB, r.w, r.h, r.samples.data(), r.samples.size());
}

// _optimize_image
std::optional<PlacedImage> optimize_image(const std::string &img_bytes, int width, int height, const Params &params,
                                          std::optional<std::pair<double, double>> placed_pt) {
    auto opened = img::open(img_bytes);
    if (!opened)
        return std::nullopt;
    Image image = *opened;
    // Pillow decodes JPEG data lazily; damage it reports while decoding would
    // stop the whole conversion there. Here the image is skipped instead, as
    // it is when the data cannot be opened at all.
    try {
        if (image.mode() == Mode::RGBA) {
            Image background = Image::filled(Mode::RGB, image.width(), image.height(), {255, 255, 255, 255});
            Image alpha = image.split()[3];
            background.paste(image, &alpha);
            image = background;
        } else if (image.mode() != Mode::RGB) {
            image = image.convert(Mode::RGB);
        }
        const Preset &p = params.preset;
        if (width > 1000 && height < 200) {
            int new_w = std::min(width, p.img_max_dim);
            int new_h = std::min(height, p.banner_max_h);
            if (new_w != width || new_h != height)
                image = image.resize(new_w, new_h, img::Filter::Lanczos);
        } else {
            int max_dim;
            if (width > 900 && height > 900)
                max_dim = p.map_max_dim;
            else if (width < 300 && height < 300)
                max_dim = 100;
            else
                max_dim = p.img_max_dim;
            if (std::max(image.width(), image.height()) > max_dim)
                image.thumbnail(max_dim, max_dim, img::Filter::Lanczos);
        }
        if (placed_pt) {
            double dpi = p.dpi;
            long long need_w = py::round_int(placed_pt->first * dpi / 72 * 1.1);
            long long need_h = py::round_int(placed_pt->second * dpi / 72 * 1.1);
            if (need_w > 0 && need_h > 0 && (image.width() > need_w || image.height() > need_h)) {
                double scale = std::min({static_cast<double>(need_w) / image.width(),
                                         static_cast<double>(need_h) / image.height(), 1.0});
                int tw = std::max(1, static_cast<int>(image.width() * scale));
                int th = std::max(1, static_cast<int>(image.height() * scale));
                if (tw != image.width() || th != image.height())
                    image = image.resize(tw, th, img::Filter::Lanczos);
            }
        }
        PlacedImage out;
        out.payload = encode_payload(image, p.jpeg_quality, p.lzma_preset, params.compact);
        out.width = width;
        out.height = height;
        return out;
    } catch (const img::Error &) {
        return std::nullopt;
    }
}

} // namespace

std::optional<std::string> render_region(mu::Page &page, const Rect &region, const Params &params) {
    try {
        double dpi = params.preset.dpi;
        mu::Raster pix = page.render(dpi / 72, region);
        if (pix.w < 2 || pix.h < 2)
            return std::nullopt;
        return encode_payload(raster_image(pix), params.preset.jpeg_quality, params.preset.lzma_preset,
                              params.compact);
    } catch (const std::exception &) {
        return std::nullopt;
    }
}

std::optional<PlacedImage> extract_placed_image(mu::Document &doc, int xref, const Params &params,
                                                std::pair<double, double> placed_pt) {
    std::optional<mu::ExtractedImage> raw;
    try {
        raw = doc.extract_image(xref);
    } catch (const std::exception &) {
        return std::nullopt;
    }
    if (!raw)
        return std::nullopt;
    // _image_bytes_with_mask
    std::string pixels = raw->image;
    if (raw->smask) {
        if (auto png = doc.image_with_mask_png(xref, raw->smask))
            pixels = *png;
    }
    if (pixels.empty())
        return std::nullopt;
    return optimize_image(pixels, raw->width, raw->height, params, placed_pt);
}

std::string encode_page(const img::Image &image, const Params &params) {
    std::string jpeg = encode_jpeg(image, params.preset.jpeg_quality);
    if (!params.compact)
        return jpeg;
    auto header = img::open(jpeg);
    Image reference = header && header->mode() == Mode::L ? image.convert(Mode::L) : image;
    return compact_payload(reference, jpeg, params.preset.lzma_preset);
}

// _encode_image_file_page: the page of an image file in its smallest
// faithful form -- the JPEG or PNG an image of a structured archive gets,
// then the source itself, exactly and at its own resolution, when it has
// 256 colours or fewer; in a compact archive, the smaller of AVIF matched on
// the JPEG and lossless WebP of the PNG.
std::string encode_image_file_page(const img::Image &source, img::Image image, const Params &params) {
    const int quality = params.preset.jpeg_quality, preset = params.preset.lzma_preset;
    if (image.mode() == Mode::RGB && is_effectively_monochrome(image))
        image = image.convert(Mode::L);
    std::string payload = smallest_classic_payload(image, quality, preset);

    const long long px = pixels(source);
    std::optional<std::vector<std::pair<long, std::vector<int>>>> colours;
    if (px <= kPngMaxPixels)
        colours = source.getcolors(256);
    std::optional<Image> exact;
    if (colours && !colours->empty())
        exact = exact_palette(source, *colours);
    if (exact) {
        std::vector<int> levels = px <= kPngDeflateMaxPixels ? std::vector<int>{0, 9} : std::vector<int>{0};
        for (int level : levels) {
            std::string native = img::save_png(*exact, level);
            if (stored(native, preset) < stored(payload, preset))
                payload = native;
        }
    }

    if (!params.compact)
        return payload;
    std::string best = compact_payload(image, payload, preset);
    if (psdi::payload_kind(payload) == "png") {
        std::string jpeg = compact_payload(image, smallest_jpeg(image, quality, preset), preset);
        if (stored(jpeg, preset) < stored(best, preset))
            best = jpeg;
    }
    return best;
}

// For the page-image encoders in compress.cpp.
img::Image image_of_raster(const mu::Raster &raster) { return raster_image(raster); }

} // namespace pdft::engine
