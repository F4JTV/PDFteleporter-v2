// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// Points, rectangles and quads with the semantics of PyMuPDF's Python
// classes, which the engine was written against.
//
// Coordinates are doubles, as Python floats are, but every value MuPDF hands
// over is a float, and the operations PyMuPDF performs in C -- intersection,
// union, including a point -- round their results to float again. Doing the
// same here is what keeps the engine's arithmetic, and so the archive it
// writes, identical to the desktop version's.
#pragma once

#include <algorithm>
#include <array>
#include <cmath>

namespace pdft {

constexpr double FZ_MIN_INF_RECT = -2147483648.0;
constexpr double FZ_MAX_INF_RECT = 2147483520.0;

struct Point {
    double x = 0, y = 0;
    Point() = default;
    Point(double x_, double y_) : x(x_), y(y_) {}
    bool operator==(const Point &o) const { return x == o.x && y == o.y; }
    bool operator!=(const Point &o) const { return !(*this == o); }
};

struct Rect {
    double x0 = 0, y0 = 0, x1 = 0, y1 = 0;

    Rect() = default;
    Rect(double a, double b, double c, double d) : x0(a), y0(b), x1(c), y1(d) {}

    static Rect infinite() { return {FZ_MIN_INF_RECT, FZ_MIN_INF_RECT, FZ_MAX_INF_RECT, FZ_MAX_INF_RECT}; }

    double operator[](int i) const { return i == 0 ? x0 : i == 1 ? y0 : i == 2 ? x1 : y1; }
    std::array<double, 4> tuple() const { return {x0, y0, x1, y1}; }

    // Rect.width / .height: never negative.
    double width() const { return std::max(0.0, x1 - x0); }
    double height() const { return std::max(0.0, y1 - y0); }
    double get_area() const { return 1.0 * width() * height(); }

    bool is_empty() const { return x0 >= x1 || y0 >= y1; }
    bool is_infinite() const {
        return x0 == FZ_MIN_INF_RECT && y0 == FZ_MIN_INF_RECT && x1 == FZ_MAX_INF_RECT && y1 == FZ_MAX_INF_RECT;
    }
    bool is_valid() const { return x0 <= x1 && y0 <= y1; }

    // Rect + (a, b, c, d)
    Rect plus(double a, double b, double c, double d) const { return {x0 + a, y0 + b, x1 + c, y1 + d}; }

    Rect &normalize() {
        if (x1 < x0)
            std::swap(x0, x1);
        if (y1 < y0)
            std::swap(y0, y1);
        return *this;
    }

    // Rect.intersect(r), in place.
    Rect &intersect(const Rect &r);
    // Rect.include_rect(r) -- the | operator -- in place.
    Rect &include_rect(const Rect &r);
    Rect &include_point(const Point &p);

    // r1 & r2 and r1 | r2: copies.
    Rect operator&(const Rect &r) const { Rect a = *this; return a.intersect(r); }
    Rect operator|(const Rect &r) const { Rect a = *this; return a.include_rect(r); }

    bool intersects(const Rect &r) const {
        return !is_empty() && !is_infinite() && !r.is_empty() && !r.is_infinite() &&
               x0 < r.x1 && r.x0 < x1 && y0 < r.y1 && r.y0 < y1;
    }
    // r in self
    bool contains(const Rect &r) const {
        return x0 <= r.x0 && r.x0 <= r.x1 && r.x1 <= x1 && y0 <= r.y0 && r.y0 <= r.y1 && r.y1 <= y1;
    }

    bool operator==(const Rect &o) const { return x0 == o.x0 && y0 == o.y0 && x1 == o.x1 && y1 == o.y1; }
};

struct Quad {
    Point ul, ur, ll, lr;
    // Quad.rect: the smallest rectangle around it, computed in float by MuPDF.
    Rect rect() const;
};

// A double as MuPDF stores it.
inline double f32(double v) { return static_cast<double>(static_cast<float>(v)); }

} // namespace pdft
