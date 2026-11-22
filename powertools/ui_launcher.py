"""Launcher gaya PowerToys Run (Alt+Space)."""

from __future__ import annotations

import os
import time
import urllib.parse
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gio, GLib, Gtk

from . import config, ui
from .apps import AppEntry, UsageStore, search_apps
from .matcher import calculate, fuzzy_score
from .store import ClipStore


@dataclass(slots=True)
class Result:
    kind: str
    title: str
    subtitle: str
    icon: object
    app: AppEntry | None = None
    value: str | None = None


ACTIONS = [
    (
        "clipboard",
        "Riwayat Clipboard",
        "Lihat dan tempel item yang pernah disalin",
        "edit-paste-symbolic",
        "clip clipboard riwayat history tempel paste",
    ),
    (
        "color",
        "Ambil Warna dari Layar",
        "Color picker dengan magnifier",
        "applications-graphics-symbolic",
        "warna color picker eyedropper pipet",
    ),
    (
        "lyrics",
        "Lirik Mengambang",
        "Tampilkan lirik lagu yang sedang diputar",
        "audio-x-generic-symbolic",
        "lirik lyrics lagu musik floating",
    ),
    (
        "settings",
        "Pengaturan PowerTools",
        "Atur modul dan opsi tanpa edit config.json",
        "preferences-system-symbolic",
        "pengaturan setting config opsi modul",
    ),
]


def _no_autohide() -> bool:
    return bool(os.environ.get("POWERTOOLS_NO_AUTOHIDE"))


class LauncherWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, usage: UsageStore, cfg, store: ClipStore) -> None:
        super().__init__(application=app)
        self._app = app
        self._usage = usage
        self._cfg = cfg
        self._store = store
        self._results: list[Result] = []
        self._rows: list[Gtk.ListBoxRow] = []
        self._apps: list[AppEntry] = []
        self._ignore_focus_loss_until = 0.0
        self._was_active = False

        self.set_title("PowerTools Launcher")
        self.set_icon_name(config.APP_ID)
        self.add_css_class("pt-window")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_size_request(700 + 2 * ui.SURFACE_MARGIN, -1)
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

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        header.add_css_class("pt-header")

        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="PowerTools", xalign=0)
        title.add_css_class("pt-title")
        title.set_hexpand(True)
        title_row.append(title)
        hint = Gtk.Label(label="Alt+Space", xalign=1)
        hint.add_css_class("pt-faint")
        title_row.append(hint)
        header.append(title_row)

        self._entry = Gtk.Entry()
        self._entry.add_css_class("pt-search")
        self._entry.set_placeholder_text("Cari aplikasi, hitung (contoh: 12*8), atau cari di web…")
        self._entry.set_hexpand(True)
        try:
            self._entry.set_icon_from_icon_name(
                Gtk.EntryIconPosition.PRIMARY, "system-search-symbolic"
            )
        except Exception:
            pass
        self._entry.connect("changed", self._on_changed)
        self._entry.connect("activate", self._on_entry_activate)
        header.append(self._entry)
        surface.append(header)

        self._list = Gtk.ListBox()
        self._list.add_css_class("pt-list")
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.set_activate_on_single_click(True)
        self._list.connect("row-activated", self._on_row_activated)

        self._scroller = Gtk.ScrolledWindow()
        self._scroller.add_css_class("pt-scroll")
        self._scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroller.set_vexpand(True)
        self._scroller.set_min_content_height(340)
        self._scroller.set_child(self._list)
        surface.append(self._scroller)

        surface.append(
            ui.hint_bar(
                "↑↓ pilih   ·   Enter jalankan   ·   Esc tutup",
                "PowerToys Run",
            )
        )

        controller = Gtk.EventControllerKey()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(controller)

    # ------------------------------------------------------------------ public
    def set_apps(self, apps: list[AppEntry]) -> None:
        self._apps = apps
        if self.get_visible():
            self._refresh()

    def show_window(self) -> None:
        if self.get_visible() and self.is_active():
            self.hide_window()
            return
        self._entry.set_text("")
        self._refresh()
        self.set_visible(True)
        self.present()
        GLib.idle_add(self._focus_entry)
        self._ignore_focus_loss_until = time.monotonic() + 0.4
        self._was_active = True

    def hide_window(self) -> None:
        self.set_visible(False)
        self._app.window_hidden(self)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ results
    def _refresh(self) -> None:
        query = self._entry.get_text().strip()
        self._results = self._compute_results(query)
        for row in list(self._rows):
            self._list.remove(row)
        self._rows = []
        for result in self._results:
            row = self._make_row(result)
            self._list.append(row)
            self._rows.append(row)
        rows = max(1, min(len(self._rows), 9))
        self._scroller.set_min_content_height(rows * 54 + 6)
        if self._rows:
            self._list.select_row(self._rows[0])
            ui.scroll_row_into_view(self._scroller, self._rows[0])
        else:
            self._list.select_row(None)

    def _compute_results(self, query: str) -> list[Result]:
        limit = int(self._cfg.get("launcher.max_results", 10))
        results: list[Result] = []

        if not query:
            recent = self._usage.recent(self._apps, 6)
            if not recent:
                recent = self._apps[:6]
            for app in recent:
                results.append(self._app_result(app))
            results.extend(self._action_results(ACTIONS, limit - len(results)))
            return results[:limit]

        calc = calculate(query)
        if calc is not None:
            results.append(
                Result(
                    kind="calc",
                    title=calc,
                    subtitle="Tekan Enter untuk menyalin hasil",
                    icon="accessories-calculator-symbolic",
                    value=calc,
                )
            )

        if len(results) < limit:
            for app in search_apps(self._apps, query, self._usage, limit - len(results)):
                results.append(self._app_result(app))

        if len(results) < limit:
            results.extend(self._action_results(ACTIONS, limit - len(results), query))

        if len(results) < limit and len(query) >= 2:
            template = str(self._cfg.get("launcher.web_search", "https://www.google.com/search?q={query}"))
            url = template.replace("{query}", urllib.parse.quote_plus(query))
            results.append(
                Result(
                    kind="web",
                    title=f'Cari "{query}" di web',
                    subtitle=url,
                    icon="web-browser-symbolic",
                    value=url,
                )
            )
        return results[:limit]

    def _app_result(self, app: AppEntry) -> Result:
        subtitle = app.comment or app.generic or app.executable or "Aplikasi"
        return Result(
            kind="app",
            title=app.name,
            subtitle=subtitle,
            icon=app.icon or "application-x-executable-symbolic",
            app=app,
        )

    def _action_results(self, actions, limit: int, query: str = "") -> list[Result]:
        if limit <= 0:
            return []
        selected: list[tuple[float, tuple]] = []
        for action in actions:
            action_id, title, subtitle, icon, keywords = action
            if query:
                score = max(
                    fuzzy_score(query, title),
                    fuzzy_score(query, keywords) * 0.8,
                )
                if score < 0:
                    continue
            else:
                score = 0.0
            selected.append((score, action))
        selected.sort(key=lambda item: item[0], reverse=True)
        return [
            Result(kind="action", title=title, subtitle=subtitle, icon=icon, value=action_id)
            for _score, (_id, title, subtitle, icon, _kw) in selected[:limit]
        ]

    def _make_row(self, result: Result) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.add_css_class("pt-row")
        row.result = result  # type: ignore[attr-defined]

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        image = ui.make_icon(result.icon, 28)
        image.set_valign(Gtk.Align.CENTER)
        box.append(image)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text_box.set_hexpand(True)
        title = Gtk.Label(label=result.title, xalign=0)
        title.add_css_class("pt-row-title")
        title.set_ellipsize(3)  # Pango.EllipsizeMode.END
        text_box.append(title)
        if result.subtitle:
            subtitle = Gtk.Label(label=result.subtitle, xalign=0)
            subtitle.add_css_class("pt-row-sub")
            subtitle.set_ellipsize(3)
            subtitle.set_lines(1)
            text_box.append(subtitle)
        box.append(text_box)

        badge_text = {
            "calc": "HITUNG",
            "action": "AKSI",
            "web": "WEB",
        }.get(result.kind)
        if badge_text:
            badge = Gtk.Label(label=badge_text)
            badge.add_css_class("pt-badge")
            badge.set_valign(Gtk.Align.CENTER)
            box.append(badge)

        row.set_child(box)
        return row

    # ------------------------------------------------------------------- input
    def _on_changed(self, entry: Gtk.Entry) -> None:
        self._refresh()

    def _on_entry_activate(self, entry: Gtk.Entry) -> None:
        self._activate_selected()

    def _on_row_activated(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        self._execute(row.result)  # type: ignore[attr-defined]

    def _activate_selected(self) -> None:
        row = self._list.get_selected_row()
        if row is not None:
            self._execute(row.result)  # type: ignore[attr-defined]

    def _move_selection(self, step: int) -> None:
        if not self._rows:
            return
        current = self._list.get_selected_row()
        try:
            index = self._rows.index(current) if current is not None else -1
        except ValueError:
            index = -1
        index = max(0, min(len(self._rows) - 1, index + step))
        self._list.select_row(self._rows[index])
        ui.scroll_row_into_view(self._scroller, self._rows[index])

    def _on_key_pressed(self, controller, keyval, keycode, state) -> bool:
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
        return False

    # ----------------------------------------------------------------- execute
    def _execute(self, result: Result) -> None:
        if result.kind == "app" and result.app is not None:
            info = result.app.info
            self.hide_window()
            self._usage.bump(result.app.app_id)
            GLib.idle_add(self._launch_info, info)
        elif result.kind == "calc" and result.value is not None:
            if ui.copy_to_clipboard(result.value):
                self._store.touch(result.value)
                ui.notify(self._app, "Hasil disalin", result.value)
            self.hide_window()
        elif result.kind == "web" and result.value is not None:
            self.hide_window()
            try:
                Gio.AppInfo.launch_default_for_uri(result.value, None)
            except GLib.Error:
                pass
        elif result.kind == "action" and result.value:
            self.hide_window()
            handler = getattr(self._app, f"action_{result.value}", None)
            if handler is not None:
                handler()

    @staticmethod
    def _launch_info(info: Gio.AppInfo) -> bool:
        try:
            info.launch([], None)
        except GLib.Error:
            pass
        return False

    # ------------------------------------------------------------------- focus
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