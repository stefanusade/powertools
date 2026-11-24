"""Perekam GIF headless untuk jendela GTK PowerTools.

Sesi desktop tidak selalu mengizinkan screencast (portal menolak, D-Bus Shell
ditutup), jadi tiap frame dirender langsung dari widget dengan
``Gsk.CairoRenderer`` — sama seperti tangkapan statis di ``docs/``. Hasilnya
urutan PNG, lalu Pillow menggabungnya menjadi GIF berpalet.

Pemakaian:

    from gifkit import Scene, encode_gif, frames_dir
    scene = Scene(window, frames_dir("clipboard"), seconds=6.0)
    scene.at(0.6, lambda: entry.set_text("git"))
    scene.start(on_done=lambda: encode_gif(...))
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")

from gi.repository import GLib, Gsk, Gtk

#: Semua rekaman menulis PNG ke satu tempat di direktori sementara, supaya
#: repo tidak kotor dan sisa rekaman mudah dibersihkan.
FRAME_ROOT = Path(tempfile.gettempdir()) / "powertools-gifs"


def frames_dir(name: str) -> Path:
    """Direktori frame untuk satu rekaman (dibersihkan oleh ``Scene.start``)."""
    return FRAME_ROOT / name


class Scene:
    """Rekam satu jendela selama ``seconds`` detik pada ``fps`` frame/detik.

    Waktu yang dipakai adalah waktu maya: tiap frame selalu maju tepat
    ``1/fps`` detik, berapa pun lama render sebenarnya. Tanpa ini, jendela
    besar (color picker 1200x760) butuh ~150 ms per frame sehingga rekaman
    kehilangan frame dan gerakannya jadi patah-patah saat diputar.
    """

    def __init__(
        self,
        window: Gtk.Window,
        outdir: str | Path,
        seconds: float = 6.0,
        fps: int = 12,
    ) -> None:
        self.window = window
        self.outdir = Path(outdir)
        self.seconds = seconds
        self.fps = fps
        self._actions: list[tuple[float, int, object]] = []
        self._frame_hooks: list[object] = []
        self._seq = 0
        self._frame = 0
        self._renderer = Gsk.CairoRenderer.new()
        self._renderer.realize()
        self._on_done = None

    def at(self, when: float, action) -> "Scene":
        self._seq += 1
        self._actions.append((when, self._seq, action))
        self._actions.sort(key=lambda item: (item[0], item[1]))
        return self

    def on_frame(self, callback) -> "Scene":
        """Panggil ``callback(waktu_maya)`` pada setiap frame sebelum render.

        Dipakai untuk animasi yang harus mengikuti jam rekaman (mis. posisi
        lagu pada jendela lirik), bukan jam dinding.
        """
        self._frame_hooks.append(callback)
        return self

    def now(self) -> float:
        """Waktu maya rekaman, dalam detik."""
        return self._frame / float(self.fps)

    def start(self, on_done=None, warmup: float = 0.4) -> None:
        self._on_done = on_done
        shutil.rmtree(self.outdir, ignore_errors=True)
        self.outdir.mkdir(parents=True, exist_ok=True)
        # Jendela baru benar-benar ter-layout beberapa putaran loop setelah
        # present(); frame pertama sebelum itu kosong, jadi rekaman ditunda.
        GLib.timeout_add(max(1, int(warmup * 1000)), self._begin)

    def _begin(self) -> bool:
        # Satu frame per putaran loop, tanpa timer: urutan render tetap
        # deterministik dan tidak ada frame yang terlewat.
        GLib.idle_add(self._apply)
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------ frame
    def _apply(self) -> bool:
        """Terapkan aksi/hook untuk frame ini, lalu beri GTK satu putaran.

        Snapshot tepat sesudah widget diubah menghasilkan node kosong: GTK
        menandai widget perlu di-layout ulang dan baru bisa digambar setelah
        putaran loop berikutnya. Karena itu render dipisah ke ``_capture_tick``.
        """
        now = self.now()

        while self._actions and self._actions[0][0] <= now:
            _when, _seq, action = self._actions.pop(0)
            try:
                action()
            except Exception as exc:  # jangan hentikan rekaman karena satu aksi
                print("aksi gagal:", exc, flush=True)

        for hook in self._frame_hooks:
            try:
                hook(now)
            except Exception as exc:
                print("hook gagal:", exc, flush=True)

        GLib.timeout_add(1, self._capture_tick)
        return GLib.SOURCE_REMOVE

    def _capture_tick(self, attempt: int = 1) -> bool:
        # Snapshot bisa kosong walau jendela sudah tampil: GTK menandai widget
        # perlu layout ulang sesudah CSS/animasi berubah, dan pada putaran itu
        # snapshot belum menghasilkan apa pun. Dicoba lagi beberapa kali
        # sebelum menyerah, supaya frame tidak hilang (gerakan tetap mulus).
        if self._capture():
            self._frame += 1
        elif attempt < 30:
            GLib.timeout_add(1, self._capture_tick, attempt + 1)
            return GLib.SOURCE_REMOVE
        else:
            self._frame += 1

        if self.now() >= self.seconds:
            # Renderer harus dilepas sebelum proses keluar; kalau tidak,
            # gsk_renderer_dispose gagal dan Python berhenti dengan SIGABRT.
            try:
                self._renderer.unrealize()
            except Exception:
                pass
            if self._on_done is not None:
                self._on_done()
            return GLib.SOURCE_REMOVE
        GLib.idle_add(self._apply)
        return GLib.SOURCE_REMOVE

    def _capture(self) -> bool:
        """Render satu frame ke PNG. False kalau widget belum siap digambar."""
        width = self.window.get_width()
        height = self.window.get_height()
        if width <= 0 or height <= 0:
            return False
        paintable = Gtk.WidgetPaintable.new(self.window)
        snapshot = Gtk.Snapshot.new()
        paintable.snapshot(snapshot, width, height)
        node = snapshot.to_node()
        if node is None:
            return False
        texture = self._renderer.render_texture(node)
        path = self.outdir / f"frame_{self._frame + 1:04d}.png"
        return bool(texture.save_to_png(str(path)))


def encode_gif(
    frames: str | Path,
    output: str | Path,
    fps: int = 12,
    width: int = 640,
    colors: int = 128,
    loop: int = 0,
    source_fps: int = 12,
    octree: bool = False,
) -> Path:
    """Gabung urutan PNG menjadi GIF berpalet.

    Pillow dipakai, bukan ffmpeg: untuk UI yang sebagian besar diam, encoder
    ffmpeg menghasilkan berkas 4-5x lebih besar (tiap frame ditulis penuh).
    Palet dibuat sekali dari beberapa frame lalu dipakai semua frame, supaya
    warna teks tidak berkedip antar frame.

    ``octree`` memakai kuantisasi oktree, yang mempertahankan warna pekat
    berarea kecil (mis. titik hijau penanda "sedang berbunyi" di tab island).
    Kuantisasi median-cut membuang warna seperti itu karena jumlah pikselnya
    kalah jauh dari latar gelap.
    """
    from PIL import Image

    frames = Path(frames)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    paths = sorted(frames.glob("frame_*.png"))
    if not paths:
        raise RuntimeError(f"tidak ada frame di {frames}")
    # Pilih frame berdasarkan waktu, bukan dengan pembulatan langkah: 12 → 10
    # fps harus membuang 1 dari 6 frame, bukan memakai semuanya (kalau tidak,
    # GIF berjalan 20% lebih lambat dari aslinya).
    if fps < source_fps:
        total = len(paths)
        count = max(1, int(round(total * fps / source_fps)))
        paths = [
            paths[min(total - 1, int(round(index * source_fps / fps)))]
            for index in range(count)
        ]

    images = []
    for path in paths:
        img = Image.open(path).convert("RGB")
        if width and img.width != width:
            height = max(2, round(img.height * width / img.width))
            img = img.resize((width, height), Image.LANCZOS)
        images.append(img)

    # Jendela bisa berubah tinggi selama rekaman (mis. daftar hasil launcher
    # menyusut). Semua frame disamakan ke kanvas terbesar supaya GIF tidak
    # ditolak; latar pengisi diambil dari sudut kiri atas frame itu sendiri,
    # yang di UI ini adalah margin gelap di luar kartu.
    canvas_w = max(img.width for img in images)
    canvas_h = max(img.height for img in images)
    if any(img.size != (canvas_w, canvas_h) for img in images):
        framed = []
        for img in images:
            if img.size == (canvas_w, canvas_h):
                framed.append(img)
                continue
            canvas = Image.new("RGB", (canvas_w, canvas_h), img.getpixel((0, 0)))
            canvas.paste(img, (0, 0))
            framed.append(canvas)
        images = framed

    # Palet global: gabungkan sampel dari beberapa frame, lalu quantize sekali.
    samples = images[:: max(1, len(images) // 8)][:8]
    strip = Image.new("RGB", (samples[0].width, samples[0].height * len(samples)))
    for index, img in enumerate(samples):
        strip.paste(img, (0, index * img.height))
    method = Image.FASTOCTREE if octree else Image.MEDIANCUT
    palette = strip.quantize(colors=colors, method=method)
    # Tanpa dithering: UI ini berisi bidang warna rata dan teks kecil, jadi
    # pola titik justru mengotori teks dan membuat GIF 4-6x lebih besar.
    paletted = [img.quantize(palette=palette, dither=Image.NONE) for img in images]

    duration = max(20, int(round(1000 / fps)))
    paletted[0].save(
        output,
        save_all=True,
        append_images=paletted[1:],
        optimize=True,
        duration=duration,
        loop=loop,
        disposal=1,
    )
    return output


def type_text(widget, text: str, start: float, step: float = 0.07, scene=None):
    """Jadwalkan pengetikan bertahap ke sebuah Gtk.Entry."""
    for index in range(1, len(text) + 1):
        scene.at(
            start + (index - 1) * step,
            lambda value=text[:index]: widget.set_text(value),
        )


def pin_size(window, width: int, height: int) -> None:
    """Patok ukuran jendela selama rekaman.

    Jendela Pengaturan menyesuaikan tinggi dengan isi tab; kalau dibiarkan,
    kanvas GIF berubah-ubah dan gerakannya terlihat patah.
    """
    window._fit_to_content = lambda: False
    window.set_default_size(width, height)
    window.set_size_request(width, height)


def setup_env(prefix: str = "pt-gif-") -> Path:
    """Arahkan config/data/cache ke direktori sementara sebelum import paket."""
    base = Path(tempfile.mkdtemp(prefix=prefix))
    os.environ["POWERTOOLS_CONFIG_DIR"] = str(base / "config")
    os.environ["POWERTOOLS_DATA_DIR"] = str(base / "data")
    os.environ["POWERTOOLS_CACHE_DIR"] = str(base / "cache")
    os.environ.setdefault("POWERTOOLS_THEME", "dark")
    return base
