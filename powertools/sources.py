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

from .discord import (
    ACTIVITY_TYPE_LISTENING,
    ACTIVITY_TYPE_WATCHING,
    valid_app_id,
)

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

# Petunjuk dari ``xesam:url``. Hanya sebagian pemutar yang mengirim URL; kalau
# ada, ini lebih tepat daripada menebak dari judul.
URL_HINTS = (
    ("music.youtube.com", "youtube-music"),
    ("youtu.be", "youtube"),
    ("youtube.com", "youtube"),
    ("open.spotify.com", "spotify"),
    ("spotify.com", "spotify"),
    ("netflix.com", "netflix"),
    ("soundcloud.com", "soundcloud"),
    ("bandcamp.com", "bandcamp"),
    ("deezer.com", "deezer"),
    ("tidal.com", "tidal"),
    ("twitch.tv", "twitch"),
)

# Preset Application ID bawaan. Discord mengunci kata kerja ("Listening to"
# atau "Watching") beserta nama aplikasi, jadi satu platform butuh satu
# aplikasi Discord. Semua ID di sini paten dan disediakan PowerTools: pengguna
# tidak mengetik ID, ia hanya memilih sumber mana yang memakai preset ini lewat
# dropdown di Pengaturan.
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
    {
        # Nama aplikasi: "Spotify - PowerTools".
        "key": "spotify",
        "label": "Spotify",
        "app_id": "1574690633262567494",
    },
    {
        # Nama aplikasi: "Netflix - PowerTools".
        "key": "netflix",
        "label": "Netflix",
        "app_id": "1574693531161731112",
    },
    {
        # Nama aplikasi: "YouTube - PowerTools".
        "key": "youtube",
        "label": "YouTube",
        "app_id": "1574695288088559706",
    },
)
PRESETS = {preset["key"]: preset for preset in APP_PRESETS}

# Sumber video memakai aktivitas "Watching", sumber musik "Listening to".
# Discord menulis kata kerjanya sendiri dari tipe activity; nama aplikasi
# preset tetap yang muncul setelahnya. Preset platform menentukan kata kerja
# lebih dulu (Brave yang dipetakan ke preset YouTube tetap "Watching"), lalu
# preset umum mengikuti jenis sumbernya.
WATCHING_SOURCES = frozenset({"youtube", "netflix"})
WATCHING_PRESETS = frozenset({"youtube", "netflix"})
LISTENING_PRESETS = frozenset({"spotify", "youtube-music"})


def activity_type_for(source: object = "", app_id: object = "") -> int:
    """Tipe activity Discord untuk sumber dan Application ID yang dipakai.

    Preset platform menentukan kata kerjanya sendiri: YouTube dan Netflix
    ``Watching``, Spotify dan YouTube Music ``Listening to``. Preset umum
    (``default``) serta Application ID milik pengguna mengikuti jenis
    sumbernya, sehingga sumber video tetap ``Watching`` walau memakai preset
    umum.
    """
    preset = preset_for_app_id(app_id)
    if preset is not None:
        if preset["key"] in WATCHING_PRESETS:
            return ACTIVITY_TYPE_WATCHING
        if preset["key"] in LISTENING_PRESETS:
            return ACTIVITY_TYPE_LISTENING

    key = str(source or "").strip().lower()
    if key in WATCHING_SOURCES:
        return ACTIVITY_TYPE_WATCHING
    return ACTIVITY_TYPE_LISTENING

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


def slug_source(text: object) -> str:
    """Kunci sumber dari nama bebas: ``YouTube Music`` → ``youtube-music``."""
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


def _hinted_source(text: object) -> str:
    lowered = str(text or "").lower()
    if not lowered:
        return ""
    for needle, mapped in SITE_HINTS:
        if needle in lowered:
            return mapped
    return ""


def source_key(
    owner: str,
    title: str = "",
    album: str = "",
    *,
    app_name: str = "",
    url: str = "",
) -> str:
    """Kunci sumber yang stabil untuk pemilihan Application ID Discord.

    Contoh: ``org.mpris.MediaPlayer2.brave.instance282140`` → ``brave``.

    Untuk browser, kunci bisa lebih spesifik, berurutan:

    1. ``app_name`` — PWA yang dipasang (mis. YouTube Music) punya proses dan
       pemutar MPRIS sendiri; namanya diambil dari berkas .desktop.
    2. ``url`` — kalau pemutar mengirim ``xesam:url``.
    3. judul/album — sebagian situs menyertakan namanya di metadata.
    4. nama browser itu sendiri.
    """
    name = str(owner or "").strip()
    prefix = "org.mpris.MediaPlayer2."
    if name.startswith(prefix):
        name = name[len(prefix):]
    # Buang suffix instance: "brave.instance282140" → "brave".
    name = name.split(".")[0]
    key = slug_source(name)

    if key in BROWSER_SOURCES:
        hinted = _hinted_source(app_name)
        if hinted:
            return hinted
        lowered_url = str(url or "").lower()
        for needle, mapped in URL_HINTS:
            if needle in lowered_url:
                return mapped
        hinted = _hinted_source(f"{title} {album}")
        if hinted:
            return hinted
        # PWA yang bukan layanan yang dikenal tetap dapat kunci sendiri, supaya
        # tidak bercampur dengan tab browser biasa.
        slug = slug_source(app_name)
        if slug:
            return slug
    return key
