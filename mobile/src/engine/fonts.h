// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// The fonts the engine measures text with and the rebuild sets it in: the
// base-14 faces MuPDF carries and Carlito, shipped with the program, as
// PyMuPDF's fitz.Font presents them.
#pragma once

#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <utility>

struct fz_context;
struct fz_font;

namespace pdft::engine {

// Where the program's own files are found (fonts/Carlito-*.ttf): a function
// returning a file's bytes by its path relative to the resource root.
using ResourceLoader = std::function<std::optional<std::string>(const std::string &path)>;
void set_resource_loader(ResourceLoader loader);
std::optional<std::string> load_resource(const std::string &path);

class Font {
public:
    // fitz.Font("helv"): a base-14 face by PyMuPDF's short name or its own.
    static std::shared_ptr<Font> base14(fz_context *ctx, const std::string &name);
    // fitz.Font(fontbuffer=...)
    static std::shared_ptr<Font> from_buffer(fz_context *ctx, const std::string &data);
    ~Font();
    Font(const Font &) = delete;
    Font &operator=(const Font &) = delete;

    // Font.has_glyph(code): the glyph of this font itself, no fallback.
    int has_glyph(int code) const;
    // Font.glyph_advance(code): at size 1, from a fallback font where this
    // one has no glyph.
    double glyph_advance(int code) const;
    // Font.text_length(text, fontsize=size)
    double text_length(const std::u32string &text, double size) const;
    std::string name() const;
    fz_font *get() const { return font_; }
    fz_context *ctx() const { return ctx_; }

private:
    Font(fz_context *ctx, fz_font *font) : ctx_(ctx), font_(font) {}
    fz_context *ctx_;
    fz_font *font_;
};

// engine._Fonts: the face of each class and style, loaded once.
class ClassFonts {
public:
    explicit ClassFonts(fz_context *ctx) : ctx_(ctx) {}
    std::shared_ptr<Font> get(const std::string &family, const std::string &style);

private:
    fz_context *ctx_;
    std::map<std::pair<std::string, int>, std::shared_ptr<Font>> loaded_;
};

} // namespace pdft::engine
