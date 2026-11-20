// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// What the phone offers that the desktop does not: the share sheet, opening a
// file in another application, and files handed over by other applications
// ("Ouvrir avec", "Partager vers"). On the desktop the same calls fall back
// to the file manager and to the command line.
#pragma once

#include <QString>

namespace pdft::app::platform {

// Whether the platform has a share sheet (Android).
bool canShare();

// Offers `path` (a file of the application's own storage) to the other
// applications. False when nothing could be started.
bool shareFile(const QString &path, const QString &mime, const QString &title);

// Opens `path` in the application the system associates with `mime`.
bool openFile(const QString &path, const QString &mime);

// A document another application handed over since the last call (a
// content:// or file:// URI, or a path), or an empty string.
QString takeIncoming();

// Sets the document given on the command line (desktop).
void setStartupArgument(const QString &argument);

} // namespace pdft::app::platform
