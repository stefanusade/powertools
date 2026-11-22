"""Popup riwayat clipboard (padanan Win+V)."""

from __future__ import annotations

import os
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gio, GLib, Gtk, Pango

from . import config, ui
from .dbusutil import paste_previous
from .matcher import match_tokens
from .store import Clip, ClipStore

# Jeda kecil antara "clipboard diisi" dan "popup disembunyikan". Cukup untuk
# satu putaran main loop supaya permintaan clipboard terkirim selagi popup
# masih memegang fokus, tapi tidak terasa oleh pengguna.
CLIPBOARD_SETTLE_MS = 40


def _no_autohide() -> bool:
    return bool(os.environ.get("POWERTOOLS_NO_AUTOHIDE"))


class ClipboardWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, store: ClipStore, cfg) -> None:
        super().__init__(application=app)
        self._app = app
        self._store = store
        self._cfg = cfg
        self._clips: list[Clip] = []
        self._rows: list[Gtk.ListBoxRow] = []
        self._query = ""
        self._monitor: Gio.FileMonitor | None = None
        self._monitor_source = 0
        self._ignore_focus_loss_until = 0.0
        self._was_active = False

        self.set_title("PowerTools Clipboard")
        self.set_icon_name(config.APP_ID)
        self.add_css_class("pt-window")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_default_size(640 + 2 * ui.SURFACE_MARGIN, 580 + 2 * ui.SURFACE_MARGIN)
        self.set_modal(False)

        self._build()
        self.connect("close-request", self._on_close_request)
        self.connect("notify::is-active", self._on_active_changed)

    # ------------------------------------------------------------------- build
    def _build(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for edge in ("top", "bottom", "start", "end"):
            getattr(root, f"set_margin_{edge}")(ui.SURFACE_MARGIN)

        surface = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        surface.add_css_class("pt-surface")
        root.append(surface)
        self.set_child(root)

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        header.add_css_class("pt-header")

        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Riwayat Clipboard", xalign=0)
        title.add_css_class("pt-title")
        title.set_hexpand(True)
        title_row.append(title)

        self._count_label = Gtk.Label(label="", xalign=1)
        self._count_label.add_css_class("pt-faint")
        title_row.append(self._count_label)
        header.append(title_row)

        self._entry = Gtk.Entry()
        self._entry.add_css_class("pt-search")
        self._entry.set_placeholder_text("Cari teks yang pernah disalin…")
        self._entry.set_hexpand(True)
        try:
            self._entry.set_icon_from_icon_name(
                Gtk.EntryIconPosition.PRIMARY, "system-search-symbolic"
            )
        except Exception:
            pass
        self._entry.connect("changed", self._on_search_changed)
        header.append(self._entry)
        surface.append(header)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.set_activate_on_single_click(True)
        self._list.set_filter_func(self._filter_row)
        self._list.connect("row-activated", self._on_row_activated)
        self._list.add_css_class("pt-list")

        self._scroller = Gtk.ScrolledWindow()
        self._scroller.add_css_class("pt-scroll")
        self._scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroller.set_vexpand(True)
        self._scroller.set_min_content_height(430)
        self._scroller.set_child(self._list)

        self._empty_label = Gtk.Label(label="Belum ada riwayat clipboard.")
        self._empty_label.add_css_class("pt-empty")
        self._empty_label.set_wrap(True)

        self._stack = Gtk.Stack()
        self._stack.set_vexpand(True)
        self._stack.add_named(self._scroller, "list")
        self._stack.add_named(self._empty_label, "empty")
        surface.append(self._stack)

        self._warning = Gtk.Label(
            label="Clipboard otomatis nonaktif — ekstensi GNOME PowerTools belum aktif."
        )
        self._warning.add_css_class("pt-faint")
        self._warning.set_margin_start(16)
        self._warning.set_margin_end(16)
        self._warning.set_margin_bottom(6)
        self._warning.set_xalign(0)
        self._warning.set_visible(False)
        surface.append(self._warning)

        surface.append(
            ui.hint_bar(
                "↑↓ pilih   ·   Enter tempel   ·   Ctrl+P pin   ·   Ctrl+D hapus   ·   Esc tutup",
                "Win+V",
            )
        )

        controller = Gtk.EventControllerKey()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(controller)

    # ------------------------------------------------------------------ public
    def set_capture_warning(self, visible: bool) -> None:
        self._warning.set_visible(bool(visible))

    def show_window(self) -> None:
        if self.get_visible() and self.is_active():
            self.hide_window()
            return
        self._entry.set_text("")
        self.refresh()
        self.set_visible(True)
        self.present()
        GLib.idle_add(self._focus_entry)
        self._start_monitor()
        self._ignore_focus_loss_until = time.monotonic() + 0.4
        self._was_active = True

    def hide_window(self) -> None:
        self._stop_monitor()
        self.set_visible(False)
        self._app.window_hidden(self)  # type: ignore[attr-defined]

    def refresh(self) -> None:
        self._clips = self._store.load()
        max_shown = int(self._cfg.get("clipboard.max_shown", 120))
        # Kalau ada teks pencarian, tetap cari di seluruh riwayat.
        source = self._clips if self._query else self._clips[:max_shown]
        for row in list(self._rows):
            self._list.remove(row)
        self._rows = []
        for clip in source:
            row = self._make_row(clip)
            self._list.append(row)
            self._rows.append(row)

        self._count_label.set_label(
            f"{ui.format_number_id(len(self._clips))} item"
            if self._clips
            else ""
        )
        self._apply_filter(reset_selection=True)

    # -------------------------------------------------------------------- rows
    def _make_row(self, clip: Clip) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.add_css_class("pt-row")
        row.clip = clip  # type: ignore[attr-defined]

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        if clip.is_image:
            thumb_path, width, height = ui.image_thumb(clip)
            clip.width, clip.height = width, height
            title, meta = self._image_texts(clip)
            if thumb_path is not None:
                picture = Gtk.Picture.new_for_filename(str(thumb_path))
                picture.set_can_shrink(True)
                picture.set_content_fit(Gtk.ContentFit.CONTAIN)
                picture.set_size_request(84, 56)
                picture.add_css_class("pt-thumb")
                picture.set_valign(Gtk.Align.CENTER)
                box.append(picture)
            else:
                icon = Gtk.Image.new_from_icon_name("image-x-generic-symbolic")
                icon.set_pixel_size(22)
                icon.set_valign(Gtk.Align.CENTER)
                icon.set_opacity(0.55)
                icon.set_size_request(84, -1)
                box.append(icon)
        else:
            title, meta = None, None
            icon = Gtk.Image.new_from_icon_name("edit-copy-symbolic")
            icon.set_pixel_size(17)
            icon.set_valign(Gtk.Align.START)
            icon.set_margin_top(3)
            icon.set_opacity(0.5)
            box.append(icon)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)
        text_box.set_valign(Gtk.Align.CENTER if clip.is_image else Gtk.Align.FILL)

        if clip.is_image:
            title_label = Gtk.Label(label=title, xalign=0)
            title_label.add_css_class("pt-row-title")
            text_box.append(title_label)
            meta_label = Gtk.Label(label=meta, xalign=0)
            meta_label.add_css_class("pt-row-sub")
            text_box.append(meta_label)
        else:
            preview = Gtk.Label(label=ui.preview_text(clip.text))
            preview.add_css_class("pt-preview")
            preview.set_xalign(0)
            preview.set_wrap(True)
            preview.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            preview.set_lines(3)
            text_box.append(preview)

            meta = ui.format_age(clip.ts) + " · " + ui.format_number_id(clip.chars) + " karakter"
            if clip.lines > 1:
                meta += " · " + ui.format_number_id(clip.lines) + " baris"
            meta_label = Gtk.Label(label=meta, xalign=0)
            meta_label.add_css_class("pt-row-sub")
            text_box.append(meta_label)

        box.append(text_box)

        if clip.pinned:
            pin = Gtk.Image.new_from_icon_name("starred-symbolic")
            pin.set_pixel_size(15)
            pin.set_valign(Gtk.Align.START)
            pin.set_margin_top(3)
            pin.set_opacity(0.9)
            box.append(pin)

        row.set_child(box)
        return row

    def _image_texts(self, clip: Clip) -> tuple[str, str]:
        width, height = clip.width, clip.height
        if not (width and height):
            width, height = ui.image_size(clip)
        title = "Gambar"
        if width and height:
            title += f" {ui.format_number_id(width)}×{ui.format_number_id(height)}"
        meta = " · ".join(
            part
            for part in (
                ui.mime_label(clip.mime),
                ui.format_bytes(clip.size),
                ui.format_age(clip.ts),
            )
            if part
        )
        return title, meta

    # ------------------------------------------------------------------ filter
    def _filter_row(self, row: Gtk.ListBoxRow) -> bool:
        clip: Clip = row.clip  # type: ignore[attr-defined]
        if not self._query:
            return True
        return match_tokens(self._query, self._filter_haystack(clip))

    @staticmethod
    def _filter_haystack(clip: Clip) -> str:
        if not clip.is_image:
            return clip.text
        parts = ["gambar", "image", ui.mime_label(clip.mime), clip.mime]
        if clip.width and clip.height:
            parts.append(f"{clip.width}x{clip.height}")
        parts.append(ui.format_bytes(clip.size))
        return " ".join(part for part in parts if part)

    def _apply_filter(self, reset_selection: bool = False) -> None:
        self._list.invalidate_filter()
        visible = [row for row in self._rows if self._filter_row(row)]
        if self._rows:
            self._stack.set_visible_child_name("list" if visible else "empty")
            if not visible:
                self._empty_label.set_label(
                    f'Tidak ada hasil untuk "{self._query}".'
                    if self._query
                    else "Belum ada riwayat clipboard."
                )
        else:
            self._stack.set_visible_child_name("empty")
            self._empty_label.set_label("Belum ada riwayat clipboard.")
        if reset_selection or not self._selected_visible(visible):
            self._list.select_row(visible[0] if visible else None)
            if visible:
                ui.scroll_row_into_view(self._scroller, visible[0])

    def _selected_visible(self, visible: list[Gtk.ListBoxRow]) -> bool:
        selected = self._list.get_selected_row()
        return selected is not None and selected in visible

    # --------------------------------------------------------------- filtering
    def _on_search_changed(self, entry: Gtk.Entry) -> None:
        self._query = entry.get_text().strip()
        self._apply_filter(reset_selection=True)

    # ------------------------------------------------------------------- keys
    def _visible_rows(self) -> list[Gtk.ListBoxRow]:
        return [row for row in self._rows if self._filter_row(row)]

    def _move_selection(self, step: int) -> None:
        rows = self._visible_rows()
        if not rows:
            return
        current = self._list.get_selected_row()
        try:
            index = rows.index(current) if current is not None else -1
        except ValueError:
            index = -1
        index = max(0, min(len(rows) - 1, index + step))
        self._list.select_row(rows[index])
        ui.scroll_row_into_view(self._scroller, rows[index])

    def _on_key_pressed(self, controller, keyval, keycode, state) -> bool:
        from gi.repository import Gdk

        control = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if keyval == Gdk.KEY_Escape:
            self.hide_window()
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            self._move_selection(-1)
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            self._move_selection(1)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter):
            self._activate_selected()
            return True
        if control and keyval in (Gdk.KEY_p, Gdk.KEY_P):
            self._toggle_pin_selected()
            return True
        if (control and keyval in (Gdk.KEY_d, Gdk.KEY_D)) or (
            shift and keyval == Gdk.KEY_Delete
        ):
            self._delete_selected()
            return True
        return False

    def _activate_selected(self) -> None:
        row = self._list.get_selected_row()
        if row is not None:
            self._copy_clip(row.clip)  # type: ignore[attr-defined]

    def _on_row_activated(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        self._copy_clip(row.clip)  # type: ignore[attr-defined]

    # ----------------------------------------------------------------- actions
    def _copy_clip(self, clip: Clip) -> None:
        # ui.copy_* menaruh clipboard lewat shell kalau jembatan tersedia:
        # sebagai pemilik selection di Wayland, permintaannya selalu diterima.
        if clip.is_image:
            copied = ui.copy_image_to_clipboard(
                config.IMAGES_DIR / clip.image, clip.mime
            )
        else:
            copied = ui.copy_to_clipboard(clip.text)
        if not copied:
            return
        self._store.touch(clip)
        auto_paste = bool(self._cfg.get("clipboard.auto_paste", True))
        # Jendela JANGAN disembunyikan pada iterasi main loop yang sama.
        # Cadangan GDK baru mengirim permintaan clipboard pada putaran loop
        # berikutnya; kalau jendela disembunyikan lebih dulu, fokus pindah ke
        # aplikasi tujuan sehingga permintaan ditolak dan Ctrl+V menempelkan
        # item lama.
        GLib.timeout_add(CLIPBOARD_SETTLE_MS, self._finish_copy, auto_paste)

    def _finish_copy(self, auto_paste: bool) -> bool:
        self.hide_window()
        if auto_paste:
            # Clipboard sengaja tidak dikosongkan: Ctrl+V manual setelah ini
            # tetap menempelkan item yang sama. Ekstensi yang memfokuskan
            # jendela sebelumnya lalu mengirim Ctrl+V (Ctrl+Shift+V di terminal).
            paste_previous()
        return GLib.SOURCE_REMOVE

    def _toggle_pin_selected(self) -> None:
        row = self._list.get_selected_row()
        if row is None:
            return
        clip: Clip = row.clip  # type: ignore[attr-defined]
        clip.pinned = self._store.toggle_pin(clip)
        key = clip.key
        self.refresh()
        for candidate in self._visible_rows():
            if candidate.clip.key == key:  # type: ignore[attr-defined]
                self._list.select_row(candidate)
                ui.scroll_row_into_view(self._scroller, candidate)
                break

    def _delete_selected(self) -> None:
        row = self._list.get_selected_row()
        if row is None:
            return
        clip: Clip = row.clip  # type: ignore[attr-defined]
        index = self._visible_rows().index(row) if row in self._visible_rows() else 0
        self._store.delete(clip)
        self.refresh()
        rows = self._visible_rows()
        if rows:
            self._list.select_row(rows[min(index, len(rows) - 1)])
        self._count_label.set_label(
            f"{ui.format_number_id(len(self._clips))} item" if self._clips else ""
        )

    # ------------------------------------------------------------------ focus
    def _focus_entry(self) -> bool:
        self._entry.grab_focus()
        return False

    def _on_active_changed(self, window, pspec) -> None:
        if _no_autohide() or not self.get_visible():
            return
        if self.is_active():
            self._was_active = True
            return
        if not self._was_active:
            return
        if time.monotonic() < self._ignore_focus_loss_until:
            return
        self.hide_window()

    def _on_close_request(self, window) -> bool:
        self.hide_window()
        return True

    # ---------------------------------------------------------------- monitor
    def _start_monitor(self) -> None:
        self._stop_monitor()
        try:
            monitor = Gio.File.new_for_path(str(self._store.spool_path)).monitor_file(
                Gio.FileMonitorFlags.NONE, None
            )
        except GLib.Error:
            return
        self._monitor = monitor
        monitor.connect("changed", self._on_spool_changed)

    def _stop_monitor(self) -> None:
        if self._monitor_source:
            GLib.source_remove(self._monitor_source)
            self._monitor_source = 0
        if self._monitor is not None:
            try:
                self._monitor.cancel()
            except Exception:
                pass
            self._monitor = None

    def _on_spool_changed(self, monitor, file, other_file, event_type) -> None:
        if self._monitor_source:
            return
        self._monitor_source = GLib.timeout_add(180, self._on_monitor_tick)

    def _on_monitor_tick(self) -> bool:
        self._monitor_source = 0
        if self.get_visible():
            selected = self._list.get_selected_row()
            self.refresh()
            if selected is not None:
                for row in self._rows:
                    if row.clip.key == selected.clip.key:  # type: ignore[attr-defined]
                        self._list.select_row(row)
                        break
        return False