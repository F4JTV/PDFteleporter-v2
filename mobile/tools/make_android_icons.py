#!/usr/bin/env python3
# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Write the Android launcher icons from the application's icon (Pillow).

    python3 tools/make_android_icons.py

An adaptive icon (Android 8 and later): the drawing on a white layer, kept
inside the circle every launcher mask leaves visible, and a one-colour
version for the themed icons of Android 13, where the light page of the
drawing gives way and its outlines stay. The plain icon is kept for the
places that show it as it is.
"""

from __future__ import annotations

import math
import pathlib
import sys

from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent.parent
SOURCE = HERE.parent / "application/assets/pdfteleporter_256.png"
RES = HERE / "android/res"

DENSITIES = {"mdpi": 1.0, "hdpi": 1.5, "xhdpi": 2.0, "xxhdpi": 3.0, "xxxhdpi": 4.0}
LAYER_DP = 108       # an adaptive icon layer
SAFE_RADIUS_DP = 33  # the circle no mask cuts into
LEGACY_DP = 48


def trimmed(image: Image.Image) -> Image.Image:
    return image.crop(image.getbbox())


def fit_radius(image: Image.Image) -> float:
    """The distance from the centre to the farthest drawn pixel."""
    alpha = image.getchannel("A")
    w, h = image.size
    cx, cy = w / 2, h / 2
    far = 0.0
    pixels = alpha.load()
    for y in range(h):
        for x in range(w):
            if pixels[x, y] > 16:
                far = max(far, math.hypot(x + 0.5 - cx, y + 0.5 - cy))
    return far


def monochrome(image: Image.Image) -> Image.Image:
    """White where the drawing is dark or coloured, clear where it is pale."""
    rgba = image.convert("RGBA")
    out = Image.new("RGBA", rgba.size, (255, 255, 255, 0))
    src = rgba.load()
    dst = out.load()
    for y in range(rgba.size[1]):
        for x in range(rgba.size[0]):
            r, g, b, a = src[x, y]
            luminance = 0.299 * r + 0.587 * g + 0.114 * b
            keep = 1.0 if luminance < 200 else max(0.0, (230 - luminance) / 30)
            dst[x, y] = (255, 255, 255, int(a * keep))
    return out


def main() -> int:
    icon = trimmed(Image.open(SOURCE).convert("RGBA"))
    # Centred on its own box, then scaled so that its farthest pixel lies
    # on the safe circle.
    side = max(icon.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(icon, ((side - icon.width) // 2, (side - icon.height) // 2))
    radius = fit_radius(square)
    mono = monochrome(square)

    for name, factor in DENSITIES.items():
        folder = RES / f"mipmap-{name}"
        folder.mkdir(parents=True, exist_ok=True)
        layer = round(LAYER_DP * factor)
        size = round(side * (SAFE_RADIUS_DP * factor) / radius)
        offset = ((layer - size) // 2, (layer - size) // 2)
        for picture, file in ((square, "ic_launcher_foreground.png"), (mono, "ic_launcher_monochrome.png")):
            canvas = Image.new("RGBA", (layer, layer), (0, 0, 0, 0))
            canvas.alpha_composite(picture.resize((size, size), Image.LANCZOS), offset)
            canvas.save(folder / file, optimize=True)
        legacy = round(LEGACY_DP * factor)
        inner = round(legacy * 0.9)
        canvas = Image.new("RGBA", (legacy, legacy), (0, 0, 0, 0))
        canvas.alpha_composite(square.resize((inner, inner), Image.LANCZOS),
                               ((legacy - inner) // 2, (legacy - inner) // 2))
        canvas.save(folder / "ic_launcher.png", optimize=True)
        print(folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
