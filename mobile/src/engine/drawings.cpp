// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// Vector paths: rectangles and rules recovered from them, the clips they are
// painted through, the compaction of a page's drawing list, and the regions
// that must be rasterised (engine.py, "rectangles" to "_artwork_regions").

#include "internal.h"

#include "pyutil.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <set>
#include <unordered_map>

namespace pdft::engine {

// --- numbers ------------------------------------------------------------------------------

Value quantize(double value, int precision) {
    if (precision <= 0)
        return Value(py::round_int(value));
    double rounded = py::round_to(value, precision);
    if (py::is_integer(rounded))
        return Value(static_cast<long long>(rounded));
    return Value(rounded);
}

Value quantize_box(const Box &box, int precision) {
    json::Array out;
    for (double v : box)
        out.push_back(quantize(v, precision));
    return Value(out);
}

Box box_of(const Value &v) { return {num(v[0]), num(v[1]), num(v[2]), num(v[3])}; }

const Value &vmin(const Value &a, const Value &b) { return num(b) < num(a) ? b : a; }
const Value &vmax(const Value &a, const Value &b) { return num(b) > num(a) ? b : a; }

std::string color_to_hex(const std::optional<std::vector<double>> &color) {
    if (!color || color->size() != 3)
        return "";
    char buf[8];
    int c[3];
    for (int i = 0; i < 3; ++i)
        c[i] = static_cast<int>(std::max(0LL, std::min(255LL, py::round_int((*color)[static_cast<size_t>(i)] * 255))));
    std::snprintf(buf, sizeof buf, "#%02x%02x%02x", c[0], c[1], c[2]);
    return buf;
}

bool covered(const Rect &box, const std::vector<Rect> &regions) {
    if (regions.empty())
        return false;
    if (box.is_empty())
        return false;
    for (const Rect &region : regions) {
        Rect overlap = box & region;
        if (!overlap.is_empty() && overlap.get_area() >= 0.7 * box.get_area())
            return true;
    }
    return false;
}

// --- rectangles in paths ----------------------------------------------------------------------

namespace {

constexpr double kShortSideMaxSlope = 0.1;
constexpr double kMinSide = 1e-3;

using Pt = std::pair<double, double>;

// _side_direction: 1 horizontal, 0 vertical, -1 neither.
int side_direction(double dx, double dy, double tolerance) {
    if (dy <= tolerance && dx > tolerance)
        return 1;
    if (dx <= tolerance && dy > tolerance)
        return 0;
    if (kMinSide < dx && dx <= tolerance && dy <= kShortSideMaxSlope * dx)
        return 1;
    if (kMinSide < dy && dy <= tolerance && dx <= kShortSideMaxSlope * dy)
        return 0;
    return -1;
}

bool is_axis_aligned_rectangle(const std::vector<Pt> &points, double tolerance = 0.6) {
    if (points.size() < 4)
        return false;
    std::vector<Pt> pts = points;
    double min_x = pts[0].first, max_x = pts[0].first, min_y = pts[0].second, max_y = pts[0].second;
    for (const auto &p : pts) {
        min_x = std::min(min_x, p.first);
        max_x = std::max(max_x, p.first);
        min_y = std::min(min_y, p.second);
        max_y = std::max(max_y, p.second);
    }
    double closing = std::min({tolerance, (max_x - min_x) / 4, (max_y - min_y) / 4});
    if (std::fabs(pts[0].first - pts.back().first) <= closing && std::fabs(pts[0].second - pts.back().second) <= closing)
        pts.pop_back();
    if (pts.size() != 4)
        return false;
    int horizontal[4];
    for (int index = 0; index < 4; ++index) {
        const Pt &a = pts[static_cast<size_t>(index)];
        const Pt &b = pts[static_cast<size_t>((index + 1) % 4)];
        int direction = side_direction(std::fabs(b.first - a.first), std::fabs(b.second - a.second), tolerance);
        if (direction < 0)
            return false;
        horizontal[index] = direction;
    }
    return horizontal[0] != horizontal[1] && horizontal[1] != horizontal[2] && horizontal[2] != horizontal[3];
}

Rect bounding(const std::vector<Pt> &points) {
    double x0 = points[0].first, y0 = points[0].second, x1 = x0, y1 = y0;
    for (const auto &p : points) {
        x0 = std::min(x0, p.first);
        y0 = std::min(y0, p.second);
        x1 = std::max(x1, p.first);
        y1 = std::max(y1, p.second);
    }
    return {x0, y0, x1, y1};
}

std::vector<Pt> quad_corners(const Quad &q) {
    return {{q.ul.x, q.ul.y}, {q.ur.x, q.ur.y}, {q.lr.x, q.lr.y}, {q.ll.x, q.ll.y}};
}

} // namespace

std::vector<Rect> rects_from_path_items(const std::vector<mu::PathItem> &items) {
    std::vector<Rect> rects;
    std::vector<Pt> points;
    auto flush = [&]() {
        if (is_axis_aligned_rectangle(points))
            rects.push_back(bounding(points));
        points.clear();
    };
    for (const auto &item : items) {
        switch (item.kind) {
        case mu::PathItem::RectItem:
            flush();
            rects.push_back(item.rect);
            break;
        case mu::PathItem::QuadItem: {
            flush();
            std::vector<Pt> corners = quad_corners(item.quad);
            if (is_axis_aligned_rectangle(corners))
                rects.push_back(bounding(corners));
            break;
        }
        case mu::PathItem::Line: {
            Pt start{item.p[0].x, item.p[0].y}, end{item.p[1].x, item.p[1].y};
            if (!points.empty() && (std::fabs(points.back().first - start.first) > 0.6 ||
                                    std::fabs(points.back().second - start.second) > 0.6))
                flush();
            if (points.empty())
                points.push_back(start);
            points.push_back(end);
            break;
        }
        default:
            flush();
            break;
        }
    }
    flush();
    return rects;
}

namespace {

// dict keyed by a float coordinate, in insertion order: -0.0 and 0.0 are
// one key, as in Python.
template <typename V>
struct OrderedByNumber {
    std::vector<std::pair<double, V>> items;
    V &operator[](double key) {
        for (auto &item : items)
            if (item.first == key)
                return item.second;
        items.emplace_back(key, V{});
        return items.back().second;
    }
};

std::vector<Box> join_path_segments(const std::vector<Box> &lines) {
    using Spans = std::vector<std::pair<double, double>>;
    OrderedByNumber<Spans> horizontal, vertical;
    std::vector<std::pair<Pt, Pt>> others;
    for (const Box &l : lines) {
        double x0 = l[0], y0 = l[1], x1 = l[2], y1 = l[3];
        if (y0 == y1) {
            horizontal[y0].emplace_back(std::min(x0, x1), std::max(x0, x1));
        } else if (x0 == x1) {
            vertical[x0].emplace_back(std::min(y0, y1), std::max(y0, y1));
        } else {
            Pt a{x0, y0}, b{x1, y1};
            std::pair<Pt, Pt> segment = b < a ? std::make_pair(b, a) : std::make_pair(a, b);
            if (std::find(others.begin(), others.end(), segment) == others.end())
                others.push_back(segment);
        }
    }
    auto joined = [](Spans spans) {
        std::sort(spans.begin(), spans.end());
        std::vector<std::pair<double, double>> out{spans[0]};
        for (size_t i = 1; i < spans.size(); ++i) {
            if (spans[i].first <= out.back().second)
                out.back().second = std::max(out.back().second, spans[i].second);
            else
                out.push_back(spans[i]);
        }
        return out;
    };
    std::vector<Box> result;
    for (const auto &[a, b] : others)
        result.push_back({a.first, a.second, b.first, b.second});
    for (const auto &[y, spans] : horizontal.items)
        for (const auto &[lo, hi] : joined(spans))
            result.push_back({lo, y, hi, y});
    for (const auto &[x, spans] : vertical.items)
        for (const auto &[lo, hi] : joined(spans))
            result.push_back({x, lo, x, hi});
    return result;
}

} // namespace

std::vector<Box> axis_lines_from_path_items(const std::vector<mu::PathItem> &items, double tolerance) {
    std::vector<Box> lines;
    for (const auto &item : items) {
        if (item.kind != mu::PathItem::Line)
            continue;
        double x0 = item.p[0].x, y0 = item.p[0].y, x1 = item.p[1].x, y1 = item.p[1].y;
        bool horizontal = std::fabs(y1 - y0) <= tolerance && std::fabs(x1 - x0) > tolerance;
        bool vertical = std::fabs(x1 - x0) <= tolerance && std::fabs(y1 - y0) > tolerance;
        if (horizontal || vertical)
            lines.push_back({x0, y0, x1, y1});
    }
    return join_path_segments(lines);
}

bool path_is_only_axis_lines(const std::vector<mu::PathItem> &items, double tolerance) {
    if (items.empty())
        return false;
    for (const auto &item : items)
        if (item.kind != mu::PathItem::Line)
            return false;
    for (const auto &item : items) {
        double dx = std::fabs(item.p[1].x - item.p[0].x);
        double dy = std::fabs(item.p[1].y - item.p[0].y);
        if (!((dy <= tolerance && tolerance < dx) || (dx <= tolerance && tolerance < dy)))
            return false;
    }
    return true;
}

bool path_is_artwork(const std::vector<mu::PathItem> &items) {
    if (items.empty())
        return false;
    for (const auto &item : items)
        if (item.kind == mu::PathItem::Curve)
            return true;
    if (path_is_only_axis_lines(items))
        return false;
    for (const auto &item : items)
        if (item.kind == mu::PathItem::QuadItem && !is_axis_aligned_rectangle(quad_corners(item.quad)))
            return true;
    size_t rects = rects_from_path_items(items).size();
    size_t segments = 0;
    for (const auto &item : items)
        if (item.kind == mu::PathItem::Line)
            ++segments;
    return segments > 0 && rects * 4 < segments;
}

// --- clipping -----------------------------------------------------------------------------

namespace {

constexpr double kClipSlack = 0.5;

// _clips_by_seqno: the scissors still open above each path, intersected.
std::map<long, Box> clips_by_seqno(mu::Page &page) {
    std::map<long, Box> clips;
    struct Open {
        long level;
        std::optional<Box> scissor;
    };
    std::vector<Open> open;
    for (const mu::Drawing &item : page.drawings(true)) {
        long level = item.has_level ? item.level : 0;
        while (!open.empty() && open.back().level >= level)
            open.pop_back();
        if (item.type == "clip") {
            open.push_back({level, item.scissor.tuple()});
            continue;
        }
        if (item.type == "group") {
            open.push_back({level, std::nullopt});
            continue;
        }
        bool any = false;
        Box clip{};
        for (const Open &o : open) {
            if (!o.scissor)
                continue;
            const Box &s = *o.scissor;
            if (!any) {
                clip = s;
                any = true;
            } else {
                clip[0] = std::max(clip[0], s[0]);
                clip[1] = std::max(clip[1], s[1]);
                clip[2] = std::min(clip[2], s[2]);
                clip[3] = std::min(clip[3], s[3]);
            }
        }
        if (any)
            clips.emplace(item.seqno, clip);
    }
    return clips;
}

bool hidden_by(const Box &box, const Box &clip) {
    return clip[0] > clip[2] || clip[1] > clip[3] || box[2] < clip[0] || box[0] > clip[2] || box[3] < clip[1] ||
           box[1] > clip[3];
}

} // namespace

Box grown(const Box &box, double pad) { return {box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad}; }

bool spills(const Box &box, const Box &clip) {
    return box[0] < clip[0] - kClipSlack || box[1] < clip[1] - kClipSlack || box[2] > clip[2] + kClipSlack ||
           box[3] > clip[3] + kClipSlack;
}

std::optional<Rect> clip_box(const Box &box, const Box &clip) {
    double x0 = std::max(box[0], clip[0]), y0 = std::max(box[1], clip[1]);
    double x1 = std::min(box[2], clip[2]), y1 = std::min(box[3], clip[3]);
    if (x0 > x1 || y0 > y1)
        return std::nullopt;
    return Rect(x0, y0, x1, y1);
}

std::optional<Box> clip_segment(double x0, double y0, double x1, double y1, const Box &clip) {
    double dx = x1 - x0, dy = y1 - y0;
    double t0 = 0.0, t1 = 1.0;
    const double pq[4][2] = {{-dx, x0 - clip[0]}, {dx, clip[2] - x0}, {-dy, y0 - clip[1]}, {dy, clip[3] - y0}};
    for (const auto &e : pq) {
        double p = e[0], q = e[1];
        if (p == 0) {
            if (q < 0)
                return std::nullopt;
            continue;
        }
        double t = q / p;
        if (p < 0) {
            if (t > t1)
                return std::nullopt;
            t0 = std::max(t0, t);
        } else {
            if (t < t0)
                return std::nullopt;
            t1 = std::min(t1, t);
        }
    }
    return Box{x0 + t0 * dx, y0 + t0 * dy, x0 + t1 * dx, y0 + t1 * dy};
}

std::vector<Box> outline_sides(const Box &r) {
    double x0 = r[0], y0 = r[1], x1 = r[2], y1 = r[3];
    if (y0 == y1 || x0 == x1)
        return {{x0, y0, x1, y1}};
    return {{x0, y0, x1, y0}, {x1, y0, x1, y1}, {x1, y1, x0, y1}, {x0, y1, x0, y0}};
}

void visible_drawings(mu::Page &page, PageDrawings &out) {
    std::map<long, Box> clips = clips_by_seqno(page);
    out.paths = page.drawings(false);
    out.visible.clear();
    for (const mu::Drawing &path : out.paths) {
        std::optional<Box> clip;
        auto found = clips.find(path.seqno);
        if (found != clips.end())
            clip = found->second;
        if (clip) {
            double pad = path.color ? (path.width ? *path.width : 0.0) / 2 : 0.0;
            if (hidden_by(grown(path.rect.tuple(), pad), *clip))
                continue;
        }
        out.visible.push_back({&path, clip});
    }
}

// --- drawing entries ----------------------------------------------------------------------

namespace {

constexpr double kRuleMaxThickness = 2.0;
constexpr double kDrawGrid = 32.0;
constexpr int kCompactPasses = 4;
const char *const kWhite = "#ffffff";

} // namespace

std::optional<double> stroke_width(double width) {
    if (width <= 0.1)
        return std::nullopt;
    if (width < 0.15)
        return std::max(py::round_to(width, 2), 0.11);
    return py::round_to(width, 1);
}

Value rule_entry(double x0, double y0, double x1, double y1, const std::string &colour, double width,
                 int precision) {
    Box box;
    if (std::fabs(x1 - x0) >= std::fabs(y1 - y0)) {
        double centre = (y0 + y1) / 2;
        box = {std::min(x0, x1), centre, std::max(x0, x1), centre};
    } else {
        double centre = (x0 + x1) / 2;
        box = {centre, std::min(y0, y1), centre, std::max(y0, y1)};
    }
    Value entry = Value::object({{"t", Value("rect")}, {"r", quantize_box(box, precision)}, {"c", Value(colour)}});
    if (auto stroke = stroke_width(width))
        entry["w"] = *stroke;
    return entry;
}

namespace {

enum class Kind { Fill, Rule, Other };

Kind drawing_kind(const Value &entry) {
    const Value *t = entry.find("t");
    if (!t || !t->is_string() || t->as_string() != "rect")
        return Kind::Other;
    const Value &r = entry.at("r");
    double x0 = num(r[0]), y0 = num(r[1]), x1 = num(r[2]), y1 = num(r[3]);
    bool f = entry.contains("f"), c = entry.contains("c");
    if (f && !c)
        return Kind::Fill;
    if (c && !f && (x0 == x1 || y0 == y1))
        return Kind::Rule;
    return Kind::Other;
}

Box drawing_footprint(const Value &entry) {
    const Value &r = entry.at("r");
    double x0 = num(r[0]), y0 = num(r[1]), x1 = num(r[2]), y1 = num(r[3]);
    double lx = std::min(x0, x1), hx = std::max(x0, x1), ly = std::min(y0, y1), hy = std::max(y0, y1);
    const Value *t = entry.find("t");
    bool line = t && t->is_string() && t->as_string() == "line";
    if (entry.contains("c") || line) {
        const Value *w = entry.find("w");
        double pad = w ? std::max(num(*w) / 2, 0.5) : 1.0;
        return {lx - pad, ly - pad, hx + pad, hy + pad};
    }
    return {lx, ly, hx, hy};
}

bool paints_nothing(Kind kind, const Box &b) {
    if (kind == Kind::Fill || kind == Kind::Rule)
        return b[0] == b[2] && b[1] == b[3];
    return false;
}

bool is_thin(Kind kind, const Box &b) {
    if (kind == Kind::Rule)
        return true;
    return std::min(b[2] - b[0], b[3] - b[1]) < kRuleMaxThickness;
}

// _union_box, on the stored values (each keeps its int or float type).
std::optional<Value> union_box(Kind kind, const Value &a, const Value &b) {
    Box A = box_of(a), B = box_of(b);
    bool thin = is_thin(kind, A) || is_thin(kind, B);
    auto joins = [&](double lo_a, double hi_a, double lo_b, double hi_b) {
        if (thin)
            return hi_a == lo_b || hi_b == lo_a;
        return lo_a <= hi_b && lo_b <= hi_a;
    };
    auto make = [](const Value &v0, const Value &v1, const Value &v2, const Value &v3) {
        return Value(json::Array{v0, v1, v2, v3});
    };
    if (kind == Kind::Fill) {
        if (A[1] == B[1] && A[3] == B[3] && joins(A[0], A[2], B[0], B[2]))
            return make(vmin(a[0], b[0]), a[1], vmax(a[2], b[2]), a[3]);
        if (A[0] == B[0] && A[2] == B[2] && joins(A[1], A[3], B[1], B[3]))
            return make(a[0], vmin(a[1], b[1]), a[2], vmax(a[3], b[3]));
        return std::nullopt;
    }
    if (A[1] == A[3] && A[3] == B[1] && B[1] == B[3] && joins(A[0], A[2], B[0], B[2]))
        return make(vmin(a[0], b[0]), a[1], vmax(a[2], b[2]), a[3]);
    if (A[0] == A[2] && A[2] == B[0] && B[0] == B[2] && joins(A[1], A[3], B[1], B[3]))
        return make(a[0], vmin(a[1], b[1]), a[2], vmax(a[3], b[3]));
    return std::nullopt;
}

// Keys of Python tuples holding numbers and strings: numbers by value, so
// that 1 and 1.0, or -0.0 and 0.0, are one key as in a dict.
struct KeyBuilder {
    std::string s;
    KeyBuilder &str(const std::string &v) {
        s += 's';
        s += std::to_string(v.size());
        s += ':';
        s += v;
        return *this;
    }
    KeyBuilder &none() {
        s += 'n';
        return *this;
    }
    KeyBuilder &number(double v) {
        if (v == 0)
            v = 0;  // -0.0
        char buf[40];
        std::snprintf(buf, sizeof buf, "d%a;", v);
        s += buf;
        return *this;
    }
    KeyBuilder &value(const Value *v) {
        if (!v || v->is_null())
            return none();
        if (v->is_string())
            return str(v->as_string());
        if (v->is_number())
            return number(v->as_double());
        return str(json::dumps(*v));
    }
};

struct Style {
    Kind kind;
    std::string key;  // (kind, f, c, w)
};

Style style_of(Kind kind, const Value &entry) {
    KeyBuilder k;
    k.number(static_cast<double>(static_cast<int>(kind)));
    k.value(entry.find("f")).value(entry.find("c")).value(entry.find("w"));
    return {kind, k.s};
}

std::vector<std::string> index_keys(const Style &style, const Box &b) {
    auto key = [&](std::initializer_list<std::pair<char, double>> parts) {
        KeyBuilder k;
        k.str(style.key);
        for (const auto &[tag, v] : parts) {
            if (tag == 0)
                k.number(v);
            else
                k.str(std::string(1, tag));
        }
        return k.s;
    };
    // tags: 'h','v','r'(row),'c'(col),'e'(end),'s'(start); 0 for a number
    if (style.kind == Kind::Rule) {
        if (b[1] == b[3])
            return {key({{'h', 0}, {0, b[1]}, {'e', 0}, {0, b[2]}}), key({{'h', 0}, {0, b[1]}, {'s', 0}, {0, b[0]}})};
        return {key({{'v', 0}, {0, b[0]}, {'e', 0}, {0, b[3]}}), key({{'v', 0}, {0, b[0]}, {'s', 0}, {0, b[1]}})};
    }
    return {
        key({{'r', 0}, {0, b[1]}, {0, b[3]}}),
        key({{'c', 0}, {0, b[0]}, {0, b[2]}}),
        key({{'r', 0}, {0, b[1]}, {0, b[3]}, {'e', 0}, {0, b[2]}}),
        key({{'r', 0}, {0, b[1]}, {0, b[3]}, {'s', 0}, {0, b[0]}}),
        key({{'c', 0}, {0, b[0]}, {0, b[2]}, {'e', 0}, {0, b[3]}}),
        key({{'c', 0}, {0, b[0]}, {0, b[2]}, {'s', 0}, {0, b[1]}}),
    };
}

std::vector<std::string> query_keys(const Style &style, const Box &b) {
    auto key = [&](std::initializer_list<std::pair<char, double>> parts) {
        KeyBuilder k;
        k.str(style.key);
        for (const auto &[tag, v] : parts) {
            if (tag == 0)
                k.number(v);
            else
                k.str(std::string(1, tag));
        }
        return k.s;
    };
    if (style.kind == Kind::Rule) {
        if (b[1] == b[3])
            return {key({{'h', 0}, {0, b[1]}, {'e', 0}, {0, b[0]}}), key({{'h', 0}, {0, b[1]}, {'s', 0}, {0, b[2]}})};
        return {key({{'v', 0}, {0, b[0]}, {'e', 0}, {0, b[1]}}), key({{'v', 0}, {0, b[0]}, {'s', 0}, {0, b[3]}})};
    }
    if (is_thin(style.kind, b))
        return {
            key({{'r', 0}, {0, b[1]}, {0, b[3]}, {'e', 0}, {0, b[0]}}),
            key({{'r', 0}, {0, b[1]}, {0, b[3]}, {'s', 0}, {0, b[2]}}),
            key({{'c', 0}, {0, b[0]}, {0, b[2]}, {'e', 0}, {0, b[1]}}),
            key({{'c', 0}, {0, b[0]}, {0, b[2]}, {'s', 0}, {0, b[3]}}),
        };
    return {key({{'r', 0}, {0, b[1]}, {0, b[3]}}), key({{'c', 0}, {0, b[0]}, {0, b[2]}})};
}

std::string exact_key(const Style &style, const Box &b) {
    KeyBuilder k;
    k.str(style.key);
    for (double v : b)
        k.number(v);
    return k.s;
}

long long cell(double v) { return static_cast<long long>(std::floor(v / kDrawGrid)); }

struct Limits {
    long long x0, y0, x1, y1;
};

template <typename F>
void grid_cells(const Box &f, const std::optional<Limits> &limits, F &&visit) {
    long long gx0 = cell(f[0]), gy0 = cell(f[1]), gx1 = cell(f[2]), gy1 = cell(f[3]);
    if (limits) {
        gx0 = std::max(gx0, limits->x0);
        gy0 = std::max(gy0, limits->y0);
        gx1 = std::min(gx1, limits->x1);
        gy1 = std::min(gy1, limits->y1);
    }
    for (long long cx = gx0; cx <= gx1; ++cx)
        for (long long cy = gy0; cy <= gy1; ++cy)
            visit(cx, cy);
}

struct CellHash {
    size_t operator()(const std::pair<long long, long long> &c) const {
        return std::hash<long long>()(c.first * 1000003LL ^ c.second);
    }
};

std::vector<Value> compact_once(const std::vector<Value> &entries, const std::optional<Box> &bounds) {
    std::vector<std::optional<Value>> out;
    std::vector<Style> styles;
    std::vector<Box> prints;
    std::unordered_map<std::pair<long long, long long>, std::vector<size_t>, CellHash> grid;
    std::unordered_map<std::string, std::vector<size_t>> keys;
    std::unordered_map<std::string, size_t> exact;
    std::optional<Limits> limits;
    if (bounds)
        limits = Limits{cell((*bounds)[0]) - 1, cell((*bounds)[1]) - 1, cell((*bounds)[2]) + 1, cell((*bounds)[3]) + 1};

    auto index_entry = [&](size_t i) {
        grid_cells(prints[i], limits, [&](long long cx, long long cy) { grid[{cx, cy}].push_back(i); });
        Kind kind = styles[i].kind;
        if (kind == Kind::Fill || kind == Kind::Rule) {
            Box box = box_of(out[i]->at("r"));
            for (const auto &key : index_keys(styles[i], box))
                keys[key].push_back(i);
            exact[exact_key(styles[i], box)] = i;
        }
    };

    // Is anything drawn after index `after` (-1: anything) overlapping
    // `footprint`, other than entries of `style`?
    auto painted_after = [&](const Box &footprint, long long after, const Style *style) {
        std::set<size_t> seen;
        bool found = false;
        grid_cells(footprint, limits, [&](long long cx, long long cy) {
            if (found)
                return;
            auto it = grid.find({cx, cy});
            if (it == grid.end())
                return;
            for (size_t j : it->second) {
                if (static_cast<long long>(j) <= after || seen.count(j) || !out[j])
                    continue;
                seen.insert(j);
                if (style && styles[j].key == style->key)
                    continue;
                const Box &o = prints[j];
                if (o[0] <= footprint[2] && footprint[0] <= o[2] && o[1] <= footprint[3] && footprint[1] <= o[3]) {
                    found = true;
                    return;
                }
            }
        });
        return found;
    };

    for (const Value &entry : entries) {
        Kind kind = drawing_kind(entry);
        Value box_value = entry.at("r");
        Box box = box_of(box_value);
        Style style = style_of(kind, entry);
        Box footprint = drawing_footprint(entry);

        if (kind == Kind::Fill || kind == Kind::Rule) {
            if (paints_nothing(kind, box))
                continue;
            if (kind == Kind::Fill && entry.at("f").is_string() && entry.at("f").as_string() == kWhite &&
                !painted_after(footprint, -1, nullptr))
                continue;
            auto previous = exact.find(exact_key(style, box));
            if (previous != exact.end() && out[previous->second] && !is_thin(kind, box)) {
                if (!painted_after(footprint, static_cast<long long>(previous->second), &style))
                    continue;
                out[previous->second].reset();
            }
            std::set<size_t> candidate_set;
            for (const auto &key : query_keys(style, box)) {
                auto it = keys.find(key);
                if (it == keys.end())
                    continue;
                for (size_t i : it->second)
                    if (out[i])
                        candidate_set.insert(i);
            }
            bool merged = false;
            for (auto it = candidate_set.rbegin(); it != candidate_set.rend(); ++it) {
                size_t i = *it;
                auto joined = union_box(kind, out[i]->at("r"), box_value);
                if (!joined)
                    continue;
                if (painted_after(footprint, static_cast<long long>(i), &style))
                    break;
                exact.erase(exact_key(style, box_of(out[i]->at("r"))));
                (*out[i])["r"] = *joined;
                prints[i] = drawing_footprint(*out[i]);
                index_entry(i);
                merged = true;
                break;
            }
            if (merged)
                continue;
        }
        Value kept = entry;
        kept["r"] = box_value;
        out.push_back(kept);
        styles.push_back(style);
        prints.push_back(footprint);
        index_entry(out.size() - 1);
    }
    std::vector<Value> result;
    for (auto &entry : out)
        if (entry)
            result.push_back(std::move(*entry));
    return result;
}

// _drawing_sort_key, as a list of numbers compared lexicographically.
std::vector<double> drawing_sort_key(const Value &entry) {
    const Value &r = entry.at("r");
    double x0 = num(r[0]), y0 = num(r[1]), x1 = num(r[2]), y1 = num(r[3]);
    if (entry.contains("c") && !entry.contains("f")) {
        const Value *w = entry.find("w");
        double width = w ? num(*w) : 0;
        if (y0 == y1)
            return {0, width, 0, y0, x0, x1};
        if (x0 == x1)
            return {0, width, 1, x0, y0, y1};
        return {1, width, 2, y0, x0, y1, x1};
    }
    return {2, 0, 2, y0, x0, y1, x1};
}

std::optional<std::string> paint_colour(const Value &entry) {
    const Value *t = entry.find("t");
    if (!t || !t->is_string() || t->as_string() != "rect")
        return std::nullopt;
    bool f = entry.contains("f"), c = entry.contains("c");
    // The colours are strings ("#rrggbb", or "" for one MuPDF gave no RGB for).
    if (f && !c)
        return entry.at("f").is_string() ? entry.at("f").as_string() : json::dumps(entry.at("f"));
    if (c && !f)
        return entry.at("c").is_string() ? entry.at("c").as_string() : json::dumps(entry.at("c"));
    return std::nullopt;
}

double frame_tolerance(int precision) { return precision >= 1 ? 0.11 : 0.21; }

std::vector<Value> frames_from_rules(const std::vector<Value> &entries, double tolerance) {
    // (width, along, start) buckets -> indices, in insertion order per bucket
    std::map<std::string, std::vector<size_t>> horizontal, vertical;
    auto bucket = [](const Value *width, long long a, long long s) {
        KeyBuilder k;
        k.value(width);
        k.number(static_cast<double>(a)).number(static_cast<double>(s));
        return k.s;
    };
    std::vector<size_t> tops;
    for (size_t index = 0; index < entries.size(); ++index) {
        const Value &entry = entries[index];
        if (drawing_kind(entry) != Kind::Rule)
            continue;
        Box r = box_of(entry.at("r"));
        const Value *width = entry.find("w");
        if (r[1] == r[3] && r[0] < r[2]) {
            horizontal[bucket(width, py::round_int(r[1] * 10), py::round_int(r[0] * 10))].push_back(index);
            tops.push_back(index);
        } else if (r[0] == r[2] && r[1] < r[3]) {
            vertical[bucket(width, py::round_int(r[0] * 10), py::round_int(r[1] * 10))].push_back(index);
        }
    }
    if (tops.empty() || vertical.empty())
        return entries;

    std::set<size_t> used;
    std::map<size_t, Value> frames;
    int reach = static_cast<int>(tolerance * 10) + 1;

    auto near = [&](const std::map<std::string, std::vector<size_t>> &table, const Value *width, double along,
                    double start) {
        std::vector<size_t> found;
        long long a = py::round_int(along * 10), s = py::round_int(start * 10);
        for (int da = -reach; da <= reach; ++da)
            for (int ds = -reach; ds <= reach; ++ds) {
                auto it = table.find(bucket(width, a + da, s + ds));
                if (it == table.end())
                    continue;
                for (size_t index : it->second)
                    if (!used.count(index))
                        found.push_back(index);
            }
        return found;
    };

    std::stable_sort(tops.begin(), tops.end(), [&](size_t i, size_t j) {
        const Value &ri = entries[i].at("r"), &rj = entries[j].at("r");
        if (num(ri[1]) != num(rj[1]))
            return num(ri[1]) < num(rj[1]);
        return num(ri[0]) < num(rj[0]);
    });
    for (size_t top_index : tops) {
        if (used.count(top_index))
            continue;
        const Value &tr = entries[top_index].at("r");
        double a = num(tr[0]), top = num(tr[1]), b = num(tr[2]);
        const Value *width = entries[top_index].find("w");
        double w = width && !width->is_null() ? num(*width) : 0;
        double half = w / 2;
        struct Best {
            double by;
            size_t bottom, left, right;
        };
        std::optional<Best> best;
        for (size_t left : near(vertical, width, a + half, top - half)) {
            Box lr = box_of(entries[left].at("r"));
            double lx = lr[0], ly0 = lr[1], ly1 = lr[3];
            double bottom = ly1 - half;
            if (std::fabs(lx - (a + half)) > tolerance || std::fabs(ly0 - (top - half)) > tolerance || bottom - top <= w)
                continue;
            for (size_t right : near(vertical, width, b - half, top - half)) {
                if (right == left)
                    continue;
                Box rr = box_of(entries[right].at("r"));
                double rx = rr[0], ry0 = rr[1], ry1 = rr[3];
                if (std::fabs(rx - (b - half)) > tolerance || std::fabs(ry0 - (top - half)) > tolerance || rx - lx <= w)
                    continue;
                for (size_t bottom_index : near(horizontal, width, bottom, a)) {
                    if (bottom_index == top_index)
                        continue;
                    Box br = box_of(entries[bottom_index].at("r"));
                    double ba = br[0], by = br[1], bb = br[2];
                    if (std::fabs(ba - a) > tolerance || std::fabs(bb - b) > tolerance ||
                        std::fabs(ly1 - (by + half)) > tolerance || std::fabs(ry1 - (by + half)) > tolerance ||
                        by - top <= w)
                        continue;
                    if (!best || by < best->by)
                        best = Best{by, bottom_index, left, right};
                }
            }
        }
        if (!best)
            continue;
        Value frame = Value::object();
        frame["t"] = "rect";
        frame["r"] = Value(json::Array{entries[best->left].at("r")[0], tr[1], entries[best->right].at("r")[0],
                                       entries[best->bottom].at("r")[1]});
        frame["c"] = entries[top_index].at("c");
        if (width && !width->is_null())
            frame["w"] = *width;
        used.insert({top_index, best->bottom, best->left, best->right});
        frames[top_index] = frame;
    }
    if (frames.empty())
        return entries;
    std::vector<Value> result;
    for (size_t index = 0; index < entries.size(); ++index) {
        auto frame = frames.find(index);
        if (frame != frames.end())
            result.push_back(frame->second);
        else if (!used.count(index))
            result.push_back(entries[index]);
    }
    return result;
}

std::vector<Value> order_colour_runs(const std::vector<Value> &entries, double tolerance) {
    std::vector<Value> out, run;
    std::optional<std::string> colour;
    auto flush = [&]() {
        std::vector<Value> framed = frames_from_rules(run, tolerance);
        std::vector<std::pair<std::vector<double>, size_t>> keyed;
        for (size_t i = 0; i < framed.size(); ++i)
            keyed.emplace_back(drawing_sort_key(framed[i]), i);
        std::stable_sort(keyed.begin(), keyed.end(),
                         [](const auto &x, const auto &y) { return x.first < y.first; });
        for (const auto &k : keyed)
            out.push_back(framed[k.second]);
    };
    for (const Value &entry : entries) {
        auto paint = paint_colour(entry);
        if (!paint) {
            flush();
            run.clear();
            colour.reset();
            out.push_back(entry);
            continue;
        }
        if (!run.empty() && *paint != *colour) {
            flush();
            run.clear();
        }
        run.push_back(entry);
        colour = paint;
    }
    flush();
    return out;
}

} // namespace

std::vector<Value> compact_drawings(std::vector<Value> entries, const Box &bounds, int precision) {
    std::vector<Value> current = std::move(entries);
    std::vector<Value> compacted;
    bool settled = false;
    for (int pass = 0; pass < kCompactPasses; ++pass) {
        compacted = compact_once(current, bounds);
        if (compacted.size() == current.size()) {
            settled = true;
            break;
        }
        current = compacted;
    }
    if (!settled)
        compacted = current;
    return order_colour_runs(compacted, frame_tolerance(precision));
}

// --- regions ------------------------------------------------------------------------------

std::vector<Rect> merge_boxes(const std::vector<Rect> &boxes, double pad) {
    std::vector<Rect> merged;
    for (const Rect &box : boxes) {
        bool placed = false;
        for (Rect &existing : merged) {
            if (existing.plus(-pad, -pad, pad, pad).intersects(box)) {
                existing = existing | box;
                placed = true;
                break;
            }
        }
        if (!placed)
            merged.push_back(box);
    }
    for (int round = 0; round < 8; ++round) {
        std::vector<Rect> collapsed;
        for (const Rect &box : merged) {
            bool placed = false;
            for (Rect &existing : collapsed) {
                if (existing.plus(-pad, -pad, pad, pad).intersects(box)) {
                    existing = existing | box;
                    placed = true;
                    break;
                }
            }
            if (!placed)
                collapsed.push_back(box);
        }
        if (collapsed.size() == merged.size())
            break;
        merged = collapsed;
    }
    return merged;
}

namespace {

constexpr double kArtworkMinSide = 24.0;
constexpr double kArtworkMaxPageFraction = 0.55;
constexpr double kGlyphRunMaxGap = 48.0;
constexpr size_t kGlyphRunMinCount = 3;
constexpr long kGlyphMinCurves = 30;
constexpr double kGlyphMinShortSide = 5.0;

bool glyph_neighbours(const Rect &a, const Rect &b) {
    double v_overlap = std::min(a.y1, b.y1) - std::max(a.y0, b.y0);
    double h_gap = std::max(a.x0, b.x0) - std::min(a.x1, b.x1);
    if (v_overlap >= 0.5 * std::min(a.height(), b.height()) && h_gap <= kGlyphRunMaxGap)
        return true;
    double h_overlap = std::min(a.x1, b.x1) - std::max(a.x0, b.x0);
    double v_gap = std::max(a.y0, b.y0) - std::min(a.y1, b.y1);
    return h_overlap >= 0.5 * std::min(a.width(), b.width()) && v_gap <= kGlyphRunMaxGap;
}

std::vector<Rect> glyph_regions(const std::vector<Rect> &small, const std::vector<long> &curve_counts) {
    size_t count = small.size();
    std::vector<size_t> parent(count);
    for (size_t i = 0; i < count; ++i)
        parent[i] = i;
    auto find = [&](size_t i) {
        while (parent[i] != i) {
            parent[i] = parent[parent[i]];
            i = parent[i];
        }
        return i;
    };
    for (size_t i = 0; i < count; ++i)
        for (size_t j = i + 1; j < count; ++j)
            if (glyph_neighbours(small[i], small[j]))
                parent[find(i)] = find(j);
    std::vector<std::pair<size_t, std::vector<size_t>>> clusters;
    for (size_t i = 0; i < count; ++i) {
        size_t root = find(i);
        auto it = std::find_if(clusters.begin(), clusters.end(), [&](const auto &c) { return c.first == root; });
        if (it == clusters.end())
            clusters.push_back({root, {i}});
        else
            it->second.push_back(i);
    }
    std::vector<Rect> regions;
    for (const auto &[root, members] : clusters) {
        Rect u = small[members[0]];
        for (size_t k = 1; k < members.size(); ++k)
            u.include_rect(small[members[k]]);
        double long_side = std::max(u.width(), u.height());
        double short_side = std::min(u.width(), u.height());
        if (long_side < kArtworkMinSide || short_side < kGlyphMinShortSide)
            continue;
        long curves = 0;
        for (size_t index : members)
            curves += curve_counts[index];
        if (members.size() >= kGlyphRunMinCount || curves >= kGlyphMinCurves)
            regions.push_back(u.plus(-1.0, -1.0, 1.0, 1.0));
    }
    return regions;
}

} // namespace

std::vector<Rect> artwork_regions(mu::Page &page, const std::vector<VisibleDrawing> &drawings) {
    std::vector<Rect> boxes;
    std::vector<std::pair<Rect, long>> paths;
    Rect page_rect = page.rect();
    for (const auto &[path, clip] : drawings) {
        const auto &items = path->items;
        if (!path_is_artwork(items))
            continue;
        Rect rect = path->rect & page_rect;
        if (clip && spills(rect.tuple(), *clip)) {
            auto clipped = clip_box(rect.tuple(), *clip);
            if (!clipped)
                continue;
            rect = *clipped;
        }
        if (rect.is_empty() || rect.width() <= 0 || rect.height() <= 0)
            continue;
        boxes.push_back(rect);
        long curves = 0;
        for (const auto &item : items)
            if (item.kind == mu::PathItem::Curve)
                ++curves;
        paths.emplace_back(rect, curves);
    }
    if (boxes.empty())
        return {};

    std::vector<Rect> regions, small;
    for (const Rect &region : merge_boxes(boxes)) {
        if (region.width() >= kArtworkMinSide && region.height() >= kArtworkMinSide)
            regions.push_back(region);
        else
            small.push_back(region);
    }
    if (!small.empty()) {
        std::vector<long> curve_counts;
        for (const Rect &region : small) {
            long total = 0;
            for (const auto &[rect, curves] : paths)
                if (region.contains(rect))
                    total += curves;
            curve_counts.push_back(total);
        }
        for (const Rect &r : glyph_regions(small, curve_counts))
            regions.push_back(r);
    }
    if (regions.empty())
        return {};

    for (const auto &info : page.image_info_xrefs()) {
        Rect box = info.bbox & page_rect;
        if (box.is_empty() || box.get_area() <= 0)
            continue;
        for (Rect &region : regions) {
            Rect overlap = box & region;
            if (!overlap.is_empty() && overlap.get_area() >= 0.25 * box.get_area()) {
                region = region | box;
                break;
            }
        }
    }
    regions = merge_boxes(regions);
    double page_area = std::fabs(page_rect.width() * page_rect.height());
    if (page_area == 0)
        page_area = 1.0;
    double total = 0;
    for (const Rect &r : regions)
        total += r.get_area();
    if (total / page_area > kArtworkMaxPageFraction)
        return {};
    return regions;
}

} // namespace pdft::engine
