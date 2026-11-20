// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// What the engine's translation units share. The functions keep the names
// of the desktop engine's (engine.py), without the leading underscore, so
// that each can be read against its original.
#pragma once

#include "engine.h"
#include "fonts.h"
#include "format.h"
#include "geometry.h"
#include "json.h"
#include "mupdf_facade.h"
#include "presets.h"

#include <array>
#include <atomic>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace pdft::img {
class Image;
}

namespace pdft::engine {

using json::Value;
using Box = std::array<double, 4>;

// --- settings ---------------------------------------------------------------------------

struct Params {
    Preset preset;
    bool compact = false;
};

// --- numbers as the manifest stores them ------------------------------------------------

// _quantize: an int when no decimal is needed, else a float.
Value quantize(double value, int precision);
Value quantize_box(const Box &box, int precision);
inline Value quantize_box(const Rect &r, int precision) { return quantize_box(Box{r.x0, r.y0, r.x1, r.y1}, precision); }
// A JSON number as a double (ints are exact).
inline double num(const Value &v) { return v.as_double(); }
Box box_of(const Value &v);
// min() and max() as Python computes them on two numbers: the first wins a
// tie, keeping its type (int or float).
const Value &vmin(const Value &a, const Value &b);
const Value &vmax(const Value &a, const Value &b);

// _color_to_hex: "#rrggbb", or "" for anything but three components.
std::string color_to_hex(const std::optional<std::vector<double>> &color);

// _covered: whether a box lies inside an already-rasterised region.
bool covered(const Rect &box, const std::vector<Rect> &regions);

// --- vector paths -------------------------------------------------------------------------

// A path the page shows, with the rectangle it is painted through (null for
// none) -- _visible_drawings.
struct VisibleDrawing {
    const mu::Drawing *path;
    std::optional<Box> clip;
};

struct PageDrawings {
    std::vector<mu::Drawing> paths;   // page.get_drawings()
    std::vector<VisibleDrawing> visible;
};
void visible_drawings(mu::Page &page, PageDrawings &out);

std::vector<Rect> rects_from_path_items(const std::vector<mu::PathItem> &items);
std::vector<Box> axis_lines_from_path_items(const std::vector<mu::PathItem> &items, double tolerance = 0.6);
bool path_is_only_axis_lines(const std::vector<mu::PathItem> &items, double tolerance = 0.6);
bool path_is_artwork(const std::vector<mu::PathItem> &items);

bool spills(const Box &box, const Box &clip);
Box grown(const Box &box, double pad);
std::optional<Rect> clip_box(const Box &box, const Box &clip);
std::optional<Box> clip_segment(double x0, double y0, double x1, double y1, const Box &clip);
std::vector<Box> outline_sides(const Box &rect);

Value rule_entry(double x0, double y0, double x1, double y1, const std::string &colour, double width,
                 int precision);
std::optional<double> stroke_width(double width);
std::vector<Value> compact_drawings(std::vector<Value> entries, const Box &bounds, int precision);

std::vector<Rect> merge_boxes(const std::vector<Rect> &boxes, double pad = 8.0);
std::vector<Rect> artwork_regions(mu::Page &page, const std::vector<VisibleDrawing> &drawings);

// --- text ---------------------------------------------------------------------------------

// Fonts and measurements kept for a whole document (the `faces` dict).
struct Faces {
    explicit Faces(fz_context *ctx) : ctx(ctx), class_fonts(ctx) {}
    fz_context *ctx;
    std::map<int, std::optional<std::string>> programs;    // xref -> real name
    ClassFonts class_fonts;
    struct Base14 {
        std::shared_ptr<Font> face;
        std::map<char32_t, double> advances;
    };
    std::map<std::pair<std::string, std::string>, Base14> base14;
};

std::vector<Rect> unreadable_text_regions(mu::Page &page);

// --- image payloads -----------------------------------------------------------------------

std::optional<std::string> render_region(mu::Page &page, const Rect &region, const Params &params);
struct PlacedImage {
    std::string payload;
    int width = 0, height = 0;
};
std::optional<PlacedImage> extract_placed_image(mu::Document &doc, int xref, const Params &params,
                                                std::pair<double, double> placed_pt);
std::string encode_page(const img::Image &image, const Params &params);
// Image.frombytes("RGB", ...) over a rendered page or region.
img::Image image_of_raster(const mu::Raster &raster);

// --- progress -----------------------------------------------------------------------------

constexpr int PROGRESS_SCALE = 1000;

class Progress {
public:
    Progress(ProgressFn cb, double total_units, const std::atomic<bool> *cancel);
    void advance(double units, const std::string &message, bool force = false);
    void phase(const std::string &message);
    void finish(const std::string &message);

private:
    void emit(const std::string &message, bool force);
    ProgressFn cb_;
    double total_;
    double done_ = 0;
    double last_time_ = -1e9;
    const std::atomic<bool> *cancel_;
};
void report(const ProgressFn &cb, int step, int total, const std::string &message);

// --- the page ---------------------------------------------------------------------------------

Value extract_page(mu::Page &page, int page_idx, const psdi::ImageMap &images, int precision,
                   const std::vector<Rect> &artwork, Faces &faces, const PageDrawings &drawings);

} // namespace pdft::engine
