# PDF Teleporter — complete archive

Two halves. `application/` is the thing you build and ship; `reference/` is the
material it was reconstructed from, kept because the wire format has to stay
interoperable and the only authoritative description of it is that bytecode.

```
application/     PyQt6 rewrite, Windows build and installer
reference/       recovered sources of the original implementation
```

## application/

```
psditool/format.py          .psdi container, binary layout
psditool/engine.py          compression and rebuild, both modes
psditool/presets.py         quality presets, transfer estimates
psditool/cli.py             headless CLI, also the context-menu entry point
psditool/shell_windows.py   Explorer integration (HKEY_CURRENT_USER only)
psditool/gui/               PyQt6 interface, native system theme
pdfteleporter.py            single entry point: GUI / context menu / CLI
pdfteleporter.spec          PyInstaller, two executables from one analysis
build/version_info*.txt     Windows version resources
installer/pdfteleporter.iss Inno Setup 7 script
build.cmd                   builds the application, then the installer
assets/                     application icon
```

Start at `application/README.md`. It covers usage, the archive format, the
compression work, the build, and code signing.

Quick start on Windows:

```
pip install -r requirements.txt pyinstaller
build.cmd full
```

Running from source needs no build at all: `python pdfteleporter.py`.

## reference/

The original was distributed as a PyInstaller bundle with no source, under the
GNU GPL v3, which grants the right to recover and modify it. Only two of the
9478 modules in that bundle were the application; the rest were vendored
libraries, most of them unreachable from the code.

```
sources/pdf_trans.py         engine, 37 functions + PDFTransferManager
sources/PDFteleporter.py     original Tkinter GUI
disasm/*.dis                 full disassembly, the authoritative fallback
disasm/*.pyc                 the extracted bytecode itself
tools/pycdc-python313.patch  Python 3.13 support for Decompyle++
```

The recovered sources are a specification to read, not importable modules. Every
statement is present and in order, with docstrings and identifiers intact, but
`try`/`except` structure is mangled: CPython 3.11+ moved exception handling into
out-of-line tables that pycdc only partly reconstructs, so handlers do not close
and following statements nest one level too deep. When a control-flow question
matters, check `disasm/` rather than trusting the indentation.

`tools/pycdc-python313.patch` applies to `zrax/pycdc` master and adds the 3.13
opcodes that blocked decompilation outright — `MAKE_FUNCTION` without an oparg,
`SET_FUNCTION_ATTRIBUTE`, the new f-string opcodes, `CALL_KW`, inlined
comprehensions, dict and set comprehensions, and several more. Without it the
engine decompiles to eighteen lines.

`NOTES-EXTRACTION.md` records the recovered `.psdi` layout and the constants,
independently of the source files.
