// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// See pdfout.h. The PyMuPDF functions ported here are in the PyMuPDF 2.1
// sources (src/__init__.py): Shape, TextWriter, Page.insert_image and
// _insert_image, calc_image_matrix, Page.insert_link and getLinkText,
// Page.insert_htmlbox with Story.fit_scale and Page.show_pdf_page,
// JM_insert_contents, JM_merge_resources, JM_update_stream,
// repair_mono_font, Document.subset_fonts and Document.save.

#include "pdfout.h"

#include "mupdf_facade.h"
#include "pyutil.h"

extern "C" {
#include <mupdf/fitz.h>
#include <mupdf/pdf.h>
}

#include <algorithm>
#include <cfloat>
#include <cmath>
#include <cstring>
#include <set>

namespace pdft::engine::pdfout {

namespace {

// MuPDF calls whose error becomes a C++ exception once MuPDF's error frame is
// left; nothing with a destructor may live inside the frame.
#define OUT_TRY(ctx, ...) ::pdft::mu::guarded((ctx), [&]() { __VA_ARGS__; })

fz_matrix fzm(const Matrix &m) {
    return fz_make_matrix(static_cast<float>(m.a), static_cast<float>(m.b), static_cast<float>(m.c),
                          static_cast<float>(m.d), static_cast<float>(m.e), static_cast<float>(m.f));
}
Matrix pym(fz_matrix m) { return {m.a, m.b, m.c, m.d, m.e, m.f}; }
fz_rect fzr(const Rect &r) {
    return fz_make_rect(static_cast<float>(r.x0), static_cast<float>(r.y0), static_cast<float>(r.x1),
                        static_cast<float>(r.y1));
}
Rect pyr(fz_rect r) { return {r.x0, r.y0, r.x1, r.y1}; }

// Python's Rect.width and .height: max(0, x1 - x0).
double width_of(const Rect &r) {
    double v = r.x1 - r.x0;
    return v > 0 ? v : 0.0;
}
double height_of(const Rect &r) {
    double v = r.y1 - r.y0;
    return v > 0 ? v : 0.0;
}

bool is_infinite(const Rect &r) {
    return r.x0 == FZ_MIN_INF_RECT && r.y0 == FZ_MIN_INF_RECT && r.x1 == FZ_MAX_INF_RECT && r.y1 == FZ_MAX_INF_RECT;
}
bool is_empty(const Rect &r) { return r.x0 >= r.x1 || r.y0 >= r.y1; }

// min() and max() of two Python floats: the first wins a tie.
double pymin(double a, double b) { return b < a ? b : a; }

// JM_TUPLE: each value rounded to five decimals, anything under 1e-4 made 0.
double jm(double x) { return std::abs(x) >= 1e-4 ? py::round_to(x, 5) : 0.0; }
Matrix jm(const Matrix &m) { return {jm(m.a), jm(m.b), jm(m.c), jm(m.d), jm(m.e), jm(m.f)}; }

// calc_matrix(sr, tr, keep=True, rotate=0)
Matrix calc_matrix(const Rect &sr, const Rect &tr) {
    double smp_x = (sr.x0 + sr.x1) / 2.0, smp_y = (sr.y0 + sr.y1) / 2.0;
    double tmp_x = (tr.x0 + tr.x1) / 2.0, tmp_y = (tr.y0 + tr.y1) / 2.0;
    // Matrix(0): cos and sin rounded to eight places, c = -sin
    Matrix rotation{1.0, 0.0, -0.0, 1.0, 0.0, 0.0};
    Matrix m = concat(Matrix{1, 0, 0, 1, -smp_x, -smp_y}, rotation);
    Rect sr1 = transform_rect(sr, m);
    if (width_of(sr1) == 0 || height_of(sr1) == 0)
        throw std::domain_error("float division by zero");
    double fw = width_of(tr) / width_of(sr1);
    double fh = height_of(tr) / height_of(sr1);
    fw = fh = pymin(fw, fh);
    m = concat(m, Matrix{fw, 0, 0, fh, 0, 0});
    m = concat(m, Matrix{1, 0, 0, 1, tmp_x, tmp_y});
    return jm(m);
}

// calc_image_matrix(width, height, tr, 0, True)
Matrix calc_image_matrix(int width, int height, const Rect &tr) {
    Matrix ret = calc_matrix(Rect(0, 0, width, height), tr);
    return concat(Matrix{static_cast<double>(width), 0, 0, static_cast<double>(height), 0, 0}, ret);
}

std::string buffer_string(fz_context *ctx, fz_buffer *buf) {
    unsigned char *data = nullptr;
    size_t len = fz_buffer_storage(ctx, buf, &data);
    return std::string(reinterpret_cast<const char *>(data), len);
}

// JM_insert_contents: a new stream object holding `data`, appended to (or put
// before) the page's /Contents, made an array if it was not one.
int insert_contents(fz_context *ctx, pdf_document *pdf, pdf_obj *pageref, const std::string &data, bool overlay) {
    fz_buffer *buf = nullptr;
    pdf_obj *newconts = nullptr;
    pdf_obj *carr = nullptr;
    int xref = 0;
    fz_var(xref);
    fz_var(buf);
    fz_var(newconts);
    fz_var(carr);
    bool failed = false;
    char message[256] = "";
    fz_try(ctx) {
        buf = fz_new_buffer_from_copied_data(ctx, reinterpret_cast<const unsigned char *>(data.data()), data.size());
        pdf_obj *contents = pdf_dict_get(ctx, pageref, PDF_NAME(Contents));
        newconts = pdf_add_stream(ctx, pdf, buf, nullptr, 0);
        xref = pdf_to_num(ctx, newconts);
        if (pdf_is_array(ctx, contents)) {
            if (overlay)
                pdf_array_push(ctx, contents, newconts);
            else
                pdf_array_insert(ctx, contents, newconts, 0);
        } else {
            carr = pdf_new_array(ctx, pdf, 5);
            if (overlay) {
                if (contents)
                    pdf_array_push(ctx, carr, contents);
                pdf_array_push(ctx, carr, newconts);
            } else {
                pdf_array_push(ctx, carr, newconts);
                if (contents)
                    pdf_array_push(ctx, carr, contents);
            }
            pdf_dict_put(ctx, pageref, PDF_NAME(Contents), carr);
        }
    }
    fz_always(ctx) {
        pdf_drop_obj(ctx, carr);
        pdf_drop_obj(ctx, newconts);
        fz_drop_buffer(ctx, buf);
    }
    fz_catch(ctx) {
        failed = true;
        std::strncpy(message, fz_caught_message(ctx), sizeof message - 1);
    }
    if (failed)
        throw mu::Error(message);
    return xref;
}

// JM_update_stream(doc, obj, buffer, compress=1)
void update_stream(fz_context *ctx, pdf_document *pdf, pdf_obj *obj, fz_buffer *buffer) {
    size_t length = fz_buffer_storage(ctx, buffer, nullptr);
    if (length > 30) {
        unsigned char *data = nullptr;
        size_t compressed_length = 0;
        fz_buffer *compressed = nullptr;
        fz_var(data);
        fz_var(compressed);
        bool done = false;
        bool failed = false;
        char message[256] = "";
        fz_var(done);
        fz_var(failed);
        fz_try(ctx) {
            data = fz_new_deflated_data_from_buffer(ctx, &compressed_length, buffer, FZ_DEFLATE_BEST);
            if (data && compressed_length) {
                compressed = fz_new_buffer_from_data(ctx, data, compressed_length);
                data = nullptr;
                fz_resize_buffer(ctx, compressed, compressed_length);
                if (compressed_length < length) {
                    pdf_dict_put(ctx, obj, PDF_NAME(Filter), PDF_NAME(FlateDecode));
                    pdf_update_stream(ctx, pdf, obj, compressed, 1);
                    done = true;
                }
            }
        }
        fz_always(ctx) {
            fz_free(ctx, data);
            fz_drop_buffer(ctx, compressed);
        }
        fz_catch(ctx) {
            failed = true;
            std::strncpy(message, fz_caught_message(ctx), sizeof message - 1);
        }
        if (failed)
            throw mu::Error(message);
        if (done)
            return;
    }
    OUT_TRY(ctx, pdf_update_stream(ctx, pdf, obj, buffer, 0));
}

// The Python string of a ColorCode: "r g b RG " or "r g b rg ".
std::string color_code(const std::array<double, 3> &c, bool stroke) {
    for (double v : c)
        if (v < 0 || v > 1)
            throw std::invalid_argument("need 1, 3 or 4 color components in range 0 to 1");
    return g({c[0], c[1], c[2]}) + (stroke ? " RG " : " rg ");
}

// The lines of a content stream, as str.splitlines() makes them of the
// ASCII the PDF device writes.
std::vector<std::string> split_lines(const std::string &text) {
    std::vector<std::string> lines;
    size_t start = 0;
    for (size_t i = 0; i < text.size(); ++i) {
        char c = text[i];
        if (c == '\n' || c == '\r' || c == '\x0b' || c == '\x0c' || c == '\x1c' || c == '\x1d' || c == '\x1e') {
            lines.push_back(text.substr(start, i - start));
            if (c == '\r' && i + 1 < text.size() && text[i + 1] == '\n')
                ++i;
            start = i + 1;
        }
    }
    if (start < text.size())
        lines.push_back(text.substr(start));
    return lines;
}

bool ends_with(const std::string &s, const char *suffix) {
    size_t n = std::strlen(suffix);
    return s.size() >= n && s.compare(s.size() - n, n, suffix) == 0;
}

// str.replace(old, new): every occurrence.
std::string replace_all(std::string s, const std::string &from, const std::string &to) {
    size_t at = 0;
    while ((at = s.find(from, at)) != std::string::npos) {
        s.replace(at, from.size(), to);
        at += to.size();
    }
    return s;
}

// fz_atoi
int atoi_of(const std::string &s) { return fz_atoi(s.c_str()); }

// JM_merge_resources: the fonts and graphic states a text device made, renamed
// after the page's own. Returns the offsets (max_alp, max_fonts).
std::pair<int, int> merge_resources(fz_context *ctx, pdf_page *page, pdf_obj *temp_res) {
    int max_alp = -1, max_fonts = -1;
    pdf_document *doc = page->doc;
    std::vector<std::string> names;
    // The page's names, read first.
    pdf_obj *resources = nullptr, *main_extg = nullptr, *main_fonts = nullptr, *temp_extg = nullptr,
            *temp_fonts = nullptr;
    OUT_TRY(ctx, {
        resources = pdf_dict_get(ctx, page->obj, PDF_NAME(Resources));
        if (!resources)
            resources = pdf_dict_put_dict(ctx, page->obj, PDF_NAME(Resources), 5);
        main_extg = pdf_dict_get(ctx, resources, PDF_NAME(ExtGState));
        main_fonts = pdf_dict_get(ctx, resources, PDF_NAME(Font));
        temp_extg = pdf_dict_get(ctx, temp_res, PDF_NAME(ExtGState));
        temp_fonts = pdf_dict_get(ctx, temp_res, PDF_NAME(Font));
    });
    auto key_name = [&](pdf_obj *dict, int i) {
        const char *name = nullptr;
        OUT_TRY(ctx, name = pdf_to_name(ctx, pdf_dict_get_key(ctx, dict, i)));
        return std::string(name ? name : "");
    };
    auto dict_len = [&](pdf_obj *dict) {
        int n = 0;
        OUT_TRY(ctx, n = pdf_dict_len(ctx, dict));
        return n;
    };
    if (pdf_is_dict(ctx, temp_extg)) {
        int n = dict_len(temp_extg);
        if (pdf_is_dict(ctx, main_extg)) {
            for (int i = 0, len = dict_len(main_extg); i < len; ++i) {
                std::string alp = key_name(main_extg, i);
                if (alp.compare(0, 3, "Alp") != 0)
                    continue;
                int j = atoi_of(alp.substr(3));
                if (j > max_alp)
                    max_alp = j;
            }
        } else {
            OUT_TRY(ctx, main_extg = pdf_dict_put_dict(ctx, resources, PDF_NAME(ExtGState), n));
        }
        max_alp += 1;
        for (int i = 0; i < n; ++i) {
            std::string alp = key_name(temp_extg, i);
            std::string text = "Alp" + std::to_string(atoi_of(alp.size() > 3 ? alp.substr(3) : "") + max_alp);
            OUT_TRY(ctx, pdf_dict_puts(ctx, main_extg, text.c_str(), pdf_dict_get_val(ctx, temp_extg, i)));
        }
    }
    if (pdf_is_dict(ctx, main_fonts)) {
        for (int i = 0, len = dict_len(main_fonts); i < len; ++i) {
            std::string font = key_name(main_fonts, i);
            if (font.compare(0, 1, "F") != 0)
                continue;
            int j = atoi_of(font.substr(1));
            if (j > max_fonts)
                max_fonts = j;
        }
    } else {
        OUT_TRY(ctx, main_fonts = pdf_dict_put_dict(ctx, resources, PDF_NAME(Font), 2));
    }
    max_fonts += 1;
    for (int i = 0, len = dict_len(temp_fonts); i < len; ++i) {
        std::string font = key_name(temp_fonts, i);
        std::string text = "F" + std::to_string(atoi_of(font.size() > 1 ? font.substr(1) : "") + max_fonts);
        OUT_TRY(ctx, pdf_dict_puts(ctx, main_fonts, text.c_str(), pdf_dict_get_val(ctx, temp_fonts, i)));
    }
    (void)doc;
    return {max_alp, max_fonts};
}

} // namespace

// --- numbers and matrices -----------------------------------------------------------------

std::string g(double value) {
    char buf[64];
    fz_snprintf(buf, sizeof buf, "%g", value);
    return buf;
}

std::string g(std::initializer_list<double> values) {
    std::string out;
    for (double v : values) {
        if (!out.empty())
            out += ' ';
        out += g(v);
    }
    return out;
}

Matrix concat(const Matrix &one, const Matrix &two) { return pym(fz_concat(fzm(one), fzm(two))); }

Matrix invert(const Matrix &m) {
    // util_invert_matrix, computed in Python floats from MuPDF's single
    // precision fields, stored back in them.
    fz_matrix src = fzm(m);
    double a = src.a;
    double det = a * src.d - static_cast<double>(src.b) * src.c;
    if (det < -DBL_EPSILON || det > DBL_EPSILON) {
        fz_matrix dst;
        double rdet = 1 / det;
        dst.a = static_cast<float>(src.d * rdet);
        dst.b = static_cast<float>(-static_cast<double>(src.b) * rdet);
        dst.c = static_cast<float>(-static_cast<double>(src.c) * rdet);
        dst.d = static_cast<float>(a * rdet);
        a = -static_cast<double>(src.e) * dst.a - static_cast<double>(src.f) * dst.c;
        dst.f = static_cast<float>(-static_cast<double>(src.e) * dst.b - static_cast<double>(src.f) * dst.d);
        dst.e = static_cast<float>(a);
        return pym(dst);
    }
    return Matrix{0, 0, 0, 0, 0, 0};   // Matrix() when inversion fails
}

std::array<double, 2> transform_point(double x, double y, const Matrix &m) {
    fz_point p = fz_transform_point(fz_make_point(static_cast<float>(x), static_cast<float>(y)), fzm(m));
    return {p.x, p.y};
}

Rect transform_rect(const Rect &r, const Matrix &m) { return pyr(fz_transform_rect(fzr(r), fzm(m))); }

// --- the document --------------------------------------------------------------------------

Document::Document(mu::Context &ctx) : ctx_(ctx.get()) {
    OUT_TRY(ctx_, pdf_ = pdf_create_document(ctx_));
    view_ = std::make_unique<mu::Document>(ctx, reinterpret_cast<fz_document *>(pdf_));
}

Document::~Document() {
    for (auto &page : pages_)
        fz_drop_page(ctx_, page->page_);
    view_.reset();
    pdf_drop_document(ctx_, pdf_);
}

Page &Document::new_page(double width, double height) {
    // extra._newPage: a unit rectangle stretched to the size, in floats.
    fz_rect mediabox = fz_unit_rect;
    mediabox.x1 = static_cast<float>(width);
    mediabox.y1 = static_cast<float>(height);
    pdf_obj *resources = nullptr;
    pdf_obj *page_obj = nullptr;
    fz_page *page = nullptr;
    fz_var(resources);
    fz_var(page_obj);
    fz_var(page);
    bool failed = false;
    char message[256] = "";
    int number = page_count();
    fz_try(ctx_) {
        resources = pdf_add_new_dict(ctx_, pdf_, 1);
        page_obj = pdf_add_page(ctx_, pdf_, mediabox, 0, resources, nullptr);
        pdf_insert_page(ctx_, pdf_, -1, page_obj);
        page = fz_load_page(ctx_, reinterpret_cast<fz_document *>(pdf_), number);
    }
    fz_always(ctx_) {
        pdf_drop_obj(ctx_, page_obj);
        pdf_drop_obj(ctx_, resources);
    }
    fz_catch(ctx_) {
        failed = true;
        std::strncpy(message, fz_caught_message(ctx_), sizeof message - 1);
    }
    if (failed)
        throw mu::Error(message);
    auto out = std::make_unique<Page>();
    out->number_ = number;
    out->page_ = page;
    out->pdf_ = pdf_page_from_fz_page(ctx_, page);
    fz_rect bound;
    fz_matrix ctm = fz_identity;
    fz_rect unit = fz_unit_rect;
    OUT_TRY(ctx_, {
        bound = fz_bound_page(ctx_, page);
        pdf_page_transform(ctx_, out->pdf_, &unit, &ctm);
    });
    out->rect_ = pyr(bound);
    out->ctm_ = pym(ctm);
    pages_.push_back(std::move(out));
    return *pages_.back();
}

int Document::insert_contents(Page &page, const std::string &data, bool overlay) {
    return pdfout::insert_contents(ctx_, pdf_, page.pdf()->obj, data, overlay);
}

void Document::update_stream(int xref, const std::string &data) {
    // Document.update_stream(xref, stream): the object must be a dictionary.
    pdf_obj *obj = nullptr;
    fz_buffer *res = nullptr;
    OUT_TRY(ctx_, obj = pdf_new_indirect(ctx_, pdf_, xref, 0));
    try {
        if (!pdf_is_dict(ctx_, obj))
            throw mu::Error("is no PDF dict");
        OUT_TRY(ctx_, res = fz_new_buffer_from_copied_data(ctx_, reinterpret_cast<const unsigned char *>(data.data()),
                                                           data.size()));
        pdfout::update_stream(ctx_, pdf_, obj, res);
    } catch (...) {
        fz_drop_buffer(ctx_, res);
        pdf_drop_obj(ctx_, obj);
        throw;
    }
    fz_drop_buffer(ctx_, res);
    pdf_drop_obj(ctx_, obj);
}

void Document::insert_image(Page &page, const Rect &rect, const std::string &stream) {
    place_image(page, rect, &stream, nullptr);
}

void Document::insert_image(Page &page, const Rect &rect, const Pixels &pixmap) {
    place_image(page, rect, nullptr, &pixmap);
}

void Document::place_image(Page &page, const Rect &r, const std::string *stream, const Pixels *pixmap) {
    if (is_empty(r) || is_infinite(r))
        throw std::invalid_argument("rect must be finite and not empty");
    Rect clip = transform_rect(r, invert(page.transformation_matrix()));
    // The first "fzImg<n>" the page's resources do not use: on a rebuilt page
    // the images placed before this one, numbered from 0.
    std::string imgname = "fzImg" + std::to_string(page.images_);

    // _insert_image: a digest of the source finds an image already in the
    // document, else it is added.
    std::string digest(16, '\0');
    fz_pixmap *pix = nullptr;
    fz_buffer *imgbuf = nullptr;
    fz_image *image = nullptr;
    pdf_obj *ref = nullptr;
    fz_var(pix);
    fz_var(imgbuf);
    fz_var(image);
    fz_var(ref);
    auto cleanup = [&]() {
        pdf_drop_obj(ctx_, ref);
        fz_drop_image(ctx_, image);
        fz_drop_buffer(ctx_, imgbuf);
        fz_drop_pixmap(ctx_, pix);
    };
    int w = 0, h = 0;
    try {
        if (pixmap) {
            if (pixmap->components != 1 && pixmap->components != 3)
                throw std::invalid_argument("pixmap must be grey or RGB");
            OUT_TRY(ctx_, {
                fz_colorspace *cs = pixmap->components == 1 ? fz_device_gray(ctx_) : fz_device_rgb(ctx_);
                pix = fz_new_pixmap(ctx_, cs, pixmap->width, pixmap->height, nullptr, 0);
            });
            size_t row = static_cast<size_t>(pixmap->width) * static_cast<size_t>(pixmap->components);
            if (pixmap->samples.size() < row * static_cast<size_t>(pixmap->height))
                throw std::invalid_argument("not enough pixels");
            unsigned char *samples = fz_pixmap_samples(ctx_, pix);
            size_t stride = static_cast<size_t>(fz_pixmap_stride(ctx_, pix));
            for (int y = 0; y < pixmap->height; ++y)
                std::memcpy(samples + static_cast<size_t>(y) * stride, pixmap->samples.data() + static_cast<size_t>(y) * row, row);
            w = pixmap->width;
            h = pixmap->height;
            unsigned char d[16];
            OUT_TRY(ctx_, fz_md5_pixmap(ctx_, pix, d));
            digest.assign(reinterpret_cast<const char *>(d), 16);
            digest.insert(0, "pixmap:");
        } else {
            OUT_TRY(ctx_, imgbuf = fz_new_buffer_from_copied_data(
                              ctx_, reinterpret_cast<const unsigned char *>(stream->data()), stream->size()));
            fz_md5 state;
            unsigned char d[16];
            fz_md5_init(&state);
            fz_md5_update(&state, reinterpret_cast<const unsigned char *>(stream->data()), stream->size());
            fz_md5_final(&state, d);
            digest.assign(reinterpret_cast<const char *>(d), 16);
            digest.insert(0, "stream:");
        }
        auto known = inserted_images_.find(digest);
        if (known != inserted_images_.end()) {
            int img_xref = known->second;
            OUT_TRY(ctx_, {
                ref = pdf_new_indirect(ctx_, pdf_, img_xref, 0);
                w = pdf_to_int(ctx_, pdf_dict_geta(ctx_, ref, PDF_NAME(Width), PDF_NAME(W)));
                h = pdf_to_int(ctx_, pdf_dict_geta(ctx_, ref, PDF_NAME(Height), PDF_NAME(H)));
            });
            if (pixmap) {
                // The pixmap branch keeps the pixmap's own size.
                w = pixmap->width;
                h = pixmap->height;
            }
        } else {
            OUT_TRY(ctx_, {
                if (pixmap) {
                    image = fz_new_image_from_pixmap(ctx_, pix, nullptr);
                } else {
                    image = fz_new_image_from_buffer(ctx_, imgbuf);
                    w = image->w;
                    h = image->h;
                }
                ref = pdf_add_image(ctx_, pdf_, image);
            });
            inserted_images_[digest] = pdf_to_num(ctx_, ref);
        }
        Matrix mat = calc_image_matrix(w, h, clip);
        OUT_TRY(ctx_, {
            pdf_obj *resources = pdf_dict_get_inheritable(ctx_, page.pdf()->obj, PDF_NAME(Resources));
            if (!resources)
                resources = pdf_dict_put_dict(ctx_, page.pdf()->obj, PDF_NAME(Resources), 2);
            pdf_obj *xobject = pdf_dict_get(ctx_, resources, PDF_NAME(XObject));
            if (!xobject)
                xobject = pdf_dict_put_dict(ctx_, resources, PDF_NAME(XObject), 2);
            pdf_dict_puts(ctx_, xobject, imgname.c_str(), ref);
        });
        std::string s = "\nq\n" + g({mat.a, mat.b, mat.c, mat.d, mat.e, mat.f}) + " cm\n/" + imgname + " Do\nQ\n";
        insert_contents(page, s, true);
        ++page.images_;
    } catch (...) {
        cleanup();
        throw;
    }
    cleanup();
}

void Document::insert_link_uri(Page &page, const Rect &from, const std::string &uri) {
    // getLinkText: the "uri" skeleton, the rectangle in PDF coordinates, and
    // a /NM name; the URI goes in unescaped, as PyMuPDF writes it.
    Rect r = transform_rect(from, invert(page.transformation_matrix()));
    std::string rect = g({r.x0, r.y0, r.x1, r.y1});
    std::string annot = "<</A<</S/URI/URI(" + uri + ")>>/Rect[" + rect + "]/BS<</W 0>>/Subtype/Link>>";
    std::string name = "fitz-L" + std::to_string(page.links_);
    annot = replace_all(annot, "/Link", "/Link/NM(" + name + ")");
    // Page._addAnnot_FromString: an item that does not parse is skipped.
    pdf_obj *obj = nullptr;
    pdf_obj *added = nullptr;
    pdf_obj *ind = nullptr;
    fz_buffer *buf = nullptr;
    fz_stream *stm = nullptr;
    pdf_lexbuf lexbuf;
    fz_var(obj);
    fz_var(added);
    fz_var(ind);
    fz_var(buf);
    fz_var(stm);
    pdf_lexbuf_init(ctx_, &lexbuf, PDF_LEXBUF_SMALL);
    bool failed = false;
    fz_try(ctx_) {
        pdf_obj *pageobj = page.pdf()->obj;
        if (!pdf_dict_get(ctx_, pageobj, PDF_NAME(Annots)))
            pdf_dict_put_array(ctx_, pageobj, PDF_NAME(Annots), 1);
        pdf_obj *annots = pdf_dict_get(ctx_, pageobj, PDF_NAME(Annots));
        buf = fz_new_buffer_from_copied_data(ctx_, reinterpret_cast<const unsigned char *>(annot.data()), annot.size());
        stm = fz_open_buffer(ctx_, buf);
        obj = pdf_parse_stm_obj(ctx_, pdf_, stm, &lexbuf);
        added = pdf_add_object(ctx_, pdf_, obj);
        ind = pdf_new_indirect(ctx_, pdf_, pdf_to_num(ctx_, added), 0);
        pdf_array_push(ctx_, annots, ind);
    }
    fz_always(ctx_) {
        pdf_drop_obj(ctx_, ind);
        pdf_drop_obj(ctx_, added);
        pdf_drop_obj(ctx_, obj);
        fz_drop_stream(ctx_, stm);
        fz_drop_buffer(ctx_, buf);
        pdf_lexbuf_fin(ctx_, &lexbuf);
    }
    fz_catch(ctx_) { failed = true; }
    if (!failed)
        ++page.links_;
}

// --- HTML boxes ----------------------------------------------------------------------

namespace {

// JM_read_contents: the page's content streams, one after the other,
// separated by a space.
fz_buffer *read_contents(fz_context *ctx, pdf_obj *pageref) {
    fz_buffer *res = nullptr;
    fz_buffer *nres = nullptr;
    int i = 0;
    fz_var(res);
    fz_var(nres);
    fz_var(i);
    fz_try(ctx) {
        pdf_obj *contents = pdf_dict_get(ctx, pageref, PDF_NAME(Contents));
        if (pdf_is_array(ctx, contents)) {
            res = fz_new_buffer(ctx, 1024);
            int n = pdf_array_len(ctx, contents);
            for (i = 0; i < n; ++i) {
                if (i > 0)
                    fz_append_byte(ctx, res, 32);
                pdf_obj *obj = pdf_array_get(ctx, contents, i);
                if (pdf_is_stream(ctx, obj)) {
                    nres = pdf_load_stream(ctx, obj);
                    fz_append_buffer(ctx, res, nres);
                    fz_drop_buffer(ctx, nres);
                    nres = nullptr;
                }
            }
        } else if (contents) {
            res = pdf_load_stream(ctx, contents);
        } else {
            res = fz_new_buffer(ctx, 0);
        }
    }
    fz_catch(ctx) {
        fz_drop_buffer(ctx, nres);
        fz_drop_buffer(ctx, res);
        fz_rethrow(ctx);
    }
    return res;
}

struct FitResult {
    bool big_enough = false;
    Rect filled;
    double parameter = 0;
    Rect rect;
    bool has_parameter = false;
};

// Story.fit_scale(rect, scale_min, scale_max, delta=0.001, flags): the search
// of Story.fit, step for step, so that the scale found is PyMuPDF's.
FitResult fit_scale(fz_context *ctx, fz_story *story, const Rect &rect, double scale_min,
                    std::optional<double> scale_max, int flags) {
    const double delta = 0.001;
    double x0 = rect.x0, y0 = rect.y0;
    double width = rect.x1 - rect.x0, height = rect.y1 - rect.y0;
    auto fn = [&](double scale) { return Rect(x0, y0, x0 + scale * width, y0 + scale * height); };

    std::optional<double> pmin = scale_min, pmax = scale_max;
    std::optional<FitResult> pmin_result, pmax_result;
    double last_p = 0;
    OUT_TRY(ctx, fz_reset_story(ctx, story));

    auto update = [&](double parameter) {
        Rect r = fn(parameter);
        FitResult result;
        result.parameter = parameter;
        result.has_parameter = true;
        bool big_enough;
        if (is_empty(r)) {
            big_enough = false;
        } else {
            fz_rect filled = fz_empty_rect;
            int more = 0;
            OUT_TRY(ctx, more = fz_place_story_flags(ctx, story, fzr(r), &filled, flags));
            big_enough = more == 0;
            result.filled = pyr(filled);
            result.rect = r;
            result.big_enough = big_enough;
        }
        if (big_enough) {
            pmax = parameter;
            pmax_result = result;
        } else {
            pmin = parameter;
            pmin_result = result;
        }
        last_p = parameter;
        return big_enough;
    };
    auto ret = [&]() {
        if (pmax) {
            if (last_p != *pmax)
                update(*pmax);
            return *pmax_result;
        }
        return pmin_result ? *pmin_result : FitResult{};
    };

    // pmin is given (scale_min).
    if (update(*pmin))
        return ret();
    if (!pmax) {
        double parameter = *pmin == 0 ? 1 : (*pmin > 0 ? 2 * *pmin : -*pmin);
        for (;;) {
            if (update(parameter))
                break;
            parameter *= 2;
        }
    } else {
        if (!update(*pmax)) {
            pmax.reset();
            return ret();
        }
    }
    for (;;) {
        if (*pmax - *pmin < delta)
            return ret();
        update((*pmin + *pmax) / 2);
    }
}

} // namespace

void Document::insert_htmlbox(Page &page, const Rect &rect_in, const std::string &html, const std::string &css,
                              double scale_low) {
    Rect rect = rect_in;
    if (!(0 <= scale_low && scale_low <= 1))
        throw std::invalid_argument("'scale_low' must be in [0, 1]");
    Rect temp_rect(0, 0, width_of(rect), height_of(rect));
    std::string mycss = "body {margin:1px;}" + css;
    fz_buffer *htmlbuf = nullptr;
    fz_story *story = nullptr;
    OUT_TRY(ctx_, {
        htmlbuf = fz_new_buffer_from_copied_data(ctx_, reinterpret_cast<const unsigned char *>(html.data()),
                                                 html.size());
        story = fz_new_story(ctx_, htmlbuf, mycss.c_str(), 12, nullptr);
    });
    fz_buffer *pdfbuf = nullptr;
    fz_output *out = nullptr;
    fz_document_writer *writer = nullptr;
    fz_document *srcdoc = nullptr;
    fz_page *srcpage = nullptr;
    pdf_graft_map *gmap = nullptr;
    auto cleanup = [&]() {
        fz_drop_page(ctx_, srcpage);
        fz_drop_document(ctx_, srcdoc);
        pdf_drop_graft_map(ctx_, gmap);
        fz_drop_document_writer(ctx_, writer);
        fz_drop_output(ctx_, out);
        fz_drop_buffer(ctx_, pdfbuf);
        fz_drop_story(ctx_, story);
        fz_drop_buffer(ctx_, htmlbuf);
    };
    try {
        std::optional<double> rect_scale_max;
        if (scale_low != 0)
            rect_scale_max = 1 / scale_low;
        FitResult fit = fit_scale(ctx_, story, temp_rect, 1, rect_scale_max, FZ_PLACE_STORY_FLAG_NO_OVERFLOW);
        if (!fit.big_enough) {
            cleanup();
            return;   // no fit: nothing is drawn
        }
        // story.write_with_links(): one page the size of fit.rect, the story
        // placed again (without flags) and drawn.
        fz_rect mediabox = fzr(fit.rect);
        OUT_TRY(ctx_, {
            pdfbuf = fz_new_buffer(ctx_, 1024);
            out = fz_new_output_with_buffer(ctx_, pdfbuf);
            writer = fz_new_pdf_writer_with_output(ctx_, out, "");
            out = nullptr;   // owned by the writer
            fz_rect filled = fz_empty_rect;
            int more = fz_place_story_flags(ctx_, story, mediabox, &filled, 0);
            fz_device *dev = fz_begin_page(ctx_, writer, mediabox);
            fz_draw_story(ctx_, story, dev, fz_identity);
            (void)more;
            fz_end_page(ctx_, writer);
            fz_close_document_writer(ctx_, writer);
        });
        // Document("pdf", stream)
        OUT_TRY(ctx_, {
            fz_stream *stm = fz_open_buffer(ctx_, pdfbuf);
            fz_try(ctx_) { srcdoc = fz_open_document_with_stream(ctx_, "pdf", stm); }
            fz_always(ctx_) { fz_drop_stream(ctx_, stm); }
            fz_catch(ctx_) { fz_rethrow(ctx_); }
            srcpage = fz_load_page(ctx_, srcdoc, 0);
        });
        pdf_page *src = pdf_page_from_fz_page(ctx_, srcpage);
        pdf_document *srcpdf = pdf_specifics(ctx_, srcdoc);
        if (!src || !srcpdf)
            throw mu::Error("is no PDF");

        // page.show_pdf_page(rect, doc, 0)
        if (is_empty(rect) || is_infinite(rect))
            throw std::invalid_argument("rect must be finite and not empty");
        Rect tar_rect = transform_rect(rect, invert(page.transformation_matrix()));
        fz_rect srcbound;
        fz_matrix srcctm = fz_identity;
        fz_rect unit = fz_unit_rect;
        OUT_TRY(ctx_, {
            srcbound = fz_bound_page(ctx_, srcpage);
            pdf_page_transform(ctx_, src, &unit, &srcctm);
        });
        Rect src_rect = pyr(srcbound);
        if (is_empty(src_rect) || is_infinite(src_rect))
            throw std::invalid_argument("clip must be finite and not empty");
        src_rect = transform_rect(src_rect, invert(pym(srcctm)));
        Matrix matrix = calc_matrix(src_rect, tar_rect);
        std::string imgname = "fzFrm" + std::to_string(page.forms_);

        // _show_pdf_page and JM_xobject_from_page
        pdf_obj *xobj1 = nullptr, *subres1 = nullptr, *subres = nullptr, *xobj2 = nullptr, *resources = nullptr;
        pdf_obj *placeholder = nullptr;
        fz_buffer *contents = nullptr, *res = nullptr;
        fz_var(placeholder);
        fz_var(xobj1);
        fz_var(subres1);
        fz_var(subres);
        fz_var(xobj2);
        fz_var(resources);
        fz_var(contents);
        fz_var(res);
        bool failed = false;
        char message[256] = "";
        fz_var(failed);
        fz_var(gmap);
        fz_try(ctx_) {
            gmap = pdf_new_graft_map(ctx_, pdf_);
            pdf_obj *spageref = src->obj;
            fz_rect mb = pdf_to_rect(ctx_, pdf_dict_get_inheritable(ctx_, spageref, PDF_NAME(MediaBox)));
            pdf_obj *o = pdf_dict_get_inheritable(ctx_, spageref, PDF_NAME(Resources));
            resources = pdf_graft_mapped_object(ctx_, gmap, o);
            contents = read_contents(ctx_, spageref);
            // PyMuPDF passes PdfObj(0), the integer 0 and not NULL: /Resources
            // is made here with it, and replaced below, which fixes the key's
            // place in the dictionary.
            placeholder = pdf_new_int(ctx_, 0);
            xobj1 = pdf_new_xobject(ctx_, pdf_, mb, fz_identity, placeholder, contents);
        }
        fz_catch(ctx_) {
            failed = true;
            std::strncpy(message, fz_caught_message(ctx_), sizeof message - 1);
        }
        if (!failed) {
            try {
                pdfout::update_stream(ctx_, pdf_, xobj1, contents);
            } catch (const std::exception &e) {
                failed = true;
                std::strncpy(message, e.what(), sizeof message - 1);
            }
        }
        if (!failed) {
            fz_try(ctx_) {
                pdf_dict_put(ctx_, xobj1, PDF_NAME(Resources), resources);
                subres1 = pdf_new_dict(ctx_, pdf_, 5);
                pdf_dict_puts(ctx_, subres1, "fullpage", xobj1);
                subres = pdf_new_dict(ctx_, pdf_, 5);
                pdf_dict_put(ctx_, subres, PDF_NAME(XObject), subres1);
                res = fz_new_buffer(ctx_, 20);
                fz_append_string(ctx_, res, "/fullpage Do");
                xobj2 = pdf_new_xobject(ctx_, pdf_, fzr(src_rect), fzm(matrix), subres, res);
                pdf_obj *tpageref = page.pdf()->obj;
                pdf_obj *tres = pdf_dict_get_inheritable(ctx_, tpageref, PDF_NAME(Resources));
                if (!tres)
                    tres = pdf_dict_put_dict(ctx_, tpageref, PDF_NAME(Resources), 5);
                pdf_obj *txo = pdf_dict_get(ctx_, tres, PDF_NAME(XObject));
                if (!txo)
                    txo = pdf_dict_put_dict(ctx_, tres, PDF_NAME(XObject), 5);
                pdf_dict_puts(ctx_, txo, imgname.c_str(), xobj2);
            }
            fz_catch(ctx_) {
                failed = true;
                std::strncpy(message, fz_caught_message(ctx_), sizeof message - 1);
            }
        }
        pdf_drop_obj(ctx_, xobj2);
        pdf_drop_obj(ctx_, placeholder);
        pdf_drop_obj(ctx_, subres);
        pdf_drop_obj(ctx_, subres1);
        pdf_drop_obj(ctx_, xobj1);
        pdf_drop_obj(ctx_, resources);
        fz_drop_buffer(ctx_, res);
        fz_drop_buffer(ctx_, contents);
        if (failed)
            throw mu::Error(message);
        insert_contents(page, " q /" + imgname + " Do Q ", true);
        ++page.forms_;
    } catch (...) {
        cleanup();
        throw;
    }
    cleanup();
}

// --- fonts and saving ----------------------------------------------------------------

void Document::subset_fonts() {
    std::vector<int> pages(static_cast<size_t>(page_count()));
    for (size_t i = 0; i < pages.size(); ++i)
        pages[i] = static_cast<int>(i);
    OUT_TRY(ctx_, pdf_subset_fonts(ctx_, pdf_, static_cast<int>(pages.size()), pages.data()));
}

std::string Document::save(bool deflate_images, int garbage) {
    if (page_count() < 1)
        throw std::invalid_argument("cannot save with zero pages");
    pdf_write_options opts = pdf_default_write_options;
    opts.do_incremental = 0;
    opts.do_ascii = 0;
    opts.do_compress = 1;
    opts.do_compress_images = deflate_images ? 1 : 0;
    opts.do_compress_fonts = 0;
    opts.do_decompress = 0;
    opts.do_garbage = garbage;
    opts.do_pretty = 0;
    opts.do_linear = 0;
    opts.do_clean = 0;
    opts.do_sanitize = 0;
    opts.dont_regenerate_id = 0;
    opts.do_appearance = 0;
    opts.do_encrypt = PDF_ENCRYPT_NONE;
    opts.permissions = 4095;
    opts.reproducible = 0;
    opts.do_preserve_metadata = 1;
    opts.do_use_objstms = 0;
    opts.compression_effort = 0;
    pdf_->resynth_required = 0;

    fz_buffer *buf = nullptr;
    fz_output *out = nullptr;
    fz_var(buf);
    fz_var(out);
    bool failed = false;
    char message[256] = "";
    fz_try(ctx_) {
        // JM_embedded_clean: an empty /Collection goes; attachments open the
        // attachments pane.
        pdf_obj *root = pdf_dict_get(ctx_, pdf_trailer(ctx_, pdf_), PDF_NAME(Root));
        pdf_obj *coll = pdf_dict_get(ctx_, root, PDF_NAME(Collection));
        if (coll && pdf_dict_len(ctx_, coll) == 0)
            pdf_dict_del(ctx_, root, PDF_NAME(Collection));
        if (pdf_dict_getl(ctx_, root, PDF_NAME(Names), PDF_NAME(EmbeddedFiles), PDF_NAME(Names), nullptr))
            pdf_dict_put_name(ctx_, root, PDF_NAME(PageMode), "UseAttachments");
        // JM_ensure_identity: a random /ID when there is none. PyMuPDF makes
        // a str of the 16 random bytes, which reaches MuPDF in UTF-8 and is
        // cut to 16 bytes: the same here, so the ID reads the same way.
        if (!pdf_dict_get(ctx_, pdf_trailer(ctx_, pdf_), PDF_NAME(ID))) {
            unsigned char rnd[16];
            fz_memrnd(ctx_, rnd, 16);
            char id_bytes[32];
            size_t n = 0;
            for (unsigned char c : rnd) {
                if (c < 0x80) {
                    id_bytes[n++] = static_cast<char>(c);
                } else {
                    id_bytes[n++] = static_cast<char>(0xC0 | (c >> 6));
                    id_bytes[n++] = static_cast<char>(0x80 | (c & 0x3F));
                }
            }
            pdf_obj *id = pdf_dict_put_array(ctx_, pdf_trailer(ctx_, pdf_), PDF_NAME(ID), 2);
            pdf_array_push_string(ctx_, id, id_bytes, 16);
            pdf_array_push_string(ctx_, id, id_bytes, 16);
        }
        buf = fz_new_buffer(ctx_, 65536);
        out = fz_new_output_with_buffer(ctx_, buf);
        pdf_write_document(ctx_, pdf_, out, &opts);
        fz_close_output(ctx_, out);
    }
    fz_always(ctx_) { fz_drop_output(ctx_, out); }
    fz_catch(ctx_) {
        failed = true;
        std::strncpy(message, fz_caught_message(ctx_), sizeof message - 1);
    }
    if (failed) {
        fz_drop_buffer(ctx_, buf);
        throw mu::Error(message);
    }
    std::string data = buffer_string(ctx_, buf);
    fz_drop_buffer(ctx_, buf);
    return data;
}

// --- shapes --------------------------------------------------------------------------

Shape::Shape(Page &page) : page_(page), ipctm_(invert(page.transformation_matrix())) {}

void Shape::draw_rect(const Rect &r) {
    auto bl = transform_point(r.x0, r.y1, ipctm_);
    draw_cont_ += g({jm(bl[0]), jm(bl[1]), jm(width_of(r)), jm(height_of(r))}) + " re\n";
}

void Shape::draw_polyline(const std::vector<std::array<double, 2>> &points) {
    // After finish() there is no last point, so the line always opens with
    // a move.
    for (size_t i = 0; i < points.size(); ++i) {
        auto p = transform_point(points[i][0], points[i][1], ipctm_);
        draw_cont_ += g({jm(p[0]), jm(p[1])}) + (i == 0 ? " m\n" : " l\n");
    }
}

void Shape::finish(double width, std::optional<std::array<double, 3>> color,
                   std::optional<std::array<double, 3>> fill, bool close_path) {
    if (draw_cont_.empty())
        return;
    if (width == 0)
        color.reset();
    else if (!color)
        width = 0;
    std::string color_str = color ? color_code(*color, true) : "";
    std::string fill_str = fill ? color_code(*fill, false) : "";
    if (width != 1 && width != 0)
        draw_cont_ += g(width) + " w\n";
    if (close_path)
        draw_cont_ += "h\n";
    if (color)
        draw_cont_ += color_str;
    if (fill) {
        draw_cont_ += fill_str;
        draw_cont_ += color ? "B\n" : "f\n";
    } else {
        draw_cont_ += "S\n";
    }
    totalcont_ += "\nq\n" + draw_cont_ + "Q\n";
    draw_cont_.clear();
}

void Shape::commit(Document &doc) {
    if (!totalcont_.empty()) {
        // wrap_contents(): every stream written here is balanced, so there
        // is nothing to add.
        int xref = doc.insert_contents(page_, " ", true);
        doc.update_stream(xref, totalcont_);
    }
    draw_cont_.clear();
    totalcont_.clear();
}

// --- text ----------------------------------------------------------------------------

TextWriter::TextWriter(fz_context *ctx, const Rect &page_rect) : ctx_(ctx), rect_(page_rect) {
    fz_text *text = nullptr;
    OUT_TRY(ctx, text = fz_new_text(ctx));
    text_ = text;
    ictm_ = invert(Matrix{1, 0, 0, -1, 0, height_of(rect_)});
}

TextWriter::~TextWriter() { fz_drop_text(ctx_, static_cast<fz_text *>(text_)); }

void TextWriter::append(double x, double y, const std::string &utf8, fz_font *font, double fontsize) {
    auto pos = transform_point(x, y, ictm_);
    fz_matrix trm = fz_make_matrix(static_cast<float>(fontsize), 0, 0, static_cast<float>(fontsize),
                                   static_cast<float>(pos[0]), static_cast<float>(pos[1]));
    OUT_TRY(ctx_, fz_show_string(ctx_, static_cast<fz_text *>(text_), font, trm, utf8.c_str(), 0, 0, FZ_BIDI_LTR, FZ_LANG_UNSET));
    if (fz_font_flags(font)->is_mono &&
        std::find(used_fonts_.begin(), used_fonts_.end(), font) == used_fonts_.end())
        used_fonts_.push_back(font);
}

void TextWriter::write_text(Document &doc, Page &page, const std::array<double, 3> &rgb) {
    fz_context *ctx = ctx_;
    pdf_document *pdf = doc.pdf();
    pdf_obj *resources = nullptr;
    fz_buffer *contents = nullptr;
    fz_device *dev = nullptr;
    fz_var(resources);
    fz_var(contents);
    fz_var(dev);
    float color[3] = {static_cast<float>(rgb[0]), static_cast<float>(rgb[1]), static_cast<float>(rgb[2])};
    bool failed = false;
    char message[256] = "";
    fz_try(ctx) {
        resources = pdf_new_dict(ctx, pdf, 5);
        contents = fz_new_buffer(ctx, 1024);
        dev = pdf_new_pdf_device(ctx, pdf, fz_identity, resources, contents);
        fz_fill_text(ctx, dev, static_cast<fz_text *>(text_), fz_identity, fz_device_rgb(ctx), color, 1, fz_default_color_params);
        fz_close_device(ctx, dev);
    }
    fz_always(ctx) { fz_drop_device(ctx, dev); }
    fz_catch(ctx) {
        failed = true;
        std::strncpy(message, fz_caught_message(ctx), sizeof message - 1);
    }
    if (failed) {
        fz_drop_buffer(ctx, contents);
        pdf_drop_obj(ctx, resources);
        throw mu::Error(message);
    }
    std::pair<int, int> max_nums;
    std::string content;
    try {
        max_nums = merge_resources(ctx, page.pdf(), resources);
        content = buffer_string(ctx, contents);
    } catch (...) {
        fz_drop_buffer(ctx, contents);
        pdf_drop_obj(ctx, resources);
        throw;
    }
    fz_drop_buffer(ctx, contents);
    pdf_drop_obj(ctx, resources);

    auto [max_alp, max_font] = max_nums;
    std::vector<std::string> lines{"q"};
    for (std::string line : split_lines(content)) {
        if (ends_with(line, " cm"))
            continue;
        if (line == "BT") {
            lines.push_back(line);
            lines.push_back("0 Tr");
            continue;
        }
        if (ends_with(line, " gs")) {
            size_t space = line.find_first_of(" \t");
            std::string first = line.substr(0, space);
            int alp = std::stoi(first.size() > 4 ? first.substr(4) : "0") + max_alp;
            line = "/Alp" + std::to_string(alp) + " gs";
        } else if (ends_with(line, " Tf")) {
            // line.split(): runs of whitespace
            std::vector<std::string> temp;
            size_t i = 0;
            while (i < line.size()) {
                while (i < line.size() && std::isspace(static_cast<unsigned char>(line[i])))
                    ++i;
                size_t j = i;
                while (j < line.size() && !std::isspace(static_cast<unsigned char>(line[j])))
                    ++j;
                if (j > i)
                    temp.push_back(line.substr(i, j - i));
                i = j;
            }
            lines.push_back("1 w");
            int font = std::stoi(temp[0].size() > 2 ? temp[0].substr(2) : "0") + max_font;
            std::string joined = "/F" + std::to_string(font);
            for (size_t k = 1; k < temp.size(); ++k)
                joined += " " + temp[k];
            line = joined;
        } else if (ends_with(line, " rg")) {
            lines.push_back(replace_all(line, "rg", "RG"));
        } else if (ends_with(line, " g")) {
            lines.push_back(replace_all(line, " g", " G"));
        } else if (ends_with(line, " k")) {
            lines.push_back(replace_all(line, " k", " K"));
        }
        lines.push_back(line);
    }
    lines.push_back("Q\n");
    std::string out;
    for (size_t i = 0; i < lines.size(); ++i) {
        if (i)
            out += '\n';
        out += lines[i];
    }
    doc.insert_contents(page, out, true);

    // repair_mono_font: a monospaced font's widths all set to its space's.
    for (fz_font *font : used_fonts_) {
        std::string name = fz_font_name(ctx, font);
        std::set<int> xrefs;
        for (const auto &f : mu::Page(doc.view(), page.number()).fonts())
            if (f.basefont == name && f.refname.compare(0, 1, "F") == 0 && f.encoding.compare(0, 8, "Identity") == 0)
                xrefs.insert(f.xref);
        if (xrefs.empty())
            continue;
        // Font.glyph_advance(32): through the fallback lookup.
        float advance = 0;
        OUT_TRY(ctx, {
            fz_font *used = font;
            int gid = fz_encode_character_with_fallback(ctx, font, 32, 0, FZ_LANG_UNSET, &used);
            advance = fz_advance_glyph(ctx, used, gid, 0);
        });
        long long width = py::round_int(static_cast<double>(advance) * 1000);
        for (int xref : xrefs) {
            // TOOLS.set_font_width
            OUT_TRY(ctx, {
                pdf_obj *fontobj = pdf_load_object(ctx, pdf, xref);
                int k = 0;
                fz_var(k);
                fz_try(ctx) {
                    pdf_obj *dfonts = pdf_dict_get(ctx, fontobj, PDF_NAME(DescendantFonts));
                    if (pdf_is_array(ctx, dfonts)) {
                        for (k = 0; k < pdf_array_len(ctx, dfonts); ++k) {
                            pdf_obj *dfont = pdf_array_get(ctx, dfonts, k);
                            pdf_obj *warray = pdf_new_array(ctx, pdf, 3);
                            fz_try(ctx) {
                                pdf_array_push_int(ctx, warray, 0);
                                pdf_array_push_int(ctx, warray, 65535);
                                pdf_array_push_int(ctx, warray, width);
                                pdf_dict_put(ctx, dfont, PDF_NAME(W), warray);
                            }
                            fz_always(ctx) { pdf_drop_obj(ctx, warray); }
                            fz_catch(ctx) { fz_rethrow(ctx); }
                        }
                    }
                }
                fz_always(ctx) { pdf_drop_obj(ctx, fontobj); }
                fz_catch(ctx) { fz_rethrow(ctx); }
            });
        }
    }
}

} // namespace pdft::engine::pdfout
