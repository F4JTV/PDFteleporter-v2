/*
 * Copyright (C) 2026 The PDF Teleporter contributors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * JPEG through libjpeg-turbo, the library Pillow uses, with Pillow's
 * settings: the same version decodes to the same pixels and encodes to the
 * same bytes as the desktop version.
 *
 * MuPDF carries its own libjpeg (IJG 10) for the pages it renders, and both
 * export the same symbols: this file and libjpeg-turbo are therefore linked
 * into a library of their own (pdft_jpeg), which exports these functions
 * and nothing else.
 *
 * Written in C because libjpeg reports errors by longjmp: the jump never
 * crosses a C++ frame.
 */
#ifndef PDFT_JPEG_CODEC_H
#define PDFT_JPEG_CODEC_H

#include <stddef.h>

#if defined(PDFT_JPEG_BUILD)
#define PDFT_JPEG_API __attribute__((visibility("default")))
#else
#define PDFT_JPEG_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int width, height;     /* as decoded, after any scaling */
    int components;        /* 1 grey, 3 RGB, 4 CMYK as stored in the file */
    unsigned char *pixels; /* malloc'd, rows packed, width * components bytes each */
} pdft_jpeg_image;

/* What Pillow's JPEG reader sees before decoding: the frame size, the
 * number of components and the sample precision. 0 on success. */
PDFT_JPEG_API int pdft_jpeg_header(const unsigned char *data, size_t len, int *width, int *height,
                     int *components, int *precision, char *err, size_t errlen);

/* Decode as Pillow does: grey stays grey, three components come out as RGB,
 * four as CMYK exactly as stored. `scale` is 1, 2, 4 or 8 (Pillow's draft).
 * Corrupt data is decoded through, as libjpeg's warnings allow; a stream
 * that ends before its last line is an error, as Pillow reports it. 0 on
 * success; free out->pixels. */
PDFT_JPEG_API int pdft_jpeg_decode(const unsigned char *data, size_t len, int scale, pdft_jpeg_image *out,
                     char *err, size_t errlen);

/* Encode grey (1) or RGB (3) pixels as Pillow's JPEG writer does with
 * quality, optimize and progressive: libjpeg's defaults, the quality scaled
 * baseline tables, 4:2:0 for colour, the simple progression script, and the
 * image's comment (COM marker) when it has one, as Pillow carries it over.
 * 0 on success; free *out. */
PDFT_JPEG_API int pdft_jpeg_encode(const unsigned char *pixels, int width, int height, int components,
                     int quality, int progressive, int optimize, const unsigned char *comment,
                     size_t comment_len, unsigned char **out, size_t *out_len, char *err,
                     size_t errlen);

#ifdef __cplusplus
}
#endif

#endif
