// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// A guided run through the interface, for the documentation and the tests
// (desktop builds, --tour): compress a document, rebuild the archive made,
// and save a picture of every screen, in the three themes.
#pragma once

#include <QString>

class QQuickWindow;

namespace pdft::app {

class Backend;

// The process exit code: 0 when every step completed.
int run_tour(QQuickWindow *window, Backend &backend, const QString &document, const QString &out_dir);

} // namespace pdft::app
