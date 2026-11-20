#!/usr/bin/env python3
# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Render the phone interface with the Qt of a PySide6 wheel, and an engine
stand-in: a check of the QML on the Qt version the APK is built with (6.12)
without the Android toolchain, and pictures of each screen.

    pip install PySide6-Essentials==6.12.0
    QT_QPA_PLATFORM=offscreen python3 tests/ui_preview.py out/

The stand-in has the properties, signals and methods of app/backend.h, and
the figures of a real run; the QML is loaded from qml/ as it is. Any QML
warning fails the run.
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile

from PySide6.QtCore import (Property, Q_ARG, QMetaObject, QObject, QTimer, QUrl, Signal, Slot,
                            QtMsgType, qInstallMessageHandler)
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance
from PySide6.QtQuickControls2 import QQuickStyle

HERE = pathlib.Path(__file__).resolve().parent.parent
QML = HERE / "qml"


def make_log_model():
    """Enough of LogModel for the page: a list model with three roles."""
    from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt

    class LogModel(QAbstractListModel):
        countChanged = Signal()
        ROLES = {Qt.UserRole + 1: b"time", Qt.UserRole + 2: b"message", Qt.UserRole + 3: b"level"}

        def __init__(self):
            super().__init__()
            self.rows: list[tuple[str, str, str]] = []

        def rowCount(self, parent=QModelIndex()):
            return 0 if parent.isValid() else len(self.rows)

        def data(self, index, role):
            if not index.isValid():
                return None
            row = self.rows[index.row()]
            names = {Qt.UserRole + 1: 0, Qt.UserRole + 2: 1, Qt.UserRole + 3: 2}
            return row[names[role]] if role in names else None

        def roleNames(self):
            return self.ROLES

        def append(self, time, message, level):
            n = len(self.rows)
            self.beginInsertRows(QModelIndex(), n, n)
            self.rows.append((time, message, level))
            self.endInsertRows()
            self.countChanged.emit()

        @Slot()
        def clear(self):
            self.beginResetModel()
            self.rows.clear()
            self.endResetModel()
            self.countChanged.emit()

        def _count(self):
            return len(self.rows)

        count = Property(int, _count, notify=countChanged)

    return LogModel()


def prop(kind, name, signal, signal_name, writable=False):
    """A Qt property over the attribute "_<name>" of the stand-in."""
    def getter(self):
        return getattr(self, "_" + name)

    def setter(self, value):
        if getattr(self, "_" + name) != value:
            setattr(self, "_" + name, value)
            getattr(self, signal_name).emit()

    return Property(kind, getter, setter if writable else None, notify=signal)


class Backend(QObject):
    themeChanged = Signal()
    settingsChanged = Signal()
    sourceChanged = Signal()
    estimateChanged = Signal()
    archiveChanged = Signal()
    receivedChanged = Signal()
    pdfChanged = Signal()
    busyChanged = Signal()
    progressChanged = Signal()
    showPage = Signal(int)
    notify = Signal(str)
    failed = Signal(str, str)

    def __init__(self):
        super().__init__()
        self._log = make_log_model()
        self._theme = "light"
        self._qualityIndex = 2
        self._textOnly = False
        self._compatible = True
        self._hasSource = False
        self._sourceName = ""
        self._sourceInfo = ""
        self._sourceIsImage = False
        self._estimateState = "none"
        self._estimateSize = ""
        self._estimateTimes = []
        self._estimateOverLimit = False
        self._hasArchive = False
        self._archiveName = ""
        self._archiveInfo = ""
        self._archiveNote = ""
        self._hasReceived = False
        self._receivedValid = False
        self._receivedName = ""
        self._receivedInfo = ""
        self._receivedCrcOk = False
        self._hasPdf = False
        self._pdfName = ""
        self._pdfInfo = ""
        self._busy = False
        self._busyTitle = ""
        self._progress = 0.0
        self._progressText = ""
        self.say("Prêt. Sélectionnez un PDF ou une image à compresser, ou une archive .psdi ou .psdz "
                 "à recomposer.", "info")

    def say(self, message, level="info"):
        self._log.append("12:00:00", message, level)

    # --- constants
    def _version(self):
        return "2.6.0"

    version = Property(str, _version, constant=True)
    canShare = Property(bool, lambda self: True, constant=True)
    winlinkLimitKb = Property(int, lambda self: 120, constant=True)
    log = Property(QObject, lambda self: self._log, constant=True)
    qualities = Property("QVariantList", lambda self: [
        {"id": "ultra_low", "label": "Très basse", "description": "Urgence (Packet 1200 bauds)"},
        {"id": "low", "label": "Basse", "description": "Qualité réduite (Packet 9600 / VARA HF lent)"},
        {"id": "medium", "label": "Moyenne", "description": "Qualité moyenne (VARA HF / FM)"},
        {"id": "high", "label": "Haute", "description": "Haute qualité (VARA FM rapide)"},
    ], constant=True)

    # --- state
    theme = prop(str, "theme", themeChanged, "themeChanged", writable=True)
    qualityIndex = prop(int, "qualityIndex", settingsChanged, "settingsChanged", writable=True)
    textOnly = prop(bool, "textOnly", settingsChanged, "settingsChanged", writable=True)
    compatible = prop(bool, "compatible", settingsChanged, "settingsChanged", writable=True)
    hasSource = prop(bool, "hasSource", sourceChanged, "sourceChanged")
    sourceName = prop(str, "sourceName", sourceChanged, "sourceChanged")
    sourceInfo = prop(str, "sourceInfo", sourceChanged, "sourceChanged")
    sourceIsImage = prop(bool, "sourceIsImage", sourceChanged, "sourceChanged")
    estimateState = prop(str, "estimateState", estimateChanged, "estimateChanged")
    estimateSize = prop(str, "estimateSize", estimateChanged, "estimateChanged")
    estimateTimes = prop("QVariantList", "estimateTimes", estimateChanged, "estimateChanged")
    estimateOverLimit = prop(bool, "estimateOverLimit", estimateChanged, "estimateChanged")
    hasArchive = prop(bool, "hasArchive", archiveChanged, "archiveChanged")
    archiveName = prop(str, "archiveName", archiveChanged, "archiveChanged")
    archiveInfo = prop(str, "archiveInfo", archiveChanged, "archiveChanged")
    archiveNote = prop(str, "archiveNote", archiveChanged, "archiveChanged")
    hasReceived = prop(bool, "hasReceived", receivedChanged, "receivedChanged")
    receivedValid = prop(bool, "receivedValid", receivedChanged, "receivedChanged")
    receivedName = prop(str, "receivedName", receivedChanged, "receivedChanged")
    receivedInfo = prop(str, "receivedInfo", receivedChanged, "receivedChanged")
    receivedCrcOk = prop(bool, "receivedCrcOk", receivedChanged, "receivedChanged")
    hasPdf = prop(bool, "hasPdf", pdfChanged, "pdfChanged")
    pdfName = prop(str, "pdfName", pdfChanged, "pdfChanged")
    pdfInfo = prop(str, "pdfInfo", pdfChanged, "pdfChanged")
    busy = prop(bool, "busy", busyChanged, "busyChanged")
    busyTitle = prop(str, "busyTitle", busyChanged, "busyChanged")
    progress = prop(float, "progress", progressChanged, "progressChanged")
    progressText = prop(str, "progressText", progressChanged, "progressChanged")

    # --- methods the QML calls
    @Slot(QUrl)
    def openFile(self, url): pass

    @Slot(QUrl)
    def openSource(self, url): pass

    @Slot(QUrl)
    def openReceived(self, url): pass

    @Slot()
    def compress(self): pass

    @Slot()
    def rebuild(self): pass

    @Slot()
    def cancel(self): pass

    @Slot()
    def shareArchive(self): pass

    @Slot(QUrl)
    def saveArchiveTo(self, url): pass

    @Slot()
    def sharePdf(self): pass

    @Slot()
    def viewPdf(self): pass

    @Slot(QUrl)
    def savePdfTo(self, url): pass

    @Slot(str, result=QUrl)
    def suggestedSaveUrl(self, name):
        return QUrl.fromLocalFile("/" + name)

    @Slot()
    def copyLog(self): pass

    @Slot(str, result=str)
    def licenseText(self, which):
        path = HERE.parent / "LICENSE.txt" if which == "gpl" else HERE / "legal/THIRD_PARTY_NOTICES.txt"
        return path.read_text(encoding="utf-8")

    @Slot()
    def checkIncoming(self): pass

    # --- the states of a run, set directly
    def set(self, **values):
        for name, value in values.items():
            setattr(self, "_" + name, value)
        for sig in ("sourceChanged", "estimateChanged", "archiveChanged", "receivedChanged", "pdfChanged",
                    "busyChanged", "progressChanged", "settingsChanged", "themeChanged"):
            getattr(self, sig).emit()


def interface_of_backend_h() -> tuple[set[str], set[str], set[str]]:
    """The properties, invokable methods and signals app/backend.h declares."""
    import re
    text = (HERE / "app/backend.h").read_text(encoding="utf-8")
    props = set(re.findall(r"Q_PROPERTY\([^)]*?(\w+) READ", text))
    methods = set(re.findall(r"Q_INVOKABLE [^(]*?(\w+)\(", text))
    block = text[text.index("signals:"):text.index("private:")]
    signals = set(re.findall(r"void (\w+)\(", block))
    return props, methods, signals


def check_stand_in(backend: QObject) -> list[str]:
    """What the stand-in lacks of the real Backend: the QML would not be
    tested against the interface it really has."""
    meta = backend.metaObject()
    props = {meta.property(i).name() for i in range(meta.propertyCount())}
    methods = {bytes(meta.method(i).name()).decode() for i in range(meta.methodCount())}
    want_props, want_methods, want_signals = interface_of_backend_h()
    missing = sorted((want_props - props) | (want_methods - methods) | (want_signals - methods))
    return [f"stand-in lacks {name}" for name in missing]


def module_dir(tmp: pathlib.Path) -> pathlib.Path:
    """The QML files as the module qt_add_qml_module makes of them."""
    root = tmp / "PdfTeleporter"
    root.mkdir(parents=True)
    lines = ["module PdfTeleporter"]
    for f in sorted(QML.glob("*.qml")):
        shutil.copy(f, root / f.name)
        prefix = "singleton " if f.stem == "Theme" else ""
        lines.append(f"{prefix}{f.stem} 1.0 {f.name}")
    shutil.copy(QML / "IconPaths.js", root / "IconPaths.js")
    shutil.copytree(QML / "images", root / "images")
    (root / "qmldir").write_text("\n".join(lines) + "\n")
    return root


def main() -> int:
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "ui-preview")
    out.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []

    def handler(kind, context, message):
        if kind != QtMsgType.QtDebugMsg and "QQmlApplicationEngine" not in message:
            problems.append(message)
        print(message, file=sys.stderr)

    qInstallMessageHandler(handler)
    app = QGuiApplication(sys.argv[:1])
    QQuickStyle.setStyle("Material")
    backend = Backend()
    problems.extend(check_stand_in(backend))
    qmlRegisterSingletonInstance(Backend, "PdfTeleporter", 1, 0, "Backend", backend)

    tmp = pathlib.Path(tempfile.mkdtemp())
    root = module_dir(tmp)
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(tmp))
    engine.load(QUrl.fromLocalFile(str(root / "Main.qml")))
    if not engine.rootObjects():
        print("Main.qml did not load", file=sys.stderr)
        return 1
    window = engine.rootObjects()[0]

    def settle(ms=600):
        loop_end = [False]
        QTimer.singleShot(ms, lambda: loop_end.__setitem__(0, True))
        while not loop_end[0]:
            app.processEvents()

    def call(function, *args):
        QMetaObject.invokeMethod(window, function, *[Q_ARG("QVariant", a) for a in args])

    def shot(name):
        settle()
        window.grabWindow().save(str(out / f"{name}.png"))
        print(out / f"{name}.png")

    times = [{"label": "Packet 1200", "time": "16 min 20 s"}, {"label": "VARA HF", "time": "8 min 10 s"},
             {"label": "VARA FM", "time": "2 min 02 s"}]
    for theme in ("light", "dark", "red"):
        backend.set(theme=theme, hasSource=False, estimateState="none", hasArchive=False, hasReceived=False,
                    hasPdf=False, busy=False)
        backend.showPage.emit(0)
        shot(f"{theme}-01-vide")
        backend.set(hasSource=True, sourceName="sitrep_urgence_oms_mali_mois_de_mai_2021.pdf",
                    sourceInfo="PDF · 611 ko", estimateState="ready", estimateSize="100 ko",
                    estimateTimes=times, estimateOverLimit=False)
        call("revealEnd", 0)
        shot(f"{theme}-02-estimation")
        backend.set(busy=True, busyTitle="Compression", progress=0.42, progressText="Texte et dessins (4/8)…")
        shot(f"{theme}-03-progression")
        backend.set(busy=False, hasArchive=True, archiveName="sitrep_urgence_oms_mali_mois_de_mai_2021.psdz",
                    archiveInfo="64,4 ko · 10,5 % de l'original · 1,3 s",
                    archiveNote="Archive compacte .psdz : la station qui la reçoit doit utiliser PDF Teleporter "
                                "2.5.0 ou plus ; la version F1GBD ne l'ouvre pas.",
                    estimateOverLimit=True, compatible=False)
        call("revealEnd", 0)
        shot(f"{theme}-04-archive")
        backend.set(hasReceived=True, receivedValid=True, receivedCrcOk=True,
                    receivedName="sitrep_urgence_oms_mali_mois_de_mai_2021.psdz",
                    receivedInfo="Version 3 (structuré compact .psdz) · 8 page(s) · CRC correct",
                    hasPdf=True, pdfName="sitrep_urgence_oms_mali_mois_de_mai_2021_rebuilt.pdf",
                    pdfInfo="8 page(s) · 276 ko", compatible=True)
        backend.showPage.emit(1)
        shot(f"{theme}-05-recomposer")
        backend.say("Archive écrite : 64,4 ko (10,5 % de l'original) en 1,3 s", "success")
        backend.say("L'archive dépasse la limite de pièce jointe Winlink de 120 ko — choisissez une qualité "
                    "inférieure.", "warning")
        backend.say("Archive invalide : PSDI signature missing", "error")
        backend.showPage.emit(2)
        shot(f"{theme}-06-journal")
        call("showAbout")
        shot(f"{theme}-07-a-propos")
        call("closeDialogs")
        backend.failed.emit("Échec de la recomposition", "Archive invalide : PSDI signature missing")
        shot(f"{theme}-08-erreur")
        call("closeDialogs")

    engine.deleteLater()
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"{len(problems)} problem(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
