// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PdfTeleporter

// Archive -> PDF: the archive received, checked as soon as it is chosen,
// and the PDF rebuilt from it.
ScrollPage {
    id: page

    signal pickArchive()
    signal savePdf()

    Connections {
        target: Backend

        function onPdfChanged() {
            if (Backend.hasPdf)
                page.revealEnd()
        }
    }

    Card {
        title: "Archive reçue"
        iconName: "unarchive"

        Label {
            visible: !Backend.hasReceived
            text: "Aucune archive sélectionnée. Ouvrez une archive .psdi ou .psdz reçue, ou "
                  + "partagez-la vers PDF Teleporter depuis une autre application."
            color: Theme.textDim
            font.pixelSize: Theme.smallSize + 1
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        FileLine {
            visible: Backend.hasReceived
            iconName: !Backend.receivedValid ? "error" : Backend.receivedCrcOk ? "success" : "warning"
            iconColor: !Backend.receivedValid ? Theme.error
                       : Backend.receivedCrcOk ? Theme.success : Theme.warning
            name: Backend.receivedName
            info: Backend.receivedInfo
            infoColor: !Backend.receivedValid ? Theme.error
                       : Backend.receivedCrcOk ? Theme.textDim : Theme.warning
        }

        ActionButton {
            text: Backend.hasReceived ? "Choisir une autre archive…" : "Choisir une archive…"
            iconName: "folder"
            enabled: !Backend.busy
            Layout.fillWidth: true
            onClicked: page.pickArchive()
        }
    }

    ActionButton {
        kind: "primary"
        text: "Recomposer le PDF"
        iconName: "rebuild"
        enabled: Backend.hasReceived && Backend.receivedValid && !Backend.busy
        Layout.fillWidth: true
        onClicked: Backend.rebuild()
    }

    Card {
        visible: Backend.hasPdf
        title: "PDF recomposé"
        iconName: "pdf"
        highlighted: true

        FileLine {
            iconName: "pdf"
            name: Backend.pdfName
            info: Backend.pdfInfo
        }

        ActionButton {
            kind: "primary"
            text: "Ouvrir le PDF"
            iconName: "open"
            enabled: !Backend.busy
            Layout.fillWidth: true
            onClicked: Backend.viewPdf()
        }

        GridLayout {
            columns: page.width > 440 ? 2 : 1
            columnSpacing: 8
            rowSpacing: 0
            Layout.fillWidth: true

            ActionButton {
                text: Backend.canShare ? "Partager" : "Ouvrir le dossier"
                iconName: Backend.canShare ? "share" : "folder"
                enabled: !Backend.busy
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                onClicked: Backend.sharePdf()
            }

            ActionButton {
                text: "Enregistrer sous…"
                iconName: "save"
                enabled: !Backend.busy
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                onClicked: page.savePdf()
            }
        }
    }
}
