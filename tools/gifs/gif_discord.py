"""GIF: tab Discord Listening — pilih preset tanpa mengetik Application ID."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

from gifkit import Scene, encode_gif, frames_dir, pin_size, setup_env

setup_env("pt-gif-discord-")

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from powertools import config, mpris, ui
from powertools.ui_settings import SettingsWindow

OUT = frames_dir("discord")
GIF = REPO / "docs" / "gifs" / "discord.gif"
WIDTH, HEIGHT = 860, 760

# Sumber tiruan supaya GIF tidak menampilkan lagu yang sedang diputar di mesin
# ini; bentuknya sama dengan hasil mpris.list_sources().
SOURCES = [
    mpris.SourceInfo("spotify", "Lampu Kota", "Senja Kecil", True,
                     "org.mpris.MediaPlayer2.spotify"),
    mpris.SourceInfo("youtube", "Konser Malam", "Senja Kecil", True,
                     "org.mpris.MediaPlayer2.chromium"),
    mpris.SourceInfo("youtube-music", "Peron", "Senja Kecil", False,
                     "org.mpris.MediaPlayer2.chromium"),
    mpris.SourceInfo("vlc", "Rekaman Konser", "", True,
                     "org.mpris.MediaPlayer2.vlc"),
]

app = Gtk.Application(application_id="io.github.powertools.GifDiscord")


def on_activate(application):
    ui.install_css()
    cfg = config.Config()
    cfg.set("modules.discord", True)
    cfg.set("discord.app_ids", {})
    cfg.save()

    window = SettingsWindow(application, cfg)
    pin_size(window, WIDTH, HEIGHT)
    window.show_window()

    # Ganti pemindaian D-Bus dengan daftar tetap, supaya isi GIF dapat diulang
    # dan tidak memuat judul lagu dari sesi pengguna.
    def fake_detected(force: bool = False) -> None:
        window._discord_detected = list(SOURCES)
        window._discord_detected_loaded = True
        window._rebuild_discord_sources()

    window._refresh_discord_detected = fake_detected

    scene = Scene(window, OUT, seconds=9.6, fps=12)
    scene.at(1.0, lambda: window._select_page("discord"))

    def pick(source, index):
        def run():
            dropdown = window._discord_dropdowns.get(source)
            if dropdown is not None:
                dropdown.set_selected(index)

        return run

    # 0=Otomatis, 1=PowerTools, 2=YouTube Music, 3=Spotify, 4=Netflix,
    # 5=YouTube.
    scene.at(2.2, pick("youtube-music", 2))
    scene.at(3.0, pick("spotify", 3))
    # YouTube → "YouTube": kata kerjanya jadi "Watching".
    scene.at(4.4, pick("youtube", 5))
    # VLC → preset umum
    scene.at(5.8, pick("vlc", 1))
    # "Sumber lain" → tidak dikirim ke Discord
    scene.at(7.0, pick("default", 1))
    # Kembali ke Otomatis supaya terlihat pilihannya bisa diganti lagi.
    scene.at(8.2, pick("default", 0))
    scene.at(8.4, pick("spotify", 0))
    scene.at(8.6, lambda: window._select_page("general"))
    scene.at(9.2, lambda: window._select_page("discord"))

    def done():
        gif = encode_gif(OUT, GIF, fps=10, width=680, colors=128)
        print("gif:", gif, gif.stat().st_size // 1024, "KB", flush=True)
        application.quit()

    scene.start(on_done=done)


app.connect("activate", on_activate)
app.run([])
