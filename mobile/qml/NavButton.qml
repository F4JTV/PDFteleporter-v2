// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PdfTeleporter

// A destination of the bottom navigation bar: the current one carries its
// icon in a tonal pill, as Material navigation bars do.
AbstractButton {
    id: control

    property string iconName
    property bool current: false

    Layout.fillWidth: true
    Layout.fillHeight: true
    focusPolicy: Qt.TabFocus

    Accessible.role: Accessible.PageTab
    Accessible.name: text

    background: Item {}

    contentItem: Item {
        ColumnLayout {
            anchors.centerIn: parent
            spacing: 4

            Rectangle {
                implicitWidth: 64
                implicitHeight: 32
                radius: 16
                color: control.current ? Theme.accentSoft
                       : control.pressed ? Theme.surfaceAlt : "transparent"
                border.width: control.visualFocus ? 2 : 0
                border.color: Theme.accent
                Layout.alignment: Qt.AlignHCenter

                Behavior on color {
                    ColorAnimation { duration: 150 }
                }

                Icon {
                    anchors.centerIn: parent
                    name: control.iconName
                    color: control.current ? Theme.accent : Theme.textDim
                }
            }

            Label {
                text: control.text
                color: control.current ? Theme.text : Theme.textDim
                font.pixelSize: 13
                font.weight: control.current ? Font.DemiBold : Font.Normal
                Layout.alignment: Qt.AlignHCenter
            }
        }
    }
}
