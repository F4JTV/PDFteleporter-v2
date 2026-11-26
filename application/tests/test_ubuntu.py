# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
"""The Ubuntu installer script: it parses, and its launcher's command line
reads back as written.

Run wherever bash is found -- Git for Windows brings one -- and skipped
elsewhere.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "install-ubuntu.sh")
BASH = shutil.which("bash")


def _read_exec_argument(value: str) -> str:
    """One quoted argument of a desktop entry's Exec key, read as the
    Desktop Entry Specification says: the string escapes first, then the
    quoting, in which \\ " ` $ must be escaped and nothing else may be, then
    the field codes, of which only %% may remain."""
    unescaped = re.sub(r"\\(.)", lambda m: {"s": " ", "n": "\n", "t": "\t", "r": "\r"}
                       .get(m.group(1), m.group(1)), value)
    if not (len(unescaped) >= 2 and unescaped[0] == unescaped[-1] == '"'):
        raise ValueError(f"not one quoted argument: {unescaped!r}")
    out, chars = [], iter(unescaped[1:-1])
    for char in chars:
        if char == "\\":
            escaped = next(chars, "")
            if escaped not in ('"', "`", "$", "\\"):
                raise ValueError(f"\\{escaped} is no escape inside quotes")
            out.append(escaped)
        elif char in ('"', "`", "$"):
            raise ValueError(f"{char} left unescaped inside quotes")
        elif char == "%":
            if next(chars, "") != "%":
                raise ValueError("a field code a launcher would replace")
            out.append("%")
        else:
            out.append(char)
    return "".join(out)


@unittest.skipUnless(BASH, "bash is not available")
class TestUbuntuInstaller(unittest.TestCase):
    def test_the_script_parses(self):
        result = subprocess.run([BASH, "-n", SCRIPT], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_exec_arguments_read_back_as_written(self):
        # A path with spaces, accents or quotes must reach Python whole: the
        # desktop entry's Exec key has two layers of escaping and its own %.
        with open(SCRIPT, encoding="utf-8") as handle:
            function = re.search(r"^exec_arg\(\) \{.*?^\}", handle.read(), re.M | re.S).group(0)
        samples = [
            "/home/u/.local/share/pdfteleporter/launch.py",
            "/home/u/Mes Applis/PDF Téléporteur/launch.py",
            'a"b', "a$b `c` $(d)", r"back\slash \\double", "100% %f", "it's",
            "semi;colon & pipe | <x> #h ~t *?",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                result = subprocess.run(
                    [BASH, "-c", function + '\nexec_arg "$1"', "bash", sample],
                    capture_output=True, text=True, encoding="utf-8", check=True)
                self.assertEqual(_read_exec_argument(result.stdout), sample)


if __name__ == "__main__":
    unittest.main()
