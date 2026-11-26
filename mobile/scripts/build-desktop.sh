#!/usr/bin/env bash
# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
#
# Builds the phone version on the desktop, Ubuntu 24.04, for testing: the
# command-line tool pdft-cli (the engine alone) and the interface, on the
# system's Qt 6.4. Optionally runs the tests against the desktop version.
#
#   scripts/build-desktop.sh [options]
#
#   --no-apt        install no system package (they are already there)
#   --build DIR     the build directory (mobile/build-desktop)
#   --local-avif    libaom 3.14.1 and libyuv from their sources, as on Android
#                   (googlesource.com must be reachable) instead of the
#                   system's: compact archives then match the desktop's too
#   --test          differential tests against the desktop engine (Python),
#                   then a tour of the interface with a picture of each screen
#
# Results in the build directory: pdft-cli, pdfteleporter (the interface),
# and with --test, test-results/ and tour/.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
BUILD="$HERE/build-desktop"
APT=1
LOCAL_AVIF=0
TEST=0

while [ $# -gt 0 ]; do
    case "$1" in
        --no-apt) APT=0; shift ;;
        --build) BUILD="$2"; shift 2 ;;
        --local-avif) LOCAL_AVIF=1; shift ;;
        --test) TEST=1; shift ;;
        -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Option inconnue : $1" >&2; exit 2 ;;
    esac
done

step() { printf '\n=== %s\n' "$*"; }

if [ "$APT" = 1 ]; then
    step "Paquets système"
    sudo apt-get update
    # The file dialogs: GNOME's own through qt6-gtk-platformtheme, and where
    # Qt has none to show, Qt Quick's, which needs Qt.labs.folderlistmodel --
    # without it, Qt 6.4 crashes as the dialog opens.
    sudo apt-get install -y --no-install-recommends \
        git cmake ninja-build g++ pkg-config nasm perl python3 python3-venv \
        libgl-dev libaom-dev libyuv-dev \
        qt6-base-dev qt6-declarative-dev qt6-gtk-platformtheme \
        qml6-module-qtquick qml6-module-qtquick-controls qml6-module-qtquick-dialogs \
        qml6-module-qtquick-layouts qml6-module-qtquick-shapes qml6-module-qtquick-templates \
        qml6-module-qtquick-window qml6-module-qtqml-workerscript qml6-module-qt-labs-folderlistmodel \
        xvfb xauth libxcb-cursor0 libxkbcommon-x11-0
fi

step "Sources tierces"
if [ "$LOCAL_AVIF" = 1 ]; then
    "$HERE/scripts/fetch-third-party.sh"
    avif=LOCAL
else
    "$HERE/scripts/fetch-third-party.sh" --no-avif-deps
    avif=SYSTEM
fi

step "Compilation"
cmake -S "$HERE" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release -DPDFT_AVIF_DEPS="$avif"
cmake --build "$BUILD" --parallel

if [ "$TEST" = 1 ]; then
    step "Tests différentiels"
    venv="$BUILD/venv"
    [ -x "$venv/bin/python" ] || python3 -m venv "$venv"
    "$venv/bin/pip" install --quiet -r "$HERE/tests/requirements.txt"
    out="$BUILD/test-results"
    rm -rf "$out"
    mkdir -p "$out"
    pdfs=("$REPO/sitrep_urgence_oms_mali_mois_de_mai_2021.vv10482.pdf"
          "$REPO/PDFteleporter-comparaison-note-responsable-de-zone.pdf")
    "$venv/bin/python" "$HERE/tests/make_images.py" "$out/images" > "$out/images.txt"
    mapfile -t images < "$out/images.txt"
    "$venv/bin/python" "$HERE/tests/compare_engine.py" "$BUILD/pdft-cli" "$out/engine" \
        "${pdfs[@]}" "${images[@]}"
    "$venv/bin/python" "$HERE/tests/compare_rebuild.py" "$BUILD/pdft-cli" "$out/rebuild" \
        "$out"/engine/cpp/*.psd? "$REPO"/application/tests/vectors/*.psdz

    step "Visite de l'interface"
    rm -rf "$BUILD/tour"
    HOME="$BUILD/tour-home" xvfb-run -a -s "-screen 0 1600x1200x24" \
        env QT_QPA_PLATFORM=xcb QT_SCALE_FACTOR=2 \
        "$BUILD/pdfteleporter" --tour "$BUILD/tour" "${pdfs[0]}"
    echo "Captures dans $BUILD/tour"
fi

step "Terminé"
ls -l "$BUILD/pdft-cli" "$BUILD/pdfteleporter"
