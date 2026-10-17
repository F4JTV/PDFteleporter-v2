# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Licence and copyright notices.

GPL v3 makes notices the vehicle of attribution: section 4 obliges anyone who
redistributes the code to keep them intact, which is worthless if there are
none to keep. Section 5(d) obliges an interactive interface to show them.
These tests make sure a new file, a reworded About box or a version bump
cannot silently drop them.
"""

from __future__ import annotations

import ast
import contextlib
import io
import os
import re
import unittest

from psditool import (
    __copyright__, __original_author__, __source_url__, __version__,
)

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Content descending from F1GBD's implementation must keep naming him.
DERIVED_FILES = {
    "psditool/engine.py",
    "psditool/format.py",
    "psditool/presets.py",
    "tests/test_interop.py",
}


def _source_files() -> list[str]:
    found = []
    for root, dirs, files in os.walk(APP_DIR):
        dirs[:] = [d for d in dirs
                   if d not in ("__pycache__", "vectors", "assets", "dist", ".venv-build")]
        for name in files:
            rel = os.path.relpath(os.path.join(root, name), APP_DIR).replace(os.sep, "/")
            if rel.endswith((".py", ".spec", ".iss", ".cmd")) or (
                    rel.startswith("build/version_info") and rel.endswith(".txt")):
                found.append(rel)
    return sorted(found)


def _head(rel: str, lines: int = 12) -> str:
    with open(os.path.join(APP_DIR, rel), encoding="utf-8") as handle:
        return "".join(handle.readline() for _ in range(lines))


class TestSourceHeaders(unittest.TestCase):

    def test_every_source_file_carries_copyright_and_spdx(self):
        files = _source_files()
        self.assertGreater(len(files), 20, "the file walk found too little")
        for rel in files:
            head = _head(rel)
            with self.subTest(file=rel):
                self.assertIn("Copyright (C) 2026 The PDF Teleporter contributors", head)
                self.assertIn("SPDX-License-Identifier: GPL-3.0-only", head)

    def test_derived_files_keep_the_original_author(self):
        for rel in sorted(DERIVED_FILES):
            with self.subTest(file=rel):
                self.assertIn("F1GBD", _head(rel))

    def test_batch_file_keeps_echo_off_first_and_crlf(self):
        # A rem line before @echo off is echoed; LF-only batch files break
        # labels and goto in cmd.exe.
        with open(os.path.join(APP_DIR, "build.cmd"), "rb") as handle:
            data = handle.read()
        self.assertTrue(data.lower().startswith(b"@echo off\r\n"))
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))

    def test_python_headers_do_not_displace_docstrings(self):
        for rel in _source_files():
            if not rel.endswith(".py"):
                continue
            with open(os.path.join(APP_DIR, rel), encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            with self.subTest(file=rel):
                self.assertIsNotNone(ast.get_docstring(tree), "module docstring lost")


class TestDisplayedNotices(unittest.TestCase):

    def test_about_box_shows_the_appropriate_legal_notices(self):
        try:
            from psditool.gui.main_window import about_text
        except ImportError as exc:  # pragma: no cover - PyQt6 missing
            self.skipTest(f"PyQt6 unavailable: {exc}")
        text = about_text()
        self.assertIn(__copyright__, text)
        self.assertIn("F1GBD", text)
        self.assertIn("GNU GPL", text)
        self.assertIn("sans aucune garantie", text)
        self.assertIn("LICENSE.txt", text)
        self.assertIn(f'href="{__source_url__}"', text)

    def test_command_line_version_shows_the_notices_line_by_line(self):
        from psditool import cli

        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as stop:
            cli.build_parser().parse_args(["--version"])
        self.assertEqual(stop.exception.code, 0)
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[0], f"PDF Teleporter {__version__}")
        self.assertIn(__copyright__, lines)
        self.assertIn(f"Based on PDF Teleporter by {__original_author__}", lines)
        self.assertIn(f"Source: {__source_url__}", lines)
        self.assertTrue(any("NO WARRANTY" in line for line in lines))


class TestBuildMetadata(unittest.TestCase):
    """The installer and the executables' Properties tab say the same thing."""

    def _read(self, rel: str) -> str:
        with open(os.path.join(APP_DIR, rel), encoding="utf-8") as handle:
            return handle.read()

    def test_versions_agree(self):
        iss = self._read("installer/pdfteleporter.iss")
        self.assertIn(f'#define AppVersion     "{__version__}"', iss)
        numeric = ", ".join(__version__.split(".")) + ", 0"
        for rel in ("build/version_info.txt", "build/version_info_cli.txt"):
            info = self._read(rel)
            with self.subTest(file=rel):
                self.assertIn(f'"FileVersion", "{__version__}"', info)
                self.assertIn(f'"ProductVersion", "{__version__}"', info)
                self.assertEqual(len(re.findall(re.escape(f"({numeric})"), info)), 2)

    def test_installer_names_publisher_copyright_and_source(self):
        iss = self._read("installer/pdfteleporter.iss")
        self.assertIn('"The PDF Teleporter contributors"', iss)
        self.assertIn("AppCopyright=Copyright (C) 2026", iss)
        self.assertIn(f'#define AppURL         "{__source_url__}"', iss)
        self.assertIn("AppPublisherURL={#AppURL}", iss)

    def test_executables_carry_the_copyright(self):
        for rel in ("build/version_info.txt", "build/version_info_cli.txt"):
            info = self._read(rel)
            with self.subTest(file=rel):
                self.assertIn("Copyright (C) 2026 The PDF Teleporter contributors", info)
                self.assertIn("F1GBD", info)
                self.assertNotIn("PDF Teleporter project", info)

    def test_licence_text_is_the_unmodified_gpl(self):
        # The "How to Apply" template at the end belongs to the licence text
        # and must stay verbatim: notices go in the source files, not here.
        licence = self._read("LICENSE.txt")
        self.assertIn("GNU GENERAL PUBLIC LICENSE", licence)
        self.assertIn("Version 3, 29 June 2007", licence)
        self.assertIn("<year>  <name of author>", licence)


if __name__ == "__main__":
    unittest.main()
