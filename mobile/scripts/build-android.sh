#!/usr/bin/env bash
# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
#
# Builds the PDF Teleporter APK on Ubuntu 24.04 from a bare system: the
# system packages, the Android SDK and NDK, Qt for Android, the third-party
# sources, then the APK, aligned and signed. Every version is fixed below:
# the ones Qt 6.12 is supported with, so that a build that worked keeps
# working.
#
#   scripts/build-android.sh [options]
#
#   --abis LIST          the ABIs, ";"-separated (default "arm64-v8a;armeabi-v7a",
#                        the phones); "all" adds x86_64 and x86 (emulators)
#   --accept-licenses    accept the Android SDK licences without asking (CI)
#   --no-apt             install no system package (they are already there)
#   --tools DIR          where the SDK, the NDK and Qt go (~/pdft-android-tools)
#   --build DIR          the build directory (mobile/build-android)
#   --clean              empty the build directory first: after a failed build
#                        or an update of the project (the tools stay)
#
# Signing: when PDFT_KEYSTORE (a keystore file), PDFT_KEY_ALIAS,
# PDFT_KEYSTORE_PASS and PDFT_KEY_PASS are set, the APK is signed with that
# key. Otherwise it is signed with the Android debug key
# (~/.android/debug.keystore): enough to install it on a phone, not to
# publish it, and an APK signed with another key cannot update it.
#
# Result: mobile/dist/PDFTeleporter-<version>-android.apk and its SHA-256.

set -euo pipefail

QT_VERSION=6.12.0
NDK_VERSION=27.2.12479018
BUILD_TOOLS_VERSION=36.0.0
SDK_PLATFORM=android-36
MIN_SDK=28
CMDLINE_TOOLS_BUILD=13114758
AQT_VERSION=3.3.0

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$HERE/VERSION")"
ABIS="arm64-v8a;armeabi-v7a"
TOOLS="${PDFT_TOOLS:-$HOME/pdft-android-tools}"
BUILD="$HERE/build-android"
ACCEPT_LICENSES=0
APT=1
CLEAN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --abis) ABIS="$2"; shift 2 ;;
        --accept-licenses) ACCEPT_LICENSES=1; shift ;;
        --no-apt) APT=0; shift ;;
        --tools) TOOLS="$2"; shift 2 ;;
        --build) BUILD="$2"; shift 2 ;;
        --clean) CLEAN=1; shift ;;
        -h|--help) sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Option inconnue : $1" >&2; exit 2 ;;
    esac
done
[ "$ABIS" = all ] && ABIS="arm64-v8a;armeabi-v7a;x86_64;x86"

step() { printf '\n=== %s\n' "$*"; }
fail() { echo "Erreur : $*" >&2; exit 1; }

qt_arch() {
    case "$1" in
        arm64-v8a) echo android_arm64_v8a ;;
        armeabi-v7a) echo android_armv7 ;;
        x86_64) echo android_x86_64 ;;
        x86) echo android_x86 ;;
        *) fail "ABI inconnue : $1" ;;
    esac
}

mkdir -p "$TOOLS/downloads"

# --- system packages --------------------------------------------------------------
if [ "$APT" = 1 ]; then
    step "Paquets système"
    if [ -r /etc/os-release ] && ! grep -q 'VERSION_ID="24.04"' /etc/os-release; then
        echo "Ce script est écrit pour Ubuntu 24.04 ; on continue quand même."
    fi
    sudo apt-get update
    # nasm: the x86 assembly of libaom and libjpeg-turbo, for the x86 ABIs.
    sudo apt-get install -y --no-install-recommends \
        git cmake ninja-build python3 python3-venv openjdk-21-jdk-headless \
        nasm perl unzip curl ca-certificates build-essential
fi

cmake_version="$(cmake --version | head -n1 | awk '{print $3}')"
if ! printf '3.25\n%s\n' "$cmake_version" | sort -V -C; then
    fail "CMake $cmake_version : Qt $QT_VERSION en demande 3.25 au moins."
fi

if [ -z "${JAVA_HOME:-}" ]; then
    for candidate in /usr/lib/jvm/java-21-openjdk-amd64 /usr/lib/jvm/java-21-openjdk-arm64; do
        [ -d "$candidate" ] && JAVA_HOME="$candidate" && break
    done
fi
[ -n "${JAVA_HOME:-}" ] || fail "JDK 21 introuvable : installez openjdk-21-jdk-headless ou définissez JAVA_HOME."
java_major="$("$JAVA_HOME/bin/java" -version 2>&1 | awk -F'"' '/version/ { split($2, v, "."); print v[1]; exit }')"
[ "${java_major:-0}" -ge 21 ] || fail "JDK ${java_major:-?} dans $JAVA_HOME : Qt $QT_VERSION demande le JDK 21."
export JAVA_HOME
export PATH="$JAVA_HOME/bin:$PATH"

# --- Android SDK and NDK ----------------------------------------------------------
step "SDK et NDK Android"
export ANDROID_HOME="${ANDROID_HOME:-$TOOLS/android-sdk}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
SDKMANAGER="$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager"
if [ ! -x "$SDKMANAGER" ]; then
    zip="$TOOLS/downloads/commandlinetools-linux-${CMDLINE_TOOLS_BUILD}_latest.zip"
    [ -f "$zip" ] || curl -fL --retry 3 -o "$zip" \
        "https://dl.google.com/android/repository/commandlinetools-linux-${CMDLINE_TOOLS_BUILD}_latest.zip"
    rm -rf "$TOOLS/cmdline-tools-unpack"
    unzip -q "$zip" -d "$TOOLS/cmdline-tools-unpack"
    mkdir -p "$ANDROID_HOME/cmdline-tools"
    rm -rf "$ANDROID_HOME/cmdline-tools/latest"
    mv "$TOOLS/cmdline-tools-unpack/cmdline-tools" "$ANDROID_HOME/cmdline-tools/latest"
fi
if [ "$ACCEPT_LICENSES" = 1 ]; then
    yes | "$SDKMANAGER" --licenses > /dev/null || true
else
    echo "Les licences du SDK Android vont s'afficher : lisez-les et répondez."
    "$SDKMANAGER" --licenses
fi
# The NDK alone is close to a gigabyte to download and three once unpacked:
# its progress stays on screen.
echo "Installation de platform-tools, $SDK_PLATFORM, build-tools $BUILD_TOOLS_VERSION et du NDK $NDK_VERSION."
echo "La première fois, près d'un gigaoctet à télécharger : plusieurs minutes."
"$SDKMANAGER" --install "platform-tools" "platforms;$SDK_PLATFORM" \
    "build-tools;$BUILD_TOOLS_VERSION" "ndk;$NDK_VERSION"
NDK="$ANDROID_HOME/ndk/$NDK_VERSION"
[ -f "$NDK/build/cmake/android.toolchain.cmake" ] || fail "NDK $NDK_VERSION absent de $NDK."
BUILD_TOOLS="$ANDROID_HOME/build-tools/$BUILD_TOOLS_VERSION"

# --- Qt for Android ---------------------------------------------------------------
step "Qt $QT_VERSION"
if [ ! -x "$TOOLS/venv/bin/aqt" ]; then
    python3 -m venv "$TOOLS/venv"
    "$TOOLS/venv/bin/pip" install --quiet "aqtinstall==$AQT_VERSION"
fi
AQT="$TOOLS/venv/bin/aqt"
QT_ROOT="$TOOLS/Qt"
QT_HOST="$QT_ROOT/$QT_VERSION/gcc_64"

# A Qt counts as installed once aqt has finished with it and it holds what
# the build needs. aqt unpacks its archives side by side: an interrupted run
# leaves a Qt with some of its modules only (qtbase without qtdeclarative,
# say), which the next run must install again, not take as it is.
#   install_qt DIR "FILES THE BUILD NEEDS" AQT-ARGUMENTS...
install_qt() {
    local dir="$1" files="$2"
    shift 2
    if [ -f "$dir/.pdft-complete" ]; then
        echo "$(basename "$dir") : déjà installé"
        return 0
    fi
    # aqt shows nothing while it unpacks, which takes minutes: the operator
    # must not take that for a hang and stop it.
    echo "Installation de Qt $(basename "$dir") : plusieurs minutes, dont de longues"
    echo "décompressions sans affichage. Ne pas interrompre."
    "$AQT" install-qt "$@" -O "$QT_ROOT" || return 1
    local file missing=""
    for file in $files; do
        [ -e "$dir/$file" ] || missing="$missing $file"
    done
    if [ -n "$missing" ]; then
        echo "Qt incomplet dans $dir ; il manque :$missing" >&2
        return 1
    fi
    date -u '+%Y-%m-%dT%H:%M:%SZ' > "$dir/.pdft-complete"
}

# The desktop Qt of the same version: its tools (moc, qmlcachegen,
# androiddeployqt) build for Android.
host_files="lib/cmake/Qt6CoreTools/Qt6CoreToolsConfig.cmake lib/cmake/Qt6GuiTools/Qt6GuiToolsConfig.cmake
            lib/cmake/Qt6QmlTools/Qt6QmlToolsConfig.cmake libexec/qmlcachegen"
install_qt "$QT_HOST" "$host_files" linux desktop "$QT_VERSION" linux_gcc_64 ||
    fail "échec de l'installation de Qt $QT_VERSION pour le bureau (Qt hôte)."
android_files="lib/cmake/Qt6Qml/Qt6QmlConfig.cmake lib/cmake/Qt6Quick/Qt6QuickConfig.cmake
               lib/cmake/Qt6QuickControls2/Qt6QuickControls2Config.cmake
               qml/QtQuick/Controls/Material/qmldir qml/QtQuick/Dialogs/qmldir qml/QtQuick/Shapes/qmldir"
IFS=';' read -r -a abi_list <<< "$ABIS"
for abi in "${abi_list[@]}"; do
    arch="$(qt_arch "$abi")"
    dir="$QT_ROOT/$QT_VERSION/$arch"
    # Qt 6.7 and later file Android under the "all_os" host.
    install_qt "$dir" "$android_files" all_os android "$QT_VERSION" "$arch" ||
        install_qt "$dir" "$android_files" linux android "$QT_VERSION" "$arch" ||
        fail "échec de l'installation de Qt $QT_VERSION pour Android ($abi)."
done
QT_ANDROID="$QT_ROOT/$QT_VERSION/$(qt_arch "${abi_list[0]}")"

# --- sources ----------------------------------------------------------------------
step "Sources tierces"
"$HERE/scripts/fetch-third-party.sh"

# --- build --------------------------------------------------------------------------
step "Compilation ($ABIS)"
if [ "$CLEAN" = 1 ] && [ -d "$BUILD" ]; then
    # Only a directory CMake has configured is emptied.
    [ -f "$BUILD/CMakeCache.txt" ] || fail "--clean : $BUILD n'est pas un dossier de compilation (pas de CMakeCache.txt)."
    echo "Dossier de compilation vidé : $BUILD"
    rm -rf "$BUILD"
fi
# 16 KB pages: the libraries are aligned for the phones that use them.
"$QT_ANDROID/bin/qt-cmake" -S "$HERE" -B "$BUILD" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DQT_HOST_PATH="$QT_HOST" \
    -DANDROID_SDK_ROOT="$ANDROID_HOME" \
    -DANDROID_NDK_ROOT="$NDK" \
    -DANDROID_PLATFORM="android-$MIN_SDK" \
    -DANDROID_SUPPORT_FLEXIBLE_PAGE_SIZES=ON \
    -DQT_ANDROID_ABIS="$ABIS" \
    -DQT_ANDROID_MULTI_ABI_FORWARD_VARS="ANDROID_PLATFORM;ANDROID_SUPPORT_FLEXIBLE_PAGE_SIZES;PDFT_AVIF_DEPS;PDFT_BUILD_TOOLS" \
    -DPDFT_AVIF_DEPS=LOCAL \
    -DPDFT_BUILD_TOOLS=OFF
cmake --build "$BUILD" --target pdfteleporter_make_apk --parallel

UNSIGNED="$BUILD/android-build/pdfteleporter.apk"
[ -f "$UNSIGNED" ] || fail "APK introuvable : $UNSIGNED"

# --- alignment and signature --------------------------------------------------------
step "Signature"
mkdir -p "$HERE/dist"
OUT="$HERE/dist/PDFTeleporter-$VERSION-android.apk"
ALIGNED="$BUILD/pdfteleporter-aligned.apk"
"$BUILD_TOOLS/zipalign" -P 16 -f 4 "$UNSIGNED" "$ALIGNED"

if [ -n "${PDFT_KEYSTORE:-}" ]; then
    : "${PDFT_KEY_ALIAS:?PDFT_KEY_ALIAS manquant}" "${PDFT_KEYSTORE_PASS:?PDFT_KEYSTORE_PASS manquant}"
    : "${PDFT_KEY_PASS:=$PDFT_KEYSTORE_PASS}"
    export PDFT_KEYSTORE_PASS PDFT_KEY_PASS
    "$BUILD_TOOLS/apksigner" sign --ks "$PDFT_KEYSTORE" --ks-key-alias "$PDFT_KEY_ALIAS" \
        --ks-pass env:PDFT_KEYSTORE_PASS --key-pass env:PDFT_KEY_PASS --out "$OUT" "$ALIGNED"
    echo "APK signée avec la clé $PDFT_KEY_ALIAS."
else
    # The debug key every Android build tool uses, with its public password.
    debug_ks="$HOME/.android/debug.keystore"
    if [ ! -f "$debug_ks" ]; then
        mkdir -p "$HOME/.android"
        keytool -genkeypair -keystore "$debug_ks" -storepass android -alias androiddebugkey \
            -keypass android -keyalg RSA -keysize 2048 -validity 10000 \
            -dname "CN=Android Debug,O=Android,C=US" > /dev/null
    fi
    "$BUILD_TOOLS/apksigner" sign --ks "$debug_ks" --ks-key-alias androiddebugkey \
        --ks-pass pass:android --key-pass pass:android --out "$OUT" "$ALIGNED"
    echo "APK signée avec la clé de débogage Android : installable, pas destinée à être publiée."
fi
"$BUILD_TOOLS/apksigner" verify "$OUT"
rm -f "$OUT.idsig"
(cd "$HERE/dist" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256")

step "Terminé"
ls -l "$OUT"
cat "$OUT.sha256"
