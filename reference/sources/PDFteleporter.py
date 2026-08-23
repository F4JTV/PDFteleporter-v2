# Source Generated with Decompyle++
# File: PDFteleporter.pyc (Python 3.13)

'''
╔══════════════════════════════════════════════════════════════════════╗
║                     PDF TELEPORTER v1.0.6                          ║
║        Compression / Décompression PDF pour transfert radio         ║
║                   F1GBD — ADRASEC 77 — FNRASEC                     ║
╚══════════════════════════════════════════════════════════════════════╝

Application autonome pour :
  • Compacter un PDF en fichier .psdi (archive compressée optimisée radio)
  • Recomposer un fichier .psdi reçu en PDF

Utilise la bibliothèque pdf_trans.py (même répertoire) pour la
compression structurée (texte + images) ou rendu image des pages PDF.

Formats supportés en entrée : .pdf
Format archive : .psdi (signature PSDI, compression LZMA/zlib)

Dépendances :
  pip install pymupdf Pillow --break-system-packages

Fichier requis :
  pdf_trans.py v1.0.6 (même répertoire que PDFteleporter.py)

Historique :
  v1.0.6 (juin 2026) — Intègre pdf_trans v1.0.6 : AUTO-BASCULE en mode rendu
                      image pour les PDF SCANNÉS ou TOURNÉS. Le mode struct
                      ignorait la rotation de page (/Rotate) et aplatissait
                      les images-masques en opaque, ce qui basculait les
                      arrêtés préfectoraux scannés de 90° sur fond noir à la
                      recomposition. pdf_to_archive() détecte désormais ces
                      documents et route automatiquement vers le mode image
                      (get_pixmap, qui respecte la rotation et compose les
                      masques). Les vrais PDF texte natifs non tournés
                      restent en mode struct. Compatibilité ascendante totale.
                      Corrige aussi la PERTE DES CADRES ET COULEURS de tableau
                      à la recomposition struct : les grilles et fonds de
                      cellule tracés en segments de ligne (LibreOffice, et
                      selon la version de MuPDF — d\'où une différence possible
                      Windows/Linux) étaient ignorés par l\'extraction, qui ne
                      gardait que les rectangles "re". L\'extraction reconstruit
                      désormais les rectangles aussi à partir des lignes.
  v1.0.5 (mai 2026) — Intègre pdf_trans v1.0.5 : correctif des CASES À
                      COCHER perdues à la recomposition des PDF formulaires
                      (AcroForm). L\'état coché des cases / boutons radio,
                      logé dans l\'apparence du widget et non restitué par
                      l\'extraction texte, est désormais relevé puis redessiné
                      sous forme de coche vectorielle dans le PDF recomposé.
                      Concerne notamment les SITREP ADRASEC remplis.
                      Compatibilité ascendante totale.
  v1.0.4 (mai 2026) — Intègre pdf_trans v1.0.4 : correctif de l\'interligne
                      sur les PDF à fontes très petites. La hauteur du rect
                      de rendu est désormais proportionnelle à la taille
                      réelle des polices (au lieu d\'un minimum fixe à 12 pt
                      qui chevauchait les lignes successives sur les SITREP
                      Excel à fontes ~4 pt). Élimine l\'empilement vertical
                      des paragraphes dans les cadres "Commentaires".
                      Compatibilité ascendante totale.
  v1.0.3 (mai 2026) — Intègre pdf_trans v1.0.3 :
                      1) Décomposition automatique des ligatures Unicode
                         (ﬀ ﬁ ﬂ ﬃ ﬄ ﬅ ﬆ) et heuristique de restauration
                         pour les caractères U+FFFD produits par LibreOffice
                         à la place des ligatures \'ti\' et \'tt\' (Situa**ti**on,
                         qui**tt**é, Éduca**ti**on, lu**tt**e, rou**ti**er,
                         pollu**ti**on, etc.).
                      2) Compensation de la marge interne d\'insert_htmlbox
                         pour éviter le débordement de texte sur les PDF
                         Excel à fontes très petites (~4 pt) à bbox optimisés
                         au pixel près.
  v1.0.2 (mai 2026) — Intègre pdf_trans v1.0.2 : correctif du débordement
                      de texte dans les cellules de tableaux lors de la
                      recomposition structurée. La famille de fonte
                      d\'origine (sans-serif/serif/mono) est désormais
                      conservée pour un rendu plus fidèle. Compatibilité
                      ascendante préservée avec les archives .psdi v1.0.0
                      et v1.0.1.
  v1.0.1 (mai 2026) — Intègre pdf_trans v1.0.1 (correctif fond noir
                      Microsoft Print To PDF / Word LTSC) + correctif Linux
                      pour l\'ouverture du PDF recomposé (xdg-open avec
                      environnement LD_LIBRARY_PATH propre, nécessaire pour
                      les binaires PyInstaller).
  v1.0.0 (mars 2026) — Version initiale publique.

Auteur  : F1GBD / ADRASEC 77
Licence : Usage libre radioamateurs et sécurité civile
'''
import os
import sys
import threading
import logging
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
VERSION = '1.0.6'

def hide_console():
    '''Masque la fenêtre console sous Windows.'''
    if os.name == 'nt':
        
        try:
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
            return None
            return None
            return None

        return None

hide_console()
logger = logging.getLogger('PDFteleporter')
logging.basicConfig(level = logging.INFO, format = '%(asctime)s [%(levelname)s] %(message)s')
PDF_TRANS_AVAILABLE = False

try:
    import pdf_trans
    PDF_TRANS_AVAILABLE = True
    logger.info('pdf_trans chargé — compression PDF disponible')
    COLORS = { }['bg_dark']['bg_medium']['bg_light']['bg_input']['text_primary']['text_secondary']['text_dim']['accent_blue']['accent_cyan']['accent_green']['accent_yellow']['accent_orange']['accent_red']['success']['warning']['error']
    QUALITY_LABELS = {
        'ultra_low': ('⚡ Ultra Low', 'Urgence — Packet 1200 baud (~5% taille)'),
        'low': ('📡 Low', 'Packet 9600 / VARA HF lent (~10% taille)'),
        'medium': ('📻 Medium', 'VARA HF / FM standard (~20% taille)'),
        'high': ('🔊 High', 'VARA FM rapide (~25% taille, bonne qualité)'),
        'fast': ('🚀 SANS IMAGE', 'Texte seul — transfert ultra-rapide') }
    MODE_LABELS = {
        'struct': 'Structuré (texte + images)',
        'image': 'Rendu image (pages JPEG)' }
    
    class PDFTeleporterApp:
        __firstlineno__ = 168
        __doc__ = 'Application GUI autonome de compression / décompression PDF.'
        
        def __init__(self):
            self._setup_dpi()
            self.root = tk.Tk()
            self.root.title(f'''📄 PDF Teleporter v{VERSION} — par F1GBD ADRASEC 77''')
            self.root.configure(bg = COLORS['bg_dark'])
            self.root.minsize(820, 600)
            self.root.update_idletasks()
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            w = min(900, sw - 40)
            h = sh - 60
            x = max(0, (sw - w) // 2)
            self.root.geometry(f'''{w}x{h}+{x}+0''')
            
            try:
                self.root.state('zoomed')
                self._busy = False
                self._last_psdi_path = None
                self._build_gui()
                return None

            continue

        
        def _setup_dpi(self):
            '''Configure le DPI awareness selon la version de Windows.'''
            if os.name != 'nt':
                pass
            return None
            
            try:
                import ctypes
                ver = sys.getwindowsversion()
                if ver.build >= 22000:
                    
                    try:
                        ctypes.windll.shcore.SetProcessDpiAwareness(2)
                        return None
                        
                        try:
                            ctypes.windll.shcore.SetProcessDpiAwareness(1)
                            return None
                            except Exception:
                                ctypes.windll.user32.SetProcessDPIAware()
                                
                                try:
                                    return None
                                    
                                    try:
                                        except Exception:
                                            ctypes.windll.user32.SetProcessDPIAware()
                                            
                                            try:
                                                return None
                                                
                                                try:
                                                    pass

                                                return None







        
        def _build_gui(self):
            """Construit toute l'interface."""
            main = tk.Frame(self.root, bg = COLORS['bg_dark'])
            main.pack(fill = tk.BOTH, expand = True, padx = 10, pady = 10)
            self._build_header(main)
            center = tk.Frame(main, bg = COLORS['bg_dark'])
            center.pack(fill = tk.BOTH, expand = True, pady = (10, 0))
            left = tk.LabelFrame(center, text = '  📦  COMPACTER  (PDF → .psdi)  ', font = ('Consolas', 11, 'bold'), fg = COLORS['accent_cyan'], bg = COLORS['bg_medium'], labelanchor = 'n', padx = 10, pady = 10)
            left.pack(side = tk.LEFT, fill = tk.BOTH, expand = True, padx = (0, 5))
            self._build_compress_panel(left)
            right = tk.LabelFrame(center, text = '  📬  RECOMPOSER  (.psdi → PDF)  ', font = ('Consolas', 11, 'bold'), fg = COLORS['accent_green'], bg = COLORS['bg_medium'], labelanchor = 'n', padx = 10, pady = 10)
            right.pack(side = tk.RIGHT, fill = tk.BOTH, expand = True, padx = (5, 0))
            self._build_decompress_panel(right)
            self._build_log_panel(main)
            self._build_footer(main)

        
        def _build_header(self, parent):
            hdr = tk.Frame(parent, bg = COLORS['bg_dark'])
            hdr.pack(fill = tk.X)
            tk.Label(hdr, text = '📄  PDF TELEPORTER', font = ('Consolas', 16, 'bold'), fg = COLORS['accent_orange'], bg = COLORS['bg_dark']).pack(side = tk.LEFT, padx = (5, 15))
            tk.Label(hdr, text = f'''v{VERSION}''', font = ('Consolas', 9), fg = COLORS['text_dim'], bg = COLORS['bg_dark']).pack(side = tk.LEFT)
            tk.Button(hdr, text = 'ℹ️ A PROPOS', font = ('Arial', 8, 'bold'), bg = COLORS['bg_light'], fg = COLORS['accent_yellow'], activebackground = COLORS['accent_yellow'], activeforeground = 'black', relief = 'flat', padx = 6, pady = 1, cursor = 'hand2', command = self._show_about).pack(side = tk.LEFT, padx = (10, 0))
            status_text = '✅ pdf_trans chargé' if PDF_TRANS_AVAILABLE else '❌ pdf_trans manquant'
            status_color = COLORS['success'] if PDF_TRANS_AVAILABLE else COLORS['error']
            tk.Label(hdr, text = status_text, font = ('Consolas', 9), fg = status_color, bg = COLORS['bg_dark']).pack(side = tk.RIGHT, padx = 5)

        
        def _show_about(self):
            '''Affiche la fenêtre À propos.'''
            about_win = tk.Toplevel(self.root)
            about_win.title('À propos — PDF Teleporter')
            about_win.configure(bg = COLORS['bg_dark'])
            about_win.resizable(False, False)
            about_win.transient(self.root)
            about_win.grab_set()
            about_win.update_idletasks()
            x = self.root.winfo_x() + (self.root.winfo_width() - 520) // 2
            y = self.root.winfo_y() + (self.root.winfo_height() - 530) // 2
            about_win.geometry(f'''520x530+{x}+{y}''')
            tk.Label(about_win, text = '📄  PDF Teleporter', font = ('Consolas', 16, 'bold'), fg = COLORS['accent_orange'], bg = COLORS['bg_dark']).pack(pady = (15, 2))
            tk.Label(about_win, text = 'Mars 2026', font = ('Consolas', 10), fg = COLORS['text_dim'], bg = COLORS['bg_dark']).pack()
            tk.Frame(about_win, bg = COLORS['accent_orange'], height = 2).pack(fill = tk.X, padx = 30, pady = 10)
            about_text = 'Un outil de Téléportation de document PDF :\n\nCompression de documents PDF (SITREP, PSD, BSD,\nORSEC, FICHES REFLEXES) pour de la transmission\nvia les modes radio TNC Packet et VARA Modem\n(HF/FM/SAT) de TCQ ou Winlink Express.\n\n• Les données texte, tableaux et images sont extraites\n  du PDF, compressées individuellement et transmises\n  dans une archive PSDI minimale.\n\n• Le fichier PSDI est recomposé côté réception avec\n  sa mise en forme originale (tableaux, images, textes)\n  sans aucune opération manuelle spécifique.'
            tk.Label(about_win, text = about_text, font = ('Arial', 9), fg = COLORS['text_secondary'], bg = COLORS['bg_dark'], justify = tk.LEFT).pack(padx = 25, pady = (0, 10))
            tk.Frame(about_win, bg = COLORS['text_dim'], height = 1).pack(fill = tk.X, padx = 30, pady = 5)
            tk.Label(about_win, text = 'Développé par F1GBD (Jean-Louis Naudin)\nADRASEC 77 — Sécurité Civile — FNRASEC', font = ('Consolas', 9, 'bold'), fg = COLORS['accent_cyan'], bg = COLORS['bg_dark']).pack(pady = (5, 10))
            tk.Button(about_win, text = 'Fermer', font = ('Arial', 10), bg = COLORS['bg_light'], fg = COLORS['text_primary'], activebackground = COLORS['accent_blue'], activeforeground = 'black', relief = 'flat', padx = 20, pady = 3, cursor = 'hand2', command = about_win.destroy).pack(pady = (0, 15))

        
        def _build_compress_panel(self, parent):
            file_frame = tk.Frame(parent, bg = COLORS['bg_medium'])
            file_frame.pack(fill = tk.X, pady = (0, 8))
            tk.Label(file_frame, text = 'Fichier PDF :', font = ('Arial', 9), fg = COLORS['text_secondary'], bg = COLORS['bg_medium']).pack(anchor = 'w')
            row = tk.Frame(file_frame, bg = COLORS['bg_medium'])
            row.pack(fill = tk.X)
            self.var_pdf_path = tk.StringVar()
            tk.Entry(row, textvariable = self.var_pdf_path, font = ('Consolas', 9), bg = COLORS['bg_input'], fg = COLORS['text_primary'], insertbackground = COLORS['text_primary'], state = 'readonly').pack(side = tk.LEFT, fill = tk.X, expand = True, padx = (0, 5))
            tk.Button(row, text = '📂 Parcourir', font = ('Arial', 9), bg = COLORS['bg_light'], fg = COLORS['accent_blue'], activebackground = COLORS['accent_blue'], activeforeground = 'black', relief = 'flat', padx = 8, command = self._browse_pdf).pack(side = tk.RIGHT)
            self.var_pdf_info = tk.StringVar(value = 'Aucun fichier sélectionné')
            tk.Label(parent, textvariable = self.var_pdf_info, font = ('Consolas', 8), fg = COLORS['text_dim'], bg = COLORS['bg_medium'], anchor = 'w').pack(fill = tk.X, pady = (0, 5))
            tk.Label(parent, text = 'Qualité de compression :', font = ('Arial', 9, 'bold'), fg = COLORS['accent_orange'], bg = COLORS['bg_medium']).pack(anchor = 'w', pady = (5, 3))
            self.var_quality = tk.StringVar(value = 'medium')
            qual_frame = tk.Frame(parent, bg = COLORS['bg_medium'])
            qual_frame.pack(fill = tk.X)
            for qkey in ('ultra_low', 'low', 'medium', 'high', 'fast'):
                (label, desc) = QUALITY_LABELS[qkey]
                rb_row = tk.Frame(qual_frame, bg = COLORS['bg_medium'])
                rb_row.pack(fill = tk.X, pady = 1)
                tk.Radiobutton(rb_row, text = label, variable = self.var_quality, value = qkey, font = ('Arial', 9, 'bold'), fg = COLORS['text_primary'], bg = COLORS['bg_medium'], selectcolor = COLORS['bg_dark'], activebackground = COLORS['bg_medium'], command = self._update_estimates).pack(side = tk.LEFT)
                tk.Label(rb_row, text = f'''  {desc}''', font = ('Arial', 7), fg = COLORS['text_dim'], bg = COLORS['bg_medium']).pack(side = tk.LEFT)
            tk.Label(parent, text = 'Mode :', font = ('Arial', 9, 'bold'), fg = COLORS['accent_orange'], bg = COLORS['bg_medium']).pack(anchor = 'w', pady = (8, 2))
            mode_frame = tk.Frame(parent, bg = COLORS['bg_medium'])
            mode_frame.pack(fill = tk.X)
            self.var_mode = tk.StringVar(value = 'struct')
            for mkey, mlabel in MODE_LABELS.items():
                tk.Radiobutton(mode_frame, text = mlabel, variable = self.var_mode, value = mkey, font = ('Arial', 9), fg = COLORS['text_primary'], bg = COLORS['bg_medium'], selectcolor = COLORS['bg_dark'], activebackground = COLORS['bg_medium'], command = self._update_estimates).pack(anchor = 'w')
            MODE_LABELS.items()
            est_frame = tk.Frame(parent, bg = COLORS['bg_dark'], relief = tk.SUNKEN, bd = 1)
            est_frame.pack(fill = tk.X, pady = (10, 5))
            self.var_estimates = tk.StringVar(value = 'Sélectionnez un PDF pour voir les estimations')
            tk.Label(est_frame, textvariable = self.var_estimates, font = ('Consolas', 8), fg = COLORS['accent_cyan'], bg = COLORS['bg_dark'], justify = tk.LEFT, anchor = 'w').pack(fill = tk.X, padx = 8, pady = 6)
            self.btn_compress = tk.Button(parent, text = '📦  COMPACTER  →  .psdi', font = ('Consolas', 12, 'bold'), bg = COLORS['accent_cyan'], fg = 'black', activebackground = '#00b8d4', activeforeground = 'black', relief = 'flat', pady = 8, cursor = 'hand2', command = self._do_compress, state = tk.DISABLED)
            self.btn_compress.pack(fill = tk.X, pady = (10, 0))
            self.var_compress_status = tk.StringVar(value = '')
            tk.Label(parent, textvariable = self.var_compress_status, font = ('Arial', 8), fg = COLORS['accent_green'], bg = COLORS['bg_medium']).pack(fill = tk.X, pady = (3, 0))
            self.btn_winlink = tk.Button(parent, text = '📧  Préparer pour Winlink', font = ('Consolas', 10, 'bold'), bg = COLORS['accent_yellow'], fg = 'black', activebackground = '#ffca28', activeforeground = 'black', relief = 'flat', pady = 5, cursor = 'hand2', command = self._do_winlink, state = tk.DISABLED)
            self.btn_winlink.pack(fill = tk.X, pady = (5, 0))

        
        def _build_decompress_panel(self, parent):
            file_frame = tk.Frame(parent, bg = COLORS['bg_medium'])
            file_frame.pack(fill = tk.X, pady = (0, 8))
            tk.Label(file_frame, text = 'Fichier .psdi reçu :', font = ('Arial', 9), fg = COLORS['text_secondary'], bg = COLORS['bg_medium']).pack(anchor = 'w')
            row = tk.Frame(file_frame, bg = COLORS['bg_medium'])
            row.pack(fill = tk.X)
            self.var_psdi_path = tk.StringVar()
            tk.Entry(row, textvariable = self.var_psdi_path, font = ('Consolas', 9), bg = COLORS['bg_input'], fg = COLORS['text_primary'], insertbackground = COLORS['text_primary'], state = 'readonly').pack(side = tk.LEFT, fill = tk.X, expand = True, padx = (0, 5))
            tk.Button(row, text = '📂 Parcourir', font = ('Arial', 9), bg = COLORS['bg_light'], fg = COLORS['accent_green'], activebackground = COLORS['accent_green'], activeforeground = 'black', relief = 'flat', padx = 8, command = self._browse_psdi).pack(side = tk.RIGHT)
            self.var_psdi_info = tk.StringVar(value = 'Aucun fichier sélectionné')
            tk.Label(parent, textvariable = self.var_psdi_info, font = ('Consolas', 8), fg = COLORS['text_dim'], bg = COLORS['bg_medium'], anchor = 'w', justify = tk.LEFT).pack(fill = tk.X, pady = (0, 5))
            self.var_psdi_valid = tk.StringVar(value = '')
            tk.Label(parent, textvariable = self.var_psdi_valid, font = ('Consolas', 9), fg = COLORS['accent_green'], bg = COLORS['bg_medium'], anchor = 'w', justify = tk.LEFT).pack(fill = tk.X, pady = (0, 8))
            ttk.Separator(parent, orient = 'horizontal').pack(fill = tk.X, pady = 5)
            tk.Label(parent, text = "Le fichier .psdi sera décompressé et\nrecomposé en document PDF.\n\nLe PDF résultant contiendra le texte et\nles images compressées de l'original.\n\nModes supportés :\n  • Structuré (texte + images repositionnées)\n  • Rendu image (pages en JPEG)", font = ('Arial', 9), fg = COLORS['text_dim'], bg = COLORS['bg_medium'], justify = tk.LEFT).pack(fill = tk.X, pady = 5)
            self.btn_decompress = tk.Button(parent, text = '📬  RECOMPOSER  →  .pdf', font = ('Consolas', 12, 'bold'), bg = COLORS['accent_green'], fg = 'black', activebackground = '#00e676', activeforeground = 'black', relief = 'flat', pady = 8, cursor = 'hand2', command = self._do_decompress, state = tk.DISABLED)
            self.btn_decompress.pack(fill = tk.X, pady = (10, 0))
            self.var_decompress_status = tk.StringVar(value = '')
            tk.Label(parent, textvariable = self.var_decompress_status, font = ('Arial', 8), fg = COLORS['accent_cyan'], bg = COLORS['bg_medium']).pack(fill = tk.X, pady = (3, 0))
            self.btn_open_pdf = tk.Button(parent, text = '📂  Ouvrir le PDF recomposé', font = ('Arial', 10, 'bold'), bg = COLORS['bg_light'], fg = COLORS['accent_yellow'], activebackground = COLORS['accent_yellow'], activeforeground = 'black', relief = 'flat', pady = 5, cursor = 'hand2', command = self._open_last_pdf)
            self._last_pdf_path = None

        
        def _build_log_panel(self, parent):
            log_frame = tk.LabelFrame(parent, text = '  📋 Journal  ', font = ('Consolas', 9), fg = COLORS['text_dim'], bg = COLORS['bg_medium'], labelanchor = 'nw')
            log_frame.pack(fill = tk.X, pady = (10, 0))
            self.log_text = tk.Text(log_frame, height = 4, font = ('Consolas', 8), bg = COLORS['bg_dark'], fg = COLORS['text_secondary'], insertbackground = COLORS['text_primary'], relief = tk.FLAT, wrap = tk.WORD, state = tk.DISABLED)
            self.log_text.pack(fill = tk.X, padx = 3, pady = 3)
            self.log_text.tag_configure('info', foreground = COLORS['text_secondary'])
            self.log_text.tag_configure('success', foreground = COLORS['success'])
            self.log_text.tag_configure('warning', foreground = COLORS['warning'])
            self.log_text.tag_configure('error', foreground = COLORS['error'])
            self.log_text.tag_configure('timestamp', foreground = COLORS['text_dim'])

        
        def _build_footer(self, parent):
            footer = tk.Frame(parent, bg = COLORS['bg_dark'])
            footer.pack(fill = tk.X, pady = (8, 0))
            tk.Label(footer, text = 'Développé par F1GBD (mai 2026) — ADRASEC 77 — FNRASEC', font = ('Arial', 7), fg = COLORS['text_dim'], bg = COLORS['bg_dark']).pack(side = tk.RIGHT)
            tk.Label(footer, text = 'Téleportation de PDF par Radio', font = ('Arial', 7), fg = COLORS['text_dim'], bg = COLORS['bg_dark']).pack(side = tk.LEFT)

        
        def _log(self, msg, level = 'info'):
            '''Ajoute un message au journal.'''
            ts = datetime.now().strftime('%H:%M:%S')
            
            try:
                self.log_text.config(state = tk.NORMAL)
                self.log_text.insert(tk.END, f'''[{ts}] ''', 'timestamp')
                self.log_text.insert(tk.END, f'''{msg}\n''', level)
                self.log_text.see(tk.END)
                self.log_text.config(state = tk.DISABLED)
                return None


        
        def _browse_pdf(self):
            '''Sélectionne un fichier PDF à compacter.'''
            path = filedialog.askopenfilename(title = 'Sélectionner un document PDF', filetypes = [
                ('Documents PDF', '*.pdf'),
                ('Tous', '*.*')])
            if not path:
                pass
            return None
            self.var_pdf_path.set(path)
            size = os.path.getsize(path)
            name = os.path.basename(path)
            self.var_pdf_info.set(f'''📎 {name}  —  {size:,} octets ({size // 1024} Ko)''')
            self.btn_compress.config(state = tk.NORMAL if PDF_TRANS_AVAILABLE else tk.DISABLED)
            self._log(f'''PDF sélectionné : {name} ({size:,} octets)''')
            self._update_estimates()

        
        def _browse_psdi(self):
            '''Sélectionne un fichier .psdi à recomposer.'''
            path = filedialog.askopenfilename(title = 'Sélectionner une archive .psdi', filetypes = [
                ('Archives PSDI', '*.psdi'),
                ('Tous', '*.*')])
            if not path:
                pass
            return None
            self.var_psdi_path.set(path)
            size = os.path.getsize(path)
            name = os.path.basename(path)
            self.var_psdi_info.set(f'''📎 {name}  —  {size:,} octets ({size // 1024} Ko)''')
            self._log(f'''Archive sélectionnée : {name} ({size:,} octets)''')
            if PDF_TRANS_AVAILABLE:
                
                try:
                    f = open(path, 'rb')
                    data = f.read()
                    
                    try:
                        None(None, None)
                        result = pdf_trans.validate_archive(data)
                        None if result.get('valid') else None
                        
                        try:
                            'Image'
                            'Structuré'
                            continue
                        except Exception:
                            e = 'Structuré'
                            self.var_psdi_valid.set(f'''❌ Erreur lecture : {e}''')
                            self.btn_decompress.config(state = tk.DISABLED)
                            self._log(f'''Erreur lecture archive : {e}''', 'error')

                        e = None
                        del e
                        continue
                        e = None
                        del e



        
        def _update_estimates(self):
            '''Met à jour les estimations de taille et temps de transfert.'''
            path = self.var_pdf_path.get()
            if not path and os.path.exists(path) or PDF_TRANS_AVAILABLE:
                pass
            return None
            quality = self.var_quality.get()
            mode = self.var_mode.get()
            
            def _compute():
                
                try:
                    actual_quality = 'ultra_low' if quality == 'fast' else quality
                    skip_img = quality == 'fast'
                    (_, info) = pdf_trans.pdf_to_archive(path, quality = actual_quality, mode = mode, skip_images = skip_img)
                    arch_size = info['archive_size']
                    ratio = info['ratio_percent']
                    est = info.get('estimates', { })
                    t_info = pdf_trans.get_transfer_info(arch_size)
                    nb_frags = t_info.get('tnc_fragments', '?')
                    lines = [
                        f'''Archive : {arch_size:,} octets ({arch_size // 1024} Ko) — {ratio}% de l\'original''',
                        f'''Fragments TNC : {nb_frags} trames''',
                        '']
                    for name, duration in est.items():
                        lines.append(f'''  ⏱ {name} : {duration}''')
                    text = '\n'.join(lines)
                    self.root.after(0, (lambda : self.var_estimates.set(text)))
                    return None
                except Exception:
                    e = None
                    self.root.after(0, (lambda : self.var_estimates.set(f'''Erreur : {e}''')))

                e = None
                del e
                return None
                e = None
                del e

            self.var_estimates.set('⏳ Calcul des estimations...')
            threading.Thread(target = _compute, daemon = True).start()

        
        def _do_compress(self):
            '''Lance la compression PDF → .psdi.'''
            if not self._busy or PDF_TRANS_AVAILABLE:
                pass
            return None
            pdf_path = self.var_pdf_path.get()
            if not pdf_path or os.path.exists(pdf_path):
                messagebox.showwarning('Attention', 'Sélectionnez un fichier PDF valide.')
            return None
            quality = self.var_quality.get()
            mode = self.var_mode.get()
            actual_quality = 'ultra_low' if quality == 'fast' else quality
            skip_img = quality == 'fast'
            base_name = os.path.splitext(os.path.basename(pdf_path))[0]
            default_name = f'''{base_name}_{quality}.psdi'''
            output_path = filedialog.asksaveasfilename(title = "Enregistrer l'archive compactée", initialfile = default_name, defaultextension = '.psdi', filetypes = [
                ('Archives PSDI', '*.psdi'),
                ('Tous', '*.*')])
            if not output_path:
                pass
            return None
            self._busy = True
            self.btn_compress.config(state = tk.DISABLED, text = '⏳ Compression en cours...')
            self._log(f'''Compression : {os.path.basename(pdf_path)} → qualité={quality}, mode={mode}''')
            
            def progress_cb(step, total, msg):
                self.root.after(0, (lambda m = msg: self.var_compress_status.set(m)))
                self.root.after(0, (lambda m = msg: self._log(m)))

            
            def _thread():
                
                try:
                    (archive_data, info) = pdf_trans.pdf_to_archive(pdf_path, quality = actual_quality, mode = mode, output_path = output_path, progress_callback = progress_cb, skip_images = skip_img)
                    orig = info['original_size']
                    arch = info['archive_size']
                    ratio = info['ratio_percent']
                    elapsed = info.get('compression_time', 0)
                    
                    def _done():
                        self._busy = False
                        self.btn_compress.config(state = tk.NORMAL, text = '📦  COMPACTER  →  .psdi')
                        self.var_compress_status.set(f'''✅ {os.path.basename(output_path)} — {arch:,} octets ({ratio}%) en {elapsed:.1f}s''')
                        self._log(f'''✅ Compacté : {orig:,} → {arch:,} octets ({ratio}%, ×{orig / arch:.1f}) en {elapsed:.1f}s''', 'success')
                        self._log(f'''   Fichier : {output_path}''', 'success')
                        self._last_psdi_path = output_path
                        self.btn_winlink.config(state = tk.NORMAL)

                    self.root.after(0, _done)
                    return None
                except Exception:
                    e = None
                    
                    def _err():
                        self._busy = False
                        self.btn_compress.config(state = tk.NORMAL, text = '📦  COMPACTER  →  .psdi')
                        self.var_compress_status.set(f'''❌ Erreur : {e}''')
                        self._log(f'''Erreur compression : {e}''', 'error')

                    self.root.after(0, _err)

                e = None
                del e
                return None
                e = None
                del e

            threading.Thread(target = _thread, daemon = True).start()

        
        def _do_winlink(self):
            '''Prépare le fichier .psdi pour envoi via Winlink Express.'''
            psdi_path = getattr(self, '_last_psdi_path', None)
            if not psdi_path or os.path.exists(psdi_path):
                messagebox.showwarning('Attention', "Compactez d'abord un PDF avant de préparer un message Winlink.")
            return None
            psdi_name = os.path.basename(psdi_path)
            psdi_size = os.path.getsize(psdi_path)
            
            try:
                docs_dir = os.path.join(os.path.expanduser('~'), 'Documents', 'PDFteleporter')
                os.makedirs(docs_dir, exist_ok = True)
                dest_path = os.path.join(docs_dir, psdi_name)
                if os.path.abspath(psdi_path) != os.path.abspath(dest_path):
                    import shutil
                shutil.copy2(psdi_path, dest_path)
                win = tk.Toplevel(self.root)
                win.title('📧 Envoi via Winlink Express')
                win.configure(bg = COLORS['bg_dark'])
                win.resizable(False, False)
                win.transient(self.root)
                win.grab_set()
                win.update_idletasks()
                x = self.root.winfo_x() + (self.root.winfo_width() - 520) // 2
                y = self.root.winfo_y() + (self.root.winfo_height() - 520) // 2
                win.geometry(f'''520x520+{x}+{y}''')
                tk.Label(win, text = '📧  Envoi via Winlink Express', font = ('Consolas', 13, 'bold'), fg = COLORS['accent_yellow'], bg = COLORS['bg_dark']).pack(pady = (12, 3))
                tk.Label(win, text = f'''📎 {psdi_name}\n{psdi_size:,} octets ({psdi_size // 1024} Ko)''', font = ('Consolas', 9), fg = COLORS['text_secondary'], bg = COLORS['bg_dark']).pack(pady = (0, 5))
                tk.Frame(win, bg = COLORS['accent_yellow'], height = 2).pack(fill = tk.X, padx = 25, pady = 5)
                instructions = f'''📋  Procédure d\'envoi :\n\n1.  Ouvrez Winlink Express\n\n2.  Cliquez sur  Message → New Message...\n\n3.  Remplissez le destinataire (indicatif)\n\n4.  Sujet suggéré :  [PSDI] nom_du_document\n\n5.  Cliquez sur  Attachments → Add\n     Sélectionnez le fichier :\n     📂 {dest_path}\n\n6.  Dans le corps du message, indiquez :\n     « Recomposer avec PDF Teleporter »\n\n7.  Cliquez  Post to Outbox\n\n8.  Envoyez via  VARA HF / FM / Packet / Telnet'''
                instr_frame = tk.Frame(win, bg = COLORS['bg_medium'], relief = tk.RIDGE, bd = 1)
                instr_frame.pack(fill = tk.BOTH, expand = True, padx = 15, pady = 5)
                tk.Label(instr_frame, text = instructions, font = ('Arial', 9), fg = COLORS['text_primary'], bg = COLORS['bg_medium'], justify = tk.LEFT, anchor = 'nw').pack(fill = tk.BOTH, padx = 10, pady = 8)
                btn_frame = tk.Frame(win, bg = COLORS['bg_dark'])
                btn_frame.pack(fill = tk.X, padx = 15, pady = (5, 12))
                
                def _open_folder():
                    '''Ouvre le dossier contenant le .psdi.'''
                    folder = os.path.dirname(dest_path)
                    
                    try:
                        if sys.platform == 'win32':
                            os.startfile(folder)
                        return None
                        
                        try:
                            if sys.platform == 'darwin':
                                import subprocess
                                subprocess.Popen([
                                    'open',
                                    folder])
                            return None
                            
                            try:
                                import subprocess
                                subprocess.Popen([
                                    'xdg-open',
                                    folder])
                                return None
                            except Exception:
                                e = None
                                self._log(f'''Erreur ouverture dossier : {e}''', 'error')

                            e = None
                            del e
                            return None
                            e = None
                            del e



                
                def _copy_path():
                    '''Copie le chemin du fichier dans le presse-papiers.'''
                    self.root.clipboard_clear()
                    self.root.clipboard_append(dest_path)
                    self._log(f'''Chemin copié : {dest_path}''', 'info')
                    messagebox.showinfo('Copié', f'''Chemin copié dans le presse-papiers :\n{dest_path}''')

                tk.Button(btn_frame, text = '📂 Ouvrir le dossier', font = ('Arial', 10, 'bold'), bg = COLORS['accent_yellow'], fg = 'black', activebackground = '#ffca28', activeforeground = 'black', relief = 'flat', padx = 10, pady = 5, cursor = 'hand2', command = _open_folder).pack(side = tk.LEFT, padx = (0, 5))
                tk.Button(btn_frame, text = '📋 Copier le chemin', font = ('Arial', 10), bg = COLORS['bg_light'], fg = COLORS['accent_cyan'], activebackground = COLORS['accent_cyan'], activeforeground = 'black', relief = 'flat', padx = 10, pady = 5, cursor = 'hand2', command = _copy_path).pack(side = tk.LEFT, padx = (0, 5))
                tk.Button(btn_frame, text = 'Fermer', font = ('Arial', 10), bg = COLORS['bg_light'], fg = COLORS['text_primary'], relief = 'flat', padx = 15, pady = 5, cursor = 'hand2', command = win.destroy).pack(side = tk.RIGHT)
                self._log(f'''📧 Fichier prêt pour Winlink : {dest_path}''', 'success')
                return None
            except Exception:
                dest_path = psdi_path

            continue

        
        def _do_decompress(self):
            '''Lance la décompression .psdi → PDF.'''
            if not self._busy or PDF_TRANS_AVAILABLE:
                pass
            return None
            psdi_path = self.var_psdi_path.get()
            if not psdi_path or os.path.exists(psdi_path):
                messagebox.showwarning('Attention', 'Sélectionnez un fichier .psdi valide.')
            return None
            base_name = os.path.splitext(os.path.basename(psdi_path))[0]
            default_name = f'''{base_name}_recomposé.pdf'''
            output_path = filedialog.asksaveasfilename(title = 'Enregistrer le PDF recomposé', initialfile = default_name, defaultextension = '.pdf', filetypes = [
                ('Documents PDF', '*.pdf'),
                ('Tous', '*.*')])
            if not output_path:
                pass
            return None
            self._busy = True
            self.btn_decompress.config(state = tk.DISABLED, text = '⏳ Recomposition en cours...')
            self.btn_open_pdf.pack_forget()
            self._last_pdf_path = None
            self._log(f'''Recomposition : {os.path.basename(psdi_path)}''')
            
            def progress_cb(step, total, msg):
                self.root.after(0, (lambda m = msg: self.var_decompress_status.set(m)))
                self.root.after(0, (lambda m = msg: self._log(m)))

            
            def _thread():
                
                try:
                    f = open(psdi_path, 'rb')
                    archive_data = f.read()
                    
                    try:
                        None(None, None)
                        (pdf_bytes, info) = pdf_trans.archive_to_pdf(archive_data, output_path = output_path, progress_callback = progress_cb)
                        pdf_size = len(pdf_bytes)
                        arch_size = len(archive_data)
                        
                        def _done():
                            self._busy = False
                            self.btn_decompress.config(state = tk.NORMAL, text = '📬  RECOMPOSER  →  .pdf')
                            self.var_decompress_status.set(f'''✅ {os.path.basename(output_path)} — {pdf_size:,} octets''')
                            self._log(f'''✅ Recomposé : {arch_size:,} → {pdf_size:,} octets''', 'success')
                            self._log(f'''   Fichier : {output_path}''', 'success')
                            self._last_pdf_path = output_path
                            self.btn_open_pdf.pack(fill = tk.X, pady = (5, 0))

                        self.root.after(0, _done)
                        return None
                        with None:
                            if not None:
                                pass
                        
                        try:
                            continue
                        except Exception:
                            e = None
                            
                            def _err():
                                self._busy = False
                                self.btn_decompress.config(state = tk.NORMAL, text = '📬  RECOMPOSER  →  .pdf')
                                self.var_decompress_status.set(f'''❌ Erreur : {e}''')
                                self._log(f'''Erreur recomposition : {e}''', 'error')

                            self.root.after(0, _err)

                        e = None
                        del e
                        return None
                        e = None
                        del e



            threading.Thread(target = _thread, daemon = True).start()

        
        def _open_last_pdf(self):
            """Ouvre le dernier PDF recomposé avec l'application par défaut."""
            if self._last_pdf_path:
                if os.path.exists(self._last_pdf_path):
                    
                    try:
                        if sys.platform == 'win32':
                            os.startfile(self._last_pdf_path)
                        elif sys.platform == 'darwin':
                            import subprocess
                            subprocess.Popen([
                                'open',
                                self._last_pdf_path])
                        else:
                            import subprocess
                            subprocess.Popen([
                                'xdg-open',
                                self._last_pdf_path])
                        self._log(f'''Ouverture : {self._last_pdf_path}''')
                        return None
                        return None
                        return None
                    except Exception:
                        e = None
                        self._log(f'''Erreur ouverture : {e}''', 'error')
                        messagebox.showerror('Erreur', f'''Impossible d\'ouvrir le fichier :\n{e}''')

                    e = None
                    del e
                    return None
                    e = None
                    del e

        
        def run(self):
            '''Lance la boucle principale Tkinter.'''
            if not PDF_TRANS_AVAILABLE:
                self._log('⚠️ pdf_trans.py non trouvé !', 'error')
                self._log('   Placez pdf_trans.py dans le même répertoire', 'warning')
                self._log('   Nécessite : pip install pymupdf Pillow', 'warning')
            else:
                self._log('PDF Teleporter prêt', 'success')
                presets = pdf_trans.get_quality_presets()
                for q, desc in presets.items():
                    self._log(f'''  {q} : {desc}''')
            self.root.mainloop()

        __static_attributes__ = ('_busy', '_last_pdf_path', '_last_psdi_path', 'btn_compress', 'btn_decompress', 'btn_open_pdf', 'btn_winlink', 'log_text', 'root', 'var_compress_status', 'var_decompress_status', 'var_estimates', 'var_mode', 'var_pdf_info', 'var_pdf_path', 'var_psdi_info', 'var_psdi_path', 'var_psdi_valid', 'var_quality')

    if __name__ == '__main__':
        app = PDFTeleporterApp()
        app.run()
    return None
    return None
except ImportError:
    logger.error('Manque le teleporteur')

continue
