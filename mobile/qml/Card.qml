// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material.impl
import QtQuick.Layouts
import PdfTeleporter

// A raised card: a rounded surface with a Material shadow, a titled
// heading and its content in a column.
Item {
    id: card

    property string title
    property string iconName
    // How high the card stands: the shadow it casts.
    property int elevation: 2
    // An accent outline, for the card holding the result just made.
    property bool highlighted: false
    property real padding: 16
    default property alias content: body.data

    Layout.fillWidth: true
    implicitWidth: column.implicitWidth + 2 * padding
    implicitHeight: column.implicitHeight + 2 * padding

    Rectangle {
        id: surface

        anchors.fill: parent
        radius: Theme.radius
        color: Theme.surface
        border.width: card.highlighted ? 2 : Theme.shadows ? 0 : 1
        border.color: card.highlighted ? Theme.accent : Theme.outline

        layer.enabled: Theme.shadows && card.elevation > 0
        layer.effect: ElevationEffect {
            elevation: card.elevation
        }
    }

    ColumnLayout {
        id: column

        anchors.fill: parent
        anchors.margins: card.padding
        spacing: 12

        RowLayout {
            visible: card.title !== ""
            spacing: 12
            Layout.fillWidth: true

            Rectangle {
                visible: card.iconName !== ""
                implicitWidth: 36
                implicitHeight: 36
                radius: 18
                color: Theme.accentSoft

                Icon {
                    anchors.centerIn: parent
                    name: card.iconName
                    color: Theme.accent
                    size: 20
                }
            }

            Label {
                text: card.title
                color: Theme.text
                font.pixelSize: Theme.headingSize
                font.weight: Font.DemiBold
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
        }

        ColumnLayout {
            id: body

            spacing: 10
            Layout.fillWidth: true
        }
    }
}
