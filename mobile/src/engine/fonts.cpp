// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

#include "fonts.h"

#include <array>

extern "C" {
#include <mupdf/fitz.h>
}

#include <cstdlib>
#include <fstream>
#include <mutex>
#include <sstream>
#include <stdexcept>

namespace pdft::engine {

namespace {

std::mutex loader_mutex;

std::optional<std::string> read_file(const std::string &path) {
    std::ifstream in(path, std::ios::binary);
    if (!in)
        return std::nullopt;
    std::ostringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

// Without a loader set by the application: $PDFT_RESOURCE_DIR, else the
// directory the build was configured with (the desktop version's sources).
ResourceLoader &loader() {
    static ResourceLoader instance = [](const std::string &path) -> std::optional<std::string> {
        const char *dir = std::getenv("PDFT_RESOURCE_DIR");
        std::string root = dir ? dir : "";
#ifdef PDFT_DEFAULT_RESOURCE_DIR
        if (root.empty())
            root = PDFT_DEFAULT_RESOURCE_DIR;
#endif
        if (root.empty())
            return std::nullopt;
        return read_file(root + "/" + path);
    };
    return instance;
}

const char *base14_name(const std::string &alias) {
    static const std::pair<const char *, const char *> names[] = {
        {"helv", "Helvetica"},     {"heit", "Helvetica-Oblique"}, {"hebo", "Helvetica-Bold"},
        {"hebi", "Helvetica-BoldOblique"}, {"cour", "Courier"},   {"coit", "Courier-Oblique"},
        {"cobo", "Courier-Bold"},  {"cobi", "Courier-BoldOblique"}, {"tiro", "Times-Roman"},
        {"tibo", "Times-Bold"},    {"tiit", "Times-Italic"},      {"tibi", "Times-BoldItalic"},
        {"symb", "Symbol"},        {"zadb", "ZapfDingbats"},
    };
    for (const auto &[short_name, full] : names)
        if (alias == short_name)
            return full;
    return nullptr;
}

} // namespace

void set_resource_loader(ResourceLoader new_loader) {
    std::lock_guard<std::mutex> lock(loader_mutex);
    loader() = std::move(new_loader);
}

std::optional<std::string> load_resource(const std::string &path) {
    ResourceLoader copy;
    {
        std::lock_guard<std::mutex> lock(loader_mutex);
        copy = loader();
    }
    return copy ? copy(path) : std::nullopt;
}

std::shared_ptr<Font> Font::base14(fz_context *ctx, const std::string &name) {
    const char *full = base14_name(name);
    std::string wanted = full ? full : name;
    fz_font *font = nullptr;
    fz_var(font);
    bool failed = false;
    fz_try(ctx) {
        font = fz_new_base14_font(ctx, wanted.c_str());
        if (!font)
            font = fz_new_builtin_font(ctx, wanted.c_str(), 0, 0);
    }
    fz_catch(ctx) { failed = true; }
    if (failed || !font)
        throw std::runtime_error("no font named " + name);
    return std::shared_ptr<Font>(new Font(ctx, font));
}

std::shared_ptr<Font> Font::from_buffer(fz_context *ctx, const std::string &data) {
    fz_buffer *buf = nullptr;
    fz_font *font = nullptr;
    fz_var(buf);
    fz_var(font);
    bool failed = false;
    fz_try(ctx) {
        buf = fz_new_buffer_from_copied_data(ctx, reinterpret_cast<const unsigned char *>(data.data()), data.size());
        font = fz_new_font_from_buffer(ctx, nullptr, buf, 0, 0);
    }
    fz_always(ctx) { fz_drop_buffer(ctx, buf); }
    fz_catch(ctx) { failed = true; }
    if (failed || !font)
        throw std::runtime_error("cannot load font");
    return std::shared_ptr<Font>(new Font(ctx, font));
}

Font::~Font() { fz_drop_font(ctx_, font_); }

int Font::has_glyph(int code) const {
    int gid = 0;
    fz_var(gid);
    fz_try(ctx_) { gid = fz_encode_character(ctx_, font_, code); }
    fz_catch(ctx_) { gid = 0; }
    return gid;
}

double Font::glyph_advance(int code) const {
    float advance = 0;
    fz_var(advance);
    fz_try(ctx_) {
        fz_font *used = font_;
        int gid = fz_encode_character_with_fallback(ctx_, font_, code, 0, FZ_LANG_UNSET, &used);
        advance = fz_advance_glyph(ctx_, used, gid, 0);
    }
    fz_catch(ctx_) { advance = 0; }
    return static_cast<double>(advance);
}

double Font::text_length(const std::u32string &text, double size) const {
    double total = 0;
    for (char32_t c : text)
        total += glyph_advance(static_cast<int>(c));
    return total * size;
}

std::string Font::name() const { return fz_font_name(ctx_, font_); }

std::shared_ptr<Font> ClassFonts::get(const std::string &family_in, const std::string &style) {
    static const std::map<std::string, std::array<const char *, 4>> classes = {
        {"s", {"helv", "hebo", "heit", "hebi"}},
        {"t", {"tiro", "tibo", "tiit", "tibi"}},
        {"m", {"cour", "cobo", "coit", "cobi"}},
        {"c", {"Carlito-Regular.ttf", "Carlito-Bold.ttf", "Carlito-Italic.ttf", "Carlito-BoldItalic.ttf"}},
    };
    std::string family = classes.count(family_in) ? family_in : "s";
    int index = style == "B" ? 1 : style == "I" ? 2 : style == "BI" ? 3 : 0;
    auto key = std::make_pair(family, index);
    auto found = loaded_.find(key);
    if (found != loaded_.end())
        return found->second;
    std::string name = classes.at(family)[static_cast<size_t>(index)];
    std::shared_ptr<Font> font;
    if (name.size() > 4 && name.compare(name.size() - 4, 4, ".ttf") == 0) {
        try {
            auto data = load_resource("fonts/" + name);
            if (!data)
                throw std::runtime_error("missing " + name);
            font = Font::from_buffer(ctx_, *data);
        } catch (const std::exception &) {
            // A build missing its fonts still rebuilds every page, in the
            // class's nearest base font.
            font = get("s", style);
        }
    } else {
        font = Font::base14(ctx_, name);
    }
    loaded_[key] = font;
    return font;
}

} // namespace pdft::engine
