// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// `pdft-cli imgcheck JOBS.json`: image operations for the differential tests.
// tests/imaging_reference.py runs the same jobs through Pillow; the two
// outputs must agree wherever the operation is Pillow's own code.

#include "imaging_check.h"

#include "codecs.h"
#include "image.h"
#include "json.h"
#include "metrics.h"
#include "mupdf_facade.h"

extern "C" {
#include <mupdf/fitz.h>
}

#include <fstream>
#include <iostream>
#include <sstream>

namespace pdft {

using json::Value;

namespace {

std::string read_all(const std::string &path) {
    std::ifstream in(path, std::ios::binary);
    if (!in)
        throw std::runtime_error("cannot open " + path);
    std::ostringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

std::string sha256(const std::string &data) {
    fz_sha256 state;
    unsigned char digest[32];
    fz_sha256_init(&state);
    fz_sha256_update(&state, reinterpret_cast<const unsigned char *>(data.data()), data.size());
    fz_sha256_final(&state, digest);
    static const char hex[] = "0123456789abcdef";
    std::string out;
    for (unsigned char b : digest) {
        out += hex[b >> 4];
        out += hex[b & 15];
    }
    return out;
}

std::string to_hex(const std::string &data) {
    static const char hex[] = "0123456789abcdef";
    std::string out;
    for (unsigned char b : data) {
        out += hex[b >> 4];
        out += hex[b & 15];
    }
    return out;
}

img::Mode parse_mode(const std::string &name) {
    static const std::pair<const char *, img::Mode> modes[] = {
        {"1", img::Mode::One}, {"L", img::Mode::L},       {"LA", img::Mode::LA},     {"P", img::Mode::P},
        {"RGB", img::Mode::RGB}, {"RGBA", img::Mode::RGBA}, {"CMYK", img::Mode::CMYK}, {"I", img::Mode::I},
        {"F", img::Mode::F},   {"I;16", img::Mode::I16}};
    for (const auto &m : modes)
        if (name == m.first)
            return m.second;
    throw std::runtime_error("unknown mode " + name);
}

img::Image raw_image(const Value &job, const char *key = "raw") {
    std::string data = read_all(job.at(key).as_string());
    const Value &size = job.at("size");
    return img::Image::frombytes(parse_mode(job.at("mode").as_string()), static_cast<int>(size[0].as_int()),
                                 static_cast<int>(size[1].as_int()), data.data(), data.size());
}

Value describe(const img::Image &image) {
    Value out = Value::object();
    out["mode"] = img::mode_name(image.mode());
    out["size"] = Value(json::Array{Value(image.width()), Value(image.height())});
    out["sha"] = sha256(image.tobytes());
    if (image.mode() == img::Mode::P) {
        std::vector<uint8_t> palette = image.palette_rgb();
        out["palette"] = to_hex(std::string(palette.begin(), palette.end()));
    }
    return out;
}

std::optional<std::array<double, 4>> box_of(const Value &job) {
    const Value &box = job.get("box", Value());
    if (box.is_null())
        return std::nullopt;
    return std::array<double, 4>{box[0].as_double(), box[1].as_double(), box[2].as_double(), box[3].as_double()};
}

std::optional<double> gap_of(const Value &job) {
    const Value &gap = job.get("gap", Value());
    if (gap.is_null())
        return std::nullopt;
    return gap.as_double();
}

// engine._is_effectively_monochrome
bool monochrome(const img::Image &image) {
    std::string raw = image.resize(64, 64).tobytes();
    size_t total = raw.size() / 3;
    size_t coloured = 0;
    for (size_t at = 0; at + 2 < raw.size(); at += 3) {
        auto a = static_cast<uint8_t>(raw[at]), b = static_cast<uint8_t>(raw[at + 1]), c = static_cast<uint8_t>(raw[at + 2]);
        if (std::max({a, b, c}) - std::min({a, b, c}) > 18)
            ++coloured;
    }
    return total ? static_cast<double>(coloured) / static_cast<double>(total) <= 0.002 : true;
}

Value run(const Value &job, mu::Context &mupdf) {
    const std::string &op = job.at("op").as_string();
    if (op == "open") {
        std::string data = read_all(job.at("file").as_string());
        auto image = img::open(data, &mupdf);
        if (!image)
            return Value::object({{"error", Value("cannot open")}});
        Value out = Value::object();
        out["mode_before_load"] = img::mode_name(image->mode());
        if (job.contains("thumb")) {
            double d = job.at("thumb").as_double();
            image->thumbnail(d, d, img::Filter::Lanczos);
        }
        out["image"] = describe(*image);
        if (job.contains("dump")) {
            std::ofstream dump(job.at("dump").as_string() + ".pdft", std::ios::binary);
            std::string raw = image->tobytes();
            dump.write(raw.data(), static_cast<std::streamsize>(raw.size()));
        }
        const img::Info &info = image->info();
        out["icc"] = info.icc_profile ? Value(static_cast<long long>(info.icc_profile->size())) : Value();
        out["comment"] = info.comment ? Value(to_hex(*info.comment)) : Value();
        out["orientation"] = img::exif_orientation(info);
        return out;
    }
    if (op == "frames") {
        std::string data = read_all(job.at("file").as_string());
        json::Array list;
        int index = 0;
        for (const auto &frame : img::open_frames(data, &mupdf)) {
            img::Image upright = img::exif_transpose(frame);
            list.push_back(describe(upright));
            if (job.contains("dump")) {
                std::ofstream dump(job.at("dump").as_string() + "." + std::to_string(index) + ".pdft",
                                   std::ios::binary);
                std::string raw = upright.tobytes();
                dump.write(raw.data(), static_cast<std::streamsize>(raw.size()));
            }
            ++index;
        }
        return Value(list);
    }
    if (op == "resize") {
        img::Image image = raw_image(job);
        const Value &to = job.at("to");
        auto filter = static_cast<img::Filter>(job.get("filter", Value(3)).as_int());
        return describe(image.resize(static_cast<int>(to[0].as_int()), static_cast<int>(to[1].as_int()), filter,
                                     box_of(job), gap_of(job)));
    }
    if (op == "thumbnail") {
        img::Image image = raw_image(job);
        double d = job.at("max").as_double();
        image.thumbnail(d, d, img::Filter::Lanczos, gap_of(job));
        return describe(image);
    }
    if (op == "convert")
        return describe(raw_image(job).convert(parse_mode(job.at("to").as_string())));
    if (op == "quantize")
        return describe(raw_image(job).quantize(static_cast<int>(job.at("colors").as_int()),
                                                static_cast<int>(job.at("kmeans").as_int())));
    if (op == "getcolors" || op == "own_palette") {
        img::Image image = raw_image(job);
        auto colours = image.getcolors(static_cast<int>(job.get("max", Value(4096)).as_int()));
        if (!colours)
            return Value();
        json::Array list;
        std::vector<uint8_t> palette;
        for (const auto &[count, pixel] : *colours) {
            json::Array p;
            for (int v : pixel) {
                p.push_back(Value(v));
                palette.push_back(static_cast<uint8_t>(v));
            }
            list.push_back(Value(json::Array{Value(static_cast<long long>(count)), Value(p)}));
        }
        if (op == "getcolors")
            return Value(list);
        palette.resize(768, 0);
        return describe(image.quantize_to(palette));
    }
    if (op == "metrics") {
        img::Image a = raw_image(job, "a");
        img::Image b = raw_image(job, "b");
        Value out = Value::object();
        out["psnr"] = img::psnr(a, b);
        out["ssim"] = img::ssim(a, b);
        return out;
    }
    if (op == "metrics_files") {
        // reference raw against an encoded file, decoded as Image.open does
        img::Image a = raw_image(job, "a");
        auto b = img::open(read_all(job.at("file").as_string()), &mupdf);
        if (!b)
            return Value::object({{"error", Value("cannot open")}});
        Value out = Value::object();
        out["psnr"] = img::psnr(a, *b);
        out["ssim"] = img::ssim(a, *b);
        return out;
    }
    if (op == "monochrome")
        return Value(monochrome(raw_image(job)));
    if (op == "save") {
        img::Image image = raw_image(job);
        if (job.contains("quantize"))
            image = image.quantize(static_cast<int>(job.at("quantize")[0].as_int()),
                                   static_cast<int>(job.at("quantize")[1].as_int()));
        if (job.get("own_palette", Value(false)).as_bool()) {
            std::vector<uint8_t> palette;
            // Held here: a range-for over *image.getcolors(256) would read
            // the optional after its end.
            const auto colors = image.getcolors(256);
            for (const auto &[count, pixel] : *colors)
                for (int v : pixel)
                    palette.push_back(static_cast<uint8_t>(v));
            palette.resize(768, 0);
            image = image.quantize_to(palette);
        }
        const std::string &format = job.at("format").as_string();
        std::string data;
        if (format == "PNG")
            data = img::save_png(image, static_cast<int>(job.at("level").as_int()));
        else if (format == "JPEG")
            data = img::save_jpeg(image, static_cast<int>(job.at("quality").as_int()), true,
                                  job.at("progressive").as_bool());
        else if (format == "WEBP")
            data = img::save_webp(image, job.at("lossless").as_bool(), job.at("quality").as_double(),
                                  static_cast<int>(job.at("method").as_int()));
        else if (format == "AVIF")
            data = img::save_avif(image, static_cast<int>(job.at("quality").as_int()),
                                  static_cast<int>(job.at("speed").as_int()));
        else
            throw std::runtime_error("unknown format " + format);
        if (job.contains("out")) {
            std::ofstream out(job.at("out").as_string(), std::ios::binary);
            out.write(data.data(), static_cast<std::streamsize>(data.size()));
        }
        Value out = Value::object();
        out["len"] = Value(static_cast<long long>(data.size()));
        out["sha"] = sha256(data);
        auto decoded = img::open(data, &mupdf);
        out["decoded"] = decoded ? describe(*decoded) : Value();
        return out;
    }
    throw std::runtime_error("unknown op " + op);
}

} // namespace

int imaging_check(const std::string &jobs_path) {
    Value jobs = json::loads(read_all(jobs_path));
    mu::Context mupdf;
    json::Array results;
    for (const auto &job : jobs.as_array()) {
        try {
            results.push_back(run(job, mupdf));
        } catch (const std::exception &e) {
            results.push_back(Value::object({{"exception", Value(e.what())}}));
        }
    }
    std::cout << json::dumps(Value(results)) << "\n";
    return 0;
}

} // namespace pdft
