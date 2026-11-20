// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts
import PdfTeleporter

// A long plain text to read through: a licence, the third-party notices.
Dialog {
    id: dialog

    property alias text: body.text

    anchors.centerIn: Overlay.overlay
    width: Math.min((parent ? parent.width : 600) - 24, 720)
    height: (parent ? parent.height : 800) - 48
    modal: true
    padding: 0
    topPadding: 8

    Material.background: Theme.surface
    Material.foreground: Theme.text
    Material.accent: Theme.accent

    Overlay.modal: Rectangle {
        color: Theme.dim
    }

    footer: DialogButtonBox {
        Button {
            text: "Fermer"
            flat: true
            DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
        }
    }

    Flickable {
        id: flick

        anchors.fill: parent
        contentWidth: width
        contentHeight: body.implicitHeight + 24
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        ScrollIndicator.vertical: ScrollIndicator {}

        // Plain text in a TextEdit: selectable, and laid out in one piece
        // however long it is.
        TextEdit {
            id: body

            x: 20
            y: 8
            width: flick.width - 40
            readOnly: true
            selectByMouse: false
            wrapMode: TextEdit.Wrap
            textFormat: TextEdit.PlainText
            color: Theme.text
            font.pixelSize: 12
            font.family: "monospace"
        }
    }

    onOpened: flick.contentY = 0
}
