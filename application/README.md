# PDF Teleporter

Compresses PDF documents into compact `.psdi` archives for transmission over
narrow radio links (Packet TNC, VARA HF/FM, Winlink Express attachments), and
rebuilds them into readable PDFs on reception.

A 600 kB situation report that would take a quarter of an hour to push through
VARA HF fits into roughly 120 kB and moves in about three minutes, with its
layout intact at the other end.

PyQt6 rewrite of an earlier Tkinter application, with Windows Explorer
integration added. Archives remain byte-compatible with other `.psdi`
implementations, so a station running a different build can still rebuild what
this one sends.

## Install

```
pip install -r requirements.txt
python pdfteleporter.py
```

Python 3.10 or later.

## Use

The interface is in French: it is used by French emergency-communications
operators, and an English interface in that setting is a source of hesitation
under time pressure. Code, comments and this document stay in English.

Qt's own French catalogue (`qtbase_fr.qm`) is loaded at startup and bundled by
the spec. Without it the window reads in French while every file dialog and
standard button Qt supplies is in English.

**Graphical.** Compression on the left, rebuild on the right, timestamped log
across the bottom.

The interface forces the Qt **Fusion** style. Fusion renders identically on
every Windows version and under Wine, which matters for a tool deployed to
whatever machines an exercise provides: the Windows 11, Windows 10 and legacy
styles differ enough in metrics that a layout verified on one can crowd or clip
on another.

Forcing the style does not override the operator's system settings. Fusion
draws from the application palette, so the light or dark palette is chosen from
the system colour scheme and reapplied if that scheme changes while the
application is running. The dark palette is built explicitly rather than left
to Qt, which derives one on its own only where the platform plugin reports a
colour scheme. Selecting an archive validates its signature and CRC
immediately, so a file damaged in transit is caught before any time is spent
rebuilding it.

**Explorer context menu.** *Tools → Add Explorer context menu* registers two
verbs under `HKEY_CURRENT_USER` (no elevation, nothing machine-wide):

- right-click a `.pdf` → **Teleport: compress to .psdi**
- right-click a `.psdi` → **Teleport: rebuild PDF**

On Windows 11 these appear under *Show more options* (or Shift+F10). Reaching
the top-level menu requires a signed MSIX shell extension, which is a
disproportionate amount of machinery for a tool used a few times per exercise.

**Command line.**

```
python pdfteleporter.py compress SITREP.pdf -q low
python pdfteleporter.py rebuild  SITREP.psdi -o out.pdf
python pdfteleporter.py inspect  SITREP.psdi
python pdfteleporter.py presets
python pdfteleporter.py shell install
```

## Quality presets

| Preset | DPI | JPEG | Coordinates | Intended link |
|---|---|---|---|---|
| `ultra_low` | 72 | 20 | whole points | Emergency, Packet 1200 baud |
| `low` | 90 | 30 | whole points | Packet 9600, slow VARA HF |
| `medium` | 120 | 45 | 0.1 pt | VARA HF / FM, the usual choice |
| `high` | 150 | 55 | 0.1 pt | Fast VARA FM |

`Text only` drops every image, which is the fastest possible transfer when only
the wording matters.

## How it encodes

Two encodings share one container.

**Structured** keeps text as text: each span carries its bounding box, size,
colour and a coarse font classification, and images are extracted once by xref
then re-placed by reference. Glyphs never become pixels, so this is far more
compact for a document that was born digital.

**Page image** flattens each page to a single JPEG. Larger, but it is the only
thing that works for a scan, and the only path that composites ink masks and
honours page rotation the way a viewer does.

The choice is not left to the operator. A scanned prefectural order looks like
a PDF but behaves like a photograph; encoding it structurally produces a page
rotated 90° on a black background. `detect_optimal_mode` looks for rotation
and for pages that are mostly image with almost no text, and switches to page
image mode on its own, logging why.

Three quirks of real-world documents are handled explicitly, because each one
produced visibly broken output before:

- LibreOffice writes the `ti` and `tt` ligatures as non-standard code points
  (U+019F, U+01A9) that arrive as replacement characters. They are decomposed
  on extraction; where the glyph is already lost, the substitution is guessed
  from context.
- Microsoft's PDF chain draws frames as multi-segment paths. Taking a path's
  overall bounding box instead of walking its sub-items paints a full-page
  black rectangle, so sub-items are walked.
- A checkbox tick lives in the widget's `/AP /N` appearance stream, which
  neither `get_text` nor `get_drawings` reports. Ticked boxes are collected
  separately and redrawn as a geometric polyline, with no font dependency.

## Where the bytes go

On a dense six-page report, text accounts for about 91% of the compressed
manifest; vector rectangles compress to almost nothing because LZMA already
exploits their repetition. Optimising anything other than the text encoding is
therefore wasted effort.

Four reductions are applied, and none of them changes the format. A decoder
written against the original specification reads these archives unmodified,
because the manifest has always been read with `.get(key, default)`:

| Change | Effect |
|---|---|
| Omit span keys equal to their default (`f`, `fa`, `c`) | −2% |
| Omit the span box on a single-span line | −18% |
| LZMA2 `pb=0` with the extreme match finder | −2% |
| Whole-point coordinates on the two lowest presets | −6% |

Measured end to end on a six-page report: **5,223 → 4,116 bytes at `medium`
(−21%)**, and **3,812 bytes at `low` (−27%)**.

The single-span case matters most because a lone span covers exactly its line,
so its box was stored twice in the archive. The reader already falls back to
the line box when a span has none.

Whole-point coordinates were checked rather than assumed: a twelve-row table
in 7 pt text rebuilds with no cell overflow and no line overlap. Half-point
rounding was measured too and turned out *worse* than no rounding at all — it
appends `.5` to values that were previously whole numbers.

Page-image mode gains separately: JPEG is written progressively, which is
identical pixel-for-pixel and five to nine percent smaller, and a page with no
meaningful colour has its chroma planes dropped. A scanned order in black ink
comes out about 14% smaller with nothing lost.

## Archive format

Little-endian throughout.

```
[0:4]   magic b'PSDI'
[4:6]   uint16  version

version 1 - structured
    uint32  crc32 of the uncompressed manifest
    uint32  compressed manifest length
    bytes   manifest, JSON, LZMA raw stream, single LZMA2 filter
    uint16  image count
    per image:  uint16 xref | uint8 flags | uint32 length | bytes payload

version 2 - page image
    uint16  page count
    per page:   float32 width | float32 height | uint8 flags | uint32 length | bytes
```

Flags bit 0 marks an LZMA-compressed payload. It is not always set: JPEG data
is already entropy-coded and LZMA usually inflates it, so each payload is
stored whichever way came out smaller.

This layout is an interoperability contract with stations on the air. Do not
reorder or resize fields.

## Building for Windows

```
pip install -r requirements.txt pyinstaller
build.cmd full
```

`build.cmd` alone builds only the application; `full` also compiles the
installer. Output lands in `dist\PDFteleporter\` and `dist\installer\`.

Two executables come out of one PyInstaller analysis:

| | |
|---|---|
| `PDFteleporter.exe` | windowed — the GUI, and what the context menu launches |
| `psditool.exe` | console — so the command line can actually print |

A windowed executable has no stdout on Windows, so driving the CLI through
`PDFteleporter.exe` from a terminal would silently produce nothing. The console
twin costs a few hundred kilobytes and removes that trap.

The spec names the packages to exclude rather than trusting the build
environment. PyInstaller bundles whatever it finds importable, and the original
release shipped 846 MB unpacked — 330 MB of it torch — none of it reachable
from this code. `build.cmd` warns if the output exceeds 250 MB, which is the
symptom of an exclusion that no longer covers something newly installed.

### If the build launches with "No module named 'pymupdf'"

The build environment did not have PyMuPDF, so it was never bundled. PyInstaller
downgrades a missing hidden import to a warning and finishes successfully, so
the failure only surfaces when the executable is launched on a clean machine.

Almost always this is two interpreters: the dependencies were installed into one
Python and PyInstaller ran under another. The bare `pyinstaller` command
resolves through `PATH` and may not be the interpreter you think it is. Use
`python -m PyInstaller` so both resolve to the same place.

Both are now guarded. The spec refuses to build if PyMuPDF, Pillow or PyQt6 is
not importable, naming the interpreter it checked, and `build.cmd` verifies
after the build that `_internal\pymupdf` actually exists before declaring
success. Since PyMuPDF ships no PyInstaller hook and PyInstaller bundles none
for it, its compiled extensions are collected explicitly rather than left to
automatic detection.

### Code signing and SmartScreen

Version metadata does **not** satisfy SmartScreen. The two are unrelated:
Windows Defender SmartScreen checks for an Authenticode signature and the
reputation attached to the signing certificate. An unsigned executable gets the
blue *"Windows protected your PC"* screen on first run no matter how complete
its version resource is, and the operator has to click *More info → Run anyway*.

What the version resource *does* buy: a proper Properties dialog, a sane entry
in Add/Remove Programs, recognisable output for corporate software inventories,
and one fewer heuristic flag for antivirus engines. Worth having, but not the
answer to this particular question.

The real options, roughly in order of cost:

1. **Nothing.** Document the *More info → Run anyway* click. Perfectly workable
   for a tool distributed to a known group who were told it was coming.
2. **Self-signed certificate deployed to Trusted Publishers by group policy.**
   Free, and clean inside an organisation that manages its own machines.
   Useless for anyone downloading the file from outside it.
3. **OV code-signing certificate** (~€200–400/year). Since the 2023 CA/Browser
   Forum baseline the private key must live on a hardware token or a cloud HSM,
   which complicates automated builds. SmartScreen reputation is *not*
   immediate — it accrues as copies are downloaded and run, so early users may
   still see the warning.
4. **EV code-signing certificate** (~€400–700/year). Grants SmartScreen
   reputation immediately. This is the only option that removes the warning
   from the first download onwards.

Signing is wired in but disabled by default. Set `SIGNTOOL` and `SIGN_ARGS` in
the environment and `build.cmd` signs both executables before packaging:

```
set SIGNTOOL=C:\Program Files (x86)\Windows Kits\10\bin\x64\signtool.exe
set SIGN_ARGS=/fd sha256 /tr http://timestamp.digicert.com /td sha256 /a
build.cmd full
```

For the installer itself, configure a Sign Tool in the Inno Setup IDE and
uncomment `SignTool` and `SignedUninstaller` in the `.iss`. `SignedUninstaller`
is not optional if you sign at all: the uninstaller is generated on the target
machine at install time, so signing the installer beforehand does not cover it.

Two build choices already reduce false positives regardless of signing: the
build is one-folder rather than one-file, because one-file executables unpack
themselves into `%TEMP%` at every launch and that behaviour is itself a
heuristic trigger; and UPX compression is off, since several engines flag
UPX-packed binaries on sight.

### Installer

`installer\pdfteleporter.iss` requires **Inno Setup 7**. It uses
`SetupArchitecture=x64`, which does not exist in Inno Setup 6 — that version
fails with an unknown-directive error rather than quietly building something
else.

Notable choices:

- `ArchitecturesAllowed` defaults to `x64compatible` (set implicitly by
  `SetupArchitecture`), which matches Arm64 Windows 11 running x64 under
  emulation. The bare `x64` identifier is deprecated and would exclude those
  machines.
- `PrivilegesRequired=lowest` with `PrivilegesRequiredOverridesAllowed=dialog`,
  so an operator without administrator rights can still install. The
  application needs none: it writes only under `HKEY_CURRENT_USER`.
- `MinVersion=10.0`, because PyQt6 will not start on Windows 7 and the Inno
  default of `6.1sp1` would let Setup run anyway.
- `WizardStyle=modern dynamic`, so the installer follows the system light/dark
  setting like the application does.
- The context-menu registration scope follows the install mode. A per-user
  install writes under `HKEY_CURRENT_USER`; an administrative install is
  elevated, so `HKEY_CURRENT_USER` would be the *administrator's* hive rather
  than the operator's — the menu would be registered for an account nobody
  uses, and an elevated uninstall could never find the operator's keys to
  remove them. Administrative installs therefore write under
  `HKEY_LOCAL_MACHINE`, which every account sees.

  The obvious-looking alternative, `runasoriginaluser`, does not work: it is
  valid only in `[Run]`, and there is no equivalent for `[UninstallRun]` — the
  Pascal `ExecAsOriginalUser` is explicitly unsupported at uninstall time.
  Selecting the scope by install mode solves both halves at once.
- The uninstaller sweeps both scopes before deleting files, so Explorer is not
  left with entries pointing at a deleted executable, and keys from an earlier
  install in the other mode are cleared too.

## Licence

GNU GPL v3, inherited from the original implementation. PyMuPDF is AGPL v3.
