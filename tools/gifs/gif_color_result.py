"""GIF: jendela hasil color picker — warna berganti, salin HEX lalu HSL/CMYK."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

from gifkit import Scene, encode_gif, frames_dir, setup_env

setup_env("pt-gif-color-")

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from powertools import config, ui
from powertools.store import ClipStore
from powertools.ui_color import ColorResultWindow

OUT = frames_dir("color")
GIF = REPO / "docs" / "gifs" / "color-picker.gif"

COLORS = [
    (56, 132, 255),
    (255, 99, 71),
    (46, 204, 113),
    (155, 89, 182),
]

app = Gtk.Application(application_id="io.github.powertools.GifColor")


class Shim:
    def window_hidden(self, _window):
        pass


def on_activate(application):
    ui.install_css()
    cfg = config.Config()
    store = ClipStore(images=False)
    window = ColorResultWindow(application, store, cfg)
    window._app = Shim()
    window.show_color(COLORS[0])
    window._flash("HEX disalin: #3884ff")

    scene = Scene(window, OUT, seconds=7.6, fps=12)
    scene.at(1.2, lambda: window.show_color(COLORS[1]))
    scene.at(2.6, lambda: window.show_color(COLORS[2]))
    scene.at(4.0, lambda: window.show_color(COLORS[3]))
    scene.at(5.2, lambda: window.show_color((255, 184, 0)))
    # Tombol "Salin" di baris HSL dan CMYK: teks status muncul di footer.
    scene.at(6.2, lambda: window._on_copy_format(None, "hsl"))
    scene.at(6.9, lambda: window._on_copy_format(None, "cmyk"))

    def done():
        gif = encode_gif(OUT, GIF, fps=10, width=680, colors=160)
        print("gif:", gif, gif.stat().st_size // 1024, "KB", flush=True)
        application.quit()

    scene.start(on_done=done)


app.connect("activate", on_activate)
app.run([])
