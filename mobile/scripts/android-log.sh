#!/usr/bin/env bash
# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
#
# Records what a phone logs about the application, to find why it stops:
# first the crashes the phone has kept, then everything logged while the
# problem is reproduced, until Ctrl+C. The phone is plugged in over USB,
# with USB debugging on (Settings > About phone: tap "Build number" seven
# times; then Developer options > USB debugging).
#
#   scripts/android-log.sh [options] [OUTPUT]
#
#   --install        install mobile/dist/PDFTeleporter-<version>-android.apk
#                    first (an APK signed with another key is refused: uninstall
#                    that one first)
#   --tools DIR      where build-android.sh put the SDK (~/pdft-android-tools)
#   OUTPUT           the log file (mobile/dist/android-log-<date>.txt)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$HERE/VERSION")"
TOOLS="$HOME/pdft-android-tools"
PACKAGE="org.pdfteleporter.app"
INSTALL=0
OUT=""

while [ $# -gt 0 ]; do
    case "$1" in
        --install) INSTALL=1; shift ;;
        --tools) TOOLS="$2"; shift 2 ;;
        -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) echo "Option inconnue : $1" >&2; exit 2 ;;
        *) OUT="$1"; shift ;;
    esac
done

fail() { echo "Erreur : $*" >&2; exit 1; }

ADB="$TOOLS/android-sdk/platform-tools/adb"
if [ ! -x "$ADB" ]; then
    ADB="$(command -v adb || true)"
    [ -n "$ADB" ] || fail "adb introuvable : ni dans $TOOLS/android-sdk/platform-tools ni dans le PATH."
fi

"$ADB" start-server > /dev/null
if [ "$("$ADB" get-state 2>/dev/null || true)" != "device" ]; then
    "$ADB" devices -l >&2
    fail "aucun téléphone prêt. Branche-le en USB, active le débogage USB et accepte sur le téléphone la clé de cet ordinateur."
fi

if [ "$INSTALL" = 1 ]; then
    APK="$HERE/dist/PDFTeleporter-$VERSION-android.apk"
    [ -f "$APK" ] || fail "$APK introuvable : lance d'abord scripts/build-android.sh."
    echo "Installation de $(basename "$APK")…"
    "$ADB" install -r "$APK"
fi

mkdir -p "$HERE/dist"
[ -n "$OUT" ] || OUT="$HERE/dist/android-log-$(date +%Y%m%d-%H%M%S).txt"

prop() { "$ADB" shell getprop "$1" | tr -d '\r'; }
{
    echo "=== PDF Teleporter $VERSION, $(date '+%Y-%m-%d %H:%M:%S')"
    echo "=== $(prop ro.product.manufacturer) $(prop ro.product.model), Android $(prop ro.build.version.release)" \
         "(API $(prop ro.build.version.sdk)), $(prop ro.product.cpu.abi)"
    echo "=== Version installée : $("$ADB" shell dumpsys package "$PACKAGE" | tr -d '\r' \
         | sed -n 's/^ *versionName=//p' | head -1)"
    echo
    echo "=== Plantages gardés par le téléphone"
    "$ADB" logcat -d -b crash -v threadtime || true
    echo
    echo "=== Journal pendant la reproduction"
} > "$OUT"

"$ADB" logcat -c || true
"$ADB" shell am start -n "$PACKAGE/.PdftActivity" > /dev/null || true

cat << EOF

Le journal s'enregistre dans :
  $OUT
Reproduis le problème sur le téléphone, puis Ctrl+C ici. Seules les lignes
de l'application et des plantages s'affichent ; le fichier garde tout.

EOF

trap 'echo; echo "Journal enregistré : $OUT"; exit 0' INT
"$ADB" logcat -v threadtime | tee -a "$OUT" \
    | grep --line-buffered -E "$PACKAGE|pdfteleporter|PDFteleporter|PdfTeleporter|PDFTeleporter|qt\.|AndroidRuntime|DEBUG|libc |FATAL"
