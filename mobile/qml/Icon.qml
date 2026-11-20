// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

import QtQuick
import QtQuick.Shapes
import PdfTeleporter

import "IconPaths.js" as IconPaths

// A Material icon drawn from its path, in any colour and at any size: no
// image files, and every icon follows the palette, the night one included.
Item {
    id: icon

    property string name
    property color color: Theme.text
    property real size: 24

    implicitWidth: size
    implicitHeight: size
    visible: name !== ""

    Shape {
        id: shape

        // Qt 6.6 and later draw curves smoothly by themselves; before, the
        // shape is antialiased by multisampling its layer.
        property bool curveRenderer: false

        anchors.fill: parent
        layer.enabled: !curveRenderer
        layer.samples: 4
        layer.smooth: true

        Component.onCompleted: {
            if (shape.preferredRendererType !== undefined && Shape.CurveRenderer !== undefined) {
                shape.preferredRendererType = Shape.CurveRenderer
                curveRenderer = true
            }
        }

        ShapePath {
            fillColor: icon.color
            fillRule: ShapePath.WindingFill
            strokeWidth: -1
            strokeColor: "transparent"
            scale: Qt.size(icon.size / 24, icon.size / 24)

            PathSvg {
                path: IconPaths.paths[icon.name] || ""
            }
        }
    }
}
