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

Tampilan: satu tab vertikal per modul di sisi kiri, ditambah tab `Umum` untuk
opsi aplikasi. Menambah fitur baru cukup menambah satu halaman di
`_build_module_page()` tanpa membuat jendela makin panjang dan menyesatkan.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gio, GLib, Gtk

from . import config, ui
from .discord import valid_app_id
from .mpris import list_sources
from .sources import (
    APP_PRESETS,
    KNOWN_SOURCES,
    PRESETS,
    preset_for_app_id,
    source_label,
)

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
        self._module_banners: dict[str, Gtk.Widget] = {}
        self._nav_rows: dict[str, Gtk.ListBoxRow] = {}
        self._nav_pages: dict[Gtk.ListBoxRow, str] = {}
        self._nav_dots: dict[str, Gtk.Widget] = {}
        self._current_page = "general"
        # Sumber MPRIS yang terdeteksi saat tab Discord dibuka; di-cache supaya
        # membangun ulang baris tidak memanggil D-Bus berulang kali.
        self._discord_detected: list = []
        self._discord_detected_loaded = False
        self._discord_scan_pending = False
        self._discord_source_options: list[tuple[str, str]] = []
        self._discord_dropdowns: dict[str, Gtk.DropDown] = {}

        self.set_title("PowerTools Pengaturan")
        self.set_icon_name(config.APP_ID)
        self.add_css_class("pt-window")
        self.set_decorated(False)
        self.set_resizable(True)
        self.set_default_size(
            760 + 2 * ui.SURFACE_MARGIN, 640 + 2 * ui.SURFACE_MARGIN
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

        surface.append(self._build_body())

        surface.append(self._build_footer())

    def _build_body(self) -> Gtk.Widget:
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        body.add_css_class("pt-settings-body")
        body.set_vexpand(True)

        self._stack = Gtk.Stack()
        self._stack.set_hexpand(True)
        self._stack.set_vexpand(True)
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(110)
        self._stack.add_named(self._page(self._build_general_page()), "general")
        for name in config.MODULES:
            self._stack.add_named(
                self._page(self._build_module_page(name)), name)

        body.append(self._build_nav())

        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        separator.add_css_class("pt-nav-sep")
        body.append(separator)
        body.append(self._stack)

        self._select_page("general")
        return body

    # --------------------------------------------------------------------- nav
    def _build_nav(self) -> Gtk.Widget:
        nav = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        nav.add_css_class("pt-nav")
        nav.set_size_request(196, -1)
        nav.set_margin_top(4)
        nav.set_margin_bottom(10)
        nav.set_margin_start(8)
        nav.set_margin_end(6)

        listbox = Gtk.ListBox()
        listbox.add_css_class("pt-nav-list")
        listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        listbox.set_activate_on_single_click(True)
        listbox.connect("row-selected", self._on_nav_selected)
        self._nav_list = listbox

        self._add_nav_row(
            listbox, "general", "preferences-system-symbolic", "Umum")

        listbox.append(self._nav_heading("MODUL"))
        for name in config.MODULES:
            icon_name, _shortcut, description, _detail = MODULE_INFO[name]
            row = self._add_nav_row(
                listbox, name, icon_name, config.MODULE_LABELS[name])
            row.set_tooltip_text(description)

        nav.append(listbox)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        nav.append(spacer)
        version = Gtk.Label(label=f"PowerTools {config.VERSION}", xalign=0)
        version.add_css_class("pt-nav-version")
        nav.append(version)
        return nav

    def _nav_heading(self, text: str) -> Gtk.Widget:
        row = Gtk.ListBoxRow()
        row.add_css_class("pt-nav-head")
        row.set_selectable(False)
        row.set_activatable(False)
        row.set_focusable(False)
        label = Gtk.Label(label=text, xalign=0)
        label.add_css_class("pt-section")
        row.set_child(label)
        return row

    def _add_nav_row(self, listbox: Gtk.ListBox, page: str, icon_name: str,
                     label: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.add_css_class("pt-nav-row")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(16)
        icon.set_valign(Gtk.Align.CENTER)
        box.append(icon)

        text = Gtk.Label(label=label, xalign=0)
        text.add_css_class("pt-nav-label")
        text.set_hexpand(True)
        box.append(text)

        if page != "general":
            # Titik kecil: penanda modul yang sedang dimatikan, supaya tidak
            # perlu membuka tabnya satu per satu. Modul aktif tidak diberi
            # titik agar daftar tidak penuh warna.
            dot = Gtk.Box()
            dot.add_css_class("pt-nav-dot")
            dot.add_css_class("pt-nav-dot-off")
            dot.set_valign(Gtk.Align.CENTER)
            dot.set_visible(False)
            box.append(dot)
            self._nav_dots[page] = dot

        row.set_child(box)
        listbox.append(row)
        self._nav_rows[page] = row
        self._nav_pages[row] = page
        return row

    def _on_nav_selected(self, _listbox: Gtk.ListBox, row) -> None:
        page = self._nav_pages.get(row) if row is not None else None
        if page is None:
            return
        self._current_page = page
        self._stack.set_visible_child_name(page)
        self._on_page_shown(page)
        # Tinggi tiap kategori berbeda; sesuaikan jendela setelah transisi.
        # Hanya setelah jendela benar-benar tampil: sebelum itu lebar alokasi
        # masih 0 dan _fit_to_content akan mengecilkan jendela.
        if self.get_visible() and self.get_width() > 0:
            GLib.timeout_add(60, self._fit_to_content)

    def _on_page_shown(self, page: str) -> None:
        """Muat data yang hanya relevan saat tabnya dibuka."""
        if page == "discord":
            # Daftar aplikasi terbuka dipindai di background supaya jendela
            # Pengaturan tidak menunggu D-Bus saat dibuka.
            self._refresh_discord_detected()
            self._rebuild_discord_sources()

    def _select_page(self, page: str) -> None:
        already = self._stack.get_visible_child_name() == page
        row = self._nav_rows.get(page)
        if row is not None:
            self._nav_list.select_row(row)
        if already:
            # select_row tidak memancarkan "row-selected" kalau barisnya sudah
            # terpilih, jadi hook halaman dipanggil langsung.
            self._current_page = page
            self._on_page_shown(page)

    # ------------------------------------------------------------------- pages
    def _page(self, content: Gtk.Widget) -> Gtk.Widget:
        scroller = Gtk.ScrolledWindow()
        scroller.add_css_class("pt-scroll")
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        # Halaman terlebar (Discord) tidak boleh menentukan lebar jendela;
        # ukuran jendela diatur sendiri oleh _fit_to_content.
        if hasattr(scroller, "set_propagate_natural_width"):
            scroller.set_propagate_natural_width(False)
        scroller.set_child(content)
        return scroller

    def _page_box(self) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.set_margin_start(16)
        page.set_margin_end(16)
        page.set_margin_top(10)
        page.set_margin_bottom(14)
        return page

    def _build_general_page(self) -> Gtk.Widget:
        page = self._page_box()
        section = Gtk.Label(label="APLIKASI", xalign=0)
        section.add_css_class("pt-section")
        page.append(section)
        page.append(self._build_general_card())

        section = Gtk.Label(label="TENTANG", xalign=0)
        section.add_css_class("pt-section")
        page.append(section)
        page.append(self._build_about_card())
        return page

    def _build_module_page(self, name: str) -> Gtk.Widget:
        icon_name, shortcut, description, detail = MODULE_INFO[name]
        page = self._page_box()

        head_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        head_card.add_css_class("pt-card")

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        image = Gtk.Image.new_from_icon_name(icon_name)
        image.set_pixel_size(24)
        image.set_valign(Gtk.Align.CENTER)
        head.append(image)

        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
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

        subtitle = Gtk.Label(label=description, xalign=0, wrap=True)
        subtitle.add_css_class("pt-row-sub")
        texts.append(subtitle)
        head.append(texts)

        switch = Gtk.Switch()
        switch.set_valign(Gtk.Align.CENTER)
        switch.set_tooltip_text("Aktifkan/nonaktifkan modul ini")
        switch.connect("notify::active", self._on_module_toggled, name)
        self._module_switches[name] = switch
        head.append(switch)
        head_card.append(head)

        banner = Gtk.Label(
            label="Modul nonaktif — pengaturan di bawah belum berlaku sampai "
                  "modul dinyalakan.",
            xalign=0, wrap=True)
        banner.add_css_class("pt-banner")
        banner.set_visible(False)
        head_card.append(banner)
        self._module_banners[name] = banner
        page.append(head_card)

        options = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        options.add_css_class("pt-card")
        options.append(self._build_module_options(name))
        self._module_bodies[name] = options
        page.append(options)

        note = Gtk.Label(label=detail, xalign=0, wrap=True)
        note.add_css_class("pt-faint")
        page.append(note)
        return page

    def _sync_module_state(self, name: str) -> None:
        """Samakan isi tab, banner, dan titik navigasi dengan saklar modul."""
        enabled = self._cfg.module_enabled(name)
        body = self._module_bodies.get(name)
        if body is not None:
            # Application ID Discord boleh diisi sebelum modul dinyalakan,
            # karena ID itulah yang dibutuhkan untuk menyalakannya.
            body.set_sensitive(enabled or name == "discord")
        banner = self._module_banners.get(name)
        if banner is not None:
            banner.set_visible(not enabled)
        dot = self._nav_dots.get(name)
        if dot is not None:
            dot.set_visible(not enabled)

    def _build_module_options(self, name: str) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        if name == "clipboard":
            self._add_switch(box, "Tempel otomatis setelah memilih",
                             "clipboard.auto_paste")
            self._add_switch(box, "Simpan gambar di riwayat",
                             "clipboard.images")
            self._add_spin(box, "Maksimal item teks", "clipboard.max_items",
                           10, 5000, 10)
            self._add_spin(box, "Kedaluwarsa item (menit, 0 = selamanya)",
                           "clipboard.max_age_minutes", 0, 525_600, 30)
        elif name == "launcher":
            self._add_spin(box, "Jumlah hasil", "launcher.max_results",
                           3, 30, 1)
            entry = self._add_entry(
                box, "URL pencarian web", "launcher.web_search",
                "https://www.google.com/search?q={query}")
            self._entry_launcher_web_search = entry
        elif name == "color":
            self._add_choice(box, "Format warna yang disalin",
                             "color.format", list(FORMATS))
        elif name == "media":
            self._add_gsetting_spin(box, "Jeda hover (ms)",
                                    "media-island-hover-delay", 0, 10_000, 100)
            self._add_gsetting_spin(box, "Lama popup (ms)",
                                    "media-island-popup-time", 0, 30_000, 500)
            if not self._gsettings.available:
                note = Gtk.Label(
                    label="Pengaturan waktu island butuh ekstensi GNOME terpasang.",
                    xalign=0, wrap=True)
                note.add_css_class("pt-faint")
                note.set_margin_top(4)
                box.append(note)
        elif name == "lyrics":
            self._add_switch(box, "Tampilkan otomatis saat lagu diputar",
                             "lyrics.auto_show")
            self._add_switch(box, "Sematkan di atas semua jendela",
                             "lyrics.pinned")
            self._add_switch(box, "Sembunyikan jendela lirik dari taskbar",
                             "lyrics.hide_from_taskbar")
            self._add_switch(box, "Romaji untuk lagu Korea/Jepang",
                             "lyrics.romanize")
            self._add_spin(box, "Geser lirik (ms, + = lebih cepat)",
                           "lyrics.offset_ms", -10_000, 10_000, 100)
            self._add_spin(box, "Ukuran teks (pt)",
                           "lyrics.font_size", 9, 28, 1)
            self._add_scale(box, "Kepekatan latar (%)",
                            "lyrics.opacity", 30, 100, 5)
            hint = Gtk.Label(
                label="Turunkan nilainya agar jendela di belakang lirik ikut "
                      "terlihat. Hanya latar kartu yang dibuat tembus "
                      "pandang; teks tetap pekat supaya mudah dibaca.",
                xalign=0, wrap=True)
            hint.add_css_class("pt-faint")
            hint.set_margin_top(2)
            box.append(hint)

            seek_hint = Gtk.Label(
                label="Klik dua kali pada baris lirik untuk melompat ke bagian "
                      "itu (butuh pemutar yang mendukung MPRIS Seek).",
                xalign=0, wrap=True)
            seek_hint.add_css_class("pt-faint")
            seek_hint.set_margin_top(4)
            box.append(seek_hint)
        elif name == "discord":
            return self._build_discord_options(box)

        return box

    # ------------------------------------------------------------- Discord
    def _build_discord_options(self, box: Gtk.Box) -> Gtk.Widget:
        """Isi tab Discord: pemetaan sumber ke judul Listening to.

        Application ID tidak lagi diketik: semua preset paten disediakan
        PowerTools. Pengguna hanya memilih sumber mana yang memakai preset.
        """
        self._add_switch(box, "Tampilkan saat lagu dijeda",
                         "discord.show_paused")
        self._add_switch(box, "Tampilkan cover album",
                         "discord.show_album_art")

        section = Gtk.Label(label="Judul \"Listening to ...\"", xalign=0)
        section.add_css_class("pt-section")
        box.append(section)

        hint = Gtk.Label(
            label="Judul di Discord mengikuti nama aplikasi preset, bukan "
                  "nama pemutar. Pilih judul untuk tiap sumber; pilihan "
                  "tersimpan dan tetap berlaku setelah PowerTools dimulai "
                  "ulang.",
            xalign=0, wrap=True)
        hint.add_css_class("pt-faint")
        box.append(hint)

        self._discord_sources_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.append(self._discord_sources_box)

        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_row.set_margin_top(6)
        self._discord_source_choice = Gtk.DropDown()
        self._discord_source_choice.add_css_class("pt-drop")
        self._discord_source_choice.set_hexpand(True)
        self._discord_source_choice.set_tooltip_text(
            "Sumber yang sedang terbuka muncul di sini. Pilih satu, lalu "
            "tentukan judul Listening to yang dipakai.")
        add_row.append(self._discord_source_choice)

        self._discord_preset_choice = Gtk.DropDown.new_from_strings(
            [preset["label"] for preset in APP_PRESETS])
        self._discord_preset_choice.add_css_class("pt-drop")
        self._discord_preset_choice.set_size_request(170, -1)
        add_row.append(self._discord_preset_choice)

        self._discord_add_button = Gtk.Button(label="Tambah")
        self._discord_add_button.connect(
            "clicked", self._on_discord_source_add)
        add_row.append(self._discord_add_button)
        box.append(add_row)

        tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        tools.set_margin_top(4)
        refresh = Gtk.Button(label="Muat ulang daftar aplikasi")
        refresh.connect("clicked", self._on_discord_refresh)
        tools.append(refresh)
        self._discord_detected_label = Gtk.Label(label="", xalign=0)
        self._discord_detected_label.add_css_class("pt-faint")
        self._discord_detected_label.set_hexpand(True)
        # Label ini hanya info; jangan sampai menentukan lebar jendela.
        self._discord_detected_label.set_ellipsize(3)
        tools.append(self._discord_detected_label)
        box.append(tools)

        self._entry_discord_fallback_asset = self._add_entry(
            box, "Asset cadangan untuk cover lokal", "discord.fallback_asset",
            "mis. music")
        self._entry_discord_fallback_asset.set_tooltip_text(
            "Nama asset yang diunggah di Rich Presence → Art Assets. Dipakai "
            "saat cover hanya berupa file lokal (Chromium/YouTube Music) yang "
            "tidak bisa diambil Discord, supaya tidak muncul tanda \"?\"."
        )

        note = Gtk.Label(
            label="Discord desktop harus terbuka; PowerTools tetap berjalan "
                  "di background (resident) agar status terus diperbarui.",
            xalign=0, wrap=True)
        note.add_css_class("pt-faint")
        box.append(note)

        self._discord_status_label = Gtk.Label(label="Nonaktif", xalign=0)
        self._discord_status_label.set_wrap(True)
        self._discord_status_label.add_css_class("pt-faint")
        box.append(self._discord_status_label)

        self._rebuild_discord_sources()
        return box

    @staticmethod
    def _clear_box(box: Gtk.Box) -> None:
        child = box.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            box.remove(child)
            child = following

    def _discord_use_builtin(self) -> bool:
        value = self._cfg.get("discord.use_builtin_ids", True)
        return value if isinstance(value, bool) else True

    def _discord_mapping(self) -> dict[str, str]:
        """Peta sumber → Application ID yang valid dari config."""
        raw = self._cfg.get("discord.app_ids", {})
        if not isinstance(raw, dict):
            return {}
        result: dict[str, str] = {}
        for key, value in raw.items():
            if valid_app_id(value):
                result[str(key).strip().lower()] = str(value).strip()
        return result

    def _refresh_discord_detected(self, force: bool = False) -> None:
        """Pindai sumber MPRIS yang sedang terbuka di thread terpisah.

        Query D-Bus bisa memakan beberapa detik kalau ada banyak pemutar, jadi
        UI tidak boleh menunggunya. Hasilnya dipasang lewat main loop.
        """
        if self._discord_detected_loaded and not force:
            return
        if self._discord_scan_pending:
            return
        self._discord_scan_pending = True
        label = getattr(self, "_discord_detected_label", None)
        if label is not None:
            label.set_label("Memindai aplikasi…")
        threading.Thread(target=self._scan_discord_sources, daemon=True).start()

    def _scan_discord_sources(self) -> None:
        try:
            found = list_sources()
        except Exception:
            found = []
        GLib.idle_add(self._apply_discord_detected, found)

    def _apply_discord_detected(self, found: list) -> bool:
        self._discord_scan_pending = False
        self._discord_detected = list(found)
        self._discord_detected_loaded = True
        try:
            self._rebuild_discord_sources()
        except Exception:
            # Jendela bisa saja sudah ditutup saat pemindaian selesai.
            pass
        return GLib.SOURCE_REMOVE

    def _rebuild_discord_sources(self) -> None:
        """Gambar ulang daftar sumber dan dropdown preset-nya."""
        box = getattr(self, "_discord_sources_box", None)
        if box is None:
            return
        self._clear_box(box)
        self._discord_dropdowns = {}

        mapping = self._discord_mapping()
        detected = {info.source: info for info in self._discord_detected}
        # "default" bukan pemutar; ia cadangan untuk sumber yang tidak
        # dipetakan, jadi ditampilkan di baris "Sumber lain", bukan sebagai
        # baris sumber.
        fallback_id = mapping.pop("default", "")
        sources = sorted(
            set(mapping) | set(detected),
            key=lambda key: source_label(key).lower(),
        )

        previous = self._loading
        self._loading = True
        try:
            if not sources:
                empty = Gtk.Label(
                    label="Belum ada pemutar terdeteksi. Buka pemutar musik "
                          "lalu tekan \"Muat ulang daftar aplikasi\".",
                    xalign=0, wrap=True)
                empty.add_css_class("pt-faint")
                box.append(empty)
            for source in sources:
                box.append(self._build_discord_source_row(
                    source, mapping.get(source, ""), detected.get(source)))
            box.append(self._build_discord_fallback_row(fallback_id))
            legacy = self._build_discord_legacy_row()
            if legacy is not None:
                box.append(legacy)
        finally:
            self._loading = previous

        self._refresh_discord_add_choices(sources)
        if hasattr(self, "_discord_detected_label"):
            count = len(self._discord_detected)
            self._discord_detected_label.set_label(
                f"{count} aplikasi terdeteksi" if count
                else "Tidak ada aplikasi MPRIS yang terbuka")

    def _build_discord_source_row(self, source: str, app_id: str,
                                  info) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.add_css_class("pt-optrow")

        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        texts.set_hexpand(True)
        name = Gtk.Label(label=source_label(source), xalign=0)
        name.add_css_class("pt-row-title")
        texts.append(name)

        detail_parts = []
        if info is None:
            detail_parts.append("tersimpan")
        else:
            detail_parts.append("sedang diputar" if info.playing else "terbuka")
            if info.title:
                detail = info.title
                if info.artist:
                    detail = f"{detail} — {info.artist}"
                detail_parts.append(detail)
        detail = Gtk.Label(label=" · ".join(detail_parts), xalign=0)
        detail.add_css_class("pt-faint")
        texts.append(detail)
        row.append(texts)

        labels, keys = self._discord_preset_choices(app_id)
        dropdown = Gtk.DropDown.new_from_strings(labels)
        dropdown.add_css_class("pt-drop")
        dropdown.set_size_request(180, -1)
        dropdown.set_selected(self._discord_preset_index(app_id, keys))
        dropdown.connect(
            "notify::selected", self._on_discord_source_preset, source, keys)
        self._discord_dropdowns[source] = dropdown
        row.append(dropdown)

        remove = Gtk.Button(label="Hapus")
        remove.connect("clicked", self._on_discord_source_remove, source)
        row.append(remove)
        return row

    def _discord_preset_choices(self, app_id: str) -> tuple[list[str], list[str]]:
        labels = ["Otomatis"] + [preset["label"] for preset in APP_PRESETS]
        keys = [""] + [preset["key"] for preset in APP_PRESETS]
        if app_id and preset_for_app_id(app_id) is None:
            labels.append("Application ID lama")
            keys.append("__legacy__")
        return labels, keys

    def _discord_preset_index(self, app_id: str, keys: list[str]) -> int:
        if not app_id:
            return 0
        for index, key in enumerate(keys):
            if key == "__legacy__":
                return index
            preset = PRESETS.get(key)
            if preset is not None and preset["app_id"] == app_id:
                return index
        return 0

    def _build_discord_fallback_row(self, default_id: str = "") -> Gtk.Widget:
        """Baris cadangan: dipakai sumber yang tidak punya pilihan sendiri."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.add_css_class("pt-optrow")

        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        texts.set_hexpand(True)
        name = Gtk.Label(label="Sumber lain", xalign=0)
        name.add_css_class("pt-row-title")
        texts.append(name)

        detail = Gtk.Label(
            label="Dipakai kalau sumbernya belum punya pilihan sendiri."
                  if self._discord_use_builtin()
                  else "Preset dimatikan: sumber tanpa pilihan tidak dikirim "
                       "ke Discord.",
            xalign=0, wrap=True)
        detail.add_css_class("pt-faint")
        texts.append(detail)
        row.append(texts)

        labels, keys = self._discord_fallback_choices(default_id)
        dropdown = Gtk.DropDown.new_from_strings(labels)
        dropdown.add_css_class("pt-drop")
        dropdown.set_size_request(180, -1)
        dropdown.set_selected(
            self._discord_fallback_index(default_id, keys))
        dropdown.connect(
            "notify::selected", self._on_discord_fallback_selected, keys)
        self._discord_dropdowns["default"] = dropdown
        row.append(dropdown)
        return row

    def _discord_fallback_choices(
        self, default_id: str
    ) -> tuple[list[str], list[str]]:
        labels = ["Otomatis (bawaan)", "Tidak dikirim"] + [
            preset["label"] for preset in APP_PRESETS]
        keys = ["auto", "none"] + [preset["key"] for preset in APP_PRESETS]
        if default_id and preset_for_app_id(default_id) is None:
            labels.append("Application ID lama")
            keys.append("__legacy__")
        return labels, keys

    def _discord_fallback_index(self, default_id: str, keys: list[str]) -> int:
        if not default_id:
            return 0 if self._discord_use_builtin() else 1
        for index, key in enumerate(keys):
            if key == "__legacy__":
                return index
            preset = PRESETS.get(key)
            if preset is not None and preset["app_id"] == default_id:
                return index
        return 0

    def _on_discord_fallback_selected(self, dropdown: Gtk.DropDown, _pspec,
                                      keys: list[str]) -> None:
        if self._loading:
            return
        index = int(dropdown.get_selected())
        if index < 0 or index >= len(keys):
            return
        key = keys[index]
        if key == "__legacy__":
            return

        mapping = self._discord_mapping()
        if key == "auto":
            mapping.pop("default", None)
            self._cfg.set("discord.use_builtin_ids", True)
        elif key == "none":
            mapping.pop("default", None)
            self._cfg.set("discord.use_builtin_ids", False)
        else:
            preset = PRESETS.get(key)
            if preset is None:
                return
            mapping["default"] = preset["app_id"]
        self._cfg.set("discord.app_ids", mapping)
        self._commit()
        self._rebuild_discord_sources()

    def _build_discord_legacy_row(self) -> Gtk.Widget | None:
        """Baris untuk `discord.app_id` lama yang masih dihormati."""
        legacy = str(self._cfg.get("discord.app_id", "") or "").strip()
        if not valid_app_id(legacy):
            return None

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.add_css_class("pt-optrow")

        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        texts.set_hexpand(True)
        name = Gtk.Label(label="Application ID lama", xalign=0)
        name.add_css_class("pt-row-title")
        texts.append(name)
        detail = Gtk.Label(
            label="Dari config lama, masih dipakai kalau tidak ada preset.",
            xalign=0, wrap=True)
        detail.add_css_class("pt-faint")
        texts.append(detail)
        row.append(texts)

        value = Gtk.Label(label=legacy, xalign=0)
        value.set_selectable(True)
        value.add_css_class("pt-faint")
        row.append(value)

        clear = Gtk.Button(label="Hapus")
        clear.connect("clicked", self._on_discord_legacy_clear)
        row.append(clear)
        return row

    def _refresh_discord_add_choices(self, existing: list[str]) -> None:
        """Isi dropdown "Tambah" dengan sumber yang belum punya baris.

        Sumber yang sedang terbuka sudah otomatis punya baris sendiri, jadi
        yang tersisa di sini adalah pemutar yang sedang tidak jalan.
        """
        choice = getattr(self, "_discord_source_choice", None)
        if choice is None:
            return

        seen = set(existing)
        options: list[tuple[str, str]] = []
        for key in KNOWN_SOURCES:
            if key in seen:
                continue
            seen.add(key)
            options.append((key, source_label(key)))

        self._discord_source_options = options
        labels = [label for _key, label in options]
        if not labels:
            labels = ["Semua sumber sudah terdaftar"]
        choice.set_model(Gtk.StringList.new(labels))
        choice.set_sensitive(bool(options))
        if hasattr(self, "_discord_add_button"):
            self._discord_add_button.set_sensitive(bool(options))

    def _on_discord_source_preset(self, dropdown: Gtk.DropDown, _pspec,
                                  source: str, keys: list[str]) -> None:
        if self._loading:
            return
        index = int(dropdown.get_selected())
        if index < 0 or index >= len(keys):
            return
        key = keys[index]
        if key == "__legacy__":
            return
        self._store_discord_preset(source, key)

    def _on_discord_source_add(self, _button: Gtk.Button) -> None:
        index = int(self._discord_source_choice.get_selected())
        if index < 0 or index >= len(self._discord_source_options):
            return
        source = self._discord_source_options[index][0]
        preset_index = int(self._discord_preset_choice.get_selected())
        if preset_index < 0 or preset_index >= len(APP_PRESETS):
            return
        self._store_discord_preset(source, APP_PRESETS[preset_index]["key"])
        self._rebuild_discord_sources()

    def _on_discord_source_remove(self, _button: Gtk.Button,
                                  source: str) -> None:
        self._store_discord_preset(source, "")
        self._rebuild_discord_sources()

    def _on_discord_refresh(self, _button: Gtk.Button) -> None:
        self._refresh_discord_detected(force=True)

    def _on_discord_legacy_clear(self, _button: Gtk.Button) -> None:
        self._cfg.set("discord.app_id", "")
        self._commit()
        self._rebuild_discord_sources()

    def _store_discord_preset(self, source: str, preset_key: str) -> None:
        mapping = self._discord_mapping()
        if preset_key:
            preset = PRESETS.get(preset_key)
            if preset is None:
                return
            mapping[source] = preset["app_id"]
        else:
            mapping.pop(source, None)
        self._cfg.set("discord.app_ids", mapping)
        self._commit()

    def _build_general_card(self) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("pt-card")

        options = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._add_switch(
            options,
            "Tetap berjalan di background (popup instan, pakai RAM)",
            "resident",
        )
        card.append(options)

        note = Gtk.Label(
            label="Kalau semua modul dimatikan, proses background otomatis "
                  "berhenti supaya RAM dan CPU kembali ke nol. Pengaturan ini "
                  "tetap bisa dibuka lewat menu atau `powertools settings`.",
            xalign=0, wrap=True)
        note.add_css_class("pt-faint")
        card.append(note)
        return card

    def _build_about_card(self) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        card.add_css_class("pt-card")

        rows = (
            ("Versi", config.VERSION),
            ("Berkas konfigurasi", str(config.CONFIG_PATH)),
            ("Berkas data", str(config.DATA_DIR)),
        )
        for label, value in rows:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            name = Gtk.Label(label=label, xalign=0)
            name.add_css_class("pt-row-sub")
            name.set_size_request(132, -1)
            row.append(name)
            text = Gtk.Label(label=value, xalign=0, wrap=True)
            text.add_css_class("pt-faint")
            text.set_hexpand(True)
            text.set_selectable(True)
            row.append(text)
            card.append(row)
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
    def _option_row(self, text: str, control: Gtk.Widget) -> Gtk.Box:
        """Satu baris pengaturan: label di kiri, kontrol di kanan."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        row.add_css_class("pt-optrow")

        label = Gtk.Label(label=text, xalign=0)
        label.set_wrap(True)
        label.set_hexpand(True)
        label.set_valign(Gtk.Align.CENTER)
        row.append(label)

        control.set_valign(Gtk.Align.CENTER)
        control.set_halign(Gtk.Align.END)
        row.append(control)
        return row

    def _add_switch(self, box: Gtk.Box, label: str, dotted: str) -> None:
        switch = Gtk.Switch()
        switch.connect("notify::active", self._on_value_switch, dotted)
        box.append(self._option_row(label, switch))
        setattr(self, f"_sw_{dotted.replace('.', '_')}", switch)

    def _add_spin(self, box: Gtk.Box, label: str, dotted: str,
                  lower: int, upper: int, step: int) -> None:
        spin = Gtk.SpinButton.new_with_range(lower, upper, step)
        spin.add_css_class("pt-spin")
        spin.set_width_chars(6)
        spin.connect("value-changed", self._on_value_spin, dotted)
        box.append(self._option_row(label, spin))
        setattr(self, f"_spin_{dotted.replace('.', '_')}", spin)

    def _add_scale(self, box: Gtk.Box, label: str, dotted: str,
                   lower: int, upper: int, step: int) -> None:
        scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, lower, upper, step)
        scale.add_css_class("pt-scale")
        scale.set_digits(0)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.set_size_request(150, -1)
        scale.connect("value-changed", self._on_value_scale, dotted)
        box.append(self._option_row(label, scale))
        setattr(self, f"_scale_{dotted.replace('.', '_')}", scale)

    def _add_choice(self, box: Gtk.Box, label: str, dotted: str,
                    choices: list[str]) -> None:
        dropdown = Gtk.DropDown.new_from_strings(choices)
        dropdown.add_css_class("pt-drop")
        dropdown.connect("notify::selected", self._on_value_choice, dotted, choices)
        box.append(self._option_row(label, dropdown))
        setattr(self, f"_choice_{dotted.replace('.', '_')}", dropdown)

    def _add_entry(self, box: Gtk.Box, label: str, dotted: str,
                   placeholder: str = "") -> Gtk.Entry:
        """Entry dengan label di atasnya: teks panjang butuh lebar penuh."""
        text = Gtk.Label(label=label, xalign=0)
        text.set_margin_top(4)
        box.append(text)

        entry = Gtk.Entry()
        entry.add_css_class("pt-field")
        entry.set_hexpand(True)
        if placeholder:
            entry.set_placeholder_text(placeholder)
        entry.connect("changed", self._on_value_entry, dotted)
        box.append(entry)
        setattr(self, f"_entry_{dotted.replace('.', '_')}", entry)
        return entry

    def _add_gsetting_spin(self, box: Gtk.Box, label: str, key: str,
                           lower: int, upper: int, step: int) -> None:
        spin = Gtk.SpinButton.new_with_range(lower, upper, step)
        spin.add_css_class("pt-spin")
        spin.set_width_chars(6)
        spin.set_sensitive(self._gsettings.available)
        spin.connect("value-changed", self._on_gsetting_spin, key)
        box.append(self._option_row(label, spin))
        setattr(self, f"_gspin_{key.replace('-', '_')}", spin)

    # -------------------------------------------------------------- load/save
    def _load_values(self) -> None:
        self._loading = True
        try:
            for name, switch in self._module_switches.items():
                switch.set_active(self._cfg.module_enabled(name))
                self._sync_module_state(name)

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
            self._sw_lyrics_hide_from_taskbar.set_active(
                bool(self._cfg.get("lyrics.hide_from_taskbar", True)))
            self._spin_lyrics_offset_ms.set_value(
                int(self._cfg.get("lyrics.offset_ms", 0) or 0))
            self._spin_lyrics_font_size.set_value(
                int(self._cfg.get("lyrics.font_size", 15) or 15))
            self._sw_lyrics_romanize.set_active(
                bool(self._cfg.get("lyrics.romanize", False)))
            self._scale_lyrics_opacity.set_value(
                int(self._cfg.get("lyrics.opacity", 94) or 94))

            self._entry_discord_fallback_asset.set_text(
                str(self._cfg.get("discord.fallback_asset", "") or ""))
            self._sw_discord_show_paused.set_active(
                bool(self._cfg.get("discord.show_paused", False)))
            self._sw_discord_show_album_art.set_active(
                bool(self._cfg.get("discord.show_album_art", True)))
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
        self._sync_module_state(name)
        self._commit()

    def _on_value_switch(self, switch: Gtk.Switch, _pspec, dotted: str) -> None:
        if self._loading:
            return
        self._cfg.set(dotted, bool(switch.get_active()))
        self._commit()

    def _on_value_spin(self, spin: Gtk.SpinButton, dotted: str) -> None:
        if self._loading:
            return
        self._cfg.set(dotted, int(spin.get_value()))
        self._commit()

    def _on_value_scale(self, scale: Gtk.Scale, dotted: str) -> None:
        if self._loading:
            return
        self._cfg.set(dotted, int(round(scale.get_value())))
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
        """Sesuaikan tinggi jendela dengan isi tab yang aktif."""
        try:
            root = self.get_child()
            surface = root.get_first_child()
            header = surface.get_first_child()
            body = header.get_next_sibling()
            footer = body.get_next_sibling()
            page = self._stack.get_visible_child()
            content = page.get_child() if page is not None else None
            page_height = (
                content.get_preferred_size()[1].height
                if content is not None
                else 0
            )
            height = (
                header.get_preferred_size()[1].height
                + page_height
                + footer.get_preferred_size()[1].height
                + 2 * ui.SURFACE_MARGIN
                + 6
            )
            # Lebar tidak diubah: kalau memakai get_width(), jendela bisa
            # menyusut ke lebar alokasi sebelum di-map dan mengalahkan
            # ukuran default. Setelah tampil, lebar aktual dipakai supaya
            # ukuran hasil geser tangan pengguna tidak dikembalikan.
            width = (
                self.get_width() if self.get_width() > 0
                else self.get_default_size().width
            )
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

        self.set_default_size(max(600, width), max(420, height))
        return GLib.SOURCE_REMOVE

    def show_window(self) -> None:
        self._cfg.reload()
        self._load_values()
        self._update_status()
        self._select_page(self._current_page)
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
