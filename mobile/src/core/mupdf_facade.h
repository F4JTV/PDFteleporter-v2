// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// MuPDF, as the engine sees it through PyMuPDF.
//
// The desktop version reads PDF documents through PyMuPDF, and every
// decision its engine makes rests on what PyMuPDF reports: the characters of
// a page with their boxes, its vector paths with their clips, where its
// images are placed. This layer reports the same things from the same MuPDF
// release, value for value -- floats where PyMuPDF has floats, its
// corrections to MuPDF's character boxes, its merging of a fill and a stroke
// of the same path -- so that the port of the engine above it can follow the
// original line by line. The tests compare it with PyMuPDF on real documents.
#pragma once

#include "geometry.h"

#include <cstdint>
#include <memory>
#include <optional>
#include <stdexcept>
#include <cstddef>
#include <string>
#include <type_traits>
#include <vector>

struct fz_context;
struct fz_document;
struct fz_page;
struct pdf_document;
struct fz_pixmap;

namespace pdft::mu {

class Error : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

// Runs body(arg) inside a MuPDF error frame of its own, so that no local of
// the caller lives across MuPDF's setjmp. False, with MuPDF's message, on
// error. The body calls MuPDF and nothing that throws.
bool run_guarded(fz_context *ctx, void (*body)(void *), void *arg, char *message, std::size_t size) noexcept;

// f() in such a frame; a MuPDF error becomes Error.
template <class F> void guarded(fz_context *ctx, F &&f) {
    char message[256] = "";
    auto thunk = [](void *p) { (*static_cast<std::remove_reference_t<F> *>(p))(); };
    if (!run_guarded(ctx, thunk, static_cast<void *>(&f), message, sizeof message))
        throw Error(message);
}

// One MuPDF context: a job runs in one, on one thread.
class Context {
public:
    Context();
    ~Context();
    Context(const Context &) = delete;
    Context &operator=(const Context &) = delete;
    fz_context *get() const { return ctx_; }

private:
    fz_context *ctx_ = nullptr;
};

class Document;

// TEXT_FONT_* flags of a span, as PyMuPDF computes them.
enum : int {
    TEXT_FONT_SUPERSCRIPT = 1,
    TEXT_FONT_ITALIC = 2,
    TEXT_FONT_SERIFED = 4,
    TEXT_FONT_MONOSPACED = 8,
    TEXT_FONT_BOLD = 16,
};

// --- page.get_text("rawdict") ---------------------------------------------------
struct RawChar {
    char32_t c = 0;
    Point origin;
    Rect bbox;
    bool synthetic = false;
};

struct RawSpan {
    double size = 0;
    int flags = 0;
    bool has_flags = true;   // spans the engine adds carry no "flags"
    int char_flags = 0;
    std::string font;
    long color = 0;
    bool has_color = true;
    int alpha = 255;
    double ascender = 0;
    double descender = 0;
    Point origin;
    Rect bbox;
    std::vector<RawChar> chars;
};

struct RawLine {
    int wmode = 0;
    Point dir;
    Rect bbox;
    std::vector<RawSpan> spans;
};

struct RawBlock {
    int type = 0;   // 0 text, 1 image
    int number = 0;
    Rect bbox;
    std::vector<RawLine> lines;
};

// --- page.get_drawings() --------------------------------------------------------
struct PathItem {
    enum Kind { Line, Curve, RectItem, QuadItem } kind = Line;
    Point p[4];          // l: p[0]-p[1]; c: p[0]..p[3]
    Rect rect;           // re
    int orientation = 0; // re
    Quad quad;           // qu
};

struct Drawing {
    std::string type;    // "f", "s", "fs", "clip", "group"
    std::vector<PathItem> items;
    Rect rect;           // not for clips
    Rect scissor;        // clips
    // None, or the colour as RGB -- empty where MuPDF gave no colour space.
    std::optional<std::vector<double>> fill;
    std::optional<std::vector<double>> color;
    std::optional<double> width;
    std::optional<bool> close_path;
    std::optional<bool> even_odd;   // fills and clips
    double fill_opacity = 1, stroke_opacity = 1;
    long seqno = -1;     // -1: none (clips and groups)
    long level = 0;
    bool has_level = false;
};

// --- page.get_image_info(xrefs=True) --------------------------------------------
struct ImageInfo {
    int number = 0;
    Rect bbox;
    double transform[6] = {0, 0, 0, 0, 0, 0};
    int width = 0, height = 0;
    int xref = 0;
};

// --- page.get_fonts(full=True) --------------------------------------------------
struct FontEntry {
    int xref = 0;
    std::string ext;
    std::string subtype;
    std::string basefont;
    std::string refname;
    std::string encoding;
    int referencer = 0;
};

struct Link {
    Rect from;
    std::string uri;
    bool is_uri = false;
};

struct Widget {
    int field_type = 0;
    std::string field_type_string;
    std::string field_value;
    bool has_value = false;
    Rect rect;
};

// A decoded raster: 8 bits per component, rows packed, gray or RGB, with or
// without alpha, as PyMuPDF's pixmaps of images are.
struct Raster {
    int w = 0, h = 0;
    int n = 0;          // components including alpha
    bool alpha = false;
    std::string colorspace;  // "DeviceGray", "DeviceRGB", "DeviceCMYK", ...
    std::vector<uint8_t> samples;
};

// doc.extract_image(xref)
struct ExtractedImage {
    std::string ext;
    int smask = 0;
    int width = 0, height = 0;
    int colorspace_n = 0;
    std::string image;   // the bytes PyMuPDF returns
};

class Page {
public:
    Page(Document &doc, int number);
    ~Page();
    Page(const Page &) = delete;
    Page &operator=(const Page &) = delete;

    int number() const { return number_; }
    int rotation() const;          // page.rotation
    Rect rect() const;             // page.rect

    std::vector<RawBlock> rawdict();                // get_text("rawdict")
    std::vector<Rect> image_block_boxes();          // type-1 blocks of get_text("dict")
    size_t text_length_stripped();                  // len(get_text("text").strip())
    std::vector<Drawing> drawings(bool extended);   // get_drawings(extended)
    std::vector<ImageInfo> image_info_xrefs();      // get_image_info(xrefs=True)
    std::vector<FontEntry> fonts();                 // get_fonts(full=True)
    std::vector<int> image_xrefs();                 // [i[0] for i in get_images()]
    std::vector<Link> links();                      // get_links()
    std::vector<Widget> widgets();                  // widgets()
    // get_pixmap(matrix=Matrix(zoom, zoom), clip=clip): RGB, no alpha.
    Raster render(double zoom, const std::optional<Rect> &clip);

    fz_page *get() const { return page_; }
    Document &document() const { return doc_; }

private:
    Document &doc_;
    int number_;
    fz_page *page_ = nullptr;
    std::optional<std::vector<ImageInfo>> image_info_cache_;
    std::vector<std::string> digests_;
};

class Document {
public:
    Document(Context &ctx, const std::string &path);
    // A document made in memory (a new PDF): keeps a reference of its own.
    Document(Context &ctx, fz_document *doc);
    ~Document();
    Document(const Document &) = delete;
    Document &operator=(const Document &) = delete;

    int page_count() const;
    fz_context *ctx() const { return ctx_.get(); }
    fz_document *get() const { return doc_; }
    pdf_document *pdf() const { return pdf_; }

    // doc.extract_image(xref); nullopt where PyMuPDF raises.
    std::optional<ExtractedImage> extract_image(int xref);
    // Pixmap(doc, xref) and its MD5 digest.
    Raster image_pixmap(int xref);
    std::string image_digest(int xref);
    // Pixmap(Pixmap(doc, xref) without alpha, Pixmap(doc, smask)).tobytes("png"):
    // the image with its soft mask applied, as MuPDF writes it. nullopt
    // where PyMuPDF raises.
    std::optional<std::string> image_with_mask_png(int xref, int smask);
    // doc.extract_font(xref)[3]
    std::string font_buffer(int xref);
    // fitz.Font(fontbuffer=buf).name; empty if the buffer is no font.
    std::string font_name_from_buffer(const std::string &buffer);
    // doc.xref_get_key(xref, key): (type, text)
    std::pair<std::string, std::string> xref_get_key(int xref, const std::string &key);
    // doc.xref_object(xref, compressed): the object's source.
    std::string xref_object(int xref, bool compressed);
    // doc.xref_stream(xref): the decoded stream, nullopt for no stream.
    std::optional<std::string> xref_stream(int xref);
    // doc.xref_set_key(xref, key, value), PyMuPDF's way: the object printed
    // with a placeholder for the key, the placeholder replaced by `value`,
    // and the text parsed back.
    void xref_set_key(int xref, const std::string &key, const std::string &value);
    // doc.update_object(xref, text)
    void update_object(int xref, const std::string &text);

private:
    Context &ctx_;
    fz_document *doc_ = nullptr;
    pdf_document *pdf_ = nullptr;
};

// Decode an image file or payload the way MuPDF does (JPEG, PNG, JPX...).
// Returns nullopt when MuPDF cannot read it.
std::optional<Raster> decode_with_mupdf(Context &ctx, const std::string &data);
// PNG through MuPDF's own decoder (exact).

} // namespace pdft::mu
