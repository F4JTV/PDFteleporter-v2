/*
 * Copyright (C) 2026 The PDF Teleporter contributors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * What Pillow's _imaging.c provides to libImaging: error reporting and the
 * markers around long computations. Errors carry no Python exception here;
 * the caller sees the NULL result and the message of the last one, kept
 * per thread.
 */
#include "Imaging.h"

static PyObject value_error;
PyObject *PyExc_ValueError = &value_error;

static _Thread_local const char *last_error = "";

const char *pdft_imaging_last_error(void) { return last_error; }

void *ImagingError_MemoryError(void) { last_error = "out of memory"; return NULL; }
void *ImagingError_Mismatch(void) { last_error = "images do not match"; return NULL; }
void *ImagingError_ModeError(void) { last_error = "image has wrong mode"; return NULL; }
void *ImagingError_ValueError(const char *message) {
    last_error = message ? message : "unrecognized argument value";
    return NULL;
}
void ImagingError_Clear(void) { last_error = ""; }

void ImagingSectionEnter(ImagingSectionCookie *cookie) { (void)cookie; }
void ImagingSectionLeave(ImagingSectionCookie *cookie) { (void)cookie; }
