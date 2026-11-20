// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// Images as the desktop engine handles them through Pillow.
//
// The engine decides what to keep from what Pillow computes: a resampled
// thumbnail, a palette, a difference histogram. This layer computes the same
// things with Pillow's own C code (libImaging, vendored under pillow/), and
// reproduces the Python around it: which mode a conversion goes through,
// what a thumbnail asks of the JPEG decoder, which entries of `info` an
// image passes on to the images made from it.
#pragma once

#include <array>
#include <cstdint>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

struct ImagingMemoryInstance;

namespace pdft::img {

class Error : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

// The modes the engine meets. I16 is Pillow's "I;16" (little-endian).
enum class Mode { One, L, LA, La, P, PA, RGB, RGBA, RGBa, RGBX, CMYK, I, F, I16 };

const char *mode_name(Mode mode);
int mode_bands(Mode mode);

// Pillow's Image.Resampling values.
enum class Filter { Nearest = 0, Lanczos = 1, Bilinear = 2, Bicubic = 3, Box = 4, Hamming = 5 };

// Pillow's Image.Transpose values.
enum class Transpose {
    FlipLeftRight = 0,
    FlipTopBottom = 1,
    Rotate90 = 2,
    Rotate180 = 3,
    Rotate270 = 4,
    Transposed = 5,
    Transverse = 6,
};

// info["transparency"]: a palette index or grey level, an RGB triple, or one
// alpha byte per palette entry.
struct Transparency {
    enum Kind { None, Int, Triple, Bytes } kind = None;
    int value = 0;
    std::array<int, 3> rgb{};
    std::string bytes;
};

// The entries of Pillow's `info` that the writers read back.
struct Info {
    std::optional<std::string> icc_profile;  // PNG and AVIF writers embed it
    std::optional<std::string> comment;      // the JPEG writer carries it over
    std::optional<std::string> exif;         // orientation
    std::optional<std::string> xmp;          // orientation, when EXIF has none
    Transparency transparency;
};

// A file whose header is read and whose pixels are not, yet: what Pillow's
// draft() works on (JPEG).
class Source {
public:
    virtual ~Source() = default;
    virtual Mode mode() const = 0;
    virtual int width() const = 0;
    virtual int height() const = 0;
    // Image.draft(None, (w, h)): the box of the original image within the
    // reduced one, or nullopt where the format has no draft mode.
    virtual std::optional<std::array<double, 4>> draft(int w, int h) {
        (void)w;
        (void)h;
        return std::nullopt;
    }
    // Decodes; throws Error on damage.
    virtual ImagingMemoryInstance *load() = 0;
};

class Image {
public:
    Image() = default;

    // Image.new(mode, size): zero-filled.
    static Image create(Mode mode, int width, int height);
    // Image.new(mode, size, colour), the colour as the pixel's four bytes.
    static Image filled(Mode mode, int width, int height, const std::array<uint8_t, 4> &ink);
    // Image.frombytes(mode, size, data): rows packed in the mode's raw layout.
    static Image frombytes(Mode mode, int width, int height, const void *data, size_t size);
    // Takes ownership of a libImaging image.
    static Image adopt(ImagingMemoryInstance *im, Info info = {});
    static Image from_source(std::unique_ptr<Source> source, Info info);

    bool empty() const { return !state_; }
    Mode mode() const;
    int width() const;
    int height() const;
    Info &info();
    const Info &info() const;

    void load() const;
    ImagingMemoryInstance *im() const;  // loads

    // The operations below follow PIL.Image.Image's methods of the same name.
    Image copy() const;
    Image convert(Mode mode) const;
    Image resize(int width, int height, Filter filter = Filter::Bicubic,
                 std::optional<std::array<double, 4>> box = std::nullopt,
                 std::optional<double> reducing_gap = std::nullopt) const;
    Image reduce(int factor_x, int factor_y, std::optional<std::array<int, 4>> box = std::nullopt) const;
    Image crop(int x0, int y0, int x1, int y1) const;
    void thumbnail(double width, double height, Filter filter = Filter::Bicubic,
                   std::optional<double> reducing_gap = 2.0);
    std::vector<Image> split() const;
    Image band(int index) const;
    // paste(im, (0, 0), mask): mask may be null.
    void paste(const Image &image, const Image *mask);
    Image transpose(Transpose method) const;
    std::string tobytes() const;
    // getcolors(maxcolors): (count, pixel bands); nullopt past maxcolors.
    std::optional<std::vector<std::pair<long, std::vector<int>>>> getcolors(int maxcolors) const;
    std::vector<long> histogram() const;
    // quantize(colors, method=MEDIANCUT, kmeans)
    Image quantize(int colors, int kmeans) const;
    // quantize(palette=<P image holding `palette`>, dither=NONE)
    Image quantize_to(const std::vector<uint8_t> &palette_rgb) const;
    std::optional<std::array<int, 4>> getbbox() const;
    // getpalette(): the palette's entries, three bytes each.
    std::vector<uint8_t> palette_rgb() const;
    // The pixel at (x, y), as getpixel returns it for 8-bit modes.
    std::vector<int> pixel(int x, int y) const;

private:
    struct State;
    std::shared_ptr<State> state_;
    explicit Image(std::shared_ptr<State> state) : state_(std::move(state)) {}
    Image derived(ImagingMemoryInstance *im) const;  // _new(im): info copied
};

// ImageChops.difference(a, b)
Image difference(const Image &a, const Image &b);

// ImageOps.exif_transpose(image): the orientation EXIF (or XMP) records.
Image exif_transpose(const Image &image);
int exif_orientation(const Info &info);

} // namespace pdft::img
