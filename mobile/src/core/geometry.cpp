// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
#include "geometry.h"

extern "C" {
#include "mupdf/fitz.h"
}

namespace pdft {

namespace {
fz_rect to_fz(const Rect &r) {
    return fz_make_rect(static_cast<float>(r.x0), static_cast<float>(r.y0), static_cast<float>(r.x1),
                        static_cast<float>(r.y1));
}
Rect from_fz(fz_rect r) { return {r.x0, r.y0, r.x1, r.y1}; }
} // namespace

Rect &Rect::intersect(const Rect &r) {
    if (r.is_infinite())
        return *this;
    if (is_infinite() || r.is_empty()) {
        *this = r;
        return *this;
    }
    if (is_empty())
        return *this;
    *this = from_fz(fz_intersect_rect(to_fz(*this), to_fz(r)));
    return *this;
}

Rect &Rect::include_rect(const Rect &r) {
    if (r.is_infinite() || is_infinite()) {
        *this = infinite();
        return *this;
    }
    if (r.is_empty())
        return *this;
    if (is_empty()) {
        *this = r;
        return *this;
    }
    *this = from_fz(fz_union_rect(to_fz(*this), to_fz(r)));
    return *this;
}

Rect &Rect::include_point(const Point &p) {
    fz_point fp = fz_make_point(static_cast<float>(p.x), static_cast<float>(p.y));
    *this = from_fz(fz_include_point_in_rect(to_fz(*this), fp));
    return *this;
}

Rect Quad::rect() const {
    return {std::min({ul.x, ur.x, lr.x, ll.x}), std::min({ul.y, ur.y, lr.y, ll.y}),
            std::max({ul.x, ur.x, lr.x, ll.x}), std::max({ul.y, ur.y, lr.y, ll.y})};
}

} // namespace pdft
