"""Aplikasi GTK utama: satu proses untuk popup clipboard, launcher, warna, dan lirik."""

from __future__ import annotations

import os
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gio, GLib, Gtk

from . import config, dbusutil, ui
from .apps import UsageStore, load_apps
from .capture import ClipboardPoller
from .color import ColorPicker
from .dbusutil import session_bus
from .discord import DiscordPresence, build_activity, valid_app_id
from .mpris import MprisWatcher
from .sources import activity_type_for, resolve_app_id
from .store import ClipStore
from .ui_clipboard import ClipboardWindow
from .ui_color import ColorResultWindow
from .ui_launcher import LauncherWindow
from .ui_lyrics import LyricsWindow
from .ui_settings import SettingsWindow

CAPTURE_EXTENSION = "extension"
CAPTURE_POLL = "poll"
CAPTURE_NONE = "none"


class PowerToolsApp(Gtk.Application):
    def __init__(self, initial_action: str | None = None) -> None:
        super().__init__(
            application_id=config.APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.cfg = config.Config()
        self.store = ClipStore(
            max_items=int(self.cfg.get("clipboard.max_items", 300)),
            max_chars=int(self.cfg.get("clipboard.max_chars", 100_000)),
            images=bool(self.cfg.get("clipboard.images", True)),
            max_image_bytes=int(self.cfg.get("clipboard.max_image_mb", 20)) * 1024 * 1024,
            max_image_items=int(self.cfg.get("clipboard.max_image_items", 60)),
            max_age_seconds=float(self.cfg.get("clipboard.max_age_minutes", 0) or 0) * 60,
        )
        self.usage = UsageStore()
        self.apps = []
        self.capture_mode = CAPTURE_NONE

        self._clipboard_window: ClipboardWindow | None = None
        self._launcher_window: LauncherWindow | None = None
        self._color_window: ColorResultWindow | None = None
        self._settings_window: SettingsWindow | None = None
        self._lyrics_window: LyricsWindow | None = None
        self._color = ColorPicker(self)
        self._poller: ClipboardPoller | None = None
        self._mpris: MprisWatcher | None = None
        self._lyrics_track = None
        self._lyrics_position = 0.0
        self._lyrics_playing = False
        self._discord: DiscordPresence | None = None
        self._discord_track = None
        self._discord_position = 0.0
        self._discord_playing = False
        self._discord_identity: tuple[str, bool] | None = None
        self._discord_sent_at = 0.0
        self._discord_sent_position = 0.0
        self._discord_show_paused = False
        self._discord_show_album_art = True
        self._discord_status = "Nonaktif"
        self._apps_loading = False
        self._holding = False
        self._shutting_down = False
        self._config_monitor: Gio.FileMonitor | None = None
        self._config_reload_source = 0
        self._compact_source = 0
        self._pending_action: str | None = initial_action
        self._last_show: tuple[str, float] = ("", 0.0)

    # ----------------------------------------------------------------- startup
    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        GLib.set_application_name(config.APP_NAME)
        GLib.set_prgname("powertools")
        ui.install_css()
        self.cfg.ensure_exists()

        for name in ("clipboard", "launcher", "color", "lyrics", "settings", "quit"):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", self._on_app_action)
            self.add_action(action)

        if self.cfg.get("resident", True) and self.cfg.any_module_enabled():
            self._holding = True
            self.hold()

        if self.cfg.module_enabled("launcher"):
            self._load_apps_async()
        if self.cfg.module_enabled("clipboard"):
            self.store.prune()
            self._detect_capture()
            self._schedule_compact(3)
        if self.cfg.module_enabled("lyrics"):
            self._start_lyrics()
        if self.cfg.module_enabled("discord"):
            self._start_discord()

        self._watch_config()

    def do_shutdown(self) -> None:
        self._shutting_down = True
        if self._poller is not None:
            self._poller.stop()
        self._stop_lyrics()
        self._stop_discord()
        self._stop_watcher()
        if self._compact_source:
            GLib.source_remove(self._compact_source)
            self._compact_source = 0
        if self._config_reload_source:
            GLib.source_remove(self._config_reload_source)
            self._config_reload_source = 0
        self._config_monitor = None
        self.usage.save()
        Gtk.Application.do_shutdown(self)

    # ------------------------------------------------------ config berubah sendiri
    def _watch_config(self) -> None:
        """Ikuti config.json yang diubah dari luar GUI (CLI/editor)."""
        try:
            config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            monitor = Gio.File.new_for_path(str(config.CONFIG_DIR))
            self._config_monitor = monitor.monitor_directory(
                Gio.FileMonitorFlags.NONE, None
            )
            self._config_monitor.connect("changed", self._on_config_file_changed)
        except GLib.Error:
            self._config_monitor = None

    def _on_config_file_changed(self, _monitor, file, _other, _event) -> None:
        try:
            name = file.get_basename() if file else ""
        except Exception:
            name = ""
        if name != "config.json":
            return
        if self._config_reload_source:
            return
        # Beri jeda supaya penulisan atomik (tulis + rename) selesai dulu.
        self._config_reload_source = GLib.timeout_add(
            300, self._reload_from_disk
        )

    def _reload_from_disk(self) -> bool:
        self._config_reload_source = 0
        self.reload_config()
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------- command line
    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        args = command_line.get_arguments()[1:]
        action: str | None = None
        for arg in args:
            if arg.startswith("--show="):
                action = arg.split("=", 1)[1]
            elif not arg.startswith("-"):
                action = arg
        if action in ("clipboard", "launcher", "color", "lyrics", "settings", "daemon"):
            self._pending_action = action
        self.activate()
        return 0

    def do_activate(self) -> None:
        action = self._pending_action or "launcher"
        self._pending_action = None
        if action == "daemon":
            # Autostart: cukup jaga proses tetap hidup, jangan tampilkan apa pun.
            # Kalau semua modul dimatikan tidak ada yang perlu dilayani, jadi
            # proses langsung keluar supaya RAM/CPU kembali ke nol.
            if not self.cfg.any_module_enabled():
                GLib.idle_add(self._quit_when_idle_startup)
            return
        self.show_action(action)

    def _quit_when_idle_startup(self) -> bool:
        self.quit()
        return GLib.SOURCE_REMOVE

    def _on_app_action(self, action: Gio.SimpleAction, _param) -> None:
        name = action.get_name()
        if name == "quit":
            self.quit_app()
        else:
            self.show_action(name)

    # -------------------------------------------------------------------- show
    def show_action(self, kind: str) -> None:
        # Modul yang dimatikan tidak boleh bisa dibuka lewat hotkey/D-Bus.
        if kind in config.MODULES and not self.cfg.module_enabled(kind):
            label = config.MODULE_LABELS.get(kind, kind)
            ui.notify(
                self,
                "Modul dimatikan",
                f"{label} sedang nonaktif. Aktifkan lagi di Pengaturan PowerTools.",
            )
            self.open_settings()
            return

        now = time.monotonic()
        previous, timestamp = self._last_show
        # Aktivasi ganda (command-line + D-Bus ActivateAction) diabaikan.
        if previous == kind and now - timestamp < 0.35:
            return
        self._last_show = (kind, now)

        if kind == "clipboard":
            if self._launcher_window is not None:
                self._launcher_window.hide_window()
            # Ekstensi bisa saja baru diaktifkan setelah aplikasi jalan.
            self._detect_capture()
            window = self._ensure_clipboard_window()
            window.set_capture_warning(self.capture_mode == CAPTURE_NONE)
            window.show_window()
        elif kind == "launcher":
            if self._clipboard_window is not None:
                self._clipboard_window.hide_window()
            window = self._ensure_launcher_window()
            window.show_window()
        elif kind == "color":
            if self._clipboard_window is not None:
                self._clipboard_window.hide_window()
            if self._launcher_window is not None:
                self._launcher_window.hide_window()
            if self._color_window is not None:
                self._color_window.hide_window()
            self._color.start()
        elif kind == "lyrics":
            if self._launcher_window is not None:
                self._launcher_window.hide_window()
            self._show_lyrics(focus=True)
        elif kind == "settings":
            self.open_settings()

    def _ensure_clipboard_window(self) -> ClipboardWindow:
        if self._clipboard_window is None:
            self._clipboard_window = ClipboardWindow(self, self.store, self.cfg)
            self._clipboard_window.set_capture_warning(self.capture_mode == CAPTURE_NONE)
        return self._clipboard_window

    def _ensure_launcher_window(self) -> LauncherWindow:
        if self._launcher_window is None:
            self._launcher_window = LauncherWindow(self, self.usage, self.cfg, self.store)
            self._launcher_window.set_apps(self.apps)
        return self._launcher_window

    def _ensure_color_window(self) -> ColorResultWindow:
        if self._color_window is None:
            self._color_window = ColorResultWindow(self, self.store, self.cfg)
        return self._color_window

    def _ensure_lyrics_window(self) -> LyricsWindow:
        if self._lyrics_window is None:
            window = LyricsWindow(self, self.cfg)
            window.set_track(self._lyrics_track)
            window.set_position(self._lyrics_position, self._lyrics_playing)
            self._lyrics_window = window
        return self._lyrics_window

    def _show_lyrics(self, focus: bool = True) -> None:
        window = self._ensure_lyrics_window()
        window.show_window(focus=focus)

    def _ensure_settings_window(self) -> SettingsWindow:
        if self._settings_window is None:
            self._settings_window = SettingsWindow(self, self.cfg)
        return self._settings_window

    def window_hidden(self, window: Gtk.Window) -> None:
        if self.cfg.get("resident", True) and self.cfg.any_module_enabled():
            return
        GLib.idle_add(self._destroy_window, window)
        GLib.idle_add(self._quit_if_idle)

    def _destroy_window(self, window: Gtk.Window) -> bool:
        if window is self._clipboard_window:
            self._clipboard_window = None
        if window is self._launcher_window:
            self._launcher_window = None
        if window is self._color_window:
            self._color_window = None
        if window is self._lyrics_window:
            self._lyrics_window = None
        if window is self._settings_window:
            self._settings_window = None
        try:
            window.destroy()
        except Exception:
            pass
        return False

    def _quit_if_idle(self) -> bool:
        for window in self.get_windows():
            if window.get_visible():
                return False
        self.quit()
        return False

    def quit_app(self) -> None:
        self.usage.save()
        if self._holding:
            self._holding = False
            try:
                self.release()
            except Exception:
                pass
        self.quit()

    # ----------------------------------------------------------------- actions
    def action_clipboard(self) -> None:
        self.show_action("clipboard")

    def action_launcher(self) -> None:
        self.show_action("launcher")

    def action_color(self) -> None:
        self.show_action("color")

    def action_lyrics(self) -> None:
        self.show_action("lyrics")

    def action_settings(self) -> None:
        self.open_settings()

    def open_settings(self) -> None:
        self.cfg.ensure_exists()
        window = self._ensure_settings_window()
        window.show_window()

    def open_config_file(self) -> None:
        """Buka config.json mentah di editor teks (untuk opsi lanjutan)."""
        self.cfg.ensure_exists()
        try:
            Gio.AppInfo.launch_default_for_uri(config.CONFIG_PATH.as_uri(), None)
        except GLib.Error:
            ui.notify(self, "Tidak bisa membuka config", str(config.CONFIG_PATH))

    # ------------------------------------------------------- terapkan pengaturan
    def reload_config(self) -> None:
        """Terapkan config.json yang baru tanpa memulai ulang proses."""
        self.cfg.reload()

        # Batas penyimpanan clipboard ikut berubah tanpa restart.
        try:
            self.store.max_items = int(self.cfg.get("clipboard.max_items", 300))
            self.store.max_chars = int(self.cfg.get("clipboard.max_chars", 100_000))
            self.store.images = bool(self.cfg.get("clipboard.images", True))
            self.store.max_image_bytes = (
                int(self.cfg.get("clipboard.max_image_mb", 20)) * 1024 * 1024
            )
            self.store.max_image_items = int(
                self.cfg.get("clipboard.max_image_items", 60)
            )
            self.store.max_age_seconds = max(
                0.0, float(self.cfg.get("clipboard.max_age_minutes", 0) or 0) * 60
            )
        except (TypeError, ValueError):
            pass

        if self.cfg.module_enabled("clipboard"):
            self._detect_capture()
            self._schedule_compact(3)
        else:
            if self._poller is not None:
                self._poller.stop()
                self._poller = None
            self.capture_mode = CAPTURE_NONE
            if self._clipboard_window is not None:
                self._clipboard_window.set_capture_warning(False)
                if self._clipboard_window.get_visible():
                    self._clipboard_window.hide_window()

        if self.cfg.module_enabled("launcher"):
            if not self.apps and not self._apps_loading:
                self._load_apps_async()
        else:
            # Lepaskan daftar aplikasi: bisa puluhan MB untuk sistem besar.
            self.apps = []
            if self._launcher_window is not None:
                self._launcher_window.set_apps([])
                if self._launcher_window.get_visible():
                    self._launcher_window.hide_window()

        if not self.cfg.module_enabled("color") and self._color_window is not None:
            if self._color_window.get_visible():
                self._color_window.hide_window()

        if self.cfg.module_enabled("lyrics"):
            self._start_lyrics()
            if self._lyrics_window is not None:
                self._lyrics_window.apply_config()
        else:
            self._stop_lyrics()

        if self.cfg.module_enabled("discord"):
            self._start_discord()
        else:
            self._stop_discord()

        self._sync_hold()

    def _sync_hold(self) -> None:
        """Tahan proses hanya kalau masih ada modul yang butuh background."""
        should_hold = bool(self.cfg.get("resident", True)) and (
            self.cfg.any_module_enabled()
        )
        if should_hold and not self._holding:
            self._holding = True
            self.hold()
        elif not should_hold and self._holding:
            self._holding = False
            try:
                self.release()
            except Exception:
                pass
            GLib.idle_add(self._quit_if_idle)

    def apply_color(self, rgb: tuple[int, int, int]) -> None:
        # Format default tetap disalin agar alur cepat "ambil lalu tempel"
        # tidak hilang; jendela hasil menawarkan format lain dan palet.
        fmt = str(self.cfg.get("color.format", "hex"))
        text = ui.format_color(rgb, fmt)
        copied = text if ui.copy_to_clipboard(text) else None
        if copied is not None:
            self.store.touch(copied)
        self._ensure_color_window().show_color(rgb, copied, fmt)

    # ------------------------------------------------------------ MPRIS/lirik
    def _ensure_watcher(self) -> None:
        """Satu pemantau MPRIS dipakai bersama oleh lirik dan Discord."""
        if self._mpris is not None:
            return
        self._mpris = MprisWatcher(self._on_media_track, self._on_media_position)
        self._mpris.start()

    def _stop_watcher(self) -> None:
        if self._mpris is not None:
            try:
                self._mpris.stop()
            except Exception:
                pass
            self._mpris = None

    def _discord_mapping(self) -> dict[str, str]:
        """Peta sumber → Application ID yang valid dari config."""
        raw = self.cfg.get("discord.app_ids", {})
        if not isinstance(raw, dict):
            return {}
        result: dict[str, str] = {}
        for key, value in raw.items():
            if valid_app_id(value):
                result[str(key).strip().lower()] = str(value).strip()
        return result

    def _discord_use_builtin(self) -> bool:
        """True kalau Application ID bawaan PowerTools boleh dipakai."""
        value = self.cfg.get("discord.use_builtin_ids", True)
        return value if isinstance(value, bool) else True

    def _discord_app_id_for(self, track) -> str:
        """Application ID yang dipakai untuk lagu ini.

        Urutan: pemetaan sumber → `default` → ID bawaan sumber → `app_id`
        → ID bawaan `default`. Lihat ``sources.resolve_app_id``.
        """
        source = ""
        if track is not None:
            source = str(getattr(track, "source", "") or "")
        return resolve_app_id(
            source,
            app_ids=self.cfg.get("discord.app_ids", {}),
            app_id=self.cfg.get("discord.app_id", ""),
            use_builtin=self._discord_use_builtin(),
        )

    def _discord_active(self) -> bool:
        """Modul Discord butuh MPRIS kalau ada Application ID yang bisa dipakai."""
        if not self.cfg.module_enabled("discord"):
            return False
        return bool(self._discord_app_id_for(None) or self._discord_mapping())

    def _sync_watcher(self) -> None:
        """Hentikan pemantau hanya kalau tidak ada fitur yang membutuhkannya."""
        if self._shutting_down:
            return
        if self.cfg.module_enabled("lyrics") or self._discord_active():
            self._ensure_watcher()
        else:
            self._stop_watcher()

    def _start_lyrics(self) -> None:
        """Siapkan pemantau MPRIS untuk jendela lirik."""
        self._ensure_watcher()

    def _stop_lyrics(self) -> None:
        self._lyrics_track = None
        self._lyrics_position = 0.0
        self._lyrics_playing = False
        if self._lyrics_window is not None:
            window = self._lyrics_window
            self._lyrics_window = None
            dbusutil.set_lyrics_above(False)
            try:
                window.destroy()
            except Exception:
                pass
        self._sync_watcher()

    def _on_media_track(self, track) -> None:
        self._on_lyrics_track(track)
        if self._discord is None and self._discord_active():
            # Application ID bisa berasal dari pemetaan sumber, jadi sesi
            # Discord baru bisa dimulai saat lagu pertama terdeteksi.
            self._start_discord()
            if self._discord_track is track:
                # _start_discord sudah mengirim activity lagu ini.
                return
        self._on_discord_track(track)

    def _on_media_position(self, seconds: float, playing: bool) -> None:
        self._on_lyrics_position(seconds, playing)
        self._on_discord_position(seconds, playing)

    def _on_lyrics_track(self, track) -> None:
        self._lyrics_track = track
        if self._lyrics_window is not None:
            self._lyrics_window.set_track(track)
        if track is None or not self.cfg.module_enabled("lyrics"):
            return
        # auto_show hanya untuk lagu yang benar-benar sedang diputar, supaya
        # jendela tidak muncul saat pemutar cuma membuka lagu tanpa memutar.
        if not self.cfg.get("lyrics.auto_show", False):
            return
        if not getattr(track, "playing", False):
            return
        if self._lyrics_window is not None and self._lyrics_window.get_visible():
            return
        self._show_lyrics(focus=False)

    def _on_lyrics_position(self, seconds: float, playing: bool) -> None:
        self._lyrics_position = max(0.0, float(seconds or 0))
        self._lyrics_playing = bool(playing)
        if self._lyrics_window is not None:
            self._lyrics_window.set_position(self._lyrics_position, self._lyrics_playing)

    # --------------------------------------------------------------- Discord
    @property
    def discord_status(self) -> str:
        return self._discord_status

    def _start_discord(self) -> None:
        """Siapkan Rich Presence kalau modul aktif dan ada Application ID."""
        if not self.cfg.module_enabled("discord"):
            self._stop_discord()
            return

        app_id = self._discord_app_id_for(self._discord_track)
        if not app_id:
            raw = str(self.cfg.get("discord.app_id", "") or "").strip()
            self._stop_discord(keep_status=True)
            if not self._discord_use_builtin():
                self._set_discord_status(
                    "Preset dimatikan dan sumber ini belum dipetakan — "
                    "pilih preset di Pengaturan → Discord Listening"
                )
            else:
                self._set_discord_status(
                    "Discord Application ID tidak valid"
                    if raw
                    else "Application ID preset tidak tersedia"
                )
            return

        self._discord_show_paused = bool(
            self.cfg.get("discord.show_paused", False)
        )
        self._discord_show_album_art = bool(
            self.cfg.get("discord.show_album_art", True)
        )

        if self._discord is None:
            self._ensure_watcher()
            self._discord = DiscordPresence(
                app_id, on_status=self._on_discord_status
            )
            self._discord_track = (
                self._mpris.current_track if self._mpris is not None else None
            )
            self._discord_position = 0.0
            self._discord_playing = (
                bool(self._discord_track.playing) if self._discord_track else False
            )
            self._discord_identity = None
            self._discord_sent_at = 0.0
            self._discord_sent_position = 0.0
            self._discord.start()
        else:
            # Application ID per sumber bisa berubah tanpa restart proses.
            self._discord.set_app_id(app_id)

        self._discord_sync(force=True)

    def _stop_discord(self, keep_status: bool = False) -> None:
        if self._discord is not None:
            try:
                # stop() mengirim SET_ACTIVITY null lalu menutup socket.
                self._discord.stop()
            except Exception:
                pass
            self._discord = None
        self._discord_track = None
        self._discord_position = 0.0
        self._discord_playing = False
        self._discord_identity = None
        self._discord_sent_at = 0.0
        self._discord_sent_position = 0.0
        if not keep_status:
            self._set_discord_status("Nonaktif")
        self._sync_watcher()

    def _set_discord_status(self, status: str) -> None:
        self._discord_status = status
        if self._settings_window is not None:
            self._settings_window.set_discord_status(status)

    def _on_discord_status(self, status: str) -> None:
        # Callback datang dari thread IPC; UI hanya boleh disentuh di main loop.
        GLib.idle_add(self._apply_discord_status, status)

    def _apply_discord_status(self, status: str) -> bool:
        self._set_discord_status(status)
        return False

    def _on_discord_track(self, track) -> None:
        if self._discord is None:
            return
        previous = self._discord_track
        if track is not None and (
            previous is None or track.key != previous.key
        ):
            self._discord_position = 0.0
        self._discord_track = track
        self._discord_playing = bool(track.playing) if track is not None else False
        self._discord_sync(force=True)

    def _on_discord_position(self, seconds: float, playing: bool) -> None:
        self._discord_position = max(0.0, float(seconds or 0))
        self._discord_playing = bool(playing)
        if self._discord is not None:
            self._discord_sync()

    def _discord_sync(self, force: bool = False) -> None:
        """Kirim activity hanya saat lagu/status berubah atau setelah seek."""
        if self._discord is None:
            return

        track = self._discord_track
        playing = bool(self._discord_playing)
        show_paused = bool(self.cfg.get("discord.show_paused", False))
        show_art = bool(self.cfg.get("discord.show_album_art", True))
        fallback_asset = str(self.cfg.get("discord.fallback_asset", "") or "")

        # Berganti sumber (mis. Spotify → VLC) bisa berarti berganti
        # Application ID, karena Discord memakai nama aplikasi sebagai judul
        # activity. Handshake harus diulang; set_app_id menangani itu.
        desired_app = self._discord_app_id_for(track)
        if not desired_app:
            # Sumber ini sengaja tidak dikirim (preset dimatikan dan tidak ada
            # pemetaan): hapus status yang mungkin masih tertinggal.
            if self._discord_identity is not None:
                self._discord_identity = None
                self._discord_sent_at = 0.0
                self._discord_sent_position = 0.0
                self._discord.clear()
            return
        if desired_app != self._discord.app_id:
            self._discord.set_app_id(desired_app)
            self._discord_identity = None

        if track is None or (not playing and not show_paused):
            if self._discord_identity is not None:
                self._discord_identity = None
                self._discord_sent_at = 0.0
                self._discord_sent_position = 0.0
                self._discord.clear()
            return

        source = str(getattr(track, "source", "") or "")
        activity_type = activity_type_for(source, desired_app)

        # Sumber ikut masuk identity: dua pemutar bisa melaporkan lagu yang
        # sama persis, tetapi kata kerja activity-nya berbeda (YouTube
        # "Watching" vs Spotify "Listening to").
        identity = (track.key, playing, source, activity_type)
        now = time.monotonic()
        if not force and identity == self._discord_identity:
            # Discord menggerakkan progress sendiri dari timestamp; update
            # hanya perlu kalau posisi melompat (seek) atau track berganti.
            # Batas frekuensi SET_ACTIVITY ditangani DiscordPresence.
            expected = self._discord_sent_position
            if playing:
                expected += now - self._discord_sent_at
            if abs(self._discord_position - expected) < 2.0:
                return

        activity = build_activity(
            title=track.title,
            artist=track.artist,
            album=track.album,
            length_us=track.length_us,
            art_url=track.art_url,
            position=self._discord_position,
            playing=playing,
            show_paused=show_paused,
            show_album_art=show_art,
            fallback_asset=fallback_asset,
            activity_type=activity_type,
        )
        if activity is None:
            if self._discord_identity is not None:
                self._discord_identity = None
                self._discord.clear()
            return

        self._discord_identity = identity
        self._discord_sent_at = now
        self._discord_sent_position = max(0.0, self._discord_position)
        self._discord.update(activity)

    def set_lyrics_pinned(self, pinned: bool) -> None:
        """Simpan pilihan pin dari tombol di jendela lirik."""
        pinned = bool(pinned)
        if bool(self.cfg.get("lyrics.pinned", False)) != pinned:
            self.cfg.set("lyrics.pinned", pinned)
            try:
                self.cfg.save()
            except OSError:
                pass
        if self._lyrics_window is not None:
            self._lyrics_window.set_pinned(pinned)

    def set_lyrics_romanize(self, enabled: bool) -> None:
        """Simpan pilihan romaji dari tombol di jendela lirik."""
        enabled = bool(enabled)
        if bool(self.cfg.get("lyrics.romanize", False)) != enabled:
            self.cfg.set("lyrics.romanize", enabled)
            try:
                self.cfg.save()
            except OSError:
                pass
        if self._lyrics_window is not None:
            self._lyrics_window.set_romanize(enabled)

    def seek_lyrics(self, seconds: float) -> bool:
        """Lompat ke detik tertentu dari jendela lirik (klik dua kali).

        Jendela sudah menghitung offset lirik, jadi yang masuk ke sini adalah
        posisi pemutar yang sebenarnya. Return False kalau pemutar tidak ada
        atau menolak (mis. ``CanSeek`` bernilai false).
        """
        if self._mpris is None or not self.cfg.module_enabled("lyrics"):
            return False
        try:
            target = max(0.0, float(seconds))
        except (TypeError, ValueError):
            return False
        if not self._mpris.seek(target):
            return False
        self._lyrics_position = target
        if self._lyrics_window is not None:
            self._lyrics_window.set_position(target, self._lyrics_playing)
        return True

    # ------------------------------------------------------------- background
    def _load_apps_async(self) -> None:
        if self._apps_loading:
            return
        self._apps_loading = True

        def worker() -> None:
            try:
                apps = load_apps()
            except Exception:
                apps = []
            GLib.idle_add(self._apps_loaded, apps)

        threading.Thread(target=worker, name="powertools-apps", daemon=True).start()

    def _apps_loaded(self, apps) -> bool:
        self._apps_loading = False
        if not self.cfg.module_enabled("launcher"):
            # Modul dimatikan saat pemindaian berjalan: buang hasilnya.
            self.apps = []
            return False
        self.apps = apps
        if self._launcher_window is not None:
            self._launcher_window.set_apps(apps)
        return False

    def _schedule_compact(self, seconds: int) -> None:
        """Satu timer pemangkasan saja, dipasang ulang saat modul dinyalakan."""
        if self._compact_source:
            return
        self._compact_source = GLib.timeout_add_seconds(seconds, self._compact_store)

    def _compact_store(self) -> bool:
        self._compact_source = 0
        if not self.cfg.module_enabled("clipboard"):
            return False
        self.store.compact_if_needed()
        self.store.prune()
        # Ulangi tiap 5 menit supaya item kedaluwarsa cepat hilang.
        self._schedule_compact(300)
        return False

    def _detect_capture(self) -> None:
        if not self.cfg.module_enabled("clipboard"):
            if self._poller is not None:
                self._poller.stop()
                self._poller = None
            self.capture_mode = CAPTURE_NONE
            return
        session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
        try:
            bus = session_bus()
            if bus is None:
                raise RuntimeError("tanpa session bus")
            bus.call(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "NameHasOwner",
                GLib.Variant("(s)", ("org.gnome.Shell.Extensions.PowerTools",)),
                None,
                Gio.DBusCallFlags.NONE,
                1500,
                None,
                lambda conn, result: self._on_bridge_check(conn, result, session_type),
            )
        except (GLib.Error, RuntimeError):
            self._on_bridge_check(None, None, session_type)

    def _on_bridge_check(self, conn, result, session_type: str) -> None:
        if not self.cfg.module_enabled("clipboard"):
            self.capture_mode = CAPTURE_NONE
            return False

        owned = False
        if conn is not None and result is not None:
            try:
                owned = bool(conn.call_finish(result).unpack()[0])
            except GLib.Error:
                owned = False

        if owned:
            self.capture_mode = CAPTURE_EXTENSION
        elif session_type == "x11" and self.cfg.get("clipboard.poll_fallback", True):
            self.capture_mode = CAPTURE_POLL
            if self._poller is None:
                self._poller = ClipboardPoller(
                    self.store, int(self.cfg.get("clipboard.poll_ms", 800))
                )
                self._poller.start()
        else:
            self.capture_mode = CAPTURE_NONE

        if self._clipboard_window is not None:
            self._clipboard_window.set_capture_warning(self.capture_mode == CAPTURE_NONE)
        return False