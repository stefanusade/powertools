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

# Application ID bawaan supaya modul Discord langsung berguna tanpa pengguna
# membuat aplikasi sendiri. Discord mengunci judul "Listening to ..." pada nama
# aplikasi, jadi satu nama tambahan berarti satu aplikasi tambahan.
#
# Aplikasi ini milik pemilik PowerTools, bukan pengguna. Pengguna tetap bisa
# menimpanya lewat ``discord.app_ids.<sumber>`` atau mematikan seluruh tabel
# ini dengan ``discord.use_builtin_ids: false``.
BUILTIN_APP_IDS = {
    # Nama aplikasi: "PowerTools" — dipakai untuk sumber yang tidak punya
    # aplikasi khusus.
    "default": "1573948090166288504",
    # Nama aplikasi: "YouTube Music - PowerTools".
    "youtube-music": "1573959687760646274",
}


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
