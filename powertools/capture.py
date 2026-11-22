"""Fallback pemantauan clipboard untuk sesi X11 (tanpa ekstensi GNOME)."""

from __future__ import annotations

from gi.repository import GLib

from . import ui
from .store import ClipStore


class ClipboardPoller:
    """Polling ringan: hanya membaca saat timer berbunyi, tanpa proses baru.

    Di Wayland GNOME ini tidak berguna (aplikasi background tidak boleh
    membaca selection), karena itu jembatan ekstensi yang dipakai.
    """

    def __init__(self, store: ClipStore, interval_ms: int = 800) -> None:
        self._store = store
        self._interval = max(300, int(interval_ms))
        self._source = 0
        self._busy = False
        self._last: str | None = None

    def start(self) -> None:
        if self._source:
            return
        self._source = GLib.timeout_add(200, self._baseline)

    def stop(self) -> None:
        if self._source:
            GLib.source_remove(self._source)
            self._source = 0

    def _baseline(self) -> bool:
        self._busy = True
        ui.get_clipboard_text_async(self._on_baseline)
        return False

    def _on_baseline(self, text: str | None) -> None:
        self._busy = False
        self._last = text
        self._source = GLib.timeout_add(self._interval, self._tick)

    def _tick(self) -> bool:
        if not self._busy:
            self._busy = True
            ui.get_clipboard_text_async(self._on_text)
        return True

    def _on_text(self, text: str | None) -> None:
        self._busy = False
        if text and text != self._last:
            self._last = text
            self._store.append(text)