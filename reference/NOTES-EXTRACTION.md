# PDFteleporter — bytecode extraction notes

Source binary: PDFteleporter.7z (Windows, PyInstaller onedir, CPython 3.13).
Upstream ships no source; licence bundled with the binary is GNU GPL v3, and
the build embeds PyMuPDF (AGPL v3). Both grant source access and the right to
modify, so recovering the source from the shipped bytecode is within licence.

## What the application actually is

Only two modules are the application; the other 9478 modules in the PYZ are
vendored third-party libraries (torch, transformers, pyarrow, scipy, sklearn,
onnxruntime — none of them used, all dragged in from the dev environment).

  PDFteleporter.pyc   58 KB   Tkinter GUI, class PDFTeleporterApp
  pdf_trans.pyc       86 KB   engine, 37 functions + class PDFTransferManager

PDF_TRANS_VERSION reads '1.0.6'. The module docstring history stops at 1.0.5
because upstream did not update it.

## .psdi wire format (recovered, authoritative)

Common header:
    [0:4]    magic  b'PSDI'
    [4:6]    '<H'   version

Version 1 — ARCHIVE_VERSION_STRUCT (structured text+images mode):
    [6:10]   '<I'   crc32(manifest_bytes) & 0xFFFFFFFF
    [10:14]  '<I'   manifest_size
    [14:...]        manifest, lzma FORMAT_RAW, filters=[{'id': FILTER_LZMA2}]
    then     '<H'   nb_images
    per image:
             '<H'   xref id
             '<B'   flags
             '<I'   payload length
             payload

Version 2 — ARCHIVE_VERSION_IMAGE (flattened page-render mode):
    [6:8]    '<H'   nb_pages
    per page:
             '<f'   page width
             '<f'   page height
             '<B'   flags
             '<I'   payload length
             payload

Transport constants:
    TNC_MAX_PAYLOAD      170
    TNC_FRAME_TYPE_CODE  'P'
    TNC_INTER_FRAME_DELAY 0.5
    VARA_FRAME_TYPE      'VPDF'

Quality presets (dpi / jpeg_quality / img_max_dim / map_max_dim /
banner_max_h / lzma_preset):
    ultra_low   72 / 20 / 400 / 300 / 25 / 6
    low         90 / 30 / 600 / 400 / 30 / 9
    medium     120 / 45 / 800 / 600 / 40 / 9
    high       150 / 55 / 900 / 700 / 50 / 9

## State of the recovered sources

sources/*.py are pycdc output. Every statement is present, in order, with
docstrings and identifiers intact. Two known defects remain, both structural
rather than semantic:

  1. try/except reconstruction. CPython 3.11+ moved exception handling to
     out-of-line exception tables; pycdc only partially reconstructs them, so
     handlers do not close and following statements nest one level too deep.
     Statement order is still correct — cross-check against disasm/*.dis.
  2. One generator expression in prepare_for_tnc renders as a `lambda .0:`
     stub because 3.13 keeps genexps in a separate code object.

Neither file is importable as-is. They are a specification to read, not a
drop-in module.

## tools/pycdc-python313.patch

Applies to zrax/pycdc master. Adds the Python 3.13 opcodes that blocked
decompilation outright:

  MAKE_FUNCTION (no oparg) + SET_FUNCTION_ATTRIBUTE
  CONVERT_VALUE / FORMAT_SIMPLE / FORMAT_WITH_SPEC   (f-strings)
  CALL_KW                                            (3.13 keyword calls)
  LOAD_FAST_AND_CLEAR + SWAP                         (inlined comprehensions)
  MAP_ADD / SET_ADD                                  (dict/set comprehensions)
  TO_BOOL, LOAD_ASSERTION_ERROR, DELETE_DEREF
  POP_JUMP_IF_NONE / POP_JUMP_IF_NOT_NONE
  MAKE_CELL, COPY_FREE_VARS, RETURN_GENERATOR, LOAD_FAST_CHECK
  CALL_INTRINSIC_1, YIELD_VALUE (3.12 oparg form)
  STORE_FAST_LOAD_FAST / STORE_FAST_STORE_FAST       (split at fetch level)
  class detection past the NULL that 3.11+ pushes after LOAD_BUILD_CLASS
  PUSH_NULL emitted after the callable in 3.13 call sequences

Build:
    git clone https://github.com/zrax/pycdc && cd pycdc
    git apply pycdc-python313.patch
    cmake -DCMAKE_BUILD_TYPE=Release . && make -j4
