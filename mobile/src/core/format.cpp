// Copyright (C) 2026 The PDF Teleporter contributors
// Portions derived from PDF Teleporter by F1GBD (Jean-Louis Naudin),
// ADRASEC 77 - FNRASEC, author of the original program and of the .psdi
// format.
// SPDX-License-Identifier: GPL-3.0-only
#include "format.h"

#include "lzma_codec.h"
#include "pyutil.h"

#include <zlib.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <unordered_map>

namespace pdft::psdi {

using json::Value;

namespace {

// --- helpers -------------------------------------------------------------------

uint32_t crc32_of(std::string_view data) {
    uLong crc = crc32(0L, Z_NULL, 0);
    // zlib takes 32-bit lengths; feed it in pieces.
    const char *p = data.data();
    size_t left = data.size();
    while (left > 0) {
        uInt n = static_cast<uInt>(std::min<size_t>(left, 1u << 30));
        crc = crc32(crc, reinterpret_cast<const Bytef *>(p), n);
        p += n;
        left -= n;
    }
    return static_cast<uint32_t>(crc);
}

void put_u8(Bytes &out, uint8_t v) { out += static_cast<char>(v); }
void put_u16(Bytes &out, uint32_t v) {
    out += static_cast<char>(v & 0xFF);
    out += static_cast<char>((v >> 8) & 0xFF);
}
void put_u32(Bytes &out, uint32_t v) {
    for (int i = 0; i < 4; ++i)
        out += static_cast<char>((v >> (8 * i)) & 0xFF);
}
void put_f32(Bytes &out, double v) {
    float f = static_cast<float>(v);
    uint32_t bits;
    std::memcpy(&bits, &f, 4);
    put_u32(out, bits);
}

uint32_t get_u16(std::string_view d, size_t pos) {
    return static_cast<uint8_t>(d[pos]) | (static_cast<uint32_t>(static_cast<uint8_t>(d[pos + 1])) << 8);
}
uint32_t get_u32(std::string_view d, size_t pos) {
    uint32_t v = 0;
    for (int i = 3; i >= 0; --i)
        v = (v << 8) | static_cast<uint8_t>(d[pos + static_cast<size_t>(i)]);
    return v;
}
double get_f32(std::string_view d, size_t pos) {
    uint32_t bits = get_u32(d, pos);
    float f;
    std::memcpy(&f, &bits, 4);
    return static_cast<double>(f);
}

void need(std::string_view data, size_t offset, size_t length, const std::string &what) {
    if (offset > data.size() || length > data.size() - offset) {
        size_t avail = offset < data.size() ? data.size() - offset : 0;
        throw PsdiError("Truncated archive: " + what + " needs " + std::to_string(length) +
                        " bytes at offset " + std::to_string(offset) + ", only " +
                        std::to_string(avail) + " available");
    }
}

// The smallest dictionary that still reaches the start of the data, between
// 4 KiB and the 8 MiB a decoder built on the plain filter spec brings.
uint32_t dict_size_for(size_t length) {
    uint32_t size = 4u << 10;
    while (size < length && size < (8u << 20))
        size <<= 1;
    return size;
}

Bytes lzma_compress(std::string_view data, int preset) {
    return lzma::compress_raw(data, static_cast<uint32_t>(preset), dict_size_for(data.size()));
}

// Manifest JSON: preset 9, extreme, pb=0 (see format.py).
Bytes lzma_compress_text(std::string_view data) {
    return lzma::compress_raw(data, 9u | lzma::PRESET_EXTREME, dict_size_for(data.size()), 0);
}

Bytes lzma_decompress(std::string_view data, size_t limit = MAX_PAYLOAD_SIZE) {
    try {
        return lzma::decompress_raw(data, limit);
    } catch (const lzma::LimitError &e) {
        throw PsdiError(e.what());
    } catch (const lzma::Error &e) {
        throw PsdiError(std::string("Corrupt compressed stream: ") + e.what());
    }
}

// isinstance(v, (int, float)) and not isinstance(v, bool) and isfinite(v)
bool is_number(const Value &v) {
    if (v.is_int())
        return true;
    return v.is_float() && std::isfinite(v.as_double());
}

// isinstance(v, int), bool included as Python includes it.
bool is_int_like(const Value &v) { return v.is_int() || v.is_bool(); }

// isinstance(v, (int, float)), bool included.
bool is_numeric_like(const Value &v) { return v.is_int() || v.is_bool() || v.is_float(); }

void write_images(Bytes &out, const ImageMap &images, int preset) {
    put_u16(out, static_cast<uint32_t>(images.size()));
    std::vector<std::pair<long long, const std::pair<const std::string, Bytes> *>> order;
    for (const auto &entry : images)
        order.emplace_back(std::stoll(entry.first), &entry);
    std::stable_sort(order.begin(), order.end(),
                     [](const auto &a, const auto &b) { return a.first < b.first; });
    for (const auto &item : order) {
        if (item.first < 0 || item.first > 0xFFFF)
            throw std::out_of_range("image key " + item.second->first + " does not fit the archive's 16 bits");
        Packed packed = pack_payload(item.second->second, preset);
        put_u16(out, static_cast<uint32_t>(item.first));
        put_u8(out, packed.flags);
        put_u32(out, static_cast<uint32_t>(packed.data.size()));
        out += packed.data;
    }
}

ImageList read_images(std::string_view data, size_t pos) {
    need(data, pos, 2, "image count");
    uint32_t count = get_u16(data, pos);
    pos += 2;
    if (count > MAX_IMAGES)
        throw PsdiError("Implausible image count: " + std::to_string(count));
    ImageList images;
    for (uint32_t index = 0; index < count; ++index) {
        need(data, pos, 7, "image " + std::to_string(index) + " header");
        uint32_t xref = get_u16(data, pos);
        pos += 2;
        uint8_t flags = static_cast<uint8_t>(data[pos]);
        pos += 1;
        uint32_t size = get_u32(data, pos);
        pos += 4;
        if (size > MAX_PAYLOAD_SIZE)
            throw PsdiError("Image " + std::to_string(xref) + " claims " + std::to_string(size) +
                            " bytes, above the " + std::to_string(MAX_PAYLOAD_SIZE) + " byte ceiling");
        need(data, pos, size, "image " + std::to_string(xref) + " payload");
        std::string key = std::to_string(xref);
        Bytes payload = unpack_payload(flags, data.substr(pos, size));
        // A repeated key keeps its first place and its last payload, as a
        // Python dict does.
        auto it = std::find_if(images.begin(), images.end(),
                               [&](const auto &e) { return e.first == key; });
        if (it != images.end())
            it->second = std::move(payload);
        else
            images.emplace_back(key, std::move(payload));
        pos += size;
    }
    return images;
}

void check_manifest(const Value &manifest) {
    if (!manifest.is_object())
        throw PsdiError("Manifest is not an object");
    const Value *pages = manifest.find("pages");
    const Value *sizes = manifest.find("page_sizes");
    if (!pages || !sizes || !pages->is_array() || !sizes->is_array())
        throw PsdiError("Manifest is missing its pages or page_sizes list");
    if (pages->size() > MAX_PAGES)
        throw PsdiError("Implausible page count: " + std::to_string(pages->size()));
    for (const Value &page : pages->as_array()) {
        if (!page.is_object())
            throw PsdiError("Manifest contains a malformed page entry");
        const Value &number = page.get("pn", Value());
        if (!is_int_like(number) || number.as_int() < 1 ||
            number.as_int() > static_cast<long long>(sizes->size())) {
            std::string shown = number.is_null() ? "None" : json::dumps(number);
            throw PsdiError("Page number " + shown + " has no matching entry in page_sizes (" +
                            std::to_string(sizes->size()) + " present)");
        }
    }
    for (const Value &size : sizes->as_array()) {
        if (!size.is_object())
            throw PsdiError("Manifest contains a malformed page size");
        for (const char *key : {"w", "h"}) {
            const Value &v = size.get(key, Value());
            if (!is_numeric_like(v) || !(v.as_double() > 0 && v.as_double() <= 20000)) {
                std::string shown = v.is_null() ? "None" : json::dumps(v);
                throw PsdiError("Implausible page dimension: " + shown);
            }
        }
    }
}

// --- the columnar manifest -------------------------------------------------------

constexpr uint8_t BODY_COLUMNS = 0;
constexpr uint8_t BODY_JSON = 1;
const char *const STREAMS[] = {"pages", "lines", "lboxes", "spans", "sboxes",
                               "dstyles", "dboxes", "extras", "lengths"};
constexpr int STREAM_COUNT = 9;
enum StreamId { S_PAGES, S_LINES, S_LBOXES, S_SPANS, S_SBOXES, S_DSTYLES, S_DBOXES, S_EXTRAS, S_LENGTHS };
const char *const PAGE_KEYS[] = {"pn", "tb", "ir", "dr", "ck", "lk"};
const char *const SPAN_STYLE_KEYS[] = {"sz", "c", "f", "fa"};
const char *const DRAW_STYLE_KEYS[] = {"t", "f", "c", "w"};
constexpr int SCALES[] = {1, 10, 100};
constexpr long long INLINE_SPANS = 15;
constexpr int MAX_VARINT_BYTES = 10;
constexpr double HUNG_HEIGHT_EM = 1.01;

struct Unsupported : std::exception {
    const char *what_;
    explicit Unsupported(const char *what) : what_(what) {}
    const char *what() const noexcept override { return what_; }
};

bool hung_layout(const Value &tl) {
    // manifest.get("tl") == 1, with Python's equality.
    return json::equal(tl, Value(1));
}

// The height, in units of 1/scale point, of a line hung from its baseline.
long long hung_height(const Value &spans, double scale) {
    const Value *best = nullptr;
    Value fallback(8);
    for (const Value &span : spans.as_array()) {
        if (!span.is_object())
            continue;
        const Value &size = span.get("sz", fallback);
        if (!best) {
            best = &size;
            continue;
        }
        if (!is_numeric_like(size) || !is_numeric_like(*best))
            throw Unsupported("span size");
        if (size.as_double() > best->as_double())
            best = &size;
    }
    const Value &size = best ? *best : fallback;
    if (!is_number(size))
        throw Unsupported("span size");
    return py::round_int(HUNG_HEIGHT_EM * size.as_double() * scale);
}

long long zigzag(long long v) { return v >= 0 ? v * 2 : -v * 2 - 1; }
long long unzigzag(unsigned long long v) {
    return (v & 1) == 0 ? static_cast<long long>(v >> 1) : -static_cast<long long>((v + 1) >> 1);
}

struct Varints {
    Bytes data;
    void u(long long value) {
        if (value < 0)
            throw Unsupported("negative count");
        unsigned long long v = static_cast<unsigned long long>(value);
        while (v >= 0x80) {
            data += static_cast<char>((v & 0x7F) | 0x80);
            v >>= 7;
        }
        data += static_cast<char>(v);
    }
    void s(long long value) { u(zigzag(value)); }
};

struct VarintReader {
    std::string_view data;
    std::string name;
    size_t pos = 0;

    unsigned long long u() {
        unsigned long long value = 0;
        int shift = 0;
        for (int i = 0; i < MAX_VARINT_BYTES; ++i) {
            if (pos >= data.size())
                throw PsdiError("Compact manifest: the " + name + " stream ends early");
            uint8_t byte = static_cast<uint8_t>(data[pos++]);
            if (shift < 64)
                value |= static_cast<unsigned long long>(byte & 0x7F) << shift;
            if (!(byte & 0x80))
                return value;
            shift += 7;
        }
        throw PsdiError("Compact manifest: oversized number in the " + name + " stream");
    }
    long long s() { return unzigzag(u()); }
    long long ui() { return static_cast<long long>(u()); }
    bool exhausted() const { return pos == data.size(); }
};

// A key for a style tuple under Python's equality: numbers by value.
std::string style_key_part(const Value &v) {
    switch (v.type()) {
    case Value::Type::Null: return "n";
    case Value::Type::Bool:
    case Value::Type::Int: return "i" + std::to_string(v.as_int());
    case Value::Type::Float: {
        double d = v.as_double();
        if (py::is_integer(d) && std::fabs(d) < 9.2e18)
            return "i" + std::to_string(static_cast<long long>(d));
        return "f" + json::float_repr(d);
    }
    case Value::Type::String: return "s" + v.as_string();
    default: throw Unsupported("style value");
    }
}

struct StyleTable {
    std::unordered_map<std::string, int> index;
    std::vector<Value> styles;   // dicts, in the order first seen

    template <size_t N>
    int add(const Value &entry, const char *const (&keys)[N]) {
        std::string key;
        Value style = Value::object();
        for (const char *k : keys) {
            const Value *v = entry.find(k);
            if (!v)
                continue;
            if (v->is_array() || v->is_object())
                throw Unsupported("style value");
            key += k;
            key += '\x1f';
            key += style_key_part(*v);
            key += '\x1e';
            style[k] = *v;
        }
        auto it = index.find(key);
        if (it != index.end())
            return it->second;
        int id = static_cast<int>(styles.size());
        index.emplace(key, id);
        styles.push_back(std::move(style));
        return id;
    }
};

class ColumnWriter {
public:
    explicit ColumnWriter(int scale) : scale_(scale) {}

    Bytes body(const Value &manifest) {
        if (!manifest.is_object())
            throw Unsupported("manifest");
        const Value *pages = manifest.find("pages");
        if (!pages || !pages->is_array())
            throw Unsupported("manifest");
        hung_ = hung_layout(manifest.get("tl", Value()));
        const Value *sizes = manifest.find("page_sizes");
        if (sizes && sizes->is_array()) {
            for (const Value &size : sizes->as_array())
                page_heights_.push_back(size.is_object() ? size.get("h", Value()) : Value());
        }
        long index = 0;
        for (const Value &page : pages->as_array())
            this->page(index++, page);
        if (refs_string_ && refs_int_)
            throw Unsupported("mixed image reference types");

        Value header = Value::object();
        Value rest = Value::object();
        for (const auto &m : manifest.members())
            if (m.first != "pages")
                rest[m.first] = m.second;
        header["m"] = rest;
        header["n"] = Value(static_cast<long long>(pages->size()));
        header["q"] = Value(scale_);
        header["ss"] = Value(span_styles_.styles);
        header["ds"] = Value(draw_styles_.styles);
        header["xs"] = Value(refs_string_ && !refs_int_);
        Bytes out;
        auto section = [&out](const Bytes &s) {
            put_u32(out, static_cast<uint32_t>(s.size()));
            out += s;
        };
        section(json::dumps(header));
        for (int i = 0; i < STREAM_COUNT; ++i)
            section(out_[i].data);
        section(text_);
        return out;
    }

private:
    int scale_;
    Varints out_[STREAM_COUNT];
    Bytes text_;
    StyleTable span_styles_;
    StyleTable draw_styles_;
    bool refs_string_ = false;
    bool refs_int_ = false;
    bool hung_ = false;
    std::vector<Value> page_heights_;

    long long coord(const Value &value) const {
        if (!is_number(value))
            throw Unsupported("coordinate");
        long long units;
        if (value.is_int()) {
            units = value.as_int() * scale_;
            double back = scale_ != 1 ? static_cast<double>(units) / scale_ : static_cast<double>(units);
            if (scale_ != 1 ? back != static_cast<double>(value.as_int()) : units != value.as_int())
                throw Unsupported("coordinate finer than the scale");
            return units;
        }
        double v = value.as_double();
        double scaled = v * scale_;
        if (!std::isfinite(scaled) || std::fabs(scaled) > 9.0e18)
            throw Unsupported("coordinate");
        units = py::round_int(scaled);
        double back = scale_ != 1 ? static_cast<double>(units) / scale_ : static_cast<double>(units);
        if (back != v)
            throw Unsupported("coordinate finer than the scale");
        return units;
    }

    std::vector<long long> box(const Value &value) const {
        if (!value.is_array() || value.size() != 4)
            throw Unsupported("box");
        std::vector<long long> out;
        for (const Value &v : value.as_array())
            out.push_back(coord(v));
        return out;
    }

    void string(const Value &value) {
        if (!value.is_string())
            throw Unsupported("string");
        const std::string &s = value.as_string();
        out_[S_LENGTHS].u(static_cast<long long>(s.size()));
        text_ += s;
    }

    static bool keys_within(const Value &dict, std::initializer_list<const char *> allowed) {
        for (const auto &m : dict.members()) {
            bool ok = false;
            for (const char *a : allowed)
                if (m.first == a)
                    ok = true;
            if (!ok)
                return false;
        }
        return true;
    }

    static bool keys_exactly(const Value &dict, std::initializer_list<const char *> wanted) {
        if (dict.members().size() != wanted.size())
            return false;
        for (const char *w : wanted)
            if (!dict.contains(w))
                return false;
        return true;
    }

    std::optional<long long> page_bottom(const Value &number) const {
        if (!hung_ || !number.is_int())
            return std::nullopt;
        long long n = number.as_int();
        if (n < 1 || n > static_cast<long long>(page_heights_.size()))
            return std::nullopt;
        const Value &height = page_heights_[static_cast<size_t>(n - 1)];
        if (!is_number(height))
            return std::nullopt;
        return py::round_int(height.as_double() * scale_);
    }

    void page(long index, const Value &page) {
        if (!page.is_object() || !keys_within(page, {"pn", "tb", "ir", "dr", "ck", "lk"}))
            throw Unsupported("page keys");
        Varints &head = out_[S_PAGES];
        long long mask = 0;
        for (int bit = 0; bit < 6; ++bit)
            if (page.contains(PAGE_KEYS[bit]))
                mask |= 1LL << bit;
        head.u(mask);
        if (const Value *number = page.find("pn")) {
            if (!number->is_int())
                throw Unsupported("page number");
            head.s(number->as_int() - (index + 1));
        }
        if (const Value *blocks = page.find("tb"))
            text_blocks(*blocks, page_bottom(page.get("pn", Value())));
        if (const Value *refs = page.find("ir")) {
            if (!refs->is_array())
                throw Unsupported("image placements");
            head.u(static_cast<long long>(refs->size()));
            for (const Value &ref : refs->as_array()) {
                if (!ref.is_object() || !keys_exactly(ref, {"x", "b"}))
                    throw Unsupported("image placement");
                const Value &key = ref.at("x");
                long long number;
                if (key.is_string()) {
                    const std::string &s = key.as_string();
                    if (s.empty() || !std::all_of(s.begin(), s.end(), [](char c) { return c >= '0' && c <= '9'; }))
                        throw Unsupported("image reference");
                    if (s.size() > 1 && s[0] == '0')
                        throw Unsupported("image reference");
                    if (s.size() > 18)
                        throw Unsupported("image reference");
                    number = std::stoll(s);
                    refs_string_ = true;
                } else if (key.is_int()) {
                    number = key.as_int();
                    refs_int_ = true;
                } else {
                    throw Unsupported("image reference");
                }
                out_[S_EXTRAS].u(number);
                for (long long v : box(ref.at("b")))
                    out_[S_EXTRAS].s(v);
            }
        }
        if (const Value *entries = page.find("dr"))
            drawings(*entries);
        if (const Value *checks = page.find("ck")) {
            if (!checks->is_array())
                throw Unsupported("checks");
            head.u(static_cast<long long>(checks->size()));
            for (const Value &check : checks->as_array())
                for (long long v : box(check))
                    out_[S_EXTRAS].s(v);
        }
        if (const Value *links = page.find("lk")) {
            if (!links->is_array())
                throw Unsupported("links");
            head.u(static_cast<long long>(links->size()));
            for (const Value &link : links->as_array()) {
                if (!link.is_object() || !keys_exactly(link, {"b", "u"}))
                    throw Unsupported("link");
                for (long long v : box(link.at("b")))
                    out_[S_EXTRAS].s(v);
                string(link.at("u"));
            }
        }
    }

    void text_blocks(const Value &blocks, std::optional<long long> bottom) {
        if (!blocks.is_array())
            throw Unsupported("text blocks");
        Varints &head = out_[S_PAGES];
        head.u(static_cast<long long>(blocks.size()));
        std::vector<const Value *> lines_of;
        for (const Value &block : blocks.as_array()) {
            if (!block.is_object() || !keys_exactly(block, {"l"}))
                throw Unsupported("text block");
            const Value &lines = block.at("l");
            if (!lines.is_array())
                throw Unsupported("lines");
            lines_of.push_back(&lines);
        }

        struct LineBox {
            std::vector<long long> box;
            std::pair<long long, long long> key;
        };
        std::vector<LineBox> boxes;
        std::vector<std::pair<std::pair<long long, long long>, long>> corners; // insertion order
        for (const Value *lines : lines_of) {
            for (const Value &line : lines->as_array()) {
                if (!line.is_object() || !keys_exactly(line, {"b", "s"}))
                    throw Unsupported("line");
                LineBox lb;
                lb.box = box(line.at("b"));
                lb.key = {lb.box[2], lb.box[3]};
                const Value &spans = line.at("s");
                if (bottom && spans.is_array() &&
                    lb.box[3] == lb.box[1] + hung_height(spans, scale_))
                    lb.key = {lb.box[2], *bottom};
                auto it = std::find_if(corners.begin(), corners.end(),
                                       [&](const auto &c) { return c.first == lb.key; });
                if (it == corners.end())
                    corners.emplace_back(lb.key, 1);
                else
                    ++it->second;
                boxes.push_back(std::move(lb));
            }
        }
        std::pair<long long, long long> corner{0, 0};
        long best = 0;
        for (const auto &c : corners) {
            if (c.second > best) {
                best = c.second;
                corner = c.first;
            }
        }
        head.s(corner.first);
        head.s(corner.second);

        long long x_prev = 0, y_prev = 0;
        size_t n = 0;
        for (const Value *lines : lines_of) {
            head.u(static_cast<long long>(lines->size()));
            for (const Value &line : lines->as_array()) {
                const LineBox &lb = boxes[n++];
                const std::vector<long long> &b = lb.box;
                const Value &spans = line.at("s");
                if (!spans.is_array())
                    throw Unsupported("spans");
                bool is_open = lb.key == corner &&
                               !(bottom && lb.key == std::make_pair(b[2], b[3]) && b[3] == *bottom);
                bool has_boxes = false;
                for (const Value &span : spans.as_array())
                    if (span.is_object() && span.contains("b"))
                        has_boxes = true;
                long long count = static_cast<long long>(spans.size());
                out_[S_LINES].u(std::min(count, INLINE_SPANS) | (static_cast<long long>(is_open) << 4) |
                                (static_cast<long long>(has_boxes) << 5));
                if (count >= INLINE_SPANS)
                    out_[S_LINES].u(count);
                out_[S_LBOXES].s(b[0] - x_prev);
                out_[S_LBOXES].s(b[1] - y_prev);
                if (!is_open) {
                    out_[S_LBOXES].s(b[2] - b[0]);
                    out_[S_LBOXES].s(b[3] - b[1]);
                }
                x_prev = b[0];
                y_prev = b[1];
                for (const Value &span : spans.as_array())
                    this->span(span, b, has_boxes);
            }
        }
    }

    void span(const Value &span, const std::vector<long long> &line_box, bool has_boxes) {
        if (!span.is_object() || !span.contains("t") ||
            !keys_within(span, {"t", "b", "sz", "c", "f", "fa"}))
            throw Unsupported("span");
        out_[S_SPANS].u(span_styles_.add(span, SPAN_STYLE_KEYS));
        string(span.at("t"));
        if (!has_boxes)
            return;
        const Value *b = span.find("b");
        if (!b) {
            out_[S_SBOXES].u(0);
            return;
        }
        std::vector<long long> sb = box(*b);
        out_[S_SBOXES].u(1);
        out_[S_SBOXES].s(sb[0] - line_box[0]);
        out_[S_SBOXES].s(sb[1] - line_box[1]);
        out_[S_SBOXES].s(sb[2] - sb[0]);
        out_[S_SBOXES].s(sb[3] - sb[1]);
    }

    void drawings(const Value &entries) {
        if (!entries.is_array())
            throw Unsupported("drawings");
        out_[S_PAGES].u(static_cast<long long>(entries.size()));
        std::unordered_map<int, std::array<long long, 4>> previous;
        for (const Value &entry : entries.as_array()) {
            if (!entry.is_object() || !entry.contains("r") ||
                !keys_within(entry, {"r", "t", "f", "c", "w"}))
                throw Unsupported("drawing");
            int style = draw_styles_.add(entry, DRAW_STYLE_KEYS);
            out_[S_DSTYLES].u(style);
            std::vector<long long> r = box(entry.at("r"));
            std::array<long long, 4> prev{0, 0, 0, 0};
            auto it = previous.find(style);
            if (it != previous.end())
                prev = it->second;
            out_[S_DBOXES].s(r[0] - prev[0]);
            out_[S_DBOXES].s(r[1] - prev[1]);
            out_[S_DBOXES].s((r[2] - r[0]) - prev[2]);
            out_[S_DBOXES].s((r[3] - r[1]) - prev[3]);
            previous[style] = {r[0], r[1], r[2] - r[0], r[3] - r[1]};
        }
    }
};

const Value &scalar_table(const Value *value, std::initializer_list<const char *> keys, const char *what) {
    if (!value || !value->is_array())
        throw PsdiError(std::string("Compact manifest: malformed ") + what + " table");
    for (const Value &style : value->as_array()) {
        bool bad = !style.is_object();
        if (!bad) {
            for (const auto &m : style.members()) {
                bool known = false;
                for (const char *k : keys)
                    if (m.first == k)
                        known = true;
                if (!known || m.second.is_array() || m.second.is_object())
                    bad = true;
            }
        }
        if (bad)
            throw PsdiError(std::string("Compact manifest: malformed ") + what + " style");
    }
    return *value;
}

std::pair<Bytes, std::pair<uint32_t, size_t>> compact_body(std::string_view data) {
    size_t pos = 6;
    need(data, pos, 8, "manifest header");
    uint32_t checksum = get_u32(data, pos);
    uint32_t body_size = get_u32(data, pos + 4);
    pos += 8;
    if (body_size > MAX_MANIFEST_SIZE)
        throw PsdiError("Manifest claims " + std::to_string(body_size) + " compressed bytes, above the " +
                        std::to_string(MAX_MANIFEST_SIZE) + " byte ceiling");
    need(data, pos, body_size, "manifest");
    Bytes body = lzma_decompress(data.substr(pos, body_size), MAX_MANIFEST_SIZE);
    if (body.empty())
        throw PsdiError("Compact manifest is empty");
    return {std::move(body), {checksum, pos + body_size}};
}

bool valid_utf8(std::string_view s) {
    size_t i = 0;
    while (i < s.size()) {
        unsigned char c = static_cast<unsigned char>(s[i]);
        int extra;
        uint32_t code;
        if (c < 0x80) { ++i; continue; }
        if ((c & 0xE0) == 0xC0) { extra = 1; code = c & 0x1F; }
        else if ((c & 0xF0) == 0xE0) { extra = 2; code = c & 0x0F; }
        else if ((c & 0xF8) == 0xF0) { extra = 3; code = c & 0x07; }
        else return false;
        if (i + static_cast<size_t>(extra) >= s.size())
            return false;
        for (int k = 1; k <= extra; ++k) {
            unsigned char cc = static_cast<unsigned char>(s[i + static_cast<size_t>(k)]);
            if ((cc & 0xC0) != 0x80)
                return false;
            code = (code << 6) | (cc & 0x3F);
        }
        // Overlong forms, surrogates and code points past U+10FFFF are
        // what Python's strict decoder refuses.
        if ((extra == 1 && code < 0x80) || (extra == 2 && code < 0x800) || (extra == 3 && code < 0x10000))
            return false;
        if (code > 0x10FFFF || (code >= 0xD800 && code <= 0xDFFF))
            return false;
        i += static_cast<size_t>(extra) + 1;
    }
    return true;
}

json::Value parse_json(std::string_view text, const char *prefix) {
    if (!valid_utf8(text))
        throw PsdiError(std::string(prefix) + "'utf-8' codec can't decode bytes");
    try {
        return json::loads(text);
    } catch (const json::ParseError &e) {
        throw PsdiError(std::string(prefix) + e.what());
    }
}

} // namespace

// --- public -------------------------------------------------------------------------

std::string extension_for(int version) {
    return std::string(is_compact_version(version) ? COMPACT_EXTENSION : EXTENSION);
}

Packed pack_payload(std::string_view raw, int preset) {
    Bytes packed = lzma_compress(raw, preset);
    if (packed.size() < raw.size())
        return {FLAG_LZMA, std::move(packed)};
    return {0, Bytes(raw)};
}

Bytes unpack_payload(uint8_t flags, std::string_view data) {
    if (flags & FLAG_LZMA)
        return lzma_decompress(data);
    return Bytes(data);
}

std::string payload_kind(std::string_view data) {
    if (data.substr(0, 3) == std::string_view("\xff\xd8\xff", 3))
        return "jpeg";
    if (data.substr(0, 8) == std::string_view("\x89PNG\r\n\x1a\n", 8))
        return "png";
    if (data.substr(0, 4) == "RIFF" && data.size() >= 12 && data.substr(8, 4) == "WEBP")
        return "webp";
    if (data.size() >= 8 && data.substr(4, 4) == "ftyp") {
        uint32_t size = 0;
        for (int i = 0; i < 4; ++i)
            size = (size << 8) | static_cast<uint8_t>(data[static_cast<size_t>(i)]);
        size_t end = std::min<size_t>({size, 64, data.size()});
        if (end > 8) {
            std::string_view brands = data.substr(8, end - 8);
            for (size_t i = 0; i + 3 < brands.size(); i += 4) {
                std::string_view name = brands.substr(i, 4);
                if (name == "avif" || name == "avis")
                    return "avif";
            }
        }
    }
    return "";
}

const Bytes *StructArchive::image(const std::string &key) const {
    for (const auto &entry : images)
        if (entry.first == key)
            return &entry.second;
    return nullptr;
}

int peek_version(std::string_view data) {
    if (data.size() > MAX_ARCHIVE_SIZE)
        throw PsdiError("Archive is " + std::to_string(data.size()) + " bytes, above the " +
                        std::to_string(MAX_ARCHIVE_SIZE) + " byte ceiling");
    if (data.size() < 6)
        throw PsdiError("Archive too short");
    if (data.substr(0, 4) != ARCHIVE_MAGIC)
        throw PsdiError("PSDI signature missing");
    return static_cast<int>(get_u16(data, 4));
}

Validation validate(std::string_view data) {
    Validation report;
    int version;
    try {
        version = peek_version(data);
    } catch (const PsdiError &e) {
        report.error = e.what();
        return report;
    }
    report.version = version;
    try {
        if (version == ARCHIVE_VERSION_STRUCT || version == ARCHIVE_VERSION_COMPACT_STRUCT) {
            StructArchive parsed = version == ARCHIVE_VERSION_STRUCT ? read_struct(data) : read_compact_struct(data);
            report.checksum_ok = parsed.crc_ok;
            report.pages = static_cast<long>(parsed.manifest.at("pages").size());
            report.valid = true;
        } else if (version == ARCHIVE_VERSION_IMAGE || version == ARCHIVE_VERSION_COMPACT_IMAGE) {
            ImageArchive parsed = read_image(data);
            report.pages = static_cast<long>(parsed.pages.size());
            report.checksum_ok = true;
            report.valid = true;
        } else {
            report.error = "Unsupported version: " + std::to_string(version);
        }
    } catch (const PsdiError &e) {
        report.error = e.what();
    } catch (const std::exception &e) {
        report.error = std::string("Validation failed: ") + e.what();
    }
    return report;
}

std::string manifest_json(const Value &manifest) {
    const std::string text = json::dumps(manifest);
    // Every "b":[...] or "r":[...] holding no bracket or quote.
    struct Match {
        size_t start, body_start, body_end; // [start, body_end] spans '"b":[' .. ']'
    };
    std::vector<Match> matches;
    for (size_t i = 0; i + 5 <= text.size();) {
        if (text[i] == '"' && (text[i + 1] == 'b' || text[i + 1] == 'r') && text[i + 2] == '"' &&
            text[i + 3] == ':' && text[i + 4] == '[') {
            size_t j = i + 5;
            while (j < text.size() && text[j] != '[' && text[j] != ']' && text[j] != '"')
                ++j;
            if (j < text.size() && text[j] == ']') {
                matches.push_back({i, i + 5, j});
                i = j + 1;
                continue;
            }
        }
        ++i;
    }
    if (matches.empty())
        return text;
    bool decimal = false;
    for (const Match &m : matches)
        if (text.find('.', m.body_start) < m.body_end)
            decimal = true;

    auto plain_number = [](std::string_view t) {
        size_t i = 0;
        if (i < t.size() && t[i] == '-')
            ++i;
        size_t digits = i;
        while (i < t.size() && t[i] >= '0' && t[i] <= '9')
            ++i;
        if (i == digits)
            return false;
        if (i == t.size())
            return true;
        if (t[i] != '.')
            return false;
        ++i;
        size_t frac = i;
        while (i < t.size() && t[i] >= '0' && t[i] <= '9')
            ++i;
        return i > frac && i == t.size();
    };
    auto pad = [&](std::string token) {
        if (!plain_number(token))
            return token;
        if (decimal) {
            if (token.find('.') == std::string::npos)
                token += ".0";
            if (token.size() < 6)
                token.append(6 - token.size(), '0');
            return token;
        }
        if (token.size() < 4)
            token.insert(0, 4 - token.size(), ' ');
        return token;
    };

    std::string aligned;
    aligned.reserve(text.size() + text.size() / 4);
    size_t pos = 0;
    for (const Match &m : matches) {
        aligned.append(text, pos, m.body_start - pos);
        std::string_view body(text.data() + m.body_start, m.body_end - m.body_start);
        size_t start = 0;
        bool first = true;
        while (true) {
            size_t comma = body.find(',', start);
            std::string token(body.substr(start, comma == std::string_view::npos ? std::string_view::npos : comma - start));
            if (!first)
                aligned += ',';
            first = false;
            aligned += pad(token);
            if (comma == std::string_view::npos)
                break;
            start = comma + 1;
        }
        aligned += ']';
        pos = m.body_end + 1;
    }
    aligned.append(text, pos, std::string::npos);
    try {
        if (!json::equal(json::loads(aligned), json::loads(text)))
            return text;
    } catch (const json::ParseError &) {
        return text;
    }
    return aligned;
}

Bytes write_struct(const Value &manifest, const ImageMap &images, int lzma_preset) {
    std::string manifest_bytes = manifest_json(manifest);
    Bytes compressed = lzma_compress_text(manifest_bytes);
    Bytes out;
    out += ARCHIVE_MAGIC;
    put_u16(out, ARCHIVE_VERSION_STRUCT);
    put_u32(out, crc32_of(manifest_bytes));
    put_u32(out, static_cast<uint32_t>(compressed.size()));
    out += compressed;
    write_images(out, images, lzma_preset);
    return out;
}

StructArchive read_struct(std::string_view data) {
    size_t pos = 6;
    need(data, pos, 8, "manifest header");
    uint32_t checksum = get_u32(data, pos);
    uint32_t manifest_size = get_u32(data, pos + 4);
    pos += 8;
    if (manifest_size > MAX_MANIFEST_SIZE)
        throw PsdiError("Manifest claims " + std::to_string(manifest_size) + " compressed bytes, above the " +
                        std::to_string(MAX_MANIFEST_SIZE) + " byte ceiling");
    need(data, pos, manifest_size, "manifest");
    Bytes manifest_bytes = lzma_decompress(data.substr(pos, manifest_size), MAX_MANIFEST_SIZE);
    pos += manifest_size;
    StructArchive out;
    out.crc_ok = crc32_of(manifest_bytes) == checksum;
    out.manifest = parse_json(manifest_bytes, "Manifest is not valid JSON: ");
    check_manifest(out.manifest);
    out.images = read_images(data, pos);
    return out;
}

std::optional<Bytes> encode_columns(const Value &manifest) {
    for (int scale : SCALES) {
        try {
            return ColumnWriter(scale).body(manifest);
        } catch (const Unsupported &) {
        } catch (const std::logic_error &) {
            // A value of the wrong type somewhere -- Python's TypeError --
            // or out of range (std::out_of_range is a logic_error).
        }
    }
    return std::nullopt;
}

Value decode_columns(std::string_view body) {
    std::vector<std::string_view> sections;
    size_t pos = 0;
    for (int i = 0; i < STREAM_COUNT + 2; ++i) {
        if (pos + 4 > body.size())
            throw PsdiError("Compact manifest: truncated section table");
        uint32_t size = get_u32(body, pos);
        pos += 4;
        if (size > body.size() - pos)
            throw PsdiError("Compact manifest: section runs past the end");
        sections.push_back(body.substr(pos, size));
        pos += size;
    }
    if (pos != body.size())
        throw PsdiError("Compact manifest: trailing data after the sections");

    Value header = parse_json(sections[0], "Compact manifest: header is not valid JSON: ");
    if (!header.is_object() || !header.get("m", Value()).is_object())
        throw PsdiError("Compact manifest: malformed header");
    const Value &rest = header.at("m");
    const Value &count = header.get("n", Value());
    const Value &scale_value = header.get("q", Value());
    if (rest.contains("pages"))
        throw PsdiError("Compact manifest: pages stored twice");
    if (!count.is_int() || count.as_int() < 0 || count.as_int() > static_cast<long long>(MAX_PAGES))
        throw PsdiError("Compact manifest: implausible page count " +
                        (count.is_null() ? std::string("None") : json::dumps(count)));
    double scale = 0;
    bool scale_ok = false;
    if (!scale_value.is_bool() && scale_value.is_number()) {
        scale = scale_value.as_double();
        scale_ok = scale == 1 || scale == 10 || scale == 100;
    }
    if (!scale_ok)
        throw PsdiError("Compact manifest: unknown coordinate scale " +
                        (scale_value.is_null() ? std::string("None") : json::dumps(scale_value)));
    const Value &span_styles = scalar_table(header.find("ss"), {"sz", "c", "f", "fa"}, "span");
    const Value &draw_styles = scalar_table(header.find("ds"), {"t", "f", "c", "w"}, "drawing");
    const Value *xs = header.find("xs");
    bool string_refs = xs && xs->is_bool() && xs->as_bool();

    std::vector<VarintReader> streams;
    for (int i = 0; i < STREAM_COUNT; ++i)
        streams.push_back(VarintReader{sections[static_cast<size_t>(i) + 1], STREAMS[i]});
    std::string_view text = sections.back();
    size_t text_pos = 0;

    auto string = [&]() -> std::string {
        unsigned long long size = streams[S_LENGTHS].u();
        if (size > text.size() - text_pos)
            throw PsdiError("Compact manifest: a string runs past the text");
        std::string_view value = text.substr(text_pos, static_cast<size_t>(size));
        if (!valid_utf8(value))
            throw PsdiError("Compact manifest: text is not UTF-8");
        text_pos += static_cast<size_t>(size);
        return std::string(value);
    };
    auto coord = [&](long long units) -> Value {
        if (scale != 1)
            return Value(static_cast<double>(units) / scale);
        return Value(units);
    };
    auto style = [&](const Value &table, unsigned long long index, const char *what) -> Value {
        if (index >= table.size())
            throw PsdiError(std::string("Compact manifest: ") + what + " style " + std::to_string(index) +
                            " does not exist");
        return table.as_array()[static_cast<size_t>(index)];
    };

    VarintReader &head = streams[S_PAGES];
    VarintReader &extras = streams[S_EXTRAS];
    bool hung = hung_layout(rest.get("tl", Value()));
    const Value *heights = rest.find("page_sizes");
    Value pages = Value::array();
    for (long long index = 0; index < count.as_int(); ++index) {
        unsigned long long mask = head.u();
        if (mask >> 6)
            throw PsdiError("Compact manifest: unknown page key");
        Value page = Value::object();
        if (mask & 1)
            page["pn"] = Value(head.s() + index + 1);
        if (mask & 2) {
            unsigned long long block_count = head.u();
            long long cx = head.s();
            long long cy = head.s();
            std::optional<long long> bottom;
            const Value *number = page.find("pn");
            if (hung && number && number->is_int() && heights && heights->is_array() &&
                number->as_int() >= 1 && number->as_int() <= static_cast<long long>(heights->size())) {
                const Value &size = heights->as_array()[static_cast<size_t>(number->as_int() - 1)];
                if (size.is_object() && is_number(size.get("h", Value())))
                    bottom = py::round_int(size.at("h").as_double() * scale);
            }
            long long x_prev = 0, y_prev = 0;
            Value blocks = Value::array();
            for (unsigned long long b = 0; b < block_count; ++b) {
                unsigned long long line_count = head.u();
                Value lines = Value::array();
                for (unsigned long long l = 0; l < line_count; ++l) {
                    unsigned long long flags = streams[S_LINES].u();
                    if (flags >> 6)
                        throw PsdiError("Compact manifest: unknown line flags");
                    unsigned long long span_count = flags & 0x0F;
                    if (span_count == static_cast<unsigned long long>(INLINE_SPANS))
                        span_count = streams[S_LINES].u();
                    long long x0 = x_prev + streams[S_LBOXES].s();
                    long long y0 = y_prev + streams[S_LBOXES].s();
                    long long x1, y1;
                    if (flags & 0x10) {
                        x1 = cx;
                        y1 = cy;
                    } else {
                        x1 = x0 + streams[S_LBOXES].s();
                        y1 = y0 + streams[S_LBOXES].s();
                    }
                    x_prev = x0;
                    y_prev = y0;
                    Value spans = Value::array();
                    for (unsigned long long s = 0; s < span_count; ++s) {
                        Value span = style(span_styles, streams[S_SPANS].u(), "span");
                        span["t"] = Value(string());
                        if ((flags & 0x20) && streams[S_SBOXES].u()) {
                            long long sx0 = x0 + streams[S_SBOXES].s();
                            long long sy0 = y0 + streams[S_SBOXES].s();
                            long long sx1 = sx0 + streams[S_SBOXES].s();
                            long long sy1 = sy0 + streams[S_SBOXES].s();
                            span["b"] = Value(json::Array{coord(sx0), coord(sy0), coord(sx1), coord(sy1)});
                        }
                        spans.push_back(std::move(span));
                    }
                    if ((flags & 0x10) && bottom && y1 == *bottom) {
                        try {
                            y1 = y0 + hung_height(spans, scale);
                        } catch (const Unsupported &) {
                            throw PsdiError("Compact manifest: malformed span size");
                        }
                    }
                    Value line = Value::object();
                    line["b"] = Value(json::Array{coord(x0), coord(y0), coord(x1), coord(y1)});
                    line["s"] = std::move(spans);
                    lines.push_back(std::move(line));
                }
                Value block = Value::object();
                block["l"] = std::move(lines);
                blocks.push_back(std::move(block));
            }
            page["tb"] = std::move(blocks);
        }
        if (mask & 4) {
            Value refs = Value::array();
            unsigned long long n = head.u();
            for (unsigned long long i = 0; i < n; ++i) {
                unsigned long long key = extras.u();
                json::Array box;
                for (int k = 0; k < 4; ++k)
                    box.push_back(coord(extras.s()));
                Value ref = Value::object();
                ref["x"] = string_refs ? Value(std::to_string(key)) : Value(static_cast<long long>(key));
                ref["b"] = Value(std::move(box));
                refs.push_back(std::move(ref));
            }
            page["ir"] = std::move(refs);
        }
        if (mask & 8) {
            Value entries = Value::array();
            std::unordered_map<unsigned long long, std::array<long long, 4>> previous;
            unsigned long long n = head.u();
            for (unsigned long long i = 0; i < n; ++i) {
                unsigned long long index_style = streams[S_DSTYLES].u();
                Value entry = style(draw_styles, index_style, "drawing");
                std::array<long long, 4> prev{0, 0, 0, 0};
                auto it = previous.find(index_style);
                if (it != previous.end())
                    prev = it->second;
                long long x0 = prev[0] + streams[S_DBOXES].s();
                long long y0 = prev[1] + streams[S_DBOXES].s();
                long long width = prev[2] + streams[S_DBOXES].s();
                long long height = prev[3] + streams[S_DBOXES].s();
                previous[index_style] = {x0, y0, width, height};
                entry["r"] = Value(json::Array{coord(x0), coord(y0), coord(x0 + width), coord(y0 + height)});
                entries.push_back(std::move(entry));
            }
            page["dr"] = std::move(entries);
        }
        if (mask & 16) {
            Value checks = Value::array();
            unsigned long long n = head.u();
            for (unsigned long long i = 0; i < n; ++i) {
                json::Array box;
                for (int k = 0; k < 4; ++k)
                    box.push_back(coord(extras.s()));
                checks.push_back(Value(std::move(box)));
            }
            page["ck"] = std::move(checks);
        }
        if (mask & 32) {
            Value links = Value::array();
            unsigned long long n = head.u();
            for (unsigned long long i = 0; i < n; ++i) {
                json::Array box;
                for (int k = 0; k < 4; ++k)
                    box.push_back(coord(extras.s()));
                Value link = Value::object();
                link["b"] = Value(std::move(box));
                link["u"] = Value(string());
                links.push_back(std::move(link));
            }
            page["lk"] = std::move(links);
        }
        pages.push_back(std::move(page));
    }

    std::string leftover;
    for (int i = 0; i < STREAM_COUNT; ++i) {
        if (!streams[static_cast<size_t>(i)].exhausted()) {
            if (!leftover.empty())
                leftover += ", ";
            leftover += STREAMS[i];
        }
    }
    if (!leftover.empty() || text_pos != text.size())
        throw PsdiError("Compact manifest: data left over after the last page (" +
                        (leftover.empty() ? std::string("text") : leftover) + ")");
    Value manifest = rest;
    manifest["pages"] = std::move(pages);
    return manifest;
}

Bytes write_compact_struct(const Value &manifest, const ImageMap &images, int lzma_preset) {
    constexpr size_t JSON_RETRY_BELOW = 4096;
    std::optional<Bytes> columns = encode_columns(manifest);
    if (columns) {
        bool faithful = false;
        try {
            faithful = json::equal(decode_columns(*columns), manifest);
        } catch (const PsdiError &) {
            faithful = false;
        }
        if (!faithful)
            columns.reset();
    }
    Bytes json_body = Bytes(1, static_cast<char>(BODY_JSON)) + manifest_json(manifest);
    Bytes body, compressed;
    if (!columns) {
        body = json_body;
        compressed = lzma_compress_text(json_body);
    } else {
        body = Bytes(1, static_cast<char>(BODY_COLUMNS)) + *columns;
        compressed = lzma_compress_text(body);
        if (compressed.size() < JSON_RETRY_BELOW) {
            Bytes alternative = lzma_compress_text(json_body);
            if (alternative.size() < compressed.size()) {
                body = json_body;
                compressed = std::move(alternative);
            }
        }
    }
    Bytes out;
    out += ARCHIVE_MAGIC;
    put_u16(out, ARCHIVE_VERSION_COMPACT_STRUCT);
    put_u32(out, crc32_of(body));
    put_u32(out, static_cast<uint32_t>(compressed.size()));
    out += compressed;
    write_images(out, images, lzma_preset);
    return out;
}

std::string compact_body_kind(std::string_view data) {
    auto body = compact_body(data).first;
    return static_cast<uint8_t>(body[0]) == BODY_COLUMNS ? "columns" : "json";
}

StructArchive read_compact_struct(std::string_view data) {
    auto [body, rest] = compact_body(data);
    StructArchive out;
    out.crc_ok = crc32_of(body) == rest.first;
    uint8_t kind = static_cast<uint8_t>(body[0]);
    std::string_view content(body.data() + 1, body.size() - 1);
    if (kind == BODY_COLUMNS)
        out.manifest = decode_columns(content);
    else if (kind == BODY_JSON)
        out.manifest = parse_json(content, "Manifest is not valid JSON: ");
    else
        throw PsdiError("Unknown compact manifest encoding: " + std::to_string(kind));
    check_manifest(out.manifest);
    out.images = read_images(data, rest.second);
    return out;
}

Bytes write_image(const std::vector<ImagePage> &pages, int lzma_preset, int version) {
    if (version != ARCHIVE_VERSION_IMAGE && version != ARCHIVE_VERSION_COMPACT_IMAGE)
        throw std::invalid_argument("Not a page-image version: " + std::to_string(version));
    Bytes out;
    out += ARCHIVE_MAGIC;
    put_u16(out, static_cast<uint32_t>(version));
    put_u16(out, static_cast<uint32_t>(pages.size()));
    for (const ImagePage &page : pages) {
        Packed packed = pack_payload(page.payload, lzma_preset);
        put_f32(out, page.width);
        put_f32(out, page.height);
        put_u8(out, packed.flags);
        put_u32(out, static_cast<uint32_t>(packed.data.size()));
        out += packed.data;
    }
    return out;
}

ImageArchive read_image(std::string_view data) {
    size_t pos = 6;
    need(data, pos, 2, "page count");
    uint32_t count = get_u16(data, pos);
    pos += 2;
    if (!(count > 0 && count <= MAX_PAGES))
        throw PsdiError("Implausible page count: " + std::to_string(count));
    ImageArchive out;
    for (uint32_t index = 0; index < count; ++index) {
        need(data, pos, 13, "page " + std::to_string(index + 1) + " header");
        ImagePage page;
        page.width = get_f32(data, pos);
        page.height = get_f32(data, pos + 4);
        uint8_t flags = static_cast<uint8_t>(data[pos + 8]);
        uint32_t size = get_u32(data, pos + 9);
        pos += 13;
        for (double v : {page.width, page.height})
            if (!(v > 0 && v <= 20000))
                throw PsdiError("Page " + std::to_string(index + 1) + " has an implausible dimension: " +
                                json::float_repr(v));
        if (size > MAX_PAYLOAD_SIZE)
            throw PsdiError("Page " + std::to_string(index + 1) + " claims " + std::to_string(size) +
                            " bytes, above the " + std::to_string(MAX_PAYLOAD_SIZE) + " byte ceiling");
        need(data, pos, size, "page " + std::to_string(index + 1) + " payload");
        page.payload = unpack_payload(flags, data.substr(pos, size));
        pos += size;
        out.pages.push_back(std::move(page));
    }
    return out;
}

} // namespace pdft::psdi
