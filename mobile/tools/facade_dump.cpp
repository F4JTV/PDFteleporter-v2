// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// `pdft-cli dump all PDF PAGE`: what the MuPDF layer reports for a page, in
// the structure tests/dump_pymupdf.py prints from PyMuPDF.

#include "facade_dump.h"

#include "json.h"
#include "mupdf_facade.h"
#include "pyutil.h"

#include <iostream>

namespace pdft {

using json::Value;

static Value rect(const Rect &r) { return Value(json::Array{r.x0, r.y0, r.x1, r.y1}); }
static Value point(const Point &p) { return Value(json::Array{p.x, p.y}); }

static Value item(const mu::PathItem &it) {
    json::Array a;
    switch (it.kind) {
    case mu::PathItem::Line:
        a = {Value("l"), point(it.p[0]), point(it.p[1])};
        break;
    case mu::PathItem::Curve:
        a = {Value("c"), point(it.p[0]), point(it.p[1]), point(it.p[2]), point(it.p[3])};
        break;
    case mu::PathItem::RectItem:
        a = {Value("re"), rect(it.rect), Value(it.orientation)};
        break;
    case mu::PathItem::QuadItem:
        a = {Value("qu"), Value(json::Array{point(it.quad.ul), point(it.quad.ur), point(it.quad.ll),
                                            point(it.quad.lr)})};
        break;
    }
    return Value(a);
}

static Value colour(const std::optional<std::vector<double>> &c) {
    if (!c)
        return Value();
    json::Array a;
    for (double v : *c)
        a.push_back(v);
    return Value(a);
}

static Value drawing(const mu::Drawing &d) {
    Value out = Value::object();
    out["type"] = d.type;
    if (d.type != "group") {
        json::Array items;
        for (const auto &it : d.items)
            items.push_back(item(it));
        out["items"] = Value(items);
    }
    if (d.type.rfind("clip", 0) != 0)
        out["rect"] = rect(d.rect);
    else
        out["scissor"] = rect(d.scissor);
    if (d.type == "f" || d.type == "s" || d.type == "fs") {
        out["fill"] = colour(d.fill);
        out["color"] = colour(d.color);
    }
    if (d.width)
        out["width"] = *d.width;
    if (d.close_path)
        out["closePath"] = *d.close_path;
    if (d.even_odd)
        out["even_odd"] = *d.even_odd;
    if (d.type == "f" || d.type == "fs")
        out["fill_opacity"] = d.fill_opacity;
    if (d.type == "s" || d.type == "fs")
        out["stroke_opacity"] = d.stroke_opacity;
    if (d.seqno >= 0)
        out["seqno"] = Value(static_cast<long long>(d.seqno));
    if (d.has_level)
        out["level"] = Value(static_cast<long long>(d.level));
    return out;
}

int dump_page(const std::string &path, int number) {
    mu::Context ctx;
    mu::Document doc(ctx, path);
    mu::Page page(doc, number);
    Value out = Value::object();
    out["rotation"] = page.rotation();
    out["rect"] = rect(page.rect());
    out["text_len"] = Value(static_cast<long long>(page.text_length_stripped()));

    json::Array blocks;
    for (const auto &b : page.rawdict()) {
        Value block = Value::object();
        block["type"] = b.type;
        block["number"] = b.number;
        block["bbox"] = rect(b.bbox);
        if (b.type == 0) {
            json::Array lines;
            for (const auto &l : b.lines) {
                Value line = Value::object();
                line["wmode"] = l.wmode;
                line["dir"] = point(l.dir);
                line["bbox"] = rect(l.bbox);
                json::Array spans;
                for (const auto &s : l.spans) {
                    Value span = Value::object();
                    span["size"] = s.size;
                    span["flags"] = s.flags;
                    span["char_flags"] = s.char_flags;
                    span["font"] = s.font;
                    span["color"] = Value(static_cast<long long>(s.color));
                    span["alpha"] = s.alpha;
                    span["ascender"] = s.ascender;
                    span["descender"] = s.descender;
                    span["origin"] = point(s.origin);
                    span["bbox"] = rect(s.bbox);
                    json::Array chars;
                    for (const auto &c : s.chars) {
                        Value ch = Value::object();
                        ch["c"] = py::to_utf8(c.c);
                        ch["origin"] = point(c.origin);
                        ch["bbox"] = rect(c.bbox);
                        ch["synthetic"] = c.synthetic;
                        chars.push_back(ch);
                    }
                    span["chars"] = Value(chars);
                    spans.push_back(span);
                }
                line["spans"] = Value(spans);
                lines.push_back(line);
            }
            block["lines"] = Value(lines);
        }
        blocks.push_back(block);
    }
    out["rawdict"] = Value(blocks);

    json::Array image_blocks;
    for (const auto &r : page.image_block_boxes())
        image_blocks.push_back(rect(r));
    out["image_blocks"] = Value(image_blocks);

    for (bool extended : {false, true}) {
        json::Array list;
        for (const auto &d : page.drawings(extended))
            list.push_back(drawing(d));
        out[extended ? "drawings_ext" : "drawings"] = Value(list);
    }

    json::Array info;
    for (const auto &i : page.image_info_xrefs()) {
        Value v = Value::object();
        v["number"] = i.number;
        v["bbox"] = rect(i.bbox);
        v["transform"] = Value(json::Array{i.transform[0], i.transform[1], i.transform[2], i.transform[3],
                                           i.transform[4], i.transform[5]});
        v["width"] = i.width;
        v["height"] = i.height;
        v["xref"] = i.xref;
        info.push_back(v);
    }
    out["image_info"] = Value(info);

    json::Array fonts;
    for (const auto &f : page.fonts())
        fonts.push_back(Value(json::Array{Value(f.xref), Value(f.ext), Value(f.subtype), Value(f.basefont),
                                          Value(f.refname), Value(f.encoding), Value(f.referencer)}));
    out["fonts"] = Value(fonts);
    json::Array images;
    for (int x : page.image_xrefs())
        images.push_back(x);
    out["images"] = Value(images);
    json::Array links;
    for (const auto &l : page.links())
        links.push_back(Value::object({{"from", rect(l.from)}, {"uri", Value(l.is_uri ? l.uri : std::string())}, {"is_uri", Value(l.is_uri)}}));
    out["links"] = Value(links);
    json::Array widgets;
    for (const auto &w : page.widgets())
        widgets.push_back(Value::object({{"field_type", Value(w.field_type)},
                                         {"field_value", Value(w.field_value)},
                                         {"rect", rect(w.rect)}}));
    out["widgets"] = Value(widgets);
    std::cout << json::dumps(out) << "\n";
    return 0;
}

} // namespace pdft
