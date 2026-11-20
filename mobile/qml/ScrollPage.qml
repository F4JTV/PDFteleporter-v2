// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PdfTeleporter

// A page that scrolls vertically: its content is a column, the width of the
// screen on a phone, a column in the middle on a wider one.
Flickable {
    id: page

    default property alias content: column.data
    property bool revealPending: false

    // Brings the end of the page into view, where a result card has just
    // appeared. The column is laid out after the change that calls for it:
    // the scroll waits for its new height.
    function revealEnd() {
        revealPending = true
        revealTimer.restart()
    }

    contentWidth: width
    contentHeight: column.implicitHeight + 2 * Theme.gutter
    clip: true
    flickableDirection: Flickable.VerticalFlick
    boundsBehavior: Flickable.StopAtBounds

    onContentHeightChanged: if (revealPending) revealTimer.restart()

    ScrollIndicator.vertical: ScrollIndicator {}

    Timer {
        id: revealTimer

        interval: 80
        onTriggered: {
            page.revealPending = false
            revealAnimation.to = Math.max(0, page.contentHeight - page.height)
            revealAnimation.restart()
        }
    }

    NumberAnimation {
        id: revealAnimation

        target: page
        property: "contentY"
        duration: 300
        easing.type: Easing.OutCubic
    }

    ColumnLayout {
        id: column

        width: Math.min(page.width - 2 * Theme.gutter, Theme.maxContentWidth)
        x: (page.width - width) / 2
        y: Theme.gutter
        spacing: Theme.gutter
    }
}
