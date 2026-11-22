"""Penamaan sumber MPRIS dan pemilihan Application ID Discord.

Modul ini sengaja tidak menyentuh D-Bus/GTK supaya bisa diuji tanpa sesi
grafis. ``source_key`` mengubah nama bus MPRIS menjadi kunci pendek yang stabil
(``spotify``, ``vlc``, ``youtube-music``) yang dipakai di
``discord.app_ids``. ``resolve_app_id`` memilih Application ID mana yang
dipakai untuk sebuah sumber.
"""

from __future__ import annotations

import re
from typing import Mapping

from .discord import valid_app_id

# Pemutar berbasis browser melaporkan dirinya sebagai browsernya, bukan situs
# yang sedang diputar. Judul/album kadang masih membawa nama layanan web,
# jadi itu dipakai sebagai petunjuk tambahan.
BROWSER_SOURCES = frozenset(
    {
        "brave",
        "chromium",
        "chrome",
        "google-chrome",
        "firefox",
        "mozilla",
        "vivaldi",
        "opera",
        "edge",
        "epiphany",
        "falkon",
        "zen",
        "librewolf",
        "waterfox",
    }
)

SITE_HINTS = (
    ("youtube music", "youtube-music"),
    ("music.youtube", "youtube-music"),
    ("youtube", "youtube"),
    ("soundcloud", "soundcloud"),
    ("spotify", "spotify"),
    ("netflix", "netflix"),
    ("bandcamp", "bandcamp"),
    ("deezer", "deezer"),
    ("tidal", "tidal"),
    ("twitch", "twitch"),
)

# Preset Application ID bawaan. Discord mengunci judul "Listening to ..."
# pada nama aplikasi, jadi satu platform butuh satu aplikasi Discord. Semua ID
# di sini paten dan disediakan PowerTools: pengguna tidak mengetik ID, ia hanya
# memilih sumber mana yang memakai preset ini lewat dropdown di Pengaturan.
#
# Menambah platform (mis. Spotify) cukup menambah satu entri di sini; GUI dan
# service otomatis memakainya. Urutan entri = urutan di dropdown.
APP_PRESETS: tuple[dict[str, str], ...] = (
    {
        # Nama aplikasi: "PowerTools" — cadangan untuk sumber tanpa preset.
        "key": "default",
        "label": "PowerTools (umum)",
        "app_id": "1573948090166288504",
    },
    {
        # Nama aplikasi: "YouTube Music - PowerTools".
        "key": "youtube-music",
        "label": "YouTube Music",
        "app_id": "1573959687760646274",
    },
)
PRESETS = {preset["key"]: preset for preset in APP_PRESETS}

# Kompatibilitas untuk modul lama yang hanya butuh peta kunci → ID.
BUILTIN_APP_IDS = {key: preset["app_id"] for key, preset in PRESETS.items()}

# Label ramah untuk kunci sumber MPRIS. Sumber yang tidak terdaftar tetap bisa
# dipakai; namanya dibentuk dari kuncinya (mis. "my-player" → "My Player").
SOURCE_LABELS = {
    "spotify": "Spotify",
    "vlc": "VLC",
    "mpv": "mpv",
    "rhythmbox": "Rhythmbox",
    "celluloid": "Celluloid",
    "audacious": "Audacious",
    "elisa": "Elisa",
    "strawberry": "Strawberry",
    "clementine": "Clementine",
    "amarok": "Amarok",
    "cmus": "cmus",
    "mpd": "MPD",
    "youtube-music": "YouTube Music",
    "youtube": "YouTube",
    "soundcloud": "SoundCloud",
    "deezer": "Deezer",
    "tidal": "Tidal",
    "bandcamp": "Bandcamp",
    "netflix": "Netflix",
    "twitch": "Twitch",
    "brave": "Brave",
    "chromium": "Chromium",
    "chrome": "Google Chrome",
    "google-chrome": "Google Chrome",
    "firefox": "Firefox",
    "mozilla": "Firefox",
    "vivaldi": "Vivaldi",
    "opera": "Opera",
    "edge": "Microsoft Edge",
    "epiphany": "GNOME Web",
    "falkon": "Falkon",
    "zen": "Zen Browser",
    "librewolf": "LibreWolf",
    "waterfox": "Waterfox",
}
KNOWN_SOURCES = tuple(SOURCE_LABELS)


def source_label(source: str) -> str:
    """Nama yang enak dibaca untuk kunci sumber, mis. ``youtube-music``."""
    key = str(source or "").strip().lower()
    if key in SOURCE_LABELS:
        return SOURCE_LABELS[key]
    return key.replace("-", " ").replace("_", " ").title() or "Tidak dikenal"


def preset_for_app_id(app_id: object) -> dict[str, str] | None:
    """Preset bawaan yang memiliki Application ID ini, kalau ada."""
    value = str(app_id or "").strip()
    if not value:
        return None
    for preset in APP_PRESETS:
        if preset["app_id"] == value:
            return preset
    return None


def resolve_app_id(
    source: str = "",
    *,
    app_ids: Mapping[str, object] | None = None,
    app_id: object = "",
    use_builtin: bool = True,
) -> str:
    """Application ID yang dipakai untuk sebuah sumber lagu.

    Urutan, yang pertama menang — dari yang paling spesifik ke paling umum:

    1. ``app_ids[sumber]`` — pilihan eksplisit pengguna
    2. Application ID bawaan untuk sumber itu (kalau ``use_builtin``)
    3. ``app_ids["default"]`` — cadangan eksplisit pengguna
    4. ``app_id`` — ID utama pengguna
    5. Application ID bawaan ``default`` (kalau ``use_builtin``)

    Bawaan per sumber berada di atas ``app_id`` dengan sengaja: pengguna lama
    sudah punya ``app_id`` terisi, jadi tanpa urutan ini aplikasi khusus
    seperti YouTube Music tidak akan pernah terpakai. Mengembalikan ``""``
    kalau tidak ada yang bisa dipakai.
    """
    clean: dict[str, str] = {}
    if isinstance(app_ids, Mapping):
        for key, value in app_ids.items():
            if valid_app_id(value):
                clean[str(key).strip().lower()] = str(value).strip()

    key = str(source or "").strip().lower()
    if key and key in clean:
        return clean[key]

    if use_builtin and key and valid_app_id(BUILTIN_APP_IDS.get(key)):
        return str(BUILTIN_APP_IDS[key]).strip()

    if "default" in clean:
        return clean["default"]

    if valid_app_id(app_id):
        return str(app_id).strip()

    if use_builtin and valid_app_id(BUILTIN_APP_IDS.get("default")):
        return str(BUILTIN_APP_IDS["default"]).strip()
    return ""


def source_key(owner: str, title: str = "", album: str = "") -> str:
    """Kunci sumber yang stabil untuk pemilihan Application ID Discord.

    Contoh: ``org.mpris.MediaPlayer2.brave.instance282140`` → ``brave``.
    Untuk browser, judul/album dipakai kalau menyebut layanan tertentu
    (mis. ``… - YouTube Music`` → ``youtube-music``).
    """
    name = str(owner or "").strip()
    prefix = "org.mpris.MediaPlayer2."
    if name.startswith(prefix):
        name = name[len(prefix):]
    # Buang suffix instance: "brave.instance282140" → "brave".
    name = name.split(".")[0]
    key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

    if key in BROWSER_SOURCES:
        text = f"{title} {album}".lower()
        for needle, mapped in SITE_HINTS:
            if needle in text:
                return mapped
    return key
