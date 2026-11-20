// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// The two fidelity measures the engine compares encodings with, computed as
// the desktop engine computes them: _psnr from Pillow's difference
// histogram, _ssim with ImageMath's 32-bit integer and float arithmetic, and
// Python's own float summation on top. Equal inputs give equal doubles, so
// both engines take the same decisions.
#pragma once

#include "image.h"

#include <cstddef>
#include <vector>

namespace pdft::img {

// Python 3.12+ sum() over floats, started from the integer 0: Neumaier's
// compensated summation as CPython implements it.
class PySum {
public:
    void add(double x);
    double result() const;

private:
    bool started_ = false;
    double sum_ = 0.0;
    double compensation_ = 0.0;
};

double py_sum(const std::vector<double> &values);

// engine._psnr(reference, candidate)
double psnr(const Image &reference, const Image &candidate);

// engine._ssim(reference, candidate)
double ssim(const Image &reference, const Image &candidate);

} // namespace pdft::img
