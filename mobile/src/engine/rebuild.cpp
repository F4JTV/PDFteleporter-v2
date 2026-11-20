// Copyright (C) 2026 The PDF Teleporter contributors
// Portions derived from PDF Teleporter by F1GBD (Jean-Louis Naudin),
// ADRASEC 77 - FNRASEC, author of the original program and of the .psdi
// format.
// SPDX-License-Identifier: GPL-3.0-only
//
// Archive -> PDF (engine.py, "_line_points" to "archive_to_pdf"): the pages
// drawn again from the manifest, rectangles first, then the pictures, the
// text set span by span at its place, ticks and links; or, for a page-image
// archive, one picture a page.

#include "internal.h"

#include "codecs.h"
#include "image.h"
#include "pdfout.h"
#include "pyutil.h"

extern "C" {
#include <mupdf/fitz.h>
}

#include <charconv>
#include <chrono>
#include <cmath>
#include <set>
#include <stdexcept>

namespace pdft::engine {

namespace {

using pdfout::Shape;
using pdfout::TextWriter;
using json::Array;

// Relative costs of the rebuild's units of work (engine.py, _COST_REBUILD_*).
constexpr double kCostRebuildLine = 1.0;
constexpr double kCostRebuildPage = 20.0;
constexpr double kCostRebuildSavePerLine = 0.05;
constexpr double kCostRebuildSavePerPage = 0.5;

constexpr int kTextLayout = 1;
constexpr double kBoxAscent = 0.8;
constexpr double kBoxDescent = 0.21;
constexpr double kFitMin = 0.5;
constexpr double kFitSlack = 0.25;
constexpr double kFitSlackWholePoints = 1.0;
constexpr double kFitTolerance = 0.02;
constexpr int kJustifyMinGaps = 2;
constexpr double kJustifyMaxSpace = 1.5;
constexpr double kJustifyMinReach = 0.2;
constexpr double kJustifyMinReachWholePoints = 1.0;
constexpr double kAdjacentEm = 0.15;
constexpr double kFontBoxBaseline = 0.776;
constexpr long long kMaxDecodedPixels = 50000000;

const char *const kShapedCss = "* { margin:0; padding:0; box-sizing:border-box; }"
                               "html, body { margin:0; padding:0; line-height:1; }";

double now_seconds() {
    using namespace std::chrono;
    return duration<double>(steady_clock::now().time_since_epoch()).count();
}

// --- values as the manifest gives them -------------------------------------------------------

bool is_number(const Value &v) { return v.is_int() || v.is_float(); }

// _is_box: four numbers, booleans excluded.
bool is_box(const Value *v) {
    if (!v || !v->is_array() || v->size() != 4)
        return false;
    for (const Value &x : v->as_array())
        if (!is_number(x))
            return false;
    return true;
}

Rect rect_of(const Value &box) { return Rect(num(box[0]), num(box[1]), num(box[2]), num(box[3])); }

// _objects: the entries of container[key] that are objects.
std::vector<const Value *> objects(const Value *container, std::string_view key) {
    std::vector<const Value *> out;
    if (!container || !container->is_object())
        return out;
    const Value *value = container->find(key);
    if (!value || !value->is_array())
        return out;
    for (const Value &entry : value->as_array())
        if (entry.is_object())
            out.push_back(&entry);
    return out;
}

// _number: a finite number, else the default.
double number_or(const Value *value, double fallback) {
    if (value && is_number(*value) && std::isfinite(value->as_double()))
        return value->as_double();
    return fallback;
}

// int(text, 16) for the two characters of a colour component.
std::optional<int> hex_byte(std::u32string s) {
    s = py::strip(s);
    bool negative = false;
    if (!s.empty() && (s[0] == U'+' || s[0] == U'-')) {
        negative = s[0] == U'-';
        s.erase(0, 1);
    }
    if (s.empty())
        return std::nullopt;
    int value = 0;
    for (char32_t c : s) {
        int d;
        if (c >= U'0' && c <= U'9')
            d = static_cast<int>(c - U'0');
        else if (c >= U'a' && c <= U'f')
            d = static_cast<int>(c - U'a') + 10;
        else if (c >= U'A' && c <= U'F')
            d = static_cast<int>(c - U'A') + 10;
        else
            return std::nullopt;
        value = value * 16 + d;
    }
    return negative ? -value : value;
}

// _hex_to_rgb: "#rrggbb" as a 0..1 triple, or none.
std::optional<std::array<double, 3>> hex_to_rgb(const Value *value) {
    if (!value || !value->is_string())
        return std::nullopt;
    std::u32string s = py::to_u32(value->as_string());
    if (s.size() != 7 || s[0] != U'#')
        return std::nullopt;
    std::array<double, 3> out{};
    for (int i = 0; i < 3; ++i) {
        auto byte = hex_byte(s.substr(static_cast<size_t>(1 + 2 * i), 2));
        if (!byte)
            return std::nullopt;
        out[static_cast<size_t>(i)] = *byte / 255.0;
    }
    return out;
}

// _colour_rgb: an int RRGGBB as a 0..1 triple; anything else is black.
std::array<double, 3> colour_rgb(const Value &value) {
    long long v = value.is_int() ? value.as_int() : 0;
    return {static_cast<double>((v >> 16) & 255) / 255, static_cast<double>((v >> 8) & 255) / 255,
            static_cast<double>(v & 255) / 255};
}

// _line_points: the points of a "line" entry, in either of its shapes.
std::vector<std::array<double, 2>> line_points(const Value &entry) {
    const Value *coords = entry.find("p");
    if (!coords)
        coords = entry.find("r");
    if (!coords || !coords->is_array())
        return {};
    std::vector<const Value *> values;
    const Array &items = coords->as_array();
    bool pairs = !items.empty();
    for (const Value &item : items)
        if (!item.is_array())
            pairs = false;
    if (pairs) {
        for (const Value &pair : items)
            for (size_t k = 0; k < pair.size() && k < 2; ++k)
                values.push_back(&pair[k]);
    } else {
        for (const Value &v : items)
            values.push_back(&v);
    }
    for (const Value *v : values)
        if (!(is_number(*v) || v->is_bool()))   // isinstance(v, (int, float)): bools are ints
            return {};
    std::vector<std::array<double, 2>> points;
    for (size_t i = 0; i + 1 < values.size(); i += 2)
        points.push_back({values[i]->as_double(), values[i + 1]->as_double()});
    return points;
}

// --- text, set at its place ----------------------------------------------------------------

bool whole_points(const Value &box) {
    for (const Value &v : box.as_array())
        if (!(v.is_int() || py::is_integer(v.as_double())))
            return false;
    return true;
}
double fit_slack(const Value &box) { return whole_points(box) ? kFitSlackWholePoints : kFitSlack; }
double justify_reach(const Value &box) { return whole_points(box) ? kJustifyMinReachWholePoints : kJustifyMinReach; }

double span_baseline(const Value &box, double size, double page_h) {
    double y0 = num(box[1]), y1 = num(box[3]);
    if (y1 >= page_h - 0.6)
        return y0 + kBoxAscent * size;
    double height = y1 - y0;
    if (height <= 1.25 * size)
        return y1 - kBoxDescent * size;
    return y0 + kFontBoxBaseline * height;
}

// _SHAPED_RANGES: scripts whose letters join, or that run right to left.
bool needs_shaping(const std::u32string &text) {
    static const std::pair<char32_t, char32_t> ranges[] = {
        {0x0590, 0x0DFF}, {0x0E00, 0x109F}, {0x1780, 0x17FF}, {0xFB1D, 0xFDFF}, {0xFE70, 0xFEFF}};
    for (char32_t c : text)
        for (const auto &[low, high] : ranges)
            if (low <= c && c <= high)
                return true;
    return false;
}

std::string first_strong(const std::u32string &text) {
    for (char32_t c : text) {
        std::string kind = py::bidirectional(c);
        if (kind == "L" || kind == "R" || kind == "AL")
            return kind;
    }
    return "";
}

// Python's str() of a number the manifest gave: 8, or 10.5, or 11.0.
std::string python_str(const Value &v) {
    if (v.is_int())
        return std::to_string(v.as_int());
    return json::float_repr(v.as_double());
}

// max() and min() of two Python floats: the first wins a tie.
double pymax(double a, double b) { return b > a ? b : a; }
double pymin(double a, double b) { return b < a ? b : a; }

class TextPainter {
public:
    TextPainter(pdfout::Document &doc, pdfout::Page &page, ClassFonts &fonts)
        : doc_(doc), page_(page), fonts_(fonts), width_(page.rect().width()), height_(page.rect().height()) {}

    ClassFonts &fonts() { return fonts_; }
    double width() const { return width_; }
    double height() const { return height_; }

    void draw(const std::u32string &text, const std::shared_ptr<Font> &font, double size, double x, double y,
              const Value &colour) {
        try {
            if (!writer_ || !json::equal(colour, colour_)) {
                commit();
                writer_ = std::make_unique<TextWriter>(doc_.ctx(), page_.rect());
                colour_ = colour;
            }
            writer_->append(x, y, py::to_utf8(text), font->get(), size);
        } catch (const std::exception &) {
            // One span MuPDF refuses costs that span, never the page.
        }
    }

    // Arabic, Hebrew, Indic: shaped through HTML.
    void shaped(const std::u32string &text, const std::string &family, const std::string &style, const Value &size_value,
                double size, double x, double baseline, double right, const Value &colour) {
        Rect rect(x - 0.5, baseline - kBoxAscent * size, pymax(right, x + size) + 0.5, baseline + 0.6 * size);
        std::string css_family = family == "t" ? "serif" : family == "m" ? "monospace" : "sans-serif";
        std::string weight = style == "B" || style == "BI" ? "bold" : "normal";
        std::string slant = style == "I" || style == "BI" ? "italic" : "normal";
        std::u32string escaped = py::replace(py::replace(py::replace(text, U"&", U"&amp;"), U"<", U"&lt;"), U">", U"&gt;");
        auto rgb = colour_rgb(colour);
        char hex[8];
        std::snprintf(hex, sizeof hex, "%02llx%02llx%02llx", py::round_int(255 * rgb[0]), py::round_int(255 * rgb[1]),
                      py::round_int(255 * rgb[2]));
        std::string kind = first_strong(text);
        std::string direction = kind == "R" || kind == "AL" ? "rtl" : "ltr";
        std::string html = "<div dir=\"" + direction + "\" style=\"font-family:" + css_family +
                           ";font-size:" + python_str(size_value) + "pt;font-weight:" + weight +
                           ";font-style:" + slant + ";color:#" + hex + ";white-space:pre\">" +
                           py::to_utf8(escaped) + "</div>";
        // The text before it goes to the page first, in reading order.
        commit();
        try {
            doc_.insert_htmlbox(page_, rect, html, kShapedCss, kFitMin);
        } catch (const std::exception &) {
        }
    }

    void commit() {
        std::unique_ptr<TextWriter> writer = std::move(writer_);
        if (writer) {
            try {
                writer->write_text(doc_, page_, colour_rgb(colour_));
            } catch (const std::exception &) {
            }
        }
    }

private:
    pdfout::Document &doc_;
    pdfout::Page &page_;
    ClassFonts &fonts_;
    double width_, height_;
    std::unique_ptr<TextWriter> writer_;
    Value colour_;
};

// _paint_line: the spans of one line, each at the place its box gives it.
void paint_line(TextPainter &painter, const Value &line, bool hung) {
    const Value *line_box = line.find("b");
    if (!is_box(line_box))
        return;
    std::vector<const Value *> spans;
    if (const Value *s = line.find("s"); s && s->is_array())
        for (const Value &span : s->as_array())
            if (span.is_object()) {
                const Value *t = span.find("t");
                if (t && t->is_string() && !t->as_string().empty())
                    spans.push_back(&span);
            }
    std::optional<double> pen, source_end;
    const Value default_size(8);
    const Value default_colour(0);
    for (const Value *span : spans) {
        std::u32string text = py::replace(py::to_u32(span->at("t").as_string()), U"\t", U" ");
        const Value *size_value = span->find("sz");
        if (!size_value)
            size_value = &default_size;
        if (!is_number(*size_value) || !(0 < size_value->as_double() && size_value->as_double() < 2000))
            size_value = &default_size;
        double size = size_value->as_double();
        const Value *fa = span->find("fa");
        const Value *st = span->find("f");
        std::string family = fa ? (fa->is_string() ? fa->as_string() : "s") : "s";
        std::string style = st ? (st->is_string() ? st->as_string() : "R") : "R";
        std::shared_ptr<Font> font = painter.fonts().get(family, style);
        const Value *colour = span->find("c");
        if (!colour)
            colour = &default_colour;
        bool own = is_box(span->find("b"));
        const Value &box = own ? span->at("b") : *line_box;
        double x = own || !pen ? num(box[0]) : *pen;
        if (own && pen && source_end && std::abs(num(box[0]) - *source_end) <= kAdjacentEm * size)
            x = *pen;
        if (own)
            source_end = num(box[2]);
        else
            source_end.reset();
        double baseline = hung ? (num(box[1]) + kBoxAscent * size + num(box[3]) - kBoxDescent * size) / 2
                               : span_baseline(box, size, painter.height());
        std::u32string visible = py::rstrip(text);
        if (py::strip(visible).empty()) {
            if (pen)
                *pen += font->text_length(text, size);
            continue;
        }
        double box_right = num(box[2]);
        if (needs_shaping(visible)) {
            double right = box_right < painter.width() - 0.6 ? box_right : painter.width();
            painter.shaped(visible, family, style, *size_value, size, x, baseline, right, *colour);
            pen = x + font->text_length(text, size);
            continue;
        }
        double natural = font->text_length(visible, size);
        double full = font->text_length(text, size);
        pen = x + full;
        if (natural <= 0 || box_right >= painter.width() - 0.6 || !(own || spans.size() == 1)) {
            painter.draw(text, font, size, x, baseline, *colour);
            continue;
        }
        double width = box_right - x - (hung ? full - natural : 0.0);
        bool too_wide = hung ? natural > width + fit_slack(box) : width < natural * (1 - kFitTolerance);
        if (too_wide) {
            double scale = pymax(kFitMin, width / natural);
            painter.draw(text, font, size * scale, x, baseline, *colour);
            pen = x + full * scale;
            continue;
        }
        std::u32string body = py::lstrip_char(visible, U' ');
        std::vector<std::u32string> words = py::split_char(body, U' ');
        long long gaps = static_cast<long long>(words.size()) - 1;
        double space = font->text_length(U" ", size);
        double reach = hung ? width - natural : box_right - x - pymax(full, natural);
        double extra = gaps >= kJustifyMinGaps && reach > justify_reach(box) ? reach / static_cast<double>(gaps) : 0.0;
        if (extra <= 0 || extra > kJustifyMaxSpace * space) {
            painter.draw(text, font, size, x, baseline, *colour);
            continue;
        }
        double cursor = x + static_cast<double>(visible.size() - body.size()) * space;
        for (long long index = 0; index < static_cast<long long>(words.size()); ++index) {
            std::u32string word = words[static_cast<size_t>(index)];
            word += index < gaps ? std::u32string(U" ") : text.substr(visible.size());
            if (!py::strip(word).empty())
                painter.draw(word, font, size, cursor, baseline, *colour);
            cursor += font->text_length(word, size) + (index < gaps ? extra : 0.0);
        }
        pen = pymax(*pen, cursor);
    }
}

// _draw_checkmarks: ticks drawn as polylines, with no font.
void draw_checkmarks(pdfout::Document &doc, pdfout::Page &page, const std::vector<const Value *> &checks) {
    if (checks.empty())
        return;
    Shape shape(page);
    bool drawn = false;
    for (const Value *box : checks) {
        double x0 = num((*box)[0]), y0 = num((*box)[1]), x1 = num((*box)[2]), y1 = num((*box)[3]);
        double w = x1 - x0, h = y1 - y0;
        if (w <= 0 || h <= 0)
            continue;
        shape.draw_polyline({{x0 + 0.20 * w, y0 + 0.55 * h}, {x0 + 0.42 * w, y0 + 0.78 * h},
                             {x0 + 0.82 * w, y0 + 0.18 * h}});
        double width = pymax(0.8, pymin(2.2, 0.16 * pymin(w, h)));
        shape.finish(width, std::array<double, 3>{0, 0, 0}, std::nullopt, false);
        drawn = true;
    }
    if (drawn)
        shape.commit(doc);
}

// --- glyph widths --------------------------------------------------------------------------

unsigned long be(const std::string &d, size_t from, size_t to) {
    // int.from_bytes(program[from:to], "big"), the slice cut at the end.
    unsigned long v = 0;
    for (size_t i = from; i < to && i < d.size(); ++i)
        v = (v << 8) | static_cast<unsigned char>(d[i]);
    return v;
}

struct Metrics {
    unsigned long upem;
    std::vector<unsigned long> advances;
    unsigned long glyphs;
};

// _sfnt_advances: units per em and advances of a TrueType/OpenType program.
std::optional<Metrics> sfnt_advances(const std::string &program) {
    if (program.size() < 12)
        return std::nullopt;
    std::string magic = program.substr(0, 4);
    if (magic != std::string("\x00\x01\x00\x00", 4) && magic != "OTTO" && magic != "true")
        return std::nullopt;
    unsigned long count = be(program, 4, 6);
    std::map<std::string, std::pair<unsigned long, unsigned long>> tables;
    for (unsigned long index = 0; index < count; ++index) {
        size_t at = 12 + 16 * index;
        if (at + 16 > program.size())
            return std::nullopt;
        tables[program.substr(at, 4)] = {be(program, at + 8, at + 12), be(program, at + 12, at + 16)};
    }
    auto head = tables.find("head"), hhea = tables.find("hhea"), maxp = tables.find("maxp"), hmtx = tables.find("hmtx");
    if (head == tables.end() || hhea == tables.end() || maxp == tables.end() || hmtx == tables.end())
        return std::nullopt;
    unsigned long upem = be(program, head->second.first + 18, head->second.first + 20);
    unsigned long metrics = be(program, hhea->second.first + 34, hhea->second.first + 36);
    unsigned long glyphs = be(program, maxp->second.first + 4, maxp->second.first + 6);
    unsigned long hmtx_at = hmtx->second.first, hmtx_length = hmtx->second.second;
    if (!upem || !metrics || 4 * metrics > hmtx_length || hmtx_at + 4 * metrics > program.size())
        return std::nullopt;
    Metrics out{upem, {}, glyphs > metrics ? glyphs : metrics};
    out.advances.reserve(metrics);
    for (unsigned long i = 0; i < metrics; ++i)
        out.advances.push_back(be(program, hmtx_at + 4 * i, hmtx_at + 4 * i + 2));
    return out;
}

// _WIDTH_TOKEN: "[", "]", or a number.
std::vector<std::string> width_tokens(const std::string &text) {
    std::vector<std::string> tokens;
    size_t i = 0, n = text.size();
    auto digit = [&](size_t k) { return k < n && text[k] >= '0' && text[k] <= '9'; };
    while (i < n) {
        char c = text[i];
        if (c == '[' || c == ']') {
            tokens.emplace_back(1, c);
            ++i;
            continue;
        }
        size_t start = i, k = i;
        if (text[k] == '-')
            ++k;
        if (digit(k)) {
            while (digit(k))
                ++k;
            if (k < n && text[k] == '.') {
                ++k;
                while (digit(k))
                    ++k;
            }
            tokens.push_back(text.substr(start, k - start));
            i = k;
            continue;
        }
        if (k < n && text[k] == '.' && digit(k + 1)) {
            ++k;
            while (digit(k))
                ++k;
            tokens.push_back(text.substr(start, k - start));
            i = k;
            continue;
        }
        ++i;
    }
    return tokens;
}

// float(token), whatever the locale. Past the range of a double, the array
// is refused, as std::stod would.
double token_float(const std::string &t) {
    if (t == "[" || t == "]")
        throw std::invalid_argument("not a number");
    double v = py::parse_double(t.c_str());
    if (!std::isfinite(v))
        throw std::out_of_range("number out of range");
    return v;
}

// int(float(token)), for a cid a long long holds.
long long token_int(const std::string &t) {
    double v = std::trunc(token_float(t));
    if (!(std::fabs(v) < 9.2e18))
        throw std::out_of_range("number out of range");
    return static_cast<long long>(v);
}

// _parse_widths: a /W array as {cid: width}, in the order given.
std::vector<std::pair<long long, double>> parse_widths(const std::string &text) {
    std::vector<std::string> tokens = width_tokens(text);
    if (!tokens.empty() && tokens.front() == "[" && tokens.back() == "]")
        tokens = std::vector<std::string>(tokens.begin() + 1, tokens.end() - 1);
    std::map<long long, size_t> index;
    std::vector<std::pair<long long, double>> widths;
    auto put = [&](long long cid, double w) {
        auto found = index.find(cid);
        if (found != index.end()) {
            widths[found->second].second = w;
        } else {
            index[cid] = widths.size();
            widths.emplace_back(cid, w);
        }
    };
    size_t i = 0;
    while (i < tokens.size()) {
        long long first = token_int(tokens[i]);
        if (i + 1 < tokens.size() && tokens[i + 1] == "[") {
            i += 2;
            while (i < tokens.size() && tokens[i] != "]") {
                put(first, token_float(tokens[i]));
                ++first;
                ++i;
            }
            ++i;
        } else if (i + 2 < tokens.size()) {
            long long last = token_int(tokens[i + 1]);
            double width = token_float(tokens[i + 2]);
            if (last - first > 0xFFFF)
                throw std::invalid_argument("width range too long");
            for (long long cid = first; cid <= last; ++cid)
                put(cid, width);
            i += 3;
        } else {
            throw std::invalid_argument("truncated widths array");
        }
    }
    return widths;
}

// _width_number: f"{value:.3f}" without trailing zeros.
std::string width_number(double value) {
    // Not "%.3f": printf() would write a decimal comma in a French locale.
    char buf[400];
    auto res = std::to_chars(buf, buf + sizeof buf, value, std::chars_format::fixed, 3);
    std::string s(buf, res.ec == std::errc() ? res.ptr : buf);
    while (!s.empty() && s.back() == '0')
        s.pop_back();
    while (!s.empty() && s.back() == '.')
        s.pop_back();
    return s.empty() ? "0" : s;
}

// _format_widths
std::string format_widths(const std::map<long long, double> &widths) {
    std::vector<std::pair<long long, std::vector<double>>> runs;
    for (const auto &[cid, w] : widths) {
        if (!runs.empty() && cid == runs.back().first + static_cast<long long>(runs.back().second.size()))
            runs.back().second.push_back(w);
        else
            runs.push_back({cid, {w}});
    }
    std::vector<std::string> parts;
    for (const auto &[first, run] : runs) {
        std::vector<std::string> listed;
        size_t start = 0;
        while (start < run.size()) {
            size_t end = start;
            while (end + 1 < run.size() && run[end + 1] == run[start])
                ++end;
            if (end - start >= 2) {
                if (!listed.empty()) {
                    std::string joined;
                    for (const auto &l : listed)
                        joined += (joined.empty() ? "" : " ") + l;
                    parts.push_back(std::to_string(first + static_cast<long long>(start) -
                                                   static_cast<long long>(listed.size())) +
                                    " [" + joined + "]");
                    listed.clear();
                }
                parts.push_back(std::to_string(first + static_cast<long long>(start)) + " " +
                                std::to_string(first + static_cast<long long>(end)) + " " + width_number(run[start]));
            } else {
                for (size_t k = start; k <= end; ++k)
                    listed.push_back(width_number(run[k]));
            }
            start = end + 1;
        }
        if (!listed.empty()) {
            std::string joined;
            for (const auto &l : listed)
                joined += (joined.empty() ? "" : " ") + l;
            parts.push_back(std::to_string(first + static_cast<long long>(run.size()) -
                                           static_cast<long long>(listed.size())) +
                            " [" + joined + "]");
        }
    }
    std::string out = "[";
    for (size_t k = 0; k < parts.size(); ++k)
        out += (k ? " " : "") + parts[k];
    return out + "]";
}

// _reference: the object number a key refers to.
std::optional<int> reference(mu::Document &doc, int xref, const std::string &key) {
    auto [kind, value] = doc.xref_get_key(xref, key);
    if (kind != "xref" && kind != "array")
        return std::nullopt;
    // re.search(r"(\d+) 0 R", value)
    size_t i = 0;
    while (i < value.size()) {
        if (value[i] >= '0' && value[i] <= '9') {
            size_t j = i;
            while (j < value.size() && value[j] >= '0' && value[j] <= '9')
                ++j;
            if (value.compare(j, 4, " 0 R") == 0)
                return std::stoi(value.substr(i, j - i));
            i = j;
        } else {
            ++i;
        }
    }
    return std::nullopt;
}

// _exact_widths: each composite font's widths as its program gives them.
int exact_widths(pdfout::Document &out) {
    mu::Document &doc = out.view();
    int changed = 0;
    std::set<int> seen;
    for (int pno = 0; pno < out.page_count(); ++pno) {
        for (const auto &font : mu::Page(doc, pno).fonts()) {
            if (seen.count(font.xref) || font.subtype != "Type0")
                continue;
            seen.insert(font.xref);
            auto descendant = reference(doc, font.xref, "DescendantFonts");
            if (!descendant)
                continue;
            auto gid_map = doc.xref_get_key(*descendant, "CIDToGIDMap");
            if (gid_map.first != "null" && !(gid_map.first == "name" && gid_map.second == "/Identity"))
                continue;
            auto descriptor = reference(doc, *descendant, "FontDescriptor");
            if (!descriptor)
                continue;
            std::optional<std::string> program;
            for (const char *key : {"FontFile2", "FontFile3"}) {
                if (auto stream = reference(doc, *descriptor, key)) {
                    program = doc.xref_stream(*stream);
                    break;
                }
            }
            auto metrics = sfnt_advances(program ? *program : std::string());
            if (!metrics)
                continue;
            auto [kind_w, value] = doc.xref_get_key(*descendant, "W");
            std::optional<int> array_xref;
            std::string text;
            if (kind_w == "xref") {
                array_xref = std::stoi(value.substr(0, value.find(' ')));
                text = doc.xref_object(*array_xref, true);
            } else if (kind_w == "array") {
                text = value;
            } else {
                continue;
            }
            std::vector<std::pair<long long, double>> widths;
            try {
                widths = parse_widths(text);
            } catch (const std::invalid_argument &) {
                continue;
            } catch (const std::out_of_range &) {
                continue;
            }
            std::map<long long, double> exact;
            bool same = true;
            const auto &advances = metrics->advances;
            for (const auto &[cid, width] : widths) {
                long long at = cid < static_cast<long long>(advances.size()) - 1 ? cid
                                                                                  : static_cast<long long>(advances.size()) - 1;
                if (at < 0)
                    at += static_cast<long long>(advances.size());
                if (at < 0)
                    throw std::out_of_range("list index out of range");
                double advance = static_cast<double>(advances[static_cast<size_t>(at)] * 1000) /
                                 static_cast<double>(metrics->upem);
                double w = cid < static_cast<long long>(metrics->glyphs) && std::abs(advance - width) < 1 ? advance : width;
                exact[cid] = w;
                if (!(std::abs(w - width) < 5e-4))
                    same = false;
            }
            if (same)
                continue;
            std::string array = format_widths(exact);
            if (!array_xref)
                doc.xref_set_key(*descendant, "W", array);
            else
                doc.update_object(*array_xref, array);
            ++changed;
        }
    }
    return changed;
}

// --- pictures ------------------------------------------------------------------------------

// _pixmap_from: an AVIF or WebP payload decoded here, MuPDF reading neither.
pdfout::Pixels pixmap_from(const std::string &payload) {
    std::string kind = psdi::payload_kind(payload);
    std::string upper = kind;
    for (auto &c : upper)
        c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    std::optional<img::Image> image;
    try {
        image = img::open(payload);
    } catch (const std::exception &e) {
        throw psdi::PsdiError("Unreadable " + upper + " image: " + e.what());
    }
    if (!image)
        throw psdi::PsdiError("Unreadable " + upper + " image: cannot identify image file");
    if (static_cast<long long>(image->width()) * image->height() > kMaxDecodedPixels)
        throw psdi::PsdiError("Image of " + std::to_string(image->width()) + "x" + std::to_string(image->height()) +
                              " pixels, above the " + std::to_string(kMaxDecodedPixels) + " pixel ceiling");
    try {
        image->load();
    } catch (const std::exception &e) {
        throw psdi::PsdiError("Unreadable " + upper + " image: " + e.what());
    }
    if (image->mode() != img::Mode::L && image->mode() != img::Mode::RGB)
        image = image->convert(img::Mode::RGB);
    pdfout::Pixels pixels;
    pixels.width = image->width();
    pixels.height = image->height();
    pixels.components = image->mode() == img::Mode::L ? 1 : 3;
    pixels.samples = image->tobytes();
    return pixels;
}

// _place_image: JPEG and PNG to MuPDF as they are, AVIF and WebP as pixels.
void place_image(pdfout::Document &doc, pdfout::Page &page, const Rect &rect, const std::string &payload) {
    std::string kind = psdi::payload_kind(payload);
    if (kind == "avif" || kind == "webp")
        doc.insert_image(page, rect, pixmap_from(payload));
    else
        doc.insert_image(page, rect, payload);
}

bool is_empty(const Rect &r) { return r.x0 >= r.x1 || r.y0 >= r.y1; }

// --- the two rebuilds ----------------------------------------------------------------------

struct Rebuilt {
    std::string pdf;
    long pages = 0;
    bool crc_ok = true;
    std::string mode;
    std::string source;
};

Rebuilt rebuild_struct(const std::string &data, const ProgressFn &progress_cb, bool compact,
                       const std::atomic<bool> *cancel) {
    psdi::StructArchive parsed = compact ? psdi::read_compact_struct(data) : psdi::read_struct(data);
    const Value &manifest = parsed.manifest;
    std::map<std::string, const std::string *> images;
    for (const auto &[key, payload] : parsed.images)
        images[key] = &payload;

    const Array &pages = manifest.at("pages").as_array();
    const Array &page_sizes = manifest.at("page_sizes").as_array();
    const long long page_total = static_cast<long long>(pages.size());
    std::vector<long long> line_counts;
    long long line_sum = 0;
    for (const Value &page_data : pages) {
        long long n = 0;
        for (const Value *block : objects(&page_data, "tb"))
            n += static_cast<long long>(objects(block, "l").size());
        line_counts.push_back(n);
        line_sum += n;
    }
    Progress progress(progress_cb,
                      static_cast<double>(line_sum) * (kCostRebuildLine + kCostRebuildSavePerLine) +
                          static_cast<double>(page_total) * kCostRebuildPage,
                      cancel);

    mu::Context ctx;
    pdfout::Document doc(ctx);
    ClassFonts fonts(ctx.get());
    const Value *tl = manifest.find("tl");
    bool hung = tl && json::equal(*tl, Value(kTextLayout));

    for (long long page_no = 1; page_no <= page_total; ++page_no) {
        const Value &page_data = pages[static_cast<size_t>(page_no - 1)];
        long long page_idx = page_data.at("pn").as_int() - 1;
        if (page_idx < 0)
            page_idx += static_cast<long long>(page_sizes.size());
        if (page_idx < 0 || page_idx >= static_cast<long long>(page_sizes.size()))
            throw psdi::PsdiError("list index out of range");
        const Value &size = page_sizes[static_cast<size_t>(page_idx)];
        pdfout::Page &page = doc.new_page(num(size.at("w")), num(size.at("h")));
        std::string label = "Recomposition de la page " + std::to_string(page_no) + "/" + std::to_string(page_total);
        progress.phase(label + "…");

        // Rectangles first: backgrounds and cell fills, under everything.
        Shape shape(page);
        for (const Value *entry : objects(&page_data, "dr")) {
            const Value *kind = entry->find("t");
            if (kind && kind->is_string() && kind->as_string() == "rect") {
                const Value *r = entry->find("r");
                if (!is_box(r))
                    continue;
                const Value *w = entry->find("w");
                double line_width = w ? number_or(w, 0) : 0;
                shape.draw_rect(rect_of(*r));
                shape.finish(line_width > 0.1 ? line_width : 0, hex_to_rgb(entry->find("c")),
                             hex_to_rgb(entry->find("f")), true);
            } else if (kind && kind->is_string() && kind->as_string() == "line") {
                auto points = line_points(*entry);
                if (points.size() < 2)
                    continue;
                shape.draw_polyline(points);
                const Value *w = entry->find("w");
                double width = w ? number_or(w, 0.5) : 0.5;
                if (width < 0)
                    width = 0.5;
                if (!entry->contains("p"))
                    width = pymax(width, 0.3);
                auto colour = hex_to_rgb(entry->find("c"));
                if (!colour)
                    colour = std::array<double, 3>{0, 0, 0};
                shape.finish(width, colour, std::nullopt, false);
            }
        }
        shape.commit(doc);

        for (const Value *ref : objects(&page_data, "ir")) {
            const Value *key = ref->find("x");
            std::string name;
            if (key && key->is_string())
                name = key->as_string();
            else if (key && key->is_int())
                name = std::to_string(key->as_int());
            auto found = name.empty() ? images.end() : images.find(name);
            const Value *b = ref->find("b");
            if (found == images.end() || found->second->empty() || !is_box(b))
                continue;
            Rect rect = rect_of(*b);
            if (is_empty(rect) || rect.is_infinite())
                continue;   // a sliver rounded to nothing
            try {
                place_image(doc, page, rect, *found->second);
            } catch (const std::exception &) {
                // An undecodable payload costs that image, not the page.
            }
        }
        progress.advance(kCostRebuildPage, label + "…");

        const long long line_total = line_counts[static_cast<size_t>(page_no - 1)];
        long long drawn = 0;
        TextPainter painter(doc, page, fonts);
        for (const Value *block : objects(&page_data, "tb")) {
            for (const Value *line : objects(block, "l")) {
                paint_line(painter, *line, hung);
                ++drawn;
                progress.advance(kCostRebuildLine,
                                 label + " — texte " + std::to_string(drawn) + "/" + std::to_string(line_total) + "…");
            }
        }
        painter.commit();

        std::vector<const Value *> checks;
        if (const Value *ck = page_data.find("ck"); ck && ck->is_array())
            for (const Value &box : ck->as_array())
                if (is_box(&box))
                    checks.push_back(&box);
        draw_checkmarks(doc, page, checks);

        for (const Value *link : objects(&page_data, "lk")) {
            const Value *b = link->find("b");
            const Value *u = link->find("u");
            if (!is_box(b) || !u || !u->is_string())
                continue;
            try {
                doc.insert_link_uri(page, rect_of(*b), u->as_string());
            } catch (const std::exception &) {
            }
        }
    }

    progress.phase("Enregistrement du PDF…");
    // Carlito whole is 600 to 800 KB a style; subset, a few dozen.
    try {
        doc.subset_fonts();
    } catch (const std::exception &) {
    }
    try {
        exact_widths(doc);
    } catch (const std::exception &) {
        // The glyphs then sit where MuPDF's widths put them.
    }
    Rebuilt out;
    out.pdf = doc.save(true, 4);
    progress.finish("PDF enregistré");
    out.pages = static_cast<long>(page_total);
    out.crc_ok = parsed.crc_ok;
    out.mode = MODE_STRUCT;
    if (const Value *src = manifest.find("src"); src && src->is_string())
        out.source = src->as_string();
    return out;
}

Rebuilt rebuild_image(const std::string &data, const ProgressFn &progress_cb, const std::atomic<bool> *cancel) {
    psdi::ImageArchive parsed = psdi::read_image(data);
    const long long total = static_cast<long long>(parsed.pages.size());
    Progress progress(progress_cb, static_cast<double>(total) * (kCostRebuildPage + kCostRebuildSavePerPage), cancel);
    mu::Context ctx;
    pdfout::Document doc(ctx);
    progress.phase("Recomposition des pages (0/" + std::to_string(total) + ")…");
    for (long long i = 0; i < total; ++i) {
        const psdi::ImagePage &p = parsed.pages[static_cast<size_t>(i)];
        pdfout::Page &page = doc.new_page(p.width, p.height);
        place_image(doc, page, page.rect(), p.payload);
        progress.advance(kCostRebuildPage,
                         "Recomposition des pages (" + std::to_string(i + 1) + "/" + std::to_string(total) + ")…");
    }
    progress.phase("Enregistrement du PDF…");
    Rebuilt out;
    out.pdf = doc.save(false, 4);
    progress.finish("PDF enregistré");
    out.pages = static_cast<long>(total);
    out.mode = MODE_IMAGE;
    return out;
}

} // namespace

RebuildReport archive_to_pdf(const std::string &archive, const ProgressFn &progress, const std::atomic<bool> *cancel) {
    const double started = now_seconds();
    int version = psdi::peek_version(archive);
    report(progress, 0, PROGRESS_SCALE, "Lecture de l'archive (" + std::to_string(archive.size() / 1024) + " ko)…");
    Rebuilt rebuilt;
    if (version == psdi::ARCHIVE_VERSION_STRUCT || version == psdi::ARCHIVE_VERSION_COMPACT_STRUCT)
        rebuilt = rebuild_struct(archive, progress, version == psdi::ARCHIVE_VERSION_COMPACT_STRUCT, cancel);
    else if (version == psdi::ARCHIVE_VERSION_IMAGE || version == psdi::ARCHIVE_VERSION_COMPACT_IMAGE)
        rebuilt = rebuild_image(archive, progress, cancel);
    else
        throw psdi::PsdiError("Unsupported archive version: " + std::to_string(version));
    report(progress, PROGRESS_SCALE, PROGRESS_SCALE, "Recomposition terminée");

    RebuildReport info;
    info.pdf = std::move(rebuilt.pdf);
    info.version = version;
    info.pages = rebuilt.pages;
    info.compact = psdi::is_compact_version(version);
    info.mode = rebuilt.mode;
    info.crc_ok = rebuilt.crc_ok;
    info.source = rebuilt.source;
    info.rebuild_time = py::round_to(now_seconds() - started, 2);
    return info;
}

} // namespace pdft::engine
