// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// Built with -ffp-contract=off (see CMakeLists.txt): every float operation
// below must round on its own, as each ImageMath operation does when it
// writes its result image.

#include "metrics.h"

#include "libimaging.h"

#include <algorithm>
#include <cmath>
#include <cstdint>

namespace pdft::img {

void PySum::add(double x) {
    if (!started_) {
        sum_ = 0.0 + x;  // the integer start value, then the float fast path
        started_ = true;
        return;
    }
    double t = sum_ + x;
    if (std::fabs(sum_) >= std::fabs(x))
        compensation_ += (sum_ - t) + x;
    else
        compensation_ += (x - t) + sum_;
    sum_ = t;
}

double PySum::result() const {
    if (!started_)
        return 0.0;
    double r = sum_;
    if (compensation_ != 0.0 && std::isfinite(compensation_))
        r += compensation_;
    return r;
}

double py_sum(const std::vector<double> &values) {
    PySum s;
    for (double v : values)
        s.add(v);
    return s.result();
}

double psnr(const Image &reference, const Image &candidate) {
    Image diff = difference(reference, candidate.convert(reference.mode()));
    std::vector<long> histogram = diff.histogram();
    long long bands = mode_bands(diff.mode());
    long long squares = 0;
    for (long long band = 0; band < bands; ++band)
        for (long long value = 0; value < 256; ++value)
            squares += static_cast<long long>(histogram[static_cast<size_t>(band * 256 + value)]) * value * value;
    if (squares == 0)
        return 99.0;
    long long samples = static_cast<long long>(reference.width()) * reference.height() * bands;
    // Both integers stay below 2**53: their quotient is Python's true division.
    return 10 * std::log10(static_cast<double>(255LL * 255LL * samples) / static_cast<double>(squares));
}

namespace {

constexpr int kWindow = 7;

// Sums over a 7x7 window of one band of each image, slid over every
// position that keeps the window inside: what _window_sums builds as whole
// images, here one output row at a time from running column sums. Integer
// sums are exact whichever way they are added up, so the values are
// Pillow's.
struct WindowSums {
    int64_t x = 0, y = 0, xx = 0, yy = 0, xy = 0;
};

double band_ssim(Imaging rx, Imaging ry, int offset) {
    const int w = rx->xsize, h = rx->ysize;
    const int ow = w - kWindow + 1, oh = h - kWindow + 1;
    const int px = rx->pixelsize, py = ry->pixelsize;
    const int64_t n = kWindow * kWindow;
    const double mean_scale = 1.0 / static_cast<double>(n * n);
    const double var_scale = 1.0 / static_cast<double>(n * (n - 1));
    const double c1 = (0.01 * 255) * (0.01 * 255);
    const double c2 = (0.03 * 255) * (0.03 * 255);
    // ImageMath turns each float constant into an "F" image: float32.
    const float k_cross = static_cast<float>(2 * mean_scale);
    const float k_cov = static_cast<float>(2 * var_scale);
    const float k_squares = static_cast<float>(mean_scale);
    const float k_var = static_cast<float>(var_scale);
    const float f_c1 = static_cast<float>(c1);
    const float f_c2 = static_cast<float>(c2);

    std::vector<WindowSums> columns(static_cast<size_t>(w));
    auto add_row = [&](int row, int sign) {
        const auto *a = reinterpret_cast<const uint8_t *>(rx->image[row]) + offset;
        const auto *b = reinterpret_cast<const uint8_t *>(ry->image[row]) + offset;
        for (int col = 0; col < w; ++col) {
            int64_t x = a[static_cast<size_t>(col) * px], y = b[static_cast<size_t>(col) * py];
            WindowSums &c = columns[static_cast<size_t>(col)];
            c.x += sign * x;
            c.y += sign * y;
            c.xx += sign * x * x;
            c.yy += sign * y * y;
            c.xy += sign * x * y;
        }
    };
    for (int row = 0; row < kWindow; ++row)
        add_row(row, 1);

    PySum total;
    for (int oy = 0; oy < oh; ++oy) {
        if (oy > 0) {
            add_row(oy - 1, -1);
            add_row(oy + kWindow - 1, 1);
        }
        WindowSums s;
        for (int col = 0; col < kWindow; ++col) {
            const WindowSums &c = columns[static_cast<size_t>(col)];
            s.x += c.x;
            s.y += c.y;
            s.xx += c.xx;
            s.yy += c.yy;
            s.xy += c.xy;
        }
        for (int ox = 0; ox < ow; ++ox) {
            if (ox > 0) {
                const WindowSums &in = columns[static_cast<size_t>(ox + kWindow - 1)];
                const WindowSums &out = columns[static_cast<size_t>(ox - 1)];
                s.x += in.x - out.x;
                s.y += in.y - out.y;
                s.xx += in.xx - out.xx;
                s.yy += in.yy - out.yy;
                s.xy += in.xy - out.xy;
            }
            // The exact numerators, as 32-bit integers (they stay below 2**31).
            auto cross = static_cast<int32_t>(s.x * s.y);
            auto squares = static_cast<int32_t>(s.x * s.x + s.y * s.y);
            auto covariance = static_cast<int32_t>(s.xy * n - cross);
            auto variances = static_cast<int32_t>((s.xx + s.yy) * n - squares);
            // convert("F"), then one float32 operation after another.
            float p = static_cast<float>(cross);
            float q = static_cast<float>(squares);
            float c = static_cast<float>(covariance);
            float v = static_cast<float>(variances);
            float t1 = k_cross * p;
            t1 = t1 + f_c1;
            float t2 = k_cov * c;
            t2 = t2 + f_c2;
            float numerator = t1 * t2;
            float t3 = k_squares * q;
            t3 = t3 + f_c1;
            float t4 = k_var * v;
            t4 = t4 + f_c2;
            float denominator = t3 * t4;
            float value = denominator != 0.0f ? numerator / denominator : 0.0f;
            total.add(static_cast<double>(value));
        }
    }
    return total.result() / static_cast<double>(static_cast<int64_t>(ow) * oh);
}

} // namespace

double ssim(const Image &reference, const Image &candidate_in) {
    Image candidate = candidate_in.convert(reference.mode());
    if (std::min(reference.width(), reference.height()) < kWindow)
        return difference(reference, candidate).getbbox() ? 0.0 : 1.0;
    Imaging rx = reference.im(), ry = candidate.im();
    if (rx->type != IMAGING_TYPE_UINT8 || ry->type != IMAGING_TYPE_UINT8 || rx->xsize != ry->xsize ||
        rx->ysize != ry->ysize)
        throw Error("images do not match");
    std::vector<double> scores;
    for (int band = 0; band < rx->bands; ++band) {
        int offset = (rx->bands == 2 && band == 1) ? 3 : band;
        scores.push_back(band_ssim(rx, ry, offset));
    }
    return py_sum(scores) / static_cast<double>(scores.size());
}

} // namespace pdft::img
