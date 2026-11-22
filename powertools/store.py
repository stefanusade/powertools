"""Penyimpanan riwayat clipboard.

Format penyimpanan sengaja dibuat append-only (`clipboard.jsonl`) supaya
ekstensi GNOME Shell bisa menulis tanpa perlu membaca/menulis ulang seluruh
file setiap kali ada copy. Aplikasi (proses Python) yang melakukan
deduplikasi, pengurutan, pin, dan pemadatan.

Dua jenis catatan:

    {"t": "teks", "ts": 123.4}                      teks
    {"i": "<sha1>", "m": "image/png", "n": 81234,   gambar; isinya ada di
     "ts": 123.4}                                   images/<sha1>
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import config

MAX_LINE_BYTES = 1_000_000  # baris rusak / terlalu besar dilewati
IMAGE_KEY_PREFIX = "img:"
DEFAULT_IMAGE_MIME = "image/png"
IMAGE_MIMES = ("image/png", "image/jpeg", "image/webp", "image/bmp")


def sniff_image_mime(path: Path) -> str:
    """Tentukan format dari beberapa byte pertama (record lama bisa salah)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
    except OSError:
        return ""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"BM"):
        return "image/bmp"
    return ""


def clip_key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


@dataclass(slots=True)
class Clip:
    text: str
    ts: float
    image: str = ""
    mime: str = DEFAULT_IMAGE_MIME
    size: int = 0
    width: int = 0
    height: int = 0
    pinned: bool = False

    @property
    def is_image(self) -> bool:
        return bool(self.image)

    @property
    def key(self) -> str:
        if self.image:
            return IMAGE_KEY_PREFIX + self.image
        return clip_key(self.text)

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def lines(self) -> int:
        return self.text.count("\n") + 1


class ClipStore:
    def __init__(
        self,
        spool_path: Path | None = None,
        state_path: Path | None = None,
        max_items: int = 300,
        max_chars: int = 100_000,
        images_dir: Path | None = None,
        thumbs_dir: Path | None = None,
        images: bool = True,
        max_image_bytes: int = 20 * 1024 * 1024,
        max_image_items: int = 60,
        max_age_seconds: float = 0,
    ) -> None:
        self.spool_path = Path(spool_path) if spool_path else config.SPOOL_PATH
        self.state_path = Path(state_path) if state_path else config.STATE_PATH
        self.images_dir = Path(images_dir) if images_dir else config.IMAGES_DIR
        self.thumbs_dir = Path(thumbs_dir) if thumbs_dir else config.THUMBS_DIR
        self.max_items = max_items
        self.max_chars = max_chars
        self.images = images
        self.max_image_bytes = max_image_bytes
        self.max_image_items = max_image_items
        try:
            self.max_age_seconds = max(0.0, float(max_age_seconds))
        except (TypeError, ValueError):
            self.max_age_seconds = 0.0
        self._pins: dict[str, float] = {}
        self._deleted: dict[str, float] = {}
        self._expired: set[str] = set()
        self._load_state()

    # ------------------------------------------------------------------ state
    def _load_state(self) -> None:
        try:
            with open(self.state_path, "r", encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, ValueError):
            state = {}
        pins = state.get("pins") if isinstance(state, dict) else None
        deleted = state.get("deleted") if isinstance(state, dict) else None
        self._pins = {k: float(v) for k, v in pins.items()} if isinstance(pins, dict) else {}
        self._deleted = {k: float(v) for k, v in deleted.items()} if isinstance(deleted, dict) else {}

    def _save_state(self) -> None:
        state = {"pins": self._pins, "deleted": self._deleted}
        config.atomic_write_text(self.state_path, json.dumps(state, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------- read
    def _iter_records(self) -> Iterable[tuple[dict, float]]:
        try:
            fh = open(self.spool_path, "r", encoding="utf-8", errors="replace")
        except OSError:
            return
        with fh:
            for line in fh:
                if not line or len(line) > MAX_LINE_BYTES:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                try:
                    ts = float(rec.get("ts") or 0.0)
                except (TypeError, ValueError):
                    ts = 0.0
                yield rec, ts

    def _clip_from_record(self, rec: dict, ts: float) -> Clip | None:
        text = rec.get("t")
        if isinstance(text, str) and text.strip():
            if len(text) > self.max_chars:
                return None
            return Clip(text=text, ts=ts)

        if not self.images:
            return None
        name = rec.get("i")
        if not isinstance(name, str) or not name or "/" in name or name.startswith("."):
            return None
        try:
            size = (self.images_dir / name).stat().st_size
        except OSError:
            return None
        if size <= 0 or size > self.max_image_bytes:
            return None
        mime = rec.get("m")
        if not isinstance(mime, str) or mime not in IMAGE_MIMES:
            mime = DEFAULT_IMAGE_MIME
        detected = sniff_image_mime(self.images_dir / name)
        if detected:
            mime = detected
        return Clip(
            text="",
            ts=ts,
            image=name,
            mime=mime,
            size=size,
        )

    def load(self) -> list[Clip]:
        newest: dict[str, Clip] = {}
        for rec, ts in self._iter_records():
            clip = self._clip_from_record(rec, ts)
            if clip is None:
                continue
            key = clip.key
            deleted_at = self._deleted.get(key)
            if deleted_at is not None and ts <= deleted_at:
                continue
            current = newest.get(key)
            if current is None or ts >= current.ts:
                newest[key] = clip

        clips = list(newest.values())
        for clip in clips:
            clip.pinned = clip.key in self._pins

        # Item yang di-pin dikecualikan dari masa kedaluwarsa. Kunci yang
        # dibuang dicatat supaya prune() bisa memangkas spool dan berkasnya.
        self._expired = set()
        if self.max_age_seconds > 0:
            cutoff = time.time() - self.max_age_seconds
            self._expired = {
                c.key for c in clips if not c.pinned and c.ts < cutoff
            }
            clips = [c for c in clips if c.pinned or c.ts >= cutoff]

        clips.sort(key=lambda c: (not c.pinned, -c.ts))
        clips = self._limit(clips)

        # Nama berkas gambar yang masih dipakai; dipakai untuk pembersihan.
        self.live_images = {c.image for c in clips if c.is_image}
        return clips

    def _limit(self, clips: list[Clip]) -> list[Clip]:
        # Kuota teks dan gambar dipisah supaya tangkapan layar tidak
        # menghabiskan seluruh riwayat teks (dan sebaliknya).
        texts = self._cap([c for c in clips if not c.is_image], self.max_items)
        images = self._cap([c for c in clips if c.is_image], self.max_image_items)
        merged = texts + images
        merged.sort(key=lambda c: (not c.pinned, -c.ts))
        return merged

    @staticmethod
    def _cap(items: list[Clip], limit: int) -> list[Clip]:
        pinned = [c for c in items if c.pinned]
        regular = [c for c in items if not c.pinned]
        keep = max(limit - len(pinned), 0)
        return pinned + regular[:keep]

    # ------------------------------------------------------------------ write
    def append(self, text: str, ts: float | None = None) -> None:
        if not text or not text.strip() or len(text) > self.max_chars:
            return
        self._append_record({"t": text, "ts": time.time() if ts is None else float(ts)})

    def append_image(
        self,
        image: str,
        mime: str = DEFAULT_IMAGE_MIME,
        size: int = 0,
        ts: float | None = None,
    ) -> None:
        if not image:
            return
        self._append_record(
            {
                "i": image,
                "m": mime or DEFAULT_IMAGE_MIME,
                "n": int(size or 0),
                "ts": time.time() if ts is None else float(ts),
            }
        )

    def _append_record(self, record: dict) -> None:
        self.spool_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.spool_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
        except OSError:
            pass

    def touch(self, clip_or_text: Clip | str) -> None:
        """Naikkan item ke posisi terbaru (dipakai saat item ditempel ulang)."""
        if isinstance(clip_or_text, Clip):
            if clip_or_text.is_image:
                self.append_image(
                    clip_or_text.image, clip_or_text.mime, clip_or_text.size
                )
            else:
                self.append(clip_or_text.text)
            key = clip_or_text.key
        else:
            self.append(clip_or_text)
            key = clip_key(clip_or_text)
        # Copy baru membatalkan tombstone lama.
        if key in self._deleted:
            del self._deleted[key]
            self._save_state()

    def toggle_pin(self, clip: Clip) -> bool:
        key = clip.key
        if key in self._pins:
            del self._pins[key]
            pinned = False
        else:
            self._pins[key] = time.time()
            pinned = True
        self._save_state()
        return pinned

    def delete(self, clip: Clip) -> None:
        key = clip.key
        self._deleted[key] = time.time()
        self._pins.pop(key, None)
        self._save_state()
        remaining = [c for c in self.load() if c.key != key]
        self._rewrite(remaining)
        self._prune_files({c.image for c in remaining if c.is_image}, age_guard=0)

    def clear(self) -> None:
        self._pins.clear()
        self._deleted.clear()
        self._save_state()
        self._rewrite([])
        self._prune_files(set(), age_guard=0)

    def prune(self) -> None:
        """Buang item kedaluwarsa dan berkas gambar yang tidak lagi dirujuk.

        Kuota gambar hanya membatasi tampilan: berkas lama di luar kuota
        tetap harus dihapus dari disk. Spool juga dipangkas kalau ada
        catatan yang melewati `max_age_seconds`.
        """
        self.load()
        self._prune_files(set(getattr(self, "live_images", set())))
        # Catatan yang sudah kedaluwarsa (dan tombstone lama) ikut dibuang
        # dari spool supaya file tidak tumbuh selamanya.
        if self._expired:
            self._safe_rewrite()
            self._expired = set()

    def compact_if_needed(self) -> bool:
        """Pangkas spool kalau sudah membengkak. Return True kalau menulis."""
        try:
            size = self.spool_path.stat().st_size
        except OSError:
            return False
        if size < 512 * 1024:
            return False
        self.load()
        if not self._safe_rewrite():
            return False
        self._prune_files(self.live_images)
        return True

    def _spool_signature(self) -> tuple[int, int] | None:
        try:
            info = self.spool_path.stat()
        except OSError:
            return None
        return (info.st_size, info.st_mtime_ns)

    def _safe_rewrite(self) -> bool:
        """Tulis ulang spool hanya kalau ekstensi tidak sedang menulis.

        Ekstensi menambah baris ke berkas yang sama. Kalau ia menulis di
        antara pembacaan dan penggantian berkas, catatan baru itu akan ikut
        terhapus — clipboard lalu terasa "berhenti" sampai modul di-toggle.
        Karena itu penulisan ulang ditunda ke siklus berikutnya bila ukuran
        atau mtime berkas berubah saat kita membaca.
        """
        before = self._spool_signature()
        clips = self.load()
        if self._spool_signature() != before:
            return False
        self._rewrite(clips)
        return True

    def _rewrite(self, clips: Iterable[Clip]) -> None:
        lines = []
        for c in sorted(clips, key=lambda c: c.ts):
            if c.is_image:
                record = {"i": c.image, "m": c.mime, "n": c.size, "ts": c.ts}
            else:
                record = {"t": c.text, "ts": c.ts}
            lines.append(json.dumps(record, ensure_ascii=False) + "\n")
        try:
            config.atomic_write_text(self.spool_path, "".join(lines))
        except OSError:
            pass

    def _prune_files(self, keep: set[str], age_guard: float = 60) -> None:
        """Hapus berkas gambar/thumbnail yang tidak lagi dirujuk riwayat.

        Berkas yang baru dibuat (< age_guard detik) dibiarkan supaya tidak
        balapan dengan ekstensi yang baru menulis tangkapan.
        """
        cutoff = time.time() - age_guard
        for folder in (self.images_dir, self.thumbs_dir):
            try:
                entries = list(folder.iterdir())
            except OSError:
                continue
            for path in entries:
                name = path.name
                for suffix in (".png", ".json"):
                    if name.endswith(suffix):
                        name = name[: -len(suffix)]
                        break
                if name in keep:
                    continue
                try:
                    if path.stat().st_mtime > cutoff:
                        continue
                    path.unlink()
                except OSError:
                    pass

    def stats(self) -> dict[str, int]:
        try:
            size = self.spool_path.stat().st_size
        except OSError:
            size = 0
        clips = self.load()
        images = [c for c in clips if c.is_image]
        return {
            "items": len(clips),
            "bytes": size,
            "images": len(images),
            "image_bytes": sum(c.size for c in images),
        }