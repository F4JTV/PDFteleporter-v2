// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PdfTeleporter

// One option of a set, the whole row a touch target: a radio mark, a name
// and a line of detail. The owner keeps "selected" and answers "clicked".
AbstractButton {
    id: control

    property bool selected: false
    property string title
    property string detail

    Layout.fillWidth: true
    implicitHeight: Math.max(56, row.implicitHeight + topPadding + bottomPadding)
    padding: 10
    leftPadding: 12
    rightPadding: 12
    opacity: enabled ? 1 : 0.5

    Accessible.role: Accessible.RadioButton
    Accessible.name: title
    Accessible.description: detail

    background: Rectangle {
        radius: 12
        color: control.selected ? Theme.accentSoft
               : control.pressed ? Theme.surfaceAlt : "transparent"
        border.width: control.visualFocus ? 2 : 0
        border.color: Theme.accent

        Behavior on color {
            ColorAnimation { duration: 120 }
        }
    }

    contentItem: RowLayout {
        id: row

        spacing: 14

        Rectangle {
            implicitWidth: 22
            implicitHeight: 22
            radius: 11
            color: "transparent"
            border.width: 2
            border.color: control.selected ? Theme.accent : Theme.textDim

            Rectangle {
                anchors.centerIn: parent
                width: 10
                height: 10
                radius: 5
                color: Theme.accent
                visible: control.selected
            }
        }

        ColumnLayout {
            spacing: 2
            Layout.fillWidth: true

            Label {
                text: control.title
                color: Theme.text
                font.pixelSize: Theme.bodySize
                font.weight: control.selected ? Font.DemiBold : Font.Normal
                elide: Text.ElideRight
                Layout.fillWidth: true
            }

            Label {
                visible: text !== ""
                text: control.detail
                color: Theme.textDim
                font.pixelSize: Theme.smallSize
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }
    }
}
