"""Lirik lagu dari LRCLIB (lrclib.net) dan parsing LRC.

LRCLIB gratis, tanpa API key, dan mengembalikan lirik biasa maupun lirik
sinkron (LRC dengan timestamp). Modul ini sengaja hanya memakai pustaka
standar supaya bisa diuji tanpa GTK/jaringan.

Untuk lagu Korea/Jepang, modul ini juga menyiapkan versi huruf Latin
("romaji") lewat dua jalur: rekaman unggahan LRCLIB yang memang sudah Latin,
lalu transliterasi Hangul lokal kalau tidak ada. Kanji Jepang tidak
ditransliterasi — kalau tidak ada unggahan romaji, lirik dibiarkan apa adanya.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import config

API_BASE = "https://lrclib.net"
USER_AGENT = (
    f"PowerTools/{config.VERSION} "
    "(GNOME floating lyrics; https://github.com/powertools)"
)
CACHE_TTL = 30 * 86400
NEGATIVE_TTL = 6 * 3600
MAX_RESPONSE_BYTES = 4 * 1024 * 1024

# LRCLIB meminta klien menghormati Retry-After saat kena 429. Selama masa
# tunggu, semua permintaan langsung dilewati supaya tidak menambah beban.
_blocked_until = 0.0

_TIMESTAMP_RE = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_OFFSET_RE = re.compile(r"\[offset\s*:\s*([+-]?\d+)\]", re.IGNORECASE)
_META_RE = re.compile(
    r"\[(?:ar|ti|al|by|re|ve|au|length|offset)\s*:[^\]]*\]",
    re.IGNORECASE,
)
_TITLE_NOISE_RE = re.compile(
    r"\s*[\(\[][^\)\]]*\b(?:feat|ft\.?|official|lyrics?|audio|video|"
    r"remaster(?:ed)?|live|version|edit)\b[^\)\]]*[\)\]]",
    re.IGNORECASE,
)
_TITLE_TAIL_RE = re.compile(
    r"\s+-\s+(?:remaster(?:ed)?|live|radio edit|album version|"
    r"official (?:video|audio)|lyrics?)\s*$",
    re.IGNORECASE,
)

# Deteksi skrip untuk memutuskan apakah lirik perlu versi Latin.
_HANGUL_RE = re.compile(r"[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]")
_CJK_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]"
)

# Romaji punya proporsi vokal jauh lebih tinggi daripada bahasa Inggris
# (terukur ~0.49-0.53 vs ~0.38-0.40), jadi ini dipakai untuk menolak rekaman
# terjemahan bahasa Inggris yang bentuknya mirip romaji.
_ROMAJI_MIN_VOWELS = 0.44
_ROMAJI_MIN_LETTERS = 40


class _NotFound(Exception):
    """LRCLIB menjawab 404 (lagu belum ada di database)."""


class _NetworkError(Exception):
    """Kegagalan jaringan; jangan disimpan sebagai hasil negatif."""


@dataclass(slots=True)
class LyricLine:
    time: float
    text: str


@dataclass(slots=True)
class Lyrics:
    track: str = ""
    artist: str = ""
    album: str = ""
    duration: float = 0.0
    synced: list[LyricLine] = field(default_factory=list)
    plain: list[str] = field(default_factory=list)
    instrumental: bool = False
    record_id: int = 0
    source: str = "lrclib.net"
    # Versi huruf Latin. Timestamp-nya milik rekaman romaji sendiri kalau
    # berasal dari LRCLIB, atau salinan timestamp lirik asli kalau hasil
    # transliterasi lokal.
    romaji: list[LyricLine] = field(default_factory=list)
    romaji_plain: list[str] = field(default_factory=list)
    romaji_source: str = ""

    @property
    def has_synced(self) -> bool:
        return bool(self.synced)

    @property
    def has_any(self) -> bool:
        return self.instrumental or bool(self.synced) or bool(self.plain)

    @property
    def has_romaji(self) -> bool:
        return bool(self.romaji) or bool(self.romaji_plain)


def normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_title(value: str) -> str:
    """Buang akhiran seperti "(Official Video)" atau " - Remastered"."""
    text = normalize_text(value)
    text = _TITLE_NOISE_RE.sub("", text)
    text = _TITLE_TAIL_RE.sub("", text)
    return normalize_text(text) or normalize_text(value)


def cache_key(title: str, artist: str, album: str = "", duration: float = 0) -> str:
    raw = "\x1f".join(
        (
            normalize_text(title).casefold(),
            normalize_text(artist).casefold(),
            normalize_text(album).casefold(),
            str(int(round(float(duration or 0)))),
        )
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _fraction_seconds(fraction: str | None) -> float:
    if not fraction:
        return 0.0
    if len(fraction) == 1:
        return int(fraction) / 10.0
    if len(fraction) == 2:
        return int(fraction) / 100.0
    return int(fraction[:3]) / 1000.0


def parse_lrc(text: str | None, offset_ms: int = 0) -> list[LyricLine]:
    """Parse LRC menjadi baris bertimestamp yang sudah diurutkan.

    Mendukung beberapa timestamp pada satu baris, ``[mm:ss.xx]`` maupun
    ``[mm:ss:xx]``, tag ``[offset:...]``, dan baris metadata lain diabaikan.
    """
    if not text:
        return []

    source = str(text).replace("\r\n", "\n").replace("\r", "\n")
    file_offset = 0
    match = _OFFSET_RE.search(source)
    if match:
        try:
            file_offset = int(match.group(1))
        except ValueError:
            file_offset = 0

    shift = (file_offset + int(offset_ms or 0)) / 1000.0
    lines: list[LyricLine] = []
    for raw in source.split("\n"):
        stamps = list(_TIMESTAMP_RE.finditer(raw))
        if not stamps:
            continue

        body = _TIMESTAMP_RE.sub("", raw)
        body = _META_RE.sub("", body).strip()
        if not body:
            # Baris kosong hanya menandai jeda instrumental; tidak perlu
            # dijadikan baris yang disorot.
            continue

        for stamp in stamps:
            minutes = int(stamp.group(1))
            seconds = int(stamp.group(2))
            if seconds > 59:
                continue
            timestamp = minutes * 60 + seconds + _fraction_seconds(stamp.group(3))
            lines.append(LyricLine(max(0.0, timestamp + shift), body))

    lines.sort(key=lambda line: line.time)

    result: list[LyricLine] = []
    seen: set[tuple[float, str]] = set()
    for line in lines:
        key = (round(line.time, 3), line.text)
        if key in seen:
            continue
        seen.add(key)
        result.append(line)
    return result


def parse_plain(text: str | None) -> list[str]:
    """Bagi lirik biasa menjadi baris, dengan satu baris kosong antar-bait."""
    if not text:
        return []
    raw = str(text).replace("\r\n", "\n").replace("\r", "\n")
    result: list[str] = []
    blank = False
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            if result and not blank:
                result.append("")
            blank = True
            continue
        result.append(line)
        blank = False
    while result and not result[-1]:
        result.pop()
    return result


# ------------------------------------------------------------ transliterasi
# Hangul tersusun algoritmik (U+AC00 + initial*588 + medial*28 + final), jadi
# romanisasi bisa dilakukan tanpa kamus dan tanpa dependensi. Tabel di bawah
# memakai Revised Romanization. Kanji/kana sengaja tidak didukung: kanji butuh
# kamus (berat), dan lirik Jepang yang tidak punya unggahan romaji dibiarkan
# apa adanya.
_HANGUL_BASE = 0xAC00
_HANGUL_LAST = 0xD7A3
_HANGUL_INITIALS = (
    "g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s",
    "ss", "", "j", "jj", "ch", "k", "t", "p", "h",
)
_HANGUL_MEDIALS = (
    "a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae",
    "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i",
)
_HANGUL_FINALS = (
    "", "k", "k", "ks", "n", "nj", "nh", "t", "l", "lk", "lm", "lb", "ls",
    "lt", "lp", "lh", "m", "p", "ps", "t", "t", "ng", "t", "t", "k", "t",
    "p", "t",
)
# Final yang pindah ke suku kata berikutnya saat bertemu awal ㅇ (liaison).
# Contoh: 한국어 -> hangugeo, 좋아 -> joa (ㅎ hilang).
_HANGUL_LIAISON = {
    1: "g", 4: "n", 7: "d", 8: "r", 16: "m", 17: "b",
    19: "s", 20: "s", 21: "ng", 22: "j", 23: "ch",
    24: "k", 25: "t", 26: "p", 27: "",
}
_SILENT_INITIAL = 11


def romanize_hangul(text: str) -> str:
    """Ubah Hangul ke huruf Latin; teks non-Hangul dibiarkan apa adanya.

    Ini transliterasi baca, bukan romanisasi sempurna: aturan asimilasi
    konsonan (mis. ㄱ+ㄴ) tidak diterapkan supaya kode tetap kecil.
    """
    if not text or not _HANGUL_RE.search(text):
        return text

    chars = str(text)
    out: list[str] = []
    index = 0
    while index < len(chars):
        offset = ord(chars[index]) - _HANGUL_BASE
        if not 0 <= offset <= _HANGUL_LAST - _HANGUL_BASE:
            out.append(chars[index])
            index += 1
            continue

        initial = offset // 588
        medial = (offset % 588) // 28
        final = offset % 28

        following = -1
        if final and index + 1 < len(chars):
            following = ord(chars[index + 1]) - _HANGUL_BASE
            if not 0 <= following <= _HANGUL_LAST - _HANGUL_BASE:
                following = -1

        if following >= 0:
            next_initial = following // 588
            # ㄹ bertemu ㄹ dibaca "ll" (솔로 -> sollo), bukan "lr".
            if final == 8 and next_initial == 5:
                out.append(_HANGUL_INITIALS[initial] + _HANGUL_MEDIALS[medial] + "l")
                out.append(
                    "l"
                    + _HANGUL_MEDIALS[(following % 588) // 28]
                    + _HANGUL_FINALS[following % 28]
                )
                index += 2
                continue
            if next_initial == _SILENT_INITIAL:
                carried = _HANGUL_LIAISON.get(final)
                if carried is not None:
                    out.append(_HANGUL_INITIALS[initial] + _HANGUL_MEDIALS[medial])
                    out.append(
                        carried
                        + _HANGUL_MEDIALS[(following % 588) // 28]
                        + _HANGUL_FINALS[following % 28]
                    )
                    index += 2
                    continue

        out.append(
            _HANGUL_INITIALS[initial]
            + _HANGUL_MEDIALS[medial]
            + _HANGUL_FINALS[final]
        )
        index += 1
    return "".join(out)


def lyrics_text(lyrics: Lyrics) -> str:
    """Gabungkan semua teks lirik untuk deteksi skrip."""
    parts = [line.text for line in lyrics.synced]
    parts.extend(lyrics.plain)
    return "\n".join(parts)


def needs_romanization(lyrics: Lyrics) -> bool:
    """True kalau lirik memakai aksara non-Latin (Korea/Jepang/Tionghoa)."""
    return bool(_CJK_RE.search(lyrics_text(lyrics)))


def _latin_profile(text: str) -> tuple[int, float]:
    """(jumlah huruf Latin, rasio vokal) untuk menilai apakah teks itu romaji."""
    letters = [ch for ch in text.lower() if "a" <= ch <= "z"]
    if not letters:
        return 0, 0.0
    vowels = sum(1 for ch in letters if ch in "aeiou")
    return len(letters), vowels / len(letters)


def looks_romanized(text: str) -> bool:
    """Tebak apakah teks sudah huruf Latin hasil romanisasi.

    Rekaman terjemahan bahasa Inggris juga murni Latin, jadi proporsi vokal
    dipakai sebagai pembeda: romaji jauh lebih vokalik daripada bahasa Inggris.
    """
    if not text or _CJK_RE.search(text):
        return False
    letters, vowel_ratio = _latin_profile(text)
    return letters >= _ROMAJI_MIN_LETTERS and vowel_ratio >= _ROMAJI_MIN_VOWELS


def _request_json(path: str, params: dict[str, object]) -> object:
    global _blocked_until
    if time.time() < _blocked_until:
        raise _NetworkError()

    query = urllib.parse.urlencode(
        {key: value for key, value in params.items() if value not in ("", None)}
    )
    url = f"{API_BASE}{path}?{query}" if query else f"{API_BASE}{path}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            if response.status == 404:
                raise _NotFound()
            if response.status < 200 or response.status >= 300:
                raise _NetworkError()
            payload = response.read(MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise _NotFound() from exc
        if exc.code == 429:
            _blocked_until = time.time() + _retry_after_seconds(exc)
        raise _NetworkError() from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise _NetworkError() from exc

    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _NetworkError() from exc


def _retry_after_seconds(exc: urllib.error.HTTPError) -> float:
    """Baca Retry-After (detik); pakai nilai wajar kalau tidak ada/aneh."""
    raw = None
    try:
        raw = exc.headers.get("Retry-After") if exc.headers else None
    except Exception:
        raw = None
    try:
        delay = float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        delay = 0.0
    return min(max(delay, 30.0), 300.0)


def _has_lyrics(record: object) -> bool:
    if not isinstance(record, dict):
        return False
    if record.get("instrumental"):
        return True
    return bool(record.get("syncedLyrics") or record.get("plainLyrics"))


def _duration_of(record: dict) -> float:
    try:
        return max(0.0, float(record.get("duration") or 0))
    except (TypeError, ValueError):
        return 0.0


def _pick_record(
    records: object, title: str, artist: str, duration: float
) -> dict | None:
    if not isinstance(records, list):
        return None

    wanted_title = normalize_text(title).casefold()
    wanted_artist = normalize_text(artist).casefold()
    candidates: list[tuple[float, dict]] = []

    for record in records:
        if not _has_lyrics(record):
            continue
        assert isinstance(record, dict)

        score = 0.0
        if record.get("syncedLyrics"):
            score += 100
        elif record.get("plainLyrics"):
            score += 45
        if record.get("instrumental"):
            score += 12

        record_title = normalize_text(record.get("trackName") or record.get("name"))
        record_artist = normalize_text(record.get("artistName"))
        if wanted_title and record_title.casefold() == wanted_title:
            score += 30
        elif wanted_title and wanted_title in record_title.casefold():
            score += 12

        if wanted_artist and record_artist.casefold() == wanted_artist:
            score += 20
        elif wanted_artist and wanted_artist in record_artist.casefold():
            score += 8

        record_duration = _duration_of(record)
        if duration > 0 and record_duration > 0:
            difference = abs(record_duration - duration)
            score += max(0.0, 35.0 - difference * 3.0)

        candidates.append((score, record))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _line_count(record: dict) -> int:
    """Perkiraan jumlah baris lirik sebuah rekaman."""
    synced = parse_lrc(record.get("syncedLyrics"))
    if synced:
        return len(synced)
    return len(parse_plain(record.get("plainLyrics")))


def _pick_romaji_record(records: object, base: Lyrics) -> dict | None:
    """Cari rekaman unggahan LRCLIB yang liriknya sudah huruf Latin.

    Syaratnya ketat karena lirik yang salah panjang/berbeda durasi akan
    membuat sorotan meleset: durasi harus sama (±2 detik, sama seperti aturan
    LRCLIB), rekaman harus punya timestamp kalau lirik aslinya bertimestamp,
    dan isinya harus lulus uji ``looks_romanized`` supaya terjemahan bahasa
    Inggris tidak ikut terpilih.
    """
    if not isinstance(records, list):
        return None

    base_lines = len(base.synced) or len(base.plain)
    candidates: list[tuple[float, dict]] = []
    for record in records:
        if not isinstance(record, dict) or not _has_lyrics(record):
            continue
        try:
            if int(record.get("id") or 0) == base.record_id:
                continue
        except (TypeError, ValueError):
            pass
        if base.synced and not record.get("syncedLyrics"):
            continue

        duration = _duration_of(record)
        if base.duration > 0:
            if duration <= 0 or abs(duration - base.duration) > 2.0:
                continue

        text = record.get("plainLyrics") or record.get("syncedLyrics") or ""
        if not looks_romanized(text):
            continue

        lines = _line_count(record)
        if base_lines and lines:
            tolerance = max(4, int(base_lines * 0.2))
            if abs(lines - base_lines) > tolerance:
                continue

        score = 0.0
        if record.get("syncedLyrics"):
            score += 100
        score += max(0.0, 30.0 - abs(lines - base_lines) * 2.0)
        score += max(0.0, 20.0 - abs(duration - base.duration) * 4.0)
        candidates.append((score, record))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _header_keys(track: str, artist: str) -> set[str]:
    """Baris judul yang biasa ditambahkan pengunggah lirik romaji."""
    track = normalize_text(track)
    artist = normalize_text(artist)
    keys: set[str] = set()
    if track and artist:
        keys.add(f"{track} - {artist}".casefold())
        keys.add(f"{artist} - {track}".casefold())
    return keys


def _apply_romaji(lyrics: Lyrics, record: dict) -> bool:
    """Pasang lirik romaji dari rekaman LRCLIB ke ``lyrics``."""
    synced = parse_lrc(record.get("syncedLyrics"))
    plain = parse_plain(record.get("plainLyrics"))
    if not synced and not plain:
        return False

    # Banyak unggahan romaji diawali baris "Artis - Judul" di detik 0. Itu
    # bukan lirik: kalau dibiarkan, baris pertama ikut disorot dan semua
    # kecocokan dengan lirik asli bergeser satu baris.
    headers = _header_keys(lyrics.track, lyrics.artist)
    if headers:
        if synced and synced[0].time <= 1.5:
            if normalize_text(synced[0].text).casefold() in headers:
                synced = synced[1:]
        if plain and normalize_text(plain[0]).casefold() in headers:
            plain = plain[1:]

    if not synced and not plain:
        return False
    lyrics.romaji = synced
    lyrics.romaji_plain = plain
    lyrics.romaji_source = "lrclib.net"
    return True


def _apply_transliteration(lyrics: Lyrics) -> bool:
    """Transliterasi lokal sebagai cadangan; hanya untuk Hangul.

    Lirik Jepang sengaja tidak disentuh: tanpa kamus kanji, hasilnya akan
    setengah jadi dan lebih membingungkan daripada lirik aslinya.
    """
    text = lyrics_text(lyrics)
    if not _HANGUL_RE.search(text):
        return False
    lyrics.romaji = [
        LyricLine(line.time, romanize_hangul(line.text)) for line in lyrics.synced
    ]
    lyrics.romaji_plain = [romanize_hangul(line) for line in lyrics.plain]
    lyrics.romaji_source = "transliterasi lokal"
    return lyrics.has_romaji


def display_text(
    lyrics: Lyrics, romanize: bool
) -> tuple[list[LyricLine], list[str], str]:
    """Pilih kumpulan lirik yang ditampilkan: asli atau versi huruf Latin.

    Mengembalikan ``(synced, plain, sumber)``. Versi romaji yang hanya teks
    biasa tidak boleh menggantikan lirik asli yang bertimestamp — sorotan akan
    hilang dan gulir otomatis berhenti.
    """
    if romanize and lyrics.has_romaji:
        if not lyrics.synced or lyrics.romaji:
            source = f"romaji ({lyrics.romaji_source})"
            return lyrics.romaji, lyrics.romaji_plain, source
    return lyrics.synced, lyrics.plain, lyrics.source


def _record_to_lyrics(record: dict) -> Lyrics | None:
    if not _has_lyrics(record):
        return None
    synced = parse_lrc(record.get("syncedLyrics"))
    plain = parse_plain(record.get("plainLyrics"))
    instrumental = bool(record.get("instrumental"))
    if not synced and not plain and not instrumental:
        return None
    try:
        record_id = int(record.get("id") or 0)
    except (TypeError, ValueError):
        record_id = 0
    return Lyrics(
        track=normalize_text(record.get("trackName") or record.get("name")),
        artist=normalize_text(record.get("artistName")),
        album=normalize_text(record.get("albumName")),
        duration=_duration_of(record),
        synced=synced,
        plain=plain,
        instrumental=instrumental,
        record_id=record_id,
    )


def _lyrics_to_cache(lyrics: Lyrics) -> dict:
    return {
        "track": lyrics.track,
        "artist": lyrics.artist,
        "album": lyrics.album,
        "duration": lyrics.duration,
        "synced": [[line.time, line.text] for line in lyrics.synced],
        "plain": lyrics.plain,
        "instrumental": lyrics.instrumental,
        "record_id": lyrics.record_id,
        "source": lyrics.source,
        "romaji": [[line.time, line.text] for line in lyrics.romaji],
        "romaji_plain": lyrics.romaji_plain,
        "romaji_source": lyrics.romaji_source,
        # Penanda bahwa pencarian versi romaji sudah dijalankan (walau
        # hasilnya kosong), supaya lagu Jepang tanpa romaji tidak diambil
        # ulang terus-menerus.
        "romaji_checked": True,
    }


def _synced_from_cache(raw: object) -> list[LyricLine]:
    lines: list[LyricLine] = []
    if not isinstance(raw, list):
        return lines
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            lines.append(LyricLine(float(item[0]), str(item[1])))
        except (TypeError, ValueError):
            continue
    return lines


def _lyrics_from_cache(data: object) -> Lyrics | None:
    if not isinstance(data, dict):
        return None
    plain = data.get("plain")
    if not isinstance(plain, list):
        plain = []
    romaji_plain = data.get("romaji_plain")
    if not isinstance(romaji_plain, list):
        romaji_plain = []
    try:
        record_id = int(data.get("record_id") or 0)
    except (TypeError, ValueError):
        record_id = 0
    lyrics = Lyrics(
        track=normalize_text(data.get("track")),
        artist=normalize_text(data.get("artist")),
        album=normalize_text(data.get("album")),
        duration=float(data.get("duration") or 0),
        synced=_synced_from_cache(data.get("synced")),
        plain=[str(line) for line in plain],
        instrumental=bool(data.get("instrumental")),
        record_id=record_id,
        source=normalize_text(data.get("source")) or "lrclib.net",
        romaji=_synced_from_cache(data.get("romaji")),
        romaji_plain=[str(line) for line in romaji_plain],
        romaji_source=normalize_text(data.get("romaji_source")),
    )
    return lyrics if lyrics.has_any else None


def _romaji_missing(cached: dict, restored: Lyrics) -> bool:
    """True kalau cache lama belum pernah dicek untuk versi romaji.

    Cache yang dibuat sebelum fitur romaji ada tidak punya penanda
    ``romaji_checked``, jadi lagu Korea/Jepang yang sudah pernah di-cache akan
    selamanya tampil tanpa romaji. Entri seperti itu dianggap kedaluwarsa
    supaya diambil ulang sekali.
    """
    lyrics = cached.get("lyrics")
    if isinstance(lyrics, dict) and lyrics.get("romaji_checked"):
        return False
    return needs_romanization(restored)


def _read_cache(path: Path) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write_cache(path: Path, payload: dict) -> None:
    try:
        config.atomic_write_text(
            path, json.dumps(payload, ensure_ascii=False) + "\n"
        )
    except OSError:
        pass


def fetch_lyrics(
    title: str,
    artist: str,
    album: str = "",
    duration: float = 0,
    cache_dir: Path | None = None,
) -> Lyrics | None:
    """Ambil lirik dari LRCLIB (blocking; panggil dari thread pekerja)."""
    title = normalize_text(title)
    artist = normalize_text(artist)
    album = normalize_text(album)
    if not title:
        return None

    cache_dir = Path(cache_dir) if cache_dir else config.LYRICS_CACHE_DIR
    key = cache_key(title, artist, album, duration)
    path = cache_dir / f"{key}.json"
    cached = _read_cache(path)
    if cached is not None:
        try:
            age = time.time() - float(cached.get("saved") or 0)
        except (TypeError, ValueError):
            age = CACHE_TTL + 1
        if cached.get("negative"):
            if age < NEGATIVE_TTL:
                return None
        elif age < CACHE_TTL:
            restored = _lyrics_from_cache(cached.get("lyrics"))
            if restored is not None and not _romaji_missing(cached, restored):
                return restored

    params: dict[str, object] = {
        "track_name": title,
        "artist_name": artist,
    }
    if album:
        params["album_name"] = album
    if 1 <= duration <= 3600:
        params["duration"] = int(round(duration))

    record: object = None
    results: object = None
    try:
        record = _request_json("/api/get", params)
    except _NotFound:
        record = None
    except _NetworkError:
        return None

    if not _has_lyrics(record):
        record = None
        query = clean_title(title)
        search_params: dict[str, object] = {"track_name": query}
        if artist:
            search_params["artist_name"] = artist
        try:
            results = _request_json("/api/search", search_params)
        except _NotFound:
            results = []
        except _NetworkError:
            return None
        record = _pick_record(results, title, artist, duration)

    if not _has_lyrics(record):
        _write_cache(
            path,
            {
                "saved": time.time(),
                "negative": True,
                "query": {"title": title, "artist": artist, "album": album},
            },
        )
        return None

    assert isinstance(record, dict)
    lyrics = _record_to_lyrics(record)
    if lyrics is None:
        _write_cache(
            path,
            {
                "saved": time.time(),
                "negative": True,
                "query": {"title": title, "artist": artist, "album": album},
            },
        )
        return None

    _attach_romaji(lyrics, results, title, artist, duration)

    _write_cache(
        path,
        {
            "saved": time.time(),
            "negative": False,
            "lyrics": _lyrics_to_cache(lyrics),
            "query": {"title": title, "artist": artist, "album": album},
        },
    )
    return lyrics


def _attach_romaji(
    lyrics: Lyrics,
    results: object,
    title: str,
    artist: str,
    duration: float,
) -> None:
    """Isi ``lyrics.romaji`` kalau lagu memakai aksara non-Latin.

    Urutannya: rekaman romaji unggahan LRCLIB dulu (kualitasnya buatan
    manusia), baru transliterasi Hangul lokal. Lagu yang tidak punya keduanya
    — termasuk lagu Jepang berkanji tanpa unggahan romaji — dibiarkan apa
    adanya. Satu permintaan pencarian tambahan hanya terjadi untuk lagu
    non-Latin, dan hasilnya ikut tersimpan di cache.
    """
    if not needs_romanization(lyrics):
        return

    if results is None:
        search_params: dict[str, object] = {"track_name": clean_title(title)}
        if artist:
            search_params["artist_name"] = artist
        try:
            results = _request_json("/api/search", search_params)
        except (_NotFound, _NetworkError):
            results = []

    record = _pick_romaji_record(results, lyrics)
    if record is not None and _apply_romaji(lyrics, record):
        return
    _apply_transliteration(lyrics)
