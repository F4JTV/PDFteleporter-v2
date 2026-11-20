#!/usr/bin/env python3
# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Write legal/THIRD_PARTY_NOTICES.txt, the notices the About box shows.

The licence texts are copied from the sources the application is built
from (scripts/fetch-third-party.sh puts them under third_party/), never
retyped. Run again whenever a pinned version changes:

    python3 tools/make_notices.py

libaom and libyuv are fetched under third_party/libavif/ext for the Android
build only. Without them, their texts are taken from the Debian copyright
files of an Ubuntu system (packages libaom3 and libyuv0), which reproduce
the upstream LICENSE and PATENTS files.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import textwrap

HERE = pathlib.Path(__file__).resolve().parent.parent

# Must match scripts/fetch-third-party.sh and the Android build.
VERSIONS = {
    "mupdf": "1.28.6",
    "libjpeg-turbo": "3.2.0",
    "zlib-ng": "2.3.3",
    "libwebp": "1.6.0",
    "libavif": "1.4.2",
    "aom": "3.14.1",
    "xz": "5.8.4",
    "pillow": "13.0.0",
    "qt": "6.12",
    "androidx-core": "1.17.0",
}


def read(path: pathlib.Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return text.replace("\r\n", "\n").strip("\n") + "\n"


def debian_field(copyright_file: pathlib.Path, header: str) -> str:
    """The text of the first Debian copyright field `header` that has one
    (a licence is named in each "Files" stanza, and written out once)."""
    lines = copyright_file.read_text(encoding="utf-8").splitlines()
    for start, line in enumerate(lines):
        if line.strip() != header:
            continue
        out: list[str] = []
        for body in lines[start + 1:]:
            if not body.startswith(" "):
                break
            body = body[1:]
            out.append("" if body.strip() == "." else body)
        if out:
            return "\n".join(out).strip("\n") + "\n"
    raise SystemExit(f"{copyright_file}: no '{header}' with a text")


def section(title: str, body: str) -> str:
    rule = "=" * 78
    return f"\n{rule}\n{title}\n{rule}\n\n{body}"


def ijg_legal(readme: pathlib.Path) -> str:
    """The LEGAL ISSUES part of the IJG README."""
    text = read(readme)
    match = re.search(r"LEGAL ISSUES\n=+\n(.*?)\n(?=[A-Z][A-Z ]+\n=+\n)", text, re.S)
    if not match:
        raise SystemExit(f"{readme}: LEGAL ISSUES not found")
    return "LEGAL ISSUES\n============\n" + match.group(1).strip("\n") + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--third-party", type=pathlib.Path, default=HERE / "third_party")
    parser.add_argument("--fonts", type=pathlib.Path, default=HERE.parent / "application/psditool/fonts")
    parser.add_argument("--pillow-license", type=pathlib.Path,
                        default=HERE / "src/imaging/pillow/LICENSE-Pillow.txt")
    parser.add_argument("--lgpl", type=pathlib.Path, default=pathlib.Path("/usr/share/common-licenses/LGPL-3"))
    parser.add_argument("--debian-doc", type=pathlib.Path, default=pathlib.Path("/usr/share/doc"))
    parser.add_argument("--out", type=pathlib.Path, default=HERE / "legal/THIRD_PARTY_NOTICES.txt")
    args = parser.parse_args()

    tp = args.third_party
    mupdf = tp / "mupdf"
    mupdf_third = mupdf / "thirdparty"
    ext = tp / "libavif/ext"
    v = VERSIONS

    # libaom: BSD 2 clauses and the AOMedia patent licence.
    if (ext / "aom/LICENSE").exists():
        aom_licence = read(ext / "aom/LICENSE")
        aom_patents = read(ext / "aom/PATENTS")
    else:
        deb = args.debian_doc / "libaom3/copyright"
        aom_licence = ("Copyright (c) 2016, Alliance for Open Media. All rights reserved.\n\n"
                       + debian_field(deb, "License: BSD-2-clause"))
        aom_patents = debian_field(deb, "Comment:")
        aom_patents = aom_patents.split("\n", 2)[2].lstrip("\n")   # "In addition to ... license:"
    if (ext / "libyuv/LICENSE").exists():
        yuv_licence = read(ext / "libyuv/LICENSE")
        if (ext / "libyuv/PATENTS").exists():
            yuv_licence += "\n" + read(ext / "libyuv/PATENTS")
    else:
        deb = args.debian_doc / "libyuv0/copyright"
        yuv_licence = ("Copyright 2011 The LibYuv Project Authors. All rights reserved.\n\n"
                       + debian_field(deb, "License: BSD-3-Clause") + "\n"
                       + debian_field(deb, "Comment:"))

    # The Apache licence, version 2.0, as Gumbo ships it.
    apache = read(mupdf_third / "gumbo-parser/COPYING")
    avif = read(tp / "libavif/LICENSE")
    avif_own = avif[:avif.index("-" * 78)].strip("\n") + "\n"

    table = textwrap.dedent(f"""\
        Composant                         Version   Licence
        --------------------------------  --------  ------------------------------------
        MuPDF (Artifex Software)          {v['mupdf']:<8}  GNU AGPL version 3
          FreeType                                  FreeType License (FTL)
          HarfBuzz                                  MIT
          Little CMS (lcms2mt)                      MIT
          OpenJPEG                                  BSD 2 clauses
          jbig2dec                                  GNU AGPL version 3
          libjpeg (Independent JPEG Group)          licence IJG
          zlib                                      licence zlib
          Brotli                                    MIT
          Gumbo                                     Apache 2.0
          polices Nimbus (URW), Noto, SIL           SIL Open Font License 1.1
          police Droid Sans Fallback                Apache 2.0
        libjpeg-turbo                     {v['libjpeg-turbo']:<8}  IJG, BSD 3 clauses, zlib
        zlib-ng                           {v['zlib-ng']:<8}  licence zlib
        libwebp                           {v['libwebp']:<8}  BSD 3 clauses, brevets WebM
        libavif                           {v['libavif']:<8}  BSD 2 clauses
        libaom (Android)                  {v['aom']:<8}  BSD 2 clauses, brevets AOMedia 1.0
        libyuv (Android)                            BSD 3 clauses
        XZ Utils (liblzma)                {v['xz']:<8}  BSD 0 clause
        Pillow, libImaging                {v['pillow']:<8}  MIT-CMU (HPND)
        Carlito (polices)                           SIL Open Font License 1.1
        Material Icons (Google)                     Apache 2.0
        Qt (The Qt Company)               {v['qt']:<8}  GNU LGPL version 3
        AndroidX Core (Android)           {v['androidx-core']:<8}  Apache 2.0
        """)

    out = [textwrap.dedent("""\
        PDF Teleporter - composants tiers
        =================================

        PDF Teleporter est un logiciel libre distribué sous licence GNU GPL
        version 3 (bouton « Licence » de la fenêtre À propos). Il incorpore les
        composants ci-dessous, chacun sous sa propre licence, dont le texte suit
        la liste. Les sources exactes de chacun, aux versions indiquées, sont
        celles que récupère scripts/fetch-third-party.sh dans le dépôt du
        programme : https://github.com/F4JTV/PDFteleporter-v2

        """), table]

    out.append(section("MuPDF - GNU Affero General Public License version 3",
                       "Copyright (C) Artifex Software, Inc. https://mupdf.com\n\n"
                       "MuPDF and jbig2dec are distributed under the following licence.\n\n"
                       + read(mupdf / "COPYING")))
    out.append(section("FreeType - The FreeType Project License",
                       read(mupdf_third / "freetype/docs/FTL.TXT")))
    out.append(section("HarfBuzz", read(mupdf_third / "harfbuzz/COPYING")))
    out.append(section("Little CMS", read(mupdf_third / "lcms2/LICENSE")))
    out.append(section("OpenJPEG", read(mupdf_third / "openjpeg/LICENSE")))
    out.append(section("libjpeg (Independent JPEG Group), in MuPDF",
                       ijg_legal(mupdf_third / "libjpeg/README")))
    out.append(section("zlib, in MuPDF", read(mupdf_third / "zlib/LICENSE")))
    out.append(section("Brotli", read(mupdf_third / "brotli/LICENSE")))
    out.append(section("Gumbo", "Copyright 2010 Google Inc. Licensed under the Apache License, "
                       "Version 2.0 (section \"Apache License 2.0\" below).\n"))
    out.append(section("Fonts in MuPDF: Nimbus (URW), Noto, Charis SIL",
                       read(mupdf / "resources/fonts/sil/OFL.txt")))
    out.append(section("libjpeg-turbo", read(tp / "libjpeg-turbo/LICENSE.md")))
    out.append(section("zlib-ng", read(tp / "zlib-ng/LICENSE.md")))
    out.append(section("libwebp", read(tp / "libwebp/COPYING") + "\n" + read(tp / "libwebp/PATENTS")))
    out.append(section("libavif", avif_own))
    out.append(section("libaom", aom_licence + "\n" + aom_patents))
    out.append(section("libyuv", yuv_licence))
    out.append(section("XZ Utils (liblzma)", read(tp / "xz/COPYING.0BSD")))
    out.append(section("Pillow (libImaging)", read(args.pillow_license)))
    out.append(section("Carlito", read(args.fonts / "OFL.txt")))
    out.append(section("Material Icons", "Copyright Google LLC. Licensed under the Apache License, "
                       "Version 2.0 (section \"Apache License 2.0\" below).\n"))
    out.append(section("Qt - GNU Lesser General Public License version 3",
                       "Copyright (C) The Qt Company Ltd. https://www.qt.io\n"
                       "The LGPL version 3 supplements the GNU GPL version 3, whose text the "
                       "\"Licence\" button shows.\n\n" + read(args.lgpl)))
    out.append(section("AndroidX Core", "Copyright The Android Open Source Project. Licensed under "
                       "the Apache License, Version 2.0 (section \"Apache License 2.0\" below).\n"))
    out.append(section("Apache License 2.0 (Gumbo, Droid Sans Fallback, Material Icons, AndroidX Core)",
                       apache))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(out), encoding="utf-8")
    print(f"{args.out}: {args.out.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
