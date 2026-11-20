// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
#include "pyutil.h"

#include <clocale>
#include <cstdlib>
#if defined(__APPLE__)
#include <xlocale.h>
#elif !defined(_WIN32)
#include <locale.h>
#endif

extern "C" {
#include "mupdf/ucdn.h"
}

namespace pdft::py {

double parse_double(const char *text) {
#if defined(__ANDROID__)
    // Android's C library reads numbers the "C" way whatever the locale.
    return std::strtod(text, nullptr);
#elif defined(_WIN32)
    static const _locale_t c_numeric = _create_locale(LC_NUMERIC, "C");
    return c_numeric ? _strtod_l(text, nullptr, c_numeric) : std::strtod(text, nullptr);
#else
    // A Qt application on the desktop runs in the user's locale (Qt calls
    // setlocale(LC_ALL, "")), where strtod() alone may want a decimal comma.
    static const locale_t c_numeric = newlocale(LC_NUMERIC_MASK, "C", locale_t{});
    return c_numeric ? strtod_l(text, nullptr, c_numeric) : std::strtod(text, nullptr);
#endif
}

std::u32string to_u32(std::string_view s) {
    std::u32string out;
    out.reserve(s.size());
    size_t i = 0;
    while (i < s.size()) {
        unsigned char c = static_cast<unsigned char>(s[i]);
        char32_t code;
        int extra;
        if (c < 0x80) { code = c; extra = 0; }
        else if ((c & 0xE0) == 0xC0) { code = c & 0x1F; extra = 1; }
        else if ((c & 0xF0) == 0xE0) { code = c & 0x0F; extra = 2; }
        else if ((c & 0xF8) == 0xF0) { code = c & 0x07; extra = 3; }
        else { out += U'�'; ++i; continue; }
        bool bad = false;
        for (int k = 1; k <= extra; ++k) {
            if (i + k >= s.size() || (static_cast<unsigned char>(s[i + k]) & 0xC0) != 0x80) {
                bad = true;
                break;
            }
            code = (code << 6) | (static_cast<unsigned char>(s[i + k]) & 0x3F);
        }
        if (bad) {
            out += U'�';
            ++i;
            continue;
        }
        out += code;
        i += static_cast<size_t>(extra) + 1;
    }
    return out;
}

std::string to_utf8(char32_t code) {
    std::string out;
    if (code < 0x80) {
        out += static_cast<char>(code);
    } else if (code < 0x800) {
        out += static_cast<char>(0xC0 | (code >> 6));
        out += static_cast<char>(0x80 | (code & 0x3F));
    } else if (code < 0x10000) {
        out += static_cast<char>(0xE0 | (code >> 12));
        out += static_cast<char>(0x80 | ((code >> 6) & 0x3F));
        out += static_cast<char>(0x80 | (code & 0x3F));
    } else {
        out += static_cast<char>(0xF0 | (code >> 18));
        out += static_cast<char>(0x80 | ((code >> 12) & 0x3F));
        out += static_cast<char>(0x80 | ((code >> 6) & 0x3F));
        out += static_cast<char>(0x80 | (code & 0x3F));
    }
    return out;
}

std::string to_utf8(std::u32string_view text) {
    std::string out;
    out.reserve(text.size());
    for (char32_t c : text)
        out += to_utf8(c);
    return out;
}

bool isspace(char32_t c) {
    if (c < 0x80) {
        // ASCII: \t \n \v \f \r, the four separators \x1c-\x1f, and space.
        return (c >= 0x09 && c <= 0x0D) || (c >= 0x1C && c <= 0x20);
    }
    if (ucdn_get_general_category(c) == UCDN_GENERAL_CATEGORY_ZS)
        return true;
    int bidi = ucdn_get_bidi_class(c);
    return bidi == UCDN_BIDI_CLASS_WS || bidi == UCDN_BIDI_CLASS_B || bidi == UCDN_BIDI_CLASS_S;
}

bool isalpha(char32_t c) {
    if (c < 0x80)
        return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
    switch (ucdn_get_general_category(c)) {
    case UCDN_GENERAL_CATEGORY_LU:
    case UCDN_GENERAL_CATEGORY_LL:
    case UCDN_GENERAL_CATEGORY_LT:
    case UCDN_GENERAL_CATEGORY_LM:
    case UCDN_GENERAL_CATEGORY_LO:
        return true;
    default:
        return false;
    }
}

const char *bidirectional(char32_t c) {
    switch (ucdn_get_bidi_class(c)) {
    case UCDN_BIDI_CLASS_L: return "L";
    case UCDN_BIDI_CLASS_R: return "R";
    case UCDN_BIDI_CLASS_AL: return "AL";
    default: return "";
    }
}

bool isspace(std::u32string_view s) {
    if (s.empty())
        return false;
    for (char32_t c : s)
        if (!isspace(c))
            return false;
    return true;
}

std::u32string strip(std::u32string_view s) {
    size_t b = 0, e = s.size();
    while (b < e && isspace(s[b]))
        ++b;
    while (e > b && isspace(s[e - 1]))
        --e;
    return std::u32string(s.substr(b, e - b));
}

std::u32string lstrip(std::u32string_view s) {
    size_t b = 0;
    while (b < s.size() && isspace(s[b]))
        ++b;
    return std::u32string(s.substr(b));
}

std::u32string rstrip(std::u32string_view s) {
    size_t e = s.size();
    while (e > 0 && isspace(s[e - 1]))
        --e;
    return std::u32string(s.substr(0, e));
}

std::u32string rstrip_char(std::u32string_view s, char32_t c) {
    size_t e = s.size();
    while (e > 0 && s[e - 1] == c)
        --e;
    return std::u32string(s.substr(0, e));
}

std::u32string lstrip_char(std::u32string_view s, char32_t c) {
    size_t b = 0;
    while (b < s.size() && s[b] == c)
        ++b;
    return std::u32string(s.substr(b));
}

std::vector<std::u32string> split_char(std::u32string_view s, char32_t c) {
    std::vector<std::u32string> out;
    size_t start = 0;
    for (size_t i = 0; i <= s.size(); ++i) {
        if (i == s.size() || s[i] == c) {
            out.emplace_back(s.substr(start, i - start));
            start = i + 1;
        }
    }
    return out;
}

size_t count(std::u32string_view s, char32_t c) {
    size_t n = 0;
    for (char32_t x : s)
        if (x == c)
            ++n;
    return n;
}

std::u32string replace(std::u32string_view s, std::u32string_view from, std::u32string_view to) {
    if (from.empty())
        return std::u32string(s);
    std::u32string out;
    size_t pos = 0;
    while (true) {
        size_t hit = s.find(from, pos);
        if (hit == std::u32string_view::npos) {
            out.append(s.substr(pos));
            break;
        }
        out.append(s.substr(pos, hit - pos));
        out.append(to);
        pos = hit + from.size();
    }
    return out;
}

bool startswith(std::u32string_view s, std::u32string_view prefix) {
    return s.substr(0, prefix.size()) == prefix;
}

bool endswith(std::u32string_view s, std::u32string_view suffix) {
    return s.size() >= suffix.size() && s.substr(s.size() - suffix.size()) == suffix;
}

std::string lower_ascii(std::string_view s) {
    std::string out(s);
    for (char &c : out)
        if (c >= 'A' && c <= 'Z')
            c = static_cast<char>(c - 'A' + 'a');
    return out;
}

std::string lower(std::string_view utf8) {
    std::u32string text = to_u32(utf8);
    for (char32_t &c : text) {
        if (c >= 'A' && c <= 'Z')
            c += 32;
        else if ((c >= 0xC0 && c <= 0xDE && c != 0xD7))
            c += 32;
        else if (c >= 0x391 && c <= 0x3AB && c != 0x3A2)
            c += 32;
        else if (c >= 0x410 && c <= 0x42F)
            c += 32;
        else if (c >= 0x400 && c <= 0x40F)
            c += 80;
    }
    return to_utf8(text);
}

bool contains(std::string_view haystack, std::string_view needle) {
    return haystack.find(needle) != std::string_view::npos;
}

} // namespace pdft::py
