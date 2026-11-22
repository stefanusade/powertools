"""GIF: lirik mengambang — gulir otomatis, hover, klik dua kali = lompat."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

from gifkit import Scene, encode_gif, frames_dir, setup_env

setup_env("pt-gif-lyrics-")

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from powertools import config, lyrics, ui
from powertools.ui_lyrics import LyricsWindow

OUT = frames_dir("lyrics")
GIF = REPO / "docs" / "gifs" / "lyrics.gif"

# Lagu fiktif supaya GIF tidak memuat lirik berhak cipta.
LINES = [
    (1.0, "lampu kota masih menyala"),
    (4.0, "jendela kereta membawa pulang"),
    (7.0, "aku hitung tiang dan lampu"),
    (10.0, "satu per satu lewat"),
    (13.0, "kita pernah berjanji di sini"),
    (16.0, "di bangku kayu dekat peron"),
    (19.0, "sebelum kereta terakhir"),
    (22.0, "membawa pulang namamu"),
    (25.0, "dan sekarang hujan turun lagi"),
    (28.0, "membasuh jejak sepatu"),
    (31.0, "aku masih menunggu"),
    (34.0, "di tempat yang sama"),
    (37.0, "sampai lampu kota padam"),
    (40.0, "sampai suaraku habis"),
    (43.0, "kembalilah sebentar saja"),
    (46.0, "aku hanya ingin tahu"),
    (49.0, "apakah kamu juga ingat"),
    (52.0, "peron yang sama"),
]


class FakeTrack:
    key = "gif|lampu-kota|senja-kecil"
    title = "Lampu Kota"
    artist = "Senja Kecil"
    album = "Peron"
    length_us = 200_000_000
    playing = True
    source = "spotify"


app = Gtk.Application(application_id="io.github.powertools.GifLyrics")

STATE = {"pos": 11.0}


class FakeService:
    """Bagian PowerToolsApp yang dipakai jendela lirik."""

    def __init__(self, window):
        self.window = window
        self.seeks: list[float] = []

    def seek_lyrics(self, seconds):
        self.seeks.append(seconds)
        STATE["pos"] = seconds
        self.window.set_position(seconds, True)
        return True


def on_activate(application):
    ui.install_css()
    cfg = config.Config()
    window = LyricsWindow(application, cfg)
    service = FakeService(window)
    window._app = service

    window.set_track(FakeTrack())
    window._lyrics = lyrics.Lyrics(
        track="Lampu Kota",
        artist="Senja Kecil",
        synced=[lyrics.LyricLine(when, text) for when, text in LINES],
        source="lrclib",
    )
    window._render_lyrics(window._lyrics)
    window.show_window(focus=False)
    window.set_position(STATE["pos"], True)

    def press_double(row):
        for index in range(row.observe_controllers().get_n_items()):
            item = row.observe_controllers().get_item(index)
            if isinstance(item, Gtk.GestureClick):
                item.emit("pressed", 2, 8.0, 8.0)

    target = window._rows[6]
    state = {"frozen": False, "last": 0.0}

    def advance(now: float):
        """Posisi lagu mengikuti jam rekaman, bukan jam dinding."""
        delta = now - state["last"]
        state["last"] = now
        if not state["frozen"]:
            STATE["pos"] += delta
        window.set_position(STATE["pos"], True)

    def jump():
        # Tahan sebentar di baris tujuan supaya lompatannya terbaca.
        state["frozen"] = True
        press_double(target)

    scene = Scene(window, OUT, seconds=9.6, fps=12)
    scene.on_frame(advance)
    # Pointer berhenti di baris jauh: baris itu menyala (hover).
    scene.at(3.4, lambda: target.set_state_flags(Gtk.StateFlags.PRELIGHT, False))
    scene.at(4.4, jump)
    scene.at(4.7, lambda: target.unset_state_flags(Gtk.StateFlags.PRELIGHT))
    scene.at(6.4, lambda: state.update(frozen=False))

    def done():
        gif = encode_gif(OUT, GIF, fps=10, width=600, colors=128)
        print("gif:", gif, gif.stat().st_size // 1024, "KB", flush=True)
        print("seek:", service.seeks, flush=True)
        application.quit()

    scene.start(on_done=done)


app.connect("activate", on_activate)
app.run([])
