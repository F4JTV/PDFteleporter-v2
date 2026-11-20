// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
#include "mupdf_facade.h"

#include "pyutil.h"

extern "C" {
#include "mupdf/fitz.h"
#include "mupdf/pdf.h"
}

#include <cfloat>
#include <cmath>
#include <cstring>
#include <map>

namespace pdft::mu {

namespace {

// Runs MuPDF calls, turning a MuPDF error into a C++ exception once MuPDF's
// own error frame has been left. Nothing with a destructor may live inside
// `body`'s frames when MuPDF throws, so `body` only calls MuPDF.
#define MU_TRY(ctx, ...) ::pdft::mu::guarded((ctx), [&]() { __VA_ARGS__; })

Rect rect_of(fz_rect r) { return {r.x0, r.y0, r.x1, r.y1}; }

// A C string as PyMuPDF turns it into Python text (JM_EscapeStrFromStr):
// every byte a code point of its own, Latin-1 fashion. A font name in UTF-8
// comes out as mojibake there, and must here too: the engine compares and
// measures these names.
std::string latin1(const char *s) {
    std::string out;
    if (!s)
        return out;
    for (const unsigned char *p = reinterpret_cast<const unsigned char *>(s); *p; ++p) {
        if (*p < 0x80) {
            out += static_cast<char>(*p);
        } else {
            out += static_cast<char>(0xC0 | (*p >> 6));
            out += static_cast<char>(0x80 | (*p & 0x3F));
        }
    }
    return out;
}
Point point_of(fz_point p) { return {p.x, p.y}; }

constexpr int FILL_PATH = 1, STROKE_PATH = 2, CLIP_PATH = 3, CLIP_STROKE_PATH = 4;
constexpr int TEXTFLAGS_DICT = FZ_STEXT_PRESERVE_LIGATURES | FZ_STEXT_PRESERVE_WHITESPACE |
                               FZ_STEXT_MEDIABOX_CLIP | FZ_STEXT_PRESERVE_IMAGES |
                               FZ_STEXT_USE_CID_FOR_UNKNOWN_UNICODE;
constexpr int TEXTFLAGS_TEXT = FZ_STEXT_PRESERVE_LIGATURES | FZ_STEXT_PRESERVE_WHITESPACE |
                               FZ_STEXT_MEDIABOX_CLIP | FZ_STEXT_USE_CID_FOR_UNKNOWN_UNICODE;

// --- character boxes, as PyMuPDF corrects them -------------------------------------

float font_ascender(fz_context *ctx, fz_font *font) { return fz_font_ascender(ctx, font); }
float font_descender(fz_context *ctx, fz_font *font) { return fz_font_descender(ctx, font); }

// A character's quad with the font's ascender and descender standing in for
// MuPDF's own heights when those add up to less than an em -- PyMuPDF's
// correction for fonts whose metrics leave glyphs flattened. The arithmetic
// is in float, as it is there, so the boxes come out bit for bit the same.
fz_quad char_quad(fz_context *ctx, fz_stext_line *line, fz_stext_char *ch) {
    if (line->wmode)
        return ch->quad;
    fz_font *font = ch->font;
    float asc = font_ascender(ctx, font);
    float dsc = font_descender(ctx, font);
    float fsize = ch->size;
    float asc_dsc = asc - dsc + FLT_EPSILON;
    if (asc_dsc >= 1)
        return ch->quad;
    if (asc < 1e-3f) {
        dsc = -0.1f;
        asc = 0.9f;
        asc_dsc = 1.0f;
    }
    if (asc_dsc < 1) {
        dsc = dsc / asc_dsc;
        asc = asc / asc_dsc;
    }
    asc_dsc = asc - dsc;
    asc = asc * fsize / asc_dsc;
    dsc = dsc * fsize / asc_dsc;

    float c = line->dir.x;
    float s = line->dir.y;
    fz_matrix trm1 = fz_make_matrix(c, -s, s, c, 0, 0);
    fz_matrix trm2 = fz_make_matrix(c, s, -s, c, 0, 0);
    if (c == -1) {
        trm1.d = 1;
        trm2.d = 1;
    }
    fz_matrix xlate1 = fz_make_matrix(1, 0, 0, 1, -ch->origin.x, -ch->origin.y);
    fz_matrix xlate2 = fz_make_matrix(1, 0, 0, 1, ch->origin.x, ch->origin.y);
    fz_quad quad = fz_transform_quad(ch->quad, xlate1);
    quad = fz_transform_quad(quad, trm1);
    if (c == 1 && quad.ul.y > 0) {
        quad.ul.y = asc;
        quad.ur.y = asc;
        quad.ll.y = dsc;
        quad.lr.y = dsc;
    } else {
        quad.ul.y = -asc;
        quad.ur.y = -asc;
        quad.ll.y = -dsc;
        quad.lr.y = -dsc;
    }
    if (quad.ll.x < 0) {
        quad.ll.x = 0;
        quad.ul.x = 0;
    }
    float cwidth = quad.lr.x - quad.ll.x;
    if (cwidth < FLT_EPSILON) {
        int glyph = fz_encode_character(ctx, font, ch->c);
        if (glyph) {
            float fwidth = fz_advance_glyph(ctx, font, glyph, line->wmode);
            quad.lr.x = quad.ll.x + fwidth * fsize;
            quad.ur.x = quad.lr.x;
        }
    }
    quad = fz_transform_quad(quad, trm2);
    quad = fz_transform_quad(quad, xlate2);
    return quad;
}

fz_rect char_bbox(fz_context *ctx, fz_stext_line *line, fz_stext_char *ch) {
    fz_rect r = fz_rect_from_quad(char_quad(ctx, line, ch));
    if (!line->wmode)
        return r;
    if (r.y1 < r.y0 + ch->size)
        r.y0 = r.y1 - ch->size;
    return r;
}

bool rects_overlap(fz_rect a, fz_rect b) {
    return !(a.x0 >= b.x1 || a.y0 >= b.y1 || a.x1 <= b.x0 || a.y1 <= b.y0);
}

int char_font_flags(fz_context *ctx, fz_font *font, fz_stext_line *line, fz_stext_char *ch) {
    int flags = 0;
    if (line->wmode == 0 && line->dir.x == 1 && line->dir.y == 0)
        flags = ch->origin.y < line->first_char->origin.y - ch->size * 0.1f;
    flags += fz_font_is_italic(ctx, font) * TEXT_FONT_ITALIC;
    flags += fz_font_is_serif(ctx, font) * TEXT_FONT_SERIFED;
    flags += fz_font_is_monospaced(ctx, font) * TEXT_FONT_MONOSPACED;
    flags += fz_font_is_bold(ctx, font) * TEXT_FONT_BOLD;
    return flags;
}

// The font's name without a subset tag of six letters.
const char *span_font_name(fz_context *ctx, fz_font *font) {
    const char *name = fz_font_name(ctx, font);
    const char *s = std::strchr(name, '+');
    if (!s || s - name != 6)
        return name;
    return s + 1;
}

fz_stext_page *make_stext(fz_context *ctx, fz_page *page, int flags) {
    fz_stext_page *tpage = nullptr;
    fz_device *dev = nullptr;
    fz_stext_options options;
    std::memset(&options, 0, sizeof options);
    options.flags = flags;
    fz_var(tpage);
    fz_var(dev);
    fz_try(ctx) {
        fz_rect rect = fz_bound_page(ctx, page);
        tpage = fz_new_stext_page(ctx, rect);
        dev = fz_new_stext_device(ctx, tpage, &options);
        fz_run_page(ctx, page, dev, fz_identity, nullptr);
        fz_close_device(ctx, dev);
    }
    fz_always(ctx) { fz_drop_device(ctx, dev); }
    fz_catch(ctx) {
        fz_drop_stext_page(ctx, tpage);
        fz_rethrow(ctx);
    }
    return tpage;
}

// PyMuPDF reads a rotated page's text and drawings with its /Rotate set to
// 0 for the time being (Page.get_textpage, Page.get_cdrawings).
struct Derotate {
    fz_context *ctx;
    pdf_obj *obj = nullptr;
    int old = 0;
    Derotate(fz_context *c, fz_page *page, int rotation) : ctx(c), old(rotation) {
        pdf_page *pp = pdf_page_from_fz_page(ctx, page);
        if (!pp || rotation == 0)
            return;
        obj = pp->obj;
        fz_try(ctx) { pdf_dict_put_int(ctx, obj, PDF_NAME(Rotate), 0); }
        fz_catch(ctx) { obj = nullptr; }
    }
    ~Derotate() {
        if (!obj)
            return;
        fz_try(ctx) { pdf_dict_put_int(ctx, obj, PDF_NAME(Rotate), old); }
        fz_catch(ctx) {}
    }
};

struct StextHolder {
    fz_context *ctx;
    fz_stext_page *page = nullptr;
    ~StextHolder() { fz_drop_stext_page(ctx, page); }
};

RawLine make_line(fz_context *ctx, fz_stext_line *line, fz_rect tp_rect, fz_rect &line_rect) {
    RawLine out;
    out.wmode = line->wmode;
    out.dir = point_of(line->dir);
    fz_rect span_rect = fz_empty_rect;
    line_rect = fz_empty_rect;
    fz_point span_origin = {0, 0};

    struct Style {
        float size = -1;
        int flags = 0;
        int char_flags = 0;
        const char *font = "";
        uint32_t argb = 0;
        float asc = 0, desc = 0;
    } old_style, style;
    RawSpan span;
    bool have_span = false;

    for (fz_stext_char *ch = line->first_char; ch; ch = ch->next) {
        fz_rect r = char_bbox(ctx, line, ch);
        if (!rects_overlap(tp_rect, r) && !fz_is_infinite_rect(tp_rect))
            continue;
        style.size = ch->size;
        style.flags = char_font_flags(ctx, ch->font, line, ch);
        style.char_flags = ch->flags & ~FZ_STEXT_SYNTHETIC;
        style.font = span_font_name(ctx, ch->font);
        style.argb = ch->argb;
        style.asc = font_ascender(ctx, ch->font);
        style.desc = font_descender(ctx, ch->font);

        if (style.size != old_style.size || style.flags != old_style.flags ||
            style.char_flags != old_style.char_flags || style.argb != old_style.argb ||
            std::strcmp(style.font, old_style.font) != 0) {
            if (old_style.size >= 0) {
                span.origin = point_of(span_origin);
                span.bbox = rect_of(span_rect);
                line_rect = fz_union_rect(line_rect, span_rect);
                out.spans.push_back(std::move(span));
                span = RawSpan();
            }
            float asc = style.asc, desc = style.desc;
            if (style.asc < 1e-3f) {
                asc = 0.9f;
                desc = -0.1f;
            }
            span.size = style.size;
            span.flags = style.flags;
            span.char_flags = style.char_flags;
            span.font = latin1(style.font);
            span.color = static_cast<long>(style.argb & 0xffffff);
            span.alpha = static_cast<int>(style.argb >> 24);
            span.ascender = asc;
            span.descender = desc;
            have_span = true;
            old_style = style;
            span_rect = r;
            span_origin = ch->origin;
        }
        span_rect = fz_union_rect(span_rect, r);
        RawChar rc;
        rc.c = static_cast<char32_t>(ch->c);
        rc.origin = point_of(ch->origin);
        rc.bbox = rect_of(r);
        rc.synthetic = (ch->flags & FZ_STEXT_SYNTHETIC) != 0;
        span.chars.push_back(rc);
    }
    if (have_span) {
        span.origin = point_of(span_origin);
        span.bbox = rect_of(span_rect);
        if (!fz_is_empty_rect(span_rect)) {
            out.spans.push_back(std::move(span));
            line_rect = fz_union_rect(line_rect, span_rect);
        }
    }
    out.bbox = rect_of(line_rect);
    return out;
}

// --- the line-art device: get_drawings() ------------------------------------------

struct LineartState {
    fz_context *ctx = nullptr;
    std::vector<Drawing> *out = nullptr;
    bool clips = false;
    // the path being traced
    bool have_path = false;
    Drawing path;
    std::vector<Rect> scissors;
    float pathfactor = 0;
    fz_matrix ctm = fz_identity;
    fz_point lastpoint = {0, 0};
    fz_point firstpoint = {0, 0};
    bool havemove = false;
    fz_rect pathrect = fz_infinite_rect;
    int linecount = 0;
    int path_type = 0;
    long depth = 0;
    long seqno = 0;
    bool failed = false;
};

struct LineartDevice {
    fz_device super;
    LineartState *state;
};

LineartState &state_of(fz_device *dev) { return *reinterpret_cast<LineartDevice *>(dev)->state; }

PathItem line_item(fz_point a, fz_point b) {
    PathItem it;
    it.kind = PathItem::Line;
    it.p[0] = point_of(a);
    it.p[1] = point_of(b);
    return it;
}

// The last four lines close into a quadrilateral: replace them by one "qu".
void check_quad(LineartState &st) {
    auto &items = st.path.items;
    size_t len = items.size();
    float f[8];
    fz_point lp = {0, 0};
    for (size_t i = 0; i < 4; ++i) {
        const PathItem &line = items[len - 4 + i];
        f[i * 2] = static_cast<float>(line.p[0].x);
        f[i * 2 + 1] = static_cast<float>(line.p[0].y);
        lp = fz_make_point(static_cast<float>(line.p[1].x), static_cast<float>(line.p[1].y));
    }
    if (lp.x != f[0] || lp.y != f[1])
        return;
    st.linecount = 0;
    fz_quad q = fz_make_quad(f[0], f[1], f[6], f[7], f[2], f[3], f[4], f[5]);
    PathItem it;
    it.kind = PathItem::QuadItem;
    it.quad.ul = point_of(q.ul);
    it.quad.ur = point_of(q.ur);
    it.quad.ll = point_of(q.ll);
    it.quad.lr = point_of(q.lr);
    items.resize(len - 4);
    items.push_back(it);
}

// The last three lines and the closing make a rectangle: one "re".
bool check_rect(LineartState &st) {
    st.linecount = 0;
    auto &items = st.path.items;
    size_t len = items.size();
    const PathItem &line0 = items[len - 3];
    const PathItem &line2 = items[len - 1];
    fz_point ll = fz_make_point(static_cast<float>(line0.p[0].x), static_cast<float>(line0.p[0].y));
    fz_point lr = fz_make_point(static_cast<float>(line0.p[1].x), static_cast<float>(line0.p[1].y));
    fz_point ur = fz_make_point(static_cast<float>(line2.p[0].x), static_cast<float>(line2.p[0].y));
    fz_point ul = fz_make_point(static_cast<float>(line2.p[1].x), static_cast<float>(line2.p[1].y));
    if (ll.y != lr.y || ll.x != ul.x || ur.y != ul.y || ur.x != lr.x)
        return false;
    st.lastpoint = fz_make_point(ll.x, ll.y);
    fz_rect r;
    int orientation;
    if (ul.y < lr.y) {
        r = fz_make_rect(ul.x, ul.y, lr.x, lr.y);
        orientation = 1;
    } else {
        r = fz_make_rect(ll.x, ll.y, ur.x, ur.y);
        orientation = -1;
    }
    PathItem it;
    it.kind = PathItem::RectItem;
    it.rect = rect_of(r);
    it.rect.normalize();
    it.orientation = orientation;
    items.resize(len - 3);
    items.push_back(it);
    return true;
}

void trace_moveto(fz_context *, void *arg, float x, float y) {
    LineartState &st = *static_cast<LineartState *>(arg);
    st.lastpoint = fz_transform_point(fz_make_point(x, y), st.ctm);
    if (fz_is_infinite_rect(st.pathrect))
        st.pathrect = fz_make_rect(st.lastpoint.x, st.lastpoint.y, st.lastpoint.x, st.lastpoint.y);
    st.firstpoint = st.lastpoint;
    st.havemove = true;
    st.linecount = 0;
}

void trace_lineto(fz_context *, void *arg, float x, float y) {
    LineartState &st = *static_cast<LineartState *>(arg);
    fz_point p1 = fz_transform_point(fz_make_point(x, y), st.ctm);
    st.pathrect = fz_include_point_in_rect(st.pathrect, p1);
    st.path.items.push_back(line_item(st.lastpoint, p1));
    st.lastpoint = p1;
    st.linecount += 1;
    if (st.linecount == 4 && st.path_type != FILL_PATH)
        check_quad(st);
}

void trace_curveto(fz_context *, void *arg, float x1, float y1, float x2, float y2, float x3, float y3) {
    LineartState &st = *static_cast<LineartState *>(arg);
    st.linecount = 0;
    fz_point p1 = fz_transform_point(fz_make_point(x1, y1), st.ctm);
    fz_point p2 = fz_transform_point(fz_make_point(x2, y2), st.ctm);
    fz_point p3 = fz_transform_point(fz_make_point(x3, y3), st.ctm);
    st.pathrect = fz_include_point_in_rect(st.pathrect, p1);
    st.pathrect = fz_include_point_in_rect(st.pathrect, p2);
    st.pathrect = fz_include_point_in_rect(st.pathrect, p3);
    PathItem it;
    it.kind = PathItem::Curve;
    it.p[0] = point_of(st.lastpoint);
    it.p[1] = point_of(p1);
    it.p[2] = point_of(p2);
    it.p[3] = point_of(p3);
    st.path.items.push_back(it);
    st.lastpoint = p3;
}

void trace_close(fz_context *, void *arg) {
    LineartState &st = *static_cast<LineartState *>(arg);
    if (st.linecount == 3 && check_rect(st))
        return;
    st.linecount = 0;
    if (st.havemove) {
        if (st.firstpoint.x != st.lastpoint.x || st.firstpoint.y != st.lastpoint.y) {
            st.path.items.push_back(line_item(st.lastpoint, st.firstpoint));
            st.lastpoint = st.firstpoint;
        }
        st.havemove = false;
        st.path.close_path = false;
    } else {
        st.path.close_path = true;
    }
}

const fz_path_walker trace_walker = {trace_moveto, trace_lineto, trace_curveto, trace_close,
                                     nullptr, nullptr, nullptr, nullptr};

void lineart_path(LineartState &st, const fz_path *path) {
    st.pathrect = fz_infinite_rect;
    st.linecount = 0;
    st.lastpoint = fz_make_point(0, 0);
    st.firstpoint = fz_make_point(0, 0);
    st.path = Drawing();
    st.have_path = true;
    fz_walk_path(st.ctx, path, &trace_walker, &st);
    if (st.path.items.empty())
        st.have_path = false;
}

bool items_equal(const std::vector<PathItem> &a, const std::vector<PathItem> &b) {
    if (a.size() != b.size())
        return false;
    for (size_t i = 0; i < a.size(); ++i) {
        const PathItem &x = a[i], &y = b[i];
        if (x.kind != y.kind)
            return false;
        switch (x.kind) {
        case PathItem::Line:
            if (x.p[0] != y.p[0] || x.p[1] != y.p[1])
                return false;
            break;
        case PathItem::Curve:
            for (int k = 0; k < 4; ++k)
                if (x.p[k] != y.p[k])
                    return false;
            break;
        case PathItem::RectItem:
            if (!(x.rect == y.rect) || x.orientation != y.orientation)
                return false;
            break;
        case PathItem::QuadItem:
            if (x.quad.ul != y.quad.ul || x.quad.ur != y.quad.ur || x.quad.ll != y.quad.ll ||
                x.quad.lr != y.quad.lr)
                return false;
            break;
        }
    }
    return true;
}

// Append the path, or fold a stroke into the fill of the same path just
// before it: one "fs" entry, as PyMuPDF reports it.
void append_merge(LineartState &st) {
    std::vector<Drawing> &out = *st.out;
    Drawing &cur = st.path;
    if (!out.empty() && cur.type == "s") {
        Drawing &prev = out.back();
        if (prev.type == "f" && items_equal(prev.items, cur.items)) {
            // Keys the fill lacks come from the stroke; the fill's stay.
            if (!prev.color)
                prev.color = cur.color;
            if (!prev.width)
                prev.width = cur.width;
            prev.stroke_opacity = cur.stroke_opacity;
            if (!prev.close_path)
                prev.close_path = cur.close_path;
            prev.type = "fs";
            st.have_path = false;
            return;
        }
    }
    out.push_back(std::move(cur));
    st.have_path = false;
}

std::vector<double> colour(fz_context *ctx, fz_colorspace *cs, const float *color) {
    if (!cs)
        return {};
    float rgb[3] = {0, 0, 0};
    fz_convert_color(ctx, cs, color, fz_device_rgb(ctx), rgb, nullptr, fz_default_color_params);
    return {rgb[0], rgb[1], rgb[2]};
}

void dev_fill_path(fz_context *ctx, fz_device *dev, const fz_path *path, int even_odd, fz_matrix ctm,
                   fz_colorspace *cs, const float *color, float alpha, fz_color_params) {
    LineartState &st = state_of(dev);
    try {
        st.ctm = ctm;
        st.path_type = FILL_PATH;
        lineart_path(st, path);
        if (!st.have_path)
            return;
        st.path.type = "f";
        st.path.even_odd = even_odd != 0;
        st.path.fill_opacity = alpha;
        st.path.fill = colour(ctx, cs, color);
        st.path.rect = rect_of(st.pathrect);
        st.path.seqno = st.seqno;
        if (st.clips) {
            st.path.level = st.depth;
            st.path.has_level = true;
        }
        append_merge(st);
        st.seqno += 1;
    } catch (...) {
        st.failed = true;
    }
}

void dev_stroke_path(fz_context *ctx, fz_device *dev, const fz_path *path, const fz_stroke_state *stroke,
                     fz_matrix ctm, fz_colorspace *cs, const float *color, float alpha, fz_color_params) {
    LineartState &st = state_of(dev);
    try {
        st.pathfactor = sqrtf(fabsf(ctm.a * ctm.d - ctm.b * ctm.c));
        st.ctm = ctm;
        st.path_type = STROKE_PATH;
        lineart_path(st, path);
        if (!st.have_path)
            return;
        st.path.type = "s";
        st.path.stroke_opacity = alpha;
        st.path.color = colour(ctx, cs, color);
        st.path.width = static_cast<double>(st.pathfactor * stroke->linewidth);
        if (!st.path.close_path)
            st.path.close_path = false;
        st.path.rect = rect_of(st.pathrect);
        st.path.seqno = st.seqno;
        if (st.clips) {
            st.path.level = st.depth;
            st.path.has_level = true;
        }
        append_merge(st);
        st.seqno += 1;
    } catch (...) {
        st.failed = true;
    }
}

Rect compute_scissor(LineartState &st) {
    fz_rect scissor;
    if (!st.scissors.empty()) {
        const Rect &last = st.scissors.back();
        fz_rect l = fz_make_rect(static_cast<float>(last.x0), static_cast<float>(last.y0),
                                 static_cast<float>(last.x1), static_cast<float>(last.y1));
        scissor = fz_intersect_rect(l, st.pathrect);
    } else {
        scissor = st.pathrect;
    }
    st.scissors.push_back(rect_of(scissor));
    return rect_of(scissor);
}

void clip_common(LineartState &st, const fz_path *path, std::optional<bool> even_odd, int type, fz_matrix ctm) {
    st.ctm = ctm;
    st.path_type = type;
    lineart_path(st, path);
    if (!st.have_path)
        return;
    st.path.type = "clip";
    st.path.even_odd = even_odd;
    if (!st.path.close_path)
        st.path.close_path = false;
    st.path.scissor = compute_scissor(st);
    st.path.level = st.depth;
    st.path.has_level = true;
    append_merge(st);
    st.depth++;
}

void dev_clip_path(fz_context *, fz_device *dev, const fz_path *path, int even_odd, fz_matrix ctm, fz_rect) {
    LineartState &st = state_of(dev);
    if (!st.clips)
        return;
    try {
        clip_common(st, path, even_odd != 0, CLIP_PATH, ctm);
    } catch (...) {
        st.failed = true;
    }
}

void dev_clip_stroke_path(fz_context *, fz_device *dev, const fz_path *path, const fz_stroke_state *,
                          fz_matrix ctm, fz_rect) {
    LineartState &st = state_of(dev);
    if (!st.clips)
        return;
    try {
        clip_common(st, path, std::nullopt, CLIP_STROKE_PATH, ctm);
    } catch (...) {
        st.failed = true;
    }
}

void dev_clip_other(LineartState &st) {
    if (!st.clips)
        return;
    try {
        compute_scissor(st);
        st.depth++;
    } catch (...) {
        st.failed = true;
    }
}

void dev_clip_text(fz_context *, fz_device *dev, const fz_text *, fz_matrix, fz_rect) { dev_clip_other(state_of(dev)); }
void dev_clip_stroke_text(fz_context *, fz_device *dev, const fz_text *, const fz_stroke_state *, fz_matrix, fz_rect) {
    dev_clip_other(state_of(dev));
}
void dev_clip_image_mask(fz_context *, fz_device *dev, fz_image *, fz_matrix, fz_rect) { dev_clip_other(state_of(dev)); }

void dev_pop_clip(fz_context *, fz_device *dev) {
    LineartState &st = state_of(dev);
    if (!st.clips || st.scissors.empty())
        return;
    st.scissors.pop_back();
    st.depth--;
}

void dev_begin_group(fz_context *, fz_device *dev, fz_rect bbox, fz_colorspace *, int, int, int, float) {
    LineartState &st = state_of(dev);
    if (!st.clips)
        return;
    try {
        st.path = Drawing();
        st.path.type = "group";
        st.path.rect = rect_of(bbox);
        st.path.level = st.depth;
        st.path.has_level = true;
        st.have_path = true;
        append_merge(st);
        st.depth++;
    } catch (...) {
        st.failed = true;
    }
}

void dev_end_group(fz_context *, fz_device *dev) {
    LineartState &st = state_of(dev);
    if (!st.clips)
        return;
    st.depth--;
}

void dev_seqno(fz_device *dev) { state_of(dev).seqno += 1; }
void dev_fill_text(fz_context *, fz_device *dev, const fz_text *, fz_matrix, fz_colorspace *, const float *, float, fz_color_params) { dev_seqno(dev); }
void dev_stroke_text(fz_context *, fz_device *dev, const fz_text *, const fz_stroke_state *, fz_matrix, fz_colorspace *, const float *, float, fz_color_params) { dev_seqno(dev); }
void dev_fill_shade(fz_context *, fz_device *dev, fz_shade *, fz_matrix, float, fz_color_params) { dev_seqno(dev); }
void dev_fill_image(fz_context *, fz_device *dev, fz_image *, fz_matrix, float, fz_color_params) { dev_seqno(dev); }
void dev_fill_image_mask(fz_context *, fz_device *dev, fz_image *, fz_matrix, fz_colorspace *, const float *, float, fz_color_params) { dev_seqno(dev); }
void dev_ignore_text(fz_context *, fz_device *dev, const fz_text *, fz_matrix) { dev_seqno(dev); }

// --- resources ------------------------------------------------------------------

std::string name_of(fz_context *ctx, pdf_obj *obj) { return latin1(pdf_to_name(ctx, obj)); }

std::string font_extension(fz_context *ctx, pdf_document *pdf, int xref) {
    if (xref < 1)
        return "n/a";
    const char *ext = "n/a";
    pdf_obj *o = nullptr;
    fz_var(o);
    fz_var(ext);
    fz_try(ctx) {
        o = pdf_load_object(ctx, pdf, xref);
        pdf_obj *desft = pdf_dict_get(ctx, o, PDF_NAME(DescendantFonts));
        pdf_obj *obj;
        if (desft) {
            obj = pdf_resolve_indirect(ctx, pdf_array_get(ctx, desft, 0));
            obj = pdf_dict_get(ctx, obj, PDF_NAME(FontDescriptor));
        } else {
            obj = pdf_dict_get(ctx, o, PDF_NAME(FontDescriptor));
        }
        if (obj) {
            pdf_obj *ff3 = pdf_dict_get(ctx, obj, PDF_NAME(FontFile3));
            if (pdf_dict_get(ctx, obj, PDF_NAME(FontFile)))
                ext = "pfa";
            else if (pdf_dict_get(ctx, obj, PDF_NAME(FontFile2)))
                ext = "ttf";
            else if (ff3) {
                pdf_obj *sub = pdf_dict_get(ctx, ff3, PDF_NAME(Subtype));
                if (sub && pdf_is_name(ctx, sub)) {
                    if (pdf_name_eq(ctx, sub, PDF_NAME(Type1C)))
                        ext = "cff";
                    else if (pdf_name_eq(ctx, sub, PDF_NAME(CIDFontType0C)))
                        ext = "cid";
                    else if (pdf_name_eq(ctx, sub, PDF_NAME(OpenType)))
                        ext = "otf";
                }
            }
        }
    }
    fz_always(ctx) { pdf_drop_obj(ctx, o); }
    fz_catch(ctx) { ext = "n/a"; }
    return ext;
}

struct ScanState {
    std::vector<int> tracer;
};

void gather_fonts(fz_context *ctx, pdf_document *pdf, pdf_obj *dict, std::vector<FontEntry> &out, int stream_xref) {
    int n = pdf_dict_len(ctx, dict);
    for (int i = 0; i < n; ++i) {
        pdf_obj *refname = pdf_dict_get_key(ctx, dict, i);
        pdf_obj *fontdict = pdf_dict_get_val(ctx, dict, i);
        if (!pdf_is_dict(ctx, fontdict))
            continue;
        pdf_obj *subtype = pdf_dict_get(ctx, fontdict, PDF_NAME(Subtype));
        pdf_obj *basefont = pdf_dict_get(ctx, fontdict, PDF_NAME(BaseFont));
        pdf_obj *name = (!basefont || pdf_is_null(ctx, basefont)) ? pdf_dict_get(ctx, fontdict, PDF_NAME(Name)) : basefont;
        pdf_obj *encoding = pdf_dict_get(ctx, fontdict, PDF_NAME(Encoding));
        if (pdf_is_dict(ctx, encoding))
            encoding = pdf_dict_get(ctx, encoding, PDF_NAME(BaseEncoding));
        FontEntry e;
        e.xref = pdf_to_num(ctx, fontdict);
        e.ext = e.xref ? font_extension(ctx, pdf, e.xref) : "n/a";
        e.subtype = name_of(ctx, subtype);
        e.basefont = name_of(ctx, name);
        e.refname = name_of(ctx, refname);
        e.encoding = name_of(ctx, encoding);
        e.referencer = stream_xref;
        out.push_back(std::move(e));
    }
}

void gather_images(fz_context *ctx, pdf_obj *dict, std::vector<int> &out) {
    int n = pdf_dict_len(ctx, dict);
    for (int i = 0; i < n; ++i) {
        pdf_obj *imagedict = pdf_dict_get_val(ctx, dict, i);
        if (!pdf_is_dict(ctx, imagedict))
            continue;
        pdf_obj *type = pdf_dict_get(ctx, imagedict, PDF_NAME(Subtype));
        if (!pdf_name_eq(ctx, type, PDF_NAME(Image)))
            continue;
        out.push_back(pdf_to_num(ctx, imagedict));
    }
}

// JM_scan_resources: fonts (what=1) or images (what=2), recursing into the
// resources of form XObjects. MuPDF calls only: no C++ object is built
// between fz_try and a possible throw.
template <typename Gather>
void scan_resources(fz_context *ctx, pdf_document *pdf, pdf_obj *rsrc, int what, int stream_xref,
                    ScanState &state, Gather gather) {
    if (pdf_mark_obj(ctx, rsrc))
        return;
    bool stop = false;
    pdf_obj *xobj = pdf_dict_get(ctx, rsrc, PDF_NAME(XObject));
    gather(rsrc, xobj, stream_xref);
    int n = pdf_dict_len(ctx, xobj);
    for (int i = 0; i < n && !stop; ++i) {
        pdf_obj *obj = pdf_dict_get_val(ctx, xobj, i);
        int sxref = pdf_is_stream(ctx, obj) ? pdf_to_num(ctx, obj) : 0;
        pdf_obj *subrsrc = pdf_dict_get(ctx, obj, PDF_NAME(Resources));
        if (subrsrc) {
            if (std::find(state.tracer.begin(), state.tracer.end(), sxref) == state.tracer.end()) {
                state.tracer.push_back(sxref);
                scan_resources(ctx, pdf, subrsrc, what, sxref, state, gather);
            } else {
                stop = true;
            }
        }
    }
    pdf_unmark_obj(ctx, rsrc);
    (void)what;
}

Raster raster_of(fz_context *ctx, fz_pixmap *pix) {
    Raster r;
    r.w = fz_pixmap_width(ctx, pix);
    r.h = fz_pixmap_height(ctx, pix);
    r.n = fz_pixmap_components(ctx, pix);
    r.alpha = fz_pixmap_alpha(ctx, pix) != 0;
    fz_colorspace *cs = fz_pixmap_colorspace(ctx, pix);
    r.colorspace = cs ? fz_colorspace_name(ctx, cs) : "";
    int stride = static_cast<int>(fz_pixmap_stride(ctx, pix));
    const unsigned char *s = fz_pixmap_samples(ctx, pix);
    r.samples.resize(static_cast<size_t>(r.w) * r.h * r.n);
    for (int y = 0; y < r.h; ++y)
        std::memcpy(&r.samples[static_cast<size_t>(y) * r.w * r.n], s + static_cast<size_t>(y) * stride,
                    static_cast<size_t>(r.w) * r.n);
    return r;
}

struct PixHolder {
    fz_context *ctx;
    fz_pixmap *pix = nullptr;
    ~PixHolder() { fz_drop_pixmap(ctx, pix); }
};

struct ImageHolder {
    fz_context *ctx;
    fz_image *img = nullptr;
    ~ImageHolder() { fz_drop_image(ctx, img); }
};

struct BufferHolder {
    fz_context *ctx;
    fz_buffer *buf = nullptr;
    ~BufferHolder() { fz_drop_buffer(ctx, buf); }
};

std::string md5_hex(const unsigned char digest[16]) {
    static const char hex[] = "0123456789abcdef";
    std::string out;
    for (int i = 0; i < 16; ++i) {
        out += hex[digest[i] >> 4];
        out += hex[digest[i] & 15];
    }
    return out;
}

} // namespace

// --- Context ------------------------------------------------------------------------

Context::Context() {
    ctx_ = fz_new_context(nullptr, nullptr, FZ_STORE_DEFAULT);
    if (!ctx_)
        throw Error("cannot create a MuPDF context");
    fz_set_warning_callback(ctx_, [](void *, const char *) {}, nullptr);
    fz_set_error_callback(ctx_, [](void *, const char *) {}, nullptr);
    MU_TRY(ctx_, fz_register_document_handlers(ctx_));
}

Context::~Context() { fz_drop_context(ctx_); }

// --- Document -----------------------------------------------------------------------

Document::Document(Context &ctx, const std::string &path) : ctx_(ctx) {
    fz_context *c = ctx.get();
    fz_document *doc = nullptr;
    MU_TRY(c, doc = fz_open_document(c, path.c_str()));
    doc_ = doc;
    pdf_ = pdf_specifics(c, doc_);
}

Document::Document(Context &ctx, fz_document *doc) : ctx_(ctx) {
    doc_ = fz_keep_document(ctx.get(), doc);
    pdf_ = pdf_specifics(ctx.get(), doc_);
}

Document::~Document() { fz_drop_document(ctx_.get(), doc_); }

int Document::page_count() const {
    int n = 0;
    MU_TRY(ctx_.get(), n = fz_count_pages(ctx_.get(), doc_));
    return n;
}

std::optional<ExtractedImage> Document::extract_image(int xref) {
    fz_context *ctx = ctx_.get();
    if (!pdf_)
        return std::nullopt;
    ExtractedImage out;
    fz_buffer *res = nullptr;
    fz_image *img = nullptr;
    pdf_obj *obj = nullptr;
    const char *ext = "";
    int smask = 0, w = 0, h = 0, n = 0;
    fz_var(res);
    fz_var(img);
    fz_var(obj);
    fz_var(ext);
    fz_var(smask);
    fz_var(w);
    fz_var(h);
    fz_var(n);
    bool failed = false;
    fz_try(ctx) {
        int len = pdf_xref_len(ctx, pdf_);
        if (xref < 1 || xref >= len)
            fz_throw(ctx, FZ_ERROR_ARGUMENT, "bad xref");
        obj = pdf_new_indirect(ctx, pdf_, xref, 0);
        pdf_obj *subtype = pdf_dict_get(ctx, obj, PDF_NAME(Subtype));
        if (!pdf_name_eq(ctx, subtype, PDF_NAME(Image)))
            fz_throw(ctx, FZ_ERROR_ARGUMENT, "not an image");
        pdf_obj *o = pdf_dict_geta(ctx, obj, PDF_NAME(SMask), PDF_NAME(Mask));
        if (o)
            smask = pdf_to_num(ctx, o);
        img = pdf_load_image(ctx, pdf_, obj);
        fz_compressed_buffer *cbuf = fz_compressed_image_buffer(ctx, img);
        int type = cbuf ? cbuf->params.type : FZ_IMAGE_UNKNOWN;
        switch (type) {
        case FZ_IMAGE_BMP: ext = "bmp"; break;
        case FZ_IMAGE_GIF: ext = "gif"; break;
        case FZ_IMAGE_JPEG: ext = "jpeg"; break;
        case FZ_IMAGE_JPX: ext = "jpx"; break;
        case FZ_IMAGE_JXR: ext = "jxr"; break;
        case FZ_IMAGE_PNG: ext = "png"; break;
        case FZ_IMAGE_PNM: ext = "pnm"; break;
        case FZ_IMAGE_TIFF: ext = "tiff"; break;
        case FZ_IMAGE_PSD: ext = "psd"; break;
        default: ext = "n/a"; break;
        }
        if (!cbuf || type == FZ_IMAGE_JBIG2 || type == FZ_IMAGE_UNKNOWN || type < FZ_IMAGE_BMP) {
            res = fz_new_buffer_from_image_as_png(ctx, img, fz_default_color_params);
            ext = "png";
        } else if (type == FZ_IMAGE_JPEG && img->n == 4) {
            res = fz_new_buffer_from_image_as_jpeg(ctx, img, fz_default_color_params, 95, 1);
        } else {
            res = fz_keep_buffer(ctx, cbuf->buffer);
        }
        w = img->w;
        h = img->h;
        n = img->n;
    }
    fz_always(ctx) {
        pdf_drop_obj(ctx, obj);
        fz_drop_image(ctx, img);
    }
    fz_catch(ctx) { failed = true; }
    if (failed) {
        fz_drop_buffer(ctx, res);
        return std::nullopt;
    }
    unsigned char *data = nullptr;
    size_t size = fz_buffer_storage(ctx, res, &data);
    out.image.assign(reinterpret_cast<const char *>(data), size);
    fz_drop_buffer(ctx, res);
    out.ext = ext;
    out.smask = smask;
    out.width = w;
    out.height = h;
    out.colorspace_n = n;
    return out;
}

Raster Document::image_pixmap(int xref) {
    fz_context *ctx = ctx_.get();
    PixHolder pix{ctx};
    ImageHolder img{ctx};
    pdf_obj *ref = nullptr;
    fz_var(ref);
    bool failed = false;
    fz_try(ctx) {
        int len = pdf_xref_len(ctx, pdf_);
        if (xref < 1 || xref >= len)
            fz_throw(ctx, FZ_ERROR_ARGUMENT, "bad xref");
        ref = pdf_new_indirect(ctx, pdf_, xref, 0);
        pdf_obj *type = pdf_dict_get(ctx, ref, PDF_NAME(Subtype));
        if (!pdf_name_eq(ctx, type, PDF_NAME(Image)) && !pdf_name_eq(ctx, type, PDF_NAME(Alpha)) &&
            !pdf_name_eq(ctx, type, PDF_NAME(Luminosity)))
            fz_throw(ctx, FZ_ERROR_ARGUMENT, "is no image");
        img.img = pdf_load_image(ctx, pdf_, ref);
        fz_irect area = fz_infinite_irect;
        fz_matrix m = fz_make_matrix(static_cast<float>(img.img->w), 0, 0, static_cast<float>(img.img->h), 0, 0);
        pix.pix = fz_get_pixmap_from_image(ctx, img.img, &area, &m, nullptr, nullptr);
    }
    fz_always(ctx) { pdf_drop_obj(ctx, ref); }
    fz_catch(ctx) { failed = true; }
    if (failed)
        throw Error("cannot load image " + std::to_string(xref));
    return raster_of(ctx, pix.pix);
}

std::string Document::image_digest(int xref) {
    fz_context *ctx = ctx_.get();
    PixHolder pix{ctx};
    ImageHolder img{ctx};
    pdf_obj *ref = nullptr;
    fz_var(ref);
    unsigned char digest[16] = {0};
    bool failed = false;
    fz_try(ctx) {
        ref = pdf_new_indirect(ctx, pdf_, xref, 0);
        img.img = pdf_load_image(ctx, pdf_, ref);
        fz_irect area = fz_infinite_irect;
        fz_matrix m = fz_make_matrix(static_cast<float>(img.img->w), 0, 0, static_cast<float>(img.img->h), 0, 0);
        pix.pix = fz_get_pixmap_from_image(ctx, img.img, &area, &m, nullptr, nullptr);
        fz_md5_pixmap(ctx, pix.pix, digest);
    }
    fz_always(ctx) { pdf_drop_obj(ctx, ref); }
    fz_catch(ctx) { failed = true; }
    if (failed)
        throw Error("cannot load image " + std::to_string(xref));
    return md5_hex(digest);
}

std::optional<std::string> Document::image_with_mask_png(int xref, int smask) {
    fz_context *ctx = ctx_.get();
    PixHolder base{ctx}, nobase{ctx}, mask{ctx}, combined{ctx};
    ImageHolder img{ctx}, mimg{ctx};
    BufferHolder png{ctx};
    fz_output *out = nullptr;
    fz_var(out);
    pdf_obj *ref = nullptr, *mref = nullptr;
    fz_var(ref);
    fz_var(mref);
    bool failed = false;
    fz_try(ctx) {
        ref = pdf_new_indirect(ctx, pdf_, xref, 0);
        img.img = pdf_load_image(ctx, pdf_, ref);
        fz_irect area = fz_infinite_irect;
        fz_matrix m = fz_make_matrix(static_cast<float>(img.img->w), 0, 0, static_cast<float>(img.img->h), 0, 0);
        base.pix = fz_get_pixmap_from_image(ctx, img.img, &area, &m, nullptr, nullptr);
        fz_pixmap *color = base.pix;
        if (fz_pixmap_alpha(ctx, base.pix)) {
            // Pixmap(base, 0): the colour channels alone.
            fz_colorspace *cs = fz_pixmap_colorspace(ctx, base.pix);
            if (!cs)
                fz_throw(ctx, FZ_ERROR_ARGUMENT, "cannot drop alpha for 'NULL' colorspace");
            nobase.pix = fz_new_pixmap(ctx, cs, base.pix->w, base.pix->h, nullptr, 0);
            int n = fz_pixmap_colorants(ctx, base.pix);
            for (int y = 0; y < base.pix->h; ++y) {
                const unsigned char *s = base.pix->samples + static_cast<size_t>(y) * base.pix->stride;
                unsigned char *d = nobase.pix->samples + static_cast<size_t>(y) * nobase.pix->stride;
                for (int x = 0; x < base.pix->w; ++x) {
                    for (int k = 0; k < n; ++k)
                        *d++ = s[k];
                    s += base.pix->n;
                }
            }
            color = nobase.pix;
        }
        mref = pdf_new_indirect(ctx, pdf_, smask, 0);
        mimg.img = pdf_load_image(ctx, pdf_, mref);
        fz_irect marea = fz_infinite_irect;
        fz_matrix mm = fz_make_matrix(static_cast<float>(mimg.img->w), 0, 0, static_cast<float>(mimg.img->h), 0, 0);
        mask.pix = fz_get_pixmap_from_image(ctx, mimg.img, &marea, &mm, nullptr, nullptr);
        combined.pix = fz_new_pixmap_from_color_and_mask(ctx, color, mask.pix);
        // Pixmap.tobytes("png"), which takes only grey and RGB.
        png.buf = fz_new_buffer(ctx, static_cast<size_t>(fz_pixmap_stride(ctx, combined.pix)) *
                                         static_cast<size_t>(fz_pixmap_height(ctx, combined.pix)));
        out = fz_new_output_with_buffer(ctx, png.buf);
        fz_write_pixmap_as_png(ctx, out, combined.pix);
        fz_close_output(ctx, out);
    }
    fz_always(ctx) {
        fz_drop_output(ctx, out);
        pdf_drop_obj(ctx, ref);
        pdf_drop_obj(ctx, mref);
    }
    fz_catch(ctx) { failed = true; }
    if (failed)
        return std::nullopt;
    unsigned char *data = nullptr;
    size_t size = fz_buffer_storage(ctx, png.buf, &data);
    return std::string(reinterpret_cast<const char *>(data), size);
}

std::string Document::font_buffer(int xref) {
    fz_context *ctx = ctx_.get();
    if (!pdf_)
        return {};
    fz_buffer *buf = nullptr;
    pdf_obj *o = nullptr;
    fz_var(buf);
    fz_var(o);
    bool failed = false;
    fz_try(ctx) {
        o = pdf_load_object(ctx, pdf_, xref);
        pdf_obj *type = pdf_dict_get(ctx, o, PDF_NAME(Type));
        pdf_obj *subtype = pdf_dict_get(ctx, o, PDF_NAME(Subtype));
        if (pdf_name_eq(ctx, type, PDF_NAME(Font)) && !py::startswith(pdf_to_name(ctx, subtype), "CIDFontType")) {
            pdf_obj *desft = pdf_dict_get(ctx, o, PDF_NAME(DescendantFonts));
            pdf_obj *obj;
            if (desft) {
                obj = pdf_resolve_indirect(ctx, pdf_array_get(ctx, desft, 0));
                obj = pdf_dict_get(ctx, obj, PDF_NAME(FontDescriptor));
            } else {
                obj = pdf_dict_get(ctx, o, PDF_NAME(FontDescriptor));
            }
            pdf_obj *stream = nullptr;
            if (obj) {
                if (pdf_obj *f = pdf_dict_get(ctx, obj, PDF_NAME(FontFile)))
                    stream = f;
                if (pdf_obj *f = pdf_dict_get(ctx, obj, PDF_NAME(FontFile2)))
                    stream = f;
                if (pdf_obj *f = pdf_dict_get(ctx, obj, PDF_NAME(FontFile3)))
                    stream = f;
            }
            if (stream)
                buf = pdf_load_stream(ctx, stream);
        }
    }
    fz_always(ctx) { pdf_drop_obj(ctx, o); }
    fz_catch(ctx) { failed = true; }
    if (failed || !buf) {
        fz_drop_buffer(ctx, buf);
        return {};
    }
    unsigned char *data = nullptr;
    size_t n = fz_buffer_storage(ctx, buf, &data);
    std::string out(reinterpret_cast<const char *>(data), n);
    fz_drop_buffer(ctx, buf);
    return out;
}

std::string Document::font_name_from_buffer(const std::string &buffer) {
    fz_context *ctx = ctx_.get();
    fz_buffer *buf = nullptr;
    fz_font *font = nullptr;
    fz_var(buf);
    fz_var(font);
    std::string name;
    char copy[256] = "";
    bool failed = false;
    fz_try(ctx) {
        buf = fz_new_buffer_from_copied_data(ctx, reinterpret_cast<const unsigned char *>(buffer.data()), buffer.size());
        font = fz_new_font_from_buffer(ctx, nullptr, buf, 0, 0);
        std::strncpy(copy, fz_font_name(ctx, font), sizeof copy - 1);
    }
    fz_always(ctx) {
        fz_drop_font(ctx, font);
        fz_drop_buffer(ctx, buf);
    }
    fz_catch(ctx) { failed = true; }
    if (failed)
        throw Error("not a font");
    return copy;
}

std::pair<std::string, std::string> Document::xref_get_key(int xref, const std::string &key) {
    fz_context *ctx = ctx_.get();
    pdf_obj *obj = nullptr;
    fz_buffer *res = nullptr;
    fz_var(obj);
    fz_var(res);
    std::string type, text;
    char tbuf[64] = "";
    const char *kind = "unknown";
    bool have_text = false;
    bool failed = false;
    fz_try(ctx) {
        obj = pdf_load_object(ctx, pdf_, xref);
        pdf_obj *sub = pdf_dict_getp(ctx, obj, key.c_str());
        if (!sub) {
            kind = "null";
            std::strcpy(tbuf, "null");
            have_text = true;
        } else if (pdf_is_indirect(ctx, sub)) {
            kind = "xref";
            std::snprintf(tbuf, sizeof tbuf, "%i 0 R", pdf_to_num(ctx, sub));
            have_text = true;
        } else if (pdf_is_array(ctx, sub)) {
            kind = "array";
        } else if (pdf_is_dict(ctx, sub)) {
            kind = "dict";
        } else if (pdf_is_int(ctx, sub)) {
            kind = "int";
            std::snprintf(tbuf, sizeof tbuf, "%i", pdf_to_int(ctx, sub));
            have_text = true;
        } else if (pdf_is_real(ctx, sub)) {
            kind = "float";
        } else if (pdf_is_null(ctx, sub)) {
            kind = "null";
            std::strcpy(tbuf, "null");
            have_text = true;
        } else if (pdf_is_bool(ctx, sub)) {
            kind = "bool";
            std::strcpy(tbuf, pdf_to_bool(ctx, sub) ? "true" : "false");
            have_text = true;
        } else if (pdf_is_name(ctx, sub)) {
            kind = "name";
            std::snprintf(tbuf, sizeof tbuf, "/%s", pdf_to_name(ctx, sub));
            have_text = true;
        }
        if (!have_text) {
            res = fz_new_buffer(ctx, 512);
            fz_output *out = fz_new_output_with_buffer(ctx, res);
            fz_try(ctx) {
                pdf_print_obj(ctx, out, pdf_resolve_indirect(ctx, sub), 1, 0);
                fz_close_output(ctx, out);
            }
            fz_always(ctx) { fz_drop_output(ctx, out); }
            fz_catch(ctx) { fz_rethrow(ctx); }
        }
    }
    fz_always(ctx) { pdf_drop_obj(ctx, obj); }
    fz_catch(ctx) { failed = true; }
    if (failed) {
        fz_drop_buffer(ctx, res);
        throw Error("bad xref");
    }
    type = kind;
    if (have_text) {
        text = tbuf;
    } else if (res) {
        unsigned char *data = nullptr;
        size_t n = fz_buffer_storage(ctx, res, &data);
        text.assign(reinterpret_cast<const char *>(data), n);
    }
    fz_drop_buffer(ctx, res);
    return {type, text};
}

// --- Page -----------------------------------------------------------------------------

Page::Page(Document &doc, int number) : doc_(doc), number_(number) {
    fz_context *ctx = doc.ctx();
    fz_page *page = nullptr;
    MU_TRY(ctx, page = fz_load_page(ctx, doc.get(), number));
    page_ = page;
}

Page::~Page() { fz_drop_page(doc_.ctx(), page_); }

int Page::rotation() const {
    fz_context *ctx = doc_.ctx();
    pdf_page *pp = pdf_page_from_fz_page(ctx, page_);
    if (!pp)
        return 0;
    int rotate = 0;
    MU_TRY(ctx, rotate = pdf_to_int(ctx, pdf_dict_get_inheritable(ctx, pp->obj, PDF_NAME(Rotate))));
    while (rotate < 0)
        rotate += 360;
    while (rotate >= 360)
        rotate -= 360;
    if (rotate % 90 != 0)
        return 0;
    return rotate;
}

Rect Page::rect() const {
    fz_context *ctx = doc_.ctx();
    fz_rect r;
    MU_TRY(ctx, r = fz_bound_page(ctx, page_));
    return rect_of(r);
}

std::vector<RawBlock> Page::rawdict() {
    fz_context *ctx = doc_.ctx();
    StextHolder tp{ctx};
    {
        Derotate guard(ctx, page_, rotation());
        MU_TRY(ctx, tp.page = make_stext(ctx, page_, TEXTFLAGS_DICT));
    }
    std::vector<RawBlock> out;
    fz_rect tp_rect = tp.page->mediabox;
    int block_n = -1;
    for (fz_stext_block *block = tp.page->first_block; block; block = block->next) {
        if (block->type == FZ_STEXT_BLOCK_TEXT) {
            if (!(rects_overlap(tp_rect, block->bbox) || fz_is_infinite_rect(tp_rect)))
                continue;
            RawBlock b;
            b.type = 0;
            b.number = ++block_n;
            fz_rect block_rect = fz_empty_rect;
            for (fz_stext_line *line = block->u.t.first_line; line; line = line->next) {
                if (fz_is_empty_rect(fz_intersect_rect(tp_rect, line->bbox)) && !fz_is_infinite_rect(tp_rect))
                    continue;
                fz_rect line_rect;
                b.lines.push_back(make_line(ctx, line, tp_rect, line_rect));
                block_rect = fz_union_rect(block_rect, line_rect);
            }
            b.bbox = rect_of(block_rect);
            out.push_back(std::move(b));
        } else if (block->type == FZ_STEXT_BLOCK_IMAGE) {
            if (!(fz_contains_rect(tp_rect, block->bbox) || fz_is_infinite_rect(tp_rect)))
                continue;
            RawBlock b;
            b.type = 1;
            b.number = ++block_n;
            b.bbox = rect_of(block->bbox);
            out.push_back(std::move(b));
        }
    }
    return out;
}

std::vector<Rect> Page::image_block_boxes() {
    std::vector<Rect> out;
    for (const RawBlock &b : rawdict())
        if (b.type == 1)
            out.push_back(b.bbox);
    return out;
}

size_t Page::text_length_stripped() {
    fz_context *ctx = doc_.ctx();
    StextHolder tp{ctx};
    {
        Derotate guard(ctx, page_, rotation());
        MU_TRY(ctx, tp.page = make_stext(ctx, page_, TEXTFLAGS_TEXT));
    }
    fz_rect rect = tp.page->mediabox;
    std::u32string text;
    for (fz_stext_block *block = tp.page->first_block; block; block = block->next) {
        if (block->type != FZ_STEXT_BLOCK_TEXT)
            continue;
        int last_char = 0;
        for (fz_stext_line *line = block->u.t.first_line; line; line = line->next) {
            bool break_line = true;
            for (fz_stext_char *ch = line->first_char; ch; ch = ch->next) {
                fz_rect chbbox = char_bbox(ctx, line, ch);
                if (!ch->next && (line->flags & FZ_STEXT_LINE_FLAGS_JOINED)) {
                    break_line = false;
                } else if (fz_is_infinite_rect(rect) || rects_overlap(rect, chbbox)) {
                    last_char = ch->c;
                    char32_t c = static_cast<char32_t>(last_char);
                    if (c >= 0xD800 && c <= 0xDFFF)
                        c = 0xFFFD;
                    text += c;
                }
            }
            if (break_line && last_char != 10 && last_char > 0) {
                text += U'\n';
                last_char = 10;
            }
        }
        if (last_char != 10 && last_char > 0)
            text += U'\n';
    }
    return py::strip(text).size();
}

std::vector<Drawing> Page::drawings(bool extended) {
    fz_context *ctx = doc_.ctx();
    std::vector<Drawing> out;
    LineartState st;
    st.ctx = ctx;
    st.out = &out;
    st.clips = extended;
    LineartDevice *dev = nullptr;
    fz_var(dev);
    bool failed = false;
    char message[256] = "";
    Derotate guard(ctx, page_, rotation());
    fz_try(ctx) {
        dev = fz_new_derived_device(ctx, LineartDevice);
        dev->state = &st;
        dev->super.fill_path = dev_fill_path;
        dev->super.stroke_path = dev_stroke_path;
        dev->super.clip_path = dev_clip_path;
        dev->super.clip_stroke_path = dev_clip_stroke_path;
        dev->super.fill_text = dev_fill_text;
        dev->super.stroke_text = dev_stroke_text;
        dev->super.clip_text = dev_clip_text;
        dev->super.clip_stroke_text = dev_clip_stroke_text;
        dev->super.ignore_text = dev_ignore_text;
        dev->super.fill_shade = dev_fill_shade;
        dev->super.fill_image = dev_fill_image;
        dev->super.fill_image_mask = dev_fill_image_mask;
        dev->super.clip_image_mask = dev_clip_image_mask;
        dev->super.pop_clip = dev_pop_clip;
        dev->super.begin_group = dev_begin_group;
        dev->super.end_group = dev_end_group;
        fz_run_page(ctx, page_, &dev->super, fz_identity, nullptr);
        fz_close_device(ctx, &dev->super);
    }
    fz_always(ctx) { fz_drop_device(ctx, dev ? &dev->super : nullptr); }
    fz_catch(ctx) {
        failed = true;
        std::strncpy(message, fz_caught_message(ctx), sizeof message - 1);
    }
    if (failed)
        throw Error(message);
    if (st.failed)
        throw Error("out of memory while listing the drawings");
    return out;
}

std::vector<ImageInfo> Page::image_info_xrefs() {
    fz_context *ctx = doc_.ctx();
    if (!image_info_cache_) {
        StextHolder tp{ctx};
        {
            Derotate guard(ctx, page_, rotation());
            MU_TRY(ctx, tp.page = make_stext(ctx, page_, FZ_STEXT_PRESERVE_IMAGES));
        }
        std::vector<ImageInfo> infos;
        std::vector<std::string> digests;
        int block_n = -1;
        for (fz_stext_block *block = tp.page->first_block; block; block = block->next) {
            block_n += 1;
            if (block->type == FZ_STEXT_BLOCK_TEXT)
                continue;
            if (block->type != FZ_STEXT_BLOCK_IMAGE)
                continue;
            fz_image *img = block->u.i.image;
            PixHolder pix{ctx};
            unsigned char digest[16] = {0};
            MU_TRY(ctx, {
                fz_irect area = fz_infinite_irect;
                fz_matrix m = fz_make_matrix(static_cast<float>(img->w), 0, 0, static_cast<float>(img->h), 0, 0);
                pix.pix = fz_get_pixmap_from_image(ctx, img, &area, &m, nullptr, nullptr);
                fz_md5_pixmap(ctx, pix.pix, digest);
            });
            ImageInfo info;
            info.number = block_n;
            info.bbox = rect_of(block->bbox);
            fz_matrix t = block->u.i.transform;
            info.transform[0] = t.a;
            info.transform[1] = t.b;
            info.transform[2] = t.c;
            info.transform[3] = t.d;
            info.transform[4] = t.e;
            info.transform[5] = t.f;
            info.width = img->w;
            info.height = img->h;
            infos.push_back(info);
            digests.push_back(md5_hex(digest));
        }
        image_info_cache_ = infos;
        digests_ = digests;
    }
    std::vector<ImageInfo> infos = *image_info_cache_;
    if (!doc_.pdf())
        return infos;
    std::map<std::string, int> by_digest;
    for (int xref : image_xrefs()) {
        try {
            by_digest[doc_.image_digest(xref)] = xref;
        } catch (const Error &) {
            // Pixmap(doc, xref) raises in PyMuPDF too, and get_image_info
            // with it: an image MuPDF cannot read stops the whole listing.
            throw;
        }
    }
    for (size_t i = 0; i < infos.size(); ++i) {
        auto it = by_digest.find(digests_[i]);
        infos[i].xref = it == by_digest.end() ? 0 : it->second;
    }
    return infos;
}

std::vector<FontEntry> Page::fonts() {
    fz_context *ctx = doc_.ctx();
    std::vector<FontEntry> out;
    pdf_page *pp = pdf_page_from_fz_page(ctx, page_);
    if (!pp)
        return out;
    pdf_document *pdf = doc_.pdf();
    ScanState state;
    std::vector<FontEntry> *outp = &out;
    bool failed = false;
    fz_try(ctx) {
        pdf_obj *rsrc = pdf_dict_get_inheritable(ctx, pp->obj, PDF_NAME(Resources));
        if (rsrc) {
            scan_resources(ctx, pdf, rsrc, 1, 0, state, [&](pdf_obj *r, pdf_obj *, int sxref) {
                gather_fonts(ctx, pdf, pdf_dict_get(ctx, r, PDF_NAME(Font)), *outp, sxref);
            });
        }
    }
    fz_catch(ctx) { failed = true; }
    if (failed)
        throw Error("cannot list the fonts of page " + std::to_string(number_ + 1));
    return out;
}

std::vector<int> Page::image_xrefs() {
    fz_context *ctx = doc_.ctx();
    std::vector<int> out;
    pdf_page *pp = pdf_page_from_fz_page(ctx, page_);
    if (!pp)
        return out;
    pdf_document *pdf = doc_.pdf();
    ScanState state;
    std::vector<int> *outp = &out;
    bool failed = false;
    fz_try(ctx) {
        pdf_obj *rsrc = pdf_dict_get_inheritable(ctx, pp->obj, PDF_NAME(Resources));
        if (rsrc) {
            scan_resources(ctx, pdf, rsrc, 2, 0, state, [&](pdf_obj *, pdf_obj *xobj, int) {
                gather_images(ctx, xobj, *outp);
            });
        }
    }
    fz_catch(ctx) { failed = true; }
    if (failed)
        throw Error("cannot list the images of page " + std::to_string(number_ + 1));
    return out;
}

std::vector<Link> Page::links() {
    fz_context *ctx = doc_.ctx();
    std::vector<Link> out;
    fz_link *links = nullptr;
    MU_TRY(ctx, links = fz_load_links(ctx, page_));
    for (fz_link *ln = links; ln; ln = ln->next) {
        Link l;
        l.from = rect_of(ln->rect);
        l.uri = ln->uri ? ln->uri : "";
        bool external = ln->uri && fz_is_external_link(ctx, ln->uri);
        l.is_uri = external && !l.uri.empty() && !py::startswith(l.uri, "file:") &&
                   l.uri.find(':') != std::string::npos;
        out.push_back(std::move(l));
    }
    fz_drop_link(ctx, links);
    return out;
}

std::vector<Widget> Page::widgets() {
    fz_context *ctx = doc_.ctx();
    std::vector<Widget> out;
    pdf_page *pp = pdf_page_from_fz_page(ctx, page_);
    if (!pp)
        return out;
    for (pdf_annot *annot = pdf_first_widget(ctx, pp); annot; annot = pdf_next_widget(ctx, annot)) {
        Widget w;
        char value[512] = "";
        bool have = false;
        int type = 0;
        fz_rect r = fz_empty_rect;
        char type_string[64] = "";
        bool failed = false;
        fz_var(type);
        fz_var(r);
        fz_var(have);
        fz_var(failed);
        fz_try(ctx) {
            type = pdf_widget_type(ctx, annot);
            pdf_obj *obj = pdf_annot_obj(ctx, annot);
            const char *fvalue = nullptr;
            if (type == PDF_WIDGET_TYPE_RADIOBUTTON) {
                pdf_obj *as = pdf_dict_get(ctx, obj, PDF_NAME(AS));
                if (as)
                    fvalue = pdf_to_name(ctx, as);
            }
            if (!fvalue || !*fvalue)
                fvalue = pdf_field_value(ctx, obj);
            if (fvalue) {
                std::strncpy(value, fvalue, sizeof value - 1);
                have = true;
            }
            const char *ts = pdf_field_type_string(ctx, obj);
            if (ts)
                std::strncpy(type_string, ts, sizeof type_string - 1);
            r = pdf_bound_annot(ctx, annot);
        }
        fz_catch(ctx) { failed = true; }
        if (failed)
            continue;
        w.field_type = type;
        w.field_type_string = type_string;
        w.field_value = value;
        w.has_value = have;
        w.rect = rect_of(r);
        out.push_back(std::move(w));
    }
    return out;
}

Raster Page::render(double zoom, const std::optional<Rect> &clip) {
    fz_context *ctx = doc_.ctx();
    fz_display_list *dl = nullptr;
    fz_pixmap *pix = nullptr;
    fz_device *dev = nullptr;
    fz_var(dl);
    fz_var(pix);
    fz_var(dev);
    bool failed = false;
    char message[256] = "";
    fz_rect rclip = clip ? fz_make_rect(static_cast<float>(clip->x0), static_cast<float>(clip->y0),
                                        static_cast<float>(clip->x1), static_cast<float>(clip->y1))
                         : fz_infinite_rect;
    fz_matrix matrix = fz_scale(static_cast<float>(zoom), static_cast<float>(zoom));
    fz_try(ctx) {
        dl = fz_new_display_list_from_page(ctx, page_);
        fz_rect rect = fz_bound_display_list(ctx, dl);
        rect = fz_intersect_rect(rect, rclip);
        rect = fz_transform_rect(rect, matrix);
        fz_irect irect = fz_round_rect(rect);
        pix = fz_new_pixmap_with_bbox(ctx, fz_device_rgb(ctx), irect, nullptr, 0);
        fz_clear_pixmap_with_value(ctx, pix, 0xFF);
        if (!fz_is_infinite_rect(rclip)) {
            dev = fz_new_draw_device_with_bbox(ctx, matrix, pix, &irect);
            fz_run_display_list(ctx, dl, dev, fz_identity, rclip, nullptr);
        } else {
            dev = fz_new_draw_device(ctx, matrix, pix);
            fz_run_display_list(ctx, dl, dev, fz_identity, fz_infinite_rect, nullptr);
        }
        fz_close_device(ctx, dev);
    }
    fz_always(ctx) {
        fz_drop_device(ctx, dev);
        fz_drop_display_list(ctx, dl);
    }
    fz_catch(ctx) {
        failed = true;
        std::strncpy(message, fz_caught_message(ctx), sizeof message - 1);
        fz_drop_pixmap(ctx, pix);
    }
    if (failed)
        throw Error(message);
    Raster r = raster_of(ctx, pix);
    fz_drop_pixmap(ctx, pix);
    return r;
}

std::optional<Raster> decode_with_mupdf(Context &context, const std::string &data) {
    fz_context *ctx = context.get();
    fz_buffer *buf = nullptr;
    fz_image *img = nullptr;
    fz_pixmap *pix = nullptr;
    fz_var(buf);
    fz_var(img);
    fz_var(pix);
    bool failed = false;
    fz_try(ctx) {
        buf = fz_new_buffer_from_copied_data(ctx, reinterpret_cast<const unsigned char *>(data.data()), data.size());
        img = fz_new_image_from_buffer(ctx, buf);
        pix = fz_get_pixmap_from_image(ctx, img, nullptr, nullptr, nullptr, nullptr);
    }
    fz_always(ctx) {
        fz_drop_image(ctx, img);
        fz_drop_buffer(ctx, buf);
    }
    fz_catch(ctx) {
        failed = true;
        fz_drop_pixmap(ctx, pix);
    }
    if (failed)
        return std::nullopt;
    Raster r = raster_of(ctx, pix);
    fz_drop_pixmap(ctx, pix);
    return r;
}

} // namespace pdft::mu

namespace pdft::mu {

namespace {

// Text as PyMuPDF hands it to MuPDF: a Python string from bytes read
// Latin-1 fashion (JM_EscapeStrFromBuffer), encoded back in UTF-8.
std::string utf8_of_latin1(const std::string &bytes) {
    std::string out;
    out.reserve(bytes.size());
    for (unsigned char c : bytes) {
        if (c < 0x80) {
            out += static_cast<char>(c);
        } else {
            out += static_cast<char>(0xC0 | (c >> 6));
            out += static_cast<char>(0x80 | (c & 0x3F));
        }
    }
    return out;
}

// JM_object_to_buffer: pdf_print_obj into a buffer.
std::string print_object(fz_context *ctx, pdf_obj *obj, bool tight) {
    fz_buffer *res = nullptr;
    fz_output *out = nullptr;
    fz_var(res);
    fz_var(out);
    std::string text;
    const char *message = nullptr;
    fz_try(ctx) {
        res = fz_new_buffer(ctx, 512);
        out = fz_new_output_with_buffer(ctx, res);
        pdf_print_obj(ctx, out, obj, tight ? 1 : 0, 0);
        fz_close_output(ctx, out);
    }
    fz_always(ctx) { fz_drop_output(ctx, out); }
    fz_catch(ctx) { message = "cannot print the object"; }
    if (!message && res) {
        unsigned char *data = nullptr;
        size_t len = fz_buffer_storage(ctx, res, &data);
        text.assign(reinterpret_cast<const char *>(data), len);
    }
    fz_drop_buffer(ctx, res);
    if (message)
        throw Error(message);
    return text;
}

// JM_pdf_obj_from_str: the object a source text describes (new reference).
pdf_obj *parse_object(fz_context *ctx, pdf_document *pdf, const std::string &utf8) {
    fz_buffer *buf = nullptr;
    fz_stream *stm = nullptr;
    pdf_obj *obj = nullptr;
    pdf_lexbuf lexbuf;
    fz_var(buf);
    fz_var(stm);
    fz_var(obj);
    bool failed = false;
    pdf_lexbuf_init(ctx, &lexbuf, PDF_LEXBUF_SMALL);
    fz_try(ctx) {
        buf = fz_new_buffer_from_copied_data(ctx, reinterpret_cast<const unsigned char *>(utf8.data()), utf8.size());
        stm = fz_open_buffer(ctx, buf);
        obj = pdf_parse_stm_obj(ctx, pdf, stm, &lexbuf);
    }
    fz_always(ctx) {
        fz_drop_stream(ctx, stm);
        fz_drop_buffer(ctx, buf);
        pdf_lexbuf_fin(ctx, &lexbuf);
    }
    fz_catch(ctx) { failed = true; }
    if (failed)
        throw Error("cannot parse the object");
    return obj;
}

bool name_safe(const std::string &s, bool allow_slash) {
    for (unsigned char c : s) {
        if (c == 0 || std::strchr(" \t\n\r\x0b\x0c()<>[]{}%", c))
            return false;
        if (c == '/' && !allow_slash)
            return false;
    }
    return true;
}

} // namespace

std::string Document::xref_object(int xref, bool compressed) {
    fz_context *ctx = ctx_.get();
    pdf_obj *obj = nullptr;
    int len = 0;
    MU_TRY(ctx, len = pdf_xref_len(ctx, pdf_));
    if (xref < 1 || xref > len - 1)
        throw Error("bad xref");
    MU_TRY(ctx, obj = pdf_load_object(ctx, pdf_, xref));
    std::string text;
    try {
        text = print_object(ctx, pdf_resolve_indirect(ctx, obj), compressed);
    } catch (...) {
        pdf_drop_obj(ctx, obj);
        throw;
    }
    pdf_drop_obj(ctx, obj);
    return text;
}

std::optional<std::string> Document::xref_stream(int xref) {
    fz_context *ctx = ctx_.get();
    int len = 0;
    MU_TRY(ctx, len = pdf_xref_len(ctx, pdf_));
    if (xref < 1 || xref > len - 1)
        throw Error("bad xref");
    pdf_obj *obj = nullptr;
    fz_buffer *res = nullptr;
    fz_var(obj);
    fz_var(res);
    int is_stream = 0;
    bool failed = false;
    fz_try(ctx) {
        obj = pdf_new_indirect(ctx, pdf_, xref, 0);
        is_stream = pdf_is_stream(ctx, obj);
        if (is_stream)
            res = pdf_load_stream_number(ctx, pdf_, xref);
    }
    fz_always(ctx) { pdf_drop_obj(ctx, obj); }
    fz_catch(ctx) { failed = true; }
    if (failed) {
        fz_drop_buffer(ctx, res);
        throw Error("cannot read stream " + std::to_string(xref));
    }
    if (!is_stream)
        return std::nullopt;
    unsigned char *data = nullptr;
    size_t size = fz_buffer_storage(ctx, res, &data);
    std::string out(reinterpret_cast<const char *>(data), size);
    fz_drop_buffer(ctx, res);
    return out;
}

void Document::xref_set_key(int xref, const std::string &key, const std::string &value) {
    fz_context *ctx = ctx_.get();
    if (key.empty() || !name_safe(key, true))
        throw Error("bad 'key'");
    if (value.empty() || (value[0] == '/' && !name_safe(value.substr(1), false)))
        throw Error("bad 'value'");
    int len = 0;
    MU_TRY(ctx, len = pdf_xref_len(ctx, pdf_));
    if (xref < 1 || xref > len - 1)
        throw Error("bad xref");
    static const char *const eyecatcher = "fitz: replace me!";
    std::string skey = key.substr(key.rfind('/') == std::string::npos ? 0 : key.rfind('/') + 1);
    pdf_obj *obj = nullptr;
    MU_TRY(ctx, obj = pdf_load_object(ctx, pdf_, xref));
    std::string objstr;
    try {
        // JM_set_object_value. Keys with a path are not used by the program
        // and are refused rather than half supported.
        if (key.find('/') != std::string::npos)
            throw Error("key paths are not supported");
        MU_TRY(ctx, pdf_dict_putp_drop(ctx, obj, key.c_str(), pdf_new_text_string(ctx, eyecatcher)));
        objstr = print_object(ctx, obj, true);
    } catch (...) {
        pdf_drop_obj(ctx, obj);
        throw;
    }
    pdf_drop_obj(ctx, obj);
    std::string text = utf8_of_latin1(objstr);
    std::string nullval = "/" + skey + "(" + eyecatcher + ")";
    std::string newval = "/" + skey + " " + value;
    size_t at = text.find(nullval);
    if (at != std::string::npos)
        text.replace(at, nullval.size(), newval);
    pdf_obj *new_obj = parse_object(ctx, pdf_, text);
    bool failed = false;
    fz_try(ctx) { pdf_update_object(ctx, pdf_, xref, new_obj); }
    fz_always(ctx) { pdf_drop_obj(ctx, new_obj); }
    fz_catch(ctx) { failed = true; }
    if (failed)
        throw Error("cannot update object " + std::to_string(xref));
}

void Document::update_object(int xref, const std::string &text) {
    fz_context *ctx = ctx_.get();
    int len = 0;
    MU_TRY(ctx, len = pdf_xref_len(ctx, pdf_));
    if (xref < 1 || xref > len - 1)
        throw Error("bad xref");
    pdf_obj *new_obj = parse_object(ctx, pdf_, text);
    bool failed = false;
    fz_try(ctx) { pdf_update_object(ctx, pdf_, xref, new_obj); }
    fz_always(ctx) { pdf_drop_obj(ctx, new_obj); }
    fz_catch(ctx) { failed = true; }
    if (failed)
        throw Error("cannot update object " + std::to_string(xref));
}

} // namespace pdft::mu

namespace pdft::mu {

bool run_guarded(fz_context *ctx, void (*body)(void *), void *arg, char *message, std::size_t size) noexcept {
    bool ok = true;
    fz_var(ok);
    fz_try(ctx) { body(arg); }
    fz_catch(ctx) {
        ok = false;
        if (size) {
            std::strncpy(message, fz_caught_message(ctx), size - 1);
            message[size - 1] = '\0';
        }
    }
    return ok;
}

} // namespace pdft::mu
