// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
#include "json.h"
#include "pyutil.h"

#include <charconv>
#include <cmath>
#include <cstring>
#include <limits>

namespace pdft::json {

namespace {

[[noreturn]] void type_error(const char *what) {
    throw std::logic_error(std::string("JSON value is not ") + what);
}

void append_utf8(std::string &out, uint32_t code) {
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
}

bool numbers_equal(const Value &a, const Value &b) {
    // Python compares int with int exactly and anything with a float by
    // value; True and False are the integers 1 and 0.
    bool a_int = a.is_int() || a.is_bool();
    bool b_int = b.is_int() || b.is_bool();
    if (a_int && b_int)
        return a.as_int() == b.as_int();
    return a.as_double() == b.as_double();
}

} // namespace

Value Value::object(std::initializer_list<Member> members) {
    Value v = object();
    for (const auto &m : members)
        v[m.first] = m.second;
    return v;
}

bool Value::as_bool() const {
    if (type_ != Type::Bool)
        type_error("a boolean");
    return int_ != 0;
}

long long Value::as_int() const {
    if (type_ != Type::Int && type_ != Type::Bool)
        type_error("an integer");
    return int_;
}

double Value::as_double() const {
    if (type_ == Type::Float)
        return float_;
    if (type_ == Type::Int || type_ == Type::Bool)
        return static_cast<double>(int_);
    type_error("a number");
}

const std::string &Value::as_string() const {
    if (type_ != Type::String)
        type_error("a string");
    return string_;
}

std::string &Value::as_string() {
    if (type_ != Type::String)
        type_error("a string");
    return string_;
}

const Array &Value::as_array() const {
    if (type_ != Type::Array)
        type_error("an array");
    return array_;
}

Array &Value::as_array() {
    if (type_ != Type::Array)
        type_error("an array");
    return array_;
}

const std::vector<Member> &Value::members() const {
    if (type_ != Type::Object)
        type_error("an object");
    return object_;
}

std::vector<Member> &Value::members() {
    if (type_ != Type::Object)
        type_error("an object");
    return object_;
}

bool Value::contains(std::string_view key) const {
    return find(key) != nullptr;
}

const Value *Value::find(std::string_view key) const {
    if (type_ != Type::Object)
        return nullptr;
    for (const auto &m : object_)
        if (m.first == key)
            return &m.second;
    return nullptr;
}

Value *Value::find(std::string_view key) {
    if (type_ != Type::Object)
        return nullptr;
    for (auto &m : object_)
        if (m.first == key)
            return &m.second;
    return nullptr;
}

Value &Value::operator[](std::string_view key) {
    if (type_ != Type::Object)
        type_error("an object");
    for (auto &m : object_)
        if (m.first == key)
            return m.second;
    object_.emplace_back(std::string(key), Value());
    return object_.back().second;
}

const Value &Value::at(std::string_view key) const {
    const Value *v = find(key);
    if (!v)
        throw std::out_of_range("JSON object has no key " + std::string(key));
    return *v;
}

const Value &Value::get(std::string_view key, const Value &fallback) const {
    const Value *v = find(key);
    return v ? *v : fallback;
}

bool Value::erase(std::string_view key) {
    if (type_ != Type::Object)
        return false;
    for (auto it = object_.begin(); it != object_.end(); ++it) {
        if (it->first == key) {
            object_.erase(it);
            return true;
        }
    }
    return false;
}

size_t Value::size() const {
    if (type_ == Type::Array)
        return array_.size();
    if (type_ == Type::Object)
        return object_.size();
    if (type_ == Type::String)
        return string_.size();
    type_error("a container");
}

bool equal(const Value &a, const Value &b) {
    if (a.is_number() || a.is_bool()) {
        if (!(b.is_number() || b.is_bool()))
            return false;
        return numbers_equal(a, b);
    }
    if (a.type() != b.type())
        return false;
    switch (a.type()) {
    case Value::Type::Null:
        return true;
    case Value::Type::String:
        return a.as_string() == b.as_string();
    case Value::Type::Array: {
        const Array &x = a.as_array();
        const Array &y = b.as_array();
        if (x.size() != y.size())
            return false;
        for (size_t i = 0; i < x.size(); ++i)
            if (!equal(x[i], y[i]))
                return false;
        return true;
    }
    case Value::Type::Object: {
        const auto &x = a.members();
        const auto &y = b.members();
        if (x.size() != y.size())
            return false;
        for (const auto &m : x) {
            const Value *other = b.find(m.first);
            if (!other || !equal(m.second, *other))
                return false;
        }
        return true;
    }
    default:
        return false;
    }
}

std::string float_repr(double value) {
    if (std::isnan(value))
        return "NaN";
    if (std::isinf(value))
        return value > 0 ? "Infinity" : "-Infinity";
    char buf[64];
    auto res = std::to_chars(buf, buf + sizeof buf, value, std::chars_format::scientific);
    std::string sci(buf, res.ptr);
    // sci is [-]d[.ddd]e[+-]XX: the shortest digits that read back exactly,
    // which are the digits repr() uses.
    bool negative = false;
    size_t pos = 0;
    if (sci[0] == '-') {
        negative = true;
        pos = 1;
    }
    size_t epos = sci.find('e');
    std::string digits;
    for (size_t i = pos; i < epos; ++i)
        if (sci[i] != '.')
            digits += sci[i];
    int exponent = std::stoi(sci.substr(epos + 1));
    // Python drops trailing zeros of the digit string (0.0 is "0").
    while (digits.size() > 1 && digits.back() == '0')
        digits.pop_back();
    int decpt = exponent + 1;
    int ndigits = static_cast<int>(digits.size());
    std::string out = negative ? "-" : "";
    if (digits == "0") {
        out += "0.0";
        return out;
    }
    if (decpt <= -4 || decpt > 16) {
        out += digits[0];
        if (ndigits > 1) {
            out += '.';
            out += digits.substr(1);
        }
        int e = decpt - 1;
        out += 'e';
        out += e < 0 ? '-' : '+';
        int ae = e < 0 ? -e : e;
        if (ae < 10)
            out += '0';
        out += std::to_string(ae);
        return out;
    }
    if (decpt <= 0) {
        out += "0.";
        out.append(static_cast<size_t>(-decpt), '0');
        out += digits;
    } else if (decpt >= ndigits) {
        out += digits;
        out.append(static_cast<size_t>(decpt - ndigits), '0');
        out += ".0";
    } else {
        out += digits.substr(0, static_cast<size_t>(decpt));
        out += '.';
        out += digits.substr(static_cast<size_t>(decpt));
    }
    return out;
}

namespace {

void dump_string(std::string &out, const std::string &s) {
    static const char hex[] = "0123456789abcdef";
    out += '"';
    for (unsigned char c : s) {
        switch (c) {
        case '"': out += "\\\""; break;
        case '\\': out += "\\\\"; break;
        case '\b': out += "\\b"; break;
        case '\f': out += "\\f"; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default:
            if (c < 0x20) {
                out += "\\u00";
                out += hex[c >> 4];
                out += hex[c & 15];
            } else {
                out += static_cast<char>(c);
            }
        }
    }
    out += '"';
}

void dump(std::string &out, const Value &v) {
    switch (v.type()) {
    case Value::Type::Null: out += "null"; break;
    case Value::Type::Bool: out += v.as_bool() ? "true" : "false"; break;
    case Value::Type::Int: out += std::to_string(v.as_int()); break;
    case Value::Type::Float: out += float_repr(v.as_double()); break;
    case Value::Type::String: dump_string(out, v.as_string()); break;
    case Value::Type::Array: {
        out += '[';
        bool first = true;
        for (const auto &item : v.as_array()) {
            if (!first)
                out += ',';
            first = false;
            dump(out, item);
        }
        out += ']';
        break;
    }
    case Value::Type::Object: {
        out += '{';
        bool first = true;
        for (const auto &m : v.members()) {
            if (!first)
                out += ',';
            first = false;
            dump_string(out, m.first);
            out += ':';
            dump(out, m.second);
        }
        out += '}';
        break;
    }
    }
}

class Parser {
public:
    explicit Parser(std::string_view text) : s_(text) {}

    Value parse() {
        skip_ws();
        Value v = value(0);
        skip_ws();
        if (pos_ != s_.size())
            fail("Extra data");
        return v;
    }

private:
    std::string_view s_;
    size_t pos_ = 0;

    [[noreturn]] void fail(const char *what) {
        throw ParseError(std::string(what) + " at offset " + std::to_string(pos_));
    }

    void skip_ws() {
        while (pos_ < s_.size()) {
            char c = s_[pos_];
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r')
                ++pos_;
            else
                break;
        }
    }

    bool consume(std::string_view word) {
        if (s_.substr(pos_, word.size()) == word) {
            pos_ += word.size();
            return true;
        }
        return false;
    }

    Value value(int depth) {
        if (depth > 500)
            fail("Nesting too deep");
        if (pos_ >= s_.size())
            fail("Expecting value");
        char c = s_[pos_];
        if (c == '{')
            return object(depth);
        if (c == '[')
            return array(depth);
        if (c == '"')
            return Value(string());
        if (consume("null"))
            return Value();
        if (consume("true"))
            return Value(true);
        if (consume("false"))
            return Value(false);
        if (consume("NaN"))
            return Value(std::numeric_limits<double>::quiet_NaN());
        if (consume("Infinity"))
            return Value(std::numeric_limits<double>::infinity());
        if (consume("-Infinity"))
            return Value(-std::numeric_limits<double>::infinity());
        return number();
    }

    Value number() {
        size_t start = pos_;
        if (pos_ < s_.size() && s_[pos_] == '-')
            ++pos_;
        if (pos_ >= s_.size())
            fail("Expecting value");
        if (s_[pos_] == '0') {
            ++pos_;
        } else if (s_[pos_] >= '1' && s_[pos_] <= '9') {
            while (pos_ < s_.size() && s_[pos_] >= '0' && s_[pos_] <= '9')
                ++pos_;
        } else {
            pos_ = start;
            fail("Expecting value");
        }
        bool is_float = false;
        if (pos_ + 1 < s_.size() && s_[pos_] == '.' && s_[pos_ + 1] >= '0' && s_[pos_ + 1] <= '9') {
            is_float = true;
            ++pos_;
            while (pos_ < s_.size() && s_[pos_] >= '0' && s_[pos_] <= '9')
                ++pos_;
        }
        if (pos_ < s_.size() && (s_[pos_] == 'e' || s_[pos_] == 'E')) {
            size_t save = pos_;
            ++pos_;
            if (pos_ < s_.size() && (s_[pos_] == '+' || s_[pos_] == '-'))
                ++pos_;
            if (pos_ < s_.size() && s_[pos_] >= '0' && s_[pos_] <= '9') {
                is_float = true;
                while (pos_ < s_.size() && s_[pos_] >= '0' && s_[pos_] <= '9')
                    ++pos_;
            } else {
                pos_ = save;
            }
        }
        std::string_view text = s_.substr(start, pos_ - start);
        if (!is_float) {
            long long v = 0;
            auto res = std::from_chars(text.data(), text.data() + text.size(), v);
            if (res.ec == std::errc())
                return Value(v);
            // Beyond 64 bits: Python would keep it exact; a float is the
            // nearest this reader can hold.
        }
        return Value(py::parse_double(std::string(text).c_str()));
    }

    uint32_t hex4() {
        if (pos_ + 4 > s_.size())
            fail("Invalid \\uXXXX escape");
        uint32_t v = 0;
        for (int i = 0; i < 4; ++i) {
            char c = s_[pos_++];
            v <<= 4;
            if (c >= '0' && c <= '9') v |= static_cast<uint32_t>(c - '0');
            else if (c >= 'a' && c <= 'f') v |= static_cast<uint32_t>(c - 'a' + 10);
            else if (c >= 'A' && c <= 'F') v |= static_cast<uint32_t>(c - 'A' + 10);
            else fail("Invalid \\uXXXX escape");
        }
        return v;
    }

    std::string string() {
        ++pos_; // opening quote
        std::string out;
        while (true) {
            if (pos_ >= s_.size())
                fail("Unterminated string");
            char c = s_[pos_++];
            if (c == '"')
                break;
            if (c == '\\') {
                if (pos_ >= s_.size())
                    fail("Unterminated string");
                char e = s_[pos_++];
                switch (e) {
                case '"': out += '"'; break;
                case '\\': out += '\\'; break;
                case '/': out += '/'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case 'n': out += '\n'; break;
                case 'r': out += '\r'; break;
                case 't': out += '\t'; break;
                case 'u': {
                    uint32_t code = hex4();
                    if (code >= 0xD800 && code <= 0xDBFF && pos_ + 6 <= s_.size()
                            && s_[pos_] == '\\' && s_[pos_ + 1] == 'u') {
                        size_t save = pos_;
                        pos_ += 2;
                        uint32_t low = hex4();
                        if (low >= 0xDC00 && low <= 0xDFFF)
                            code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00);
                        else
                            pos_ = save;
                    }
                    append_utf8(out, code);
                    break;
                }
                default:
                    fail("Invalid \\escape");
                }
            } else if (static_cast<unsigned char>(c) < 0x20) {
                fail("Invalid control character");
            } else {
                out += c;
            }
        }
        return out;
    }

    Value array(int depth) {
        ++pos_;
        Value v = Value::array();
        skip_ws();
        if (pos_ < s_.size() && s_[pos_] == ']') {
            ++pos_;
            return v;
        }
        while (true) {
            skip_ws();
            v.push_back(value(depth + 1));
            skip_ws();
            if (pos_ >= s_.size())
                fail("Expecting ',' delimiter");
            char c = s_[pos_++];
            if (c == ']')
                break;
            if (c != ',')
                fail("Expecting ',' delimiter");
        }
        return v;
    }

    Value object(int depth) {
        ++pos_;
        Value v = Value::object();
        skip_ws();
        if (pos_ < s_.size() && s_[pos_] == '}') {
            ++pos_;
            return v;
        }
        while (true) {
            skip_ws();
            if (pos_ >= s_.size() || s_[pos_] != '"')
                fail("Expecting property name enclosed in double quotes");
            std::string key = string();
            skip_ws();
            if (pos_ >= s_.size() || s_[pos_] != ':')
                fail("Expecting ':' delimiter");
            ++pos_;
            skip_ws();
            // A repeated key keeps its first place and its last value, as a
            // Python dict does.
            v[key] = value(depth + 1);
            skip_ws();
            if (pos_ >= s_.size())
                fail("Expecting ',' delimiter");
            char c = s_[pos_++];
            if (c == '}')
                break;
            if (c != ',')
                fail("Expecting ',' delimiter");
        }
        return v;
    }
};

} // namespace

std::string dumps(const Value &value) {
    std::string out;
    dump(out, value);
    return out;
}

Value loads(std::string_view text) {
    return Parser(text).parse();
}

} // namespace pdft::json
