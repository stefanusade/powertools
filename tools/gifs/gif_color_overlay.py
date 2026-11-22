"""GIF: overlay color picker — loupe mengikuti kursor, klik memilih warna."""

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

from gifkit import Scene, FRAME_ROOT, encode_gif, frames_dir, setup_env

setup_env("pt-gif-picker-")

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from powertools import ui
from powertools.color import ColorOverlay

OUT = frames_dir("picker")
GIF = REPO / "docs" / "gifs" / "color-picker-overlay.gif"
SHOT = FRAME_ROOT / "wallpaper.png"
SHOT_COPY = FRAME_ROOT / "picker-shot.png"
WIDTH, HEIGHT = 1000, 640

app = Gtk.Application(application_id="io.github.powertools.GifPicker")
PICKED = {}

# Rute pointer: (waktu, x, y). Diinterpolasi tiap frame supaya gerakannya
# mulus tanpa bergantung pada kecepatan render.
ROUTE = [
    (0.4, 380, 190),    # langit atas
    (1.6, 700, 300),    # gradasi tengah
    (2.8, 690, 322),    # tepi bulan
    (4.0, 620, 300),    # bulan
    (5.0, 210, 420),    # bukit gelap
    (6.0, 480, 520),    # bayangan bawah
    (7.0, 640, 315),    # kembali ke bulan
]


def make_wallpaper(path: Path, width: int = 1200, height: int = 760) -> Path:
    """Latar bergaya desktop untuk uji picker (bukan tangkapan layar)."""
    from PIL import Image, ImageDraw, ImageFilter

    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)

    # Langit senja: gradasi dari biru tua ke jingga.
    top = (24, 32, 58)
    mid = (94, 78, 122)
    low = (236, 138, 92)
    for y in range(height):
        t = y / height
        if t < 0.62:
            f = t / 0.62
            color = tuple(int(top[i] + (mid[i] - top[i]) * f) for i in range(3))
        else:
            f = (t - 0.62) / 0.38
            color = tuple(int(mid[i] + (low[i] - mid[i]) * f) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)

    # Bulan.
    draw.ellipse([760, 330, 900, 470], fill=(255, 214, 150))
    # Siluet bukit.
    draw.polygon(
        [(0, 560), (220, 380), (420, 560), (520, 470), (760, 620), (0, 760)],
        fill=(28, 30, 44),
    )
    draw.polygon(
        [(420, 620), (700, 430), (980, 620), (1200, 520), (1200, 760), (420, 760)],
        fill=(19, 21, 32),
    )
    # Air: pantulan lembut.
    water = img.crop((0, 600, width, height)).filter(ImageFilter.GaussianBlur(3))
    img.paste(water, (0, 640))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 700, width, height], fill=(16, 18, 28))

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def on_activate(application):
    ui.install_css()

    def on_done(rgb):
        PICKED["rgb"] = rgb

    make_wallpaper(SHOT)
    # _finish() menghapus berkas tangkapan; pakai salinan supaya wallpaper
    # sumber tetap ada untuk rekaman berikutnya.
    shutil.copyfile(SHOT, SHOT_COPY)
    window = ColorOverlay(application, SHOT_COPY, on_done)
    # Overlay asli fullscreen; untuk rekaman cukup seukuran "layar" uji supaya
    # gambarnya tidak berletterbox dan detail piksel tetap besar.
    window.show_overlay = lambda: window.present()
    # _finish() menutup overlay; untuk rekaman jendela dibiarkan hidup
    # beberapa frame lagi supaya momen kliknya ikut terekam.
    window.destroy = lambda *args, **kwargs: None
    window.set_default_size(WIDTH, HEIGHT)
    window.present()

    def pointer_at(now: float) -> tuple[int, int]:
        if now <= ROUTE[0][0]:
            return ROUTE[0][1], ROUTE[0][2]
        for index in range(len(ROUTE) - 1):
            t0, x0, y0 = ROUTE[index]
            t1, x1, y1 = ROUTE[index + 1]
            if t0 <= now <= t1:
                f = (now - t0) / max(1e-6, t1 - t0)
                # halus di awal dan akhir tiap segmen
                f = f * f * (3 - 2 * f)
                return int(x0 + (x1 - x0) * f), int(y0 + (y1 - y0) * f)
        return ROUTE[-1][1], ROUTE[-1][2]

    scene = Scene(window, OUT, seconds=8.0, fps=12)
    scene.on_frame(lambda now: window._update(*pointer_at(now)))
    scene.at(7.4, lambda: window._on_click(None, 1, *pointer_at(7.4)))

    def done():
        gif = encode_gif(OUT, GIF, fps=10, width=720, colors=128)
        print("gif:", gif, gif.stat().st_size // 1024, "KB", flush=True)
        print("dipilih:", PICKED.get("rgb"), flush=True)
        application.quit()

    scene.start(on_done=done)


app.connect("activate", on_activate)
app.run([])
