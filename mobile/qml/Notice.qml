// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PdfTeleporter

// A message that must not read like the dimmed text around it: tinted, with
// the icon of its level ("warning" or "error").
Rectangle {
    id: notice

    property string level: "warning"
    property alias text: label.text

    Layout.fillWidth: true
    implicitHeight: row.implicitHeight + 20
    radius: 10
    color: level === "error" ? Theme.errorSoft : Theme.warningSoft
    border.width: Theme.night ? 1 : 0
    border.color: Theme.levelColor(level)

    RowLayout {
        id: row

        anchors.fill: parent
        anchors.margins: 10
        spacing: 10

        Icon {
            name: Theme.levelIcon(notice.level)
            color: Theme.levelColor(notice.level)
            size: 20
            Layout.alignment: Qt.AlignTop
        }

        Label {
            id: label

            color: Theme.text
            font.pixelSize: Theme.smallSize + 1
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }
}
