"""GIF: riwayat clipboard (Super+V) — cari, pilih, pin, cari lagi."""

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

from gifkit import Scene, encode_gif, frames_dir, setup_env, type_text

setup_env("pt-gif-clipboard-")

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from powertools import config, ui
from powertools.store import ClipStore
from powertools.ui_clipboard import ClipboardWindow

OUT = frames_dir("clipboard")
GIF = REPO / "docs" / "gifs" / "clipboard.gif"

# Isi contoh yang realistis; disimpan langsung ke store supaya GIF tidak
# menampilkan riwayat clipboard pengguna yang menjalankan skrip ini.
CLIPS = [
    "git rebase -i HEAD~3",
    "npm run build && npm run preview",
    "const total = items.reduce((sum, item) => sum + item.price, 0);",
    "sudo systemctl restart powertools.service",
    "Terima kasih, file proposalnya sudah saya kirim lewat email pagi ini.",
    "https://lrclib.net/api/search?track_name=Hype+Boy",
    "ssh -p 2222 deploy@10.0.0.14 'docker compose up -d'",
    "SELECT title, artist FROM tracks WHERE played_at > now() - interval '7 days';",
    "Kunci API baru sudah dirotasi, versi lama dimatikan Jumat lalu.",
    "ffmpeg -i in.mp4 -vf scale=1280:-2 -c:v libx264 -crf 22 out.mp4",
]

now = time.time()
store = ClipStore(images=False)
for index, text in enumerate(CLIPS):
    store.append(text, ts=now - (index + 1) * 137)
store.toggle_pin(store.load()[3])

app = Gtk.Application(application_id="io.github.powertools.GifClipboard")


class Shim:
    def window_hidden(self, _window):
        pass


def on_activate(application):
    ui.install_css()
    cfg = config.Config()
    window = ClipboardWindow(application, store, cfg)
    window._app = Shim()
    window.show_window()
    entry = window._entry

    scene = Scene(window, OUT, seconds=7.2, fps=12)
    type_text(entry, "git", 0.5, 0.12, scene)
    scene.at(1.5, lambda: window._move_selection(1))
    scene.at(2.2, lambda: entry.set_text(""))
    scene.at(2.9, lambda: window._list.select_row(window._rows[0]))
    scene.at(3.4, window._toggle_pin_selected)
    scene.at(4.2, lambda: entry.set_text(""))
    type_text(entry, "npm", 4.5, 0.12, scene)
    scene.at(5.6, lambda: entry.set_text(""))
    scene.at(5.9, lambda: window._move_selection(2))

    def done():
        gif = encode_gif(OUT, GIF, fps=10, width=640, colors=128)
        print("gif:", gif, gif.stat().st_size // 1024, "KB", flush=True)
        application.quit()

    scene.start(on_done=done)


app.connect("activate", on_activate)
app.run([])
