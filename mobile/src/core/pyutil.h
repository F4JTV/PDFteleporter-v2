// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// The bits of Python's semantics the engine depends on.
//
// The engine is a port of the desktop version's Python, and what it writes
// into an archive turns on details such as how round() breaks a tie or what
// str.isspace() calls a space. Each helper here does what the Python builtin
// it is named after does, so that the port can read like the original.
#pragma once

#include <charconv>
#include <cmath>
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace pdft::py {

// round(x): to the nearest integer, ties to even.
inline long long round_int(double x) {
    return static_cast<long long>(std::nearbyint(x));
}

// float(text) for a number written the plain decimal way ("-12.5", ".5",
// "1e-07"), NUL-terminated: the nearest double, as CPython reads it, with
// a "." for the decimal point whatever the locale the program runs in. Out
// of range, it is an infinity or a zero, as for float().
double parse_double(const char *text);

// round(x, ndigits) for ndigits >= 0: the double nearest to the decimal
// x rounded to ndigits places, correctly rounded, ties to even -- which is
// what CPython computes through its own dtoa.
inline double round_to(double x, int ndigits) {
    if (!std::isfinite(x))
        return x;
    char buf[400];
    auto res = std::to_chars(buf, buf + sizeof buf - 1, x, std::chars_format::fixed, ndigits);
    if (res.ec != std::errc())
        return x;
    // Not std::from_chars: Android's C++ library (NDK r27) cannot read a
    // floating-point number with it.
    *res.ptr = '\0';
    double out = parse_double(buf);
    // CPython keeps the sign of a zero result: round(-0.04, 1) is -0.0.
    if (out == 0.0)
        return std::copysign(0.0, x);
    return out;
}

// float.is_integer()
inline bool is_integer(double x) {
    return std::isfinite(x) && std::floor(x) == x;
}

// Python's % and // on floats take the sign of the divisor.
inline double floordiv(double a, double b) { return std::floor(a / b); }

// --- Unicode text ------------------------------------------------------------
// Text travels as UTF-8 and is handled as code points where a character
// matters. Classification uses the Unicode database MuPDF carries.

std::u32string to_u32(std::string_view utf8);
std::string to_utf8(std::u32string_view text);
std::string to_utf8(char32_t c);

// str.isspace(): general category Zs, or bidirectional class WS, B or S.
bool isspace(char32_t c);
// str.isalpha(): general category Lu, Ll, Lt, Lm or Lo.
bool isalpha(char32_t c);
// unicodedata.bidirectional(c) restricted to what the engine asks: "L",
// "R", "AL", or "" for anything else.
const char *bidirectional(char32_t c);

bool isspace(std::u32string_view s);   // every character, and not empty
std::u32string strip(std::u32string_view s);
std::u32string lstrip(std::u32string_view s);
std::u32string rstrip(std::u32string_view s);
// str.rstrip(" ") and friends: only the given character.
std::u32string rstrip_char(std::u32string_view s, char32_t c);
std::u32string lstrip_char(std::u32string_view s, char32_t c);
std::vector<std::u32string> split_char(std::u32string_view s, char32_t c);
size_t count(std::u32string_view s, char32_t c);
std::u32string replace(std::u32string_view s, std::u32string_view from, std::u32string_view to);
bool startswith(std::u32string_view s, std::u32string_view prefix);
bool endswith(std::u32string_view s, std::u32string_view suffix);

// For byte strings, ASCII only.
std::string lower_ascii(std::string_view s);
bool contains(std::string_view haystack, std::string_view needle);
inline bool startswith(std::string_view s, std::string_view prefix) {
    return s.substr(0, prefix.size()) == prefix;
}
inline bool endswith(std::string_view s, std::string_view suffix) {
    return s.size() >= suffix.size() && s.substr(s.size() - suffix.size()) == suffix;
}
// str.lower() on arbitrary text: what the engine lowers are font names,
// ASCII in practice; other characters are lowered through the Unicode
// database where it has a simple mapping.
std::string lower(std::string_view utf8);

} // namespace pdft::py
