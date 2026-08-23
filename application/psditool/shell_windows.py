"""Explorer context-menu integration for Windows.

Two verbs are registered:

    right-click a .pdf   -> Téléporter : compresser en .psdi
    right-click a .psdi  -> Téléporter : recomposer le PDF

The verb labels are French because they appear in the operator's Explorer;
everything else in this file stays in English like the rest of the code.

Registration has two scopes, and picking the right one is not cosmetic. A
per-user install writes under HKEY_CURRENT_USER and needs no elevation. An
all-users install runs elevated, so HKEY_CURRENT_USER would be the
administrator's hive rather than the operator's -- the menu would be
registered for an account nobody uses, and the uninstaller (also elevated)
could never find the operator's keys to remove them. All-users installs
therefore write under HKEY_LOCAL_MACHINE, which every account sees and an
elevated uninstall can clean up completely.

A note on Windows 11: entries registered this way land in the legacy menu,
which 11 hides behind "Show more options" (or Shift+F10). Getting into the
top-level menu requires a packaged MSIX shell extension, which needs signing
and a very different build. For an operational tool used a handful of times
per exercise, the extra click is the better trade.
"""

from __future__ import annotations

import os
import sys

SCOPE_USER = "user"
SCOPE_MACHINE = "machine"

PROGID = "PDFteleporter.psdi"
GUI_EXE_NAME = "PDFteleporter.exe"

# Relative to the chosen root; identical under HKCU and HKLM.
PDF_VERB_KEY = r"Software\Classes\SystemFileAssociations\.pdf\shell\PDFteleporterCompress"
PSDI_EXT_KEY = r"Software\Classes\.psdi"
PSDI_PROGID_KEY = rf"Software\Classes\{PROGID}"
PSDI_VERB_KEY = rf"{PSDI_PROGID_KEY}\shell\PDFteleporterRebuild"

PDF_VERB_LABEL = "Téléporter : compresser en .psdi"
PSDI_VERB_LABEL = "Téléporter : recomposer le PDF"


def is_supported() -> bool:
    return os.name == "nt"


def _root(scope: str):
    import winreg

    if scope == SCOPE_MACHINE:
        return winreg.HKEY_LOCAL_MACHINE
    return winreg.HKEY_CURRENT_USER


def _launcher() -> tuple[str, str]:
    """Return (executable, argument prefix) for invoking this tool.

    The registered command must always point at the windowed executable. The
    installer registers the menu by running the console twin
    (``psditool.exe shell install``), so taking sys.executable at face value
    would wire every right-click to the console build and flash a black window
    on each use.
    """
    if getattr(sys, "frozen", False):
        gui = os.path.join(os.path.dirname(sys.executable), GUI_EXE_NAME)
        return (gui if os.path.exists(gui) else sys.executable), ""

    executable = sys.executable
    windowless = os.path.join(os.path.dirname(executable), "pythonw.exe")
    if os.path.exists(windowless):
        executable = windowless

    package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return executable, f'"{os.path.join(package_root, "pdfteleporter.py")}"'


def _command(action: str) -> str:
    executable, prefix = _launcher()
    parts = [f'"{executable}"']
    if prefix:
        parts.append(prefix)
    parts.extend([f"--{action}", '"%1"'])
    return " ".join(parts)


def _icon() -> str:
    executable, _ = _launcher()
    return executable


def install(scope: str = SCOPE_USER) -> None:
    """Register both verbs. Safe to run repeatedly; it overwrites in place."""
    if not is_supported():
        raise RuntimeError("Context-menu integration is Windows-only")

    import winreg

    root = _root(scope)

    with winreg.CreateKey(root, PDF_VERB_KEY) as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, PDF_VERB_LABEL)
        winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, _icon())
    with winreg.CreateKey(root, PDF_VERB_KEY + r"\command") as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, _command("compress"))

    # Give .psdi a ProgID of its own so the file type has a name and an icon
    # in Explorer, not just a verb.
    with winreg.CreateKey(root, PSDI_EXT_KEY) as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, PROGID)
    with winreg.CreateKey(root, PSDI_PROGID_KEY) as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, "Archive PDF Teleporter")
    with winreg.CreateKey(root, PSDI_PROGID_KEY + r"\DefaultIcon") as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, f"{_icon()},0")

    with winreg.CreateKey(root, PSDI_VERB_KEY) as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, PSDI_VERB_LABEL)
        winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, _icon())
    with winreg.CreateKey(root, PSDI_VERB_KEY + r"\command") as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, _command("rebuild"))

    _notify_shell()


def _delete_tree(root, path: str) -> None:
    import winreg

    try:
        with winreg.OpenKey(root, path) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_tree(root, f"{path}\\{child}")
        winreg.DeleteKey(root, path)
    except FileNotFoundError:
        pass
    except PermissionError:
        # An all-users key reached without elevation. Report nothing: the
        # uninstaller runs elevated and will succeed there.
        pass


def uninstall(scope: str | None = None) -> None:
    """Remove every key this tool created.

    With no scope, both are swept. That is what an uninstaller wants: a
    machine may carry keys from an earlier per-user install alongside the
    current all-users one, and leaving either behind gives Explorer menu
    entries pointing at a deleted executable.
    """
    if not is_supported():
        raise RuntimeError("Context-menu integration is Windows-only")

    scopes = (SCOPE_USER, SCOPE_MACHINE) if scope is None else (scope,)
    for one in scopes:
        root = _root(one)
        _delete_tree(root, PDF_VERB_KEY)
        _delete_tree(root, PSDI_PROGID_KEY)
        _delete_tree(root, PSDI_EXT_KEY)
    _notify_shell()


def installed_scopes() -> list[str]:
    """Return the scopes in which the menu is currently registered."""
    if not is_supported():
        return []

    import winreg

    found = []
    for scope in (SCOPE_USER, SCOPE_MACHINE):
        try:
            winreg.CloseKey(
                winreg.OpenKey(_root(scope), PDF_VERB_KEY + r"\command")
            )
            found.append(scope)
        except OSError:
            pass
    return found


def is_installed() -> bool:
    return bool(installed_scopes())


def _notify_shell() -> None:
    """Tell Explorer to reload associations so the change is visible at once."""
    try:
        import ctypes

        SHCNE_ASSOCCHANGED = 0x08000000
        SHCNF_IDLIST = 0x0000
        ctypes.windll.shell32.SHChangeNotify(
            SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None
        )
    except Exception:  # noqa: BLE001
        pass
