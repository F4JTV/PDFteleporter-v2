// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// JSON values as Python's json module sees them.
//
// The manifest of an archive is JSON, and the desktop version writes it with
// json.dumps(ensure_ascii=False, separators=(",", ":")). Writing the same
// bytes here is what lets an archive made on a phone be told apart from one
// made on a PC by nothing but its images, so this follows Python to the
// letter: objects keep their insertion order, integers and floats stay
// distinct ("42" against "42.0"), floats are written as repr() writes them,
// and equality is Python's (1 == 1.0, key order ignored in objects).
#pragma once

#include <cstdint>
#include <initializer_list>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace pdft::json {

class Value;
using Array = std::vector<Value>;
using Member = std::pair<std::string, Value>;

class Value {
public:
    enum class Type { Null, Bool, Int, Float, String, Array, Object };

    Value() = default;
    Value(std::nullptr_t) {}
    Value(bool v) : type_(Type::Bool), int_(v ? 1 : 0) {}
    Value(int v) : type_(Type::Int), int_(v) {}
    Value(long v) : type_(Type::Int), int_(v) {}
    Value(long long v) : type_(Type::Int), int_(v) {}
    Value(unsigned v) : type_(Type::Int), int_(v) {}
    Value(unsigned long v) : type_(Type::Int), int_(static_cast<long long>(v)) {}
    Value(unsigned long long v) : type_(Type::Int), int_(static_cast<long long>(v)) {}
    Value(double v) : type_(Type::Float), float_(v) {}
    Value(const char *v) : type_(Type::String), string_(v) {}
    Value(std::string v) : type_(Type::String), string_(std::move(v)) {}
    Value(std::string_view v) : type_(Type::String), string_(v) {}
    Value(Array v) : type_(Type::Array), array_(std::move(v)) {}

    static Value object() { Value v; v.type_ = Type::Object; return v; }
    static Value object(std::initializer_list<Member> members);
    static Value array() { return Value(Array{}); }

    Type type() const { return type_; }
    bool is_null() const { return type_ == Type::Null; }
    bool is_bool() const { return type_ == Type::Bool; }
    bool is_int() const { return type_ == Type::Int; }
    bool is_float() const { return type_ == Type::Float; }
    // A number as Python's isinstance(v, (int, float)) sees it -- which, as
    // in Python, includes bool unless excluded on purpose.
    bool is_number() const { return type_ == Type::Int || type_ == Type::Float; }
    bool is_string() const { return type_ == Type::String; }
    bool is_array() const { return type_ == Type::Array; }
    bool is_object() const { return type_ == Type::Object; }

    bool as_bool() const;
    long long as_int() const;      // Int or Bool
    double as_double() const;      // Int, Bool or Float
    const std::string &as_string() const;
    std::string &as_string();
    const Array &as_array() const;
    Array &as_array();

    // Objects, in insertion order.
    const std::vector<Member> &members() const;
    std::vector<Member> &members();
    bool contains(std::string_view key) const;
    const Value *find(std::string_view key) const;
    Value *find(std::string_view key);
    // obj[key]: creates the member (as null) at the end when missing.
    Value &operator[](std::string_view key);
    const Value &at(std::string_view key) const;
    // dict.get(key, default)
    const Value &get(std::string_view key, const Value &fallback) const;
    // del obj[key]; returns whether it was there.
    bool erase(std::string_view key);
    // Arrays.
    Value &operator[](size_t index) { return as_array()[index]; }
    const Value &operator[](size_t index) const { return as_array()[index]; }
    size_t size() const;
    void push_back(Value v) { as_array().push_back(std::move(v)); }

private:
    Type type_ = Type::Null;
    long long int_ = 0;
    double float_ = 0.0;
    std::string string_;
    Array array_;
    std::vector<Member> object_;
};

// Python equality: numbers by value (True == 1 == 1.0), objects by their
// key/value pairs whatever the order, arrays element by element.
bool equal(const Value &a, const Value &b);
inline bool operator==(const Value &a, const Value &b) { return equal(a, b); }
inline bool operator!=(const Value &a, const Value &b) { return !equal(a, b); }

// json.dumps(value, ensure_ascii=False, separators=(",", ":"))
std::string dumps(const Value &value);
// repr(float), as json.dumps writes a float.
std::string float_repr(double value);

class ParseError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

// json.loads(text): numbers with a fraction or an exponent are floats, the
// others integers; NaN and Infinity are accepted, as Python accepts them.
Value loads(std::string_view text);

} // namespace pdft::json
