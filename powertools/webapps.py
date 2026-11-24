"""Deteksi aplikasi web (PWA) dari pemutar MPRIS browser.

Browser berbasis Chromium dan Firefox mengekspos **satu** pemutar MPRIS per
proses browser, bukan per tab atau per jendela. Aplikasi web yang dipasang
lewat browser (mis. YouTube Music) bahkan bisa memakai proses yang sama dengan
tab biasa: argumen ``--app-id`` hanya ada pada proses yang *pertama*
dijalankan, jadi baris perintah tidak bisa dipakai untuk menebak.

Yang bisa dipercaya adalah jendelanya. PWA punya identitas jendela sendiri
(``_crx_<app-id>`` untuk Chromium, ``FFPWA-<ulid>`` untuk Firefox PWA), dan
identitas itu hanya terlihat oleh shell. Modul ini karena itu bertanya ke
ekstensi GNOME lewat jembatan D-Bus, lalu:

1. mencari jendela yang judulnya memuat judul lagu MPRIS — itulah jendela yang
   sedang memutar. Kalau jendela itu PWA, sumbernya PWA; kalau jendela browser
   biasa, berarti tab biasa;
2. kalau tidak ada judul yang cocok, memakai PWA hanya bila *seluruh* jendela
   proses itu adalah PWA yang sama — tidak ada tab yang bisa jadi sumbernya;
3. selain itu menganggapnya tab browser biasa.

Nama aplikasi diambil dari berkas ``.desktop`` yang dibuat browser saat PWA
dipasang. Semua lokal: tanpa jaringan, tanpa membaca isi tab.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .dbusutil import bridge_windows

#: Akhiran nama bus MPRIS: ``…brave.instance10804`` → PID 10804.
_INSTANCE = re.compile(r"instance(\d+)$")

#: Identitas jendela PWA Chromium/Brave/Chrome/Edge/Vivaldi/Opera. Chromium
#: memakai ``_crx_<app-id>``; sebagian versi/lingkungan membuang garis bawah.
_CHROMIUM_CLASS = re.compile(r"^_?crx_([A-Za-z0-9._-]+)$", re.IGNORECASE)

#: Identitas jendela Firefox PWA (proyek firefoxpwa).
_FIREFOX_CLASS = re.compile(r"^(FFPWA-[A-Za-z0-9]+)$")

#: Id ekstensi Chromium tanpa awalan (32 huruf a–p).
_BARE_ID = re.compile(r"^([a-p]{32})$")

#: Direktori berkas .desktop, dari yang paling spesifik ke sistem.
APP_DIRS: tuple[str, ...] = (
    "~/.local/share/applications",
    "/usr/local/share/applications",
    "/usr/share/applications",
    "~/.local/share/flatpak/exports/share/applications",
    "/var/lib/flatpak/exports/share/applications",
    "/var/lib/snapd/desktop/applications",
)

#: Panjang minimum judul lagu untuk dicocokkan. Judul sangat pendek ("Lagu")
#: bisa muncul di jendela mana saja dan justru menyesatkan.
MIN_TITLE = 8

#: ``True``/``False`` setelah jembatan dihubungi; ``None`` sebelum itu. Ekstensi
#: versi lama tidak punya metode daftar jendela, jadi hasilnya diingat supaya
#: tidak memanggil D-Bus berulang kali hanya untuk gagal.
_BRIDGE_OK: bool | None = None


def pid_from_owner(owner: object) -> int:
    """PID proses pemutar dari nama bus MPRIS, atau 0 kalau tidak ada."""
    match = _INSTANCE.search(str(owner or "").strip())
    if match is None:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


def _desktop_field(text: str, field: str) -> str:
    """Nilai ``field=`` pertama dari isi berkas .desktop."""
    prefix = f"{field}="
    for line in text.splitlines():
        if line.startswith(prefix):
            return line.split("=", 1)[1].strip()
    return ""


def _app_dirs(dirs: Iterable[str | Path] | None) -> list[Path]:
    source = APP_DIRS if dirs is None else dirs
    return [Path(item).expanduser() for item in source]


def desktop_name_for_app_id(
    app_id: object,
    dirs: Iterable[str | Path] | None = None,
) -> str:
    """Nama aplikasi dari berkas .desktop milik PWA, atau ``""``.

    Tiga jejak dicoba berurutan, karena penamaan berkas berbeda antar browser
    dan versi:

    1. nama berkas — ``brave-<app-id>-Default.desktop``,
    2. ``StartupWMClass`` — Chrome/Brave menulis ``crx_<app-id>`` di sini,
    3. ``Exec`` — sebagian hanya mencantumkan ``--app-id=<app-id>``.
    """
    needle = str(app_id or "").strip()
    if not needle:
        return ""

    directories = _app_dirs(dirs)
    for directory in directories:
        try:
            entries = sorted(directory.glob("*.desktop"))
        except OSError:
            continue
        for path in entries:
            if needle not in path.name:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            name = _desktop_field(text, "Name")
            if name:
                return name

    for directory in directories:
        try:
            entries = sorted(directory.glob("*.desktop"))
        except OSError:
            continue
        for path in entries:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            wm_class = _desktop_field(text, "StartupWMClass")
            if wm_class and app_id_from_class(wm_class) == needle:
                name = _desktop_field(text, "Name")
                if name:
                    return name
    for directory in directories:
        try:
            entries = sorted(directory.glob("*.desktop"))
        except OSError:
            continue
        for path in entries:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if needle in _desktop_field(text, "Exec"):
                name = _desktop_field(text, "Name")
                if name:
                    return name
    return ""


def app_id_from_class(wm_class: object) -> str:
    """Id PWA dari identitas jendela, atau ``""`` untuk jendela browser biasa."""
    text = str(wm_class or "").strip()
    if not text:
        return ""
    match = (
        _CHROMIUM_CLASS.match(text)
        or _FIREFOX_CLASS.match(text)
        or _BARE_ID.match(text)
    )
    return match.group(1) if match else ""


def _normalise(text: object) -> str:
    return " ".join(str(text or "").lower().split())


def _title_matches(track_title: str, window_title: str) -> bool:
    """True kalau judul lagu muncul di judul jendela.

    Judul jendela biasanya menambahkan nama browser ("… - Brave") atau nama
    aplikasi ("… - YouTube Music"), jadi pencocokan dilakukan pada isi judul.
    """
    needle = _normalise(track_title)
    if len(needle) < MIN_TITLE:
        return False
    haystack = _normalise(window_title)
    return bool(haystack) and needle in haystack


def pwa_id_from_windows(
    track_title: object,
    windows: Iterable[tuple[str, str]],
) -> str:
    """Id PWA yang sedang memutar lagu ini, atau ``""``.

    ``windows`` adalah daftar ``(wm_class, judul)`` milik proses browser.
    """
    entries = list(windows or ())
    if not entries:
        return ""

    title = str(track_title or "")
    for wm_class, window_title in entries:
        if _title_matches(title, window_title):
            # Jendela pertama yang judulnya cocok adalah sumbernya. Kalau
            # jendela itu browser biasa, berarti lagunya dari tab — bukan PWA.
            return app_id_from_class(wm_class)

    # Tidak ada judul yang cocok: aman hanya kalau seluruh jendela proses itu
    # adalah PWA yang sama, karena tidak ada tab yang bisa jadi sumbernya.
    ids = {app_id_from_class(wm_class) for wm_class, _title in entries}
    if len(ids) == 1:
        only = next(iter(ids))
        if only:
            return only
    return ""


def windows_for_owner(owner: object, timeout_ms: int = 400) -> list[tuple[str, str]]:
    """Jendela milik proses pemutar, atau list kosong kalau tak bisa dibaca."""
    global _BRIDGE_OK

    pid = pid_from_owner(owner)
    if pid <= 0 or _BRIDGE_OK is False:
        return []
    windows = bridge_windows(pid, timeout_ms)
    if windows is None:
        _BRIDGE_OK = False
        return []
    _BRIDGE_OK = True
    return windows


def app_name_for_owner(
    owner: object,
    track_title: object = "",
    *,
    dirs: Iterable[str | Path] | None = None,
    windows: Iterable[tuple[str, str]] | None = None,
) -> str:
    """Nama aplikasi web yang memutar media, atau ``""`` untuk tab biasa."""
    if windows is None:
        windows = windows_for_owner(owner)
    app_id = pwa_id_from_windows(track_title, windows)
    if not app_id:
        return ""
    return desktop_name_for_app_id(app_id, dirs)


def clear_cache() -> None:
    """Lupakan hasil sebelumnya (dipakai tes dan tombol muat ulang)."""
    global _BRIDGE_OK
    _BRIDGE_OK = None
