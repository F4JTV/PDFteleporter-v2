// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
#pragma once

#include <string>

namespace pdft {
// Prints the MuPDF layer's report on one page as JSON.
int dump_page(const std::string &path, int number);
} // namespace pdft
