"""Jendela lirik mengambang dengan sorotan dan scroll otomatis.

Jendela ini sengaja berupa jendela GTK biasa, bukan overlay shell: dengan
begitu pin/unpin punya arti nyata. Saat di-pin, ekstensi GNOME memanggil
``Meta.Window.make_above()`` sehingga jendela selalu di atas jendela lain;
saat dilepas, jendela kembali menjadi jendela biasa yang bisa tertutup.
"""

from __future__ import annotations

import bisect
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, GLib, Gtk, Pango

from . import config, dbusutil, lyrics

LYRICS_TITLE = "PowerTools Lirik"
SCROLL_ANIM_MS = 16
MANUAL_SCROLL_PAUSE = 6.0
# Ruang transparan antara kartu dan tepi jendela. Shadow kartu digambar di
# sini; kalau ruangnya lebih kecil dari jangkauan shadow, GTK memotongnya di
# batas jendela dan tepinya terlihat seperti kotak kaku (garis lurus gelap).
# Jangkauan shadow = offset-y + blur pada lapisan terjauh (lihat _CSS).
SHADOW_REACH = 19  # 5px offset + 14px blur
SHADOW_PAD = SHADOW_REACH + 5  # sisa ruang agar ujung blur tetap lembut
if SHADOW_PAD <= SHADOW_REACH:  # jaga-jaga saat nilai diubah tangan
    raise ValueError("SHADOW_PAD harus lebih besar dari jangkauan shadow")

_CSS = """
window.pt-lyrics-window {
    background-color: transparent;
}
.pt-lyrics-card {
    background-color: rgba(14, 14, 18, 0.94);
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 18px;
    padding: 8px 14px 10px 14px;
    color: #f4f4f7;
    /* Jangkauan terjauh 19px (5 offset + 14 blur) dan harus muat di
       SHADOW_PAD; kalau tidak, tepinya terpotong jadi kotak kaku. */
    box-shadow:
        0 1px 2px rgba(0, 0, 0, 0.22),
        0 5px 14px rgba(0, 0, 0, 0.32);
}
.pt-lyrics-title {
    color: #ffffff;
    font-size: 10pt;
    font-weight: 700;
}
.pt-lyrics-artist {
    color: rgba(255, 255, 255, 0.55);
    font-size: 8.4pt;
}
.pt-lyrics-line {
    color: rgba(255, 255, 255, 0.38);
    padding: 3px 0;
}
.pt-lyrics-near {
    color: rgba(255, 255, 255, 0.64);
}
.pt-lyrics-current {
    color: #ffffff;
    font-weight: 700;
}
.pt-lyrics-plain {
    color: rgba(255, 255, 255, 0.72);
    padding: 2px 0;
}
.pt-lyrics-status {
    color: rgba(255, 255, 255, 0.45);
    font-size: 8pt;
}
.pt-lyrics-time {
    color: rgba(255, 255, 255, 0.40);
    font-size: 8pt;
    font-family: "JetBrains Mono", "Fira Code", "DejaVu Sans Mono", monospace;
}
button.pt-lyrics-btn {
    background-color: transparent;
    background-image: none;
    border: none;
    border-radius: 8px;
    box-shadow: none;
    min-width: 26px;
    min-height: 26px;
    padding: 4px;
    color: rgba(255, 255, 255, 0.62);
}
button.pt-lyrics-btn:hover {
    background-color: rgba(255, 255, 255, 0.12);
    color: #ffffff;
}
button.pt-lyrics-pin:checked {
    background-color: rgba(143, 177, 255, 0.24);
    color: #d7e4ff;
}
button.pt-lyrics-romaji {
    font-size: 8pt;
    font-weight: 700;
    padding: 4px 8px;
    min-width: 0;
}
button.pt-lyrics-romaji:checked {
    background-color: rgba(143, 177, 255, 0.24);
    color: #d7e4ff;
}
"""


def _format_time(seconds: float) -> str:
    total = max(0, int(seconds or 0))
    return f"{total // 60}:{total % 60:02d}"


class LyricsWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, cfg: config.Config) -> None:
        super().__init__(application=app)
        self._app = app
        self._cfg = cfg
        self._track = None
        self._track_key = ""
        self._lyrics: lyrics.Lyrics | None = None
        self._rows: list[Gtk.Widget] = []
        self._labels: list[Gtk.Label] = []
        self._times: list[float] = []
        # Label mode teks-biasa, sejajar dengan daftar plain (None = spasi).
        self._plain_rows: list[Gtk.Label | None] = []
        self._current = -1
        self._raw_position = 0.0
        self._position = 0.0
        self._playing = False
        self._offset_ms = int(cfg.get("lyrics.offset_ms", 0) or 0)
        self._font_size = int(cfg.get("lyrics.font_size", 15) or 15)
        self._pinned = bool(cfg.get("lyrics.pinned", False))
        self._syncing_pin = False
        self._romanize = bool(cfg.get("lyrics.romanize", False))
        self._syncing_romaji = False
        self._fetch_token = 0
        self._fetching_key = ""
        self._fetch_cache: dict[str, lyrics.Lyrics | None] = {}
        self._scroll_source = 0
        self._scroll_target = 0.0
        self._manual_until = 0.0
        self._place_tries = 0
        self._placed = False
        self._destroyed = False

        self.set_title(LYRICS_TITLE)
        self.set_icon_name(config.APP_ID)
        self.add_css_class("pt-lyrics-window")
        self.set_decorated(False)
        self.set_resizable(True)
        self.set_default_size(432 + 2 * SHADOW_PAD, 204 + 2 * SHADOW_PAD)
        self.set_size_request(320, 120)
        self.set_modal(False)

        self._install_css()
        self._build()
        self._sync_pin_button()
        self._sync_romaji_button()
        self._update_time()

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)

        self.connect("close-request", self._on_close_request)
        self.connect("destroy", self._on_destroy)

    # ------------------------------------------------------------------- build
    def _install_css(self) -> None:
        self._provider = Gtk.CssProvider()
        try:
            self._provider.load_from_data(_CSS.encode("utf-8"))
        except GLib.Error:
            self._provider = None
            return
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, self._provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def _build(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for edge in ("top", "bottom", "start", "end"):
            getattr(root, f"set_margin_{edge}")(SHADOW_PAD)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.add_css_class("pt-lyrics-card")
        root.append(card)
        self.set_child(root)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        handle = Gtk.WindowHandle()
        handle.set_hexpand(True)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        titles.set_hexpand(True)
        titles.set_valign(Gtk.Align.CENTER)

        self._title = Gtk.Label(label="Tidak ada media", xalign=0)
        self._title.add_css_class("pt-lyrics-title")
        self._title.set_ellipsize(Pango.EllipsizeMode.END)
        self._title.set_lines(1)
        self._title.set_hexpand(True)
        titles.append(self._title)

        self._artist = Gtk.Label(label="Putar lagu untuk melihat lirik", xalign=0)
        self._artist.add_css_class("pt-lyrics-artist")
        self._artist.set_ellipsize(Pango.EllipsizeMode.END)
        self._artist.set_lines(1)
        self._artist.set_hexpand(True)
        titles.append(self._artist)

        handle.set_child(titles)
        header.append(handle)

        self._pin_button = Gtk.ToggleButton()
        self._pin_button.add_css_class("pt-lyrics-btn")
        self._pin_button.add_css_class("pt-lyrics-pin")
        self._pin_button.set_child(
            Gtk.Image.new_from_icon_name("view-pin-symbolic")
        )
        self._pin_button.set_tooltip_text("Sematkan di atas semua jendela")
        self._pin_button.connect("toggled", self._on_pin_toggled)
        header.append(self._pin_button)

        self._romaji_button = Gtk.ToggleButton(label="Romaji")
        self._romaji_button.add_css_class("pt-lyrics-btn")
        self._romaji_button.add_css_class("pt-lyrics-romaji")
        self._romaji_button.set_tooltip_text(
            "Tampilkan lirik dalam huruf Latin"
        )
        self._romaji_button.connect("toggled", self._on_romaji_toggled)
        # Hanya muncul kalau lagu yang sedang diputar punya versi Latin.
        self._romaji_button.set_visible(False)
        header.append(self._romaji_button)

        close = Gtk.Button()
        close.add_css_class("pt-lyrics-btn")
        close.set_child(Gtk.Image.new_from_icon_name("window-close-symbolic"))
        close.set_tooltip_text("Sembunyikan lirik")
        close.connect("clicked", lambda *_: self.hide_window())
        header.append(close)

        card.append(header)

        self._scroller = Gtk.ScrolledWindow()
        self._scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroller.set_vexpand(True)
        self._scroller.set_min_content_height(112)
        self._scroller.set_child(self._make_lines_box())
        card.append(self._scroller)

        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll.connect("scroll", self._on_user_scroll)
        self._scroller.add_controller(scroll)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._spinner = Gtk.Spinner()
        self._spinner.set_valign(Gtk.Align.CENTER)
        self._spinner.set_visible(False)
        footer.append(self._spinner)

        self._status = Gtk.Label(label="", xalign=0)
        self._status.add_css_class("pt-lyrics-status")
        self._status.set_hexpand(True)
        self._status.set_ellipsize(Pango.EllipsizeMode.END)
        footer.append(self._status)

        self._time = Gtk.Label(label="", xalign=1)
        self._time.add_css_class("pt-lyrics-time")
        footer.append(self._time)
        card.append(footer)

    def _make_lines_box(self) -> Gtk.Widget:
        self._lines_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._lines_box.set_hexpand(True)
        return self._lines_box

    def _attrs(self) -> Pango.AttrList:
        attrs = Pango.AttrList()
        size = max(9, min(28, int(self._font_size)))
        attrs.insert(Pango.attr_size_new(Pango.units_from_double(size)))
        return attrs

    # ------------------------------------------------------------------ public
    def set_track(self, track) -> None:
        """Perbarui lagu yang sedang diputar (objek mpris.Track atau None)."""
        if self._destroyed:
            return
        if track is None:
            self._track = None
            self._track_key = ""
            self._lyrics = None
            self._rows = []
            self._labels = []
            self._times = []
            self._plain_rows = []
            self._current = -1
            self._fetch_token += 1
            self._fetching_key = ""
            self._title.set_label("Tidak ada media")
            self._artist.set_label("Putar lagu untuk melihat lirik")
            self._clear_lines()
            self._add_placeholder("Putar lagu untuk melihat lirik.")
            self._status.set_label("")
            self._spinner.stop()
            self._spinner.set_visible(False)
            self._romaji_button.set_visible(False)
            self._update_time()
            return

        key = getattr(track, "key", "")
        self._track = track
        self._title.set_label(getattr(track, "title", "") or "Tanpa judul")
        self._artist.set_label(
            getattr(track, "artist", "")
            or getattr(track, "album", "")
            or "Tanpa artis"
        )

        if key == self._track_key:
            return
        self._track_key = key
        self._lyrics = None
        self._rows = []
        self._labels = []
        self._times = []
        self._plain_rows = []
        self._current = -1
        self._fetch_token += 1
        self._fetching_key = ""
        self._clear_lines()
        self._status.set_label("Mencari lirik…")
        self._spinner.set_visible(True)
        self._spinner.start()
        self._romaji_button.set_visible(False)
        self._update_time()
        if self.get_visible():
            self._start_fetch()

    def set_position(self, seconds: float, playing: bool = False) -> None:
        if self._destroyed:
            return
        self._raw_position = max(0.0, float(seconds or 0))
        self._playing = bool(playing)
        self._position = self._raw_position + self._offset_ms / 1000.0
        self._update_time()
        if self._times:
            index = self._index_for(self._position)
            if index != self._current:
                self._update_current(index)

    def apply_config(self) -> None:
        if self._destroyed:
            return
        try:
            self._offset_ms = int(self._cfg.get("lyrics.offset_ms", 0) or 0)
        except (TypeError, ValueError):
            self._offset_ms = 0
        try:
            font_size = int(self._cfg.get("lyrics.font_size", 15) or 15)
        except (TypeError, ValueError):
            font_size = 15
        font_size = max(9, min(28, font_size))
        if font_size != self._font_size:
            self._font_size = font_size
            attrs = self._attrs()
            for label in self._labels:
                label.set_attributes(attrs)
        self.set_pinned(bool(self._cfg.get("lyrics.pinned", False)))
        self.set_romanize(bool(self._cfg.get("lyrics.romanize", False)))
        self.set_position(self._raw_position, self._playing)

    def set_pinned(self, pinned: bool) -> None:
        self._pinned = bool(pinned)
        self._sync_pin_button()
        if self.get_visible():
            self._push_pin()

    def set_romanize(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._romanize:
            return
        self._apply_romanize(enabled)

    def refresh_pin(self) -> None:
        if self.get_visible():
            self._push_pin()

    def show_window(self, focus: bool = True) -> None:
        if self._destroyed:
            return
        if self._track is not None and self._lyrics is None:
            self._start_fetch()
        self.set_visible(True)
        if focus:
            self.present()
        if not self._placed:
            self._place_tries = 0
            GLib.timeout_add(120, self._place_in_shell)
        self._push_pin()
        self._update_current(self._current, force=True)

    def hide_window(self) -> None:
        if self._destroyed:
            return
        self.set_visible(False)
        dbusutil.set_lyrics_above(False)
        notify = getattr(self._app, "window_hidden", None)
        if callable(notify):
            notify(self)

    # -------------------------------------------------------------------- pin
    def _on_pin_toggled(self, button: Gtk.ToggleButton) -> None:
        if self._syncing_pin:
            return
        self._pinned = bool(button.get_active())
        setter = getattr(self._app, "set_lyrics_pinned", None)
        if callable(setter):
            setter(self._pinned)
        else:
            self._push_pin()

    def _sync_pin_button(self) -> None:
        self._syncing_pin = True
        try:
            self._pin_button.set_active(self._pinned)
        finally:
            self._syncing_pin = False
        self._pin_button.set_tooltip_text(
            "Lepas sematan (bisa tertutup jendela lain)"
            if self._pinned
            else "Sematkan di atas semua jendela"
        )

    def _push_pin(self, retries: int = 0) -> None:
        if self._destroyed or not self.get_visible():
            return
        if dbusutil.set_lyrics_above(self._pinned):
            return
        if retries < 8:
            GLib.timeout_add(120, self._retry_pin, retries + 1)

    def _retry_pin(self, retries: int) -> bool:
        self._push_pin(retries)
        return GLib.SOURCE_REMOVE

    # ----------------------------------------------------------------- romaji
    def _on_romaji_toggled(self, button: Gtk.ToggleButton) -> None:
        if self._syncing_romaji:
            return
        enabled = bool(button.get_active())
        # Terapkan dulu ke lagu yang sedang diputar. Kalau hanya mengandalkan
        # setter milik aplikasi, ``set_romanize`` akan menganggap statusnya
        # sudah sama dan melewati render — tombol terlihat aktif tetapi teks
        # baru berganti saat lagu berikutnya diputar.
        self._apply_romanize(enabled)
        setter = getattr(self._app, "set_lyrics_romanize", None)
        if callable(setter):
            setter(enabled)

    def _apply_romanize(self, enabled: bool) -> None:
        self._romanize = enabled
        self._sync_romaji_button()
        if self._lyrics is not None and self._lyrics.has_romaji:
            self._switch_text_mode(self._lyrics)

    def _sync_romaji_button(self) -> None:
        self._syncing_romaji = True
        try:
            self._romaji_button.set_active(self._romanize)
        finally:
            self._syncing_romaji = False

    def _place_in_shell(self) -> bool:
        if self._destroyed or not self.get_visible():
            return GLib.SOURCE_REMOVE
        if dbusutil.place_lyrics_window():
            self._placed = True
            return GLib.SOURCE_REMOVE
        if self._place_tries >= 8:
            return GLib.SOURCE_REMOVE
        self._place_tries += 1
        GLib.timeout_add(120, self._place_in_shell)
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------ fetch
    def _start_fetch(self) -> None:
        track = self._track
        if track is None:
            return
        key = getattr(track, "key", "")
        if not key:
            return
        token = self._fetch_token

        if key in self._fetch_cache:
            self._apply_lyrics(token, key, self._fetch_cache[key])
            return
        if self._fetching_key == key:
            return

        self._fetching_key = key
        title = getattr(track, "title", "")
        artist = getattr(track, "artist", "")
        album = getattr(track, "album", "")
        length_us = int(getattr(track, "length_us", 0) or 0)
        duration = length_us / 1_000_000.0 if length_us > 0 else 0.0

        def worker() -> None:
            result = None
            try:
                result = lyrics.fetch_lyrics(title, artist, album, duration)
            except Exception:
                result = None
            GLib.idle_add(self._apply_lyrics, token, key, result)

        threading.Thread(
            target=worker, name="powertools-lyrics", daemon=True
        ).start()

    def _apply_lyrics(
        self, token: int, key: str, result: lyrics.Lyrics | None
    ) -> bool:
        if self._destroyed or token != self._fetch_token:
            return GLib.SOURCE_REMOVE
        if self._fetching_key == key:
            self._fetching_key = ""
        # Hanya hasil yang berhasil di-cache di memori. Kegagalan jaringan
        # tidak boleh "menempel" sampai jendela ditutup; percobaan berikutnya
        # akan memakai cache negatif di disk kalau lagunya memang tidak ada.
        if result is not None and result.has_any:
            self._fetch_cache[key] = result
        self._spinner.stop()
        self._spinner.set_visible(False)

        if result is None or not result.has_any:
            self._lyrics = None
            self._rows = []
            self._labels = []
            self._times = []
            self._plain_rows = []
            self._current = -1
            self._clear_lines()
            self._add_placeholder("Lirik tidak ditemukan untuk lagu ini.")
            self._status.set_label("")
            self._romaji_button.set_visible(False)
            return GLib.SOURCE_REMOVE

        self._lyrics = result
        self._render_lyrics(result)
        return GLib.SOURCE_REMOVE

    def _render_lyrics(self, result: lyrics.Lyrics, scroll: bool = True) -> None:
        self._clear_lines()
        self._rows = []
        self._labels = []
        self._times = []
        self._plain_rows = []
        self._current = -1

        self._romaji_button.set_visible(result.has_romaji)
        self._sync_romaji_button()

        synced, plain, source = lyrics.display_text(result, self._romanize)

        if result.instrumental:
            self._add_placeholder("♪  Instrumental")
            self._status.set_label(f"Instrumental · {result.source}")
        elif synced:
            self._times = [line.time for line in synced]
            for line in synced:
                label = self._add_label(line.text or "♪", "pt-lyrics-line")
                self._rows.append(label)
            self._status.set_label(f"Lirik sinkron · {source}")
            self._update_current(
                self._index_for(self._position), force=True, scroll=scroll
            )
        elif plain:
            for text in plain:
                if text:
                    self._plain_rows.append(
                        self._add_label(text, "pt-lyrics-plain")
                    )
                else:
                    spacer = Gtk.Box()
                    spacer.set_size_request(-1, 8)
                    self._lines_box.append(spacer)
                    self._plain_rows.append(None)
            self._status.set_label(f"Lirik tanpa timestamp · {source}")
        else:
            self._add_placeholder("Lirik tidak tersedia.")
            self._status.set_label("")

    def _switch_text_mode(self, result: lyrics.Lyrics) -> None:
        """Tukar lirik asli <-> romaji tanpa mengganggu posisi baca.

        Kalau jumlah barisnya sama (selalu begitu untuk transliterasi lokal),
        teks diganti di tempat: tidak ada widget yang dibangun ulang, tidak ada
        kedip, dan posisi gulir tidak berubah. Kalau berbeda (unggahan romaji
        kadang punya baris ekstra), tampilan dibangun ulang tetapi posisi gulir
        dikembalikan.
        """
        synced, plain, source = lyrics.display_text(result, self._romanize)

        if synced and len(synced) == len(self._rows) == len(self._times):
            self._times = [line.time for line in synced]
            for row, line in zip(self._rows, synced):
                row.set_label(line.text or "♪")
            self._current = -1
            self._update_current(self._index_for(self._position), scroll=False)
            self._status.set_label(f"Lirik sinkron · {source}")
            return

        if not synced and plain and len(plain) == len(self._plain_rows):
            for row, text in zip(self._plain_rows, plain):
                if row is not None:
                    row.set_label(text)
            self._status.set_label(f"Lirik tanpa timestamp · {source}")
            return

        adjustment = self._scroller.get_vadjustment()
        saved = adjustment.get_value() if adjustment is not None else 0.0
        self._render_lyrics(result, scroll=False)
        if adjustment is not None and saved > 0:
            GLib.idle_add(self._restore_scroll, saved)

    def _restore_scroll(self, value: float) -> bool:
        if self._destroyed:
            return GLib.SOURCE_REMOVE
        adjustment = self._scroller.get_vadjustment()
        if adjustment is not None:
            adjustment.set_value(value)
        return GLib.SOURCE_REMOVE

    def _add_label(self, text: str, style_class: str) -> Gtk.Label:
        label = Gtk.Label(label=text, xalign=0)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_hexpand(True)
        label.set_attributes(self._attrs())
        label.add_css_class(style_class)
        self._lines_box.append(label)
        self._labels.append(label)
        return label

    def _add_placeholder(self, text: str) -> None:
        self._add_label(text, "pt-lyrics-plain")

    def _clear_lines(self) -> None:
        child = self._lines_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._lines_box.remove(child)
            child = next_child
        self._labels = []
        self._rows = []
        self._plain_rows = []

    # --------------------------------------------------------------- scrolling
    def _index_for(self, position: float) -> int:
        if not self._times:
            return -1
        index = bisect.bisect_right(self._times, position) - 1
        if index < 0:
            return -1
        return min(index, len(self._times) - 1)

    def _update_current(
        self, index: int, force: bool = False, scroll: bool = True
    ) -> None:
        if not self._rows:
            self._current = -1
            return
        if not force and index == self._current:
            return
        self._current = index

        for row_index, row in enumerate(self._rows):
            row.remove_css_class("pt-lyrics-current")
            row.remove_css_class("pt-lyrics-near")
            if row_index == index:
                row.add_css_class("pt-lyrics-current")
            elif abs(row_index - index) == 1:
                row.add_css_class("pt-lyrics-near")

        if scroll and 0 <= index < len(self._rows):
            self._scroll_to_row(index, force=force)

    def _scroll_to_row(self, index: int, force: bool = False) -> None:
        if not force and time.monotonic() < self._manual_until:
            return
        GLib.idle_add(self._scroll_now, index)

    def _scroll_now(self, index: int) -> bool:
        if self._destroyed or not self.get_visible():
            return GLib.SOURCE_REMOVE
        if index < 0 or index >= len(self._rows):
            return GLib.SOURCE_REMOVE
        row = self._rows[index]
        adjustment = self._scroller.get_vadjustment()
        if adjustment is None:
            return GLib.SOURCE_REMOVE
        page = adjustment.get_page_size()
        if page <= 0:
            return GLib.SOURCE_REMOVE

        # compute_bounds memberi koordinat relatif terhadap kotak baris, yaitu
        # ruang yang sama dengan nilai adjustment ScrolledWindow.
        try:
            found, bounds = row.compute_bounds(self._lines_box)
        except Exception:
            found = False
        if found:
            row_top = bounds.get_y()
            row_height = bounds.get_height()
        else:
            allocation = row.get_allocation()
            row_top = float(allocation.y)
            row_height = float(allocation.height)

        target = row_top + row_height / 2.0 - page * 0.38
        lower = adjustment.get_lower()
        upper = max(lower, adjustment.get_upper() - page)
        self._scroll_target = min(max(target, lower), upper)
        if not self._scroll_source:
            self._scroll_source = GLib.timeout_add(
                SCROLL_ANIM_MS, self._animate_scroll
            )
        return GLib.SOURCE_REMOVE

    def _animate_scroll(self) -> bool:
        self._scroll_source = 0
        adjustment = self._scroller.get_vadjustment()
        if adjustment is None:
            return GLib.SOURCE_REMOVE
        current = adjustment.get_value()
        delta = self._scroll_target - current
        if abs(delta) < 0.7:
            adjustment.set_value(self._scroll_target)
            return GLib.SOURCE_REMOVE

        adjustment.set_value(current + delta * 0.35)
        self._scroll_source = GLib.timeout_add(
            SCROLL_ANIM_MS, self._animate_scroll
        )
        return GLib.SOURCE_REMOVE

    def _on_user_scroll(self, _controller, _dx, _dy) -> bool:
        self._manual_until = time.monotonic() + MANUAL_SCROLL_PAUSE
        return False

    def _update_time(self) -> None:
        if self._track is None:
            self._time.set_label("")
            return
        total = int(getattr(self._track, "length_us", 0) or 0) / 1_000_000.0
        if total > 0:
            self._time.set_label(
                f"{_format_time(self._position)} / {_format_time(total)}"
            )
        else:
            self._time.set_label(_format_time(self._position))

    # ------------------------------------------------------------------ close
    def _on_key_pressed(self, _controller, keyval, _keycode, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.hide_window()
            return True
        return False

    def _on_close_request(self, _window) -> bool:
        self.hide_window()
        return True

    def _on_destroy(self, _window) -> None:
        self._destroyed = True
        if self._scroll_source:
            GLib.source_remove(self._scroll_source)
            self._scroll_source = 0
        if self._provider is not None:
            display = Gdk.Display.get_default()
            if display is not None:
                try:
                    Gtk.StyleContext.remove_provider_for_display(
                        display, self._provider
                    )
                except Exception:
                    pass
            self._provider = None
