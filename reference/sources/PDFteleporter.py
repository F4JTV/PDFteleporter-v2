# Source Generated with Decompyle++
# File: PDFteleporter.pyc (Python 3.13)

from __future__ import annotations
import os
import sys
import logging
import traceback
from datetime import datetime
VERSION = '2.1.0'
APP_NAME = 'PDF Teleporter'
ORG_NAME = 'ADRASEC 77 - F1GBD'
logging.basicConfig(level = logging.INFO, format = '%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('PDFteleporter')
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QUrl
from PyQt6.QtGui import QIcon, QDesktopServices, QGuiApplication, QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QDialog, QLabel, QPushButton, QLineEdit, QRadioButton, QButtonGroup, QGroupBox, QVBoxLayout, QHBoxLayout, QFrame, QTextEdit, QProgressBar, QFileDialog, QMessageBox, QScrollArea
PSDI_AVAILABLE = False
PSDI_ERROR = ''

try:
    import psdi_lib
    PSDI_AVAILABLE = True
    logger.info('psdi_lib v%s chargé — moteur PSDI disponible', getattr(psdi_lib, 'PSDI_LIB_VERSION', '?'))
    COLORS = { }['bg_dark']['bg_medium']['bg_light']['bg_input']['text_primary']['text_secondary']['text_dim']['accent_blue']['accent_cyan']['accent_green']['accent_yellow']['accent_orange']['accent_red']['success']['warning']['error']
    QSS = '\nQWidget {{\n    background-color: {bg_dark};\n    color: {text_primary};\n    font-family: "Segoe UI", Arial, sans-serif;\n    font-size: 10pt;\n}}\nQMainWindow, QDialog {{ background-color: {bg_dark}; }}\n\nQGroupBox {{\n    background-color: {bg_medium};\n    border: 1px solid {bg_light};\n    border-radius: 6px;\n    margin-top: 16px;\n    padding: 10px;\n    font-family: Consolas, "Courier New", monospace;\n    font-size: 11pt;\n    font-weight: bold;\n}}\nQGroupBox::title {{\n    subcontrol-origin: margin;\n    subcontrol-position: top center;\n    padding: 2px 12px;\n    background-color: {bg_dark};\n    color: {accent_cyan};\n}}\nQGroupBox#panelDecompress::title {{ color: {accent_green}; }}\nQGroupBox#panelLog {{\n    font-size: 9pt;\n    font-weight: normal;\n}}\nQGroupBox#panelLog::title {{ color: {text_dim}; subcontrol-position: top left; }}\n\nQLabel {{ background: transparent; }}\nQLabel#h1 {{\n    font-family: Consolas, "Courier New", monospace;\n    font-size: 18pt; font-weight: bold; color: {accent_orange};\n}}\nQLabel#version   {{ color: {text_dim}; font-family: Consolas, monospace; }}\nQLabel#sectionTitle {{ color: {accent_orange}; font-weight: bold; }}\nQLabel#fileInfo  {{ color: {text_dim}; font-family: Consolas, monospace; font-size: 8pt; }}\nQLabel#hint      {{ color: {text_dim}; }}\nQLabel#footer    {{ color: {text_dim}; font-size: 8pt; }}\nQLabel#statusOk  {{ color: {success}; font-family: Consolas, monospace; font-size: 9pt; }}\nQLabel#statusKo  {{ color: {error};   font-family: Consolas, monospace; font-size: 9pt; }}\nQLabel#radioHint {{ color: {text_dim}; font-size: 8pt; }}\n\nQLineEdit {{\n    background-color: {bg_input};\n    color: {text_primary};\n    border: 1px solid {bg_light};\n    border-radius: 3px;\n    padding: 4px 6px;\n    font-family: Consolas, "Courier New", monospace;\n    font-size: 9pt;\n}}\nQLineEdit:read-only {{ color: {text_secondary}; }}\n\nQPushButton {{\n    background-color: {bg_light};\n    color: {text_primary};\n    border: 1px solid #2a3350;\n    border-radius: 4px;\n    padding: 5px 10px;\n}}\nQPushButton:hover  {{ background-color: #27304f; }}\nQPushButton:pressed{{ background-color: #161c30; }}\nQPushButton:disabled {{ color: {text_dim}; background-color: #12172a; border-color: #1b2138; }}\n\nQPushButton#browseBlue  {{ color: {accent_blue}; }}\nQPushButton#browseGreen {{ color: {accent_green}; }}\n\nQPushButton#primaryCyan, QPushButton#primaryGreen, QPushButton#primaryYellow {{\n    color: #000000;\n    font-family: Consolas, "Courier New", monospace;\n    font-weight: bold;\n    border: none;\n    border-radius: 5px;\n    padding: 10px;\n    font-size: 12pt;\n}}\nQPushButton#primaryCyan   {{ background-color: {accent_cyan}; }}\nQPushButton#primaryCyan:hover  {{ background-color: #62efff; }}\nQPushButton#primaryGreen  {{ background-color: {accent_green}; }}\nQPushButton#primaryGreen:hover {{ background-color: #66ffa6; }}\nQPushButton#primaryYellow {{ background-color: {accent_yellow}; font-size: 10pt; padding: 7px; }}\nQPushButton#primaryYellow:hover {{ background-color: #ffff74; }}\nQPushButton#primaryCyan:disabled, QPushButton#primaryGreen:disabled,\nQPushButton#primaryYellow:disabled {{ background-color: #263042; color: {text_dim}; }}\n\nQRadioButton {{ background: transparent; spacing: 6px; padding: 1px; }}\nQRadioButton::indicator {{ width: 13px; height: 13px; }}\nQRadioButton::indicator:unchecked {{\n    border: 2px solid {text_dim}; border-radius: 8px; background: {bg_dark};\n}}\nQRadioButton::indicator:checked {{\n    border: 2px solid {accent_cyan}; border-radius: 8px; background: {accent_cyan};\n}}\n\nQTextEdit {{\n    background-color: {bg_dark};\n    color: {text_secondary};\n    border: 1px solid {bg_light};\n    border-radius: 3px;\n    font-family: Consolas, "Courier New", monospace;\n    font-size: 9pt;\n}}\nQTextEdit#estimates {{ color: {accent_cyan}; border: 1px solid #202a45; }}\n\nQProgressBar {{\n    background-color: {bg_input};\n    border: 1px solid {bg_light};\n    border-radius: 3px;\n    text-align: center;\n    color: {text_secondary};\n    font-size: 8pt;\n    height: 14px;\n}}\nQProgressBar::chunk {{ background-color: {accent_blue}; border-radius: 2px; }}\n\nQScrollArea {{ background: transparent; border: none; }}\nQScrollArea > QWidget > QWidget {{ background: transparent; }}\n\nQScrollBar:vertical {{ background: {bg_dark}; width: 10px; margin: 0; }}\nQScrollBar::handle:vertical {{ background: {bg_light}; border-radius: 5px; min-height: 24px; }}\nQScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}\nQFrame#hline {{ background-color: {bg_light}; max-height: 1px; border: none; }}\nQFrame#accentLine {{ background-color: {accent_orange}; max-height: 2px; border: none; }}\n'.format(**COLORS)
    QUALITY_LABELS = {
        'ultra_low': ('⚡ Ultra Low', 'Urgence — Packet 1200 baud (~5% taille)'),
        'low': ('📡 Low', 'Packet 9600 / VARA HF lent (~10% taille)'),
        'medium': ('📻 Medium', 'VARA HF / FM standard (~20% taille)'),
        'high': ('🔊 High', 'VARA FM rapide (~25% taille, bonne qualité)'),
        'fast': ('🚀 SANS IMAGE', 'Texte seul — transfert ultra-rapide') }
    MODE_LABELS = {
        'struct': 'Structuré (texte + images)',
        'image': 'Rendu image (pages JPEG)' }
    
    def resource_path(name):
        base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, name)

    
    def app_icon():
        for candidate in ('PDteleporter_color.ico', 'PDFteleporter.ico', 'PDFteleporter_bw.ico'):
            path = resource_path(candidate)
            if not os.path.exists(path):
                continue
            
            return ('PDteleporter_color.ico', 'PDFteleporter.ico', 'PDFteleporter_bw.ico'), QIcon(path)
        return QIcon()

    
    def human_size(num):
        return f'''{num:,} octets ({num // 1024} Ko)'''.replace(',', ' ')

    
    def open_with_system(path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    
    class Worker(QThread):
        __firstlineno__ = 358
        progress = pyqtSignal(int, int, str)
        result = pyqtSignal(object)
        error = pyqtSignal(str)
        
        def __init__(self, job, parent = None):
            super().__init__(parent)
            self._job = job

        
        def run(self):
            
            try:
                self.result.emit(self._job(self._cb))
                return None
            except Exception:
                exc = None
                logger.error('Worker: %s\n%s', exc, traceback.format_exc())
                self.error.emit(str(exc))

            exc = None
            del exc
            return None
            exc = None
            del exc

        
        def _cb(self, step, total, message):
            
            try:
                if not step:
                    step
                if not total:
                    total
                self.progress.emit(int(0), int(0), str(message))
                return None


        __static_attributes__ = ('_job',)
        __classcell__ = __class__
        return __class__

    
    class LogWindow(QDialog):
        __firstlineno__ = 390
        
        def __init__(self, parent = None):
            super().__init__(parent)
            self.setWindowTitle('📋 Journal — PDF Teleporter')
            self.setWindowIcon(app_icon())
            self.setWindowFlags(Qt.WindowType.Window)
            self.setModal(False)
            self.resize(820, 380)
            lay = QVBoxLayout(self)
            lay.setContentsMargins(10, 10, 10, 10)
            lay.setSpacing(6)
            self.text = QTextEdit()
            self.text.setReadOnly(True)
            lay.addWidget(self.text, 1)
            row = QHBoxLayout()
            b_copy = QPushButton('📋 Copier tout')
            b_copy.clicked.connect(self._copy_all)
            b_clear = QPushButton('🗑 Effacer')
            b_clear.clicked.connect(self.text.clear)
            b_close = QPushButton('Fermer')
            b_close.clicked.connect(self.hide)
            row.addWidget(b_copy)
            row.addWidget(b_clear)
            row.addStretch(1)
            row.addWidget(b_close)
            lay.addLayout(row)

        
        def _copy_all(self):
            QGuiApplication.clipboard().setText(self.text.toPlainText())

        
        def append_html(self, html):
            self.text.append(html)
            bar = self.text.verticalScrollBar()
            bar.setValue(bar.maximum())

        
        def show_and_raise(self):
            self.show()
            self.raise_()
            self.activateWindow()

        __static_attributes__ = ('text',)
        __classcell__ = __class__
        return __class__

    
    class AboutDialog(QDialog):
        __firstlineno__ = 443
        
        def __init__(self, parent = None):
            super().__init__(parent)
            self.setWindowTitle('À propos — PDF Teleporter')
            self.setWindowIcon(app_icon())
            self.setFixedSize(540, 560)
            lay = QVBoxLayout(self)
            lay.setContentsMargins(24, 16, 24, 16)
            title = QLabel('📄  PDF Teleporter')
            title.setObjectName('h1')
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(title)
            sub = QLabel(f'''version {VERSION} — août 2026''')
            sub.setObjectName('version')
            sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(sub)
            line = QFrame()
            line.setObjectName('accentLine')
            line.setFixedHeight(2)
            lay.addWidget(line)
            lay.addSpacing(8)
            body = QLabel('Un outil de Téléportation de document PDF :\n\nCompression de documents PDF (SITREP, PSD, BSD, ORSEC,\nFICHES REFLEXES) pour de la transmission via les modes\nradio TNC Packet et VARA Modem (HF/FM/SAT) de TCQ ou\nWinlink Express.\n\n• Les données texte, tableaux et images sont extraites du\n  PDF, compressées individuellement et transmises dans une\n  archive PSDI minimale.\n\n• Le fichier PSDI est recomposé côté réception avec sa mise\n  en forme originale (tableaux, images, textes) sans aucune\n  opération manuelle spécifique.\n\n• Les archives .psdi sont interopérables dans les deux sens\n  avec le module PDF de TCQ (format inchangé).')
            body.setStyleSheet(f'''color: {COLORS['text_secondary']};''')
            lay.addWidget(body)
            lay.addStretch(1)
            engine = getattr(psdi_lib, 'PSDI_LIB_VERSION', '?') if PSDI_AVAILABLE else 'indisponible'
            fmt = getattr(psdi_lib, 'PDF_TRANS_VERSION', '?') if PSDI_AVAILABLE else '-'
            tech = QLabel(f'''Interface : PyQt6   •   Moteur : psdi_lib v{engine}\nFormat d\'archive PSDI : v{fmt}   •   Python {sys.version.split()[0]}''')
            tech.setObjectName('fileInfo')
            tech.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(tech)
            sep = QFrame()
            sep.setObjectName('hline')
            sep.setFixedHeight(1)
            lay.addWidget(sep)
            author = QLabel('Développé par F1GBD (Jean-Louis Naudin)\nADRASEC 77 — Sécurité Civile — FNRASEC')
            author.setStyleSheet(f'''color: {COLORS['accent_cyan']}; font-family: Consolas, monospace;''')
            author.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(author)
            btn = QPushButton('Fermer')
            btn.clicked.connect(self.accept)
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(btn)
            row.addStretch(1)
            lay.addLayout(row)

        __static_attributes__ = ()
        __classcell__ = __class__
        return __class__

    
    class WinlinkDialog(QDialog):
        __firstlineno__ = 511
        
        def __init__(self, psdi_path, parent = None):
            super().__init__(parent)
            self._path = psdi_path
            self.setWindowTitle('📧 Envoi via Winlink Express')
            self.setWindowIcon(app_icon())
            self.setMinimumSize(540, 560)
            size = os.path.getsize(psdi_path)
            lay = QVBoxLayout(self)
            lay.setContentsMargins(18, 14, 18, 14)
            title = QLabel('📧  Envoi via Winlink Express')
            title.setStyleSheet(f'''color: {COLORS['accent_yellow']}; font-family: Consolas, monospace; font-size: 13pt; font-weight: bold;''')
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(title)
            info = QLabel(f'''📎 {os.path.basename(psdi_path)}\n{human_size(size)}''')
            info.setObjectName('fileInfo')
            info.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(info)
            if size > 120000:
                warn = QLabel('⚠️ Taille > 120 Ko — peut dépasser la limite Winlink !\nUtilisez la qualité Ultra Low ou SANS IMAGE.')
                warn.setStyleSheet(f'''color: {COLORS['warning']}; font-weight: bold;''')
            else:
                warn = QLabel('✅ Taille OK pour Winlink (limite 120 Ko)')
                warn.setStyleSheet(f'''color: {COLORS['success']};''')
            warn.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(warn)
            line = QFrame()
            line.setObjectName('accentLine')
            line.setFixedHeight(2)
            lay.addWidget(line)
            steps = QTextEdit()
            steps.setReadOnly(True)
            steps.setPlainText(f'''📋  Procédure d\'envoi :\n\n1.  Ouvrez Winlink Express\n\n2.  Cliquez sur  Message → New Message...\n\n3.  Remplissez le destinataire (indicatif)\n\n4.  Sujet suggéré :  [PSDI] nom_du_document\n\n5.  Cliquez sur  Attachments → Add\n     Sélectionnez le fichier :\n     📂 {psdi_path}\n\n6.  Dans le corps du message, indiquez :\n     « Recomposer avec PDF Teleporter »\n\n7.  Cliquez  Post to Outbox\n\n8.  Envoyez via  VARA HF / FM / Packet / Telnet''')
            lay.addWidget(steps, 1)
            row = QHBoxLayout()
            b_folder = QPushButton('📂 Ouvrir le dossier')
            b_folder.setObjectName('primaryYellow')
            b_folder.clicked.connect((lambda : open_with_system(os.path.dirname(self._path))))
            b_copy = QPushButton('📋 Copier le chemin')
            b_copy.clicked.connect(self._copy_path)
            b_close = QPushButton('Fermer')
            b_close.clicked.connect(self.accept)
            row.addWidget(b_folder)
            row.addWidget(b_copy)
            row.addStretch(1)
            row.addWidget(b_close)
            lay.addLayout(row)

        
        def _copy_path(self):
            QGuiApplication.clipboard().setText(self._path)
            QMessageBox.information(self, 'Copié', f'''Chemin copié dans le presse-papiers :\n{self._path}''')

        __static_attributes__ = ('_path',)
        __classcell__ = __class__
        return __class__

    
    class PDFTeleporterWindow(QMainWindow):
        __firstlineno__ = 586
        
        def __init__(self):
            super().__init__()
            self.setWindowTitle(f'''📄 PDF Teleporter v{VERSION} — par F1GBD ADRASEC 77''')
            self.setWindowIcon(app_icon())
            self.setMinimumSize(880, 560)
            self.setAcceptDrops(True)
            self._busy = False
            self._worker = None
            self._est_worker = None
            self._est_token = 0
            self._last_psdi_path = None
            self._last_pdf_path = None
            self.log_window = LogWindow(self)
            self._build_ui()
            self._restore_geometry()
            QShortcut(QKeySequence('Ctrl+J'), self, activated = self._toggle_log)
            self._est_timer = QTimer(self)
            self._est_timer.setSingleShot(True)
            self._est_timer.setInterval(450)
            self._est_timer.timeout.connect(self._update_estimates)
            self._startup_banner()

        
        def _build_ui(self):
            central = QWidget()
            self.setCentralWidget(central)
            root = QVBoxLayout(central)
            root.setContentsMargins(10, 8, 10, 6)
            root.setSpacing(6)
            root.addLayout(self._build_header())
            panels = QWidget()
            center = QHBoxLayout(panels)
            center.setContentsMargins(0, 0, 0, 0)
            center.setSpacing(10)
            center.addWidget(self._build_compress_panel(), 1)
            center.addWidget(self._build_decompress_panel(), 1)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(panels)
            root.addWidget(scroll, 1)
            root.addLayout(self._build_footer())

        
        def _build_header(self):
            row = QHBoxLayout()
            title = QLabel('📄  PDF TELEPORTER')
            title.setObjectName('h1')
            row.addWidget(title)
            ver = QLabel(f'''v{VERSION}''')
            ver.setObjectName('version')
            row.addWidget(ver)
            self.btn_log = QPushButton('📋 JOURNAL')
            self.btn_log.setToolTip('Ouvrir le journal dans une fenêtre séparée (Ctrl+J)')
            self.btn_log.setStyleSheet(f'''color: {COLORS['accent_cyan']}; font-weight: bold;''')
            self.btn_log.clicked.connect(self._toggle_log)
            row.addWidget(self.btn_log)
            btn_about = QPushButton('ℹ️ À PROPOS')
            btn_about.setStyleSheet(f'''color: {COLORS['accent_yellow']}; font-weight: bold;''')
            btn_about.clicked.connect((lambda : AboutDialog(self).exec()))
            row.addWidget(btn_about)
            row.addStretch(1)
            if PSDI_AVAILABLE:
                st = QLabel(f'''✅ psdi_lib v{getattr(psdi_lib, 'PSDI_LIB_VERSION', '?')} chargé''')
                st.setObjectName('statusOk')
            else:
                st = QLabel('❌ psdi_lib manquant')
                st.setObjectName('statusKo')
            row.addWidget(st)
            return row

        
        def _build_compress_panel(self):
            box = QGroupBox('  📦  COMPACTER  (PDF → .psdi)  ')
            box.setObjectName('panelCompress')
            lay = QVBoxLayout(box)
            lay.setSpacing(5)
            lay.addWidget(QLabel('Fichier PDF :'))
            row = QHBoxLayout()
            self.ed_pdf = QLineEdit()
            self.ed_pdf.setReadOnly(True)
            self.ed_pdf.setPlaceholderText('Glissez un PDF ici ou cliquez sur Parcourir…')
            b = QPushButton('📂 Parcourir')
            b.setObjectName('browseBlue')
            b.clicked.connect(self._browse_pdf)
            row.addWidget(self.ed_pdf, 1)
            row.addWidget(b)
            lay.addLayout(row)
            self.lb_pdf_info = QLabel('Aucun fichier sélectionné')
            self.lb_pdf_info.setObjectName('fileInfo')
            lay.addWidget(self.lb_pdf_info)
            t = QLabel('Qualité de compression :')
            t.setObjectName('sectionTitle')
            lay.addWidget(t)
            self.grp_quality = QButtonGroup(self)
            for key in ('ultra_low', 'low', 'medium', 'high', 'fast'):
                (label, desc) = QUALITY_LABELS[key]
                r = QHBoxLayout()
                r.setSpacing(4)
                rb = QRadioButton(label)
                rb.setProperty('value', key)
                rb.setStyleSheet('font-weight: bold;')
                rb.setChecked(key == 'medium')
                rb.toggled.connect(self._on_option_changed)
                self.grp_quality.addButton(rb)
                hint = QLabel(desc)
                hint.setObjectName('radioHint')
                r.addWidget(rb)
                r.addWidget(hint)
                r.addStretch(1)
                lay.addLayout(r)
            t2 = QLabel('Mode :')
            t2.setObjectName('sectionTitle')
            lay.addWidget(t2)
            self.grp_mode = QButtonGroup(self)
            for key, label in MODE_LABELS.items():
                rb = QRadioButton(label)
                rb.setProperty('value', key)
                rb.setChecked(key == 'struct')
                rb.toggled.connect(self._on_option_changed)
                self.grp_mode.addButton(rb)
                lay.addWidget(rb)
            self.tx_estimates = QTextEdit()
            self.tx_estimates.setObjectName('estimates')
            self.tx_estimates.setReadOnly(True)
            self.tx_estimates.setMinimumHeight(74)
            self.tx_estimates.setMaximumHeight(220)
            self.tx_estimates.setPlainText('Sélectionnez un PDF pour voir les estimations')
            lay.addWidget(self.tx_estimates, 1)
            self.btn_compress = QPushButton('📦  COMPACTER  →  .psdi')
            self.btn_compress.setObjectName('primaryCyan')
            self.btn_compress.setEnabled(False)
            self.btn_compress.clicked.connect(self._do_compress)
            lay.addWidget(self.btn_compress)
            self.pb_compress = QProgressBar()
            self.pb_compress.setRange(0, 100)
            self.pb_compress.setValue(0)
            self.pb_compress.setVisible(False)
            lay.addWidget(self.pb_compress)
            self.lb_compress_status = QLabel('')
            self.lb_compress_status.setStyleSheet(f'''color: {COLORS['accent_green']}; font-size: 9pt;''')
            self.lb_compress_status.setWordWrap(True)
            lay.addWidget(self.lb_compress_status)
            self.btn_winlink = QPushButton('📧  Préparer pour Winlink')
            self.btn_winlink.setObjectName('primaryYellow')
            self.btn_winlink.setEnabled(False)
            self.btn_winlink.clicked.connect(self._do_winlink)
            lay.addWidget(self.btn_winlink)
            return box

        
        def _build_decompress_panel(self):
            box = QGroupBox('  📬  RECOMPOSER  (.psdi → PDF)  ')
            box.setObjectName('panelDecompress')
            lay = QVBoxLayout(box)
            lay.setSpacing(5)
            lay.addWidget(QLabel('Fichier .psdi reçu :'))
            row = QHBoxLayout()
            self.ed_psdi = QLineEdit()
            self.ed_psdi.setReadOnly(True)
            self.ed_psdi.setPlaceholderText('Glissez une archive .psdi ici…')
            b = QPushButton('📂 Parcourir')
            b.setObjectName('browseGreen')
            b.clicked.connect(self._browse_psdi)
            row.addWidget(self.ed_psdi, 1)
            row.addWidget(b)
            lay.addLayout(row)
            self.lb_psdi_info = QLabel('Aucun fichier sélectionné')
            self.lb_psdi_info.setObjectName('fileInfo')
            lay.addWidget(self.lb_psdi_info)
            self.lb_psdi_valid = QLabel('')
            self.lb_psdi_valid.setStyleSheet(f'''color: {COLORS['accent_green']}; font-family: Consolas, monospace;''')
            self.lb_psdi_valid.setWordWrap(True)
            lay.addWidget(self.lb_psdi_valid)
            sep = QFrame()
            sep.setObjectName('hline')
            sep.setFixedHeight(1)
            lay.addWidget(sep)
            hint = QLabel("Le fichier .psdi sera décompressé et\nrecomposé en document PDF.\n\nLe PDF résultant contiendra le texte et\nles images compressées de l'original.\n\nModes supportés :\n  • Structuré (texte + images repositionnées)\n  • Rendu image (pages en JPEG)")
            hint.setObjectName('hint')
            lay.addWidget(hint)
            lay.addStretch(1)
            self.btn_decompress = QPushButton('📬  RECOMPOSER  →  .pdf')
            self.btn_decompress.setObjectName('primaryGreen')
            self.btn_decompress.setEnabled(False)
            self.btn_decompress.clicked.connect(self._do_decompress)
            lay.addWidget(self.btn_decompress)
            self.pb_decompress = QProgressBar()
            self.pb_decompress.setRange(0, 100)
            self.pb_decompress.setValue(0)
            self.pb_decompress.setVisible(False)
            lay.addWidget(self.pb_decompress)
            self.lb_decompress_status = QLabel('')
            self.lb_decompress_status.setStyleSheet(f'''color: {COLORS['accent_cyan']}; font-size: 9pt;''')
            self.lb_decompress_status.setWordWrap(True)
            lay.addWidget(self.lb_decompress_status)
            self.btn_open_pdf = QPushButton('📂  Ouvrir le PDF recomposé')
            self.btn_open_pdf.setStyleSheet(f'''color: {COLORS['accent_yellow']}; font-weight: bold;''')
            self.btn_open_pdf.setVisible(False)
            self.btn_open_pdf.clicked.connect(self._open_last_pdf)
            lay.addWidget(self.btn_open_pdf)
            return box

        
        def _build_footer(self):
            row = QHBoxLayout()
            self.lb_last = QLabel('Téléportation de PDF par Radio')
            self.lb_last.setObjectName('footer')
            self.lb_last.setToolTip('Dernier message — cliquez sur 📋 JOURNAL pour tout voir')
            b = QLabel('F1GBD — ADRASEC 77 — FNRASEC')
            b.setObjectName('footer')
            row.addWidget(self.lb_last, 1)
            row.addWidget(b)
            return row

        
        def _toggle_log(self):
            if self.log_window.isVisible():
                self.log_window.hide()
            return None
            self.log_window.show_and_raise()

        
        def _restore_geometry(self):
            screen = QGuiApplication.primaryScreen()
            if not screen is not None:
                self.resize(980, 700)
            return None
            avail = screen.availableGeometry()
            w = min(max(880, avail.width() - 60), avail.width())
            h = min(max(560, min(900, avail.height() - 60)), avail.height())
            self.resize(w, h)
            self.move(avail.x() + (avail.width() - w) // 2, avail.y() + max(0, (avail.height() - h) // 2))

        
        def _log(self, msg, level = 'info'):
            color = {
                'info': COLORS['text_secondary'],
                'success': COLORS['success'],
                'warning': COLORS['warning'],
                'error': COLORS['error'] }.get(level, COLORS['text_secondary'])
            ts = datetime.now().strftime('%H:%M:%S')
            safe = msg.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            self.log_window.append_html(f'''<span style="color:{COLORS['text_dim']}">[{ts}]</span> <span style="color:{color}">{safe}</span>''')
            one_line = ' '.join(msg.split())
            if len(one_line) > 120:
                pass
            one_line = one_line[:117] + '…'
            self.lb_last.setText(f'''[{ts}] {one_line}''')
            self.lb_last.setStyleSheet(f'''color: {color}; font-size: 8pt;''')

        
        def _startup_banner(self):
            if not PSDI_AVAILABLE:
                self._log('⚠️ psdi_lib.py non trouvé ou non chargeable !', 'error')
                if PSDI_ERROR:
                    pass
                self._log(f'''   {PSDI_ERROR}''', 'error')
                self._log('   Placez psdi_lib.py dans le même répertoire', 'warning')
                self._log('   Nécessite : pip install PyQt6 pymupdf Pillow', 'warning')
            return None
            self._log(f'''PDF Teleporter v{VERSION} prêt (PyQt6)''', 'success')
            
            try:
                for q, desc in psdi_lib.get_quality_presets().items():
                    self._log(f'''  {q} : {desc}''')
                return None


        
        def dragEnterEvent(self, event):
            if event.mimeData().hasUrls():
                for url in event.mimeData().urls():
                    if not url.toLocalFile().lower().endswith(('.pdf', '.psdi')):
                        continue
                    event.acceptProposedAction()
                    event.mimeData().urls()
                    return None
            event.ignore()

        
        def dropEvent(self, event):
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                low = path.lower()
                if low.endswith('.pdf'):
                    self.load_pdf(path)
                    event.acceptProposedAction()
                    event.mimeData().urls()
                return None
                if not low.endswith('.psdi'):
                    continue
                self.load_psdi(path)
                event.acceptProposedAction()
                return None
            event.ignore()

        
        def _browse_pdf(self):
            (path, _) = QFileDialog.getOpenFileName(self, 'Sélectionner un document PDF', '', 'Documents PDF (*.pdf);;Tous les fichiers (*.*)')
            if path:
                self.load_pdf(path)
            return None

        
        def load_pdf(self, path):
            if not os.path.exists(path):
                pass
            return None
            self.ed_pdf.setText(path)
            size = os.path.getsize(path)
            self.lb_pdf_info.setText(f'''📎 {os.path.basename(path)}  —  {human_size(size)}''')
            if PSDI_AVAILABLE:
                PSDI_AVAILABLE
            self.btn_compress.setEnabled(not (self._busy))
            self._log(f'''PDF sélectionné : {os.path.basename(path)} ({human_size(size)})''')
            self._on_option_changed()

        
        def _browse_psdi(self):
            (path, _) = QFileDialog.getOpenFileName(self, 'Sélectionner une archive .psdi', '', 'Archives PSDI (*.psdi);;Tous les fichiers (*.*)')
            if path:
                self.load_psdi(path)
            return None

        
        def load_psdi(self, path):
            if not os.path.exists(path):
                pass
            return None
            self.ed_psdi.setText(path)
            size = os.path.getsize(path)
            self.lb_psdi_info.setText(f'''📎 {os.path.basename(path)}  —  {human_size(size)}''')
            self._log(f'''Archive sélectionnée : {os.path.basename(path)} ({human_size(size)})''')
            self.btn_open_pdf.setVisible(False)
            self._last_pdf_path = None
            if not PSDI_AVAILABLE:
                self.lb_psdi_valid.setText('⚠️ psdi_lib manquant — validation impossible')
                self.btn_decompress.setEnabled(False)
            return None
            
            try:
                fh = open(path, 'rb')
                data = fh.read()
                
                try:
                    None(None, None)
                    result = psdi_lib.validate_archive(data)
                    if result.get('valid'):
                        ver = result.get('version', '?')
                        crc = '✅ CRC OK' if result.get('checksum_ok') else '⚠️ CRC invalide'
                        if ver == 1:
                            pass
                        elif ver == 2:
                            pass
                        
                        mode = f'''v{ver}'''
                        self.lb_psdi_valid.setText(f'''✅ Archive valide — Mode : {mode} — {crc}''')
                        self.lb_psdi_valid.setStyleSheet(f'''color: {COLORS['accent_green']}; font-family: Consolas, monospace;''')
                        self.btn_decompress.setEnabled(not (self._busy))
                    return None
                    
                    try:
                        err = result.get('error', 'Inconnue')
                        self.lb_psdi_valid.setText(f'''❌ Archive invalide : {err}''')
                        self.lb_psdi_valid.setStyleSheet(f'''color: {COLORS['error']}; font-family: Consolas, monospace;''')
                        self.btn_decompress.setEnabled(False)
                        self._log(f'''Archive invalide : {err}''', 'error')
                        return None
                        with None:
                            if not 'Image':
                                pass
                        'Structuré'
                        
                        try:
                            continue
                        except Exception:
                            exc = None
                            self.lb_psdi_valid.setText(f'''❌ Erreur lecture : {exc}''')
                            self.lb_psdi_valid.setStyleSheet(f'''color: {COLORS['error']}; font-family: Consolas, monospace;''')
                            self.btn_decompress.setEnabled(False)
                            self._log(f'''Erreur lecture archive : {exc}''', 'error')

                        exc = None
                        del exc
                        return None
                        exc = None
                        del exc




        
        def _selected_quality(self):
            btn = self.grp_quality.checkedButton()
            if btn:
                pass
            return btn.property('value')
            return 'medium'

        
        def _selected_mode(self):
            btn = self.grp_mode.checkedButton()
            if btn:
                pass
            return btn.property('value')
            return 'struct'

        
        def _on_option_changed(self, checked = True):
            if not checked:
                pass
            return None
            if self.ed_pdf.text():
                if PSDI_AVAILABLE:
                    if not self._busy:
                        self._est_timer.start()
                    return None
                return None
            return None

        
        def _update_estimates(self):
            path = self.ed_pdf.text()
            if path and os.path.exists(path) and PSDI_AVAILABLE or self._busy:
                pass
            return None
            quality = self._selected_quality()
            mode = self._selected_mode()
            actual_quality = 'ultra_low' if quality == 'fast' else quality
            skip_img = quality == 'fast'
            self._est_token = self, self._est_token += 1, ._est_token
            self.tx_estimates.setPlainText('⏳ Calcul des estimations…')
            
            def job(_cb):
                (_, info) = psdi_lib.pdf_to_archive(path, quality = actual_quality, mode = mode, skip_images = skip_img)
                return info

            worker = Worker(job, self)
            
            def on_result(info):
                if token != self._est_token:
                    pass
                return None
                arch = info['archive_size']
                ratio = info['ratio_percent']
                if not info.get('estimates', { }):
                    info.get('estimates', { })
                est = { }
                
                try:
                    frags = psdi_lib.get_transfer_info(arch).get('tnc_fragments', '?')
                    lines = [
                        f'''Archive : {human_size(arch)} — {ratio}% de l\'original''',
                        f'''Fragments TNC : {frags} trames''',
                        '']
                    for n, d in :
                        pass
                    
                    lines += est.items(), []
                    self.tx_estimates.setPlainText('\n'.join(lines))
                    return None
                except Exception:
                    frags = '?'

                continue
                

            
            def on_error(msg):
                if token == self._est_token:
                    self.tx_estimates.setPlainText(f'''Erreur : {msg}''')
                return None

            worker.result.connect(on_result)
            worker.error.connect(on_error)
            worker.finished.connect(worker.deleteLater)
            self._est_worker = worker
            worker.start()

        
        def _set_busy(self, busy):
            self._busy = busy
            if not busy:
                not busy
                if PSDI_AVAILABLE:
                    PSDI_AVAILABLE
            self.btn_compress.setEnabled(bool(self.ed_pdf.text()))
            if not busy:
                not busy
                if PSDI_AVAILABLE:
                    PSDI_AVAILABLE
                    if bool(self.ed_psdi.text()):
                        bool(self.ed_psdi.text())
            self.btn_decompress.setEnabled(self.lb_psdi_valid.text().startswith('✅'))

        
        def _do_compress(self):
            if not self._busy or PSDI_AVAILABLE:
                pass
            return None
            pdf_path = self.ed_pdf.text()
            if not pdf_path or os.path.exists(pdf_path):
                QMessageBox.warning(self, 'Attention', 'Sélectionnez un fichier PDF valide.')
            return None
            quality = self._selected_quality()
            mode = self._selected_mode()
            actual_quality = 'ultra_low' if quality == 'fast' else quality
            skip_img = quality == 'fast'
            base = os.path.splitext(os.path.basename(pdf_path))[0]
            suggested = os.path.join(os.path.dirname(pdf_path), f'''{base}_{quality}.psdi''')
            (output_path, _) = QFileDialog.getSaveFileName(self, "Enregistrer l'archive compactée", suggested, 'Archives PSDI (*.psdi);;Tous les fichiers (*.*)')
            if not output_path:
                pass
            return None
            self._set_busy(True)
            self.btn_compress.setText('⏳ Compression en cours…')
            self.pb_compress.setValue(0)
            self.pb_compress.setVisible(True)
            self._log(f'''Compression : {os.path.basename(pdf_path)} → qualité={quality}, mode={mode}''')
            
            def job(cb):
                return psdi_lib.pdf_to_archive(pdf_path, quality = actual_quality, mode = mode, output_path = output_path, progress_callback = cb, skip_images = skip_img)[1]

            worker = Worker(job, self)
            worker.progress.connect((lambda s, t, m: self._on_progress(self.pb_compress, self.lb_compress_status, s, t, m)))
            
            def on_result(info):
                orig = info['original_size']
                arch = info['archive_size']
                ratio = info['ratio_percent']
                elapsed = info.get('compression_time', 0)
                factor = orig / arch if arch else 0
                self.pb_compress.setValue(100)
                self.lb_compress_status.setText(f'''✅ {os.path.basename(output_path)} — {arch:,} octets ({ratio}%) en {elapsed:.1f}s'''.replace(',', ' '))
                self._log(f'''✅ Compacté : {orig:,} → {arch:,} octets ({ratio}%, ×{factor:.1f}) en {elapsed:.1f}s''', 'success')
                self._log(f'''   Fichier : {output_path}''', 'success')
                self._last_psdi_path = output_path
                self.btn_winlink.setEnabled(True)
                self._finish_compress()

            
            def on_error(msg):
                self.pb_compress.setVisible(False)
                self.lb_compress_status.setText(f'''❌ Erreur : {msg}''')
                self._log(f'''Erreur compression : {msg}''', 'error')
                self._finish_compress()

            worker.result.connect(on_result)
            worker.error.connect(on_error)
            worker.finished.connect(worker.deleteLater)
            self._worker = worker
            worker.start()

        
        def _finish_compress(self):
            self.btn_compress.setText('📦  COMPACTER  →  .psdi')
            self._set_busy(False)

        
        def _do_decompress(self):
            if not self._busy or PSDI_AVAILABLE:
                pass
            return None
            psdi_path = self.ed_psdi.text()
            if not psdi_path or os.path.exists(psdi_path):
                QMessageBox.warning(self, 'Attention', 'Sélectionnez un fichier .psdi valide.')
            return None
            base = os.path.splitext(os.path.basename(psdi_path))[0]
            suggested = os.path.join(os.path.dirname(psdi_path), f'''{base}_recomposé.pdf''')
            (output_path, _) = QFileDialog.getSaveFileName(self, 'Enregistrer le PDF recomposé', suggested, 'Documents PDF (*.pdf);;Tous les fichiers (*.*)')
            if not output_path:
                pass
            return None
            self._set_busy(True)
            self.btn_decompress.setText('⏳ Recomposition en cours…')
            self.pb_decompress.setValue(0)
            self.pb_decompress.setVisible(True)
            self.btn_open_pdf.setVisible(False)
            self._last_pdf_path = None
            self._log(f'''Recomposition : {os.path.basename(psdi_path)}''')
            fh = open(psdi_path, 'rb')
            archive_data = fh.read()
            None(None, None)
            
            def job(cb):
                (pdf_bytes, info) = psdi_lib.archive_to_pdf(archive_data, output_path = output_path, progress_callback = cb)
                return len(pdf_bytes)

            worker = Worker(job, self)
            worker.progress.connect((lambda s, t, m: self._on_progress(self.pb_decompress, self.lb_decompress_status, s, t, m)))
            
            def on_result(pdf_size):
                self.pb_decompress.setValue(100)
                self.lb_decompress_status.setText(f'''✅ {os.path.basename(output_path)} — {pdf_size:,} octets'''.replace(',', ' '))
                self._log(f'''✅ Recomposé : {len(archive_data):,} → {pdf_size:,} octets''', 'success')
                self._log(f'''   Fichier : {output_path}''', 'success')
                self._last_pdf_path = output_path
                self.btn_open_pdf.setVisible(True)
                self._finish_decompress()

            
            def on_error(msg):
                self.pb_decompress.setVisible(False)
                self.lb_decompress_status.setText(f'''❌ Erreur : {msg}''')
                self._log(f'''Erreur recomposition : {msg}''', 'error')
                self._finish_decompress()

            worker.result.connect(on_result)
            worker.error.connect(on_error)
            worker.finished.connect(worker.deleteLater)
            self._worker = worker
            worker.start()
            return None
            with None:
                if not None:
                    pass
            continue

        
        def _finish_decompress(self):
            self.btn_decompress.setText('📬  RECOMPOSER  →  .pdf')
            self._set_busy(False)

        
        def _on_progress(self, bar, label, step, total, msg):
            if total > 0:
                bar.setRange(0, total)
                bar.setValue(min(step, total))
            else:
                bar.setRange(0, 0)
            label.setText(msg)
            self._log(msg)

        
        def _do_winlink(self):
            path = self._last_psdi_path
            if not path or os.path.exists(path):
                QMessageBox.warning(self, 'Attention', "Compactez d'abord un PDF avant de préparer un message Winlink.")
            return None
            dest = path
            
            try:
                docs = os.path.join(os.path.expanduser('~'), 'Documents', 'PDFteleporter')
                os.makedirs(docs, exist_ok = True)
                candidate = os.path.join(docs, os.path.basename(path))
                if os.path.abspath(path) != os.path.abspath(candidate):
                    import shutil
                shutil.copy2(path, candidate)
                dest = candidate
                self._log(f'''📧 Fichier prêt pour Winlink : {dest}''', 'success')
                WinlinkDialog(dest, self).exec()
                return None
            except Exception:
                exc = None
                self._log(f'''Copie vers Documents impossible ({exc}) — chemin d\'origine utilisé''', 'warning')

            exc = None
            del exc
            continue
            exc = None
            del exc

        
        def _open_last_pdf(self):
            if self._last_pdf_path:
                if os.path.exists(self._last_pdf_path):
                    open_with_system(self._last_pdf_path)
                    self._log(f'''Ouverture : {self._last_pdf_path}''')
                return None
            return None

        
        def closeEvent(self, event):
            for w in (self._worker, self._est_worker):
                if not w is not None:
                    continue
                if not w.isRunning():
                    continue
                w.requestInterruption()
                w.wait(1500)
            super().closeEvent(event)

        __static_attributes__ = ('_busy', '_est_timer', '_est_token', '_est_worker', '_last_pdf_path', '_last_psdi_path', '_worker', 'btn_compress', 'btn_decompress', 'btn_log', 'btn_open_pdf', 'btn_winlink', 'ed_pdf', 'ed_psdi', 'grp_mode', 'grp_quality', 'lb_compress_status', 'lb_decompress_status', 'lb_last', 'lb_pdf_info', 'lb_psdi_info', 'lb_psdi_valid', 'log_window', 'pb_compress', 'pb_decompress', 'tx_estimates')
        __classcell__ = __class__
        return __class__

    
    def main():
        QApplication.setApplicationName(APP_NAME)
        QApplication.setApplicationVersion(VERSION)
        QApplication.setOrganizationName(ORG_NAME)
        app = QApplication(sys.argv)
        app.setWindowIcon(app_icon())
        app.setStyleSheet(QSS)
        win = PDFTeleporterWindow()
        win.show()
        for arg in sys.argv[1:]:
            if arg.lower().endswith('.psdi') and os.path.exists(arg):
                win.load_psdi(arg)
                sys.argv[1:]
            return app.exec()
            if not arg.lower().endswith('.pdf'):
                continue
            if not os.path.exists(arg):
                continue
            win.load_pdf(arg)
            return app.exec()
        return app.exec()

    if __name__ == '__main__':
        sys.exit(main())
    return None
    return None
except Exception:
    _exc = None
    PSDI_ERROR = str(_exc)
    logger.error('psdi_lib indisponible : %s', _exc)

_exc = None
del _exc
continue
_exc = None
del _exc
