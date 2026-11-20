// Copyright (C) 2026 The PDF Teleporter contributors
// Portions derived from PDF Teleporter by F1GBD (Jean-Louis Naudin),
// ADRASEC 77 - FNRASEC, author of the original program and of the .psdi
// format.
// SPDX-License-Identifier: GPL-3.0-only
//
// PDF -> archive: the three encoders and their front door (engine.py,
// "_compress_struct" to "pdf_to_archive", with "_Progress" and
// "detect_optimal_mode").

#include "internal.h"

#include "codecs.h"
#include "image.h"
#include "pyutil.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <fstream>
#include <map>
#include <stdexcept>
#include <sys/stat.h>

namespace pdft::engine {

namespace {

// Relative cost of each unit of work (engine.py, _COST_*): only the ratios
// matter, they shape the bar.
constexpr double kCostCompressImage = 1.0;
constexpr double kCostCompressImageCompact = 18.0;
constexpr double kCostCompressPage = 5.0;
constexpr double kCostCompressWritePerPage = 8.0;
constexpr double kCostRenderPage = 5.0;
constexpr double kCostRenderWritePerPage = 1.0;
constexpr double kProgressMinInterval = 0.05;   // seconds

// Artwork identifiers are handed out downwards from here, so they never
// meet a real xref coming up from below.
constexpr int kArtworkIdTop = 65500;
// The manifest's "tl": text hung from the baseline (see the rebuild).
constexpr int kTextLayout = 1;

// A page with fewer characters than this whose images cover at least
// kImageCoverMin of its area is, in practice, a scan.
constexpr size_t kTextMinChars = 50;
constexpr double kImageCoverMin = 0.5;

// A page is defined in points; a photo of a sheet is mapped to A4's long side.
constexpr double kA4WidthPt = 595.0;
constexpr double kA4HeightPt = 842.0;

const char *const kImageExtensions[] = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"};

double monotonic_seconds() {
    using namespace std::chrono;
    return duration<double>(steady_clock::now().time_since_epoch()).count();
}

std::string basename(const std::string &path) {
    size_t slash = path.rfind('/');
    return slash == std::string::npos ? path : path.substr(slash + 1);
}

std::optional<long long> file_size(const std::string &path) {
    struct stat st {};
    if (::stat(path.c_str(), &st) != 0)
        return std::nullopt;
    return static_cast<long long>(st.st_size);
}

std::string read_file(const std::string &path) {
    std::ifstream in(path, std::ios::binary);
    if (!in)
        throw std::runtime_error("Cannot read " + path);
    return std::string(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
}

std::string count_label(const std::string &label, long long n, long long total) {
    return label + " (" + std::to_string(n) + "/" + std::to_string(total) + ")…";
}

// --- structured -------------------------------------------------------------------------

psdi::Bytes compress_struct(const std::string &pdf_path, const std::string &source_name, const Params &params,
                            const ProgressFn &progress_cb, bool skip_images, const std::atomic<bool> *cancel) {
    mu::Context ctx;
    mu::Document doc(ctx, pdf_path);
    psdi::ImageMap images_data;
    // image_meta keeps Python's insertion order: the xrefs in ascending
    // order, then the artwork identifiers as they are handed out.
    Value image_meta = Value::object();
    const int page_count = doc.page_count();
    const int precision = params.preset.coord_precision;

    // First pass: the largest frame each image is drawn into, across every
    // page, so that a logo repeated on eight pages is sized for its biggest
    // appearance. Done first so the image count fixes the bar's scale.
    std::map<int, std::pair<double, double>> largest_pt;
    if (!skip_images) {
        for (int i = 0; i < page_count; ++i) {
            mu::Page page(doc, i);
            for (const auto &info : page.image_info_xrefs()) {
                if (!info.xref)
                    continue;
                const Rect &b = info.bbox;
                double w_pt = std::abs(b.x1 - b.x0), h_pt = std::abs(b.y1 - b.y0);
                auto prev = largest_pt.find(info.xref);
                if (prev == largest_pt.end())
                    largest_pt.emplace(info.xref, std::make_pair(w_pt, h_pt));
                else if (w_pt * h_pt > prev->second.first * prev->second.second)
                    prev->second = {w_pt, h_pt};
            }
        }
    }

    const double image_cost = params.compact ? kCostCompressImageCompact : kCostCompressImage;
    Progress progress(progress_cb,
                      static_cast<double>(largest_pt.size()) * image_cost +
                          page_count * (kCostCompressPage + kCostCompressWritePerPage),
                      cancel);

    if (!largest_pt.empty()) {
        const long long image_total = static_cast<long long>(largest_pt.size());
        progress.phase(count_label("Extraction des images", 0, image_total));
        long long n = 0;
        for (const auto &[xref, placed] : largest_pt) {
            ++n;
            if (auto optimized = extract_placed_image(doc, xref, params, placed)) {
                std::string key = std::to_string(xref);
                image_meta[key] = Value::object({{"w", Value(optimized->width)},
                                                 {"h", Value(optimized->height)},
                                                 {"s", Value(static_cast<long long>(optimized->payload.size()))}});
                images_data[key] = std::move(optimized->payload);
            }
            progress.advance(image_cost, count_label("Extraction des images", n, image_total));
        }
    }

    progress.phase(count_label("Analyse des pages", 0, page_count));
    Value pages = Value::array();
    Value page_sizes = Value::array();
    int next_artwork_id = kArtworkIdTop;
    Faces faces(doc.ctx());

    for (int page_idx = 0; page_idx < page_count; ++page_idx) {
        mu::Page page(doc, page_idx);
        PageDrawings drawings;
        visible_drawings(page, drawings);
        std::vector<Rect> regions;
        if (!skip_images)
            regions = artwork_regions(page, drawings.visible);
        // Unreadable text is rasterised whatever the image setting: the
        // operator chose to drop pictures, not to lose their own entries.
        for (const Rect &r : unreadable_text_regions(page))
            regions.push_back(r);

        std::vector<std::pair<std::string, Rect>> rendered;
        for (const Rect &region : regions) {
            auto tile = render_region(page, region, params);
            if (!tile)
                continue;
            std::string key = std::to_string(next_artwork_id--);
            image_meta[key] = Value::object({{"w", Value(static_cast<long long>(region.width()))},
                                             {"h", Value(static_cast<long long>(region.height()))},
                                             {"s", Value(static_cast<long long>(tile->size()))}});
            images_data[key] = std::move(*tile);
            rendered.emplace_back(key, region);
        }

        std::vector<Rect> artwork;
        artwork.reserve(rendered.size());
        for (const auto &item : rendered)
            artwork.push_back(item.second);
        Value page_data = extract_page(page, page_idx, images_data, precision, artwork, faces, drawings);

        // Placed after extraction so the artwork sits above the fills and
        // below the text, exactly where the original drew it.
        for (const auto &[key, region] : rendered)
            page_data["ir"].push_back(Value::object({{"x", Value(key)}, {"b", quantize_box(region, precision)}}));

        pages.push_back(std::move(page_data));
        Rect rect = page.rect();
        page_sizes.push_back(Value::object({{"w", Value(rect.width())}, {"h", Value(rect.height())}}));
        progress.advance(kCostCompressPage, count_label("Analyse des pages", page_idx + 1, page_count));
    }

    Value manifest = Value::object({{"v", Value("1.0.6")},
                                    {"src", Value(source_name)},
                                    {"tl", Value(kTextLayout)},
                                    {"pages", std::move(pages)},
                                    {"page_sizes", std::move(page_sizes)},
                                    {"images", std::move(image_meta)}});

    progress.phase("Compression LZMA de l'archive…");
    psdi::Bytes data = params.compact ? psdi::write_compact_struct(manifest, images_data, params.preset.lzma_preset)
                                      : psdi::write_struct(manifest, images_data, params.preset.lzma_preset);
    progress.finish("Archive construite");
    return data;
}

// --- page images ------------------------------------------------------------------------

int image_version(const Params &params) {
    return params.compact ? psdi::ARCHIVE_VERSION_COMPACT_IMAGE : psdi::ARCHIVE_VERSION_IMAGE;
}

psdi::Bytes compress_image(const std::string &pdf_path, const Params &params, const ProgressFn &progress_cb,
                           const std::atomic<bool> *cancel) {
    mu::Context ctx;
    mu::Document doc(ctx, pdf_path);
    const int dpi = params.preset.dpi;
    const int total = doc.page_count();
    Progress progress(progress_cb, total * (kCostRenderPage + kCostRenderWritePerPage), cancel);

    const std::string label = "Rendu des pages à " + std::to_string(dpi) + " ppp";
    std::vector<psdi::ImagePage> pages;
    progress.phase(count_label(label, 0, total));
    for (int i = 0; i < total; ++i) {
        mu::Page page(doc, i);
        mu::Raster pix = page.render(dpi / 72.0, std::nullopt);
        Rect rect = page.rect();
        pages.push_back({rect.width(), rect.height(), encode_page(image_of_raster(pix), params)});
        progress.advance(kCostRenderPage, count_label(label, i + 1, total));
    }

    progress.phase("Écriture de l'archive…");
    psdi::Bytes data = psdi::write_image(pages, params.preset.lzma_preset, image_version(params));
    progress.finish("Archive construite");
    return data;
}

// _page_size_for: the image's aspect ratio, its longer side on A4's.
std::pair<double, double> page_size_for(int width_px, int height_px) {
    if (width_px <= 0 || height_px <= 0)
        return {kA4WidthPt, kA4HeightPt};
    const double long_pt = kA4HeightPt;
    if (width_px > height_px)
        return {long_pt, long_pt * height_px / width_px};
    return {long_pt * width_px / height_px, long_pt};
}

// _load_image_as_page: one page per frame, EXIF orientation applied,
// transparency flattened onto white, the colour of paper.
std::vector<img::Image> load_image_as_page(const std::string &path) {
    std::vector<img::Image> frames;
    mu::Context mupdf;
    for (img::Image &source : img::open_frames(read_file(path), &mupdf)) {
        img::Image frame = img::exif_transpose(source);
        switch (frame.mode()) {
        case img::Mode::RGBA:
        case img::Mode::LA:
        case img::Mode::P: {
            img::Image background = img::Image::filled(img::Mode::RGB, frame.width(), frame.height(), {255, 255, 255, 255});
            img::Image rgba = frame.convert(img::Mode::RGBA);
            img::Image alpha = rgba.band(3);
            background.paste(rgba, &alpha);
            frame = background;
            break;
        }
        case img::Mode::RGB:
            break;
        default:
            frame = frame.convert(img::Mode::RGB);
            break;
        }
        frames.push_back(std::move(frame));
    }
    return frames;
}

psdi::Bytes compress_image_file(const std::string &path, const Params &params, const ProgressFn &progress_cb,
                                const std::atomic<bool> *cancel) {
    std::vector<img::Image> frames = load_image_as_page(path);
    if (frames.empty())
        throw std::runtime_error("No image data found in " + path);

    const int dpi = params.preset.dpi;
    const long long total = static_cast<long long>(frames.size());
    Progress progress(progress_cb, static_cast<double>(total) * (kCostRenderPage + kCostRenderWritePerPage), cancel);

    const std::string label = "Préparation des images à " + std::to_string(dpi) + " ppp";
    std::vector<psdi::ImagePage> pages;
    progress.phase(count_label(label, 0, total));
    for (long long i = 0; i < total; ++i) {
        img::Image frame = std::move(frames[static_cast<size_t>(i)]);
        auto [width_pt, height_pt] = page_size_for(frame.width(), frame.height());
        int tw = static_cast<int>(std::max(1LL, py::round_int(width_pt * dpi / 72)));
        int th = static_cast<int>(std::max(1LL, py::round_int(height_pt * dpi / 72)));
        if (frame.width() != tw || frame.height() != th)
            frame = frame.resize(tw, th, img::Filter::Lanczos);
        pages.push_back({width_pt, height_pt, encode_page(frame, params)});
        progress.advance(kCostRenderPage, count_label(label, i + 1, total));
    }

    progress.phase("Écriture de l'archive…");
    psdi::Bytes data = psdi::write_image(pages, params.preset.lzma_preset, image_version(params));
    progress.finish("Archive construite");
    return data;
}

} // namespace

// --- progress -----------------------------------------------------------------------------

void report(const ProgressFn &cb, int step, int total, const std::string &message) {
    if (!cb)
        return;
    try {
        cb(step, total, message);
    } catch (const Cancelled &) {
        throw;
    } catch (...) {
        // A faulty callback never aborts a conversion (_progress).
    }
}

Progress::Progress(ProgressFn cb, double total_units, const std::atomic<bool> *cancel)
    : cb_(std::move(cb)), total_(std::max(total_units, 1.0)), cancel_(cancel) {}

void Progress::advance(double units, const std::string &message, bool force) {
    done_ = std::min(done_ + units, total_);
    emit(message, force);
}

void Progress::phase(const std::string &message) { emit(message, true); }

void Progress::finish(const std::string &message) {
    done_ = total_;
    emit(message, true);
}

void Progress::emit(const std::string &message, bool force) {
    // Every report is a point where the operator's cancellation takes
    // effect: once per image, page or line.
    if (cancel_ && cancel_->load(std::memory_order_relaxed))
        throw Cancelled();
    if (!cb_)
        return;
    double now = monotonic_seconds();
    if (!force && now - last_time_ < kProgressMinInterval)
        return;
    last_time_ = now;
    long long step = py::round_int(done_ / total_ * PROGRESS_SCALE);
    report(cb_, static_cast<int>(std::min<long long>(step, PROGRESS_SCALE)), PROGRESS_SCALE, message);
}

// --- the front door ---------------------------------------------------------------------

bool is_image_file(const std::string &path) {
    std::string lower = py::lower_ascii(path);
    for (const char *ext : kImageExtensions)
        if (py::endswith(lower, ext))
            return true;
    return false;
}

std::pair<std::string, std::optional<std::string>> detect_optimal_mode(const std::string &pdf_path) {
    try {
        mu::Context ctx;
        std::optional<mu::Document> doc;
        try {
            doc.emplace(ctx, pdf_path);
        } catch (const std::exception &) {
            return {MODE_STRUCT, std::nullopt};
        }
        const int page_count = doc->page_count();
        if (page_count == 0)
            return {MODE_STRUCT, std::nullopt};

        int image_only_pages = 0;
        for (int i = 0; i < page_count; ++i) {
            mu::Page page(*doc, i);
            // A rotated page cannot be rebuilt from unrotated coordinates.
            if (page.rotation() % 360 != 0)
                return {MODE_IMAGE, std::string("rotation")};

            size_t text_len = page.text_length_stripped();
            Rect rect = page.rect();
            double page_area = std::abs(rect.width() * rect.height());
            if (page_area == 0)
                page_area = 1.0;

            double img_area = 0.0;
            for (const Rect &b : page.image_block_boxes())
                img_area += std::abs((b.x1 - b.x0) * (b.y1 - b.y0));

            if (text_len < kTextMinChars && img_area / page_area >= kImageCoverMin)
                ++image_only_pages;
        }
        if (image_only_pages >= std::max(1, page_count / 2))
            return {MODE_IMAGE, std::string("scan")};
        return {MODE_STRUCT, std::nullopt};
    } catch (const std::exception &) {
        return {MODE_STRUCT, std::nullopt};
    }
}

CompressReport pdf_to_archive(const std::string &path, const CompressOptions &options, const ProgressFn &progress,
                              const std::atomic<bool> *cancel) {
    std::optional<long long> size = file_size(path);
    if (!size)
        throw std::runtime_error("PDF not found: " + path);
    const Preset *preset = find_preset(options.quality);
    if (!preset) {
        std::string names;
        for (const auto &p : presets())
            names += (names.empty() ? "" : ", ") + p.id;
        throw std::invalid_argument("Invalid quality: " + options.quality + ". Choose from " + names);
    }
    std::string mode = options.mode;
    if (mode != MODE_STRUCT && mode != MODE_IMAGE)
        throw std::invalid_argument("Invalid mode: " + mode + ". Choose struct or image");

    Params params{*preset, options.compact};
    const long long original_size = *size;
    const double started = monotonic_seconds();
    const std::string source = options.source_name.empty() ? basename(path) : options.source_name;

    // Mode detection reads every page before any phase can report, so the
    // bar gets a label straight away rather than staying blank for it.
    report(progress, 0, PROGRESS_SCALE, "Analyse du document (" + std::to_string(original_size / 1024) + " ko)…");

    std::optional<std::string> auto_switch_reason;
    const bool image_file = is_image_file(source) || is_image_file(path);
    if (image_file) {
        // Not a PDF at all: a photo or scan; only the page-image encoding applies.
        mode = MODE_IMAGE;
        auto_switch_reason = "image";
    } else if (mode == MODE_STRUCT) {
        auto [detected, reason] = detect_optimal_mode(path);
        // Text-only transfers deliberately discard images, so a page that is
        // merely image-heavy is not a reason to switch. A rotated page is.
        if (detected != mode && (!options.skip_images || reason == std::optional<std::string>("rotation"))) {
            auto_switch_reason = reason;
            mode = detected;
        }
    }
    if (cancel && cancel->load())
        throw Cancelled();

    psdi::Bytes archive;
    if (image_file)
        archive = compress_image_file(path, params, progress, cancel);
    else if (mode == MODE_IMAGE)
        archive = compress_image(path, params, progress, cancel);
    else
        archive = compress_struct(path, source, params, progress, options.skip_images, cancel);

    report(progress, PROGRESS_SCALE, PROGRESS_SCALE, "Compression terminée");
    const double elapsed = monotonic_seconds() - started;

    CompressReport info;
    info.source = source;
    info.original_size = original_size;
    info.archive_size = static_cast<long long>(archive.size());
    info.ratio_percent = original_size > 0
                             ? py::round_to(static_cast<double>(archive.size()) / static_cast<double>(original_size) * 100, 1)
                             : 0.0;
    info.compression_time = py::round_to(elapsed, 2);
    info.quality = options.quality;
    info.mode = mode;
    info.auto_mode_switch = auto_switch_reason;
    info.estimates = estimate_times(info.archive_size);
    info.compact = options.compact;
    info.extension = std::string(options.compact ? psdi::COMPACT_EXTENSION : psdi::EXTENSION);
    info.archive = std::move(archive);
    return info;
}

} // namespace pdft::engine
