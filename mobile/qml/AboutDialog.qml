// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts
import PdfTeleporter

// The About box, with the notices GPL v3 section 5(d) asks an interactive
// interface to show: copyright, no warranty, and how to read the licence.
// The source location is given because a binary seen without it is what
// section 6 is about. The wording is the desktop version's.
Dialog {
    id: dialog

    signal showText(string title, string text)

    readonly property string sourceUrl: "https://github.com/F4JTV/PDFteleporter-v2"

    anchors.centerIn: Overlay.overlay
    width: Math.min((parent ? parent.width : 480) - 24, 520)
    height: Math.min(content.implicitHeight + 140, (parent ? parent.height : 800) - 48)
    modal: true
    title: "À propos de PDF Teleporter"
    padding: 0
    topPadding: 4

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
        contentHeight: content.implicitHeight + 16
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        ScrollIndicator.vertical: ScrollIndicator {}

        ColumnLayout {
            id: content

            x: 24
            width: flick.width - 48
            spacing: 14

            RowLayout {
                spacing: 16

                Image {
                    visible: !Theme.night
                    source: "images/pdfteleporter.png"
                    sourceSize: Qt.size(128, 128)
                    Layout.preferredWidth: 64
                    Layout.preferredHeight: 64
                }

                Icon {
                    visible: Theme.night
                    name: "antenna"
                    color: Theme.accent
                    size: 56
                }

                ColumnLayout {
                    spacing: 2
                    Layout.fillWidth: true

                    Label {
                        text: "PDF Teleporter"
                        color: Theme.text
                        font.pixelSize: 22
                        font.weight: Font.DemiBold
                    }

                    Label {
                        text: "Version " + Backend.version
                        color: Theme.textDim
                        font.pixelSize: Theme.bodySize
                    }
                }
            }

            Label {
                text: "Compresse des documents PDF en archives .psdi pour les transmettre sur des "
                      + "liaisons radio à bande étroite, et les recompose à la réception."
                color: Theme.text
                font.pixelSize: Theme.bodySize
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            Label {
                text: "Les archives .psdi sont interopérables avec les autres implémentations du "
                      + "format .psdi. Les archives compactes .psdz, plus légères, ne sont lues que "
                      + "par PDF Teleporter 2.5.0 et plus."
                color: Theme.text
                font.pixelSize: Theme.bodySize
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            Label {
                text: "Copyright (C) 2026 The PDF Teleporter contributors.\nD'après PDF Teleporter "
                      + "et le format .psdi de F1GBD (Jean-Louis Naudin), ADRASEC 77 - FNRASEC."
                color: Theme.textDim
                font.pixelSize: Theme.smallSize + 1
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            Label {
                text: "Ce programme est un logiciel libre distribué sous licence GNU GPL version 3, "
                      + "<b>sans aucune garantie</b>. Il incorpore MuPDF, sous licence GNU AGPL "
                      + "version 3, et d'autres composants dont les licences se lisent ci-dessous."
                textFormat: Text.StyledText
                color: Theme.text
                font.pixelSize: Theme.smallSize + 1
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            Label {
                text: "Code source : <a href=\"" + dialog.sourceUrl + "\">"
                      + dialog.sourceUrl.replace("https://", "") + "</a>"
                textFormat: Text.StyledText
                linkColor: Theme.accent
                color: Theme.text
                font.pixelSize: Theme.smallSize + 1
                wrapMode: Text.WrapAnywhere
                Layout.fillWidth: true
                onLinkActivated: (link) => Qt.openUrlExternally(link)
            }

            GridLayout {
                columns: content.width > 360 ? 2 : 1
                columnSpacing: 8
                rowSpacing: 0
                Layout.fillWidth: true

                ActionButton {
                    text: "Licence"
                    iconName: "copyright"
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    onClicked: dialog.showText("Licence GNU GPL version 3", Backend.licenseText("gpl"))
                }

                ActionButton {
                    text: "Composants tiers"
                    iconName: "code"
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    onClicked: dialog.showText("Composants tiers", Backend.licenseText("third"))
                }
            }
        }
    }

    onOpened: flick.contentY = 0
}
