#!/usr/bin/env bash
# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
#
# Fetches the third-party sources the build compiles, each at the version the
# desktop version runs on (PyMuPDF 2.1 for MuPDF, Pillow 13.0.0 for the image
# libraries): the archives the phone makes are then the desktop's, byte for
# byte. Every source comes from its project's own repository, at a tag whose
# commit is checked.
#
#   scripts/fetch-third-party.sh [--dest DIR] [--no-avif-deps]
#
# --no-avif-deps leaves out libaom and libyuv (hosted on googlesource.com):
# a desktop build then takes the system's (Ubuntu: libaom-dev, libyuv-dev).
# The Android build needs them.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$HERE/third_party"
AVIF_DEPS=1

while [ $# -gt 0 ]; do
    case "$1" in
        --dest) DEST="$2"; shift 2 ;;
        --no-avif-deps) AVIF_DEPS=0; shift ;;
        -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Option inconnue : $1" >&2; exit 2 ;;
    esac
done

mkdir -p "$DEST"

# fetch NAME URL TAG COMMIT [SUBMODULE...]
fetch() {
    local name="$1" url="$2" tag="$3" commit="$4"
    shift 4
    local dir="$DEST/$name"
    if [ -d "$dir/.git" ] && [ "$(git -C "$dir" rev-parse HEAD)" = "$commit" ]; then
        echo "$name : déjà présent ($tag)"
    else
        echo "$name : téléchargement de $tag…"
        rm -rf "$dir"
        git -c advice.detachedHead=false clone --depth 1 --branch "$tag" "$url" "$dir"
        local got
        got="$(git -C "$dir" rev-parse HEAD)"
        if [ "$got" != "$commit" ]; then
            echo "$name : le tag $tag désigne $got, et non $commit attendu." >&2
            exit 1
        fi
    fi
    if [ $# -gt 0 ]; then
        git -C "$dir" submodule update --init --depth 1 -- "$@"
    fi
}

fetch mupdf https://github.com/ArtifexSoftware/mupdf.git \
    1.28.6 da5f5eb890d7b3fb76903160b5f7f48ba1301492 \
    thirdparty/brotli thirdparty/extract thirdparty/freetype thirdparty/gumbo-parser \
    thirdparty/harfbuzz thirdparty/jbig2dec thirdparty/lcms2 thirdparty/libjpeg \
    thirdparty/mujs thirdparty/openjpeg thirdparty/zlib
fetch libjpeg-turbo https://github.com/libjpeg-turbo/libjpeg-turbo.git \
    3.2.0 c85e6b905bf237038faa936dab160ebfc5da0344
fetch zlib-ng https://github.com/zlib-ng/zlib-ng.git \
    2.3.3 12731092979c6d07f42da27da673a9f6c7b13586
fetch libwebp https://github.com/webmproject/libwebp.git \
    v1.6.0 4fa21912338357f89e4fd51cf2368325b59e9bd9
fetch libavif https://github.com/AOMediaCodec/libavif.git \
    v1.4.2 c5240fc79fe5c2407e10afd35f5505ef6333ea49
fetch xz https://github.com/tukaani-project/xz.git \
    v5.8.4 d3e650e63c110e830fd5391e7f8b45df0b91d3da

if [ "$AVIF_DEPS" = 1 ]; then
    # Where libavif 1.4.2 looks for them, at the versions it pins
    # (cmake/Modules/LocalAom.cmake and LocalLibyuv.cmake): libaom v3.14.1,
    # which Pillow 13.0.0 bundles too, and libyuv 644251f2.
    ext="$DEST/libavif/ext"
    if [ -f "$ext/aom/CMakeLists.txt" ] && [ "$(git -C "$ext/aom" describe --tags 2>/dev/null)" = "v3.14.1" ]; then
        echo "aom : déjà présent (v3.14.1)"
    else
        echo "aom : téléchargement de v3.14.1…"
        rm -rf "$ext/aom"
        git -c advice.detachedHead=false clone --depth 1 --branch v3.14.1 \
            https://aomedia.googlesource.com/aom "$ext/aom"
    fi
    yuv=644251f252a84bf8ce91ff0aca86a9b16b069ab8
    if [ -d "$ext/libyuv/.git" ] && [ "$(git -C "$ext/libyuv" rev-parse HEAD)" = "$yuv" ]; then
        echo "libyuv : déjà présent (${yuv:0:8})"
    else
        echo "libyuv : téléchargement de ${yuv:0:8}…"
        rm -rf "$ext/libyuv"
        git clone --filter=blob:none --no-checkout \
            https://chromium.googlesource.com/libyuv/libyuv "$ext/libyuv"
        git -C "$ext/libyuv" -c advice.detachedHead=false checkout --quiet "$yuv"
    fi
fi

echo "Sources tierces prêtes dans $DEST"
