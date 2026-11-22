"""Lokasi berkas dan konfigurasi PowerTools.

Semua path bisa dioverride lewat environment variable supaya mudah
diuji tanpa menyentuh data milik user:

    POWERTOOLS_CONFIG_DIR, POWERTOOLS_DATA_DIR
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any

APP_ID = "io.github.powertools.App"
APP_NAME = "PowerTools"
VERSION = "0.1.0"

DEFAULTS: dict[str, Any] = {
    # Proses tetap hidup di background supaya popup muncul instan.
    # Set false kalau lebih mementingkan RAM kosong saat idle.
    "resident": True,
    # Saklar modul. Yang dimatikan benar-benar berhenti bekerja: ekstensi
    # melepas hotkey/pemantau clipboard/media, dan proses Python tidak
    # menyiapkan jendela, poller, atau memuat daftar aplikasi.
    "modules": {
        "clipboard": True,
        "launcher": True,
        "color": True,
        "media": True,
        "lyrics": True,
        # Opt-in: butuh Application ID Discord dan aplikasi Discord desktop.
        "discord": False,
    },
    "clipboard": {
        "max_items": 300,
        # Item yang lebih tua dari ini dibuang otomatis saat riwayat dibaca.
        # 0 = tidak pernah kedaluwarsa. Item yang di-pin selalu dipertahankan.
        "max_age_minutes": 0,
        # Klik/Enter pada item langsung menempelkannya ke aplikasi sebelumnya.
        # Set false kalau hanya ingin menyalin ke clipboard seperti biasa.
        "auto_paste": True,
        # Item yang lebih besar dari ini dilewati (mis. dump file raksasa).
        "max_chars": 100_000,
        # Simpan juga hasil copy berupa gambar (PNG/JPEG/WebP/BMP).
        "images": True,
        # Gambar yang lebih besar dari ini dilewati total.
        "max_image_mb": 20,
        # Batas jumlah gambar di riwayat (teks punya kuota sendiri).
        "max_image_items": 60,
        # Hanya dipakai kalau jembatan GNOME Shell tidak tersedia (X11 dll).
        "poll_fallback": True,
        "poll_ms": 800,
        # Batas baris yang dirender sekaligus di popup.
        "max_shown": 120,
    },
    "launcher": {
        "max_results": 9,
        "web_search": "https://www.google.com/search?q={query}",
    },
    "color": {
        # hex | rgb | hsl | cmyk
        "format": "hex",
    },
    "lyrics": {
        # Jendela lirik mengambang. Tanpa auto_show, buka lewat hotkey
        # Super+Shift+L, aksi launcher, atau `powertools lyrics`.
        "auto_show": False,
        # Sematkan di atas semua jendela (butuh ekstensi GNOME).
        "pinned": False,
        # Tampilkan versi huruf Latin untuk lagu Korea/Jepang. Tombolnya
        # hanya muncul kalau versi itu benar-benar tersedia.
        "romanize": False,
        # Geser seluruh timestamp (milidetik). Positif = lirik lebih cepat.
        "offset_ms": 0,
        # Ukuran teks lirik (pt).
        "font_size": 15,
        # Kepekatan latar kartu lirik dalam persen (30–100). Teks tetap
        # pekat; hanya latarnya yang dibuat tembus pandang supaya lirik
        # masih terbaca di atas jendela lain.
        "opacity": 94,
        # Sembunyikan jendela lirik dari taskbar/Alt-Tab supaya taskbar
        # tetap bersih. Ikon PowerTools di panel tetap bisa membukanya.
        # Butuh ekstensi GNOME; tanpa ekstensi jendela tampil seperti biasa.
        "hide_from_taskbar": True,
    },
    "discord": {
        # Application ID lama (opsional). GUI sekarang tidak lagi meminta ID
        # diketik: semua ID berasal dari preset di sources.py. Nilai ini tetap
        # dihormati sebagai cadangan untuk config lama dan bisa dihapus lewat
        # Pengaturan → Discord Listening → baris "Application ID lama".
        "app_id": "",
        # Pemetaan sumber → Application ID. GUI hanya menawarkan Application ID
        # preset (lihat `sources.APP_PRESETS`), jadi isinya selalu salah satu ID
        # paten — bukan teks bebas. Kunci = nama sumber (lihat `powertools
        # doctor`), mis. {"spotify": "1573948090166288504"}. Kunci `default`
        # dipakai untuk sumber yang tidak punya pemetaan sendiri.
        "app_ids": {},
        # Pakai preset bawaan PowerTools untuk sumber yang tidak dipetakan di
        # atas, supaya judul "Listening to ..." sudah menyebut sumber tanpa
        # perlu membuat aplikasi Discord sendiri. Diatur lewat dropdown baris
        # "Sumber lain" di Pengaturan. Matikan kalau ingin tidak mengirim
        # status untuk sumber yang tidak dipetakan.
        "use_builtin_ids": True,
        # Nama asset Rich Presence yang sudah diunggah di portal, dipakai
        # kalau cover hanya tersedia sebagai file lokal (file://) sehingga
        # tidak bisa diambil Discord. Kosong = tanpa gambar.
        "fallback_asset": "",
        # Tetap tampilkan status saat lagu dijeda (tanpa progress bar).
        "show_paused": False,
        # Kirim cover album http(s) sebagai large_image. Cover file:// dan
        # data: dilewati karena Discord tidak bisa mengambilnya.
        "show_album_art": True,
    },
}


def _xdg(env_var: str, fallback: str) -> Path:
    raw = os.environ.get(env_var)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / fallback


def _override(env_var: str, default: Path) -> Path:
    raw = os.environ.get(env_var)
    return Path(raw).expanduser() if raw else default


CONFIG_DIR = _override("POWERTOOLS_CONFIG_DIR", _xdg("XDG_CONFIG_HOME", ".config") / "powertools")
DATA_DIR = _override("POWERTOOLS_DATA_DIR", _xdg("XDG_DATA_HOME", ".local/share") / "powertools")
CACHE_DIR = _override("POWERTOOLS_CACHE_DIR", _xdg("XDG_CACHE_HOME", ".cache") / "powertools")

CONFIG_PATH = CONFIG_DIR / "config.json"
SPOOL_PATH = DATA_DIR / "clipboard.jsonl"
STATE_PATH = DATA_DIR / "clipboard-state.json"
USAGE_PATH = DATA_DIR / "launcher-usage.json"
COLORS_PATH = DATA_DIR / "colors.json"
IMAGES_DIR = DATA_DIR / "images"
THUMBS_DIR = DATA_DIR / "thumbs"
LYRICS_CACHE_DIR = CACHE_DIR / "lyrics"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    """Tulis file lewat file sementara + rename supaya tidak korup."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Config:
    """Konfigurasi JSON dengan akses bertitik: `cfg.get('clipboard.max_items')`."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else CONFIG_PATH
        self.data = _deep_merge(copy.deepcopy(DEFAULTS), _read_json(self.path))

    def reload(self) -> None:
        """Baca ulang config.json (dipakai setelah pengaturan diubah)."""
        self.data = _deep_merge(copy.deepcopy(DEFAULTS), _read_json(self.path))

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise ValueError(f"bukan objek: {part}")
        node[parts[-1]] = value

    def save(self) -> None:
        atomic_write_text(self.path, json.dumps(self.data, indent=2, ensure_ascii=False) + "\n")

    def ensure_exists(self) -> None:
        if not self.path.exists():
            self.save()
            return
        # Berkas lama (sebelum ada blok `modules`) dilengkapi supaya bisa
        # diubah lewat GUI maupun diedit tangan.
        if "modules" not in _read_json(self.path):
            self.save()

    # --------------------------------------------------------------- modul
    def module_enabled(self, name: str) -> bool:
        """True kalau modul aktif.

        Key yang hilang memakai default dari DEFAULTS, bukan selalu True:
        modul baru seperti `discord` tetap opt-in untuk config lama. Nilai
        yang rusak (bukan bool) dianggap aktif supaya modul lama tidak
        tiba-tiba mati karena config yang salah tulis.
        """
        default = DEFAULTS["modules"].get(name, True)
        value = self.get(f"modules.{name}", default)
        return bool(value) if isinstance(value, bool) else True

    def any_module_enabled(self) -> bool:
        return any(self.module_enabled(name) for name in MODULES)


# Urutan tampil di pengaturan.
MODULES = ("clipboard", "launcher", "color", "media", "lyrics", "discord")
MODULE_LABELS = {
    "clipboard": "Riwayat Clipboard",
    "launcher": "Launcher",
    "color": "Color Picker",
    "media": "Dynamic Island Media",
    "lyrics": "Floating Lyrics",
    "discord": "Discord Listening",
}


def parse_config_value(raw: str) -> Any:
    """Parse nilai dari CLI: true/false/angka/JSON/string."""
    low = raw.strip().lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        return json.loads(raw)
    except ValueError:
        return raw