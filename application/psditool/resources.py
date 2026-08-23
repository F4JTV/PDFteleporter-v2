"""Locating bundled resources.

PyInstaller unpacks bundled data into a temporary directory whose path is
handed over in ``sys._MEIPASS``. Paths built relative to ``__file__`` work when
running from source and silently point nowhere in a frozen build, which is how
an application ends up running fine for the developer and showing a blank icon
for everyone else.
"""

from __future__ import annotations

import os
import sys


def resource_path(*parts: str) -> str:
    """Return the absolute path of a bundled resource."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def icon_path() -> str | None:
    """Return the application icon path, or None when it is missing.

    Returning None rather than a broken path lets the caller skip setting an
    icon instead of installing an empty one, which looks worse than the
    default.
    """
    path = resource_path("assets", "pdfteleporter.ico")
    return path if os.path.exists(path) else None
