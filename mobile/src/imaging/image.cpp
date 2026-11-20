// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

#include "image.h"

#include "libimaging.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <regex>


namespace pdft::img {

// --- modes ---------------------------------------------------------------------------

namespace {

ModeID to_id(Mode mode) {
    switch (mode) {
    case Mode::One: return IMAGING_MODE_1;
    case Mode::L: return IMAGING_MODE_L;
    case Mode::LA: return IMAGING_MODE_LA;
    case Mode::La: return IMAGING_MODE_La;
    case Mode::P: return IMAGING_MODE_P;
    case Mode::PA: return IMAGING_MODE_PA;
    case Mode::RGB: return IMAGING_MODE_RGB;
    case Mode::RGBA: return IMAGING_MODE_RGBA;
    case Mode::RGBa: return IMAGING_MODE_RGBa;
    case Mode::RGBX: return IMAGING_MODE_RGBX;
    case Mode::CMYK: return IMAGING_MODE_CMYK;
    case Mode::I: return IMAGING_MODE_I;
    case Mode::F: return IMAGING_MODE_F;
    case Mode::I16: return IMAGING_MODE_I_16;
    }
    return IMAGING_MODE_UNKNOWN;
}

Mode from_id(ModeID id) {
    switch (id) {
    case IMAGING_MODE_1: return Mode::One;
    case IMAGING_MODE_L: return Mode::L;
    case IMAGING_MODE_LA: return Mode::LA;
    case IMAGING_MODE_La: return Mode::La;
    case IMAGING_MODE_P: return Mode::P;
    case IMAGING_MODE_PA: return Mode::PA;
    case IMAGING_MODE_RGB: return Mode::RGB;
    case IMAGING_MODE_RGBA: return Mode::RGBA;
    case IMAGING_MODE_RGBa: return Mode::RGBa;
    case IMAGING_MODE_RGBX: return Mode::RGBX;
    case IMAGING_MODE_CMYK: return Mode::CMYK;
    case IMAGING_MODE_I: return Mode::I;
    case IMAGING_MODE_F: return Mode::F;
    case IMAGING_MODE_I_16: return Mode::I16;
    default: break;
    }
    throw Error("unsupported image mode");
}

// ImageMode.getmode(mode).basemode
Mode mode_base(Mode mode) {
    switch (mode) {
    case Mode::One:
    case Mode::L:
    case Mode::LA:
    case Mode::La:
    case Mode::I:
    case Mode::F:
    case Mode::I16:
        return Mode::L;
    default:
        return Mode::RGB;
    }
}

Imaging check(Imaging im) {
    if (!im)
        throw Error(pdft_imaging_last_error());
    return im;
}

// Bytes per row of the mode's raw layout (what frombytes reads, tobytes writes).
size_t raw_row(Mode mode, int width) {
    size_t w = static_cast<size_t>(width);
    switch (mode) {
    case Mode::One: return (w + 7) / 8;
    case Mode::L:
    case Mode::P: return w;
    case Mode::LA:
    case Mode::La:
    case Mode::PA:
    case Mode::I16: return 2 * w;
    case Mode::RGB: return 3 * w;
    default: return 4 * w;
    }
}

} // namespace

const char *mode_name(Mode mode) {
    switch (mode) {
    case Mode::One: return "1";
    case Mode::L: return "L";
    case Mode::LA: return "LA";
    case Mode::La: return "La";
    case Mode::P: return "P";
    case Mode::PA: return "PA";
    case Mode::RGB: return "RGB";
    case Mode::RGBA: return "RGBA";
    case Mode::RGBa: return "RGBa";
    case Mode::RGBX: return "RGBX";
    case Mode::CMYK: return "CMYK";
    case Mode::I: return "I";
    case Mode::F: return "F";
    case Mode::I16: return "I;16";
    }
    return "?";
}

int mode_bands(Mode mode) {
    switch (mode) {
    case Mode::LA:
    case Mode::La:
    case Mode::PA: return 2;
    case Mode::RGB: return 3;
    case Mode::RGBA:
    case Mode::RGBa:
    case Mode::RGBX:
    case Mode::CMYK: return 4;
    default: return 1;
    }
}

// --- state ---------------------------------------------------------------------------

struct Image::State {
    Imaging im = nullptr;
    std::unique_ptr<Source> source;
    Mode mode = Mode::L;
    int width = 0, height = 0;
    Info info;

    ~State() {
        if (im)
            ImagingDelete(im);
    }
};

Image Image::adopt(ImagingMemoryInstance *im, Info info) {
    check(im);
    auto state = std::make_shared<State>();
    try {
        state->mode = from_id(im->mode);
    } catch (...) {
        ImagingDelete(im);
        throw;
    }
    state->im = im;
    state->width = im->xsize;
    state->height = im->ysize;
    state->info = std::move(info);
    return Image(std::move(state));
}

Image Image::from_source(std::unique_ptr<Source> source, Info info) {
    auto state = std::make_shared<State>();
    state->mode = source->mode();
    state->width = source->width();
    state->height = source->height();
    state->source = std::move(source);
    state->info = std::move(info);
    return Image(std::move(state));
}

Image Image::derived(ImagingMemoryInstance *im) const { return adopt(im, state_->info); }

Mode Image::mode() const { return state_->mode; }
int Image::width() const { return state_->source ? state_->source->width() : state_->width; }
int Image::height() const { return state_->source ? state_->source->height() : state_->height; }
Info &Image::info() { return state_->info; }
const Info &Image::info() const { return state_->info; }

void Image::load() const {
    if (!state_)
        throw Error("empty image");
    if (state_->im || !state_->source)
        return;
    Imaging im = state_->source->load();
    check(im);
    state_->im = im;
    state_->mode = from_id(im->mode);
    state_->width = im->xsize;
    state_->height = im->ysize;
    state_->source.reset();
}

ImagingMemoryInstance *Image::im() const {
    load();
    return state_->im;
}

// --- construction --------------------------------------------------------------------

Image Image::create(Mode mode, int width, int height) {
    return adopt(check(ImagingNew(to_id(mode), width, height)));
}

Image Image::filled(Mode mode, int width, int height, const std::array<uint8_t, 4> &ink) {
    Imaging im = check(ImagingNewDirty(to_id(mode), width, height));
    if (!ImagingFill(im, ink.data())) {
        ImagingDelete(im);
        throw Error(pdft_imaging_last_error());
    }
    return adopt(im);
}

Image Image::frombytes(Mode mode, int width, int height, const void *data, size_t size) {
    size_t row = raw_row(mode, width);
    if (width <= 0 || height <= 0 || size < row * static_cast<size_t>(height))
        throw Error("not enough image data");
    Imaging im = check(ImagingNewDirty(to_id(mode), width, height));
    const auto *in = static_cast<const uint8_t *>(data);
    for (int y = 0; y < height; ++y, in += row) {
        auto *out = reinterpret_cast<uint8_t *>(im->image[y]);
        switch (mode) {
        case Mode::One:
            for (int x = 0; x < width; ++x)
                out[x] = (in[x >> 3] & (0x80 >> (x & 7))) ? 255 : 0;
            break;
        case Mode::RGB:
            for (int x = 0; x < width; ++x) {
                out[4 * x] = in[3 * x];
                out[4 * x + 1] = in[3 * x + 1];
                out[4 * x + 2] = in[3 * x + 2];
                out[4 * x + 3] = 255;
            }
            break;
        case Mode::LA:
        case Mode::La:
        case Mode::PA:
            for (int x = 0; x < width; ++x) {
                out[4 * x] = out[4 * x + 1] = out[4 * x + 2] = in[2 * x];
                out[4 * x + 3] = in[2 * x + 1];
            }
            break;
        default:
            std::memcpy(out, in, row);
            break;
        }
    }
    return adopt(im);
}

std::string Image::tobytes() const {
    Imaging in = im();
    Mode m = mode();
    size_t row = raw_row(m, in->xsize);
    std::string out(row * static_cast<size_t>(in->ysize), '\0');
    auto *o = reinterpret_cast<uint8_t *>(out.data());
    for (int y = 0; y < in->ysize; ++y, o += row) {
        const auto *p = reinterpret_cast<const uint8_t *>(in->image[y]);
        switch (m) {
        case Mode::One:
            std::memset(o, 0, row);
            for (int x = 0; x < in->xsize; ++x)
                if (p[x])
                    o[x >> 3] |= static_cast<uint8_t>(0x80 >> (x & 7));
            break;
        case Mode::RGB:
            for (int x = 0; x < in->xsize; ++x) {
                o[3 * x] = p[4 * x];
                o[3 * x + 1] = p[4 * x + 1];
                o[3 * x + 2] = p[4 * x + 2];
            }
            break;
        case Mode::LA:
        case Mode::La:
        case Mode::PA:
            for (int x = 0; x < in->xsize; ++x) {
                o[2 * x] = p[4 * x];
                o[2 * x + 1] = p[4 * x + 3];
            }
            break;
        default:
            std::memcpy(o, p, row);
            break;
        }
    }
    return out;
}

// --- conversions ---------------------------------------------------------------------

Image Image::copy() const { return derived(check(ImagingCopy(im()))); }

Image Image::convert(Mode target) const {
    load();
    Mode source = mode();
    if (target == source)
        return copy();
    Imaging in = im();
    bool delete_trns = false;
    const Transparency &t = state_->info.transparency;
    if (t.kind != Transparency::None && source == Mode::P &&
        (target == Mode::LA || target == Mode::PA || target == Mode::RGBA)) {
        // putpalettealphas / putpalettealpha, on the image's own palette as
        // Pillow does it.
        if (!in->palette)
            throw Error("image has no palette");
        if (t.kind == Transparency::Bytes) {
            if (t.bytes.size() > 256)
                throw Error("illegal palette size");
            in->palette->mode = IMAGING_MODE_RGBA;
            for (size_t i = 0; i < t.bytes.size(); ++i)
                in->palette->palette[i * 4 + 3] = static_cast<uint8_t>(t.bytes[i]);
        } else if (t.kind == Transparency::Int) {
            if (t.value < 0 || t.value >= 256)
                throw Error("illegal palette index");
            in->palette->mode = IMAGING_MODE_RGBA;
            in->palette->palette[t.value * 4 + 3] = 0;
        } else {
            throw Error("Transparency for P mode should be bytes or int");
        }
        delete_trns = true;
    }
    // Dither.FLOYDSTEINBERG, Pillow's default; it matters only towards "P"
    // and "1".
    Imaging out = ImagingConvert(nullptr, in, to_id(target), nullptr, 3);
    if (!out) {
        // "normalize source image and try again"
        Mode base = mode_base(source);
        if (base == source)
            throw Error(pdft_imaging_last_error());
        Imaging tmp = check(ImagingConvert(nullptr, in, to_id(base), nullptr, 0));
        out = ImagingConvert(nullptr, tmp, to_id(target), nullptr, 3);
        ImagingDelete(tmp);
        check(out);
    }
    Image result = derived(out);
    // The transparency of an L, RGB or P image survives a conversion between
    // those modes in Pillow, converted; nothing the engine converts carries
    // one into a writer, so it is dropped rather than converted.
    if (delete_trns || result.mode() != source)
        result.info().transparency = Transparency{};
    return result;
}

namespace {

// Image._get_safe_box
std::array<int, 4> safe_box(int image_w, int image_h, int w, int h, Filter filter, const std::array<double, 4> &box) {
    double support = 0;
    switch (filter) {
    case Filter::Box: support = 0.5; break;
    case Filter::Bilinear:
    case Filter::Hamming: support = 1.0; break;
    case Filter::Bicubic: support = 2.0; break;
    case Filter::Lanczos: support = 3.0; break;
    case Filter::Nearest: support = 0.5; break;
    }
    double filter_support = support - 0.5;
    double scale_x = (box[2] - box[0]) / w;
    double scale_y = (box[3] - box[1]) / h;
    double support_x = filter_support * scale_x;
    double support_y = filter_support * scale_y;
    return {std::max(0, static_cast<int>(std::trunc(box[0] - support_x))),
            std::max(0, static_cast<int>(std::trunc(box[1] - support_y))),
            std::min(image_w, static_cast<int>(std::ceil(box[2] + support_x))),
            std::min(image_h, static_cast<int>(std::ceil(box[3] + support_y)))};
}

// _imaging.c _resize
Imaging core_resize(Imaging in, int w, int h, Filter filter, const std::array<double, 4> &b) {
    float box[4] = {static_cast<float>(b[0]), static_cast<float>(b[1]), static_cast<float>(b[2]),
                    static_cast<float>(b[3])};
    if (w < 1 || h < 1)
        throw Error("height and width must be > 0");
    if (box[0] < 0 || box[1] < 0)
        throw Error("box offset can't be negative");
    if (box[2] > in->xsize || box[3] > in->ysize)
        throw Error("box can't exceed original image size");
    if (box[2] - box[0] < 0 || box[3] - box[1] < 0)
        throw Error("box can't be empty");
    if (box[0] - static_cast<float>(static_cast<int>(box[0])) == 0 && box[2] - box[0] == static_cast<float>(w) &&
        box[1] - static_cast<float>(static_cast<int>(box[1])) == 0 && box[3] - box[1] == static_cast<float>(h))
        return check(ImagingCrop(in, static_cast<int>(box[0]), static_cast<int>(box[1]), static_cast<int>(box[2]),
                                 static_cast<int>(box[3])));
    if (filter == Filter::Nearest)
        throw Error("nearest-neighbour resizing is not implemented");
    return check(ImagingResample(in, w, h, static_cast<int>(filter), box));
}

} // namespace

Image Image::resize(int w, int h, Filter filter, std::optional<std::array<double, 4>> box,
                    std::optional<double> reducing_gap) const {
    if (reducing_gap && *reducing_gap < 1.0)
        throw Error("reducing_gap must be 1.0 or greater");
    const std::array<double, 4> full = {0, 0, static_cast<double>(width()), static_cast<double>(height())};
    std::array<double, 4> b = box ? *box : full;
    if (width() == w && height() == h && b == full)
        return copy();
    Mode m = mode();
    if (m == Mode::One || m == Mode::P)
        filter = Filter::Nearest;
    if ((m == Mode::LA || m == Mode::RGBA) && filter != Filter::Nearest) {
        Image premultiplied = convert(m == Mode::LA ? Mode::La : Mode::RGBa);
        return premultiplied.resize(w, h, filter, b, reducing_gap).convert(m);
    }
    load();
    Image self = *this;
    if (reducing_gap && filter != Filter::Nearest) {
        int factor_x = static_cast<int>(std::trunc((b[2] - b[0]) / w / *reducing_gap));
        int factor_y = static_cast<int>(std::trunc((b[3] - b[1]) / h / *reducing_gap));
        if (!factor_x)
            factor_x = 1;
        if (!factor_y)
            factor_y = 1;
        if (factor_x > 1 || factor_y > 1) {
            std::array<int, 4> reduce_box = safe_box(width(), height(), w, h, filter, b);
            self = self.reduce(factor_x, factor_y, reduce_box);
            b = {(b[0] - reduce_box[0]) / factor_x, (b[1] - reduce_box[1]) / factor_y,
                 (b[2] - reduce_box[0]) / factor_x, (b[3] - reduce_box[1]) / factor_y};
        }
    }
    return self.derived(core_resize(self.im(), w, h, filter, b));
}

Image Image::reduce(int factor_x, int factor_y, std::optional<std::array<int, 4>> box) const {
    if (factor_x == 1 && factor_y == 1)
        return box ? crop((*box)[0], (*box)[1], (*box)[2], (*box)[3]) : copy();
    std::array<int, 4> b = box ? *box : std::array<int, 4>{0, 0, width(), height()};
    Mode m = mode();
    if (m == Mode::LA || m == Mode::RGBA) {
        Image premultiplied = convert(m == Mode::LA ? Mode::La : Mode::RGBa);
        return premultiplied.reduce(factor_x, factor_y, b).convert(m);
    }
    Imaging in = im();
    if (factor_x < 1 || factor_y < 1)
        throw Error("scale must be > 0");
    if (b[0] < 0 || b[1] < 0)
        throw Error("box offset can't be negative");
    if (b[2] > in->xsize || b[3] > in->ysize)
        throw Error("box can't exceed original image size");
    if (b[2] <= b[0] || b[3] <= b[1])
        throw Error("box can't be empty");
    int cbox[4] = {b[0], b[1], b[2] - b[0], b[3] - b[1]};
    return derived(check(ImagingReduce(in, factor_x, factor_y, cbox)));
}

Image Image::crop(int x0, int y0, int x1, int y1) const {
    if (x1 < x0)
        throw Error("Coordinate 'right' is less than 'left'");
    if (y1 < y0)
        throw Error("Coordinate 'lower' is less than 'upper'");
    return derived(check(ImagingCrop(im(), x0, y0, x1, y1)));
}

void Image::thumbnail(double size_w, double size_h, Filter filter, std::optional<double> reducing_gap) {
    long x = static_cast<long>(std::floor(size_w));
    long y = static_cast<long>(std::floor(size_h));
    int W = width(), H = height();
    if (x >= W && y >= H)
        return;
    double aspect = static_cast<double>(W) / H;
    auto round_aspect = [](double number, auto key) {
        long lo = static_cast<long>(std::floor(number));
        long hi = static_cast<long>(std::ceil(number));
        long best = key(hi) < key(lo) ? hi : lo;  // min() keeps the first of equals
        return std::max<long>(best, 1);
    };
    if (static_cast<double>(x) / static_cast<double>(y) >= aspect) {
        double yd = static_cast<double>(y);
        x = round_aspect(yd * aspect, [&](long n) { return std::fabs(aspect - static_cast<double>(n) / yd); });
    } else {
        double xd = static_cast<double>(x);
        y = round_aspect(xd / aspect,
                         [&](long n) { return n == 0 ? 0.0 : std::fabs(aspect - xd / static_cast<double>(n)); });
    }
    std::optional<std::array<double, 4>> box;
    if (reducing_gap && state_->source && !state_->im)
        box = state_->source->draft(static_cast<int>(size_w * *reducing_gap),
                                    static_cast<int>(size_h * *reducing_gap));
    if (width() != x || height() != y) {
        Image result = resize(static_cast<int>(x), static_cast<int>(y), filter, box, reducing_gap);
        Imaging out = result.state_->im;
        result.state_->im = nullptr;  // moved into this image
        load();
        ImagingDelete(state_->im);
        state_->im = out;
        state_->mode = from_id(out->mode);
        state_->width = out->xsize;
        state_->height = out->ysize;
    }
}

Image Image::band(int index) const {
    Imaging in = im();
    if (in->bands == 1)
        return copy();
    if (index < 0 || index >= in->bands)
        throw Error("band index out of range");
    int offset = (in->bands == 2 && index == 1) ? 3 : index;
    Imaging out = check(ImagingNewDirty(IMAGING_MODE_L, in->xsize, in->ysize));
    for (int y = 0; y < in->ysize; ++y) {
        const auto *p = reinterpret_cast<const uint8_t *>(in->image[y]) + offset;
        auto *o = reinterpret_cast<uint8_t *>(out->image[y]);
        for (int x = 0; x < in->xsize; ++x, p += 4)
            o[x] = *p;
    }
    return derived(out);
}

std::vector<Image> Image::split() const {
    Imaging in = im();
    std::vector<Image> out;
    if (in->bands == 1) {
        out.push_back(copy());
        return out;
    }
    for (int i = 0; i < in->bands; ++i)
        out.push_back(band(i));
    return out;
}

void Image::paste(const Image &image, const Image *mask) {
    load();
    Image source = image;
    source.load();
    if (mode() != source.mode()) {
        bool adapter = mode() == Mode::RGB &&
                       (source.mode() == Mode::LA || source.mode() == Mode::RGBA || source.mode() == Mode::RGBa);
        if (!adapter)
            source = source.convert(mode());
    }
    Imaging m = nullptr;
    if (mask)
        m = mask->im();
    if (ImagingPaste(state_->im, source.im(), m, 0, 0, source.width(), source.height()) < 0)
        throw Error(pdft_imaging_last_error());
}

Image Image::transpose(Transpose method) const {
    Imaging in = im();
    int w = in->xsize, h = in->ysize;
    bool swap = method == Transpose::Rotate90 || method == Transpose::Rotate270 || method == Transpose::Transposed ||
                method == Transpose::Transverse;
    Imaging out = check(ImagingNewDirty(in->mode, swap ? h : w, swap ? w : h));
    if (in->palette) {
        ImagingPalette duplicate = ImagingPaletteDuplicate(in->palette);
        if (!duplicate) {
            ImagingDelete(out);
            throw Error(pdft_imaging_last_error());
        }
        ImagingPaletteDelete(out->palette);
        out->palette = duplicate;
    }
    int ps = in->pixelsize;
    for (int y = 0; y < h; ++y) {
        const char *row = in->image[y];
        for (int x = 0; x < w; ++x) {
            int ox = x, oy = y;
            switch (method) {
            case Transpose::FlipLeftRight: ox = w - 1 - x; break;
            case Transpose::FlipTopBottom: oy = h - 1 - y; break;
            case Transpose::Rotate90: ox = y; oy = w - 1 - x; break;
            case Transpose::Rotate180: ox = w - 1 - x; oy = h - 1 - y; break;
            case Transpose::Rotate270: ox = h - 1 - y; oy = x; break;
            case Transpose::Transposed: ox = y; oy = x; break;
            case Transpose::Transverse: ox = h - 1 - y; oy = w - 1 - x; break;
            }
            std::memcpy(out->image[oy] + static_cast<size_t>(ox) * ps, row + static_cast<size_t>(x) * ps,
                        static_cast<size_t>(ps));
        }
    }
    return derived(out);
}

// --- measurements --------------------------------------------------------------------

std::vector<long> Image::histogram() const {
    Imaging in = im();
    if (in->type != IMAGING_TYPE_UINT8)
        throw Error("histogram of a 32-bit image needs its extrema");
    ImagingHistogram h = ImagingGetHistogram(in, nullptr, nullptr);
    if (!h)
        throw Error(pdft_imaging_last_error());
    std::vector<long> out(h->histogram, h->histogram + 256 * h->bands);
    ImagingHistogramDelete(h);
    return out;
}

std::vector<int> Image::pixel(int x, int y) const {
    Imaging in = im();
    if (in->type != IMAGING_TYPE_UINT8)
        throw Error("pixel of a 32-bit image");
    const auto *p = reinterpret_cast<const uint8_t *>(in->image[y]) + static_cast<size_t>(x) * in->pixelsize;
    if (in->bands == 1)
        return {p[0]};
    if (in->bands == 2)
        return {p[0], p[3]};
    std::vector<int> out;
    for (int i = 0; i < in->bands; ++i)
        out.push_back(p[i]);
    return out;
}

std::optional<std::vector<std::pair<long, std::vector<int>>>> Image::getcolors(int maxcolors) const {
    Mode m = mode();
    std::vector<std::pair<long, std::vector<int>>> out;
    if (m == Mode::One || m == Mode::L || m == Mode::P) {
        std::vector<long> h = histogram();
        for (int i = 0; i < 256; ++i)
            if (h[static_cast<size_t>(i)])
                out.push_back({h[static_cast<size_t>(i)], {i}});
        if (static_cast<int>(out.size()) > maxcolors)
            return std::nullopt;
        return out;
    }
    int colors = 0;
    ImagingColorItem *items = ImagingGetColors(im(), maxcolors, &colors);
    if (!items)
        throw Error(pdft_imaging_last_error());
    if (colors > maxcolors) {
        std::free(items);
        return std::nullopt;
    }
    for (int i = 0; i < colors; ++i)
        out.push_back({static_cast<long>(items[i].count), pixel(items[i].x, items[i].y)});
    std::free(items);
    return out;
}

Image Image::quantize(int colors, int kmeans) const {
    if (kmeans < 0)
        throw Error("kmeans must not be negative");
    if (mode() == Mode::RGBA)
        throw Error("Fast Octree (method == 2) and libimagequant (method == 3) are the only valid "
                    "methods for quantizing RGBA images");
    // Quantize.MEDIANCUT
    return derived(check(ImagingQuantize(im(), colors, 0, kmeans)));
}

Image Image::quantize_to(const std::vector<uint8_t> &palette_rgb) const {
    if (mode() != Mode::RGB && mode() != Mode::L)
        throw Error("only RGB or L mode images can be quantized to a palette");
    if (palette_rgb.size() > 768)
        throw Error("invalid palette size");
    // Image.new("P", (1, 1)).putpalette(palette): an RGB palette of
    // len // 3 entries.
    ImagingPalette palette = ImagingPaletteNew(IMAGING_MODE_RGB);
    if (!palette)
        throw Error(pdft_imaging_last_error());
    palette->size = static_cast<int>(palette_rgb.size() / 3);
    for (int i = 0; i < palette->size; ++i) {
        palette->palette[i * 4] = palette_rgb[static_cast<size_t>(3 * i)];
        palette->palette[i * 4 + 1] = palette_rgb[static_cast<size_t>(3 * i + 1)];
        palette->palette[i * 4 + 2] = palette_rgb[static_cast<size_t>(3 * i + 2)];
        palette->palette[i * 4 + 3] = 255;
    }
    Imaging out = ImagingConvert(nullptr, im(), IMAGING_MODE_P, palette, 0);
    ImagingPaletteDelete(palette);
    Image result = derived(check(out));
    result.info().transparency = Transparency{};
    return result;
}

std::optional<std::array<int, 4>> Image::getbbox() const {
    int bbox[4];
    if (!ImagingGetBBox(im(), bbox, 1))
        return std::nullopt;
    return std::array<int, 4>{bbox[0], bbox[1], bbox[2], bbox[3]};
}

std::vector<uint8_t> Image::palette_rgb() const {
    Imaging in = im();
    std::vector<uint8_t> out;
    if (!in->palette)
        return out;
    for (int i = 0; i < in->palette->size; ++i)
        for (int k = 0; k < 3; ++k)
            out.push_back(in->palette->palette[i * 4 + k]);
    return out;
}

Image difference(const Image &a, const Image &b) {
    Imaging out = ImagingChopDifference(a.im(), b.im());
    if (!out)
        throw Error(pdft_imaging_last_error());
    return Image::adopt(out, a.info());
}

// --- EXIF orientation ----------------------------------------------------------------

namespace {

uint32_t read_u16(const std::string &d, size_t at, bool little) {
    auto b0 = static_cast<uint8_t>(d[at]), b1 = static_cast<uint8_t>(d[at + 1]);
    return little ? (b0 | (b1 << 8)) : ((b0 << 8) | b1);
}

uint32_t read_u32(const std::string &d, size_t at, bool little) {
    uint32_t v = 0;
    for (int i = 0; i < 4; ++i) {
        uint32_t byte = static_cast<uint8_t>(d[at + static_cast<size_t>(little ? 3 - i : i)]);
        v = (v << 8) | byte;
    }
    return v;
}

// The Orientation entry of IFD0: its value when it is one integer, 0 when it
// is something else (Pillow then finds no transposition), -1 when absent.
int ifd0_orientation(std::string data) {
    while (data.compare(0, 6, std::string("Exif\0\0", 6)) == 0)
        data.erase(0, 6);
    if (data.size() < 8)
        return -1;
    bool little;
    if (data.compare(0, 4, std::string("II*\0", 4)) == 0)
        little = true;
    else if (data.compare(0, 4, std::string("MM\0*", 4)) == 0)
        little = false;
    else
        return -1;
    size_t ifd = read_u32(data, 4, little);
    if (ifd + 2 > data.size())
        return -1;
    uint32_t count = read_u16(data, ifd, little);
    for (uint32_t i = 0; i < count; ++i) {
        size_t entry = ifd + 2 + 12 * static_cast<size_t>(i);
        if (entry + 12 > data.size())
            break;
        if (read_u16(data, entry, little) != 0x0112)
            continue;
        uint32_t type = read_u16(data, entry + 2, little);
        uint32_t n = read_u32(data, entry + 4, little);
        if (n != 1)
            return 0;
        switch (type) {
        case 1:  // BYTE
        case 7:  // UNDEFINED
            return static_cast<uint8_t>(data[entry + 8]);
        case 3:  // SHORT
            return static_cast<int>(read_u16(data, entry + 8, little));
        case 8:  // SSHORT
            return static_cast<int16_t>(read_u16(data, entry + 8, little));
        case 4:  // LONG
        case 9:  // SLONG
            return static_cast<int>(read_u32(data, entry + 8, little));
        default:
            return 0;
        }
    }
    return -1;
}

} // namespace

int exif_orientation(const Info &info) {
    if (info.exif) {
        int v = ifd0_orientation(*info.exif);
        if (v >= 0)
            return v;
    }
    if (info.xmp) {
        static const std::regex pattern(R"(tiff:Orientation(="|>)([0-9]))");
        std::smatch match;
        if (std::regex_search(*info.xmp, match, pattern))
            return match[2].str()[0] - '0';
    }
    return 1;
}

Image exif_transpose(const Image &image) {
    image.load();
    switch (exif_orientation(image.info())) {
    case 2: return image.transpose(Transpose::FlipLeftRight);
    case 3: return image.transpose(Transpose::Rotate180);
    case 4: return image.transpose(Transpose::FlipTopBottom);
    case 5: return image.transpose(Transpose::Transposed);
    case 6: return image.transpose(Transpose::Rotate270);
    case 7: return image.transpose(Transpose::Transverse);
    case 8: return image.transpose(Transpose::Rotate90);
    default: return image.copy();
    }
}

} // namespace pdft::img
