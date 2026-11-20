/*
 * Copyright (C) 2026 The PDF Teleporter contributors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * JPEG through libjpeg-turbo, with Pillow's settings. See jpeg_codec.h.
 */
#include "jpeg_codec.h"

#include <setjmp.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <jpeglib.h>
#include <jerror.h>

struct pdft_jpeg_error {
    struct jpeg_error_mgr pub;
    jmp_buf jump;
    char *msg;
    size_t msglen;
};

static void set_error(char *err, size_t errlen, const char *fmt, ...) {
    if (!err || !errlen)
        return;
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(err, errlen, fmt, ap);
    va_end(ap);
}

static void error_exit(j_common_ptr cinfo) {
    struct pdft_jpeg_error *e = (struct pdft_jpeg_error *)cinfo->err;
    if (e->msg && e->msglen) {
        char buffer[JMSG_LENGTH_MAX];
        cinfo->err->format_message(cinfo, buffer);
        snprintf(e->msg, e->msglen, "%s", buffer);
    }
    longjmp(e->jump, 1);
}

/* Warnings (corrupt data, premature end) are not errors: decode on. */
static void output_message(j_common_ptr cinfo) { (void)cinfo; }

/* --- decoding ------------------------------------------------------------------- */

/* The whole file in memory, as Pillow hands it over. Where the data runs
 * out, Pillow's decoder suspends and the reader reports a truncated file
 * (OSError); only a missing end marker after the last line is forgiven,
 * because finishing does not need it. jpeg_mem_src would instead complete
 * any truncated stream with a fake end marker and grey rows. */
static const JOCTET fake_eoi[2] = {0xFF, JPEG_EOI};

static void source_init(j_decompress_ptr cinfo) { (void)cinfo; }

static boolean source_fill(j_decompress_ptr cinfo) {
    if (!(cinfo->output_height > 0 && cinfo->output_scanline >= cinfo->output_height))
        ERREXIT(cinfo, JERR_INPUT_EOF);
    cinfo->src->next_input_byte = fake_eoi;
    cinfo->src->bytes_in_buffer = 2;
    return TRUE;
}

static void source_skip(j_decompress_ptr cinfo, long count) {
    struct jpeg_source_mgr *src = cinfo->src;
    if (count <= 0)
        return;
    while ((size_t)count > src->bytes_in_buffer) {
        count -= (long)src->bytes_in_buffer;
        (void)source_fill(cinfo);
    }
    src->next_input_byte += count;
    src->bytes_in_buffer -= (size_t)count;
}

static void source_term(j_decompress_ptr cinfo) { (void)cinfo; }

static void set_source(j_decompress_ptr cinfo, struct jpeg_source_mgr *src, const unsigned char *data,
                       size_t len) {
    src->init_source = source_init;
    src->fill_input_buffer = source_fill;
    src->skip_input_data = source_skip;
    src->resync_to_restart = jpeg_resync_to_restart;
    src->term_source = source_term;
    src->next_input_byte = data;
    src->bytes_in_buffer = len;
    cinfo->src = src;
}

int pdft_jpeg_header(const unsigned char *data, size_t len, int *width, int *height, int *components,
                     int *precision, char *err, size_t errlen) {
    struct jpeg_decompress_struct cinfo;
    struct pdft_jpeg_error jerr;
    struct jpeg_source_mgr src;
    volatile int created = 0;

    memset(&cinfo, 0, sizeof cinfo);
    cinfo.err = jpeg_std_error(&jerr.pub);
    jerr.pub.error_exit = error_exit;
    jerr.pub.output_message = output_message;
    jerr.msg = err;
    jerr.msglen = errlen;
    if (setjmp(jerr.jump)) {
        if (created)
            jpeg_destroy_decompress(&cinfo);
        return -1;
    }
    jpeg_create_decompress(&cinfo);
    created = 1;
    set_source(&cinfo, &src, data, len);
    jpeg_read_header(&cinfo, TRUE);
    *width = (int)cinfo.image_width;
    *height = (int)cinfo.image_height;
    *components = cinfo.num_components;
    *precision = cinfo.data_precision;
    jpeg_destroy_decompress(&cinfo);
    return 0;
}

int pdft_jpeg_decode(const unsigned char *data, size_t len, int scale, pdft_jpeg_image *out, char *err,
                     size_t errlen) {
    struct jpeg_decompress_struct cinfo;
    struct pdft_jpeg_error jerr;
    struct jpeg_source_mgr src;
    volatile int created = 0;
    unsigned char *volatile pixels = NULL;

    memset(out, 0, sizeof *out);
    memset(&cinfo, 0, sizeof cinfo);
    cinfo.err = jpeg_std_error(&jerr.pub);
    jerr.pub.error_exit = error_exit;
    jerr.pub.output_message = output_message;
    jerr.msg = err;
    jerr.msglen = errlen;
    if (setjmp(jerr.jump)) {
        if (created)
            jpeg_destroy_decompress(&cinfo);
        free(pixels);
        return -1;
    }
    jpeg_create_decompress(&cinfo);
    created = 1;
    set_source(&cinfo, &src, data, len);
    jpeg_read_header(&cinfo, TRUE);

    /* Pillow's modes: L, RGB, and CMYK taken as stored (rawmode "CMYK;I"
     * inverts it afterwards, which the caller does). */
    switch (cinfo.num_components) {
    case 1:
        cinfo.out_color_space = JCS_GRAYSCALE;
        break;
    case 3:
        cinfo.out_color_space = JCS_RGB;
        break;
    case 4:
        cinfo.out_color_space = JCS_CMYK;
        break;
    default:
        set_error(err, errlen, "cannot handle %d-layer images", cinfo.num_components);
        jpeg_destroy_decompress(&cinfo);
        return -1;
    }
    if (scale > 1) {
        cinfo.scale_num = 1;
        cinfo.scale_denom = (unsigned int)scale;
    }
    jpeg_start_decompress(&cinfo);

    size_t w = cinfo.output_width, h = cinfo.output_height, n = (size_t)cinfo.output_components;
    if (!w || !h || w > SIZE_MAX / n / h) {
        set_error(err, errlen, "bad JPEG dimensions");
        jpeg_destroy_decompress(&cinfo);
        return -1;
    }
    size_t stride = w * n;
    pixels = (unsigned char *)malloc(stride * h);
    if (!pixels) {
        set_error(err, errlen, "out of memory");
        jpeg_destroy_decompress(&cinfo);
        return -1;
    }
    while (cinfo.output_scanline < cinfo.output_height) {
        JSAMPROW row = pixels + (size_t)cinfo.output_scanline * stride;
        if (jpeg_read_scanlines(&cinfo, &row, 1) != 1)
            break;
    }
    jpeg_finish_decompress(&cinfo);
    jpeg_destroy_decompress(&cinfo);

    out->width = (int)w;
    out->height = (int)h;
    out->components = (int)n;
    out->pixels = pixels;
    return 0;
}

/* --- encoding ------------------------------------------------------------------- */

struct growing_dest {
    struct jpeg_destination_mgr pub;
    unsigned char *buffer;
    size_t capacity;
};

static void init_destination(j_compress_ptr cinfo) {
    struct growing_dest *dest = (struct growing_dest *)cinfo->dest;
    dest->pub.next_output_byte = dest->buffer;
    dest->pub.free_in_buffer = dest->capacity;
}

static boolean empty_output_buffer(j_compress_ptr cinfo) {
    struct growing_dest *dest = (struct growing_dest *)cinfo->dest;
    size_t used = dest->capacity;
    size_t capacity = dest->capacity * 2;
    unsigned char *bigger = (unsigned char *)realloc(dest->buffer, capacity);
    if (!bigger)
        ERREXIT1(cinfo, JERR_OUT_OF_MEMORY, 10);
    dest->buffer = bigger;
    dest->capacity = capacity;
    dest->pub.next_output_byte = bigger + used;
    dest->pub.free_in_buffer = capacity - used;
    return TRUE;
}

static void term_destination(j_compress_ptr cinfo) { (void)cinfo; }

int pdft_jpeg_encode(const unsigned char *pixels, int width, int height, int components, int quality,
                     int progressive, int optimize, const unsigned char *comment, size_t comment_len,
                     unsigned char **out, size_t *out_len, char *err, size_t errlen) {
    struct jpeg_compress_struct cinfo;
    struct pdft_jpeg_error jerr;
    struct growing_dest dest;
    volatile int created = 0;

    *out = NULL;
    *out_len = 0;
    if (components != 1 && components != 3) {
        set_error(err, errlen, "cannot write %d components as JPEG", components);
        return -1;
    }
    if (width <= 0 || height <= 0 || width > 65500 || height > 65500) {
        set_error(err, errlen, "image size is out of range for JPEG");
        return -1;
    }
    memset(&dest, 0, sizeof dest);
    dest.capacity = 65536;
    dest.buffer = (unsigned char *)malloc(dest.capacity);
    if (!dest.buffer) {
        set_error(err, errlen, "out of memory");
        return -1;
    }
    dest.pub.init_destination = init_destination;
    dest.pub.empty_output_buffer = empty_output_buffer;
    dest.pub.term_destination = term_destination;

    memset(&cinfo, 0, sizeof cinfo);
    cinfo.err = jpeg_std_error(&jerr.pub);
    jerr.pub.error_exit = error_exit;
    jerr.pub.output_message = output_message;
    jerr.msg = err;
    jerr.msglen = errlen;
    if (setjmp(jerr.jump)) {
        if (created)
            jpeg_destroy_compress(&cinfo);
        free(dest.buffer);
        return -1;
    }
    jpeg_create_compress(&cinfo);
    created = 1;
    cinfo.dest = &dest.pub;
    cinfo.image_width = (JDIMENSION)width;
    cinfo.image_height = (JDIMENSION)height;
    cinfo.input_components = components;
    cinfo.in_color_space = components == 1 ? JCS_GRAYSCALE : JCS_RGB;
    jpeg_set_defaults(&cinfo);
    if (quality != -1)
        jpeg_set_quality(&cinfo, quality, TRUE);
    if (progressive)
        jpeg_simple_progression(&cinfo);
    cinfo.smoothing_factor = 0;
    cinfo.optimize_coding = optimize ? TRUE : FALSE;
    jpeg_start_compress(&cinfo, TRUE);
    if (comment && comment_len)
        jpeg_write_marker(&cinfo, JPEG_COM, comment, (unsigned int)comment_len);
    size_t stride = (size_t)width * (size_t)components;
    while (cinfo.next_scanline < cinfo.image_height) {
        JSAMPROW row = (JSAMPROW)(pixels + (size_t)cinfo.next_scanline * stride);
        jpeg_write_scanlines(&cinfo, &row, 1);
    }
    jpeg_finish_compress(&cinfo);
    *out_len = dest.capacity - dest.pub.free_in_buffer;
    jpeg_destroy_compress(&cinfo);
    *out = dest.buffer;
    return 0;
}
