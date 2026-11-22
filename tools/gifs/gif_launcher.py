"""GIF: launcher (Alt+Space) — kalkulator, aplikasi, aksi."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

from gifkit import Scene, encode_gif, frames_dir, setup_env, type_text

setup_env("pt-gif-launcher-")

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from powertools import config, ui
from powertools.apps import UsageStore, load_apps
from powertools.store import ClipStore
from powertools.ui_launcher import LauncherWindow

OUT = frames_dir("launcher")
GIF = REPO / "docs" / "gifs" / "launcher.gif"

app = Gtk.Application(application_id="io.github.powertools.GifLauncher")


class Shim:
    def window_hidden(self, _window):
        pass


def on_activate(application):
    ui.install_css()
    cfg = config.Config()
    apps = load_apps()
    usage = UsageStore()
    store = ClipStore(images=False)
    window = LauncherWindow(application, usage, cfg, store)
    window._app = Shim()
    window.set_apps(apps)
    window.show_window()
    entry = window._entry

    # Jendela launcher menyesuaikan tinggi dengan jumlah hasil. Untuk GIF,
    # tinggi dipatok supaya kanvas tidak berubah-ubah antar frame.
    original_refresh = window._refresh

    def fixed_refresh(*args, **kwargs):
        original_refresh(*args, **kwargs)
        window._scroller.set_min_content_height(5 * 54 + 6)

    window._refresh = fixed_refresh
    window._refresh()

    scene = Scene(window, OUT, seconds=7.4, fps=12)
    # Kalkulator: hasil muncul di baris pertama.
    type_text(entry, "12*8+4", 0.6, 0.13, scene)
    scene.at(2.0, lambda: window._move_selection(1))
    scene.at(2.8, lambda: entry.set_text(""))
    # Pencarian aplikasi sungguhan.
    type_text(entry, "set", 3.2, 0.14, scene)
    scene.at(4.2, lambda: window._move_selection(1))
    scene.at(4.9, lambda: entry.set_text(""))
    type_text(entry, "lirik", 5.2, 0.13, scene)
    scene.at(6.3, lambda: window._move_selection(1))

    def done():
        gif = encode_gif(OUT, GIF, fps=10, width=640, colors=128)
        print("gif:", gif, gif.stat().st_size // 1024, "KB", flush=True)
        application.quit()

    scene.start(on_done=done)


app.connect("activate", on_activate)
app.run([])
