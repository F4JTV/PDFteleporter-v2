"""Application style and palette.

The Fusion style is forced rather than left to the platform. Fusion renders
identically on every Windows version and under Wine, which matters for a tool
that is deployed to whatever machines an exercise happens to provide: the
native Windows 11 style, the Windows 10 style and the legacy style differ
enough in metrics that a layout verified on one can crowd or clip on another.

Forcing a style does not mean ignoring the operator's system settings. Fusion
draws from the application palette, so the light or dark palette is selected
from the system colour scheme and reapplied if that scheme changes while the
application is running.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

STYLE_NAME = "Fusion"

# Tuned for contrast against a dark and a light window background respectively.
_DARK_LOG = {
    "info": "#9aa4b5",
    "success": "#6bbf73",
    "warning": "#d6a44c",
    "error": "#e07070",
}

_LIGHT_LOG = {
    "info": "#5c6470",
    "success": "#1e7a2e",
    "warning": "#8a5a00",
    "error": "#b02020",
}


def _dark_palette() -> QPalette:
    """A dark palette in Fusion's own idiom.

    Qt derives a dark Fusion palette on its own from Qt 6.5 onwards, but only
    where the platform plugin reports a colour scheme. Building it here keeps
    the appearance identical wherever the application runs, instead of leaving
    it to a capability that may or may not be present.
    """
    window = QColor(0x1E, 0x21, 0x26)
    base = QColor(0x17, 0x19, 0x1D)
    alt_base = QColor(0x25, 0x29, 0x30)
    text = QColor(0xE2, 0xE5, 0xEA)
    disabled = QColor(0x78, 0x80, 0x8C)
    accent = QColor(0x2E, 0x84, 0xCC)

    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, window)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, base)
    p.setColor(QPalette.ColorRole.AlternateBase, alt_base)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.Button, alt_base)
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.BrightText, QColor(0xFF, 0x6B, 0x6B))
    p.setColor(QPalette.ColorRole.ToolTipBase, alt_base)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.Highlight, accent)
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(0xFF, 0xFF, 0xFF))
    p.setColor(QPalette.ColorRole.Link, accent)
    p.setColor(QPalette.ColorRole.PlaceholderText, disabled)

    # Without explicit disabled entries Fusion greys by blending against a
    # light background, which on a dark window produces text brighter than the
    # enabled state.
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight,
               QColor(0x3A, 0x40, 0x49))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.HighlightedText,
               disabled)
    return p


def _system_prefers_dark(app: QApplication) -> bool:
    """Read the system colour scheme, falling back to light when unknown.

    The offscreen and minimal platform plugins report Unknown; so does X11
    without a desktop portal. Light is the safer default because a light
    palette on a dark desktop is merely bright, whereas the reverse can be
    unreadable if the platform actually meant light.
    """
    try:
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except (AttributeError, TypeError):  # pragma: no cover - Qt below 6.5
        return False


def apply_style(app: QApplication) -> None:
    """Force Fusion and install the palette matching the system scheme."""
    app.setStyle(STYLE_NAME)
    _apply_palette(app)

    try:
        app.styleHints().colorSchemeChanged.connect(
            lambda _scheme: _apply_palette(app)
        )
    except AttributeError:  # pragma: no cover - Qt below 6.5
        pass


def _apply_palette(app: QApplication) -> None:
    if _system_prefers_dark(app):
        app.setPalette(_dark_palette())
    else:
        # Fusion's built-in light palette, rather than a hand-rolled one.
        app.setPalette(app.style().standardPalette())


def is_dark_theme() -> bool:
    """Report whether the active palette is a dark one."""
    app = QApplication.instance()
    if app is None:
        return False
    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128


def log_colors() -> dict[str, str]:
    """Return the severity palette matching the current theme."""
    return _DARK_LOG if is_dark_theme() else _LIGHT_LOG


def dim_color() -> str:
    """A muted colour for secondary text such as timestamps and estimates."""
    return log_colors()["info"]
