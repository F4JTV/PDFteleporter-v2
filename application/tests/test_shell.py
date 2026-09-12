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


if __name__ == "__main__":
    unittest.main()


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


class TestRegistryShape(unittest.TestCase):
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
