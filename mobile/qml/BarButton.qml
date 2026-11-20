// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

import QtQuick
import QtQuick.Controls
import PdfTeleporter

// An icon button of the top bar. Its name shows on a long press, or on
// hover with a mouse, and is what a screen reader says.
ToolButton {
    id: control

    property string iconName
    property string tip

    implicitWidth: 48
    implicitHeight: 48

    Accessible.name: tip
    ToolTip.text: tip
    ToolTip.delay: 600
    ToolTip.visible: hovered && tip !== ""

    onPressAndHold: if (tip !== "") ToolTip.show(tip, 1500)

    contentItem: Item {
        Icon {
            anchors.centerIn: parent
            name: control.iconName
            color: Theme.barText
        }
    }
}
