"""Rekam dynamic island di GNOME Shell bersarang (nested).

Island adalah widget GNOME Shell (St/Clutter), bukan jendela GTK, jadi tidak
bisa dirender lewat Gtk.WidgetPaintable seperti GIF lain. Satu-satunya cara:
rekam layar. Supaya rekaman tidak memuat apa pun milik pengguna, shell dijalankan
**bersarang** di layar 800×600 dengan dua pemutar MPRIS palsu (``mpris_fake``):
tidak ada aplikasi, berkas, judul asli, atau isi desktop yang ikut terekam.

Shell bersarang juga menjamin kode ekstensi yang direkam adalah yang baru
dipasang, karena shell biasa menyimpan modul ekstensi sampai logout/login.

Skrip ini menyalakan dirinya sendiri di dalam ``dbus-run-session``, jadi bus
D-Bus-nya terpisah dari sesi pengguna: pemutar palsu tidak pernah muncul di
island asli, dan nama ``org.gnome.Shell.Screencast`` tidak bentrok dengan shell
yang sedang dipakai.

Yang diperlihatkan: island muncul saat pemutar pertama berbunyi (satu tab,
tanpa baris tab), tab bertambah begitu pemutar kedua muncul, lalu klik tab
Spotify mengembalikan tampilan ke pemutar pertama — walaupun VLC yang terakhir
berbunyi. Pilihan tab menempel, tidak mengikuti pemutar terakhir.

Butuh GNOME Shell, gjs, dbus-run-session, dan ffmpeg. Jalankan ``./install.sh``
lebih dulu supaya salinan ekstensi yang dipakai shell bersarang sudah terbaru.
"""

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("POWERTOOLS_REPO", HERE.parents[1]))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

from gifkit import FRAME_ROOT, encode_gif, frames_dir

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

OUT = frames_dir("island")
GIF = REPO / "docs" / "gifs" / "island.gif"
COVER = FRAME_ROOT / "island-cover.png"
SHELL_LOG = FRAME_ROOT / "island-shell.log"
CAST = FRAME_ROOT / "island.mp4"

BUS = "org.gnome.Shell.Screencast"
PATH = "/org/gnome/Shell/Screencast"
IFACE = "org.gnome.Shell.Screencast"

RD_BUS = "org.gnome.Mutter.RemoteDesktop"
RD_PATH = "/org/gnome/Mutter/RemoteDesktop"
RD_IFACE = "org.gnome.Mutter.RemoteDesktop"
RD_SESSION = "org.gnome.Mutter.RemoteDesktop.Session"

#: Layar shell bersarang. Island selalu di tengah tepi atas.
SCREEN_W, SCREEN_H = 800, 600
ISLAND_W = 384
ISLAND_X = (SCREEN_W - ISLAND_W) // 2

#: Cadangan kalau potongan otomatis gagal: selebar island plus margin.
FALLBACK_CROP = (ISLAND_X - 12, 0, ISLAND_W + 24, 236)

#: Wilayah yang diperiksa saat mencari batas island. Island selalu di tengah
#: tepi atas; di luar wilayah ini hanya ada pointer dan penanda workspace yang
#: ikut berubah dan akan melebarkan potongan tanpa perlu.
SEARCH = (ISLAND_X - 80, 0, ISLAND_W + 160, 320)

#: Lama rekaman (detik) dan titik-titik penting di dalamnya. Frame pertama
#: sengaja dibiarkan tanpa island: frame itu jadi acuan latar untuk memotong.
SECONDS = 12.0
AT_FIRST_PLAYER = 1.2
AT_SECOND_PLAYER = 3.4
AT_HOLD = 4.9
AT_CLICK_TAB = 8.4

#: Tab yang muncul, berurutan sesuai urutan pemutar MPRIS.
TABS = ("Spotify", "VLC")


def ensure_private_bus() -> None:
    """Jalankan ulang skrip ini di bus D-Bus sendiri."""
    if os.environ.get("POWERTOOLS_GIF_BUS") == "1":
        return
    env = dict(os.environ, POWERTOOLS_GIF_BUS="1")
    os.execvpe(
        "dbus-run-session",
        ["dbus-run-session", "--", sys.executable,
         str(Path(__file__).resolve()), *sys.argv[1:]],
        env,
    )


def tab_width(label: str) -> int:
    """Lebar tab, sama seperti ``_tabWidth`` di media.js."""
    return min(150, max(62, 26 + len(label) * 7))


def make_cover(path: Path) -> None:
    """Sampul album tiruan: malam kota dengan dua sumber cahaya."""
    from PIL import Image, ImageDraw, ImageFilter

    size = 320
    img = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(img)
    for y in range(size):
        f = y / size
        draw.line([(0, y), (size, y)], fill=(
            int(28 + (86 - 28) * f),
            int(34 + (54 - 34) * f),
            int(62 + (108 - 62) * f),
        ))

    glow = Image.new("RGB", (size, size), (0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.ellipse([70, 120, 250, 300], fill=(255, 176, 92))
    gdraw.ellipse([150, 40, 250, 140], fill=(255, 214, 150))
    glow = glow.filter(ImageFilter.GaussianBlur(34))
    img = Image.blend(img, Image.blend(img, glow, 0.55), 0.75)

    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 258, size, size], fill=(14, 16, 26))
    for x in range(0, size, 34):
        height = 26 + (x * 7) % 44
        draw.rectangle([x + 4, 258 - height, x + 30, 258], fill=(19, 22, 34))

    img.save(path)


def read_log() -> str:
    try:
        return SHELL_LOG.read_text(errors="replace")
    except OSError:
        return ""


class Nested:
    """GNOME Shell bersarang beserta sesi RemoteDesktop untuk menggerakkan pointer."""

    def __init__(self) -> None:
        self.log = SHELL_LOG.open("w")
        self.process = subprocess.Popen(
            ["gnome-shell", "--nested", "--wayland"],
            env=dict(os.environ, POWERTOOLS_MEDIA_DEBUG="1"),
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        self.players: list[subprocess.Popen] = []
        self._remote = None
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    # ---------------------------------------------------------------- D-Bus
    def wait_name(self, name: str, timeout: float = 40.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                owned = self._bus.call_sync(
                    "org.freedesktop.DBus", "/org/freedesktop/DBus",
                    "org.freedesktop.DBus", "NameHasOwner",
                    GLib.Variant("(s)", (name,)), GLib.VariantType.new("(b)"),
                    Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
                if owned:
                    return True
            except GLib.Error:
                pass
            time.sleep(0.3)
        return False

    def _session(self) -> str:
        if self._remote:
            return self._remote
        result = self._bus.call_sync(
            RD_BUS, RD_PATH, RD_IFACE, "CreateSession", None, None,
            Gio.DBusCallFlags.NONE, 4000, None)
        path = result.unpack()[0]
        self._call(path, "Start", None)
        self._remote = path
        return path

    def _call(self, path: str, method: str, args) -> None:
        self._bus.call_sync(RD_BUS, path, RD_SESSION, method, args, None,
                            Gio.DBusCallFlags.NONE, 4000, None)

    # -------------------------------------------------------------- pointer
    def pointer(self) -> tuple[int, int] | None:
        """Posisi pointer terakhir dari log ekstensi (mode log detail)."""
        marker = "[powertools-media] pointer "
        for line in reversed(read_log().splitlines()):
            if marker in line:
                try:
                    _, rest = line.split(marker, 1)
                    x, y = rest.split()[:2]
                    return int(float(x)), int(float(y))
                except ValueError:
                    return None
        return None

    def wait_pointer(self, timeout: float = 20.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.pointer() is not None:
                return True
            time.sleep(0.3)
        return False

    def move_to(self, x: int, y: int, tries: int = 8) -> None:
        """Geser pointer relatif sampai posisinya terbaca di target.

        Gerakan relatif di shell bersarang tidak dijepit ke tepi layar, jadi
        selisihnya dihitung dari posisi yang benar-benar terbaca di log.
        """
        session = self._session()
        for _ in range(tries):
            current = self.pointer()
            if current is None:
                time.sleep(0.2)
                continue
            dx, dy = x - current[0], y - current[1]
            if abs(dx) < 2 and abs(dy) < 2:
                return
            self._call(session, "NotifyPointerMotionRelative",
                       GLib.Variant("(dd)", (float(dx), float(dy))))
            time.sleep(0.35)

    def click(self) -> None:
        session = self._session()
        self._call(session, "NotifyPointerButton",
                   GLib.Variant("(ib)", (272, True)))
        time.sleep(0.09)
        self._call(session, "NotifyPointerButton",
                   GLib.Variant("(ib)", (272, False)))

    # -------------------------------------------------------------- pemutar
    def spawn(self, bus_name: str, titles: str, artist: str, album: str) -> None:
        self.players.append(subprocess.Popen(
            [shutil.which("gjs"), "-m", str(HERE / "mpris_fake.js"),
             "--bus", bus_name, "--titles", titles, "--artist", artist,
             "--album", album, "--cover", str(COVER),
             "--interval", "60", "--duration", str(SECONDS + 6)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ))

    # --------------------------------------------------------------- rekaman
    def start_cast(self) -> str:
        result = self._bus.call_sync(
            BUS, PATH, IFACE, "Screencast",
            GLib.Variant("(sa{sv})", (str(CAST)[:-4], {
                # Rekaman bawaan hanya ~7 fps dan banyak frame kembar, jadi
                # gerakan pointer terlihat patah. Minta 30 fps; compositor
                # tetap mengirim frame seperlunya.
                "framerate": GLib.Variant("i", 30),
            })),
            None, Gio.DBusCallFlags.NONE, 5000, None)
        ok, filename = result.unpack()
        if not ok:
            raise RuntimeError("GNOME Shell menolak memulai rekaman")
        return filename

    def stop_cast(self) -> None:
        try:
            self._bus.call_sync(BUS, PATH, IFACE, "StopScreencast", None, None,
                                Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error:
            pass

    def close(self) -> None:
        self.stop_cast()
        for process in self.players:
            process.terminate()
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.log.close()

    # --------------------------------------------------------------- tata letak
    def island_y(self) -> int:
        """Tepi atas island: 0 kalau panel GNOME di bawah, setinggi panel kalau di atas."""
        marker = "[powertools-media] jangkar "
        for line in reversed(read_log().splitlines()):
            if marker in line:
                try:
                    _, rest = line.split(marker, 1)
                    return int(float(rest.split()[1]))
                except (ValueError, IndexError):
                    return 0
        return 0


def choreography(shell: Nested) -> None:
    """Urutan aksi selama rekaman; waktu relatif terhadap awal rekaman."""
    started = time.monotonic()

    def at(seconds: float) -> None:
        delay = seconds - (time.monotonic() - started)
        if delay > 0:
            time.sleep(delay)

    # Tepi atas island dibaca ulang setiap kali: panel GNOME bisa berpindah
    # (atas ↔ bawah) selama shell bersarang hidup, dan posisi island ikut
    # berpindah. Angka yang dibaca sekali di awal bisa sudah basi.
    #
    # Pointer ditahan di pojok kanan baris tab: masih di dalam kartu (island
    # tidak menutup) tetapi di atas bidang kosong, bukan di atas kontrol atau
    # progress bar.
    def hold_point() -> tuple[int, int]:
        return ISLAND_X + ISLAND_W - 44, shell.island_y() + 14

    def tab_centre(index: int) -> tuple[int, int]:
        x = ISLAND_X + 16
        for label in TABS[:index]:
            x += tab_width(label) + 6
        return x + tab_width(TABS[index]) // 2, shell.island_y() + 26

    at(AT_FIRST_PLAYER)
    shell.spawn("spotify", "Lampu Kota|Peron Kosong", "Senja Kecil", "Peron")

    at(AT_SECOND_PLAYER)
    shell.spawn("vlc", "Rekaman Konser|Panggung Kecil", "Paduan Suara",
                "Rekaman")

    # Pointer ditahan di dalam kartu: selama di dalam, hitungan tutup ditahan
    # sehingga island tidak hilang di tengah rekaman.
    at(AT_HOLD)
    shell.move_to(*hold_point())

    # Klik tab Spotify: island kembali ke pemutar pertama, padahal VLC yang
    # terakhir berbunyi. Inilah inti tab: pilihan menempel, bukan mengikuti
    # pemutar yang terakhir diputar.
    at(AT_CLICK_TAB)
    shell.move_to(*tab_centre(0))
    shell.click()
    at(AT_CLICK_TAB + 0.6)
    shell.move_to(*hold_point())


def video_seconds() -> float:
    """Lama rekaman menurut ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "format=duration", "-of", "default=nw=1:nk=1",
         str(CAST)],
        capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def mask_of(a: Path, b: Path):
    """Topeng piksel yang berubah lebih dari 24 pada salah satu kanal.

    Selisih diambil per kanal, bukan lewat luminance: kartu island berwarna
    abu-abu gelap di atas wallpaper biru gelap, dan selisih seperti itu nyaris
    hilang kalau dirata-ratakan sebagai kecerahan (tepi atas kartu ikut
    terpotong).
    """
    from PIL import Image, ImageChops

    diff = ImageChops.difference(
        Image.open(a).convert("RGB"), Image.open(b).convert("RGB"))
    red, green, blue = diff.split()
    peak = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return peak.point(lambda value: 255 if value > 24 else 0)


def count_mask(mask) -> int:
    return mask.histogram()[255]


def mask_box(mask, sx: int, sy: int, sw: int, sh: int):
    """Kotak area island dari topeng gabungan.

    Batas dicari dengan menghitung piksel berubah per baris/kolom: bayangan
    kartu dan pointer yang lewat hanya menyentuh beberapa piksel, jadi ambang
    kecil sudah cukup membuangnya tanpa memotong tepi kartu.
    """
    width, height = mask.size
    minimum = 8

    rows = [y for y in range(height)
            if mask.crop((0, y, width, y + 1)).histogram()[255] >= minimum]
    cols = [x for x in range(width)
            if mask.crop((x, 0, x + 1, height)).histogram()[255] >= minimum]
    if not rows or not cols:
        return None

    left = sx + cols[0] - 8
    top = sy + rows[0] - 6
    right = sx + cols[-1] + 9
    bottom = sy + rows[-1] + 7
    left, top = max(0, left), max(0, top)
    right, bottom = min(SCREEN_W, right), min(SCREEN_H, bottom)
    if right - left < ISLAND_W * 0.6 or bottom - top < 60:
        return None
    return left, top, right - left, bottom - top


def stable_background(paths: list[Path]) -> int | None:
    """Indeks frame latar yang tenang: wallpaper sudah tergambar, island belum.

    Latar dicari dari pasangan frame yang hampir sama tepat **sebelum** island
    pertama kali muncul. Kalau dicari dari frame paling awal yang tenang, yang
    didapat bisa frame sesudah island tampil (wallpaper baru selesai digambar
    belakangan), dan seluruh fase satu-tab di awal rekaman ikut terbuang.
    """
    limit = len(paths) - 1
    changes = [count_mask(mask_of(paths[i], paths[i + 1]))
               for i in range(limit)]

    appeared = next((i for i, value in enumerate(changes) if value >= 2000), None)
    if appeared is None:
        return None

    for index in range(appeared - 1, 0, -1):
        if changes[index] < 800:
            return index
    return appeared - 1 if appeared > 0 else None


def frames_from_video() -> int:
    """Pecah rekaman jadi PNG 12 fps, dipotong ke area island.

    Frame pembuka sebelum island muncul dibuang: GIF yang diawali latar kosong
    terlihat seperti gambar rusak di daftar README. Animasi muncul yang masih
    setengah transparan juga dilewati, supaya GIF mulai dari kartu yang utuh.
    """
    from PIL import Image, ImageChops

    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)

    raw = FRAME_ROOT / "island-raw"
    shutil.rmtree(raw, ignore_errors=True)
    raw.mkdir(parents=True, exist_ok=True)
    # Frame diambil apa adanya (tanpa filter fps): rekaman compositor hanya
    # ~7 fps, dan menaikkannya ke 12 fps cuma menggandakan frame kembar
    # sehingga GIF terasa tersendat.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(CAST),
         "-fps_mode", "passthrough", str(raw / "frame_%04d.png")],
        check=True,
    )

    paths = sorted(raw.glob("frame_*.png"))
    if not paths:
        return 0

    # Latar: wallpaper tanpa island. Isi kartu hanya menutupi sebagian kecil
    # layar, jadi kotak gabungan semua perubahan = area island.
    background = stable_background(paths)
    box = None
    start = 0
    if background is not None:
        base = paths[background]
        sx, sy, sw, sh = SEARCH
        union = None
        for index in range(background + 1, len(paths)):
            mask = mask_of(paths[index], base).crop(
                (sx, sy, sx + sw, sy + sh))
            if count_mask(mask) < 2000:
                continue
            if start == 0:
                start = index
            union = mask if union is None else ImageChops.lighter(union, mask)
        if union is not None:
            box = mask_box(union, sx, sy, sw, sh)

    if box is None:
        box = FALLBACK_CROP
        start = 0

    x, y, width, height = box
    print("potong:", box, flush=True)
    for path in paths:
        Image.open(path).crop((x, y, x + width, y + height)).save(
            OUT / path.name)

    frames = sorted(OUT.glob("frame_*.png"))[start:]
    for path in sorted(OUT.glob("frame_*.png"))[:start]:
        path.unlink()

    total = width * height

    # Lewati frame animasi muncul yang masih berubah banyak ke frame
    # berikutnya (kartu masih tembus pandang).
    while len(frames) > 2 and \
            count_mask(mask_of(frames[0], frames[1])) > total * 0.03:
        frames.pop(0).unlink()

    # Buang ekor animasi menutup: island memudar sendiri setelah beberapa
    # detik, dan GIF yang berakhir separuh transparan terlihat seperti rusak.
    while len(frames) > 2 and \
            count_mask(mask_of(frames[-2], frames[-1])) > total * 0.03:
        frames.pop().unlink()

    return len(frames)


def main() -> int:
    ensure_private_bus()
    make_cover(COVER)
    CAST.unlink(missing_ok=True)

    shell = Nested()
    try:
        if not shell.wait_name("org.gnome.Shell"):
            print("shell bersarang tidak siap", flush=True)
            return 1
        shell.wait_name("org.gnome.Shell.Screencast", 15)
        if not shell.wait_pointer():
            print("posisi pointer tidak terbaca dari log ekstensi", flush=True)
            return 1
        # Tunggu ekstensi selesai memasang island sebelum merekam.
        time.sleep(1.5)

        print("merekam:", shell.start_cast(), flush=True)
        worker = threading.Thread(target=choreography, args=(shell,),
                                  daemon=True)
        worker.start()
        worker.join(timeout=SECONDS + 5)
        time.sleep(0.6)
    finally:
        shell.close()

    # Shell menutup pipeline rekaman secara asinkron; tunggu berkas lengkap.
    for _ in range(40):
        if CAST.exists() and CAST.stat().st_size > 2000:
            break
        time.sleep(0.25)

    count = frames_from_video()
    print("frame:", count, flush=True)
    if count == 0:
        print("rekaman kosong", flush=True)
        return 1
    from PIL import Image

    first = sorted(OUT.glob("frame_*.png"))[0]
    # GIF diberi kecepatan sesuai rekaman, bukan angka tetap: compositor
    # bersarang memberi ~7 fps, jadi memutar 10 fps membuat gerakannya kacau.
    seconds = video_seconds()
    fps = max(5, min(12, round(count / seconds))) if seconds else 10
    gif = encode_gif(OUT, GIF, fps=fps, source_fps=fps,
                     width=Image.open(first).width, colors=128, octree=True)
    print("gif:", gif, gif.stat().st_size // 1024, "KB", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
