"""Deteksi aplikasi web (PWA) dari pemutar MPRIS browser.

Browser berbasis Chromium dan Firefox mengekspos **satu** pemutar MPRIS per
proses browser, bukan per tab. Aplikasi web yang dipasang lewat browser (mis.
YouTube Music) bahkan memakai proses yang sama dengan tab biasa: argumen
``--app-id`` hanya ada pada proses yang *pertama* dijalankan, jadi baris
perintah tidak bisa dipakai untuk menebak.

Yang bisa dipakai adalah jendelanya. PWA punya identitas jendela sendiri, dan
bentuknya berbeda antar browser/lingkungan:

* ``crx_<app-id>`` — Chromium di X11,
* ``<browser>-<app-id>-<profil>`` — Brave/Chromium di Wayland,
* ``FFPWA-<ulid>`` — Firefox PWA.

Identitas itu hanya terlihat oleh shell, jadi modul ini bertanya ke ekstensi
GNOME lewat jembatan D-Bus, lalu memilih jendela sumber dengan urutan:

1. jendela yang judulnya memuat judul lagu MPRIS — itulah yang sedang memutar;
2. kalau tidak ada yang cocok, satu-satunya PWA di proses itu — judul jendela
   PWA sering hanya nama aplikasinya ("YouTube Music"), sehingga judul lagu
   tidak akan pernah cocok;
3. selain itu dianggap tab browser biasa.

Judul jendela yang cocok ikut dikembalikan, karena judul itu satu-satunya
petunjuk situs untuk tab biasa: MPRIS tidak mengirim nama situs, tetapi jendela
YouTube berjudul "… - YouTube". Nama PWA diambil dari berkas ``.desktop`` yang
dibuat browser saat aplikasi web dipasang.

Semua lokal: tanpa jaringan, tanpa membaca isi tab.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .dbusutil import bridge_windows

#: Akhiran nama bus MPRIS: ``…brave.instance10804`` → PID 10804.
_INSTANCE = re.compile(r"instance(\d+)$")

#: Bentuk identitas jendela PWA. Id ekstensi Chromium selalu 32 huruf a–p.
_CLASS_PATTERNS = (
    re.compile(r"^_?crx_([a-p]{32})$", re.IGNORECASE),
    # brave-cinhimbnkkaeohfgghhklpknlkffjgod-Default
    re.compile(r"^[A-Za-z0-9-]+?-([a-p]{32})(?:-[A-Za-z0-9 _.-]+)?$"),
    re.compile(r"^([a-p]{32})$"),
    # Firefox PWA (proyek firefoxpwa).
    re.compile(r"^(FFPWA-[A-Za-z0-9]+)$"),
)

#: Direktori berkas .desktop, dari yang paling spesifik ke sistem.
APP_DIRS: tuple[str, ...] = (
    "~/.local/share/applications",
    "/usr/local/share/applications",
    "/usr/share/applications",
    "~/.local/share/flatpak/exports/share/applications",
    "/var/lib/flatpak/exports/share/applications",
    "/var/lib/snapd/desktop/applications",
)

#: Panjang minimum judul untuk dicocokkan sebagai bagian dari judul jendela.
#: Judul sangat pendek ("Lagu") bisa muncul di jendela mana saja dan justru
#: menyesatkan.
MIN_TITLE = 8

#: Panjang minimum untuk dicocokkan sebagai awalan ("Judul - YouTube").
MIN_SHORT = 3

#: ``True``/``False`` setelah jembatan dihubungi; ``None`` sebelum itu. Ekstensi
#: versi lama tidak punya metode daftar jendela, jadi hasilnya diingat supaya
#: tidak memanggil D-Bus berulang kali hanya untuk gagal.
_BRIDGE_OK: bool | None = None


@dataclass(frozen=True)


class BrowserContext:
    """Apa yang bisa diketahui tentang jendela sumber sebuah pemutar MPRIS."""

    #: Id aplikasi web (``--app-id``) jendela sumber, atau "".
    app_id: str = ""
    #: Nama aplikasi web yang terpasang (mis. "YouTube Music"), atau "".
    app_name: str = ""
    #: Judul jendela yang dianggap sumber; petunjuk situs untuk tab biasa.
    window_title: str = ""
    #: True kalau jendela sumber adalah aplikasi web, bukan tab browser.
    pwa: bool = False

    def __bool__(self) -> bool:
        return bool(self.app_id or self.window_title)


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


def _desktop_entries(dirs: Iterable[str | Path] | None):
    """Pasangan ``(path, teks)`` untuk semua berkas .desktop yang bisa dibaca."""
    for directory in _app_dirs(dirs):
        try:
            paths = sorted(directory.glob("*.desktop"))
        except OSError:
            continue
        for path in paths:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            yield path, text


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

    entries = list(_desktop_entries(dirs))
    for path, text in entries:
        if needle in path.name:
            name = _desktop_field(text, "Name")
            if name:
                return name
    for _path, text in entries:
        if app_id_from_class(_desktop_field(text, "StartupWMClass")) == needle:
            name = _desktop_field(text, "Name")
            if name:
                return name
    for _path, text in entries:
        if needle in _desktop_field(text, "Exec"):
            name = _desktop_field(text, "Name")
            if name:
                return name
    return ""


def desktop_name_for_class(
    wm_class: object,
    dirs: Iterable[str | Path] | None = None,
) -> str:
    """Nama aplikasi dari berkas .desktop yang namanya sama dengan kelas jendela.

    Brave di Wayland memberi PWA kelas ``brave-<app-id>-Default``, dan berkas
    .desktop buatannya bernama persis sama. Ini jalur paling langsung.
    """
    needle = str(wm_class or "").strip()
    if not needle:
        return ""
    for path, text in _desktop_entries(dirs):
        if path.stem == needle:
            name = _desktop_field(text, "Name")
            if name:
                return name
    return ""


def app_id_from_class(wm_class: object) -> str:
    """Id PWA dari identitas jendela, atau ``""`` untuk jendela browser biasa."""
    text = str(wm_class or "").strip()
    if not text:
        return ""
    for pattern in _CLASS_PATTERNS:
        match = pattern.match(text)
        if match:
            return match.group(1)
    return ""


def _normalise(text: object) -> str:
    """Huruf kecil, spasi tunggal, tanpa elipsis di ujung.

    Elipsis di ujung judul jendela berarti judulnya dipotong, bukan bagian
    dari nama lagu.
    """
    value = " ".join(str(text or "").lower().split())
    for tail in ("…", "..."):
        if value.endswith(tail):
            value = value[: -len(tail)].rstrip()
    return value


def _title_matches(track_title: str, window_title: str) -> bool:
    """True kalau judul lagu dan judul jendela menunjuk media yang sama.

    Judul jendela menambahkan nama situs/browser ("… - YouTube Music"), dan
    Chromium memotong judul panjang di titik yang bisa berbeda antara MPRIS dan
    jendela. Karena itu dicocokkan berurutan: awalan judul (paling kuat),
    judul yang memuat yang lain, lalu kecocokan kata.
    """
    needle = _normalise(track_title)
    haystack = _normalise(window_title)
    if len(needle) < MIN_SHORT or not haystack:
        return False
    # "Judul - YouTube Music", "Judul | Spotify": awalan yang diikuti pemisah
    # adalah bukti paling kuat, dan tetap berlaku untuk judul pendek.
    for separator in (" - ", " – ", " — ", " | ", " · ", " • "):
        if haystack.startswith(needle + separator):
            return True
    if len(needle) < MIN_TITLE:
        return False
    if needle in haystack:
        return True
    if len(haystack) >= MIN_TITLE and haystack in needle:
        return True
    # Potongan yang sama, titik potong berbeda.
    words = [word for word in re.split(r"[\W_]+", needle) if len(word) >= 3]
    if len(words) < 4:
        return False
    others = set(re.findall(r"\w+", haystack))
    shared = sum(1 for word in words if word in others)
    return shared / len(words) >= 0.7


def pwa_id_from_windows(
    track_title: object,
    windows: Iterable[tuple[str, str]],
) -> str:
    """Id PWA yang sedang memutar lagu ini, atau ``""``.

    ``windows`` adalah daftar ``(wm_class, judul)`` milik proses browser.
    """
    return context_from_windows(track_title, windows).app_id


def context_from_windows(
    track_title: object,
    windows: Iterable[tuple[str, str]],
    *,
    dirs: Iterable[str | Path] | None = None,
) -> BrowserContext:
    """Tentukan jendela sumber dari daftar ``(wm_class, judul)``."""
    entries = [(str(wm_class or ""), str(title or ""))
               for wm_class, title in (windows or ())]
    if not entries:
        return BrowserContext()

    title = str(track_title or "")
    matches = [entry for entry in entries if _title_matches(title, entry[1])]
    if matches:
        # Kalau dua jendela cocok (lagu yang sama di PWA dan di tab), PWA
        # dianggap sumbernya: pemutar MPRIS memakai sesi media terakhir, dan
        # jendela PWA biasanya hanya menampilkan lagu itu.
        window = next(
            (entry for entry in matches if app_id_from_class(entry[0])),
            matches[0],
        )
    else:
        pwa_windows = [entry for entry in entries if app_id_from_class(entry[0])]
        ids = {app_id_from_class(entry[0]) for entry in pwa_windows}
        if len(ids) != 1:
            # Banyak PWA berbeda, atau tidak ada PWA: tidak bisa dipastikan.
            return BrowserContext()
        window = pwa_windows[0]

    wm_class, window_title = window
    app_id = app_id_from_class(wm_class)
    if not app_id:
        return BrowserContext(window_title=window_title)
    name = (
        desktop_name_for_app_id(app_id, dirs)
        or desktop_name_for_class(wm_class, dirs)
    )
    return BrowserContext(
        app_id=app_id, app_name=name, window_title=window_title, pwa=True)


def windows_for_owner(
    owner: object, timeout_ms: int = 400
) -> list[tuple[str, str]]:
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


def context_for_owner(
    owner: object,
    track_title: object = "",
    *,
    dirs: Iterable[str | Path] | None = None,
    windows: Iterable[tuple[str, str]] | None = None,
) -> BrowserContext:
    """Jendela sumber pemutar ini: nama PWA, judul jendela, atau kosong."""
    if windows is None:
        windows = windows_for_owner(owner)
    return context_from_windows(track_title, windows, dirs=dirs)


def app_name_for_owner(
    owner: object,
    track_title: object = "",
    *,
    dirs: Iterable[str | Path] | None = None,
    windows: Iterable[tuple[str, str]] | None = None,
) -> str:
    """Nama aplikasi web yang memutar media, atau ``""`` untuk tab biasa."""
    return context_for_owner(
        owner, track_title, dirs=dirs, windows=windows).app_name


def clear_cache() -> None:
    """Lupakan status jembatan (dipakai tes dan tombol muat ulang)."""
    global _BRIDGE_OK
    _BRIDGE_OK = None
