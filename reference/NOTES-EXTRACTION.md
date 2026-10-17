# PDF Teleporter 2.1.0 (F1GBD) — bytecode extraction notes

Source binary: PDFteleporter.7z (Windows, PyInstaller onedir, CPython 3.13,
PyQt6, PyMuPDF 1.28.2 / MuPDF 1.28.2, Pillow). Licence bundled with the binary
is GNU GPL v3; PyMuPDF is AGPL v3. Both grant source access and the right to
modify, so recovering the source from the shipped bytecode is within licence.

## What the application is

Only two modules are the application; the other 268 modules in the PYZ are
stdlib and the three dependencies. The build is lean this time (107 MB on
disk, 270 modules) — the 846 MB / 9478-module problem of the 1.0.6 release
is gone.

  PDFteleporter.pyc   69 KB   PyQt6 GUI, class PDFTeleporterWindow, VERSION 2.1.0
  psdi_lib.pyc        72 KB   engine, PSDI_LIB_VERSION 2.1.0, PDF_TRANS_VERSION 1.0.6

## Engine: psdi_lib 2.1.0 versus pdf_trans 1.0.6

Compared code object by code object with tools/cmp_pyc2.py (instruction
streams, docstrings and line numbers ignored). 43 code objects on each side,
34 byte-identical, 9 different — and of those 9, eight differ only by the
log prefix string ('[pdf_trans]' -> '[psdi_lib]'), the removed docstrings,
the added PSDI_LIB_VERSION constant and two dropped `assert` round-trip
checks in the `__main__` self-test.

The single function with algorithmic changes is `_extract_page_struct`:

  1. `page.get_text('dict', flags=fitz.TEXTFLAGS_DICT | fitz.TEXT_PRESERVE_WHITESPACE)`
     instead of `flags=fitz.TEXT_PRESERVE_WHITESPACE` alone. TEXTFLAGS_DICT
     (199) adds PRESERVE_LIGATURES, PRESERVE_IMAGES, MEDIABOX_CLIP and
     CID_FOR_UNKNOWN_UNICODE. The last one is the visible change: glyphs
     with no ToUnicode mapping now come out as raw CID control characters
     (e.g. ')\x17/79' for a typed 'F4LTV') instead of U+FFFD.
  2. The U+FFFD -> 'ti'/'tt' guess is now only applied when the character
     sits between two lowercase letters (`prev_c.isalpha() and
     prev_c.islower() and next_c.isalpha() and next_c.islower()`), i.e.
     for a ligature inside a readable word.

Everything else — presets, container layout, image optimisation, auto mode
detection (including the `if mode == 'struct' and not skip_images` guard
that skips rotation detection in text-only mode), TNC/VARA transport layer,
PDFTransferManager — is the unchanged 1.0.6 bytecode.

Full diff: bench/pdf_trans-1.0.6_vs_psdi_lib-2.1.0.diff

## State of the recovered sources

sources/PDFteleporter.py is complete and readable: every method is present
with its strings and identifiers; the only distortions are pycdc's usual
`if not X: pass / return` inversion for early returns and the single
try/except in load_psdi that does not close properly. Cross-check against
disasm/PDFteleporter.dis where the control flow looks odd.

sources/psdi_lib.py has the same try/except defect as the 1.0.6 recovery,
plus garbled comprehensions. For the engine the authoritative reading is
the 1.0.6 recovered source in the v2 repository's reference/ folder plus the
two changes listed above.

## tools/pycdc-python313.patch (updated)

Supersedes the previous patch. Applies to zrax/pycdc master (b428976).
Adds on top of the earlier opcode support:

  DICT_MERGE / DICT_UPDATE + CALL_FUNCTION_EX   f(*args, **kwargs)
  LOAD_SUPER_ATTR                                super().method() in 3.12+

Build:
    git clone https://github.com/zrax/pycdc && cd pycdc
    git apply pycdc-python313.patch
    cmake -DCMAKE_BUILD_TYPE=Release . && make -j4

Extraction:
    pip install pyinstxtractor-ng
    pyinstxtractor-ng PDFteleporter.exe
    pycdc PDFteleporter.exe_extracted/PDFteleporter.pyc
    pycdc PDFteleporter.exe_extracted/PYZ.pyz_extracted/psdi_lib.pyc

## bench/

bench.py loads psdi_lib.pyc straight into a CPython 3.13 interpreter with
PyMuPDF 1.28.2 (the same MuPDF as the shipped DLL) and runs it next to
psditool on the v2 test fixtures: archive sizes, compression and rebuild
times, and the four-way rebuild matrix (each archive through each decoder).
bench.json holds the raw rows, last regenerated against psditool 2.4.0: eight
fixtures, four presets, 128 cross-decodes, all passing.
