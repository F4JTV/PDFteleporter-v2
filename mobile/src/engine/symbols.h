// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// Unicode for the characters of the Symbol and Wingdings fonts (symbols.py).
//
// Word sets its bullets and arrows in Symbol or Wingdings, fonts with no
// Unicode meaning of their own. MuPDF reads such a character as its code in
// the font moved into the private use area -- U+F0B7 for Symbol's bullet --
// which no other font has a glyph for. Here the code becomes the character
// it draws.
#pragma once

#include <map>
#include <string>

namespace pdft::engine {

using Translation = std::map<char32_t, char32_t>;

// symbols.translation_for(font_name): the table for a font's private-use
// codes, or null.
const Translation *translation_for(const std::string &font_name);

// str.translate(table)
std::u32string translate(const std::u32string &text, const Translation &table);

} // namespace pdft::engine
