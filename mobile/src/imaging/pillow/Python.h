/*
 * Copyright (C) 2026 The PDF Teleporter contributors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * The handful of Python declarations the vendored libImaging sources name,
 * with no Python behind them: these sources are built here as a plain C
 * library, for their image algorithms alone.
 */
#ifndef PDFT_PYTHON_SHIM_H
#define PDFT_PYTHON_SHIM_H

#include <limits.h>
/* The real Python.h brings math.h, and Convert.c counts on it (fmod, floor,
 * round): without a declaration, clang 16 and later refuse the call. */
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define HAVE_PROTOTYPES 1
#define STDC_HEADERS 1

/* Images may be made and freed on several threads at once (a conversion and
 * a preview): take the locks libImaging has for free-threaded Python. A
 * zero-filled pthread mutex is an unlocked one, on glibc as on bionic, which
 * is how libImaging initialises them (calloc and {0}). */
#include <pthread.h>
#define Py_GIL_DISABLED 1
typedef struct {
    pthread_mutex_t m;
} PyMutex;
static inline void PyMutex_Lock(PyMutex *mutex) { pthread_mutex_lock(&mutex->m); }
static inline void PyMutex_Unlock(PyMutex *mutex) { pthread_mutex_unlock(&mutex->m); }

typedef ptrdiff_t Py_ssize_t;
#define PY_SSIZE_T_MAX PTRDIFF_MAX

typedef struct _object {
    int unused;
} PyObject;

#define Py_INCREF(o) ((void)(o))
#define Py_DECREF(o) ((void)(o))
#define Py_XDECREF(o) ((void)(o))

extern PyObject *PyExc_ValueError;
static inline void PyErr_Clear(void) {}
static inline void *PyErr_Format(PyObject *type, const char *format, ...) {
    (void)type;
    (void)format;
    return NULL;
}
static inline void *PyCapsule_GetPointer(PyObject *capsule, const char *name) {
    (void)capsule;
    (void)name;
    return NULL;
}

#endif
