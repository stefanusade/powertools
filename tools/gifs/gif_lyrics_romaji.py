"""GIF: lirik mengambang — NMIXX "Crescendo", Romaji <-> Hangul.

Lirik lagu ini murni Hangul dan tidak ada unggahan romaji di LRCLIB, jadi
PowerTools menyiapkan versi Latin lewat transliterasi Hangul lokal. Yang
diperlihatkan: lirik dalam huruf Latin, tombol ``Romaji`` ditekan → Hangul
asli, lalu ditekan lagi → Latin. Posisi baca tidak berubah karena jumlah
barisnya sama; syair tetap berjalan seperti biasa.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

from gifkit import Scene, encode_gif, frames_dir, setup_env

setup_env("pt-gif-lyrics-romaji-")

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from powertools import config, lyrics, ui
from powertools.ui_lyrics import LyricsWindow

OUT = frames_dir("lyrics-romaji")
GIF = REPO / "docs" / "gifs" / "lyrics-romaji.gif"

TITLE = "Crescendo"
ARTIST = "NMIXX"
LENGTH = 166.0

# Kutipan enam baris pertama dari lirik sinkron NMIXX "Crescendo" (rekaman
# LRCLIB id 33813966, 2:46). Yang direkam hanya bagian ini, jadi tidak perlu
# seluruh lagu disalin ke repo. Rekaman LRCLIB itu tidak punya unggahan
# romaji, jadi versi Latin di sini berasal dari transliterasi Hangul lokal —
# persis jalur yang dipakai PowerTools untuk lagu ini.
LINES = [
    (15.47, "안아 줘 no question, head to toe enveloped"),
    (19.82, "사라지는 배경, 단둘인 우주"),
    (23.63, "별빛은 silent choir, 네 짙은 눈에 잠겨"),
    (28.43, "날 괴롭히던 long, long day, 다 잊어버려 어느새"),
    (33.48, "너 말곤 decrescendo"),
    (35.75, "꿈처럼 속삭여 줘"),
]


class FakeTrack:
    key = "gif|nmixx|crescendo"
    title = TITLE
    artist = ARTIST
    album = ""
    length_us = int(LENGTH * 1_000_000)
    playing = True
    source = "spotify"


app = Gtk.Application(application_id="io.github.powertools.GifLyricsRomaji")

# Mulai dari baris pertama. Kalau rekaman dimulai di tengah lagu, daftar
# langsung tergulir ke baris aktif dan baris sebelumnya terpotong di tepi atas
# — terlihat seperti gambar rusak di frame pertama GIF.
STATE = {"pos": 15.47}


class FakeService:
    """Bagian PowerToolsApp yang dipakai jendela lirik."""

    def __init__(self, window):
        self.window = window
        self.calls: list[bool] = []

    def set_lyrics_romanize(self, enabled):
        # Sama dengan aplikasi sungguhan: pilihan disimpan ke config supaya
        # lagu berikutnya langsung tampil dalam mode yang sama.
        self.calls.append(bool(enabled))


def on_activate(application):
    ui.install_css()
    cfg = config.Config()
    cfg.set("lyrics.romanize", True)

    window = LyricsWindow(application, cfg)
    service = FakeService(window)
    window._app = service

    track = FakeTrack()
    window.set_track(track)
    result = lyrics.Lyrics(
        track=TITLE,
        artist=ARTIST,
        duration=LENGTH,
        synced=[lyrics.LyricLine(when, text) for when, text in LINES],
        source="lrclib.net",
    )
    # ``results`` kosong = sudah dicari di LRCLIB dan tidak ada unggahan
    # romaji, jadi yang dipakai transliterasi lokal. Tidak ada permintaan
    # jaringan saat merekam.
    lyrics._attach_romaji(result, [], TITLE, ARTIST, LENGTH)
    # Jalur yang sama dengan hasil pencarian sungguhan: simpan ke cache
    # memori, matikan spinner, lalu render.
    window._apply_lyrics(window._fetch_token, track.key, result)
    window.show_window(focus=False)
    window.set_position(STATE["pos"], True)

    button = window._romaji_button
    state = {"last": 0.0}

    def advance(now: float):
        """Posisi lagu mengikuti jam rekaman, bukan jam dinding."""
        delta = now - state["last"]
        state["last"] = now
        STATE["pos"] += delta
        window.set_position(STATE["pos"], True)

    def hover():
        button.set_state_flags(Gtk.StateFlags.PRELIGHT, False)

    def leave():
        button.unset_state_flags(Gtk.StateFlags.PRELIGHT)

    def click():
        button.set_active(not button.get_active())

    scene = Scene(window, OUT, seconds=10.0, fps=12)
    scene.on_frame(advance)

    # Latin -> Hangul, lalu Hangul -> Latin. Dua arah, karena tombolnya
    # memang tombol bolak-balik, bukan pilihan sekali pakai.
    scene.at(2.6, hover)
    scene.at(2.8, click)
    scene.at(3.1, leave)
    scene.at(7.4, hover)
    scene.at(7.6, click)
    scene.at(7.9, leave)

    def done():
        gif = encode_gif(OUT, GIF, fps=10, width=600, colors=128)
        print("gif:", gif, gif.stat().st_size // 1024, "KB", flush=True)
        print("set_lyrics_romanize:", service.calls, flush=True)
        application.quit()

    scene.start(on_done=done)


app.connect("activate", on_activate)
app.run([])
