"""Main window.

Layout mirrors the operator's workflow rather than the code structure:
compression on the left, rebuild on the right, and a timestamped log across
the bottom that can be read back during an exercise debrief.
"""

from __future__ import annotations

import html
import os
from datetime import datetime

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import (
    QAction, QDesktopServices, QFontDatabase, QIcon, QPalette,
)
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (
    QComboBox, QCheckBox, QFileDialog, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QPlainTextEdit, QSplitter, QVBoxLayout, QWidget,
)

from .. import engine, format as psdi
from ..resources import icon_path
from ..presets import (
    MODE_PACKET_1200, MODE_VARA_FM_NARROW, MODE_VARA_HF, QUALITY_LABELS,
    QUALITY_ORDER, QUALITY_PRESETS, WINLINK_MAX_ATTACHMENT, format_bytes,
    format_duration, format_percent,
)
from .workers import CompressWorker, RebuildWorker
from .theme import dim_color, log_colors


# The engine reports why it switched modes as a stable identifier, not as
# prose, so the log can be translated without the engine knowing about it.
def _decimal_seconds(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


_REASONS = {
    "rotation": "page pivotée",
    "scan": "document scanné",
}


def _dim(label: QLabel) -> None:
    """Render a label as secondary text using the palette's disabled role.

    Taking the colour from the palette rather than naming one keeps the label
    legible whichever theme Windows is currently in.
    """
    palette = label.palette()
    palette.setColor(
        QPalette.ColorRole.WindowText,
        palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText),
    )
    label.setPalette(palette)


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PDF Teleporter")
        self.resize(1100, 720)

        # Also set per-window: a window created before the application icon is
        # installed, or shown on a platform that does not inherit it, would
        # otherwise carry the generic placeholder in its title bar.
        icon = icon_path()
        if icon:
            self.setWindowIcon(QIcon(icon))

        self._worker: QThread | None = None
        self._pdf_path: str | None = None
        self._psdi_path: str | None = None
        self._last_archive: str | None = None
        self._last_pdf: str | None = None

        self._build_ui()
        self._build_menu()
        self.log("Prêt. Sélectionnez un PDF à compresser ou une archive .psdi à recomposer.", "info")

    # ---------------------------------------------------------------- UI ---
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        outer.addWidget(self._build_header())

        panels = QWidget()
        row = QHBoxLayout(panels)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(self._build_compress_panel(), 1)
        row.addWidget(self._build_rebuild_panel(), 1)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(panels)
        splitter.addWidget(self._build_log_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        outer.addWidget(splitter, 1)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p%  %v")
        self.progress.hide()
        outer.addWidget(self.progress)

    def _build_header(self) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(14)

        title = QLabel("PDF Teleporter")
        font = title.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        subtitle = QLabel("Transport de documents PDF par radio en bande étroite")
        _dim(subtitle)
        layout.addWidget(subtitle)
        layout.addStretch(1)
        return frame

    def _build_compress_panel(self) -> QWidget:
        box = QGroupBox("Compresser  ·  PDF → .psdi")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)

        grid.addWidget(QLabel("PDF source"), 0, 0)
        self.pdf_edit = QLineEdit()
        self.pdf_edit.setReadOnly(True)
        self.pdf_edit.setPlaceholderText("Aucun fichier sélectionné")
        grid.addWidget(self.pdf_edit, 0, 1)
        browse = QPushButton("Parcourir…")
        browse.clicked.connect(self.browse_pdf)
        grid.addWidget(browse, 0, 2)

        grid.addWidget(QLabel("Qualité"), 1, 0)
        self.quality_combo = QComboBox()
        for key in QUALITY_ORDER:
            self.quality_combo.addItem(
                f"{QUALITY_LABELS[key]}  —  {QUALITY_PRESETS[key]['description']}",
                key,
            )
        self.quality_combo.setCurrentIndex(QUALITY_ORDER.index("medium"))
        self.quality_combo.currentIndexChanged.connect(self.update_estimates)
        grid.addWidget(self.quality_combo, 1, 1, 1, 2)

        self.skip_images = QCheckBox("Texte seul (supprimer toutes les images)")
        self.skip_images.stateChanged.connect(self.update_estimates)
        grid.addWidget(self.skip_images, 2, 1, 1, 2)

        self.estimate_label = QLabel("—")
        _dim(self.estimate_label)
        self.estimate_label.setWordWrap(True)
        grid.addWidget(self.estimate_label, 3, 0, 1, 3)

        buttons = QHBoxLayout()
        self.compress_btn = QPushButton("Compresser")
        self.compress_btn.setDefault(True)
        self.compress_btn.clicked.connect(self.do_compress)
        self.compress_btn.setEnabled(False)
        buttons.addWidget(self.compress_btn)

        self.winlink_btn = QPushButton("Préparer pour Winlink")
        self.winlink_btn.clicked.connect(self.do_winlink)
        self.winlink_btn.setEnabled(False)
        buttons.addWidget(self.winlink_btn)
        buttons.addStretch(1)
        grid.addLayout(buttons, 4, 0, 1, 3)

        grid.setRowStretch(5, 1)
        return box

    def _build_rebuild_panel(self) -> QWidget:
        box = QGroupBox("Recomposer  ·  .psdi → PDF")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)

        grid.addWidget(QLabel("Archive"), 0, 0)
        self.psdi_edit = QLineEdit()
        self.psdi_edit.setReadOnly(True)
        self.psdi_edit.setPlaceholderText("Aucun fichier sélectionné")
        grid.addWidget(self.psdi_edit, 0, 1)
        browse = QPushButton("Parcourir…")
        browse.clicked.connect(self.browse_psdi)
        grid.addWidget(browse, 0, 2)

        self.validation_label = QLabel("—")
        self.validation_label.setWordWrap(True)
        grid.addWidget(self.validation_label, 1, 0, 1, 3)

        buttons = QHBoxLayout()
        self.rebuild_btn = QPushButton("Recomposer le PDF")
        self.rebuild_btn.clicked.connect(self.do_rebuild)
        self.rebuild_btn.setEnabled(False)
        buttons.addWidget(self.rebuild_btn)

        self.open_btn = QPushButton("Ouvrir le résultat")
        self.open_btn.clicked.connect(self.open_last_pdf)
        self.open_btn.setEnabled(False)
        buttons.addWidget(self.open_btn)
        buttons.addStretch(1)
        grid.addLayout(buttons, 2, 0, 1, 3)

        grid.setRowStretch(3, 1)
        return box

    def _build_log_panel(self) -> QWidget:
        box = QGroupBox("Journal d'exploitation")
        layout = QVBoxLayout(box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        layout.addWidget(self.log_view)
        return box

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&Fichier")
        for label, slot in (("Ouvrir un PDF…", self.browse_pdf),
                            ("Ouvrir une archive…", self.browse_psdi)):
            action = QAction(label, self)
            action.triggered.connect(slot)
            file_menu.addAction(action)
        file_menu.addSeparator()
        quit_action = QAction("Quitter", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        tools = self.menuBar().addMenu("&Outils")
        self.shell_action = QAction("Ajouter au menu contextuel de l'Explorateur", self)
        self.shell_action.triggered.connect(self.toggle_shell_integration)
        tools.addAction(self.shell_action)
        self._refresh_shell_action()

        help_menu = self.menuBar().addMenu("&Aide")
        about = QAction("À propos", self)
        about.triggered.connect(self.show_about)
        help_menu.addAction(about)

    # ------------------------------------------------------------- helpers --
    def log(self, message: str, level: str = "info") -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        colors = log_colors()
        color = colors.get(level, colors["info"])
        escaped = html.escape(message)
        self.log_view.appendHtml(
            f'<span style="color:{dim_color()}">[{stamp}]</span> '
            f'<span style="color:{color}">{escaped}</span>'
        )
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )

    def _set_busy(self, busy: bool) -> None:
        self.compress_btn.setEnabled(not busy and bool(self._pdf_path))
        self.rebuild_btn.setEnabled(not busy and bool(self._psdi_path))
        self.winlink_btn.setEnabled(not busy and bool(self._last_archive))
        self.progress.setVisible(busy)
        if not busy:
            self.progress.reset()

    # ------------------------------------------------------------ actions --
    def browse_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Sélectionner un PDF", "", "Documents PDF (*.pdf);;Tous les fichiers (*)"
        )
        if path:
            self.load_pdf(path)

    def load_pdf(self, path: str) -> None:
        self._pdf_path = path
        self.pdf_edit.setText(path)
        self.compress_btn.setEnabled(True)
        size = os.path.getsize(path)
        self.log(
            f"PDF sélectionné : {os.path.basename(path)} ({format_bytes(size)})",
            "info",
        )
        self.update_estimates()

    def browse_psdi(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Sélectionner une archive", "",
            "Archives PSDI (*.psdi);;Tous les fichiers (*)"
        )
        if path:
            self.load_psdi(path)

    def load_psdi(self, path: str) -> None:
        self._psdi_path = path
        self.psdi_edit.setText(path)

        # Validate on selection: an operator needs to know a file arrived
        # intact before spending time rebuilding it.
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            self.log(f"Lecture de l'archive impossible : {exc}", "error")
            self.rebuild_btn.setEnabled(False)
            return

        report = psdi.validate(data)
        if not report["valid"]:
            self.validation_label.setText(f"Archive invalide : {report['error']}")
            self.log(f"Archive invalide : {report['error']}", "error")
            self.rebuild_btn.setEnabled(False)
            return

        mode = "structuré" if report["version"] == 1 else "image de page"
        crc = "CRC correct" if report["checksum_ok"] else "CRC INCORRECT"
        self.validation_label.setText(
            f"Version {report['version']} ({mode}) · {report['pages']} page(s) · {crc}"
        )
        self.rebuild_btn.setEnabled(True)
        self.log(
            f"Archive chargée : {os.path.basename(path)} · {mode} · {crc}",
            "success" if report["checksum_ok"] else "warning",
        )

    def update_estimates(self) -> None:
        """Show a rough on-air time before anything is compressed.

        The ratio used here is a preset-derived guess, not a measurement; the
        real figure replaces it as soon as compression finishes.
        """
        if not self._pdf_path:
            self.estimate_label.setText("—")
            return

        quality = self.quality_combo.currentData()
        rough_ratio = {"ultra_low": 0.05, "low": 0.10,
                       "medium": 0.20, "high": 0.25}[quality]
        if self.skip_images.isChecked():
            rough_ratio *= 0.4

        estimated = int(os.path.getsize(self._pdf_path) * rough_ratio)
        from ..presets import estimate_times

        times = estimate_times(estimated)
        size_text = format_bytes(estimated)
        self.estimate_label.setText(
            f"Archive estimée ≈ {size_text}   ·   "
            f"Packet 1200 {format_duration(times[MODE_PACKET_1200])}   ·   "
            f"VARA HF {format_duration(times[MODE_VARA_HF])}   ·   "
            f"VARA FM {format_duration(times[MODE_VARA_FM_NARROW])}"
        )

    def do_compress(self) -> None:
        if not self._pdf_path:
            return
        default = os.path.splitext(self._pdf_path)[0] + ".psdi"
        path, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer l'archive", default, "Archives PSDI (*.psdi)"
        )
        if not path:
            return

        self._set_busy(True)
        self.log(f"Compression en qualité « {self.quality_combo.currentData()} »…",
                 "info")

        self._worker = CompressWorker(
            self._pdf_path, self.quality_combo.currentData(), path,
            self.skip_images.isChecked(),
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_compress_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def do_rebuild(self) -> None:
        if not self._psdi_path:
            return
        default = os.path.splitext(self._psdi_path)[0] + "_rebuilt.pdf"
        path, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer le PDF", default, "Documents PDF (*.pdf)"
        )
        if not path:
            return

        self._set_busy(True)
        self.log("Recomposition du PDF…", "info")

        self._worker = RebuildWorker(self._psdi_path, path)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_rebuild_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, step: int, total: int, message: str) -> None:
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(step)
        self.progress.setFormat(f"%p%  {message}")

    def _on_compress_done(self, path: str, info: dict) -> None:
        self._last_archive = path
        self._set_busy(False)

        if info.get("auto_mode_switch"):
            self.log(
                f"Bascule automatique en mode image de page "
                f"({_REASONS.get(info['auto_mode_switch'], info['auto_mode_switch'])}) "
                f"— le document est scanné ou pivoté.",
                "warning",
            )

        times = info["estimates"]
        self.log(
            f"Archive écrite : {format_bytes(info['archive_size'])} "
            f"({format_percent(info['ratio_percent'])} de l'original) en "
            f"{_decimal_seconds(info['compression_time'])} s",
            "success",
        )
        self.log(
            f"Sur l'air — Packet 1200 : {format_duration(times[MODE_PACKET_1200])} · "
            f"VARA HF: {format_duration(times[MODE_VARA_HF])} · "
            f"VARA FM: {format_duration(times[MODE_VARA_FM_NARROW])}",
            "info",
        )
        if info["archive_size"] > WINLINK_MAX_ATTACHMENT:
            self.log(
                f"L'archive dépasse la limite de pièce jointe Winlink de "
                f"{WINLINK_MAX_ATTACHMENT // 1024} ko — choisissez une qualité "
                f"inférieure.",
                "warning",
            )

    def _on_rebuild_done(self, path: str, info: dict) -> None:
        self._last_pdf = path
        self._set_busy(False)
        self.open_btn.setEnabled(True)
        if not info.get("crc_ok", True):
            self.log("CRC du manifeste incorrect — le PDF recomposé peut être "
                     "endommagé.", "warning")
        self.log(
            f"PDF recomposé : {info['pages']} page(s), "
            f"{format_bytes(info['pdf_size'])}",
            "success",
        )

    def _on_failed(self, message: str) -> None:
        self._set_busy(False)
        self.log(message, "error")
        QMessageBox.critical(self, "Échec de l'opération", message)

    def do_winlink(self) -> None:
        if not self._last_archive:
            return
        target_dir = os.path.join(
            os.path.expanduser("~"), "Documents", "PDFteleporter"
        )
        os.makedirs(target_dir, exist_ok=True)
        target = os.path.join(target_dir, os.path.basename(self._last_archive))

        try:
            import shutil

            shutil.copy2(self._last_archive, target)
        except OSError as exc:
            self.log(f"Échec de la copie : {exc}", "error")
            return

        size = os.path.getsize(target)
        warning = ""
        if size > WINLINK_MAX_ATTACHMENT:
            warning = (
                f"\n\nAttention : {format_bytes(size)} dépasse la limite de "
                f"pièce jointe Winlink de {WINLINK_MAX_ATTACHMENT // 1024} ko. "
                f"Recompressez à une qualité inférieure."
            )

        QMessageBox.information(
            self, "Préparé pour Winlink",
            f"Archive copiée dans :\n{target}\n\n"
            f"Dans Winlink Express :\n"
            f"  1. New Message\n"
            f"  2. Attachments → Add → sélectionner ce fichier\n"
            f"  3. Post to Outbox\n"
            f"  4. Ouvrir une session (VARA HF / VARA FM / Packet / Telnet)"
            f"{warning}",
        )
        self.log(f"Préparé pour Winlink : {target}", "success")

    def open_last_pdf(self) -> None:
        if self._last_pdf:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._last_pdf))

    # ------------------------------------------------------ integration ----
    def _refresh_shell_action(self) -> None:
        from .. import shell_windows

        if not shell_windows.is_supported():
            self.shell_action.setEnabled(False)
            self.shell_action.setText("Intégration Explorateur (Windows uniquement)")
            return
        installed = shell_windows.is_installed()
        self.shell_action.setText(
            "Retirer du menu contextuel de l'Explorateur" if installed
            else "Ajouter au menu contextuel de l'Explorateur"
        )

    def toggle_shell_integration(self) -> None:
        from .. import shell_windows

        try:
            scopes = shell_windows.installed_scopes()
            if scopes:
                # The application runs unelevated, so it can only touch the
                # per-user keys. Say so rather than reporting a removal that
                # did not happen.
                shell_windows.uninstall(shell_windows.SCOPE_USER)
                if shell_windows.SCOPE_MACHINE in scopes:
                    self.log(
                        "Entrées utilisateur retirées. Cette machine porte aussi un "
                        "enregistrement système, installé pour tous les "
                        "utilisateurs ; retirez-le en désinstallant "
                        "l'application.",
                        "warning",
                    )
                else:
                    self.log("Menu contextuel retiré.", "success")
            else:
                shell_windows.install(shell_windows.SCOPE_USER)
                self.log(
                    "Menu contextuel ajouté. Sous Windows 11, les entrées "
                    "apparaissent sous « Afficher plus d'options ».",
                    "success",
                )
        except Exception as exc:  # noqa: BLE001
            self.log(f"Échec de la mise à jour du registre : {exc}", "error")
            QMessageBox.critical(self, "Échec de l'intégration", str(exc))
        self._refresh_shell_action()

    def closeEvent(self, event) -> None:
        """Do not tear the window down while a conversion is in flight.

        Destroying a running QThread aborts the process, which during an
        exercise looks like a crash and loses the log.
        """
        if self._worker is not None and self._worker.isRunning():
            answer = QMessageBox.question(
                self, "Conversion en cours",
                "Une conversion est encore en cours. Attendre la fin ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._worker.wait(5000)
        event.accept()

    def show_about(self) -> None:
        QMessageBox.about(
            self, "À propos de PDF Teleporter",
            "<b>PDF Teleporter</b><br><br>"
            "Compresse des documents PDF en archives .psdi pour les "
            "transmettre sur des liaisons radio à bande étroite, et les "
            "recompose à la réception.<br><br>"
            "Les archives sont interopérables avec les autres "
            "implémentations du format .psdi.<br><br>"
            "Distribué sous licence GNU GPL v3.",
        )
