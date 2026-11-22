"""GIF: jendela Pengaturan — pindah tab per modul, ubah satu opsi."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

from gifkit import Scene, encode_gif, frames_dir, pin_size, setup_env

setup_env("pt-gif-settings-")

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from powertools import config, ui
from powertools.ui_settings import SettingsWindow

OUT = frames_dir("settings")
GIF = REPO / "docs" / "gifs" / "settings.gif"
WIDTH, HEIGHT = 860, 690

app = Gtk.Application(application_id="io.github.powertools.GifSettings")


def on_activate(application):
    ui.install_css()
    cfg = config.Config()
    for module in config.MODULES:
        cfg.set(f"modules.{module}", True)
    cfg.save()

    window = SettingsWindow(application, cfg)
    pin_size(window, WIDTH, HEIGHT)
    window.show_window()

    scene = Scene(window, OUT, seconds=12.4, fps=12)

    def go(page):
        return lambda: window._select_page(page)

    def scroll_to(page_name, fraction):
        def run():
            page = window._stack.get_child_by_name(page_name)
            if page is None:
                return
            adj = page.get_vadjustment()
            span = max(0.0, adj.get_upper() - adj.get_page_size())
            adj.set_value(span * fraction)

        return run

    scene.at(1.2, go("clipboard"))
    scene.at(2.6, go("launcher"))
    scene.at(4.0, go("color"))
    scene.at(5.4, go("lyrics"))
    # Slider kepekatan ada di bawah tab; gulir dulu supaya perubahannya
    # benar-benar terlihat di GIF.
    scene.at(6.2, scroll_to("lyrics", 1.0))

    scale = getattr(window, "_scale_lyrics_opacity", None)

    def set_opacity(value):
        return lambda: scale.set_value(value)

    for index, value in enumerate(range(94, 34, -4)):
        scene.at(6.8 + index * 0.1, set_opacity(value))
    for index, value in enumerate(range(38, 100, 4)):
        scene.at(8.4 + index * 0.1, set_opacity(value))

    def toggle_pin():
        switch = getattr(window, "_sw_lyrics_pinned", None)
        if switch is not None:
            switch.set_active(not switch.get_active())

    scene.at(9.9, scroll_to("lyrics", 0.0))
    scene.at(10.3, toggle_pin)
    scene.at(10.9, go("discord"))
    scene.at(11.9, go("general"))

    def done():
        gif = encode_gif(OUT, GIF, fps=10, width=680, colors=128)
        print("gif:", gif, gif.stat().st_size // 1024, "KB", flush=True)
        application.quit()

    scene.start(on_done=done)


app.connect("activate", on_activate)
app.run([])
