"""Application bootstrap."""

from __future__ import annotations

import logging
import os
import sys

from PyQt6.QtCore import QLibraryInfo, QLocale, QTranslator
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from ..resources import icon_path
from .main_window import MainWindow
from .theme import apply_style


def _enable_dpi_awareness() -> None:
    """Opt into per-monitor DPI scaling on Windows.

    Qt6 scales correctly on its own once the process is marked DPI aware.
    Without this the window is bitmap-stretched on a 4K laptop panel and every
    label looks blurred. Windows 11 (build 22000+) supports per-monitor v2;
    older releases fall back through the two earlier APIs.
    """
    if os.name != "nt":
        return

    import ctypes

    for attempt in (
        lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2),
        lambda: ctypes.windll.shcore.SetProcessDpiAwareness(1),
        lambda: ctypes.windll.user32.SetProcessDPIAware(),
    ):
        try:
            attempt()
            return
        except Exception:  # noqa: BLE001
            continue


def _install_translations(app: QApplication) -> QTranslator | None:
    """Load Qt's own French strings.

    The interface text is written in French, but the buttons and dialogs Qt
    supplies -- Open, Cancel, Save, the whole file chooser -- come from Qt's
    catalogues. Without this the window reads in French while every dialog it
    opens is in English.

    The translator must be kept alive: Qt holds only a borrowed reference, and
    letting it fall out of scope silently reverts everything to English.
    """
    translator = QTranslator()
    path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if translator.load(QLocale("fr"), "qtbase", "_", path):
        app.installTranslator(translator)
        return translator

    logging.getLogger(__name__).debug(
        "French Qt catalogue not found in %s; dialogs will be in English", path
    )
    return None


def run(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    _enable_dpi_awareness()

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("PDF Teleporter")

    # Held on the application object so the translator outlives this function.
    app._translator = _install_translations(app)

    icon = icon_path()
    if icon:
        # Set on the application as well as the window: the taskbar button and
        # the Alt-Tab entry read the application icon, not the window's.
        app.setWindowIcon(QIcon(icon))

    # Fusion, forced, with the palette taken from the system colour scheme.
    # No stylesheet: the widgets keep Fusion's own metrics and drawing.
    apply_style(app)

    window = MainWindow()
    window.show()
    return app.exec()
