# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Explorer integration: the registry layout and the command lines it writes.

The registry itself cannot be exercised off Windows, so these tests cover the
parts that are pure logic: which commands are generated, that the submenu
entries are ordered and complete, and that every command the menu can produce
is one the CLI actually accepts. That last check is the one that matters --
a menu entry invoking an argument the parser rejects would fail silently on an
operator's machine, with no console to show the error.
"""

from __future__ import annotations

import unittest

from psditool import shell_windows
from psditool.cli import build_parser
from psditool.presets import QUALITY_ORDER


class TestQualitySubmenu(unittest.TestCase):
    def test_every_preset_appears_once(self):
        offered = [quality for quality, _ in shell_windows.QUALITY_MENU]
        self.assertEqual(sorted(offered), sorted(QUALITY_ORDER))

    def test_entries_run_from_fastest_to_slowest(self):
        # Explorer sorts submenu keys by name, and the numeric prefix is what
        # fixes the order. Fastest transfer first is what an operator under
        # time pressure reaches for.
        offered = [quality for quality, _ in shell_windows.QUALITY_MENU]
        self.assertEqual(offered, list(QUALITY_ORDER))

    def test_labels_are_present_and_distinct(self):
        labels = [label for _, label in shell_windows.QUALITY_MENU]
        self.assertEqual(len(set(labels)), len(labels))
        for label in labels:
            self.assertTrue(label.strip())


class TestGeneratedCommands(unittest.TestCase):
    def test_compress_command_carries_the_quality(self):
        command = shell_windows._command("compress", "low")
        self.assertIn("--compress", command)
        self.assertIn('"%1"', command)
        self.assertIn("--quality low", command)

    def test_rebuild_command_has_no_quality(self):
        command = shell_windows._command("rebuild")
        self.assertIn("--rebuild", command)
        self.assertNotIn("--quality", command)

    def test_the_file_placeholder_is_quoted(self):
        # Paths under Documents contain spaces; an unquoted %1 would arrive
        # split across several arguments.
        for quality, _ in shell_windows.QUALITY_MENU:
            self.assertIn('"%1"', shell_windows._command("compress", quality))


class TestCliAcceptsMenuCommands(unittest.TestCase):
    """Every command the menu can emit must parse."""

    @staticmethod
    def _arguments(command: str) -> list[str]:
        # Reduce a registry command string to the arguments the tool receives.
        import shlex

        parts = shlex.split(command, posix=False)
        out = []
        seen_flag = False
        for part in parts:
            if part.startswith("--"):
                seen_flag = True
            if seen_flag:
                out.append(part.strip('"'))
        return out

    def test_submenu_commands_are_understood(self):
        from psditool.cli import QUALITY_ORDER as cli_qualities

        for quality, _ in shell_windows.QUALITY_MENU:
            with self.subTest(quality=quality):
                args = self._arguments(
                    shell_windows._command("compress", quality)
                )
                self.assertEqual(args[0], "--compress")
                self.assertEqual(args[2], "--quality")
                self.assertIn(args[3], cli_qualities)

    def test_shell_subcommand_exposes_both_scopes(self):
        parser = build_parser()
        namespace = parser.parse_args(["shell", "install", "--scope", "machine"])
        self.assertEqual(namespace.scope, "machine")
        namespace = parser.parse_args(["shell", "uninstall"])
        self.assertEqual(namespace.action, "uninstall")

    def test_registry_paths_are_relative_to_a_root(self):
        # They are written under HKCU or HKLM depending on install mode, so
        # none of them may start with a hive name of its own.
        for path in (shell_windows.PDF_VERB_KEY, shell_windows.PSDI_EXT_KEY,
                     shell_windows.PSDI_PROGID_KEY, shell_windows.PSDI_VERB_KEY):
            self.assertFalse(path.upper().startswith("HKEY"))
            self.assertTrue(path.startswith("Software\\Classes"))


class FakeRegistry:
    """An in-memory stand-in for winreg, enough to exercise install().

    The cascading-submenu bug could not be caught by inspecting command
    strings: the commands were right, the key *shape* was wrong, because a
    stale "command" subkey from an earlier version made Explorer treat the
    parent as an ordinary verb. Only a test that looks at the tree install()
    leaves behind can see that.
    """

    HKEY_CURRENT_USER = "HKCU"
    HKEY_LOCAL_MACHINE = "HKLM"
    REG_SZ = 1

    def __init__(self):
        self.keys: dict[tuple[str, str], dict] = {}

    # -- winreg surface --------------------------------------------------
    def CreateKey(self, root, path):  # noqa: N802
        # winreg creates every intermediate key on the way down. Omitting them
        # here made _delete_tree loop forever on a child it could not open.
        parts = path.split("\\")
        for depth in range(1, len(parts) + 1):
            self.keys.setdefault((root, "\\".join(parts[:depth])), {})
        return _FakeKey(self, root, path)

    def OpenKey(self, root, path):  # noqa: N802
        if (root, path) not in self.keys:
            raise FileNotFoundError(path)
        return _FakeKey(self, root, path)

    def CloseKey(self, key):  # noqa: N802
        pass

    def SetValueEx(self, key, name, _reserved, _type, value):  # noqa: N802
        self.keys[(key.root, key.path)][name] = value

    def EnumKey(self, key, index):  # noqa: N802
        prefix = key.path + "\\"
        children = sorted({
            path[len(prefix):].split("\\")[0]
            for (root, path) in self.keys
            if root == key.root and path.startswith(prefix)
        })
        if index >= len(children):
            raise OSError("no more items")
        return children[index]

    def DeleteKey(self, root, path):  # noqa: N802
        if (root, path) not in self.keys:
            raise FileNotFoundError(path)
        if any(p.startswith(path + "\\") for (r, p) in self.keys if r == root):
            raise OSError("key has subkeys")
        del self.keys[(root, path)]

    # -- helpers ---------------------------------------------------------
    def subkeys_of(self, root, path) -> set:
        prefix = path + "\\"
        return {
            p[len(prefix):].split("\\")[0]
            for (r, p) in self.keys if r == root and p.startswith(prefix)
        }


class _FakeKey:
    def __init__(self, registry, root, path):
        self.registry = registry
        self.root = root
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _FakeWinreg:
    """Mixin: run each test against a fresh FakeRegistry posing as winreg."""

    def setUp(self):
        import sys

        self.fake = FakeRegistry()
        self._saved = sys.modules.get("winreg")
        sys.modules["winreg"] = self.fake
        self._supported = shell_windows.is_supported
        shell_windows.is_supported = lambda: True

    def tearDown(self):
        import sys

        shell_windows.is_supported = self._supported
        if self._saved is None:
            sys.modules.pop("winreg", None)
        else:
            sys.modules["winreg"] = self._saved


class TestRegistryShape(_FakeWinreg, unittest.TestCase):
    def _install(self):
        shell_windows.install(shell_windows.SCOPE_USER)

    def test_pdf_verb_is_a_cascade_not_a_command(self):
        self._install()
        key = self.fake.keys[("HKCU", shell_windows.PDF_VERB_KEY)]
        self.assertIn("MUIVerb", key)
        self.assertIn("SubCommands", key)
        self.assertEqual(key["SubCommands"], "")
        # A default value or a command subkey turns the cascade back into an
        # ordinary verb, and the submenu silently never opens.
        self.assertIsNone(key.get(None))
        self.assertNotIn(
            "command",
            {name.lower() for name in
             self.fake.subkeys_of("HKCU", shell_windows.PDF_VERB_KEY)},
        )

    def test_submenu_has_one_command_per_quality(self):
        self._install()
        entries = self.fake.subkeys_of(
            "HKCU", shell_windows.PDF_VERB_KEY + "\\shell"
        )
        self.assertEqual(len(entries), len(shell_windows.QUALITY_MENU))
        for entry in entries:
            path = f"{shell_windows.PDF_VERB_KEY}\\shell\\{entry}\\command"
            self.assertIn(("HKCU", path), self.fake.keys)
            self.assertIn("--quality", self.fake.keys[("HKCU", path)][None])

    def test_reinstalling_over_a_plain_verb_clears_it(self):
        # Exactly the upgrade path that broke: a previous version had written
        # a command under the PDF verb.
        stale = shell_windows.PDF_VERB_KEY + "\\command"
        self.fake.keys[("HKCU", stale)] = {None: "old.exe --compress \"%1\""}
        self.fake.keys[("HKCU", shell_windows.PDF_VERB_KEY)] = {None: "Ancien"}

        self._install()

        self.assertNotIn(("HKCU", stale), self.fake.keys)
        key = self.fake.keys[("HKCU", shell_windows.PDF_VERB_KEY)]
        self.assertIsNone(key.get(None))
        self.assertEqual(key["SubCommands"], "")

    def test_psdi_verb_stays_a_plain_command(self):
        self._install()
        path = shell_windows.PSDI_VERB_KEY + "\\command"
        self.assertIn(("HKCU", path), self.fake.keys)
        self.assertIn("--rebuild", self.fake.keys[("HKCU", path)][None])

    def test_uninstall_removes_everything(self):
        self._install()
        shell_windows.uninstall()
        leftovers = [p for (r, p) in self.fake.keys if "PDFteleporter" in p
                     or p.endswith(".psdi")]
        self.assertFalse(leftovers, f"clés restantes : {leftovers}")


class TestImageVerbs(_FakeWinreg, unittest.TestCase):
    def test_every_image_type_gets_the_same_cascade(self):
        shell_windows.install(shell_windows.SCOPE_USER)
        for extension in shell_windows.IMAGE_EXTENSIONS_FOR_MENU:
            with self.subTest(extension=extension):
                verb = shell_windows._image_verb_key(extension)
                key = self.fake.keys[("HKCU", verb)]
                self.assertEqual(key["SubCommands"], "")
                entries = self.fake.subkeys_of("HKCU", verb + "\\shell")
                self.assertEqual(len(entries), len(shell_windows.QUALITY_MENU))

    def test_image_menu_extensions_match_the_engine(self):
        # The menu must not offer a type the engine refuses, nor miss one it
        # accepts.
        from psditool import engine

        self.assertEqual(sorted(shell_windows.IMAGE_EXTENSIONS_FOR_MENU),
                         sorted(engine.IMAGE_EXTENSIONS))

    def test_uninstall_clears_image_verbs_too(self):
        shell_windows.install(shell_windows.SCOPE_USER)
        shell_windows.uninstall()
        leftovers = [p for (_r, p) in self.fake.keys if "PDFteleporterCompress" in p]
        self.assertFalse(leftovers, leftovers)


class TestTwoArchiveFormats(_FakeWinreg, unittest.TestCase):
    """Each compressible type offers both formats, and the menu says which."""

    def _install(self):
        shell_windows.install(shell_windows.SCOPE_USER)

    def _cascades(self):
        verbs = [(shell_windows.PDF_VERB_KEY, False),
                 (shell_windows.PDF_COMPACT_VERB_KEY, True)]
        for extension in shell_windows.IMAGE_EXTENSIONS_FOR_MENU:
            verbs.append((shell_windows._image_verb_key(extension), False))
            verbs.append((shell_windows._image_compact_verb_key(extension), True))
        return verbs

    def _entry_commands(self, verb_key):
        entries = sorted(self.fake.subkeys_of("HKCU", verb_key + "\\shell"))
        return [self.fake.keys[("HKCU", f"{verb_key}\\shell\\{entry}\\command")][None]
                for entry in entries]

    def test_every_compressible_type_offers_both_cascades(self):
        self._install()
        for verb_key, compact in self._cascades():
            with self.subTest(verb=verb_key):
                key = self.fake.keys[("HKCU", verb_key)]
                expected = (shell_windows.PDF_COMPACT_VERB_LABEL if compact
                            else shell_windows.PDF_VERB_LABEL)
                self.assertEqual(key["MUIVerb"], expected)
                self.assertEqual(key["SubCommands"], "")
                self.assertIsNone(key.get(None))
                self.assertNotIn("command", self.fake.subkeys_of("HKCU", verb_key))
                commands = self._entry_commands(verb_key)
                self.assertEqual(len(commands), len(shell_windows.QUALITY_MENU))

    def test_only_the_compact_cascade_asks_for_compact(self):
        # The flag is the whole difference between the two menus. Missing on
        # the compact side, the operator gets a .psdi while believing it is
        # compact; present on the other, a station running F1GBD's
        # PDFteleporter receives a file it cannot open.
        self._install()
        for verb_key, compact in self._cascades():
            for (quality, _label), command in zip(shell_windows.QUALITY_MENU,
                                                  self._entry_commands(verb_key)):
                with self.subTest(verb=verb_key, quality=quality):
                    self.assertIn(f"--quality {quality}", command)
                    self.assertEqual("--compact" in command, compact)

    def test_labels_name_the_format(self):
        self.assertIn(".psdi", shell_windows.PDF_VERB_LABEL)
        self.assertIn("F1GBD", shell_windows.PDF_VERB_LABEL)
        self.assertIn(".psdz", shell_windows.PDF_COMPACT_VERB_LABEL)
        self.assertNotIn(".psdz", shell_windows.PDF_VERB_LABEL)
        self.assertNotIn(".psdi", shell_windows.PDF_COMPACT_VERB_LABEL)

    def test_the_compatible_entry_comes_first(self):
        # Explorer lists static verbs in key-name order.
        compatible = shell_windows.PDF_VERB_KEY.rsplit("\\", 1)[1]
        compact = shell_windows.PDF_COMPACT_VERB_KEY.rsplit("\\", 1)[1]
        self.assertLess(compatible, compact)

    def test_psdz_files_have_a_type_of_their_own(self):
        self._install()
        keys = self.fake.keys
        self.assertEqual(keys[("HKCU", shell_windows.PSDZ_EXT_KEY)][None],
                         shell_windows.COMPACT_PROGID)
        self.assertEqual(keys[("HKCU", shell_windows.PSDZ_PROGID_KEY)][None],
                         shell_windows.PSDZ_TYPE_NAME)
        self.assertIn(("HKCU", shell_windows.PSDZ_PROGID_KEY + "\\DefaultIcon"), keys)
        # The .psdi association is left exactly as before.
        self.assertEqual(keys[("HKCU", shell_windows.PSDI_EXT_KEY)][None],
                         shell_windows.PROGID)
        self.assertEqual(shell_windows.PROGID, "PDFteleporter.psdi")
        for verb_key in (shell_windows.PSDI_VERB_KEY, shell_windows.PSDZ_VERB_KEY):
            with self.subTest(verb=verb_key):
                command = keys[("HKCU", verb_key + "\\command")][None]
                self.assertIn('--rebuild "%1"', command)
                # The archive says which format it is; nothing to pass.
                self.assertNotIn("--compact", command)

    def test_uninstall_leaves_neither_format_behind(self):
        self._install()
        shell_windows.uninstall()
        leftovers = [path for (_root, path) in self.fake.keys
                     if "PDFteleporter" in path
                     or path.endswith((".psdi", ".psdz"))]
        self.assertFalse(leftovers, leftovers)

    def test_installed_scopes_sees_the_menu(self):
        # Until 2.5.0 this looked for a command under the PDF verb, which is a
        # cascade and has none, so an installed menu always read as absent.
        self.assertEqual(shell_windows.installed_scopes(), [])
        shell_windows.install(shell_windows.SCOPE_USER)
        self.assertEqual(shell_windows.installed_scopes(), [shell_windows.SCOPE_USER])
        self.assertTrue(shell_windows.is_installed())
        shell_windows.install(shell_windows.SCOPE_MACHINE)
        self.assertEqual(shell_windows.installed_scopes(),
                         [shell_windows.SCOPE_USER, shell_windows.SCOPE_MACHINE])
        shell_windows.uninstall()
        self.assertEqual(shell_windows.installed_scopes(), [])
        self.assertFalse(shell_windows.is_installed())

    def test_every_written_command_reaches_the_cli_intact(self):
        # Feed each command line the menu registers to the real entry point,
        # with the actions replaced by spies, and check that the quality and
        # the format arrive. A command the shorthand misreads would fail on
        # the operator's machine with no console to show why.
        import shlex
        from unittest import mock

        from psditool import cli

        self._install()
        calls = []
        compress = mock.Mock(side_effect=lambda *a: calls.append(("compress",) + a) or 0)
        rebuild = mock.Mock(side_effect=lambda *a: calls.append(("rebuild",) + a) or 0)
        commands = []
        for verb_key, compact in self._cascades():
            for (quality, _label), command in zip(shell_windows.QUALITY_MENU,
                                                  self._entry_commands(verb_key)):
                commands.append((command, ("compress", quality, compact)))
        for verb_key in (shell_windows.PSDI_VERB_KEY, shell_windows.PSDZ_VERB_KEY):
            commands.append((self.fake.keys[("HKCU", verb_key + "\\command")][None],
                             ("rebuild",)))

        with mock.patch.object(cli, "cmd_compress", compress), \
                mock.patch.object(cli, "cmd_rebuild", rebuild):
            for command, expected in commands:
                with self.subTest(command=command):
                    parts = [part.strip('"') for part in shlex.split(command, posix=False)]
                    argv = parts[parts.index("--" + expected[0]):]
                    argv[1] = "C:\\Mes documents\\rapport.pdf"
                    calls.clear()
                    self.assertEqual(cli.main(argv), 0)
                    self.assertEqual(len(calls), 1)
                    action, target, *rest = calls[0]
                    self.assertEqual(action, expected[0])
                    self.assertEqual(target, "C:\\Mes documents\\rapport.pdf")
                    if action == "compress":
                        quality, _output, _skip, _quiet, compact = rest
                        self.assertEqual((quality, compact), expected[1:])


if __name__ == "__main__":
    unittest.main()
