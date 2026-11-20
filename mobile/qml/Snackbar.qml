// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material.impl
import QtQuick.Layouts
import PdfTeleporter

// A short confirmation at the bottom of the screen, gone by itself.
Popup {
    id: bar

    function show(message) {
        label.text = message
        open()
        timer.restart()
    }

    x: ((parent ? parent.width : 0) - width) / 2
    y: (parent ? parent.height : 0) - height - 16
    width: Math.min((parent ? parent.width : 400) - 32, 520)
    padding: 14
    leftPadding: 16
    closePolicy: Popup.CloseOnPressOutside
    focus: false

    background: Rectangle {
        radius: 12
        color: Theme.night ? "#2A0000" : Theme.dark ? "#E6EDF5" : "#262D36"
        border.width: Theme.night ? 1 : 0
        border.color: Theme.outline
        layer.enabled: Theme.shadows
        layer.effect: ElevationEffect {
            elevation: 6
        }
    }

    contentItem: RowLayout {
        spacing: 12

        Icon {
            name: "check"
            color: Theme.night ? Theme.accent : Theme.dark ? "#1D6CBD" : "#8CC4FF"
            size: 20
        }

        Label {
            id: label

            color: Theme.night ? Theme.text : Theme.dark ? "#17202B" : "#F2F5F9"
            font.pixelSize: Theme.smallSize + 1
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
    }

    enter: Transition {
        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 150 }
    }

    exit: Transition {
        NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 200 }
    }

    Timer {
        id: timer

        interval: 3500
        onTriggered: bar.close()
    }
}
