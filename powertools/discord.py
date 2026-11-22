"""Discord Rich Presence "Listening to" lewat IPC lokal.

Discord desktop — termasuk versi Flatpak dan Snap — membuka Unix socket
``discord-ipc-N``. Protokolnya sederhana: header 8 byte (opcode + panjang
little-endian) diikuti payload JSON. Modul ini hanya memakai standard library,
tidak mengirim apa pun ke internet, dan tidak butuh token/bot.

Discord tetap mewajibkan **Application ID** untuk handshake. ID itu tidak
diminta dari pengguna: PowerTools membawa beberapa preset di ``sources.py``
(milik proyek ini), dan Pengaturan hanya memilih preset mana yang dipakai untuk
tiap sumber. Nama aplikasi preset itulah yang tampil sebagai
"Listening to ...".
"""

from __future__ import annotations

import json
import os
import queue
import select
import socket
import struct
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

OP_HANDSHAKE = 0
OP_FRAME = 1
OP_CLOSE = 2
OP_PING = 3
OP_PONG = 4

MAX_FRAME = 1 << 20
ACTIVITY_TYPE_LISTENING = 2

# Discord membatasi SET_ACTIVITY sekitar 5 permintaan per 20 detik. Pakai
# token bucket supaya burst singkat (ganti lagu beruntun) tetap wajar tanpa
# membuat Discord menutup koneksi.
RATE_LIMIT = 5
RATE_WINDOW = 20.0

_UNSET = object()


def valid_app_id(app_id: object) -> bool:
    """Discord Application ID berupa deret angka (snowflake)."""
    text = str(app_id or "").strip()
    return text.isdigit() and 15 <= len(text) <= 25


def _clip(text: object, limit: int = 128) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def encode_frame(opcode: int, payload: dict[str, Any] | None = None) -> bytes:
    """Bungkus payload JSON menjadi frame IPC Discord."""
    data = json.dumps(
        payload or {}, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    if len(data) > MAX_FRAME:
        raise ValueError("payload Discord terlalu besar")
    return struct.pack("<ii", int(opcode), len(data)) + data


def decode_header(header: bytes) -> tuple[int, int]:
    if len(header) != 8:
        raise ValueError("header Discord harus 8 byte")
    opcode, length = struct.unpack("<ii", header)
    if opcode < 0 or length < 0 or length > MAX_FRAME:
        raise ValueError("frame Discord tidak valid")
    return opcode, length


def decode_frame(raw: bytes) -> tuple[int, dict[str, Any]]:
    """Kebalikan encode_frame; dipakai juga oleh tes."""
    opcode, length = decode_header(raw[:8])
    if len(raw) != 8 + length:
        raise ValueError("panjang frame Discord tidak cocok")
    payload = json.loads(raw[8:].decode("utf-8") or "{}")
    if not isinstance(payload, dict):
        raise ValueError("payload Discord bukan objek")
    return opcode, payload


def socket_candidates() -> list[Path]:
    """Semua lokasi socket Discord yang umum, termasuk Flatpak/Snap."""
    override = os.environ.get("DISCORD_IPC_PATH")
    if override:
        return [Path(override).expanduser()]

    bases: list[Path] = []
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        base = Path(runtime)
        bases.extend(
            [
                base / "app" / "com.discordapp.Discord",
                base / "app" / "com.discordapp.DiscordCanary",
                base / "app" / "com.discordapp.DiscordPTB",
                base / ".flatpak" / "com.discordapp.Discord",
                base / "snap.discord",
                base / "snap.discord-canary",
                base / "snap.discord-ptb",
                base,
            ]
        )
    for env_name in ("TMPDIR", "TEMP", "TMP"):
        value = os.environ.get(env_name)
        if value:
            bases.append(Path(value))
    bases.append(Path("/tmp"))

    seen: set[str] = set()
    result: list[Path] = []
    for base in bases:
        for index in range(10):
            path = base / f"discord-ipc-{index}"
            key = str(path)
            if key not in seen:
                seen.add(key)
                result.append(path)
    return result


def build_activity(
    *,
    title: object,
    artist: object = "",
    album: object = "",
    length_us: object = 0,
    art_url: object = "",
    position: object = 0.0,
    playing: bool = True,
    show_paused: bool = False,
    show_album_art: bool = True,
    fallback_asset: object = "",
) -> dict[str, Any] | None:
    """Susun objek activity Discord (type 2 = Listening to).

    Mengembalikan None kalau tidak ada yang perlu ditampilkan. Timestamp
    memakai posisi saat ini supaya progress bar Discord langsung tepat.
    """
    clean_title = _clip(title)
    if not clean_title:
        return None
    if not playing and not show_paused:
        return None

    clean_artist = _clip(artist)
    clean_album = _clip(album)

    state_parts: list[str] = []
    if clean_artist:
        state_parts.append(clean_artist)
    elif clean_album:
        state_parts.append(clean_album)
    if not playing:
        state_parts.append("Dijeda")

    activity: dict[str, Any] = {
        "type": ACTIVITY_TYPE_LISTENING,
        "details": clean_title,
        "instance": False,
    }
    state = " · ".join(part for part in state_parts if part)
    if state:
        activity["state"] = _clip(state)

    if playing:
        try:
            position_s = max(0, int(float(position or 0)))
        except (TypeError, ValueError):
            position_s = 0
        now = int(time.time())
        start = now - position_s
        timestamps: dict[str, int] = {"start": start}
        try:
            length_s = max(0, int(length_us) // 1_000_000)
        except (TypeError, ValueError):
            length_s = 0
        if length_s > 0:
            timestamps["end"] = start + length_s
        activity["timestamps"] = timestamps

    image = str(art_url or "").strip()
    # Discord hanya bisa mengambil gambar dari internet. Cover file://
    # (Chromium menyimpan cover di /tmp) dan data: dilewati, lalu diganti
    # asset bawaan aplikasi supaya tidak muncul tanda "?".
    public = image.startswith(("http://", "https://")) and len(image) <= 256
    fallback = str(fallback_asset or "").strip()
    if show_album_art:
        if public:
            activity["assets"] = {
                "large_image": image,
                "large_text": _clip(clean_album or clean_title),
            }
        elif fallback:
            activity["assets"] = {
                "large_image": fallback,
                "large_text": _clip(clean_album or clean_title),
            }

    return activity


class DiscordPresence:
    """Klien IPC Discord yang berjalan di thread sendiri.

    Semua operasi socket (koneksi, handshake, SET_ACTIVITY) dilakukan di
    thread terpisah supaya main loop GTK tidak pernah tertahan. Pemanggil
    cukup memanggil ``update(activity)`` atau ``clear()``.
    """

    def __init__(
        self,
        app_id: str,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self.app_id = str(app_id or "").strip()
        self._on_status = on_status
        self._queue: queue.Queue[object] = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self._status = "Menunggu Discord"
        self._status_lock = threading.Lock()
        self._desired: object = _UNSET
        self._desired_lock = threading.Lock()
        self._last_sent: object = _UNSET
        self._last_sent_lock = threading.Lock()
        # Nonce + nama asset terakhir, untuk memeriksa balasan Discord:
        # asset yang tidak ada di portal akan dibuang dari activity.
        self._sent_nonce: str | None = None
        self._sent_asset: str | None = None
        self._tokens = float(RATE_LIMIT)
        self._token_at = time.monotonic()
        self._closing = False
        self._cleared = threading.Event()

    @property
    def status(self) -> str:
        with self._status_lock:
            return self._status

    def _set_status(self, text: str) -> None:
        with self._status_lock:
            if self._status == text:
                return
            self._status = text
        callback = self._on_status
        if callback is not None:
            try:
                callback(text)
            except Exception:
                pass

    # ------------------------------------------------------------ public API
    def start(self) -> None:
        if self._thread is not None:
            return
        self._closing = False
        self._thread = threading.Thread(
            target=self._run, name="powertools-discord", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive() and self._sock is not None:
            # Beri worker kesempatan mengirim SET_ACTIVITY null sebelum socket
            # ditutup, supaya status tidak menggantung di profil Discord.
            # Lewati rate limit untuk permintaan terakhir ini.
            self._closing = True
            self._cleared.clear()
            self.update(None)
            self._cleared.wait(0.6)
        self._stop_event.set()
        self._queue.put(_UNSET)
        self._close()
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout)

    def update(self, activity: dict[str, Any] | None) -> None:
        with self._desired_lock:
            self._desired = activity
        self._queue.put(activity)

    def clear(self) -> None:
        self.update(None)

    def set_app_id(self, app_id: str) -> None:
        """Ganti Application ID saat berganti sumber lagu.

        Handshake terikat ke ``client_id``, jadi koneksi lama harus ditutup.
        Worker akan menyambung ulang lalu mengirim ulang activity terakhir.
        """
        new_id = str(app_id or "").strip()
        if new_id == self.app_id:
            return
        self.app_id = new_id
        self._close()
        with self._desired_lock:
            desired = self._desired
        if desired is _UNSET:
            # Belum pernah ada activity: tidak ada yang perlu dikirim.
            return
        self._queue.put(desired)

    # -------------------------------------------------------------- main loop
    def _run(self) -> None:
        while not self._stop_event.is_set():
            if self._sock is None:
                if not valid_app_id(self.app_id):
                    self._set_status("Application ID Discord tidak valid")
                    self._stop_event.wait(3.0)
                    continue
                # Jangan mengedipkan status tiap percobaan ulang.
                if self.status != "Discord tidak berjalan":
                    self._set_status("Menghubungkan ke Discord...")
                if not self._connect():
                    self._set_status("Discord tidak berjalan")
                    self._stop_event.wait(3.0)
                    continue
                self._set_status("Terhubung ke Discord")
                with self._desired_lock:
                    desired = self._desired
                if desired is not _UNSET:
                    if not self._send_if_changed(desired):
                        self._close()
                        continue

            try:
                self._queue.get(timeout=0.5)
            except queue.Empty:
                pass
            else:
                self._drain_queue()

            if self._stop_event.is_set():
                break
            # Selalu coba kirim state terbaru. Kalau kena rate limit, loop
            # berikutnya (0,5 dtk) mencoba lagi tanpa kehilangan update.
            with self._desired_lock:
                desired = self._desired
            if not self._send_if_changed(desired):
                self._close()
                continue
            if not self._read_pending():
                self._close()

        self._close()

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    # -------------------------------------------------------------- koneksi
    def _connect(self) -> bool:
        if not valid_app_id(self.app_id):
            self._set_status("Application ID Discord tidak valid")
            return False

        for path in socket_candidates():
            try:
                if not path.exists():
                    continue
            except OSError:
                continue

            sock: socket.socket | None = None
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(2.0)
                sock.connect(str(path))
                self._sock = sock
                if self._handshake():
                    sock.settimeout(None)
                    return True
            except OSError:
                pass
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            self._close()
        return False

    def _handshake(self) -> bool:
        if self._sock is None:
            return False
        if not self._send_frame(
            OP_HANDSHAKE, {"v": 1, "client_id": self.app_id}
        ):
            return False

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not self._stop_event.is_set():
            remaining = max(0.05, deadline - time.monotonic())
            try:
                self._sock.settimeout(remaining)
            except OSError:
                return False

            frame = self._read_frame()
            if frame is None:
                return False
            opcode, payload = frame
            if opcode == OP_PING:
                self._send_frame(OP_PONG, payload)
            elif opcode == OP_CLOSE:
                return False
            elif opcode == OP_FRAME:
                event = str(payload.get("evt") or "")
                if event == "READY":
                    return True
                if event == "ERROR":
                    data = payload.get("data")
                    message = (
                        str(data.get("message") or "ditolak Discord")
                        if isinstance(data, dict)
                        else "ditolak Discord"
                    )
                    self._set_status(f"Discord: {message}")
                    return False
        return False

    def _close(self) -> None:
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        # Saat koneksi baru, Discord belum punya activity apa pun; paksa
        # activity terakhir dikirim ulang.
        with self._last_sent_lock:
            self._last_sent = _UNSET

    # ----------------------------------------------------------------- I/O
    def _recv_exact(self, count: int) -> bytes | None:
        sock = self._sock
        if sock is None:
            return None
        chunks = bytearray()
        while len(chunks) < count:
            try:
                chunk = sock.recv(count - len(chunks))
            except (socket.timeout, OSError):
                return None
            if not chunk:
                return None
            chunks.extend(chunk)
        return bytes(chunks)

    def _read_frame(self) -> tuple[int, dict[str, Any]] | None:
        header = self._recv_exact(8)
        if header is None:
            return None
        try:
            opcode, length = decode_header(header)
        except ValueError:
            return None
        body = self._recv_exact(length) if length else b""
        if body is None:
            return None
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        return opcode, payload

    def _send_frame(self, opcode: int, payload: dict[str, Any]) -> bool:
        sock = self._sock
        if sock is None:
            return False
        try:
            sock.sendall(encode_frame(opcode, payload))
            return True
        except (OSError, ValueError):
            return False

    def _send_if_changed(self, activity: object) -> bool:
        """Hindari SET_ACTIVITY ganda untuk state yang sama."""
        if activity is _UNSET:
            return True
        with self._last_sent_lock:
            if activity == self._last_sent:
                if activity is None:
                    self._cleared.set()
                return True
            if activity is None and self._last_sent is _UNSET:
                # Koneksi baru belum punya activity; tidak perlu kirim null.
                self._last_sent = None
                self._cleared.set()
                return True
        if not self._closing and not self._take_token():
            # Jatah rate limit habis. State terbaru tetap di _desired dan
            # akan dikirim pada putaran berikutnya.
            return True
        if not self._send_activity(activity):
            return False
        with self._last_sent_lock:
            self._last_sent = activity
        if activity is None:
            self._cleared.set()
        return True

    def _take_token(self) -> bool:
        """Token bucket untuk batas SET_ACTIVITY Discord."""
        now = time.monotonic()
        self._tokens = min(
            float(RATE_LIMIT),
            self._tokens + (now - self._token_at) * (RATE_LIMIT / RATE_WINDOW),
        )
        self._token_at = now
        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        return True

    def _send_activity(self, activity: object) -> bool:
        if activity is _UNSET:
            return True
        nonce = str(uuid.uuid4())
        payload = {
            "cmd": "SET_ACTIVITY",
            "args": {"pid": os.getpid(), "activity": activity},
            "nonce": nonce,
        }
        asset: str | None = None
        if isinstance(activity, dict):
            assets = activity.get("assets")
            if isinstance(assets, dict):
                asset = str(assets.get("large_image") or "") or None
        self._sent_nonce = nonce
        self._sent_asset = asset
        return self._send_frame(OP_FRAME, payload)

    def _check_activity_response(self, data: object) -> None:
        """Beri tahu kalau Discord membuang gambar karena asset tidak ada.

        Discord membalas SET_ACTIVITY dengan activity yang benar-benar
        dipakai. Kalau kita mengirim nama asset yang belum diunggah di
        Developer Portal, ``large_image`` hilang dari balasan dan pengguna
        hanya melihat tanda "?" tanpa penjelasan.
        """
        if not isinstance(data, dict):
            return
        assets = data.get("assets")
        shown = isinstance(assets, dict) and bool(assets.get("large_image"))
        if self._sent_asset and not shown:
            self._set_status(
                f'Terhubung — asset "{self._sent_asset}" tidak ada di portal Discord'
            )
        elif self.status != "Terhubung ke Discord":
            self._set_status("Terhubung ke Discord")

    def _read_pending(self) -> bool:
        sock = self._sock
        if sock is None:
            return False
        while not self._stop_event.is_set():
            try:
                readable, _, _ = select.select([sock], [], [], 0)
            except (OSError, ValueError):
                return False
            if not readable:
                return True
            try:
                sock.settimeout(1.0)
            except OSError:
                return False

            frame = self._read_frame()
            if frame is None:
                return False
            opcode, payload = frame
            if opcode == OP_PING:
                if not self._send_frame(OP_PONG, payload):
                    return False
            elif opcode == OP_CLOSE:
                return False
            elif opcode == OP_FRAME:
                if str(payload.get("evt") or "") == "ERROR":
                    data = payload.get("data")
                    message = (
                        str(data.get("message") or "error")
                        if isinstance(data, dict)
                        else "error"
                    )
                    self._set_status(f"Discord: {message}")
                    return False
                if payload.get("nonce") == self._sent_nonce:
                    self._check_activity_response(payload.get("data"))
        return True
