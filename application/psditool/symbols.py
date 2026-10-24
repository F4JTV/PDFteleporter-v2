# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Unicode for the characters of the Symbol and Wingdings fonts.

Word sets its bullets and arrows in Symbol or Wingdings, fonts with no
Unicode meaning of their own. MuPDF reads such a character as its code in the
font moved into the private use area -- U+F0B7 for Symbol's bullet -- which
no other font has a glyph for: the rebuilt page showed a blank, or a box,
wherever the source had a bullet. Here the code becomes the character it
draws: Symbol's by its published encoding, Wingdings' for the shapes common
enough to be worth it, each by the Unicode character that looks like it.
"""

from __future__ import annotations

import re

# Adobe's Symbol encoding: code -> Unicode, for the codes whose glyph is not
# simply the ASCII character of the same code.
_SYMBOL = {
    0x22: "∀", 0x24: "∃", 0x27: "∋", 0x2a: "∗", 0x2d: "−",
    0x40: "≅",
    0x41: "Α", 0x42: "Β", 0x43: "Χ", 0x44: "Δ", 0x45: "Ε",
    0x46: "Φ", 0x47: "Γ", 0x48: "Η", 0x49: "Ι", 0x4a: "ϑ",
    0x4b: "Κ", 0x4c: "Λ", 0x4d: "Μ", 0x4e: "Ν", 0x4f: "Ο",
    0x50: "Π", 0x51: "Θ", 0x52: "Ρ", 0x53: "Σ", 0x54: "Τ",
    0x55: "Υ", 0x56: "ς", 0x57: "Ω", 0x58: "Ξ", 0x59: "Ψ",
    0x5a: "Ζ", 0x5c: "∴", 0x5e: "⊥",
    0x61: "α", 0x62: "β", 0x63: "χ", 0x64: "δ", 0x65: "ε",
    0x66: "φ", 0x67: "γ", 0x68: "η", 0x69: "ι", 0x6a: "ϕ",
    0x6b: "κ", 0x6c: "λ", 0x6d: "μ", 0x6e: "ν", 0x6f: "ο",
    0x70: "π", 0x71: "θ", 0x72: "ρ", 0x73: "σ", 0x74: "τ",
    0x75: "υ", 0x76: "ϖ", 0x77: "ω", 0x78: "ξ", 0x79: "ψ",
    0x7a: "ζ", 0x7e: "∼",
    0xa0: "€", 0xa1: "ϒ", 0xa2: "′", 0xa3: "≤", 0xa4: "⁄",
    0xa5: "∞", 0xa6: "ƒ", 0xa7: "♣", 0xa8: "♦", 0xa9: "♥",
    0xaa: "♠", 0xab: "↔", 0xac: "←", 0xad: "↑", 0xae: "→",
    0xaf: "↓", 0xb0: "°", 0xb1: "±", 0xb2: "″", 0xb3: "≥",
    0xb4: "×", 0xb5: "∝", 0xb6: "∂", 0xb7: "•", 0xb8: "÷",
    0xb9: "≠", 0xba: "≡", 0xbb: "≈", 0xbc: "…", 0xbd: "⏐",
    0xbe: "⎯", 0xbf: "↵",
    0xc0: "ℵ", 0xc1: "ℑ", 0xc2: "ℜ", 0xc3: "℘", 0xc4: "⊗",
    0xc5: "⊕", 0xc6: "∅", 0xc7: "∩", 0xc8: "∪", 0xc9: "⊃",
    0xca: "⊇", 0xcb: "⊄", 0xcc: "⊂", 0xcd: "⊆", 0xce: "∈",
    0xcf: "∉", 0xd0: "∠", 0xd1: "∇", 0xd2: "®", 0xd3: "©",
    0xd4: "™", 0xd5: "∏", 0xd6: "√", 0xd7: "⋅", 0xd8: "¬",
    0xd9: "∧", 0xda: "∨", 0xdb: "⇔", 0xdc: "⇐", 0xdd: "⇑",
    0xde: "⇒", 0xdf: "⇓",
    0xe0: "◊", 0xe1: "〈", 0xe2: "®", 0xe3: "©", 0xe4: "™",
    0xe5: "∑", 0xe6: "⎛", 0xe7: "⎜", 0xe8: "⎝", 0xe9: "⎡",
    0xea: "⎢", 0xeb: "⎣", 0xec: "⎧", 0xed: "⎨", 0xee: "⎩",
    0xef: "⎪",
    0xf1: "〉", 0xf2: "∫", 0xf3: "⌠", 0xf4: "⎮", 0xf5: "⌡",
    0xf6: "⎞", 0xf7: "⎟", 0xf8: "⎠", 0xf9: "⎤", 0xfa: "⎥",
    0xfb: "⎦", 0xfc: "⎫", 0xfd: "⎬", 0xfe: "⎭",
}
# ASCII codes Symbol draws as themselves.
_SYMBOL.update({code: chr(code) for code in range(0x20, 0x7f) if code not in _SYMBOL})

# Wingdings: the bullets, boxes, arrows and marks Word documents use, each by
# the Unicode character of the same shape. Codes not listed stay as they are.
_WINGDINGS = {
    0x21: "✏", 0x22: "✂", 0x28: "☎", 0x2a: "✉",
    0x45: "☜", 0x46: "☞", 0x4a: "☺", 0x4c: "☹", 0x4e: "☠",
    0x6c: "●", 0x6e: "■", 0x6f: "□", 0x71: "❑", 0x72: "❒",
    0x75: "◆", 0x76: "❖", 0x77: "⬥",
    0x9f: "•", 0xa7: "▪", 0xa8: "◻",
    0xd8: "➢", 0xdf: "←", 0xe0: "→", 0xe1: "↑", 0xe2: "↓",
    0xe8: "➔", 0xef: "⇦", 0xf0: "⇨", 0xf1: "⇧", 0xf2: "⇩",
    0xfb: "✗", 0xfc: "✓", 0xfd: "☒", 0xfe: "☑",
}


def _private(table: dict) -> dict:
    """The table keyed by the private-use codes MuPDF reports, U+F020 and up."""
    return {0xF000 + code: char for code, char in table.items()}


# Matched against the font's name without its subset tag: "Symbol",
# "SymbolMT", "Wingdings-Regular". Wingdings 2 and 3 draw other shapes at the
# same codes, and "Segoe UI Symbol" has Unicode of its own.
_TABLES = (
    (re.compile(r"wingdings(?![\s_-]*[23])", re.IGNORECASE), _private(_WINGDINGS)),
    (re.compile(r"symbol(?:mt)?\b", re.IGNORECASE), _private(_SYMBOL)),
)


def translation_for(font_name: str) -> dict | None:
    """The str.translate table for a font's private-use codes, or None."""
    name = font_name.split("+", 1)[-1] if "+" in font_name else font_name
    for pattern, table in _TABLES:
        if pattern.match(name):
            return table
    return None
