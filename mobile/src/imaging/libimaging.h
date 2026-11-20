// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// libImaging's header, for the C++ side of this layer.
#pragma once

#if defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wignored-qualifiers"
#endif
#include "pillow/Imaging.h"
#if defined(__GNUC__)
#pragma GCC diagnostic pop
#endif

extern "C" const char *pdft_imaging_last_error(void);
