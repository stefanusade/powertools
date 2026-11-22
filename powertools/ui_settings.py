"""Jendela Pengaturan PowerTools — GUI untuk config.json.

Semua perubahan langsung ditulis ke config.json (atomik) lalu diteruskan ke
aplikasi dan ekstensi. Modul yang dimatikan benar-benar berhenti bekerja,
bukan sekadar disembunyikan:

  * clipboard  → ekstensi melepas pemantau selection, hotkey Super+V mati,
                 proses Python tidak lagi memantau/memangkas riwayat.
  * launcher   → hotkey Alt+Space mati dan daftar aplikasi tidak dimuat
                 (tidak ada pemindaian berkas .desktop saat start).
  * color      → hotkey Super+Shift+C mati dan color picker tidak disiapkan.
  * media      → dynamic island tidak dibuat sama sekali (tanpa polling
                 hover, tanpa koneksi MPRIS).
  * lyrics     → pemantau MPRIS berhenti dan jendela lirik ditutup.
  * discord    → koneksi IPC ditutup dan status Listening to dihapus.

Kalau semua modul dimatikan, proses background tidak lagi ditahan
sehingga RAM yang dipakai PowerTools kembali ke nol.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gio, GLib, Gtk

from . import config, ui
from .sources import BUILTIN_APP_IDS

SCHEMA = "org.gnome.shell.extensions.powertools"

# key modul -> (ikon, pintasan, penjelasan, rincian resource)
MODULE_INFO = {
    "clipboard": (
        "edit-paste-symbolic",
        "Super+V",
        "Merekam teks dan gambar yang disalin, lalu menempelkannya kembali.",
        "Mematikan: pemantau clipboard di shell dilepas, hotkey Super+V mati, "
        "pemangkasan riwayat berhenti.",
    ),
    "launcher": (
        "system-search-symbolic",
        "Alt+Space",
        "Mencari aplikasi, menghitung, dan mencari di web dari satu kotak.",
        "Mematikan: hotkey Alt+Space mati dan daftar aplikasi tidak dipindai "
        "saat start (hemat CPU saat login).",
    ),
    "color": (
        "applications-graphics-symbolic",
        "Super+Shift+C",
        "Mengambil warna dari layar beserta kode hex/rgb/hsl/cmyk.",
        "Mematikan: hotkey Super+Shift+C mati dan color picker tidak disiapkan.",
    ),
    "media": (
        "media-playback-start-symbolic",
        "Hover tepi atas",
        "Dynamic island untuk Spotify, browser, VLC, dan pemutar MPRIS lain.",
        "Mematikan: island tidak dibuat, polling hover dan koneksi MPRIS "
        "berhenti total.",
    ),
    "lyrics": (
        "audio-x-generic-symbolic",
        "Super+Shift+L",
        "Lirik lagu mengambang yang menggulir sendiri mengikuti posisi lagu.",
        "Mematikan: pemantau MPRIS berhenti, jendela lirik ditutup, dan "
        "hotkey Super+Shift+L mati.",
    ),
    "discord": (
        "audio-headphones-symbolic",
        "Opsional",
        "Tampilkan lagu yang sedang diputar sebagai status Listening to di Discord.",
        "Mematikan: koneksi IPC ke Discord ditutup dan status Listening to "
        "langsung dihapus.",
    ),
}

FORMATS = ("hex", "rgb", "hsl", "cmyk")


def _rss_mb() -> int | None:
    """RAM proses ini (VmRSS) dalam MB, atau None kalau tidak terbaca."""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return max(1, round(int(line.split()[1]) / 1024))
    except (OSError, ValueError, IndexError):
        return None
    return None


class _GSettings:
    """Pembungkus tipis Gio.Settings untuk skema ekstensi (opsional)."""

    def __init__(self) -> None:
        self._settings = None
        try:
            self._settings = Gio.Settings.new(SCHEMA)
        except Exception:
            self._settings = None

    @property
    def available(self) -> bool:
        return self._settings is not None

    def get_bool(self, key: str, default: bool = True) -> bool:
        try:
            return bool(self._settings.get_boolean(key))
        except Exception:
            return default

    def set_bool(self, key: str, value: bool) -> None:
        try:
            self._settings.set_boolean(key, bool(value))
        except Exception:
            pass

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self._settings.get_int(key))
        except Exception:
            return default

    def set_int(self, key: str, value: int) -> None:
        try:
            self._settings.set_int(key, int(value))
        except Exception:
            pass


class SettingsWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, cfg: config.Config) -> None:
        super().__init__(application=app)
        self._app = app
        self._cfg = cfg
        self._gsettings = _GSettings()
        self._loading = False
        self._save_source = 0
        self._module_switches: dict[str, Gtk.Switch] = {}
        self._module_bodies: dict[str, Gtk.Widget] = {}

        self.set_title("PowerTools Pengaturan")
        self.set_icon_name(config.APP_ID)
        self.add_css_class("pt-window")
        self.set_decorated(False)
        self.set_resizable(True)
        self.set_default_size(
            640 + 2 * ui.SURFACE_MARGIN, 812 + 2 * ui.SURFACE_MARGIN
        )

        self._build()
        self._load_values()
        self._update_status()

        controller = Gtk.EventControllerKey()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(controller)

        self.connect("close-request", self._on_close_request)

    # ------------------------------------------------------------------- build
    def _build(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for edge in ("top", "bottom", "start", "end"):
            getattr(root, f"set_margin_{edge}")(ui.SURFACE_MARGIN)

        surface = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        surface.add_css_class("pt-surface")
        root.append(surface)
        self.set_child(root)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.add_css_class("pt-header")

        icon = Gtk.Image.new_from_icon_name("preferences-system-symbolic")
        icon.set_pixel_size(26)
        icon.set_valign(Gtk.Align.CENTER)
        header.append(icon)

        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        titles.set_hexpand(True)
        titles.set_valign(Gtk.Align.CENTER)
        title = Gtk.Label(label="Pengaturan PowerTools", xalign=0)
        title.add_css_class("pt-title")
        titles.append(title)
        self._status = Gtk.Label(label="", xalign=0)
        self._status.add_css_class("pt-sub")
        titles.append(self._status)
        header.append(titles)

        close = Gtk.Button.new_from_icon_name("window-close-symbolic")
        close.add_css_class("pt-iconbtn")
        close.set_tooltip_text("Tutup (Esc)")
        close.set_valign(Gtk.Align.CENTER)
        close.connect("clicked", lambda *_: self.hide_window())
        header.append(close)
        surface.append(header)

        scroller = Gtk.ScrolledWindow()
        scroller.add_css_class("pt-scroll")
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(self._build_body())
        surface.append(scroller)

        surface.append(self._build_footer())

    def _build_body(self) -> Gtk.Widget:
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.set_margin_start(16)
        body.set_margin_end(16)
        body.set_margin_top(4)
        body.set_margin_bottom(12)

        section = Gtk.Label(label="MODUL", xalign=0)
        section.add_css_class("pt-section")
        body.append(section)

        for name in config.MODULES:
            body.append(self._build_module_card(name))

        section = Gtk.Label(label="UMUM", xalign=0)
        section.add_css_class("pt-section")
        section.set_margin_top(6)
        body.append(section)

        body.append(self._build_general_card())
        return body

    def _build_module_card(self, name: str) -> Gtk.Widget:
        icon_name, shortcut, description, detail = MODULE_INFO[name]

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("pt-card")

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        image = Gtk.Image.new_from_icon_name(icon_name)
        image.set_pixel_size(22)
        image.set_valign(Gtk.Align.CENTER)
        head.append(image)

        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        texts.set_hexpand(True)
        texts.set_valign(Gtk.Align.CENTER)

        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label=config.MODULE_LABELS[name], xalign=0)
        title.add_css_class("pt-row-title")
        title_row.append(title)
        chip = Gtk.Label(label=shortcut)
        chip.add_css_class("pt-chip")
        title_row.append(chip)
        texts.append(title_row)

        subtitle = Gtk.Label(xalign=0, wrap=True, use_markup=True)
        subtitle.add_css_class("pt-row-sub")
        subtitle.set_markup(
            f"{GLib.markup_escape_text(description)}\n"
            f'<span size="smaller" alpha="65%">'
            f"{GLib.markup_escape_text(detail)}</span>"
        )
        texts.append(subtitle)
        head.append(texts)

        switch = Gtk.Switch()
        switch.set_valign(Gtk.Align.CENTER)
        switch.set_tooltip_text("Aktifkan/nonaktifkan modul ini")
        switch.connect("notify::active", self._on_module_toggled, name)
        self._module_switches[name] = switch
        head.append(switch)
        card.append(head)

        options = self._build_module_options(name)
        options.set_margin_top(2)
        self._module_bodies[name] = options
        card.append(options)

        # Klik pada judul/ikon juga mengubah switch.
        click = Gtk.GestureClick()
        click.connect(
            "released",
            lambda *_args: switch.set_active(not switch.get_active()),
        )
        texts.add_controller(click)

        return card

    def _build_module_options(self, name: str) -> Gtk.Widget:
        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(7)
        grid.set_margin_top(3)

        if name == "clipboard":
            self._add_switch(grid, 0, 0, "Tempel otomatis setelah memilih",
                             "clipboard.auto_paste")
            self._add_switch(grid, 0, 1, "Simpan gambar",
                             "clipboard.images")
            self._add_spin(grid, 1, 0, "Maksimal item teks", "clipboard.max_items",
                           10, 5000, 10)
            self._add_spin(grid, 1, 1, "Kedaluwarsa (menit)", "clipboard.max_age_minutes",
                           0, 525_600, 30)
        elif name == "launcher":
            self._add_spin(grid, 0, 0, "Jumlah hasil", "launcher.max_results",
                           3, 30, 1)
            label = Gtk.Label(label="URL pencarian web", xalign=0)
            label.set_margin_top(2)
            grid.attach(label, 0, 1, 4, 1)
            entry = Gtk.Entry()
            entry.add_css_class("pt-field")
            entry.set_hexpand(True)
            entry.set_placeholder_text("https://www.google.com/search?q={query}")
            entry.connect("changed", self._on_value_entry, "launcher.web_search")
            grid.attach(entry, 0, 2, 4, 1)
            self._entry_launcher_web_search = entry
        elif name == "color":
            self._add_choice(grid, 0, 0, "Format warna yang disalin",
                             "color.format", list(FORMATS))
        elif name == "media":
            self._add_gsetting_spin(grid, 0, 0, "Jeda hover (ms)",
                                    "media-island-hover-delay", 0, 10_000, 100)
            self._add_gsetting_spin(grid, 0, 1, "Lama popup (ms)",
                                    "media-island-popup-time", 0, 30_000, 500)
            if not self._gsettings.available:
                note = Gtk.Label(
                    label="Pengaturan waktu island butuh ekstensi GNOME terpasang.",
                    xalign=0, wrap=True)
                note.add_css_class("pt-faint")
                grid.attach(note, 0, 1, 4, 1)
        elif name == "lyrics":
            self._add_switch(grid, 0, 0, "Tampilkan otomatis saat lagu diputar",
                             "lyrics.auto_show")
            self._add_switch(grid, 0, 1, "Sematkan di atas semua jendela",
                             "lyrics.pinned")
            self._add_spin(grid, 1, 0, "Geser lirik (ms, + = lebih cepat)",
                           "lyrics.offset_ms", -10_000, 10_000, 100)
            self._add_spin(grid, 1, 1, "Ukuran teks (pt)",
                           "lyrics.font_size", 9, 28, 1)
            self._add_switch(grid, 2, 0, "Romaji untuk lagu Korea/Jepang",
                             "lyrics.romanize")
        elif name == "discord":
            return self._build_discord_options(grid)

        return grid

    def _build_discord_options(self, grid: Gtk.Grid) -> Gtk.Widget:
        """Isi kartu Discord: ID utama, ID per sumber, dan opsi tampilan."""
        label = Gtk.Label(label="Discord Application ID", xalign=0)
        grid.attach(label, 0, 0, 4, 1)

        entry = Gtk.Entry()
        entry.add_css_class("pt-field")
        entry.set_hexpand(True)
        entry.set_placeholder_text("123456789012345678")
        entry.set_tooltip_text(
            "Application ID dari Discord Developer Portal. Nama aplikasi "
            "di portal itulah yang tampil sebagai \"Listening to ...\"."
        )
        entry.connect("changed", self._on_value_entry, "discord.app_id")
        grid.attach(entry, 0, 1, 4, 1)
        self._entry_discord_app_id = entry

        self._add_switch(grid, 0, 2, "Tampilkan saat lagu dijeda",
                         "discord.show_paused")
        self._add_switch(grid, 1, 2, "Tampilkan cover album",
                         "discord.show_album_art")
        self._add_switch(grid, 0, 3, "Pakai Application ID bawaan PowerTools",
                         "discord.use_builtin_ids")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(grid)

        section = Gtk.Label(label="Application ID per sumber (opsional)",
                            xalign=0)
        section.add_css_class("pt-section")
        box.append(section)

        hint = Gtk.Label(
            label="Discord memakai nama aplikasi sebagai judul \"Listening "
                  "to ...\". Kalau ingin judulnya menyebut sumber, buat "
                  "aplikasi Discord tambahan lalu petakan ID-nya di sini. "
                  "Jangan menamainya persis seperti merek lain — Discord "
                  "melarang impersonasi; beri label tidak resmi, mis. "
                  "\"Spotify (unofficial)\". Baris bertanda \"bawaan\" "
                  "disediakan PowerTools dan hanya dipakai kalau saklar di "
                  "atas aktif.",
            xalign=0, wrap=True)
        hint.add_css_class("pt-faint")
        box.append(hint)

        self._discord_sources_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(self._discord_sources_box)

        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._entry_discord_source_key = Gtk.Entry()
        self._entry_discord_source_key.add_css_class("pt-field")
        self._entry_discord_source_key.set_placeholder_text("sumber")
        self._entry_discord_source_key.set_width_chars(12)
        self._entry_discord_source_key.set_tooltip_text(
            "Nama sumber seperti yang tampil di `powertools doctor`, "
            "mis. spotify, vlc, youtube-music."
        )
        add_row.append(self._entry_discord_source_key)

        self._entry_discord_source_id = Gtk.Entry()
        self._entry_discord_source_id.add_css_class("pt-field")
        self._entry_discord_source_id.set_hexpand(True)
        self._entry_discord_source_id.set_placeholder_text("Application ID")
        add_row.append(self._entry_discord_source_id)

        add_button = Gtk.Button(label="Tambah")
        add_button.connect("clicked", self._on_discord_source_add)
        add_row.append(add_button)
        box.append(add_row)

        asset_label = Gtk.Label(label="Asset cadangan untuk cover lokal",
                                xalign=0)
        asset_label.add_css_class("pt-section")
        box.append(asset_label)

        asset_entry = Gtk.Entry()
        asset_entry.add_css_class("pt-field")
        asset_entry.set_placeholder_text("mis. music")
        asset_entry.set_tooltip_text(
            "Nama asset yang diunggah di Rich Presence → Art Assets. Dipakai "
            "saat cover hanya berupa file lokal (Chromium/YouTube Music) yang "
            "tidak bisa diambil Discord, supaya tidak muncul tanda \"?\"."
        )
        asset_entry.connect(
            "changed", self._on_value_entry, "discord.fallback_asset")
        box.append(asset_entry)
        self._entry_discord_fallback_asset = asset_entry

        link = Gtk.LinkButton(
            uri="https://discord.com/developers/applications",
            label="Buat Application ID gratis",
        )
        link.set_halign(Gtk.Align.START)
        box.append(link)

        note = Gtk.Label(
            label="Discord desktop harus terbuka, dan PowerTools harus "
                  "tetap berjalan di background (resident) agar status "
                  "terus diperbarui.",
            xalign=0, wrap=True)
        note.add_css_class("pt-faint")
        box.append(note)

        self._discord_status_label = Gtk.Label(label="Nonaktif", xalign=0)
        self._discord_status_label.set_wrap(True)
        self._discord_status_label.add_css_class("pt-faint")
        box.append(self._discord_status_label)

        self._rebuild_discord_sources()
        return box

    def _rebuild_discord_sources(self) -> None:
        """Gambar ulang daftar pemetaan sumber → Application ID."""
        box = getattr(self, "_discord_sources_box", None)
        if box is None:
            return
        child = box.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            box.remove(child)
            child = following

        mapping = self._cfg.get("discord.app_ids", {})
        if not isinstance(mapping, dict):
            mapping = {}
        use_builtin = self._cfg.get("discord.use_builtin_ids", True)
        if not isinstance(use_builtin, bool):
            use_builtin = True

        previous = self._loading
        self._loading = True
        try:
            if not mapping and not use_builtin:
                empty = Gtk.Label(
                    label="Belum ada. Semua sumber memakai ID utama di atas.",
                    xalign=0, wrap=True)
                empty.add_css_class("pt-faint")
                box.append(empty)
            for key in sorted(mapping):
                box.append(
                    self._build_discord_source_row(
                        key, str(mapping.get(key) or "")))
            if use_builtin:
                for key in sorted(BUILTIN_APP_IDS):
                    if key in mapping:
                        continue
                    box.append(
                        self._build_builtin_source_row(
                            key, BUILTIN_APP_IDS[key]))
        finally:
            self._loading = previous

    def _build_builtin_source_row(self, key: str, app_id: str) -> Gtk.Widget:
        """Baris hanya-baca untuk Application ID bawaan PowerTools."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        name = Gtk.Label(label=key, xalign=0)
        name.set_size_request(120, -1)
        name.add_css_class("pt-faint")
        row.append(name)

        value = Gtk.Label(label=app_id, xalign=0)
        value.set_hexpand(True)
        value.set_selectable(True)
        value.add_css_class("pt-faint")
        row.append(value)

        tag = Gtk.Label(label="bawaan", xalign=1)
        tag.add_css_class("pt-faint")
        row.append(tag)
        return row

    def _build_discord_source_row(self, key: str, app_id: str) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        name = Gtk.Label(label=key, xalign=0)
        name.set_size_request(120, -1)
        name.add_css_class("pt-faint")
        row.append(name)

        entry = Gtk.Entry()
        entry.add_css_class("pt-field")
        entry.set_hexpand(True)
        entry.set_placeholder_text("Application ID")
        entry.set_text(app_id)
        entry.connect("changed", self._on_discord_source_changed, key)
        row.append(entry)

        remove = Gtk.Button(label="Hapus")
        remove.connect("clicked", self._on_discord_source_remove, key)
        row.append(remove)
        return row

    def _on_discord_source_changed(self, entry: Gtk.Entry, key: str) -> None:
        if self._loading:
            return
        self._store_discord_source(key, entry.get_text().strip())

    def _on_discord_source_add(self, _button: Gtk.Button) -> None:
        key = self._entry_discord_source_key.get_text().strip().lower()
        app_id = self._entry_discord_source_id.get_text().strip()
        if not key or not app_id:
            return
        self._store_discord_source(key, app_id)
        self._entry_discord_source_key.set_text("")
        self._entry_discord_source_id.set_text("")
        self._rebuild_discord_sources()

    def _on_discord_source_remove(self, _button: Gtk.Button, key: str) -> None:
        self._store_discord_source(key, "")
        self._rebuild_discord_sources()

    def _store_discord_source(self, key: str, app_id: str) -> None:
        mapping = self._cfg.get("discord.app_ids", {})
        mapping = dict(mapping) if isinstance(mapping, dict) else {}
        if app_id:
            mapping[key] = app_id
        else:
            mapping.pop(key, None)
        self._cfg.set("discord.app_ids", mapping)
        self._commit()

    def _build_general_card(self) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("pt-card")

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        self._add_switch(
            grid, 0, 0,
            "Tetap berjalan di background (popup instan, pakai RAM)",
            "resident",
        )
        card.append(grid)

        note = Gtk.Label(
            label="Kalau semua modul dimatikan, proses background otomatis "
                  "berhenti supaya RAM dan CPU kembali ke nol. Pengaturan ini "
                  "tetap bisa dibuka lewat menu atau `powertools settings`.",
            xalign=0, wrap=True)
        note.add_css_class("pt-faint")
        card.append(note)
        return card

    def _build_footer(self) -> Gtk.Widget:
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.add_css_class("pt-footer")

        self._saved_label = Gtk.Label(label="Perubahan langsung diterapkan", xalign=0)
        self._saved_label.set_hexpand(True)
        footer.append(self._saved_label)

        raw = Gtk.Button(label="Buka config.json")
        raw.add_css_class("pt-copy")
        raw.set_tooltip_text("Edit langsung berkas JSON-nya")
        raw.connect("clicked", lambda *_: self._open_raw_config())
        footer.append(raw)

        close = Gtk.Button(label="Tutup")
        close.add_css_class("pt-copy")
        close.connect("clicked", lambda *_: self.hide_window())
        footer.append(close)
        return footer

    # ------------------------------------------------------------------ helpers
    def _add_switch(self, grid: Gtk.Grid, row: int, col: int, label: str,
                    dotted: str) -> None:
        text = Gtk.Label(label=label, xalign=0)
        text.set_hexpand(True)
        text.set_valign(Gtk.Align.CENTER)
        switch = Gtk.Switch()
        switch.set_valign(Gtk.Align.CENTER)
        switch.set_halign(Gtk.Align.END)
        switch.connect("notify::active", self._on_value_switch, dotted)
        grid.attach(text, col * 2, row, 1, 1)
        grid.attach(switch, col * 2 + 1, row, 1, 1)
        setattr(self, f"_sw_{dotted.replace('.', '_')}", switch)

    def _add_spin(self, grid: Gtk.Grid, row: int, col: int, label: str,
                  dotted: str, lower: int, upper: int, step: int) -> None:
        text = Gtk.Label(label=label, xalign=0)
        text.set_hexpand(True)
        spin = Gtk.SpinButton.new_with_range(lower, upper, step)
        spin.add_css_class("pt-spin")
        spin.set_valign(Gtk.Align.CENTER)
        spin.set_halign(Gtk.Align.END)
        spin.connect("value-changed", self._on_value_spin, dotted)
        grid.attach(text, col * 2, row, 1, 1)
        grid.attach(spin, col * 2 + 1, row, 1, 1)
        setattr(self, f"_spin_{dotted.replace('.', '_')}", spin)

    def _add_choice(self, grid: Gtk.Grid, row: int, col: int, label: str,
                    dotted: str, choices: list[str]) -> None:
        text = Gtk.Label(label=label, xalign=0)
        text.set_hexpand(True)
        dropdown = Gtk.DropDown.new_from_strings(choices)
        dropdown.add_css_class("pt-drop")
        dropdown.set_valign(Gtk.Align.CENTER)
        dropdown.connect("notify::selected", self._on_value_choice, dotted, choices)
        grid.attach(text, col * 2, row, 1, 1)
        grid.attach(dropdown, col * 2 + 1, row, 1, 1)
        setattr(self, f"_choice_{dotted.replace('.', '_')}", dropdown)

    def _add_gsetting_spin(self, grid: Gtk.Grid, row: int, col: int, label: str,
                           key: str, lower: int, upper: int, step: int) -> None:
        text = Gtk.Label(label=label, xalign=0)
        text.set_hexpand(True)
        spin = Gtk.SpinButton.new_with_range(lower, upper, step)
        spin.add_css_class("pt-spin")
        spin.set_valign(Gtk.Align.CENTER)
        spin.set_halign(Gtk.Align.END)
        spin.set_sensitive(self._gsettings.available)
        spin.connect("value-changed", self._on_gsetting_spin, key)
        grid.attach(text, col * 2, row, 1, 1)
        grid.attach(spin, col * 2 + 1, row, 1, 1)
        setattr(self, f"_gspin_{key.replace('-', '_')}", spin)

    # -------------------------------------------------------------- load/save
    def _load_values(self) -> None:
        self._loading = True
        try:
            for name, switch in self._module_switches.items():
                switch.set_active(self._cfg.module_enabled(name))
                # Application ID Discord boleh diisi sebelum modul dinyalakan,
                # karena ID itulah yang dibutuhkan untuk menyalakannya.
                self._module_bodies[name].set_sensitive(
                    self._cfg.module_enabled(name) or name == "discord")

            self._sw_clipboard_auto_paste.set_active(
                bool(self._cfg.get("clipboard.auto_paste", True)))
            self._sw_clipboard_images.set_active(
                bool(self._cfg.get("clipboard.images", True)))
            self._spin_clipboard_max_items.set_value(
                int(self._cfg.get("clipboard.max_items", 300) or 300))
            self._spin_clipboard_max_age_minutes.set_value(
                int(float(self._cfg.get("clipboard.max_age_minutes", 0) or 0)))

            self._spin_launcher_max_results.set_value(
                int(self._cfg.get("launcher.max_results", 9) or 9))
            self._entry_launcher_web_search.set_text(
                str(self._cfg.get("launcher.web_search", "") or ""))

            fmt = str(self._cfg.get("color.format", "hex") or "hex").lower()
            self._choice_color_format.set_selected(
                FORMATS.index(fmt) if fmt in FORMATS else 0)

            self._gspin_media_island_hover_delay.set_value(
                self._gsettings.get_int("media-island-hover-delay", 1000))
            self._gspin_media_island_popup_time.set_value(
                self._gsettings.get_int("media-island-popup-time", 5000))

            self._sw_lyrics_auto_show.set_active(
                bool(self._cfg.get("lyrics.auto_show", False)))
            self._sw_lyrics_pinned.set_active(
                bool(self._cfg.get("lyrics.pinned", False)))
            self._spin_lyrics_offset_ms.set_value(
                int(self._cfg.get("lyrics.offset_ms", 0) or 0))
            self._spin_lyrics_font_size.set_value(
                int(self._cfg.get("lyrics.font_size", 15) or 15))
            self._sw_lyrics_romanize.set_active(
                bool(self._cfg.get("lyrics.romanize", False)))

            self._entry_discord_app_id.set_text(
                str(self._cfg.get("discord.app_id", "") or ""))
            self._entry_discord_fallback_asset.set_text(
                str(self._cfg.get("discord.fallback_asset", "") or ""))
            self._sw_discord_show_paused.set_active(
                bool(self._cfg.get("discord.show_paused", False)))
            self._sw_discord_show_album_art.set_active(
                bool(self._cfg.get("discord.show_album_art", True)))
            self._sw_discord_use_builtin_ids.set_active(
                bool(self._cfg.get("discord.use_builtin_ids", True)))
            self.set_discord_status(
                getattr(self._app, "discord_status", "Nonaktif"))

            self._sw_resident.set_active(bool(self._cfg.get("resident", True)))
        finally:
            self._loading = False

        # Pemetaan sumber bisa berubah dari CLI/JSON, bukan hanya dari GUI.
        self._rebuild_discord_sources()

    def _on_module_toggled(self, switch: Gtk.Switch, _pspec, name: str) -> None:
        if self._loading:
            return
        value = bool(switch.get_active())
        self._cfg.set(f"modules.{name}", value)
        if name == "media":
            # Samakan saklar lama di gsettings supaya island langsung hilang.
            self._gsettings.set_bool("media-island-enabled", value)
        self._module_bodies[name].set_sensitive(value or name == "discord")
        self._commit()

    def _on_value_switch(self, switch: Gtk.Switch, _pspec, dotted: str) -> None:
        if self._loading:
            return
        self._cfg.set(dotted, bool(switch.get_active()))
        self._commit()
        if dotted == "discord.use_builtin_ids":
            # Daftar bawaan ikut muncul/hilang.
            self._rebuild_discord_sources()

    def _on_value_spin(self, spin: Gtk.SpinButton, dotted: str) -> None:
        if self._loading:
            return
        self._cfg.set(dotted, int(spin.get_value()))
        self._commit()

    def _on_value_entry(self, entry: Gtk.Entry, dotted: str) -> None:
        if self._loading:
            return
        self._cfg.set(dotted, entry.get_text().strip())
        self._commit()

    def _on_value_choice(self, dropdown: Gtk.DropDown, _pspec,
                         dotted: str, choices: list[str]) -> None:
        if self._loading:
            return
        index = int(dropdown.get_selected())
        if 0 <= index < len(choices):
            self._cfg.set(dotted, choices[index])
            self._commit()

    def _on_gsetting_spin(self, spin: Gtk.SpinButton, key: str) -> None:
        if self._loading:
            return
        self._gsettings.set_int(key, int(spin.get_value()))
        self._update_status()

    def _commit(self) -> None:
        """Tunda sedikit supaya geseran spin tidak menulis file berkali-kali."""
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(250, self._flush)

    def _flush(self) -> bool:
        self._save_source = 0
        try:
            self._cfg.save()
        except OSError as exc:
            self._saved_label.set_label(f"Gagal menyimpan: {exc}")
            return GLib.SOURCE_REMOVE
        self._saved_label.set_label("Tersimpan — langsung diterapkan")
        reload = getattr(self._app, "reload_config", None)
        if callable(reload):
            try:
                reload()
            except Exception:
                pass
        self._update_status()
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------ status
    def _update_status(self) -> None:
        active = [name for name in config.MODULES
                  if self._cfg.module_enabled(name)]
        rss = _rss_mb()
        parts = [f"{len(active)}/{len(config.MODULES)} modul aktif"]
        if not active:
            parts.append("proses background akan berhenti (RAM 0)")
        elif rss is not None:
            parts.append(f"RAM proses {rss} MB (termasuk jendela ini)")
        self._status.set_label(" · ".join(parts))

    def set_discord_status(self, status: str) -> None:
        """Dipanggil service saat koneksi Discord berubah."""
        label = getattr(self, "_discord_status_label", None)
        if label is not None:
            label.set_label(str(status))

    # ------------------------------------------------------------------ events
    def _fit_to_content(self) -> bool:
        """Sesuaikan tinggi jendela dengan isinya (dibatasi tinggi layar)."""
        try:
            root = self.get_child()
            surface = root.get_first_child()
            header = surface.get_first_child()
            scroller = header.get_next_sibling()
            footer = scroller.get_next_sibling()
            body = scroller.get_child()
            height = (
                header.get_preferred_size()[1].height
                + body.get_preferred_size()[1].height
                + footer.get_preferred_size()[1].height
                + 2 * ui.SURFACE_MARGIN
                + 4
            )
            width = self.get_width()
        except Exception:
            return GLib.SOURCE_REMOVE

        max_height = 0
        display = self.get_display()
        surface_ = self.get_surface()
        try:
            monitor = display.get_monitor_at_surface(surface_) if surface_ else None
        except Exception:
            monitor = None
        if monitor is None:
            monitor = display.get_monitors().get_item(0)
        if monitor is not None:
            max_height = int(monitor.get_geometry().height * 0.92)
        if max_height > 0:
            height = min(height, max_height)

        self.set_default_size(max(560, width), max(400, height))
        return GLib.SOURCE_REMOVE

    def show_window(self) -> None:
        self._cfg.reload()
        self._load_values()
        self._update_status()
        self.set_visible(True)
        self.present()
        # Ukuran natural baru akurat setelah widget ter-realize.
        GLib.timeout_add(180, self._fit_to_content)

    def _on_key_pressed(self, _controller, keyval, _keycode, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.hide_window()
            return True
        return False

    def _on_close_request(self, *_args) -> bool:
        self.hide_window()
        return True

    def hide_window(self) -> None:
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._flush()
        self.set_visible(False)
        self._app.window_hidden(self)  # type: ignore[attr-defined]

    def _open_raw_config(self) -> None:
        self._cfg.ensure_exists()
        try:
            Gio.AppInfo.launch_default_for_uri(config.CONFIG_PATH.as_uri(), None)
        except GLib.Error:
            ui.notify(self._app, "Tidak bisa membuka config.json",
                      str(config.CONFIG_PATH))
