# libImaging from Pillow 13.0.0

These files are copied from Pillow 13.0.0 (`src/libImaging`, commit
293218ad059d994baf1777d882911bed6a49704a). They are the image algorithms
the desktop version of PDF Teleporter runs through Pillow: resampling,
reduction, quantisation, palette mapping, mode conversion, histograms and
pasting. The phone version runs the same code so that both make the same
decisions on the same pixels.

Pillow is distributed under the MIT-CMU (HPND) licence, reproduced in
`LICENSE-Pillow.txt`. It is compatible with the GPL-3.0 licence of
PDF Teleporter.

## Local changes

- `Python.h` is a shim, not CPython's header: it declares the few Python
  names the sources mention, and turns on the mutexes libImaging has for
  free-threaded Python, so that images can be made on several threads.
- `pillow_glue.c` provides what Pillow's `_imaging.c` gives libImaging:
  error reporting (kept per thread) and the section markers.
- The tag of `struct ImagingMemoryArena` is renamed
  `ImagingMemoryArenaInstance` (in `Imaging.h` and `Storage.c`), so that
  `Imaging.h` can be included from C++, where a struct tag and a typedef of
  the same name clash. Nothing else refers to the tag.

Only the files listed in `CMakeLists.txt` are built. The PNG writer, the
codecs and the Python-level logic around these functions (what
`Image.resize`, `Image.thumbnail` or `Image.convert` do before calling them)
are reimplemented in `../image.cpp` and `../codecs.cpp`.
