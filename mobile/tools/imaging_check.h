// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
#pragma once

#include <string>

namespace pdft {
// Runs the image jobs of a JSON file and prints their results as JSON.
int imaging_check(const std::string &jobs_path);
} // namespace pdft
