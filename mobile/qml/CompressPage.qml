// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PdfTeleporter

// PDF or image -> archive: the document, the settings, what the archive
// would weigh on the air, and the archive once made.
ScrollPage {
    id: page

    signal pickDocument()
    signal saveArchive()

    Connections {
        target: Backend

        function onArchiveChanged() {
            if (Backend.hasArchive)
                page.revealEnd()
        }
    }

    Card {
        title: "Document à compresser"
        iconName: "document"

        Label {
            visible: !Backend.hasSource
            text: "Aucun document sélectionné. PDF Teleporter compresse les PDF et les images "
                  + "(JPEG, PNG, BMP, TIFF, WebP)."
            color: Theme.textDim
            font.pixelSize: Theme.smallSize + 1
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        FileLine {
            visible: Backend.hasSource
            iconName: Backend.sourceIsImage ? "image" : "pdf"
            name: Backend.sourceName
            info: Backend.sourceInfo
        }

        ActionButton {
            text: Backend.hasSource ? "Choisir un autre document…" : "Choisir un PDF ou une image…"
            iconName: "folder"
            enabled: !Backend.busy
            Layout.fillWidth: true
            onClicked: page.pickDocument()
        }
    }

    Card {
        title: "Qualité"
        iconName: "tune"

        ColumnLayout {
            spacing: 4
            Layout.fillWidth: true

            Repeater {
                model: Backend.qualities

                delegate: ChoiceRow {
                    required property var modelData
                    required property int index

                    title: modelData.label
                    detail: modelData.description
                    selected: index === Backend.qualityIndex
                    enabled: !Backend.busy
                    onClicked: Backend.qualityIndex = index
                }
            }
        }
    }

    Card {
        title: "Options"
        iconName: "text"

        SwitchRow {
            title: "Texte seul"
            detail: "Supprime toutes les images du document."
            on: Backend.textOnly
            enabled: !Backend.busy
            onClicked: Backend.textOnly = !Backend.textOnly
        }

        SwitchRow {
            title: "Compatible avec la version F1GBD"
            detail: Backend.compatible
                    ? "Archive .psdi, lisible par toutes les stations, version F1GBD comprise."
                    : "Archive compacte .psdz, nettement plus légère, lisible seulement par "
                      + "PDF Teleporter 2.5.0 et plus."
            on: Backend.compatible
            enabled: !Backend.busy
            onClicked: Backend.compatible = !Backend.compatible
        }
    }

    Card {
        title: "Estimation"
        iconName: "antenna"

        Label {
            visible: Backend.estimateState === "none"
            text: "La taille de l'archive et sa durée de transmission s'affichent ici dès qu'un "
                  + "document est sélectionné."
            color: Theme.textDim
            font.pixelSize: Theme.smallSize + 1
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        RowLayout {
            visible: Backend.estimateState === "running"
            spacing: 12

            BusyIndicator {
                running: parent.visible
                implicitWidth: 32
                implicitHeight: 32
            }

            Label {
                text: "Estimation en cours…"
                color: Theme.textDim
                font.pixelSize: Theme.bodySize
            }
        }

        Label {
            visible: Backend.estimateState === "failed"
            text: "Estimation impossible pour ce document."
            color: Theme.error
            font.pixelSize: Theme.bodySize
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        ColumnLayout {
            visible: Backend.estimateState === "ready"
            spacing: 8
            Layout.fillWidth: true

            RowLayout {
                spacing: 8
                Layout.fillWidth: true

                Label {
                    text: "Archive"
                    color: Theme.textDim
                    font.pixelSize: Theme.bodySize
                    Layout.fillWidth: true
                }

                Label {
                    text: Backend.estimateSize
                    color: Theme.text
                    font.pixelSize: 24
                    font.weight: Font.DemiBold
                }
            }

            Rectangle {
                implicitHeight: 1
                color: Theme.outline
                Layout.fillWidth: true
            }

            Repeater {
                model: Backend.estimateTimes

                delegate: RowLayout {
                    required property var modelData

                    spacing: 10
                    Layout.fillWidth: true

                    Icon {
                        name: "time"
                        color: Theme.textDim
                        size: 18
                    }

                    Label {
                        text: modelData.label
                        color: Theme.text
                        font.pixelSize: Theme.bodySize
                        Layout.fillWidth: true
                    }

                    Label {
                        text: modelData.time
                        color: Theme.text
                        font.pixelSize: Theme.bodySize
                        font.weight: Font.DemiBold
                    }
                }
            }

            Notice {
                visible: Backend.estimateOverLimit
                level: "warning"
                text: "Au-dessus de la limite Winlink de " + Backend.winlinkLimitKb
                      + " ko — choisissez une qualité inférieure."
            }
        }
    }

    ActionButton {
        kind: "primary"
        text: "Compresser"
        iconName: "compress"
        enabled: Backend.hasSource && !Backend.busy
        Layout.fillWidth: true
        onClicked: Backend.compress()
    }

    Card {
        visible: Backend.hasArchive
        title: "Archive prête"
        iconName: "archive"
        highlighted: true

        FileLine {
            iconName: "archive"
            name: Backend.archiveName
            info: Backend.archiveInfo
        }

        Notice {
            visible: Backend.archiveNote !== ""
            level: "warning"
            text: Backend.archiveNote
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
                onClicked: Backend.shareArchive()
            }

            ActionButton {
                text: "Enregistrer sous…"
                iconName: "save"
                enabled: !Backend.busy
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                onClicked: page.saveArchive()
            }
        }
    }
}
