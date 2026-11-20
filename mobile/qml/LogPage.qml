// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PdfTeleporter

// The operating log, newest line last, as the desktop version keeps it: what
// was compressed, rebuilt, sent and saved, for the debrief.
Item {
    id: page

    ListView {
        id: list

        anchors.fill: parent
        model: Backend.log
        clip: true
        topMargin: 8
        bottomMargin: 8
        boundsBehavior: Flickable.StopAtBounds

        ScrollIndicator.vertical: ScrollIndicator {}

        onCountChanged: Qt.callLater(list.positionViewAtEnd)
        Component.onCompleted: positionViewAtEnd()

        delegate: Item {
            id: entry

            required property string time
            required property string message
            required property string level

            width: ListView.view.width
            implicitHeight: row.implicitHeight + 20

            RowLayout {
                id: row

                width: Math.min(entry.width - 2 * Theme.gutter, Theme.maxContentWidth)
                x: (entry.width - width) / 2
                y: 10
                spacing: 12

                Icon {
                    name: Theme.levelIcon(entry.level)
                    color: entry.level === "info" ? Theme.textDim : Theme.levelColor(entry.level)
                    size: 20
                    Layout.alignment: Qt.AlignTop
                }

                ColumnLayout {
                    spacing: 2
                    Layout.fillWidth: true

                    Label {
                        text: entry.time
                        color: Theme.textDim
                        font.pixelSize: 12
                        font.family: "monospace"
                    }

                    Label {
                        text: entry.message
                        color: entry.level === "error" || entry.level === "warning"
                               ? Theme.levelColor(entry.level) : Theme.text
                        font.pixelSize: Theme.smallSize + 1
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }
                }
            }

            Rectangle {
                anchors.bottom: parent.bottom
                width: row.width
                x: row.x
                height: 1
                color: Theme.outline
                opacity: 0.6
            }
        }
    }

    Label {
        anchors.centerIn: parent
        visible: list.count === 0
        text: "Journal vide."
        color: Theme.textDim
        font.pixelSize: Theme.bodySize
    }
}
