// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

import QtQuick
import QtQuick.Window
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Controls.Material.impl
import QtQuick.Dialogs
import QtQuick.Layouts
import PdfTeleporter

// The phone interface: three destinations in a bottom bar -- compress,
// rebuild, the log -- and the theme and the About box at the top.
ApplicationWindow {
    id: window

    readonly property bool android: Qt.platform.os === "android"
    readonly property var pageNames: ["Compresser", "Recomposer", "Journal"]

    function showPage(index) { pages.currentIndex = index }
    function showAbout() { aboutDialog.open() }
    function revealEnd(index) { pages.itemAt(index).revealEnd() }
    function closeDialogs() {
        textDialog.close()
        aboutDialog.close()
        errorDialog.close()
        overflowMenu.close()
    }

    // A theme in the menu: ticked when it is the one in use. A click on a
    // checkable item ticks it by itself, cutting the binding, which is set
    // again once the theme is changed.
    component ThemeItem: MenuItem {
        required property string theme

        checkable: true
        checked: Backend.theme === theme
        onTriggered: {
            Backend.theme = theme
            checked = Qt.binding(() => Backend.theme === theme)
        }
    }

    width: 412
    height: 892
    minimumWidth: 320
    minimumHeight: 480
    visible: true
    // A phone gives the whole screen to the application.
    visibility: android ? Window.FullScreen : Window.AutomaticVisibility
    title: "PDF Teleporter " + Backend.version
    color: Theme.background

    Material.theme: Theme.dark ? Material.Dark : Material.Light
    Material.primary: Theme.bar
    Material.accent: Theme.accent
    Material.background: Theme.background
    Material.foreground: Theme.text

    header: ToolBar {
        contentHeight: 60

        background: Rectangle {
            color: Theme.bar
            layer.enabled: Theme.shadows
            layer.effect: ElevationEffect {
                elevation: 4
                fullWidth: true
            }

            Rectangle {
                visible: !Theme.shadows
                anchors.bottom: parent.bottom
                width: parent.width
                height: 1
                color: Theme.outline
            }
        }

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 16
            anchors.rightMargin: 4
            spacing: 4

            Image {
                visible: !Theme.night
                source: "images/pdfteleporter.png"
                sourceSize: Qt.size(96, 96)
                Layout.preferredWidth: 34
                Layout.preferredHeight: 34
            }

            Icon {
                visible: Theme.night
                name: "antenna"
                color: Theme.barText
                size: 30
            }

            ColumnLayout {
                spacing: 0
                Layout.fillWidth: true
                Layout.leftMargin: 8

                Label {
                    text: "PDF Teleporter"
                    color: Theme.barText
                    font.pixelSize: Theme.titleSize
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }

                Label {
                    text: window.pageNames[pages.currentIndex]
                    color: Theme.barTextDim
                    font.pixelSize: Theme.smallSize
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
            }

            BarButton {
                visible: pages.currentIndex === 2
                iconName: "copy"
                tip: "Copier le journal"
                onClicked: Backend.copyLog()
            }

            BarButton {
                visible: pages.currentIndex === 2
                iconName: "clear"
                tip: "Effacer le journal"
                onClicked: Backend.log.clear()
            }

            BarButton {
                iconName: Theme.night ? "night" : Theme.dark ? "dark" : "light"
                tip: "Thème suivant"
                onClicked: Backend.theme = Theme.next(Backend.theme)
            }

            BarButton {
                id: moreButton

                iconName: "more"
                tip: "Menu"
                onClicked: overflowMenu.open()

                Menu {
                    id: overflowMenu

                    y: moreButton.height
                    x: moreButton.width - width

                    Material.background: Theme.surface
                    Material.foreground: Theme.text
                    Material.accent: Theme.accent

                    ThemeItem {
                        theme: "light"
                        text: "Thème clair"
                    }

                    ThemeItem {
                        theme: "dark"
                        text: "Thème sombre"
                    }

                    ThemeItem {
                        theme: "red"
                        text: "Thème rouge (vision de nuit)"
                    }

                    MenuSeparator {}

                    MenuItem {
                        text: "À propos"
                        onTriggered: aboutDialog.open()
                    }
                }
            }
        }
    }

    footer: ToolBar {
        position: ToolBar.Footer
        contentHeight: 72

        background: Rectangle {
            color: Theme.navigation
            layer.enabled: Theme.shadows
            layer.effect: ElevationEffect {
                elevation: 8
                fullWidth: true
            }

            Rectangle {
                width: parent.width
                height: 1
                color: Theme.outline
            }
        }

        RowLayout {
            anchors.fill: parent
            spacing: 0

            Repeater {
                model: [
                    { "icon": "compress", "text": "Compresser" },
                    { "icon": "rebuild", "text": "Recomposer" },
                    { "icon": "log", "text": "Journal" }
                ]

                delegate: NavButton {
                    required property var modelData
                    required property int index

                    iconName: modelData.icon
                    text: modelData.text
                    current: pages.currentIndex === index
                    onClicked: pages.currentIndex = index
                }
            }
        }
    }

    SwipeView {
        id: pages

        anchors.fill: parent

        CompressPage {
            onPickDocument: sourceDialog.open()
            onSaveArchive: {
                saveArchiveDialog.selectedFile = Backend.suggestedSaveUrl(Backend.archiveName)
                saveArchiveDialog.open()
            }
        }

        RebuildPage {
            onPickArchive: archiveDialog.open()
            onSavePdf: {
                savePdfDialog.selectedFile = Backend.suggestedSaveUrl(Backend.pdfName)
                savePdfDialog.open()
            }
        }

        LogPage {}
    }

    // --- choosing and saving files ---------------------------------------------------
    // On a phone these are the system's own pickers. The archive picker shows
    // every file there: an archive has no type the system knows, and a filter
    // on one would hide it.

    FileDialog {
        id: sourceDialog

        title: "Choisir un document"
        fileMode: FileDialog.OpenFile
        nameFilters: ["Documents et images (*.pdf *.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp)",
                      "Tous les fichiers (*)"]
        onAccepted: Backend.openSource(selectedFile)
    }

    FileDialog {
        id: archiveDialog

        title: "Choisir une archive"
        fileMode: FileDialog.OpenFile
        nameFilters: window.android ? []
                     : ["Archives PDF Teleporter (*.psdi *.psdz)", "Tous les fichiers (*)"]
        onAccepted: Backend.openReceived(selectedFile)
    }

    FileDialog {
        id: saveArchiveDialog

        title: "Enregistrer l'archive"
        fileMode: FileDialog.SaveFile
        nameFilters: Backend.archiveName.endsWith(".psdz") ? ["Archives compactes (*.psdz)"]
                                                           : ["Archives PSDI (*.psdi)"]
        onAccepted: Backend.saveArchiveTo(selectedFile)
    }

    FileDialog {
        id: savePdfDialog

        title: "Enregistrer le PDF"
        fileMode: FileDialog.SaveFile
        nameFilters: ["Documents PDF (*.pdf)"]
        onAccepted: Backend.savePdfTo(selectedFile)
    }

    // --- what the backend reports ----------------------------------------------------

    ProgressPopup {}

    Snackbar {
        id: snackbar
    }

    Dialog {
        id: errorDialog

        property alias text: errorText.text

        anchors.centerIn: Overlay.overlay
        width: Math.min(window.width - 48, 440)
        modal: true

        Material.background: Theme.surface
        Material.foreground: Theme.text
        Material.accent: Theme.accent

        Overlay.modal: Rectangle {
            color: Theme.dim
        }

        footer: DialogButtonBox {
            Button {
                text: "OK"
                flat: true
                DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
            }
        }

        RowLayout {
            anchors.fill: parent
            spacing: 14

            Icon {
                name: "error"
                color: Theme.error
                size: 28
                Layout.alignment: Qt.AlignTop
            }

            Label {
                id: errorText

                color: Theme.text
                font.pixelSize: Theme.bodySize
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
        }
    }

    Dialog {
        id: quitDialog

        anchors.centerIn: Overlay.overlay
        width: Math.min(window.width - 48, 440)
        modal: true
        title: "Conversion en cours"

        Material.background: Theme.surface
        Material.foreground: Theme.text
        Material.accent: Theme.accent

        Overlay.modal: Rectangle {
            color: Theme.dim
        }

        footer: DialogButtonBox {
            Button {
                text: "Quitter"
                flat: true
                DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
            }

            Button {
                text: "Attendre"
                flat: true
                DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
            }
        }

        Label {
            anchors.fill: parent
            text: "Une conversion est encore en cours. Attendre la fin ?"
            color: Theme.text
            font.pixelSize: Theme.bodySize
            wrapMode: Text.Wrap
        }

        // "Quitter": stop waiting, and leave.
        onRejected: {
            Backend.cancel()
            window.leaving = true
            Qt.quit()
        }
    }

    AboutDialog {
        id: aboutDialog

        onShowText: (title, text) => {
            textDialog.title = title
            textDialog.text = text
            textDialog.open()
        }
    }

    TextDialog {
        id: textDialog
    }

    Connections {
        target: Backend

        function onShowPage(page) { pages.currentIndex = page }
        function onNotify(message) { snackbar.show(message) }

        function onFailed(title, message) {
            errorDialog.title = title
            errorDialog.text = message
            errorDialog.open()
        }
    }

    // The back key goes back to the first page before it leaves, and never
    // leaves a conversion behind without asking.
    property bool leaving: false

    onClosing: (close) => {
        if (leaving)
            return
        if (android && pages.currentIndex !== 0) {
            close.accepted = false
            pages.currentIndex = 0
        } else if (Backend.busy) {
            close.accepted = false
            quitDialog.open()
        }
    }

    Component.onCompleted: Backend.checkIncoming()
}
