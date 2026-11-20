// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts
import PdfTeleporter

// A Material button with an icon: "primary" is the raised accent button of
// the page's main action, "tonal" a softer filled one, "text" a flat one.
Button {
    id: control

    property string iconName
    property string kind: "tonal"

    readonly property color contentColor: !enabled ? Theme.textDim
                                          : kind === "primary" ? Theme.accentText : Theme.accent

    flat: kind === "text"
    highlighted: kind === "primary"
    topInset: 4
    bottomInset: 4
    leftPadding: 20
    rightPadding: 20
    implicitHeight: kind === "primary" ? 60 : 52

    font.pixelSize: kind === "primary" ? 16 : 15
    font.weight: Font.DemiBold
    font.capitalization: Font.MixedCase

    Material.background: kind === "primary" ? Theme.accent
                         : kind === "tonal" ? Theme.accentSoft : "transparent"
    Material.foreground: contentColor
    Material.elevation: kind === "primary" && Theme.shadows ? 2 : 0

    contentItem: RowLayout {
        spacing: 10

        Item { Layout.fillWidth: true }

        Icon {
            name: control.iconName
            color: control.contentColor
            size: 20
        }

        Label {
            text: control.text
            font: control.font
            color: control.contentColor
            elide: Text.ElideRight
            Layout.maximumWidth: control.availableWidth - (control.iconName !== "" ? 30 : 0)
        }

        Item { Layout.fillWidth: true }
    }
}
