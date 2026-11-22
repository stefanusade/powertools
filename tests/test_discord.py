"""Tes Discord Rich Presence tanpa GTK dan tanpa Discord sungguhan."""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powertools.discord import (
    RATE_LIMIT,
    RATE_WINDOW,
    DiscordPresence,
    build_activity,
    decode_frame,
    encode_frame,
    socket_candidates,
    valid_app_id,
)


class FrameTests(unittest.TestCase):
    def test_encode_decode_bolak_balik(self):
        raw = encode_frame(1, {"cmd": "SET_ACTIVITY", "args": {"pid": 7}})
        opcode, payload = decode_frame(raw)
        self.assertEqual(opcode, 1)
        self.assertEqual(payload["cmd"], "SET_ACTIVITY")
        self.assertEqual(payload["args"]["pid"], 7)

    def test_header_little_endian(self):
        raw = encode_frame(3, {})
        self.assertEqual(raw[:8], struct.pack("<ii", 3, 2))

    def test_frame_rusak_ditolak(self):
        with self.assertRaises(ValueError):
            decode_frame(b"\x01\x00\x00\x00\x05\x00\x00\x00{}")


class AppIdTests(unittest.TestCase):
    def test_id_valid(self):
        self.assertTrue(valid_app_id("123456789012345678"))
        self.assertTrue(valid_app_id(" 123456789012345678 "))

    def test_id_tidak_valid(self):
        self.assertFalse(valid_app_id(""))
        self.assertFalse(valid_app_id("abc"))
        self.assertFalse(valid_app_id("123"))
        self.assertFalse(valid_app_id("12345678901234567890x"))


class ActivityTests(unittest.TestCase):
    def test_lagu_diputar(self):
        activity = build_activity(
            title="Lagu",
            artist="Artis",
            album="Album",
            length_us=200_000_000,
            art_url="https://example.test/cover.jpg",
            position=30,
            playing=True,
        )
        self.assertIsNotNone(activity)
        self.assertEqual(activity["type"], 2)
        self.assertEqual(activity["details"], "Lagu")
        self.assertEqual(activity["state"], "Artis")
        self.assertEqual(activity["assets"]["large_image"],
                         "https://example.test/cover.jpg")
        self.assertIn("start", activity["timestamps"])
        self.assertIn("end", activity["timestamps"])
        self.assertEqual(
            activity["timestamps"]["end"] - activity["timestamps"]["start"], 200
        )

    def test_lagu_dijeda_disembunyikan_secara_default(self):
        self.assertIsNone(
            build_activity(title="Lagu", playing=False, show_paused=False)
        )

    def test_lagu_dijeda_bisa_ditampilkan(self):
        activity = build_activity(
            title="Lagu", artist="Artis", playing=False, show_paused=True
        )
        self.assertIsNotNone(activity)
        self.assertIn("Dijeda", activity["state"])
        self.assertNotIn("timestamps", activity)

    def test_tanpa_judul_tidak_ada_activity(self):
        self.assertIsNone(build_activity(title="   ", playing=True))

    def test_cover_non_http_dilewati(self):
        for url in ("file:///tmp/a.png", "data:image/png;base64,abc"):
            activity = build_activity(
                title="Lagu", art_url=url, playing=True, show_album_art=True
            )
            self.assertNotIn("assets", activity)

    def test_cover_url_kepanjangan_dilewati(self):
        activity = build_activity(
            title="Lagu",
            art_url="https://example.test/" + "a" * 300,
            playing=True,
            show_album_art=True,
        )
        self.assertNotIn("assets", activity)

    def test_cover_bisa_dimatikan(self):
        activity = build_activity(
            title="Lagu",
            art_url="https://example.test/a.jpg",
            playing=True,
            show_album_art=False,
        )
        self.assertNotIn("assets", activity)

    def test_cover_lokal_pakai_asset_cadangan(self):
        activity = build_activity(
            title="Lagu",
            album="Album",
            art_url="file:///tmp/.org.chromium.Chromium.N8UCOO",
            playing=True,
            show_album_art=True,
            fallback_asset="music",
        )
        self.assertEqual(activity["assets"]["large_image"], "music")
        self.assertEqual(activity["assets"]["large_text"], "Album")

    def test_asset_cadangan_tidak_menimpa_cover_http(self):
        activity = build_activity(
            title="Lagu",
            art_url="https://example.test/a.jpg",
            playing=True,
            show_album_art=True,
            fallback_asset="music",
        )
        self.assertEqual(
            activity["assets"]["large_image"], "https://example.test/a.jpg")

    def test_tanpa_cover_pakai_asset_cadangan(self):
        activity = build_activity(
            title="Lagu", playing=True, fallback_asset="music")
        self.assertEqual(activity["assets"]["large_image"], "music")

    def test_asset_cadangan_tidak_dipakai_saat_album_art_mati(self):
        activity = build_activity(
            title="Lagu",
            art_url="file:///tmp/a.png",
            playing=True,
            show_album_art=False,
            fallback_asset="music",
        )
        self.assertNotIn("assets", activity)

    def test_teks_dipotong_ke_batas_discord(self):
        activity = build_activity(title="x" * 500, artist="y" * 500, playing=True)
        self.assertLessEqual(len(activity["details"]), 128)
        self.assertLessEqual(len(activity["state"]), 128)


class SocketCandidateTests(unittest.TestCase):
    def test_override_env(self):
        old = os.environ.get("DISCORD_IPC_PATH")
        os.environ["DISCORD_IPC_PATH"] = "/tmp/discord-ipc-9"
        try:
            self.assertEqual(
                socket_candidates(), [Path("/tmp/discord-ipc-9")]
            )
        finally:
            if old is None:
                os.environ.pop("DISCORD_IPC_PATH", None)
            else:
                os.environ["DISCORD_IPC_PATH"] = old


class RateLimitTests(unittest.TestCase):
    def test_burst_dibatasi(self):
        presence = DiscordPresence("123456789012345678")
        sent: list[dict] = []
        presence._send_activity = lambda activity: sent.append(activity) or True

        for index in range(8):
            presence._send_if_changed({"details": f"Lagu {index}"})

        self.assertEqual(len(sent), RATE_LIMIT)
        self.assertEqual(sent[-1]["details"], f"Lagu {RATE_LIMIT - 1}")

    def test_jatah_pulih_setelah_jendela(self):
        presence = DiscordPresence("123456789012345678")
        sent: list[dict] = []
        presence._send_activity = lambda activity: sent.append(activity) or True

        for index in range(RATE_LIMIT + 1):
            presence._send_if_changed({"details": f"Lagu {index}"})
        self.assertEqual(len(sent), RATE_LIMIT)

        presence._tokens = 0.0
        presence._token_at = time.monotonic() - RATE_WINDOW
        presence._send_if_changed({"details": "Lagu berikutnya"})
        self.assertEqual(len(sent), RATE_LIMIT + 1)


class _FakeDiscord(threading.Thread):
    """Server IPC mini yang meniru handshake + SET_ACTIVITY Discord."""

    def __init__(self, path: Path) -> None:
        super().__init__(daemon=True)
        self.path = path
        self.ready = threading.Event()
        self.activity = threading.Event()
        self.payload: dict | None = None
        self.handshakes: list[str] = []
        # Meniru perilaku Discord: asset yang tidak dikenal dibuang dari
        # activity yang dikembalikan.
        self.known_assets: set[str] = set()
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(path))
        self._server.listen(4)
        self._conn: socket.socket | None = None
        self._closed = False

    def run(self) -> None:
        # Klien bisa menyambung ulang (mis. saat Application ID berganti),
        # jadi server harus menerima lebih dari satu koneksi.
        while not self._closed:
            try:
                conn, _addr = self._server.accept()
            except OSError:
                return
            self._conn = conn
            try:
                with conn:
                    self._serve(conn)
            except OSError:
                pass
            finally:
                self._conn = None

    def _serve(self, conn: socket.socket) -> None:
        while True:
            try:
                frame = self._read_frame(conn)
            except OSError:
                return
            if frame is None:
                return
            opcode, payload = frame
            if opcode == 0:
                self.handshakes.append(str(payload.get("client_id") or ""))
                conn.sendall(encode_frame(
                    1,
                    {"cmd": "DISPATCH", "evt": "READY",
                     "data": {"v": 1}},
                ))
                self.ready.set()
            elif opcode == 1 and payload.get("cmd") == "SET_ACTIVITY":
                self.payload = payload
                self.activity.set()
                activity = (payload.get("args") or {}).get("activity")
                echoed = dict(activity) if isinstance(activity, dict) else None
                if isinstance(echoed, dict):
                    assets = echoed.get("assets")
                    if isinstance(assets, dict):
                        image = str(assets.get("large_image") or "")
                        if image and image not in self.known_assets:
                            assets.pop("large_image", None)
                conn.sendall(encode_frame(1, {
                    "cmd": "SET_ACTIVITY",
                    "data": echoed,
                    "evt": None,
                    "nonce": payload.get("nonce"),
                }))

    @staticmethod
    def _read_frame(conn: socket.socket) -> tuple[int, dict] | None:
        header = _recv_exact(conn, 8)
        if header is None:
            return None
        opcode, length = struct.unpack("<ii", header)
        body = _recv_exact(conn, length) if length else b""
        if body is None:
            return None
        return opcode, json.loads(body.decode("utf-8") or "{}")

    def close(self) -> None:
        self._closed = True
        for sock in (self._conn, self._server):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass


def _recv_exact(conn: socket.socket, count: int) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < count:
        try:
            chunk = conn.recv(count - len(chunks))
        except OSError:
            return None
        if not chunk:
            return None
        chunks.extend(chunk)
    return bytes(chunks)


@unittest.skipUnless(hasattr(socket, "AF_UNIX"), "butuh Unix socket")
class PresenceTests(unittest.TestCase):
    def test_handshake_dan_kirim_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "discord-ipc-0"
            server = _FakeDiscord(path)
            server.start()
            old = os.environ.get("DISCORD_IPC_PATH")
            os.environ["DISCORD_IPC_PATH"] = str(path)
            presence = DiscordPresence("123456789012345678")
            try:
                presence.start()
                self.assertTrue(server.ready.wait(3.0))

                activity = build_activity(
                    title="Lagu Uji", artist="Artis", playing=True
                )
                presence.update(activity)
                self.assertTrue(server.activity.wait(3.0))

                self.assertEqual(
                    server.payload["args"]["activity"]["details"], "Lagu Uji"
                )
                self.assertEqual(server.payload["args"]["pid"], os.getpid())
                self.assertEqual(presence.status, "Terhubung ke Discord")
            finally:
                presence.stop()
                server.close()
                if old is None:
                    os.environ.pop("DISCORD_IPC_PATH", None)
                else:
                    os.environ["DISCORD_IPC_PATH"] = old

    def test_clear_mengirim_activity_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "discord-ipc-0"
            server = _FakeDiscord(path)
            server.start()
            old = os.environ.get("DISCORD_IPC_PATH")
            os.environ["DISCORD_IPC_PATH"] = str(path)
            presence = DiscordPresence("123456789012345678")
            try:
                presence.start()
                self.assertTrue(server.ready.wait(3.0))
                presence.update(build_activity(title="Lagu", playing=True))
                self.assertTrue(server.activity.wait(3.0))
                server.activity.clear()
                presence.clear()
                self.assertTrue(server.activity.wait(3.0))
                self.assertIsNone(server.payload["args"]["activity"])
            finally:
                presence.stop()
                server.close()
                if old is None:
                    os.environ.pop("DISCORD_IPC_PATH", None)
                else:
                    os.environ["DISCORD_IPC_PATH"] = old

    def test_stop_mengirim_clear(self):
        """Mematikan modul mengirim activity null sebelum socket ditutup."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "discord-ipc-0"
            server = _FakeDiscord(path)
            server.start()
            old = os.environ.get("DISCORD_IPC_PATH")
            os.environ["DISCORD_IPC_PATH"] = str(path)
            presence = DiscordPresence("123456789012345678")
            try:
                presence.start()
                self.assertTrue(server.ready.wait(3.0))
                presence.update(build_activity(title="Lagu", playing=True))
                self.assertTrue(server.activity.wait(3.0))
                presence.stop()

                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    payload = server.payload
                    if payload and payload["args"]["activity"] is None:
                        break
                    time.sleep(0.02)
                self.assertIsNone(server.payload["args"]["activity"])
            finally:
                presence.stop()
                server.close()
                if old is None:
                    os.environ.pop("DISCORD_IPC_PATH", None)
                else:
                    os.environ["DISCORD_IPC_PATH"] = old


    def test_ganti_app_id_menyambung_ulang(self):
        """Application ID per sumber: handshake ulang dengan client_id baru."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "discord-ipc-0"
            server = _FakeDiscord(path)
            server.start()
            old = os.environ.get("DISCORD_IPC_PATH")
            os.environ["DISCORD_IPC_PATH"] = str(path)
            presence = DiscordPresence("123456789012345678")
            try:
                presence.start()
                self.assertTrue(server.ready.wait(3.0))
                presence.update(build_activity(title="Lagu", playing=True))
                self.assertTrue(server.activity.wait(3.0))

                presence.set_app_id("987654321098765432")

                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    if len(server.handshakes) >= 2:
                        break
                    time.sleep(0.02)
                self.assertIn("987654321098765432", server.handshakes)
                self.assertEqual(server.handshakes[-1], "987654321098765432")
            finally:
                presence.stop()
                server.close()
                if old is None:
                    os.environ.pop("DISCORD_IPC_PATH", None)
                else:
                    os.environ["DISCORD_IPC_PATH"] = old

    def test_asset_hilang_dilaporkan_di_status(self):
        """Asset yang tidak ada di portal tidak boleh diam-diam jadi "?"."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "discord-ipc-0"
            server = _FakeDiscord(path)
            server.known_assets = set()
            server.start()
            old = os.environ.get("DISCORD_IPC_PATH")
            os.environ["DISCORD_IPC_PATH"] = str(path)
            presence = DiscordPresence("123456789012345678")
            try:
                presence.start()
                self.assertTrue(server.ready.wait(3.0))
                presence.update(build_activity(
                    title="Lagu",
                    art_url="file:///tmp/.org.chromium.Chromium.x",
                    playing=True,
                    fallback_asset="musik_salah",
                ))
                self.assertTrue(server.activity.wait(3.0))
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    if "tidak ada di portal" in presence.status:
                        break
                    time.sleep(0.02)
                self.assertIn("tidak ada di portal", presence.status)
                self.assertIn("musik_salah", presence.status)
            finally:
                presence.stop()
                server.close()
                if old is None:
                    os.environ.pop("DISCORD_IPC_PATH", None)
                else:
                    os.environ["DISCORD_IPC_PATH"] = old

    def test_asset_valid_tetap_terhubung(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "discord-ipc-0"
            server = _FakeDiscord(path)
            server.known_assets = {"music"}
            server.start()
            old = os.environ.get("DISCORD_IPC_PATH")
            os.environ["DISCORD_IPC_PATH"] = str(path)
            presence = DiscordPresence("123456789012345678")
            try:
                presence.start()
                self.assertTrue(server.ready.wait(3.0))
                presence.update(build_activity(
                    title="Lagu",
                    art_url="file:///tmp/a.png",
                    playing=True,
                    fallback_asset="music",
                ))
                self.assertTrue(server.activity.wait(3.0))
                time.sleep(0.3)
                self.assertEqual(presence.status, "Terhubung ke Discord")
            finally:
                presence.stop()
                server.close()
                if old is None:
                    os.environ.pop("DISCORD_IPC_PATH", None)
                else:
                    os.environ["DISCORD_IPC_PATH"] = old

if __name__ == "__main__":
    unittest.main()
