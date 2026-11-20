// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// pdft-cli: the engine from the command line, for tests and for the desktop.
//
//   pdft-cli inspect ARCHIVE
//   pdft-cli manifest ARCHIVE              the manifest, as JSON
//   pdft-cli rewrite ARCHIVE OUT --preset N
//   pdft-cli compress SOURCE OUT [--quality Q] [--compact] [--text-only] [--mode struct|image]
//   pdft-cli rebuild ARCHIVE OUT.pdf
//   pdft-cli dump KIND PDF PAGE            facade output, for differential tests
//   pdft-cli imgcheck JOBS.json            image operations, for differential tests

#include "format.h"
#include "json.h"

#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#ifdef PDFT_CLI_ENGINE
#include "engine.h"
#endif
#ifdef PDFT_CLI_DUMP
#include "facade_dump.h"
#include "imaging_check.h"
#endif

using namespace pdft;

static std::string read_file(const std::string &path) {
    std::ifstream in(path, std::ios::binary);
    if (!in)
        throw std::runtime_error("cannot open " + path);
    std::ostringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

static void write_file(const std::string &path, const std::string &data) {
    std::ofstream out(path, std::ios::binary);
    if (!out)
        throw std::runtime_error("cannot write " + path);
    out.write(data.data(), static_cast<std::streamsize>(data.size()));
}

#ifdef PDFT_CLI_ENGINE
// The report as engine.py returns it, for the tests and the operator.
static json::Value report_json(const engine::CompressReport &r) {
    json::Value estimates = json::Value::object();
    for (const auto &[mode, seconds] : r.estimates)
        estimates[mode] = seconds;
    return json::Value::object({{"source", r.source},
                                {"original_size", r.original_size},
                                {"archive_size", r.archive_size},
                                {"ratio_percent", r.ratio_percent},
                                {"compression_time", r.compression_time},
                                {"quality", r.quality},
                                {"mode", r.mode},
                                {"auto_mode_switch", r.auto_mode_switch ? json::Value(*r.auto_mode_switch) : json::Value()},
                                {"estimates", estimates},
                                {"compact", r.compact},
                                {"extension", r.extension}});
}

static int cli_compress(const std::vector<std::string> &args) {
    if (args.size() < 2)
        throw std::runtime_error("compress SOURCE OUT [--quality Q] [--compact] [--text-only] [--mode M]");
    engine::CompressOptions options;
    bool show_progress = false;
    for (size_t i = 2; i < args.size(); ++i) {
        const std::string &a = args[i];
        if (a == "--quality" && i + 1 < args.size())
            options.quality = args[++i];
        else if (a == "--mode" && i + 1 < args.size())
            options.mode = args[++i];
        else if (a == "--source-name" && i + 1 < args.size())
            options.source_name = args[++i];
        else if (a == "--compact")
            options.compact = true;
        else if (a == "--text-only")
            options.skip_images = true;
        else if (a == "--progress")
            show_progress = true;
        else
            throw std::runtime_error("unknown option " + a);
    }
    engine::ProgressFn progress;
    if (show_progress)
        progress = [](int step, int total, const std::string &message) {
            std::fprintf(stderr, "[%4d/%d] %s\n", step, total, message.c_str());
        };
    engine::CompressReport report = engine::pdf_to_archive(args[0], options, progress);
    write_file(args[1], report.archive);
    std::cout << json::dumps(report_json(report)) << "\n";
    return 0;
}

static int cli_rebuild(const std::vector<std::string> &args) {
    if (args.size() < 2)
        throw std::runtime_error("rebuild ARCHIVE OUT.pdf [--progress]");
    engine::ProgressFn progress;
    if (args.size() > 2 && args[2] == "--progress")
        progress = [](int step, int total, const std::string &message) {
            std::fprintf(stderr, "[%4d/%d] %s\n", step, total, message.c_str());
        };
    engine::RebuildReport report = engine::archive_to_pdf(read_file(args[0]), progress);
    write_file(args[1], report.pdf);
    json::Value out = json::Value::object({{"mode", report.mode},
                                           {"pages", static_cast<long long>(report.pages)},
                                           {"crc_ok", report.crc_ok},
                                           {"source", report.source},
                                           {"version", report.version},
                                           {"compact", report.compact},
                                           {"pdf_size", static_cast<long long>(report.pdf.size())},
                                           {"rebuild_time", report.rebuild_time}});
    std::cout << json::dumps(out) << "\n";
    return 0;
}
#endif

static int usage() {
    std::fprintf(stderr,
                 "usage: pdft-cli inspect|manifest ARCHIVE\n"
                 "       pdft-cli rewrite ARCHIVE OUT --preset N\n"
                 "       pdft-cli compress SOURCE OUT [--quality Q] [--compact] [--text-only] [--mode M]\n"
                 "       pdft-cli rebuild ARCHIVE OUT.pdf\n"
                 "       pdft-cli dump KIND PDF PAGE\n");
    return 2;
}

int main(int argc, char **argv) {
    if (argc < 3)
        return usage();
    std::string cmd = argv[1];
    std::vector<std::string> args(argv + 2, argv + argc);
    try {
        if (cmd == "inspect") {
            std::string data = read_file(args[0]);
            psdi::Validation v = psdi::validate(data);
            json::Value out = json::Value::object();
            out["valid"] = v.valid;
            out["version"] = v.version < 0 ? json::Value() : json::Value(v.version);
            out["checksum_ok"] = v.checksum_ok;
            out["pages"] = v.pages < 0 ? json::Value() : json::Value(static_cast<long long>(v.pages));
            out["error"] = v.error.empty() ? json::Value() : json::Value(v.error);
            std::cout << json::dumps(out) << "\n";
            return v.valid ? 0 : 1;
        }
        if (cmd == "manifest" || cmd == "rewrite") {
            std::string data = read_file(args[0]);
            int version = psdi::peek_version(data);
            if (version == psdi::ARCHIVE_VERSION_IMAGE || version == psdi::ARCHIVE_VERSION_COMPACT_IMAGE) {
                psdi::ImageArchive parsed = psdi::read_image(data);
                if (cmd == "manifest") {
                    json::Value pages = json::Value::array();
                    for (const auto &p : parsed.pages)
                        pages.push_back(json::Value::object({{"w", p.width}, {"h", p.height},
                                                             {"kind", psdi::payload_kind(p.payload)},
                                                             {"size", static_cast<long long>(p.payload.size())}}));
                    std::cout << json::dumps(pages) << "\n";
                    return 0;
                }
                int preset = 6;
                for (size_t i = 2; i + 1 < args.size(); ++i)
                    if (args[i] == "--preset")
                        preset = std::stoi(args[i + 1]);
                write_file(args[1], psdi::write_image(parsed.pages, preset, version));
                return 0;
            }
            psdi::StructArchive parsed = version == psdi::ARCHIVE_VERSION_STRUCT ? psdi::read_struct(data)
                                                                                 : psdi::read_compact_struct(data);
            if (cmd == "manifest") {
                std::cout << json::dumps(parsed.manifest) << "\n";
                return 0;
            }
            int preset = 6;
            for (size_t i = 2; i + 1 < args.size(); ++i)
                if (args[i] == "--preset")
                    preset = std::stoi(args[i + 1]);
            psdi::ImageMap images(parsed.images.begin(), parsed.images.end());
            std::string out = version == psdi::ARCHIVE_VERSION_STRUCT
                                  ? psdi::write_struct(parsed.manifest, images, preset)
                                  : psdi::write_compact_struct(parsed.manifest, images, preset);
            write_file(args[1], out);
            return 0;
        }
#ifdef PDFT_CLI_ENGINE
        if (cmd == "compress")
            return cli_compress(args);
        if (cmd == "rebuild")
            return cli_rebuild(args);
#endif
#ifdef PDFT_CLI_DUMP
        if (cmd == "dump" && args.size() >= 3 && args[0] == "all")
            return dump_page(args[1], std::stoi(args[2]));
        if (cmd == "imgcheck")
            return imaging_check(args[0]);
#endif
        return usage();
    } catch (const std::exception &e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        return 1;
    }
}
