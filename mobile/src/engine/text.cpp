// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// Text: fonts and their classes, the spaces MuPDF guesses, the runs a span
// is cut into, and the page entry built from them (engine.py, from
// "_is_unreadable" to "_extract_page").

#include "internal.h"

#include "pyutil.h"
#include "symbols.h"

#include <algorithm>
#include <cmath>
#include <charconv>
#include <set>

namespace pdft::engine {

namespace {

using mu::RawBlock;
using mu::RawChar;
using mu::RawLine;
using mu::RawSpan;

// --- characters -----------------------------------------------------------------------------

// The ligatures LibreOffice leaves as private or odd code points, in the
// order _normalize_text replaces them.
const std::pair<char32_t, const char32_t *> kLigatures[] = {
    {0xFB00, U"ff"}, {0xFB01, U"fi"}, {0xFB02, U"fl"}, {0xFB03, U"ffi"}, {0xFB04, U"ffl"},
    {0xFB05, U"ft"}, {0xFB06, U"st"}, {0x019F, U"ti"}, {0x01A9, U"tt"},
};

const char32_t *ligature(char32_t c) {
    for (const auto &[code, text] : kLigatures)
        if (code == c)
            return text;
    return nullptr;
}

// char.strip() is not empty
bool visible_char(char32_t c) { return !py::isspace(c); }

bool has_visible(const std::u32string &s) {
    for (char32_t c : s)
        if (visible_char(c))
            return true;
    return false;
}

constexpr double kUnreadableRatio = 0.5;

bool is_unreadable(const std::u32string &text) {
    size_t visible = 0, lost = 0;
    for (char32_t c : text) {
        if (py::isspace(c))
            continue;
        ++visible;
        if (c < 0x20 || (c >= 0x7F && c < 0xA0))
            return true;
        if (c == 0xFFFD)
            ++lost;
    }
    if (!visible)
        return false;
    return static_cast<double>(lost) / static_cast<double>(visible) >= kUnreadableRatio;
}

std::u32string normalize_text(std::u32string text) {
    for (const auto &[code, replacement] : kLigatures)
        text = py::replace(text, std::u32string(1, code), replacement);
    if (text.find(0xFFFD) == std::u32string::npos)
        return text;
    // list(text), whose items may become two letters as they are replaced
    std::vector<std::u32string> chars;
    for (char32_t c : text)
        chars.emplace_back(1, c);
    for (size_t i = 0; i < chars.size(); ++i) {
        if (chars[i] != U"�")
            continue;
        std::u32string nxt = i + 1 < chars.size() ? chars[i + 1] : U"";
        std::u32string before;
        for (size_t k = i >= 2 ? i - 2 : 0; k < i; ++k)
            before += chars[k];
        if (nxt == U"é" || nxt == U"è" || nxt == U"ê")
            chars[i] = U"tt";
        else if (nxt == U"e" && before == U"lu")
            chars[i] = U"tt";
        else
            chars[i] = U"ti";
    }
    std::u32string out;
    for (const auto &c : chars)
        out += c;
    return out;
}

// --- fonts ----------------------------------------------------------------------------------

const char *const kMonoHints[] = {"courier", "mono", "consolas", "menlo", "cour", "lucida console",
                                  "lucidaconsole", "cmtt", "inconsolata", "monaco", "andale",
                                  "typewriter", "fixedsys", "letter gothic", "lettergothic"};
const char *const kCalibriHints[] = {"calibri", "carlito"};
const char *const kSerifHints[] = {
    "times", "serif", "roman", "cambria", "georgia", "garamond", "tinos", "liberation serif", "freeserif",
    "dejavu serif", "noto serif", "nimbusrom", "cmr", "cmbx", "cmti", "cmsl", "cmmi", "lmroman", "sfrm", "sfbx",
    "sfti", "book antiqua", "bookantiqua", "palatino", "century", "baskerville", "bookman", "minion", "caslon",
    "constantia", "charter", "utopia", "didot", "bodoni", "goudy", "stix", "libertin", "crimson", "merriweather",
    "caladea", "gelasio", "sabon", "perpetua", "cochin", "plantin",
};

template <size_t N>
bool any_hint(const std::string &lower, const char *const (&hints)[N]) {
    for (const char *hint : hints)
        if (lower.find(hint) != std::string::npos)
            return true;
    return false;
}

std::pair<std::string, std::string> classify_font(const std::string &font_name) {
    std::string lower = py::lower(font_name);
    bool bold = lower.find("bold") != std::string::npos;
    bool italic = lower.find("italic") != std::string::npos || lower.find("oblique") != std::string::npos;
    std::string key = bold && italic ? "BI" : bold ? "B" : italic ? "I" : "R";
    std::string family;
    if (any_hint(lower, kMonoHints) && lower.find("monotype") == std::string::npos)
        family = "m";
    else if (any_hint(lower, kCalibriHints))
        family = "c";
    else if (lower.find("sans") == std::string::npos && any_hint(lower, kSerifHints))
        family = "t";
    else
        family = "s";
    return {key, family};
}

// re.fullmatch(r"(?:CIDFont\+)?(?:F|T|TT|C|R|Font)?\d+(?:_\d+)?", name, re.I)
bool placeholder_font(const std::string &name) {
    std::string s = py::lower_ascii(name);
    size_t pos = 0;
    if (s.compare(0, 8, "cidfont+") == 0)
        pos = 8;
    auto digits_from = [&](size_t at) {
        size_t start = at;
        while (at < s.size() && s[at] >= '0' && s[at] <= '9')
            ++at;
        return at > start ? at : std::string::npos;
    };
    auto rest_ok = [&](size_t at) {
        size_t end = digits_from(at);
        if (end == std::string::npos)
            return false;
        if (end == s.size())
            return true;
        if (s[end] != '_')
            return false;
        size_t end2 = digits_from(end + 1);
        return end2 == s.size();
    };
    // The optional prefix: try each alternative, as the regex backtracks.
    for (const char *prefix : {"font", "tt", "f", "t", "c", "r", ""}) {
        size_t n = std::char_traits<char>::length(prefix);
        if (s.compare(pos, n, prefix) == 0 && rest_ok(pos + n))
            return true;
    }
    return false;
}

// re.match(r"[A-Z]{6}\+", name)
bool subset_tag(const std::string &name) {
    if (name.size() < 7)
        return false;
    for (int i = 0; i < 6; ++i)
        if (name[static_cast<size_t>(i)] < 'A' || name[static_cast<size_t>(i)] > 'Z')
            return false;
    return name[6] == '+';
}

// _font_programs: real names of the placeholder fonts of a page.
std::map<std::string, std::string> font_programs(mu::Page &page, mu::Document &doc, Faces &faces) {
    std::map<std::string, std::string> names;
    for (const auto &entry : page.fonts()) {
        int xref = entry.xref;
        const std::string &basefont = entry.basefont;
        std::string short_name = subset_tag(basefont) ? basefont.substr(7) : basefont;
        if (!placeholder_font(short_name))
            continue;
        if (!faces.programs.count(xref)) {
            std::optional<std::string> name;
            try {
                std::string buffer = doc.font_buffer(xref);
                if (!buffer.empty())
                    name = doc.font_name_from_buffer(buffer);
            } catch (const std::exception &) {
            }
            faces.programs[xref] = name;
        }
        const auto &cached = faces.programs[xref];
        if (cached && !cached->empty()) {
            names.emplace(short_name, *cached);
            names.emplace(basefont, *cached);
        }
    }
    return names;
}

std::pair<std::string, std::string> classify_span_font(const RawSpan &span,
                                                       const std::map<std::string, std::string> &programs) {
    auto real = programs.find(span.font);
    auto [key, family] = classify_font(real != programs.end() ? real->second : span.font);
    int flags = span.has_flags ? span.flags : 0;
    bool bold = key.find('B') != std::string::npos || (flags & mu::TEXT_FONT_BOLD);
    bool italic = key.find('I') != std::string::npos || (flags & mu::TEXT_FONT_ITALIC);
    key = bold && italic ? "BI" : bold ? "B" : italic ? "I" : "R";
    if (family == "s" && (flags & mu::TEXT_FONT_MONOSPACED))
        family = "m";
    return {key, family};
}

// _font_key
std::string font_key(std::string name) {
    size_t plus = name.rfind('+');
    if (plus != std::string::npos)
        name = name.substr(plus + 1);
    std::string squeezed;
    for (char c : name)
        if (c != ' ' && c != '-' && c != '_')
            squeezed += c;
    for (const char *style : {"BoldItalic", "BoldOblique", "Italic", "Oblique", "Bold", "Regular", "Book",
                              "Roman", "Medium", "Light"})
        if (py::endswith(squeezed, style))
            squeezed.erase(squeezed.size() - std::char_traits<char>::length(style));
    return py::lower(squeezed);
}

std::string raw_font_name(const std::string &name) {
    size_t plus = name.rfind('+');
    std::string tail = plus == std::string::npos ? name : name.substr(plus + 1);
    std::string squeezed;
    for (char c : tail)
        if (c != ' ' && c != '-')
            squeezed += c;
    return py::lower(squeezed);
}

std::set<std::string> fonts_without_unicode(mu::Page &page, mu::Document &doc) {
    std::set<std::string> names;
    for (const auto &font : page.fonts()) {
        if (font.subtype != "Type0" || !py::startswith(font.encoding, "Identity"))
            continue;
        bool has_table;
        try {
            has_table = doc.xref_get_key(font.xref, "ToUnicode").first != "null";
        } catch (const std::exception &) {
            has_table = true;
        }
        if (has_table)
            continue;
        names.insert(font_key(font.basefont));
        std::string raw = raw_font_name(font.basefont);
        if (!raw.empty())
            names.insert(raw);
    }
    return names;
}

bool font_matches(const std::string &span_font, const std::set<std::string> &bad_fonts) {
    std::string key = font_key(span_font);
    if (bad_fonts.count(key))
        return true;
    std::string raw = raw_font_name(span_font);
    for (const std::string &bad : bad_fonts) {
        // len() counts characters, not bytes
        if (py::to_u32(bad).size() >= 6 &&
            (py::startswith(raw, bad) || py::startswith(bad, raw) || py::startswith(key, bad) ||
             py::startswith(bad, key)))
            return true;
    }
    return false;
}

std::u32string span_text(const RawSpan &span) {
    std::u32string text;
    for (const auto &c : span.chars)
        text += c.c;
    return text;
}

bool span_is_lost(const RawSpan &span, const std::set<std::string> &bad_fonts) {
    if (!span.font.empty() && !bad_fonts.empty() && font_matches(span.font, bad_fonts))
        return true;
    return is_unreadable(span_text(span));
}

// --- the spaces MuPDF guesses -----------------------------------------------------------------

bool is_horizontal(const RawLine &line) { return line.wmode == 0 && line.dir.x > 0.999; }

constexpr size_t kPitchMinPairs = 20;
constexpr double kPitchTolerance = 0.05;
constexpr double kPitchShare = 0.8;
constexpr size_t kPitchMinLetters = 8;
constexpr double kPitchWidthRange = 1.0 / 3;

using FontSizeKey = std::pair<std::string, double>;

double median(std::vector<double> values) {
    std::sort(values.begin(), values.end());
    size_t n = values.size();
    if (n % 2 == 1)
        return values[n / 2];
    return (values[n / 2 - 1] + values[n / 2]) / 2;
}

std::map<FontSizeKey, double> fixed_pitch_fonts(const std::vector<RawBlock> &raw) {
    struct Step {
        double step, width;
        char32_t letter;
    };
    std::vector<std::pair<FontSizeKey, std::vector<Step>>> pairs;  // insertion order
    auto slot = [&](const FontSizeKey &key) -> std::vector<Step> & {
        for (auto &p : pairs)
            if (p.first.first == key.first && p.first.second == key.second)
                return p.second;
        pairs.push_back({key, {}});
        return pairs.back().second;
    };
    for (const auto &block : raw)
        for (const auto &line : block.lines) {
            if (!is_horizontal(line))
                continue;
            for (const auto &span : line.spans) {
                double size = span.size;
                if (!(size > 0))
                    continue;
                std::vector<const RawChar *> glyphs;
                for (const auto &c : span.chars)
                    if (!c.synthetic)
                        glyphs.push_back(&c);
                for (size_t i = 0; i + 1 < glyphs.size(); ++i) {
                    const RawChar &first = *glyphs[i], &second = *glyphs[i + 1];
                    if (!(py::isalpha(first.c) && py::isalpha(second.c)))
                        continue;
                    double step = (second.origin.x - first.origin.x) / size;
                    if (step > 0) {
                        double width = (first.bbox.x1 - first.bbox.x0) / size;
                        slot({span.font, size}).push_back({step, width, first.c});
                    }
                }
            }
        }
    std::map<FontSizeKey, double> pitches;
    for (const auto &[key, found] : pairs) {
        if (found.size() < kPitchMinPairs)
            continue;
        std::vector<double> steps;
        for (const auto &s : found)
            steps.push_back(s.step);
        double pitch = median(steps);
        std::vector<std::pair<double, char32_t>> regular;
        for (const auto &s : found)
            if (std::fabs(s.step - pitch) <= kPitchTolerance * pitch)
                regular.emplace_back(s.width, s.letter);
        if (!(static_cast<double>(regular.size()) >= kPitchShare * static_cast<double>(found.size())))
            continue;
        std::set<char32_t> letters;
        for (const auto &r : regular)
            letters.insert(r.second);
        if (letters.size() < kPitchMinLetters)
            continue;
        double lo = regular[0].first, hi = regular[0].first;
        for (const auto &r : regular) {
            lo = std::min(lo, r.first);
            hi = std::max(hi, r.first);
        }
        if (hi - lo >= kPitchWidthRange * pitch)
            pitches[key] = pitch;
    }
    return pitches;
}

void settle_fixed_pitch(std::vector<RawBlock> &raw) {
    std::map<FontSizeKey, double> pitches = fixed_pitch_fonts(raw);
    if (pitches.empty())
        return;
    for (auto &block : raw)
        for (auto &line : block.lines) {
            if (!is_horizontal(line))
                continue;
            // Spans and characters by position: (span index, char index).
            std::set<std::pair<size_t, size_t>> dropped;
            std::set<size_t> joined;
            std::optional<std::pair<size_t, size_t>> last;
            std::vector<std::pair<size_t, size_t>> guessed;
            for (size_t si = 0; si < line.spans.size(); ++si) {
                RawSpan &span = line.spans[si];
                FontSizeKey key{span.font, span.size};
                auto pitch_it = pitches.find(key);
                std::optional<double> pitch;
                if (pitch_it != pitches.end())
                    pitch = pitch_it->second;
                for (size_t ci = 0; ci < span.chars.size(); ++ci) {
                    RawChar &ch = span.chars[ci];
                    if (ch.c == U' ') {
                        if (ch.synthetic)
                            guessed.emplace_back(si, ci);
                        continue;
                    }
                    if (!guessed.empty() && last && pitch) {
                        const RawSpan &last_span = line.spans[last->first];
                        const RawChar &last_char = last_span.chars[last->second];
                        if (key.first == last_span.font && key.second == last_span.size) {
                            const char32_t *lig = ligature(last_char.c);
                            double letters = lig ? static_cast<double>(std::char_traits<char32_t>::length(lig)) : 1.0;
                            double steps = (ch.origin.x - last_char.origin.x) / span.size / *pitch;
                            if (0 < steps && steps < letters + 0.5) {
                                for (const auto &g : guessed)
                                    dropped.insert(g);
                            } else if (steps > 0) {
                                for (const auto &g : guessed)
                                    line.spans[g.first].chars[g.second].synthetic = false;
                                // the spans owning those spaces, in order
                                std::vector<size_t> between;
                                for (const auto &g : guessed)
                                    if (std::find(between.begin(), between.end(), g.first) == between.end())
                                        between.push_back(g.first);
                                bool owners_blank = true;
                                for (size_t owner : between)
                                    for (const auto &c : line.spans[owner].chars)
                                        if (visible_char(c.c))
                                            owners_blank = false;
                                bool same_colour = last_span.has_color == span.has_color &&
                                                   (!span.has_color || last_span.color == span.color);
                                bool same_flags = last_span.has_flags == span.has_flags &&
                                                  (!span.has_flags || last_span.flags == span.flags);
                                if (steps < letters + 1.5 &&
                                    std::find(between.begin(), between.end(), last->first) == between.end() &&
                                    std::find(between.begin(), between.end(), si) == between.end() && owners_blank &&
                                    same_colour && same_flags) {
                                    for (size_t owner : between)
                                        joined.insert(owner);
                                    joined.insert(si);
                                }
                            }
                        }
                    }
                    if (pitch) {
                        if (const char32_t *lig = ligature(ch.c)) {
                            double extra = static_cast<double>(std::char_traits<char32_t>::length(lig) - 1) * *pitch *
                                           span.size;
                            ch.bbox = Rect(ch.bbox.x0, ch.bbox.y0, ch.bbox.x1 + extra, ch.bbox.y1);
                        }
                    }
                    guessed.clear();
                    last = std::make_pair(si, ci);
                }
            }
            if (dropped.empty() && joined.empty())
                continue;
            std::vector<RawSpan> spans;
            for (size_t si = 0; si < line.spans.size(); ++si) {
                RawSpan &span = line.spans[si];
                std::vector<RawChar> chars;
                for (size_t ci = 0; ci < span.chars.size(); ++ci)
                    if (!dropped.count({si, ci}))
                        chars.push_back(span.chars[ci]);
                if (joined.count(si) && !spans.empty()) {
                    spans.back().chars.insert(spans.back().chars.end(), chars.begin(), chars.end());
                } else if (!chars.empty()) {
                    span.chars = std::move(chars);
                    spans.push_back(std::move(span));
                }
            }
            line.spans = std::move(spans);
        }
}

constexpr double kTypedPerGuessedSpace = 0.1;

bool trusts_guessed_spaces(const std::vector<RawBlock> &raw) {
    long typed = 0, guessed = 0;
    for (const auto &block : raw)
        for (const auto &line : block.lines)
            for (const auto &span : line.spans)
                for (const auto &c : span.chars)
                    if (c.c == U' ') {
                        if (c.synthetic)
                            ++guessed;
                        else
                            ++typed;
                    }
    return guessed > 0 && static_cast<double>(typed) <= kTypedPerGuessedSpace * static_cast<double>(guessed);
}

bool space_overlooked_after(char32_t code) {
    return (0xFB00 <= code && code <= 0xFB06) || (0x1E00 <= code && code <= 0x1FFF) ||
           (0x20D0 <= code && code <= 0x2BFF) || (0xE000 <= code && code <= 0xF8FF) ||
           (0x1D400 <= code && code <= 0x1D7FF);
}

bool space_overlooked_after(const std::u32string &text) {
    return text.size() == 1 && space_overlooked_after(text[0]);
}

constexpr double kGuessedSpaceEm = 0.15;

std::optional<RawChar> overlooked_space(const RawChar &ch, const RawChar &following, double size) {
    if (size <= 0 || !visible_char(ch.c) || !visible_char(following.c) || !space_overlooked_after(ch.c) ||
        following.bbox.x0 - ch.bbox.x1 < kGuessedSpaceEm * size)
        return std::nullopt;
    RawChar space;
    space.c = U' ';
    space.synthetic = true;
    space.origin = Point(ch.bbox.x1, ch.origin.y);
    space.bbox = Rect(ch.bbox.x1, ch.bbox.y0, following.bbox.x0, ch.bbox.y1);
    return space;
}

void add_overlooked_spaces(std::vector<RawBlock> &raw) {
    for (auto &block : raw)
        for (auto &line : block.lines) {
            if (!is_horizontal(line))
                continue;
            std::vector<RawSpan> original = std::move(line.spans);
            std::vector<RawSpan> spans;
            for (size_t index = 0; index < original.size(); ++index) {
                RawSpan span = original[index];
                const std::vector<RawChar> chars = span.chars;
                double size = span.size;
                std::vector<RawChar> filled;
                for (size_t position = 0; position < chars.size(); ++position) {
                    filled.push_back(chars[position]);
                    if (position + 1 < chars.size())
                        if (auto space = overlooked_space(chars[position], chars[position + 1], size))
                            filled.push_back(*space);
                }
                span.chars = std::move(filled);
                spans.push_back(std::move(span));
                if (!chars.empty() && index + 1 < original.size() && !original[index + 1].chars.empty()) {
                    const RawSpan &following = original[index + 1];
                    if (auto space = overlooked_space(chars.back(), following.chars.front(), following.size)) {
                        RawSpan added;
                        added.font = following.font;
                        added.size = following.size;
                        added.has_color = following.has_color;
                        added.color = following.color;
                        added.has_flags = false;
                        added.chars = {*space};
                        spans.push_back(std::move(added));
                    }
                }
            }
            line.spans = std::move(spans);
        }
}

// --- runs and boxes -------------------------------------------------------------------------

constexpr double kSpanSplitRatio = 0.45;
constexpr double kBoxAscent = 0.8;
constexpr double kBoxDescent = 0.21;

struct Run {
    std::u32string text;
    Box box;
    std::optional<double> baseline;
};

std::vector<Run> split_span_runs(const RawSpan &span) {
    std::vector<Run> out;
    if (span.chars.empty())
        return out;
    double threshold = std::max(1.0, kSpanSplitRatio * span.size);
    std::vector<std::vector<const RawChar *>> runs;
    std::vector<const RawChar *> current;
    std::optional<double> previous_end;
    for (const auto &c : span.chars) {
        if (previous_end && (c.bbox.x0 - *previous_end) > threshold) {
            runs.push_back(current);
            current.clear();
        }
        current.push_back(&c);
        previous_end = c.bbox.x1;
    }
    runs.push_back(current);
    for (const auto &run : runs) {
        std::vector<const RawChar *> visible;
        for (const RawChar *c : run)
            if (visible_char(c->c))
                visible.push_back(c);
        if (visible.empty())
            continue;
        Run r;
        for (const RawChar *c : run)
            r.text += c->c;
        double x0 = visible[0]->bbox.x0, x1 = visible[0]->bbox.x1, y0 = visible[0]->bbox.y0,
               y1 = visible[0]->bbox.y1;
        for (const RawChar *c : visible) {
            x0 = std::min(x0, c->bbox.x0);
            x1 = std::max(x1, c->bbox.x1);
            y0 = std::min(y0, c->bbox.y0);
            y1 = std::max(y1, c->bbox.y1);
        }
        r.box = {x0, y0, x1, y1};
        r.baseline = visible[0]->origin.y;
        out.push_back(std::move(r));
    }
    return out;
}

Box baseline_box(const Box &box, double baseline, double size) {
    return {box[0], baseline - kBoxAscent * size, box[2], baseline + kBoxDescent * size};
}

std::u32string str32(const Value &v) { return py::to_u32(v.as_string()); }

// _restore_space
void restore_space(Value &previous, const std::u32string &text, const Value &box, double size) {
    std::u32string prev = str32(previous.at("t"));
    std::u32string last = prev.empty() ? U"" : prev.substr(prev.size() - 1);
    bool last_space = !last.empty() && py::isspace(last);
    bool text_space = !text.empty() && py::isspace(text[0]);
    if (last_space || text_space)
        return;
    double gap = num(box[0]) - num(previous.at("b")[2]);
    if (gap <= std::max(1.0, kSpanSplitRatio * size) || space_overlooked_after(last))
        previous["t"] = previous.at("t").as_string() + " ";
}

const char *const kSpanStyleKeys[] = {"f", "fa", "sz", "c"};

const Value &get_or(const Value &obj, const char *key, const Value &fallback) {
    const Value *v = obj.find(key);
    return v ? *v : fallback;
}

std::vector<Value> merge_adjacent_spans(const std::vector<Value> &spans, const Value &line_box) {
    std::vector<Value> merged;
    std::optional<Value> previous_end;
    static const Value eight(8LL);
    static const Value null_value;
    for (const Value &span : spans) {
        const Value &box = get_or(span, "b", line_box);
        double size = num(get_or(span, "sz", eight));
        bool join = false;
        if (!merged.empty() && previous_end && num(box[0]) - num(*previous_end) <= std::max(1.0, kSpanSplitRatio * size) &&
            num(box[2]) >= num(get_or(merged.back(), "b", line_box)[2])) {
            join = true;
            for (const char *key : kSpanStyleKeys)
                if (!json::equal(get_or(merged.back(), key, null_value), get_or(span, key, null_value)))
                    join = false;
        }
        if (join) {
            Value &last = merged.back();
            Value last_box = get_or(last, "b", line_box);
            last["t"] = last.at("t").as_string() + span.at("t").as_string();
            last["b"] = Value(json::Array{vmin(last_box[0], box[0]), vmin(last_box[1], box[1]), vmax(last_box[2], box[2]),
                                          vmax(last_box[3], box[3])});
        } else {
            merged.push_back(span);
        }
        previous_end = box[2];
    }
    return merged;
}

// The base-14 faces behind the decoders' CSS families.
const char *base14_face(const std::string &family, const std::string &style) {
    static const std::map<std::pair<std::string, std::string>, const char *> faces = {
        {{"s", "R"}, "helv"}, {{"s", "B"}, "hebo"}, {{"s", "I"}, "heit"}, {{"s", "BI"}, "hebi"},
        {{"t", "R"}, "tiro"}, {{"t", "B"}, "tibo"}, {{"t", "I"}, "tiit"}, {{"t", "BI"}, "tibi"},
        {{"m", "R"}, "cour"}, {{"m", "B"}, "cobo"}, {{"m", "I"}, "coit"}, {{"m", "BI"}, "cobi"},
    };
    auto it = faces.find({family, style});
    return it == faces.end() ? nullptr : it->second;
}

constexpr double kFitMargin = 0.01;
constexpr double kFitSlack = 0.25;
constexpr double kFitSlackWholePoints = 1.0;
constexpr long kJustifyMinGaps = 2;
constexpr double kJustifyMinReach = 0.2;
constexpr double kJustifyMinReachWholePoints = 1.0;

bool whole_points(const Value &box) {
    for (size_t i = 0; i < box.size(); ++i)
        if (!(box[i].is_int() || py::is_integer(num(box[i]))))
            return false;
    return true;
}

double fit_slack(const Value &box) { return whole_points(box) ? kFitSlackWholePoints : kFitSlack; }
double justify_reach(const Value &box) { return whole_points(box) ? kJustifyMinReachWholePoints : kJustifyMinReach; }

std::optional<double> text_width(Faces &faces, const std::string &family, const std::string &style,
                                 const std::u32string &text, double size) {
    auto key = std::make_pair(family, style);
    auto it = faces.base14.find(key);
    if (it == faces.base14.end()) {
        Faces::Base14 entry;
        entry.face = Font::base14(faces.ctx, base14_face(family, style));
        it = faces.base14.emplace(key, std::move(entry)).first;
    }
    Faces::Base14 &entry = it->second;
    double total = 0.0;
    for (char32_t c : text) {
        auto found = entry.advances.find(c);
        double advance;
        if (found == entry.advances.end()) {
            int code = static_cast<int>(c);
            advance = entry.face->has_glyph(code) ? entry.face->glyph_advance(code) : -1.0;
            entry.advances[c] = advance;
        } else {
            advance = found->second;
        }
        if (advance < 0)
            return std::nullopt;
        total += advance;
    }
    return total * size;
}

Value open_line_box(const Value &line, double page_w, int precision, Faces &faces) {
    const Value &box = line.at("b");
    const Value &spans = line.at("s");
    if (spans.size() != 1 || spans[0].contains("b"))
        return box;
    const Value &span = spans[0];
    static const Value s_default("s"), r_default("R"), empty_text(""), eight(8LL);
    std::string family = get_or(span, "fa", s_default).as_string();
    std::string style = get_or(span, "f", r_default).as_string();
    std::string face_family = family == "c" ? "s" : family;
    std::u32string text = str32(get_or(span, "t", empty_text));
    if (!base14_face(face_family, style) || text.empty())
        return box;
    double size = num(get_or(span, "sz", eight));
    auto needed = text_width(faces, face_family, style, text, size);
    if (!needed)
        return box;
    double x0 = num(box[0]), x1 = num(box[2]);
    double width = x1 - x0;
    Value right = quantize(page_w, precision);
    if (*needed > width + 1.0 - kFitMargin || num(right) < x1)
        return box;
    double natural = *needed;
    if (family == "c")
        natural = faces.class_fonts.get(family, style)->text_length(text, size);
    if (natural > width + fit_slack(box))
        return box;
    if (natural < width - justify_reach(box) &&
        static_cast<long>(py::count(py::strip(text), U' ')) >= kJustifyMinGaps)
        return box;
    return Value(json::Array{box[0], box[1], right, box[3]});
}

void settle_trailing_spaces(std::vector<Value> &spans, Faces &faces, int precision) {
    Value &last = spans.back();
    last["t"] = py::to_utf8(py::rstrip(str32(last.at("t"))));
    static const Value s_default("s"), r_default("R"), eight(8LL);
    for (size_t i = 0; i + 1 < spans.size(); ++i) {
        Value &span = spans[i];
        std::u32string text = str32(span.at("t"));
        size_t trailing = text.size() - py::rstrip_char(text, U' ').size();
        const Value *box = span.find("b");
        if (!trailing || !box)
            continue;
        auto font = faces.class_fonts.get(get_or(span, "fa", s_default).as_string(),
                                          get_or(span, "f", r_default).as_string());
        double width = font->glyph_advance(32) * num(get_or(span, "sz", eight)) * static_cast<double>(trailing);
        Value b = *box;
        span["b"] = Value(json::Array{b[0], b[1], quantize(num(b[2]) + width, precision), b[3]});
    }
}

// f"{value:.0f}"
std::string format0(double value) {
    char buf[64];
    auto res = std::to_chars(buf, buf + sizeof buf, value, std::chars_format::fixed, 0);
    return std::string(buf, res.ptr);
}

} // namespace

// --- the page --------------------------------------------------------------------------------

std::vector<Rect> unreadable_text_regions(mu::Page &page) {
    std::set<std::string> bad_fonts = fonts_without_unicode(page, page.document());
    std::vector<Rect> boxes;
    Rect page_rect = page.rect();
    for (const auto &block : page.rawdict()) {
        if (block.type != 0)
            continue;
        for (const auto &line : block.lines)
            for (const auto &span : line.spans) {
                if (!span_is_lost(span, bad_fonts))
                    continue;
                std::vector<const RawChar *> visible;
                for (const auto &c : span.chars)
                    if (visible_char(c.c))
                        visible.push_back(&c);
                if (visible.empty())
                    continue;
                double x0 = visible[0]->bbox.x0, y0 = visible[0]->bbox.y0, x1 = visible[0]->bbox.x1,
                       y1 = visible[0]->bbox.y1;
                for (const RawChar *c : visible) {
                    x0 = std::min(x0, c->bbox.x0);
                    y0 = std::min(y0, c->bbox.y0);
                    x1 = std::max(x1, c->bbox.x1);
                    y1 = std::max(y1, c->bbox.y1);
                }
                Rect box = Rect(x0, y0, x1, y1) & page_rect;
                if (box.is_empty() || box.width() <= 0 || box.height() <= 0)
                    continue;
                boxes.push_back(box.plus(-1.5, -1.5, 1.5, 1.5));
            }
    }
    return boxes.empty() ? std::vector<Rect>{} : merge_boxes(boxes, 3.0);
}

Value extract_page(mu::Page &page, int page_idx, const psdi::ImageMap &images, int precision,
                   const std::vector<Rect> &artwork, Faces &faces, const PageDrawings &drawings) {
    mu::Document &doc = page.document();
    std::set<std::string> bad_fonts = fonts_without_unicode(page, doc);
    Value page_data = Value::object();
    page_data["pn"] = Value(static_cast<long long>(page_idx + 1));
    page_data["tb"] = Value::array();
    page_data["ir"] = Value::array();
    page_data["dr"] = Value::array();
    page_data["ck"] = Value::array();
    Rect page_rect = page.rect();

    // --- text
    Value out_block = Value::object({{"l", Value::array()}});
    std::map<std::string, std::string> programs = font_programs(page, doc, faces);
    std::vector<RawBlock> raw = page.rawdict();
    settle_fixed_pitch(raw);
    bool trust_guessed = trusts_guessed_spaces(raw);
    if (trust_guessed)
        add_overlooked_spaces(raw);
    for (const auto &block : raw) {
        if (block.type != 0)
            continue;
        for (const auto &line : block.lines) {
            if (covered(line.bbox, artwork))
                continue;
            Value out_line = Value::object({{"b", quantize_box(line.bbox, precision)}, {"s", Value::array()}});
            std::vector<Value> out_spans;
            bool horizontal = is_horizontal(line);
            struct Seen {
                Value size;
                size_t length;
                double baseline;
            };
            std::vector<Seen> runs_seen;
            bool space_between = false;
            for (const auto &span : line.spans) {
                const auto &chars = span.chars;
                if (!chars.empty()) {
                    bool any_visible = false;
                    for (const auto &c : chars)
                        if (visible_char(c.c))
                            any_visible = true;
                    if (!any_visible) {
                        bool typed = false;
                        for (const auto &c : chars)
                            if (!c.synthetic)
                                typed = true;
                        if (trust_guessed || typed)
                            space_between = true;
                        continue;
                    }
                }
                if (span_is_lost(span, bad_fonts))
                    continue;
                auto [font_key_style, family] = classify_span_font(span, programs);
                Value size = quantize(span.size, 1);
                Value colour = span.has_color ? Value(static_cast<long long>(span.color)) : Value(0LL);
                auto program = programs.find(span.font);
                std::string symbol_font =
                    program != programs.end() && !program->second.empty() ? program->second : span.font;
                const Translation *symbols = translation_for(symbol_font);

                for (Run &run : split_span_runs(span)) {
                    std::u32string raw_text = run.text;
                    if (symbols)
                        raw_text = translate(raw_text, *symbols);
                    if (is_unreadable(raw_text))
                        continue;
                    std::u32string text = normalize_text(raw_text);
                    if (!has_visible(text))
                        continue;
                    Box box = run.box;
                    if (horizontal && run.baseline) {
                        box = baseline_box(box, *run.baseline, num(size));
                        runs_seen.push_back({size, text.size(), *run.baseline});
                    }
                    Value stored_box = quantize_box(box, precision);
                    if (space_between && !out_spans.empty())
                        restore_space(out_spans.back(), text, stored_box, num(size));
                    space_between = false;
                    Value out_span = Value::object();
                    out_span["t"] = py::to_utf8(text);
                    if (font_key_style != "R")
                        out_span["f"] = font_key_style;
                    if (family != "s")
                        out_span["fa"] = family;
                    out_span["sz"] = size;
                    if (!json::equal(colour, Value(0LL)))
                        out_span["c"] = colour;
                    out_span["b"] = stored_box;
                    out_spans.push_back(std::move(out_span));
                }
            }
            if (out_spans.empty())
                continue;
            settle_trailing_spaces(out_spans, faces, precision);
            if (!runs_seen.empty()) {
                double size_max = num(runs_seen[0].size);
                for (const auto &r : runs_seen)
                    size_max = std::max(size_max, num(r.size));
                // max(key=(size, length)): the first of the largest
                const Seen *base = &runs_seen[0];
                for (const auto &r : runs_seen) {
                    double rs = num(r.size), bs = num(base->size);
                    if (rs > bs || (rs == bs && r.length > base->length))
                        base = &r;
                }
                const Value *x0 = &out_spans[0].at("b")[0];
                const Value *x2 = &out_spans[0].at("b")[2];
                for (const auto &s : out_spans) {
                    x0 = &vmin(*x0, s.at("b")[0]);
                    x2 = &vmax(*x2, s.at("b")[2]);
                }
                out_line["b"] = quantize_box(baseline_box({num(*x0), 0, num(*x2), 0}, base->baseline, size_max),
                                             precision);
            }
            if (out_spans.size() == 1) {
                out_spans[0].erase("b");
            } else {
                std::vector<Value> spans = merge_adjacent_spans(out_spans, out_line.at("b"));
                if (spans.size() == 1 && json::equal(spans[0].at("b")[0], out_line.at("b")[0]) &&
                    json::equal(spans[0].at("b")[2], out_line.at("b")[2]))
                    spans[0].erase("b");
                out_spans = std::move(spans);
            }
            out_line["s"] = Value(json::Array(out_spans.begin(), out_spans.end()));
            out_line["b"] = open_line_box(out_line, page_rect.width(), precision, faces);
            out_block["l"].push_back(std::move(out_line));
        }
    }
    if (out_block.at("l").size())
        page_data["tb"].push_back(std::move(out_block));

    // --- image placements
    std::set<std::string> seen;
    for (const auto &info : page.image_info_xrefs()) {
        int xref = info.xref;
        if (!xref || !images.count(std::to_string(xref)))
            continue;
        if (covered(info.bbox, artwork))
            continue;
        std::string key = std::to_string(xref) + "_" + format0(info.bbox.x0) + "_" + format0(info.bbox.y0);
        if (seen.count(key))
            continue;
        seen.insert(key);
        Value box = quantize_box(info.bbox, precision);
        if (num(box[2]) <= num(box[0]) || num(box[3]) <= num(box[1]))
            continue;
        page_data["ir"].push_back(Value::object({{"x", Value(std::to_string(xref))}, {"b", box}}));
    }

    // --- vector rectangles and rules
    std::vector<Value> entries;
    double page_area = std::fabs(page_rect.width() * page_rect.height());
    if (page_area == 0)
        page_area = 1.0;
    for (const VisibleDrawing &drawing : drawings.visible) {
        // Plain references: a lambda below uses them, and C++17 lambdas
        // cannot capture structured bindings.
        const mu::Drawing *path = drawing.path;
        const std::optional<Box> &clip = drawing.clip;
        const auto &fill = path->fill;
        const auto &color = path->color;
        if (!fill && !color)
            continue;
        double width = path->width ? *path->width : 0.0;
        const auto &items = path->items;
        double pad = color ? width / 2 : 0.0;

        auto add_rules = [&](const std::vector<Box> &segments, const std::optional<std::vector<double>> &colour) {
            for (Box s : segments) {
                double x0 = s[0], y0 = s[1], x1 = s[2], y1 = s[3];
                if (clip && spills(grown({std::min(x0, x1), std::min(y0, y1), std::max(x0, x1), std::max(y0, y1)}, pad),
                                   *clip)) {
                    auto piece = clip_segment(x0, y0, x1, y1, grown(*clip, pad));
                    if (!piece)
                        continue;
                    x0 = (*piece)[0];
                    y0 = (*piece)[1];
                    x1 = (*piece)[2];
                    y1 = (*piece)[3];
                }
                Rect seg(std::min(x0, x1), std::min(y0, y1), std::max(x0, x1), std::max(y0, y1));
                if (covered(seg.plus(-1, -1, 1, 1), artwork))
                    continue;
                entries.push_back(rule_entry(x0, y0, x1, y1, color_to_hex(colour), width, precision));
            }
        };

        if (color && path_is_only_axis_lines(items) && (!fill || rects_from_path_items(items).empty())) {
            add_rules(axis_lines_from_path_items(items), color);
            continue;
        }
        for (Rect rect : rects_from_path_items(items)) {
            rect.normalize();
            if (rect.is_infinite())
                continue;
            std::optional<std::vector<double>> outline = color;
            if (clip && spills(grown(rect.tuple(), pad), *clip)) {
                if (color) {
                    add_rules(outline_sides(rect.tuple()), color);
                    outline.reset();
                    if (!fill)
                        continue;
                }
                auto clipped = clip_box(rect.tuple(), *clip);
                if (!clipped)
                    continue;
                rect = *clipped;
            }
            if (rect.is_empty()) {
                if (outline)
                    add_rules(outline_sides(rect.tuple()), outline);
                continue;
            }
            double area = rect.width() * rect.height();
            if (area > 0.8 * page_area && fill && fill->size() == 3) {
                double luminance = 0.299 * (*fill)[0] + 0.587 * (*fill)[1] + 0.114 * (*fill)[2];
                if (luminance < 0.3)
                    continue;
            }
            if (covered(rect, artwork))
                continue;
            double thickness = std::min(rect.width(), rect.height());
            if (precision <= 0 && fill && !outline && 0.15 <= thickness && thickness < 2.0) {
                entries.push_back(rule_entry(rect.x0, rect.y0, rect.x1, rect.y1, color_to_hex(fill), thickness,
                                             precision));
                continue;
            }
            Value entry = Value::object({{"t", Value("rect")}, {"r", quantize_box(rect, precision)}});
            if (fill)
                entry["f"] = color_to_hex(fill);
            if (outline) {
                entry["c"] = color_to_hex(outline);
                if (auto stroke = stroke_width(width))
                    entry["w"] = *stroke;
            }
            entries.push_back(std::move(entry));
        }
    }
    std::vector<Value> compacted = compact_drawings(std::move(entries), page_rect.tuple(), precision);
    page_data["dr"] = Value(json::Array(compacted.begin(), compacted.end()));

    // --- hyperlinks
    for (const auto &link : page.links()) {
        if (link.uri.empty() || !link.is_uri)
            continue;
        if (link.from.is_empty())
            continue;
        if (!page_data.contains("lk"))
            page_data["lk"] = Value::array();
        page_data["lk"].push_back(Value::object({{"b", quantize_box(link.from, precision)}, {"u", Value(link.uri)}}));
    }

    // --- ticked form checkboxes
    try {
        for (const auto &widget : page.widgets()) {
            std::string type_str = py::lower(widget.field_type_string);
            bool is_check = widget.field_type == 2 || widget.field_type == 5 ||
                            type_str.find("checkbox") != std::string::npos || type_str.find("radio") != std::string::npos;
            if (!is_check)
                continue;
            const std::string &value = widget.field_value;
            if (!widget.has_value || value.empty() || value == "Off" || value == "off" || value == "OFF")
                continue;
            const Rect &r = widget.rect;
            page_data["ck"].push_back(quantize_box(Box{r.x0, r.y0, r.x1, r.y1}, precision));
        }
    } catch (const std::exception &) {
    }
    return page_data;
}

} // namespace pdft::engine
