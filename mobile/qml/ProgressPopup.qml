// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Controls.Material.impl
import QtQuick.Layouts
import PdfTeleporter

// The conversion under way: the engine's own step, how far it is, and the
// way out.
Popup {
    id: popup

    anchors.centerIn: Overlay.overlay
    width: Math.min((parent ? parent.width : 400) - 48, 420)
    modal: true
    closePolicy: Popup.NoAutoClose
    padding: 24
    visible: Backend.busy

    Material.accent: Theme.accent

    Overlay.modal: Rectangle {
        color: Theme.dim
    }

    background: Rectangle {
        radius: Theme.radius
        color: Theme.surface
        border.width: Theme.shadows ? 0 : 1
        border.color: Theme.outline
        layer.enabled: Theme.shadows
        layer.effect: ElevationEffect {
            elevation: 12
        }
    }

    contentItem: ColumnLayout {
        spacing: 16

        RowLayout {
            spacing: 14

            BusyIndicator {
                running: popup.visible
                implicitWidth: 40
                implicitHeight: 40
            }

            Label {
                text: Backend.busyTitle
                color: Theme.text
                font.pixelSize: 19
                font.weight: Font.DemiBold
                Layout.fillWidth: true
            }
        }

        ProgressBar {
            value: Backend.progress
            Layout.fillWidth: true
        }

        RowLayout {
            spacing: 8
            Layout.fillWidth: true

            Label {
                text: Backend.progressText
                color: Theme.textDim
                font.pixelSize: Theme.smallSize
                elide: Text.ElideRight
                Layout.fillWidth: true
            }

            Label {
                text: Math.round(Backend.progress * 100) + " %"
                color: Theme.textDim
                font.pixelSize: Theme.smallSize
            }
        }

        ActionButton {
            kind: "text"
            text: "Annuler"
            iconName: "close"
            Layout.alignment: Qt.AlignRight
            onClicked: Backend.cancel()
        }
    }
}
