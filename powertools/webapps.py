"""Deteksi aplikasi web (PWA) dari pemutar MPRIS browser.

Browser berbasis Chromium memasang PWA sebagai proses terpisah dengan
``--app-id=<id>``. Proses itu punya pemutar MPRIS sendiri — nama busnya tetap
``…brave.instance<PID>`` dan properti ``Identity`` tetap nama browser — jadi
tanpa membaca proses, YouTube Music yang dipasang sebagai aplikasi web terlihat
sama dengan tab Brave biasa.

Modul ini membaca ``/proc/<PID>/cmdline`` untuk mengambil ``--app-id``, lalu
mencari berkas ``.desktop`` yang dibuat browser saat PWA dipasang untuk
mendapatkan nama aplikasinya. Semua lokal, tanpa D-Bus dan tanpa jaringan.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

#: Akhiran nama bus MPRIS: ``…brave.instance10804`` → PID 10804.
_INSTANCE = re.compile(r"instance(\d+)$")

#: Argumen PWA Chromium/Brave/Chrome/Edge/Vivaldi/Opera.
_APP_ID = re.compile(r"--app-id=([A-Za-z0-9]+)")

#: Firefox PWA (proyek firefoxpwa) menjalankan Firefox dengan kelas jendela
#: ``FFPWA-<ulid>``; berkas .desktop-nya bernama sama. Argumennya bisa
#: ``--class=``, ``-class``, atau ``--name=``, jadi penandanya dicari langsung.
_FFPWA = re.compile(r"(FFPWA-[A-Za-z0-9]+)")

#: Direktori berkas .desktop, dari yang paling spesifik ke sistem.
APP_DIRS: tuple[str, ...] = (
    "~/.local/share/applications",
    "/usr/local/share/applications",
    "/usr/share/applications",
    "~/.local/share/flatpak/exports/share/applications",
    "/var/lib/flatpak/exports/share/applications",
    "/var/lib/snapd/desktop/applications",
)

#: Hasil per ``--app-id``; nama aplikasi tidak berubah selama PWA yang sama.
#: Kunci per app-id (bukan per PID) supaya PID yang dipakai ulang tidak
#: membawa nama lama.
_CACHE: dict[str, str] = {}


def pid_from_owner(owner: object) -> int:
    """PID proses pemutar dari nama bus MPRIS, atau 0 kalau tidak ada."""
    match = _INSTANCE.search(str(owner or "").strip())
    if match is None:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


def read_cmdline(pid: int, proc: str | Path = "/proc") -> str:
    """Isi ``cmdline`` proses sebagai satu baris, atau ``""``.

    Chromium menulis seluruh argv sebagai satu argumen berisi spasi, jadi
    pemisah NUL diganti spasi dan pencocokan dilakukan pada teks utuh.
    """
    if pid <= 0:
        return ""
    try:
        raw = (Path(proc) / str(pid) / "cmdline").read_bytes()
    except OSError:
        return ""
    return " ".join(
        part.decode("utf-8", "replace") for part in raw.split(b"\0") if part
    )


def app_id_from_cmdline(cmdline: object) -> str:
    """Penanda PWA dari baris perintah, atau ``""`` kalau bukan PWA.

    Chromium memakai ``--app-id=<id>``; Firefox PWA memakai
    ``--class=FFPWA-<ulid>``. Keduanya dipakai untuk mencari berkas .desktop
    yang dibuat saat aplikasi web dipasang.
    """
    text = str(cmdline or "")
    match = _APP_ID.search(text) or _FFPWA.search(text)
    return match.group(1) if match else ""


def _desktop_name(text: str) -> str:
    """Nilai ``Name=`` pertama dari isi berkas .desktop."""
    for line in text.splitlines():
        if line.startswith("Name="):
            return line.split("=", 1)[1].strip()
    return ""


def _desktop_exec(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("Exec="):
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

    Browser menamai berkasnya ``<browser>-<app-id>-<profil>.desktop``, tetapi
    sebagian versi hanya mencantumkan ``--app-id`` di ``Exec``. Keduanya
    dicocokkan supaya tidak bergantung pada satu penamaan.
    """
    needle = str(app_id or "").strip()
    if not needle:
        return ""
    for directory in _app_dirs(dirs):
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
            name = _desktop_name(text)
            if name:
                return name
    # Berkas bisa dinamai lain; periksa Exec sebagai upaya terakhir.
    for directory in _app_dirs(dirs):
        try:
            entries = sorted(directory.glob("*.desktop"))
        except OSError:
            continue
        for path in entries:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if needle in _desktop_exec(text):
                name = _desktop_name(text)
                if name:
                    return name
    return ""


def app_name_for_owner(
    owner: object,
    *,
    proc: str | Path = "/proc",
    dirs: Iterable[str | Path] | None = None,
) -> str:
    """Nama aplikasi web yang memutar media, atau ``""`` untuk browser biasa."""
    key = str(owner or "").strip()
    if not key:
        return ""

    app_id = app_id_from_cmdline(read_cmdline(pid_from_owner(key), proc))
    if not app_id:
        return ""
    if app_id in _CACHE:
        return _CACHE[app_id]

    name = desktop_name_for_app_id(app_id, dirs)
    # Hanya hasil positif yang di-cache: berkas .desktop bisa baru muncul
    # setelah PWA selesai dipasang.
    if name:
        _CACHE[app_id] = name
    return name


def clear_cache() -> None:
    """Lupakan hasil sebelumnya (dipakai tes dan setelah daftar PWA berubah)."""
    _CACHE.clear()
