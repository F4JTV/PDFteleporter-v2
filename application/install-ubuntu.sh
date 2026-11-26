#!/usr/bin/env bash
# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
#
# Run PDF Teleporter from its sources on Ubuntu 24.04 or 26.04, from a
# launcher in the applications grid and on the desktop.
#
#   ./install-ubuntu.sh               install or update, then create the launcher
#   ./install-ubuntu.sh --dock        the same, and pin the launcher to the dock
#   ./install-ubuntu.sh --no-desktop  the same, without an icon on the desktop
#   ./install-ubuntu.sh --uninstall   remove everything this script created
#
# Everything goes into the user's own folders; root is needed only for the
# system packages the script may offer to install (python3-venv, and the
# libraries Qt needs to open a window, libxcb-cursor0 above all). The sources
# are not copied: the launcher runs them where they are, so a git pull in
# this folder takes effect at the next launch. Run the script again after
# moving the folder, or after an Ubuntu upgrade that changed the Python
# version.

set -euo pipefail

readonly APP_ID=pdfteleporter
readonly APP_NAME="PDF Teleporter"
readonly MIN_PYTHON=3.10

DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
STATE_HOME=${XDG_STATE_HOME:-$HOME/.local/state}
INSTALL_DIR=$DATA_HOME/$APP_ID
VENV=$INSTALL_DIR/venv
LAUNCHER=$INSTALL_DIR/launch.py
JOURNAL=$STATE_HOME/$APP_ID/pdfteleporter.log
DESKTOP_FILE=$DATA_HOME/applications/$APP_ID.desktop
ICON_FILE=$DATA_HOME/icons/hicolor/256x256/apps/$APP_ID.png
COMMAND=$HOME/.local/bin/$APP_ID
# Marks the files this script writes outside its own folder, so that it never
# overwrites or deletes one it did not create.
readonly MARKER="Written by install-ubuntu.sh (PDF Teleporter)"

info() { printf '%s\n' "$*"; }
warn() { printf 'Attention : %s\n' "$*" >&2; }
die() { printf 'Erreur : %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage : ./install-ubuntu.sh [--dock] [--no-desktop] | --uninstall

Installe ce qu'il faut pour lancer PDF Teleporter depuis ses sources et crée
un raccourci dans la grille des applications et sur le Bureau.

  --dock         épingle aussi le raccourci au dock
  --no-desktop   pas d'icône sur le Bureau (et retire celle déjà posée)
  --uninstall    retire tout ce que ce script a créé, jamais les sources
EOF
}

# Yes or no, asked on the terminal; no when there is no terminal to ask on.
ask() {
    local reply
    { : </dev/tty; } 2>/dev/null || return 1
    read -r -p "$1 [o/N] " reply </dev/tty || return 1
    [[ $reply =~ ^[oOyY] ]]
}

is_installed() {
    command -v dpkg-query >/dev/null 2>&1 || return 0
    [[ $(dpkg-query -W -f='${db:Status-Status}' "$1" 2>/dev/null) == installed ]]
}

# One argument of a desktop entry's Exec key: quoted, with \ " ` $ escaped as
# the Exec rules want, then every backslash doubled as the string rules want,
# and % doubled, the field-code character.
exec_arg() {
    local s=$1
    s=${s//\\/\\\\}
    s=${s//\"/\\\"}
    s=${s//\`/\\\`}
    s=${s//\$/\\\$}
    s=${s//\\/\\\\}
    s=${s//%/%%}
    printf '"%s"' "$s"
}

# The user's desktop folder ("Bureau" in French), or nothing when there is none.
desktop_dir() {
    local dir="" dirs=${XDG_CONFIG_HOME:-$HOME/.config}/user-dirs.dirs
    if command -v xdg-user-dir >/dev/null 2>&1; then
        dir=$(xdg-user-dir DESKTOP 2>/dev/null || true)
    elif [[ -f $dirs ]]; then
        # The file xdg-user-dir reads: XDG_DESKTOP_DIR="$HOME/Bureau"
        dir=$(sed -n 's/^XDG_DESKTOP_DIR="\(.*\)"$/\1/p' "$dirs")
        dir=${dir//\$HOME/$HOME}
    fi
    [[ -n $dir ]] || dir=$HOME/Desktop
    if [[ -d $dir && $dir != "$HOME" ]]; then
        printf '%s\n' "$dir"
    fi
}

# add or remove the launcher in GNOME's favourites, the dock; fails where
# there is no GNOME to ask.
dock() {
    command -v gsettings >/dev/null 2>&1 || return 1
    local current updated
    current=$(gsettings get org.gnome.shell favorite-apps 2>/dev/null) || return 1
    updated=$(python3 - "$1" "$APP_ID.desktop" "$current" <<'PYEOF'
import ast, sys
action, entry, current = sys.argv[1:]
apps = [] if current.startswith("@as") else ast.literal_eval(current)
if action == "add" and entry not in apps:
    apps.append(entry)
elif action == "remove":
    apps = [app for app in apps if app != entry]
print(repr(apps) if apps else "@as []")
PYEOF
) || return 1
    if [[ $updated != "$current" ]]; then
        gsettings set org.gnome.shell favorite-apps "$updated"
    fi
}

# Remove a file only if this script wrote it.
remove_ours() {
    if [[ -f $1 ]] && grep -qF "$MARKER" "$1"; then
        rm -f -- "$1"
    fi
}

uninstall() {
    local desktop
    dock remove || true
    remove_ours "$DESKTOP_FILE"
    remove_ours "$COMMAND"
    desktop=$(desktop_dir)
    [[ -z $desktop ]] || remove_ours "$desktop/$APP_ID.desktop"
    rm -f -- "$ICON_FILE"
    # Fixed names under the data and state folders: nothing else lives there.
    rm -rf -- "$INSTALL_DIR" "$(dirname -- "$JOURNAL")"
    info "$APP_NAME est désinstallé. Ses sources n'ont pas été touchées."
}

action=install
want_dock=false
want_desktop=true
while (($#)); do
    case $1 in
        --dock) want_dock=true ;;
        --no-desktop) want_desktop=false ;;
        --uninstall) action=uninstall ;;
        -h | --help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
    shift
done

[[ $(uname -s) == Linux ]] || die "ce script est fait pour Ubuntu (Linux)."
((EUID != 0)) || die "lancez ce script sans sudo : il installe dans votre dossier personnel."

if [[ $action == uninstall ]]; then
    uninstall
    exit 0
fi

# --- The sources ---------------------------------------------------------------

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
APP_DIR=$SCRIPT_DIR
[[ -f $APP_DIR/pdfteleporter.py ]] || APP_DIR=$SCRIPT_DIR/application
[[ -f $APP_DIR/pdfteleporter.py ]] \
    || die "pdfteleporter.py est introuvable à côté de ce script ($SCRIPT_DIR)."
[[ $APP_DIR != *$'\n'* ]] || die "le chemin des sources contient un retour à la ligne."

# --- Python and its virtual environment -----------------------------------------

PYTHON=${PYTHON:-python3}
command -v "$PYTHON" >/dev/null 2>&1 \
    || die "$PYTHON est introuvable. Installez-le : sudo apt install python3"
"$PYTHON" -c "import sys; sys.exit(sys.version_info < tuple(map(int, '$MIN_PYTHON'.split('.'))))" \
    || die "Python $MIN_PYTHON ou ultérieur est nécessaire ($("$PYTHON" --version 2>&1))."
python_version=$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')

# Ubuntu forbids pip in the system Python (PEP 668): the libraries go into a
# virtual environment of their own. One whose interpreter has gone -- after an
# Ubuntu upgrade, from 3.12 to 3.14 -- or differs from the one asked for is
# rebuilt.
venv_python=$VENV/bin/python
if [[ -d $VENV ]]; then
    have=$("$venv_python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)
    if [[ $have != "$python_version" ]]; then
        info "L'environnement Python existant (${have:-inutilisable}) est recréé pour Python $python_version."
        rm -rf -- "$VENV"
    fi
fi
if [[ ! -d $VENV ]]; then
    # python3-venv holds the pip a new environment is seeded with; Ubuntu
    # leaves it out of the base system.
    venv_package=python$python_version-venv
    if [[ $(command -v "$PYTHON") == /usr/bin/* ]] \
        && ! is_installed python3-venv && ! is_installed "$venv_package"; then
        info "Paquet système manquant : $venv_package"
        if command -v apt-get >/dev/null 2>&1 && ask "L'installer maintenant (sudo apt install $venv_package) ?"; then
            sudo apt-get install -y "$venv_package"
        else
            die "installez-le d'abord : sudo apt install $venv_package"
        fi
    fi
    info "Création de l'environnement Python dans $VENV"
    mkdir -p -- "$INSTALL_DIR"
    if ! "$PYTHON" -m venv "$VENV"; then
        rm -rf -- "$VENV"
        die "l'environnement n'a pas pu être créé. Sur Ubuntu : sudo apt install $venv_package"
    fi
fi

info "Installation ou mise à jour de PyQt6, PyMuPDF et Pillow (environ 220 Mo la première fois)…"
"$venv_python" -m pip install --quiet --disable-pip-version-check --upgrade pip
if [[ -f $APP_DIR/requirements.txt ]]; then
    "$venv_python" -m pip install --disable-pip-version-check --upgrade -r "$APP_DIR/requirements.txt"
else
    "$venv_python" -m pip install --disable-pip-version-check --upgrade PyQt6 pymupdf Pillow
fi

"$venv_python" - <<'PYEOF' || die "les bibliothèques installées ne se chargent pas (voir ci-dessus)."
import PIL
import pymupdf
from PIL import features
from PyQt6.QtCore import QT_VERSION_STR

print(f"PyMuPDF {pymupdf.VersionBind}, Qt {QT_VERSION_STR}, Pillow {PIL.__version__}")
missing = [codec.upper() for codec in ("webp", "avif") if not features.check(codec)]
if missing:
    print(f"Attention : ce Pillow ne lit pas {' ni '.join(missing)} ; les archives "
          ".psdz qui en contiennent ne pourront pas être recomposées.")
PYEOF

# --- What Qt needs from the system ----------------------------------------------

# The system libraries Qt's window plugins load, as ldd finds them missing.
# libxcb-cursor0 is the usual one: Qt 6.5 and later need it to open a window
# through X11 -- the Xorg session of Ubuntu 24.04, or XWayland whenever Qt
# does not use Wayland itself -- and Ubuntu does not install it by default.
plugins=$("$venv_python" -c 'import os, PyQt6; print(os.path.join(os.path.dirname(PyQt6.__file__), "Qt6", "plugins", "platforms"))')
mapfile -t absent < <(for plugin in "$plugins"/libqxcb.so "$plugins"/libqwayland*.so; do
    if [[ -e $plugin ]]; then
        ldd "$plugin" 2>/dev/null | awk '/not found/ { print $1 }'
    fi
done | sort -u)
if ((${#absent[@]})); then
    packages=()
    for lib in "${absent[@]}"; do
        case $lib in
            libxcb-cursor.so.*) packages+=(libxcb-cursor0) ;;
            libxkbcommon-x11.so.*) packages+=(libxkbcommon-x11-0) ;;
            libxkbcommon.so.*) packages+=(libxkbcommon0) ;;
            libxcb-icccm.so.*) packages+=(libxcb-icccm4) ;;
            libxcb-image.so.*) packages+=(libxcb-image0) ;;
            libxcb-keysyms.so.*) packages+=(libxcb-keysyms1) ;;
            libxcb-render-util.so.*) packages+=(libxcb-render-util0) ;;
            libxcb-shape.so.*) packages+=(libxcb-shape0) ;;
            libxcb-randr.so.*) packages+=(libxcb-randr0) ;;
            libxcb-xkb.so.*) packages+=(libxcb-xkb1) ;;
            libX11-xcb.so.*) packages+=(libx11-xcb1) ;;
            libEGL.so.*) packages+=(libegl1) ;;
            libGL.so.* | libOpenGL.so.*) packages+=(libgl1) ;;
            libfontconfig.so.*) packages+=(libfontconfig1) ;;
            libdbus-1.so.*) packages+=(libdbus-1-3) ;;
            libwayland-*.so.*) packages+=(libwayland-client0 libwayland-cursor0 libwayland-egl1) ;;
            *) packages+=("$lib") ;;
        esac
    done
    mapfile -t packages < <(printf '%s\n' "${packages[@]}" | sort -u)
    info "Bibliothèques système manquantes pour l'affichage : ${absent[*]}"
    if command -v apt-get >/dev/null 2>&1 && ask "Installer ${packages[*]} maintenant (sudo apt install) ?"; then
        sudo apt-get install -y "${packages[@]}"
    else
        warn "la fenêtre risque de ne pas s'ouvrir sans elles."
        warn "à installer : sudo apt install ${packages[*]}"
    fi
fi

# --- The launcher ------------------------------------------------------------------

# A launcher started from the desktop has no terminal: what goes wrong would go
# unseen. It writes the output to a journal and, should the application stop
# on an error, says so in a notification. It also gives the window the name of
# its desktop entry: GNOME pairs a Wayland window with its launcher by that
# name, and without it the window shows up as "python3", without its icon.
"$venv_python" - "$APP_DIR" "$LAUNCHER" "$JOURNAL" "$MARKER" <<'PYEOF'
import os
import sys

app_dir, target, journal, marker = sys.argv[1:]
interpreter = sys.executable if " " not in sys.executable else "/usr/bin/env python3"
source = f'''#!{interpreter}
# {marker}; run it again to rewrite this file.
"""Start PDF Teleporter from its sources, inside its virtual environment."""

import os
import runpy
import signal
import subprocess
import sys

APP_DIR = {app_dir!r}
SCRIPT = os.path.join(APP_DIR, "pdfteleporter.py")
JOURNAL = {journal!r}
CHILD = "PDFTELEPORTER_LAUNCHED"


def alert(message):
    print(message, file=sys.stderr)
    if not sys.stderr.isatty():
        try:
            subprocess.run(["notify-send", "--app-name=PDF Teleporter",
                            "--icon=pdfteleporter", "PDF Teleporter", message],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=False, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass


if not os.path.isfile(SCRIPT):
    alert(f"Sources introuvables : {{SCRIPT}}. Relancez install-ubuntu.sh "
          "depuis le dossier où elles se trouvent maintenant.")
    sys.exit(1)

gui = len(sys.argv) == 1
if gui and CHILD not in os.environ and not sys.stderr.isatty():
    # Started from the desktop: run the application in a child whose output
    # goes to the journal, and report a failure.
    os.makedirs(os.path.dirname(JOURNAL), exist_ok=True)
    with open(JOURNAL, "w", encoding="utf-8") as output:
        code = subprocess.call([sys.executable, os.path.abspath(__file__)],
                               stdout=output, stderr=subprocess.STDOUT,
                               env=dict(os.environ, **{{CHILD: "1"}}))
    # Stopped by the session -- closing it sends SIGTERM -- is no failure.
    ended = (-signal.SIGTERM, -signal.SIGINT, -signal.SIGHUP)
    if code > 0 or (code < 0 and code not in ended):
        alert(f"PDF Teleporter s'est arrêté sur une erreur (code {{code}}). "
              f"Détails : {{JOURNAL}}")
    sys.exit(code)

if gui:
    from PyQt6.QtGui import QGuiApplication

    QGuiApplication.setDesktopFileName("pdfteleporter")

sys.path.insert(0, APP_DIR)
sys.argv[0] = SCRIPT
runpy.run_path(SCRIPT, run_name="__main__")
'''
os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, "w", encoding="utf-8") as handle:
    handle.write(source)
os.chmod(target, 0o755)
PYEOF

# The command, for a terminal: pdfteleporter compress fichier.pdf -q low
if [[ -e $COMMAND ]] && ! grep -qF "$MARKER" "$COMMAND"; then
    warn "$COMMAND existe déjà et n'a pas été créé par ce script : laissé tel quel."
else
    mkdir -p -- "$(dirname -- "$COMMAND")"
    quoted_python=${venv_python//\'/\'\\\'\'}
    quoted_launcher=${LAUNCHER//\'/\'\\\'\'}
    cat >"$COMMAND" <<EOF
#!/bin/sh
# $MARKER; run it again to rewrite this file.
exec '$quoted_python' '$quoted_launcher' "\$@"
EOF
    chmod 755 "$COMMAND"
fi

# --- Icon and desktop entry -------------------------------------------------------

mkdir -p -- "$(dirname -- "$ICON_FILE")" "$(dirname -- "$DESKTOP_FILE")"
if [[ -f $APP_DIR/assets/${APP_ID}_256.png ]]; then
    cp -f -- "$APP_DIR/assets/${APP_ID}_256.png" "$ICON_FILE"
else
    "$venv_python" - "$APP_DIR/assets/$APP_ID.ico" "$ICON_FILE" <<'PYEOF' || warn "icône non convertie."
import sys
from PIL import Image

icon = Image.open(sys.argv[1])
icon.size = max(icon.info.get("sizes", {icon.size}))
icon.load()
icon.convert("RGBA").save(sys.argv[2])
PYEOF
fi

# The untranslated values are in English, as desktop entries expect; a French
# desktop shows the [fr] ones.
exec_line="$(exec_arg "$venv_python") $(exec_arg "$LAUNCHER")"
try_exec=${venv_python//\\/\\\\}
cat >"$DESKTOP_FILE" <<EOF
[Desktop Entry]
# $MARKER
Type=Application
Version=1.5
Name=$APP_NAME
GenericName=PDF compression for radio
GenericName[fr]=Compression de PDF pour la radio
Comment=Compress a PDF into a .psdi or .psdz archive for radio, and rebuild it
Comment[fr]=Compresser un PDF en archive .psdi ou .psdz pour la radio, et le recomposer
Exec=$exec_line
TryExec=$try_exec
Icon=$APP_ID
Terminal=false
Categories=Utility;Archiving;Compression;
Keywords=PDF;psdi;psdz;radio;Winlink;VARA;
StartupNotify=true
StartupWMClass=$APP_NAME
EOF
chmod 644 "$DESKTOP_FILE"
if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$DESKTOP_FILE" || warn "le raccourci ne passe pas desktop-file-validate."
fi

desktop=$(desktop_dir)
if [[ -n $desktop ]]; then
    if $want_desktop; then
        # The desktop runs a launcher only once it is executable and trusted
        # ("Autoriser le lancement"); gio can fail outside a graphical session.
        cp -f -- "$DESKTOP_FILE" "$desktop/$APP_ID.desktop"
        chmod 755 "$desktop/$APP_ID.desktop"
        gio set "$desktop/$APP_ID.desktop" metadata::trusted true 2>/dev/null || true
    else
        remove_ours "$desktop/$APP_ID.desktop"
        desktop=""
    fi
fi
docked=false
if $want_dock; then
    if dock add; then
        docked=true
    else
        warn "le dock n'a pas pu être modifié (pas de session GNOME ?)."
    fi
fi

info ""
info "$APP_NAME est prêt."
info "  Raccourci : grille des applications (touche Super, puis « PDF Teleporter »)"
if [[ -n $desktop ]]; then
    info "  Sur le Bureau : $desktop/$APP_ID.desktop"
fi
if $docked; then
    info "  Épinglé au dock."
fi
info "  En ligne de commande : $APP_ID compress fichier.pdf -q low"
case ":$PATH:" in
    *":$(dirname -- "$COMMAND"):"*) ;;
    *) info "  (cette commande sera connue à la prochaine ouverture de session)" ;;
esac
info "  Journal du dernier lancement depuis le raccourci : $JOURNAL"
