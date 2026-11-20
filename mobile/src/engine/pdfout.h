// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// The PDF operations the rebuild performs, done as PyMuPDF 2.1 does them over
// MuPDF 1.28.6: the same MuPDF calls, with the same arguments, numbers
// rounded and printed the same way, and the content streams PyMuPDF writes.
// The rebuilt PDF is then the desktop version's, object for object, but for
// its random /ID.
//
// Each function names the PyMuPDF method it stands for. Only the paths the
// rebuild takes are implemented: unrotated pages with their MediaBox at the
// origin, opaque drawing, overlay insertion.
#pragma once

#include "geometry.h"

#include <array>
#include <cstdint>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <vector>

struct fz_context;
struct fz_font;
struct fz_page;
struct fz_pixmap;
struct pdf_document;
struct pdf_page;

namespace pdft::mu {
class Context;
class Document;
} // namespace pdft::mu

namespace pdft::engine::pdfout {

// PyMuPDF's Matrix: six Python floats; every product, inverse and transform
// goes through MuPDF in single precision (fz_concat, fz_transform_*).
struct Matrix {
    double a = 1, b = 0, c = 0, d = 1, e = 0, f = 0;
};
Matrix concat(const Matrix &one, const Matrix &two);
Matrix invert(const Matrix &m);
std::array<double, 2> transform_point(double x, double y, const Matrix &m);
Rect transform_rect(const Rect &r, const Matrix &m);

// _format_g: a number as MuPDF's printf writes "%g", the shortest form of
// its single-precision value; several separated by spaces.
std::string g(double value);
std::string g(std::initializer_list<double> values);

class Document;

class Page {
public:
    int number() const { return number_; }
    Rect rect() const { return rect_; }                // page.rect
    Matrix transformation_matrix() const { return ctm_; }
    pdf_page *pdf() const { return pdf_; }
    fz_page *fz() const { return page_; }

private:
    friend class Document;
    int number_ = 0;
    fz_page *page_ = nullptr;
    pdf_page *pdf_ = nullptr;
    Rect rect_;
    Matrix ctm_;
    int links_ = 0;    // link annotations named so far (fitz-L<n>)
    int images_ = 0;   // image names given so far (fzImg<n>)
    int forms_ = 0;    // form names given so far (fzFrm<n>)
    friend class Shape;
    friend class TextWriter;
};

// A decoded picture handed to insert_image as a pixmap: grey or RGB.
struct Pixels {
    int width = 0, height = 0;
    int components = 3;   // 1 or 3
    std::string samples;  // rows packed
};

class Document {
public:
    explicit Document(mu::Context &ctx);   // fitz.open()
    ~Document();
    Document(const Document &) = delete;
    Document &operator=(const Document &) = delete;

    fz_context *ctx() const { return ctx_; }
    pdf_document *pdf() const { return pdf_; }
    // The document through the reading facade (get_fonts, xref_get_key...).
    mu::Document &view() { return *view_; }
    int page_count() const { return static_cast<int>(pages_.size()); }

    Page &new_page(double width, double height);   // doc.new_page(width=, height=)

    // page.insert_image(rect, stream=payload) and (rect, pixmap=...).
    void insert_image(Page &page, const Rect &rect, const std::string &stream);
    void insert_image(Page &page, const Rect &rect, const Pixels &pixmap);
    // page.insert_link({"kind": LINK_URI, "from": rect, "uri": uri})
    void insert_link_uri(Page &page, const Rect &from, const std::string &uri);
    // page.insert_htmlbox(rect, html, css=css, scale_low=scale_low)
    void insert_htmlbox(Page &page, const Rect &rect, const std::string &html, const std::string &css,
                        double scale_low);

    // doc.subset_fonts()
    void subset_fonts();
    // doc.save(..., deflate=True, deflate_images=, garbage=4)
    std::string save(bool deflate_images, int garbage);

    // TOOLS._insert_contents(page, data, overlay): a new content stream.
    int insert_contents(Page &page, const std::string &data, bool overlay);
    // doc.update_stream(xref, data): replaced, deflated where that pays.
    void update_stream(int xref, const std::string &data);

private:
    void place_image(Page &page, const Rect &rect, const std::string *stream, const Pixels *pixmap);

    fz_context *ctx_;
    pdf_document *pdf_ = nullptr;
    std::unique_ptr<mu::Document> view_;
    std::vector<std::unique_ptr<Page>> pages_;
    std::map<std::string, int> inserted_images_;   // doc.InsertedImages
};

// page.new_shape(): drawing commands gathered, then written as one stream.
class Shape {
public:
    explicit Shape(Page &page);
    void draw_rect(const Rect &r);
    void draw_polyline(const std::vector<std::array<double, 2>> &points);
    // finish(width=, color=, fill=, closePath=); colours are 0..1 triples.
    void finish(double width, std::optional<std::array<double, 3>> color,
                std::optional<std::array<double, 3>> fill, bool close_path);
    void commit(Document &doc);

private:
    Page &page_;
    Matrix ipctm_;
    std::string draw_cont_;
    std::string totalcont_;
};

// fitz.TextWriter(page.rect): spans gathered, then written in one colour.
class TextWriter {
public:
    TextWriter(fz_context *ctx, const Rect &page_rect);
    ~TextWriter();
    TextWriter(const TextWriter &) = delete;
    TextWriter &operator=(const TextWriter &) = delete;

    // append(pos, text, font=font, fontsize=size); throws on MuPDF errors.
    void append(double x, double y, const std::string &utf8, fz_font *font, double fontsize);
    // write_text(page, color=rgb)
    void write_text(Document &doc, Page &page, const std::array<double, 3> &rgb);

private:
    fz_context *ctx_;
    void *text_ = nullptr;   // fz_text: a typedef MuPDF does not name
    Rect rect_;
    Matrix ictm_;
    std::vector<fz_font *> used_fonts_;   // monospaced fonts, for repair_mono_font
};

} // namespace pdft::engine::pdfout
