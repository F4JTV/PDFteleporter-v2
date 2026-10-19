# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Semantic comparison of two CPython 3.13 .pyc files.

Compares instruction streams (opname + resolved argument, docstrings and
line numbers ignored) per code object, so a removed docstring or a renamed
module does not count as a change.
"""
import marshal
import sys
import types
import dis
import difflib


def load(path):
    with open(path, "rb") as fh:
        fh.read(16)
        return marshal.load(fh)


def walk(code, out):
    out[code.co_qualname] = code
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            walk(c, out)
    return out


def instr_list(code):
    res = []
    for i in dis.get_instructions(code):
        if i.opname in ("CACHE", "NOP", "RESUME", "EXTENDED_ARG"):
            continue
        arg = i.argrepr
        if isinstance(i.argval, types.CodeType):
            arg = f"<code {i.argval.co_qualname}>"
        res.append(f"{i.opname} {arg}")
    # Drop a leading docstring store/load pattern: LOAD_CONST '...' ; STORE_NAME __doc__
    cleaned = []
    skip = 0
    for k, s in enumerate(res):
        if skip:
            skip -= 1
            continue
        if s.startswith("LOAD_CONST") and k + 1 < len(res) and res[k + 1] == "STORE_NAME __doc__":
            skip = 1
            continue
        cleaned.append(s)
    return cleaned


def main(old_path, new_path):
    old = walk(load(old_path), {})
    new = walk(load(new_path), {})
    common = sorted(set(old) & set(new))
    print(f"code objects: old={len(old)} new={len(new)} common={len(common)}")
    print("only old:", sorted(set(old) - set(new)))
    print("only new:", sorted(set(new) - set(old)))
    same = diff = 0
    for name in common:
        a, b = instr_list(old[name]), instr_list(new[name])
        if a == b:
            same += 1
            continue
        diff += 1
        print(f"\n=== {name} ({len(a)} vs {len(b)} instructions)")
        for line in difflib.unified_diff(a, b, "old", "new", lineterm="", n=2):
            print("   ", line[:220])
    print(f"\nidentical: {same}   different: {diff}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
