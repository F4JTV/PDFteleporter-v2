// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PdfTeleporter

// A file: its icon, its name and a line about it.
RowLayout {
    id: line

    property string iconName
    property color iconColor: Theme.accent
    property string name
    property string info
    property color infoColor: Theme.textDim

    spacing: 12
    Layout.fillWidth: true

    Icon {
        name: line.iconName
        color: line.iconColor
        size: 30
        Layout.alignment: Qt.AlignTop
    }

    ColumnLayout {
        spacing: 2
        Layout.fillWidth: true

        Label {
            text: line.name
            color: Theme.text
            font.pixelSize: Theme.bodySize
            font.weight: Font.DemiBold
            elide: Text.ElideMiddle
            Layout.fillWidth: true
        }

        Label {
            visible: text !== ""
            text: line.info
            color: line.infoColor
            font.pixelSize: Theme.smallSize
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }
}
