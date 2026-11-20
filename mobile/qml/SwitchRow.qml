// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PdfTeleporter

// A setting that is on or off, the whole row a touch target: a name, a line
// of detail and a switch drawn in the palette's own colours. The owner keeps
// "on" and answers "clicked".
AbstractButton {
    id: control

    property bool on: false
    property string title
    property string detail

    Layout.fillWidth: true
    implicitHeight: Math.max(56, row.implicitHeight + topPadding + bottomPadding)
    padding: 10
    leftPadding: 12
    rightPadding: 12
    opacity: enabled ? 1 : 0.5

    Accessible.role: Accessible.CheckBox
    Accessible.name: title
    Accessible.description: detail
    Accessible.checked: on

    background: Rectangle {
        radius: 12
        color: control.pressed ? Theme.surfaceAlt : "transparent"
        border.width: control.visualFocus ? 2 : 0
        border.color: Theme.accent
    }

    contentItem: RowLayout {
        id: row

        spacing: 14

        ColumnLayout {
            spacing: 2
            Layout.fillWidth: true

            Label {
                text: control.title
                color: Theme.text
                font.pixelSize: Theme.bodySize
                wrapMode: Text.WordWrap
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

        Rectangle {
            id: track

            implicitWidth: 48
            implicitHeight: 28
            radius: height / 2
            color: control.on ? Theme.accent : Theme.surfaceAlt
            border.width: control.on ? 0 : 2
            border.color: Theme.textDim
            Layout.alignment: Qt.AlignVCenter

            Rectangle {
                width: control.on ? 20 : 14
                height: width
                radius: width / 2
                anchors.verticalCenter: parent.verticalCenter
                x: control.on ? track.width - width - 4 : 7
                color: control.on ? Theme.accentText : Theme.textDim

                Behavior on x {
                    NumberAnimation { duration: 120; easing.type: Easing.OutCubic }
                }
            }
        }
    }
}
