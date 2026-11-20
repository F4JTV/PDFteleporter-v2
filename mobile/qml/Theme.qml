// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

pragma Singleton

import QtQuick
import PdfTeleporter

// The three palettes. Light for the day, dark for the evening, and red for
// the night: red alone, on black, keeps the operator's eyes adapted to the
// dark, so in that one every colour, the "white" included, is a red.
QtObject {
    readonly property string name: Backend.theme
    readonly property bool dark: name !== "light"
    readonly property bool night: name === "red"
    // A shadow on black is invisible: the night palette outlines instead.
    readonly property bool shadows: !night

    readonly property color background: night ? "#000000" : dark ? "#0F141A" : "#EDF1F6"
    readonly property color surface: night ? "#110000" : dark ? "#19212B" : "#FFFFFF"
    readonly property color surfaceAlt: night ? "#1A0000" : dark ? "#212A36" : "#F2F5F9"
    readonly property color outline: night ? "#4A0000" : dark ? "#2E3A48" : "#D3DBE6"

    // The bars at the top and the bottom.
    readonly property color bar: night ? "#0C0000" : dark ? "#17202A" : "#1E5C9A"
    readonly property color barText: night ? "#E00000" : dark ? "#E6EDF5" : "#FFFFFF"
    readonly property color barTextDim: night ? "#8C0000" : dark ? "#97A8BA" : "#C9DCF1"
    readonly property color navigation: night ? "#0C0000" : dark ? "#17202A" : "#FFFFFF"

    readonly property color accent: night ? "#FF0000" : dark ? "#6CAEF0" : "#1D6CBD"
    readonly property color accentText: night ? "#000000" : dark ? "#06121F" : "#FFFFFF"
    // Tonal fills: the selected destination, a chosen option, a second action.
    readonly property color accentSoft: night ? "#2A0000" : dark ? "#1F3550" : "#DCE9F7"

    readonly property color text: night ? "#E00000" : dark ? "#E6EDF5" : "#17202B"
    readonly property color textDim: night ? "#960000" : dark ? "#97A8BA" : "#5A6A7D"

    // In the night palette the levels differ by their icon, not their hue.
    readonly property color success: night ? "#C80000" : dark ? "#7CC77F" : "#2E7D32"
    readonly property color warning: night ? "#FF0000" : dark ? "#FFB74D" : "#A85A00"
    readonly property color error: night ? "#FF0000" : dark ? "#F07070" : "#C62828"
    // Behind a dialog: the night palette darkens, it never brightens.
    readonly property color dim: night ? "#E0000000" : dark ? "#B3000000" : "#80000000"
    readonly property color warningSoft: night ? "#220000" : dark ? "#3A2B13" : "#FFF2DF"
    readonly property color errorSoft: night ? "#220000" : dark ? "#3E1D1D" : "#FDEBEA"

    readonly property real radius: 16
    readonly property int gutter: 16
    // Wider than this, on a tablet or a phone on its side, the content
    // stays a column in the middle.
    readonly property int maxContentWidth: 640

    readonly property int titleSize: 20
    readonly property int headingSize: 16
    readonly property int bodySize: 15
    readonly property int smallSize: 13

    function levelColor(level) {
        switch (level) {
        case "success": return success
        case "warning": return warning
        case "error": return error
        default: return textDim
        }
    }

    function levelIcon(level) {
        switch (level) {
        case "success": return "success"
        case "warning": return "warning"
        case "error": return "error"
        default: return "info"
        }
    }

    // The theme after this one, for the button that cycles through them.
    function next(current) {
        return current === "light" ? "dark" : current === "dark" ? "red" : "light"
    }
}
