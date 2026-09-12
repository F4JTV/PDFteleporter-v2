"""Background workers.

Compression of a large scan at 150 DPI takes seconds to tens of seconds. Doing
that on the GUI thread freezes the window, which during an exercise looks
exactly like a crash. Each conversion therefore runs on its own QThread and
reports back through signals.
"""

from __future__ import annotations

import traceback

from PyQt6.QtCore import QThread, pyqtSignal

from .. import engine


class _BaseWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished_ok = pyqtSignal(str, dict)
    failed = pyqtSignal(str)

    def _emit_progress(self, step: int, total: int, message: str) -> None:
        self.progress.emit(step, total, message)


class CompressWorker(_BaseWorker):
    def __init__(self, pdf_path: str, quality: str, output_path: str,
                 skip_images: bool):
        super().__init__()
        self._pdf_path = pdf_path
        self._quality = quality
        self._output_path = output_path
        self._skip_images = skip_images

    def run(self) -> None:
        try:
            _, info = engine.pdf_to_archive(
                self._pdf_path,
                quality=self._quality,
                output_path=self._output_path,
                progress_cb=self._emit_progress,
                skip_images=self._skip_images,
            )
            self.finished_ok.emit(self._output_path, info)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.failed.emit(f"Compression failed: {exc}")


class EstimateWorker(_BaseWorker):
    """Compress into memory to measure what the archive will really weigh.

    The previous estimate multiplied the source size by a per-preset guess,
    which on a document heavy with photographs was wrong by a factor of two --
    precisely when knowing whether it fits a Winlink attachment matters most.
    Compressing for real costs a second and gives the true figure.

    Nothing is written to disk: only the size and the transfer times are kept.
    """

    def __init__(self, pdf_path: str, quality: str, skip_images: bool):
        super().__init__()
        self._pdf_path = pdf_path
        self._quality = quality
        self._skip_images = skip_images

    def run(self) -> None:
        try:
            _, info = engine.pdf_to_archive(
                self._pdf_path,
                quality=self._quality,
                skip_images=self._skip_images,
            )
            self.finished_ok.emit(self._pdf_path, info)
        except Exception as exc:  # noqa: BLE001
            # An estimate that fails is not worth interrupting anyone over;
            # the real compression will report the problem properly.
            self.failed.emit(f"Estimation impossible : {exc}")


class RebuildWorker(_BaseWorker):
    def __init__(self, archive_path: str, output_path: str):
        super().__init__()
        self._archive_path = archive_path
        self._output_path = output_path

    def run(self) -> None:
        try:
            with open(self._archive_path, "rb") as handle:
                data = handle.read()
            _, info = engine.archive_to_pdf(
                data,
                output_path=self._output_path,
                progress_cb=self._emit_progress,
            )
            self.finished_ok.emit(self._output_path, info)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.failed.emit(f"Rebuild failed: {exc}")
