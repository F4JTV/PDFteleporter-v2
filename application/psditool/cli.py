"""Command-line interface.

This is also what the Explorer context menu invokes. Keeping it separate from
the GUI means a right-click on a PDF does not have to spin up a full Qt
application when all it needs is a file conversion and a small confirmation.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from . import engine, format as psdi
from .presets import (
    MODE_LABELS, MODE_PACKET_1200, MODE_VARA_FM_NARROW, MODE_VARA_HF,
    QUALITY_LABELS,
    QUALITY_MEDIUM, QUALITY_ORDER, QUALITY_PRESETS, WINLINK_MAX_ATTACHMENT,
    format_bytes, format_duration, format_percent,
)

log = logging.getLogger("psditool")

# Mirrors the mapping used by the GUI: the engine reports a stable identifier,
# the presentation layer decides how to word it.
_REASONS = {
    "rotation": "page pivotée",
    "scan": "document scanné",
}


def _notify(title: str, message: str, error: bool = False) -> None:
    """Show a short result to the operator.

    Launched from Explorer there is no console to print to, so fall back to a
    message box when Qt is importable; otherwise stderr still works when the
    tool is driven from a terminal.
    """
    # A windowed build has no stdout at all; that, not tty-ness, is the right
    # test. Keying off isatty() would pop a dialog whenever console output was
    # piped or redirected, which is exactly when a dialog is least wanted.
    if sys.stdout is not None:
        stream = sys.stderr if error else sys.stdout
        try:
            print(f"{title} : {message}", file=stream)
        except (BrokenPipeError, OSError):
            pass
        return

    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication(sys.argv[:1])
        box = QMessageBox()
        box.setWindowTitle(title)
        box.setText(message)
        box.setIcon(
            QMessageBox.Icon.Critical if error else QMessageBox.Icon.Information
        )
        box.exec()
        del app
    except Exception:  # noqa: BLE001
        print(f"{title} : {message}", file=sys.stderr)


def cmd_compress(path: str, quality: str, output: str | None,
                 skip_images: bool, quiet: bool) -> int:
    output = output or os.path.splitext(path)[0] + ".psdi"
    try:
        _, info = engine.pdf_to_archive(
            path, quality=quality, output_path=output, skip_images=skip_images
        )
    except Exception as exc:  # noqa: BLE001
        if not quiet:
            _notify("Échec de la compression", str(exc), error=True)
        log.error("Compression failed: %s", exc)
        return 1

    times = info["estimates"]
    summary = (
        f"{os.path.basename(output)}\n\n"
        f"{format_bytes(info['original_size'])} -> "
        f"{format_bytes(info['archive_size'])} "
        f"({format_percent(info['ratio_percent'])})\n"
        f"Mode : {MODE_LABELS.get(info['mode'], info['mode'])}   "
        f"Qualité : {QUALITY_LABELS.get(info['quality'], info['quality'])}\n\n"
        f"Packet 1200 : {format_duration(times[MODE_PACKET_1200])}\n"
        f"VARA HF     : {format_duration(times[MODE_VARA_HF])}\n"
        f"VARA FM     : {format_duration(times[MODE_VARA_FM_NARROW])}"
    )
    if info.get("auto_mode_switch"):
        summary += (
            f"\n\nBascule automatique en mode image de page "
            f"({_REASONS.get(info['auto_mode_switch'], info['auto_mode_switch'])})."
        )
    if info["archive_size"] > WINLINK_MAX_ATTACHMENT:
        summary += (
            f"\n\nAttention : au-dessus de la limite de pièce jointe "
            f"Winlink de {WINLINK_MAX_ATTACHMENT // 1024} ko."
        )

    if not quiet:
        _notify("Archive créée", summary)
    return 0


def cmd_rebuild(path: str, output: str | None, quiet: bool) -> int:
    output = output or os.path.splitext(path)[0] + "_rebuilt.pdf"
    try:
        with open(path, "rb") as handle:
            data = handle.read()
        _, info = engine.archive_to_pdf(data, output_path=output)
    except Exception as exc:  # noqa: BLE001
        if not quiet:
            _notify("Échec de la recomposition", str(exc), error=True)
        log.error("Rebuild failed: %s", exc)
        return 1

    summary = (
        f"{os.path.basename(output)}\n\n"
        f"{info['pages']} page(s), {format_bytes(info['pdf_size'])}\n"
        f"Mode : {MODE_LABELS.get(info['mode'], info['mode'])}"
    )
    if not info.get("crc_ok", True):
        summary += ("\n\nAttention : CRC du manifeste incorrect, "
                    "le contenu peut être endommagé.")

    if not quiet:
        _notify("PDF recomposé", summary)
    return 0


_INSPECT_LABELS = {
    "valid": "valide",
    "version": "version",
    "checksum_ok": "CRC correct",
    "pages": "pages",
    "error": "erreur",
}


def cmd_inspect(path: str) -> int:
    with open(path, "rb") as handle:
        report = psdi.validate(handle.read())
    try:
        for key, value in report.items():
            if isinstance(value, bool):
                value = "oui" if value else "non"
            elif value is None:
                value = "-"
            print(f"{_INSPECT_LABELS.get(key, key):>12} : {value}")
    except BrokenPipeError:
        # Piping into head or less closes stdout early; that is not an error.
        return 0
    return 0 if report["valid"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psditool",
        description="Convertit des documents PDF en archives radio .psdi et inversement.",
    )
    parser.add_argument("--verbose", action="store_true",
                        help="journaliser l'activité du moteur sur stderr")
    parser.add_argument("--quiet", action="store_true",
                        help="supprimer la fenêtre de résultat")

    sub = parser.add_subparsers(dest="command")

    compress = sub.add_parser("compress", help="PDF -> .psdi")
    compress.add_argument("input")
    compress.add_argument("-o", "--output")
    compress.add_argument("-q", "--quality", default=QUALITY_MEDIUM,
                          choices=list(QUALITY_ORDER))
    compress.add_argument("--no-images", action="store_true",
                          help="texte seul, supprimer toutes les images")

    rebuild = sub.add_parser("rebuild", help=".psdi -> PDF")
    rebuild.add_argument("input")
    rebuild.add_argument("-o", "--output")

    inspect = sub.add_parser("inspect", help="valider une archive")
    inspect.add_argument("input")

    presets = sub.add_parser("presets", help="lister les préréglages de qualité")
    presets.set_defaults(command="presets")

    shell = sub.add_parser("shell", help="intégration au menu contextuel de l'Explorateur")
    shell.add_argument("action", choices=["install", "uninstall", "status"])
    shell.add_argument(
        "--scope", choices=["user", "machine"], default="user",
        help="user : HKEY_CURRENT_USER (sans élévation). machine : "
             "HKEY_LOCAL_MACHINE, tous les comptes, nécessite une élévation. "
             "uninstall balaie les deux si --scope est omis.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # The context menu passes a single file with a --compress / --rebuild
    # flag, which is shorter to encode in a registry command string than the
    # full subcommand form.
    if argv and argv[0] in ("--compress", "--rebuild") and len(argv) >= 2:
        action, target = argv[0], argv[1]
        logging.basicConfig(level=logging.WARNING)
        if action == "--compress":
            return cmd_compress(target, QUALITY_MEDIUM, None, False, False)
        return cmd_rebuild(target, None, False)

    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)-7s %(name)s: %(message)s",
    )

    if args.command == "compress":
        return cmd_compress(args.input, args.quality, args.output,
                            args.no_images, args.quiet)
    if args.command == "rebuild":
        return cmd_rebuild(args.input, args.output, args.quiet)
    if args.command == "inspect":
        return cmd_inspect(args.input)
    if args.command == "presets":
        for key in QUALITY_ORDER:
            preset = QUALITY_PRESETS[key]
            print(f"{key:<12} {preset['dpi']:>4} ppp  "
                  f"JPEG {preset['jpeg_quality']:>3}  {preset['description']}")
        return 0
    if args.command == "shell":
        from . import shell_windows

        if not shell_windows.is_supported():
            print("L'intégration à l'Explorateur est réservée à Windows.", file=sys.stderr)
            return 1
        if args.action == "install":
            shell_windows.install(args.scope)
            print(f"Menu contextuel installé (portée {args.scope}).")
        elif args.action == "uninstall":
            # Sweep both scopes unless one was named explicitly, so an
            # uninstall never leaves entries from an earlier install mode.
            explicit = "--scope" in argv
            shell_windows.uninstall(args.scope if explicit else None)
            print("Menu contextuel retiré.")
        else:
            scopes = shell_windows.installed_scopes()
            print(", ".join(scopes) if scopes else "non installé")
        return 0

    build_parser().print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
