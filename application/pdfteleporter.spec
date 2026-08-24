# PyInstaller spec for a one-folder Windows build.
#
# Two executables are produced from one analysis:
#
#   PDFteleporter.exe   windowed, the GUI and the Explorer context-menu target
#   psditool.exe        console, so the command line can actually print
#
# A windowed executable has no stdout on Windows, so running the CLI through
# PDFteleporter.exe from a terminal would silently produce nothing. Shipping a
# console twin costs a few hundred kilobytes and avoids that trap entirely.
#
# The exclusion list is not decoration. PyInstaller bundles whatever it finds
# importable in the build environment, and the original release shipped 846 MB
# unpacked -- 330 MB of it torch -- none of which this code can reach. Building
# on a machine with a cluttered virtualenv must not silently produce that
# again, so the unreachable packages are named explicitly.

import os
import sys

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

# --- Preflight -------------------------------------------------------------
# PyInstaller treats a missing hidden import as a warning, not an error, so a
# build environment without PyMuPDF produces an executable that looks fine and
# dies on the first import at run time. Fail here instead, loudly, naming the
# interpreter so a virtualenv mix-up is obvious.
_REQUIRED = [
    ("pymupdf", "pymupdf", "PDF engine"),
    ("PIL", "Pillow", "image handling"),
    ("PyQt6.QtWidgets", "PyQt6", "user interface"),
]

_missing = []
for _module, _package, _why in _REQUIRED:
    try:
        __import__(_module)
    except ImportError:
        _missing.append((_package, _why))

if _missing:
    raise SystemExit(
        "\n".join([
            "",
            "Build aborted: required packages are missing from this interpreter.",
            f"  Interpreter: {sys.executable}",
            "",
            *[f"  missing: {pkg:<10} ({why})" for pkg, why in _missing],
            "",
            "  Install them into THIS interpreter:",
            f"    \"{sys.executable}\" -m pip install " +
            " ".join(pkg for pkg, _ in _missing),
            "",
            "  If you use a virtual environment, activate it before building and",
            "  invoke PyInstaller as 'python -m PyInstaller', not the bare",
            "  'pyinstaller' command, which may resolve to a different Python.",
            "",
        ])
    )

# PyMuPDF ships no PyInstaller hook of its own, and PyInstaller bundles none
# for it, so nothing collects its compiled extensions automatically. Name them
# explicitly.
#
# The default search patterns are ('*.dll', '*.dylib', 'lib*.so'), which match
# none of what PyMuPDF actually ships: the extensions are _extra.pyd and
# _mupdf.pyd on Windows and _extra.so / _mupdf.so on Linux. PyInstaller's
# module graph usually finds them anyway, but relying on that is how a build
# silently loses a dependency, so the patterns are given here.
_BINARY_PATTERNS = ['*.dll', '*.pyd', '*.so', '*.so.*', '*.dylib']

_pymupdf_binaries = []
_pymupdf_hidden = []

# The package was called 'fitz' before 1.24 and 'pymupdf' after, with a
# compatibility shim. Probe the modern name first and only fall back, so a
# current install does not trigger the shim's deprecation warning at build
# time.
for _name in ("pymupdf", "fitz"):
    try:
        __import__(_name)
    except ImportError:
        continue
    _pymupdf_binaries += collect_dynamic_libs(_name,
                                              search_patterns=_BINARY_PATTERNS)
    _pymupdf_hidden += collect_submodules(_name)
    break

if not _pymupdf_binaries:
    raise SystemExit(
        "\nBuild aborted: PyMuPDF is importable but none of its compiled "
        "extensions were found.\nThe installation looks incomplete; reinstall "
        "it with:\n"
        f'    "{sys.executable}" -m pip install --force-reinstall pymupdf\n'
    )

# Qt's own French catalogue. PyInstaller's PyQt6 hook does not collect the
# translations directory, so without this the interface reads in French while
# every file dialog and standard button it opens is in English.
_qt_translations = []
try:
    from PyQt6.QtCore import QLibraryInfo

    _qt_tr_dir = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    for _name in ("qtbase_fr.qm", "qt_fr.qm"):
        _candidate = os.path.join(_qt_tr_dir, _name)
        if os.path.exists(_candidate):
            _qt_translations.append((_candidate, os.path.join("PyQt6", "Qt6",
                                                              "translations")))
except Exception:  # noqa: BLE001
    pass

if not _qt_translations:
    print("WARNING: no French Qt catalogue found; dialogs will be in English.")

block_cipher = None

EXCLUDES = [
    # Machine-learning and scientific stacks: never imported by this code.
    'torch', 'torchaudio', 'torchgen', 'functorch', 'transformers', 'datasets',
    'accelerate', 'peft', 'safetensors', 'tokenizers', 'sentencepiece',
    'huggingface_hub', 'onnxruntime', 'pyarrow', 'scipy', 'sklearn', 'pandas',
    'matplotlib', 'sympy', 'networkx', 'numpy', 'av', 'grpc', 'opentelemetry',
    # Development and packaging tooling.
    'IPython', 'jupyter', 'notebook', 'pytest', 'setuptools', 'pip', 'Cython',
    'test', 'pydoc_data', 'lib2to3',
    # The project's own test package: useful in the repository, dead weight in
    # a shipped build. 'unittest' itself is not excluded, since parts of the
    # standard library import it.
    'tests',
    # Qt modules the interface does not touch. QtWebEngine alone is ~150 MB.
    'PyQt6.QtWebEngineCore', 'PyQt6.QtWebEngineWidgets', 'PyQt6.QtWebChannel',
    'PyQt6.QtQml', 'PyQt6.QtQuick', 'PyQt6.QtQuick3D', 'PyQt6.QtMultimedia',
    'PyQt6.QtMultimediaWidgets', 'PyQt6.Qt3DCore', 'PyQt6.Qt3DRender',
    'PyQt6.QtCharts', 'PyQt6.QtDataVisualization', 'PyQt6.QtBluetooth',
    'PyQt6.QtNetworkAuth', 'PyQt6.QtPositioning', 'PyQt6.QtSerialPort',
    'PyQt6.QtSql', 'PyQt6.QtTest', 'PyQt6.QtDesigner', 'PyQt6.QtHelp',
    # Tk ships with CPython and is dead weight in a Qt application.
    'tkinter', 'tcl', 'tk', '_tkinter',
]

a = Analysis(
    ['pdfteleporter.py'],
    pathex=[],
    binaries=_pymupdf_binaries,
    datas=[('assets/pdfteleporter.ico', 'assets')] + _qt_translations,
    hiddenimports=_pymupdf_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

gui_exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PDFteleporter',
    debug=False,
    strip=False,
    upx=False,          # UPX-packed binaries trip several antivirus engines
    console=False,
    icon='assets/pdfteleporter.ico',
    version='build/version_info.txt',
)

cli_exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='psditool',
    debug=False,
    strip=False,
    upx=False,
    console=True,
    icon='assets/pdfteleporter.ico',
    version='build/version_info_cli.txt',
)

coll = COLLECT(
    gui_exe,
    cli_exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='PDFteleporter',
)
