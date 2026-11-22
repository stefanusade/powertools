"""Rekam dynamic island lewat org.gnome.Shell.Screencast (GNOME Shell).

Island adalah widget GNOME Shell (St/Clutter), bukan jendela GTK, jadi tidak
bisa dirender lewat Gtk.WidgetPaintable seperti GIF lain. Satu-satunya cara:
rekam area layar tempat island berada. Supaya tidak ada jendela lain yang ikut
terlihat, skrip pindah ke workspace kosong dan memasang latar sementara yang
netral; keduanya dipulihkan setelah rekaman.

Yang diperlihatkan: island muncul saat pemutar baru terdeteksi, chip pemilih
pemutar bertambah, klik chip mengganti pemutar aktif, lalu island menutup
sendiri dan muncul lagi saat pointer berhenti di tepi atas.

Butuh GNOME Shell (bukan X11/DE lain), gjs, dan ffmpeg. Skrip mengasumsikan
panel GNOME tidak di atas layar (mis. Zorin), sehingga island menempel di tepi
atas; kalau panel ada di atas, island turun sepanjang panel dan koordinat klik
perlu digeser.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("POWERTOOLS_REPO", HERE.parents[1]))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

from gifkit import FRAME_ROOT, encode_gif, frames_dir

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")

from gi.repository import Gdk, Gio, GLib

OUT = frames_dir("island")
GIF = REPO / "docs" / "gifs" / "island.gif"
CAST = FRAME_ROOT / "island.mp4"
COVER = FRAME_ROOT / "island-cover.png"

# Area rekam: selebar island (384 px) plus margin, setinggi sampai ujung bawah
# kartu (kartu tertinggi berakhir di ~124 px). Titik (0,0) ada di tengah tepi
# atas layar.
CAPTURE_W, CAPTURE_H = 520, 136
SECONDS = 14.5

BUS = "org.gnome.Shell.Screencast"
PATH = "/org/gnome/Shell/Screencast"
IFACE = "org.gnome.Shell.Screencast"

RD_BUS = "org.gnome.Mutter.RemoteDesktop"
RD_PATH = "/org/gnome/Mutter/RemoteDesktop"
RD_IFACE = "org.gnome.Mutter.RemoteDesktop"
RD_SESSION = "org.gnome.Mutter.RemoteDesktop.Session"

# Keysym X11 untuk tombol yang dipakai menggerakkan workspace.
CTRL = 0xffe3
ALT = 0xffe9
LEFT = 0xff51
RIGHT = 0xff53

# Batas aman perpindahan workspace; GNOME biasanya hanya butuh satu-dua.
MAX_WORKSPACE_STEPS = 6

WALLPAPER_KEYS = (
    "org.gnome.desktop.background picture-uri",
    "org.gnome.desktop.background picture-uri-dark",
)


def screen_size():
    """Ukuran monitor utama; island selalu di tengah tepi atas layar."""
    monitor = Gdk.Display.get_default().get_monitors().get_item(0)
    geometry = monitor.get_geometry()
    return geometry.width, geometry.height


SCREEN_W, SCREEN_H = screen_size()
CENTER_X = SCREEN_W // 2

# Titik penting, relatif terhadap island 384 px di tengah layar. Syaratnya
# panel GNOME tidak di atas layar (seperti Zorin), jadi island menempel di
# tepi atas; kalau panel di atas, island turun sepanjang panel.
CHIP_SPOTIFY = (CENTER_X - 124, 104)   # chip "Spotify" di dalam island
HOT_SPOT = (CENTER_X, 18)              # pita hover di tepi atas
AWAY = (CENTER_X - 660, SCREEN_H - 460)   # jauh dari island

state = {"players": []}


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


def make_backdrop() -> tuple:
    """Latar netral seukuran layar, supaya tidak ada isi desktop pengguna.

    Bagian atas sengaja lebih terang daripada kartu island yang hampir hitam;
    dengan latar gelap, tepi kartu nyaris tidak terlihat di GIF.

    Nama berkas memuat hash isinya: GNOME meng-cache wallpaper per URI, jadi
    menimpa berkas yang sama tidak akan memperbarui tampilan.

    Mengembalikan (path, warna_piksel_tengah) — warna tengah dipakai untuk
    memeriksa apakah desktop benar-benar tampil sebelum merekam.
    """
    import hashlib

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (SCREEN_W, SCREEN_H))
    draw = ImageDraw.Draw(img)
    top = (104, 118, 152)
    bottom = (26, 32, 52)
    for y in range(SCREEN_H):
        f = min(1.0, y / (SCREEN_H * 0.45))
        draw.line([(0, y), (SCREEN_W, y)], fill=tuple(
            int(top[i] + (bottom[i] - top[i]) * f) for i in range(3)))

    raw = img.tobytes()
    digest = hashlib.sha1(raw).hexdigest()[:10]
    path = FRAME_ROOT / f"island-backdrop-{digest}.png"
    img.save(path)
    return path, img.getpixel((SCREEN_W // 2, SCREEN_H // 2))


# ------------------------------------------------------------------ pointer
class Remote:
    """Pointer dan keyboard lewat org.gnome.Mutter.RemoteDesktop.

    Jalur ini tidak memunculkan dialog izin portal, jadi rekaman bisa berjalan
    tanpa interaksi.
    """

    def __init__(self) -> None:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        result = bus.call_sync(RD_BUS, RD_PATH, RD_IFACE, "CreateSession",
                               None, None, Gio.DBusCallFlags.NONE, 4000, None)
        self.session = result.unpack()[0]
        self._call("Start")
        self._corner()

    def _call(self, method, args=None) -> None:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync(RD_BUS, self.session, RD_SESSION, method, args, None,
                      Gio.DBusCallFlags.NONE, 4000, None)

    def _corner(self) -> None:
        # Gerakan relatif yang besar dijepit ke tepi layar, jadi sesudah ini
        # posisi pointer pasti (0,0).
        self._call("NotifyPointerMotionRelative",
                   GLib.Variant("(dd)", (-20000, -20000)))

    def move_to(self, x: int, y: int) -> None:
        self._corner()
        self._call("NotifyPointerMotionRelative",
                   GLib.Variant("(dd)", (float(x), float(y))))

    def click(self) -> None:
        self._call("NotifyPointerButton", GLib.Variant("(ib)", (272, True)))
        time.sleep(0.08)
        self._call("NotifyPointerButton", GLib.Variant("(ib)", (272, False)))

    def tap_combo(self, *modifiers: int, key: int) -> None:
        """Tekan beberapa modifier sekaligus, lalu satu tombol."""
        self._tap([(mod, True) for mod in modifiers], key,
                  [(mod, False) for mod in reversed(modifiers)])

    def _tap(self, before, key: int, after) -> None:
        for keysym, down in before:
            self._call("NotifyKeyboardKeysym",
                       GLib.Variant("(ub)", (keysym, down)))
            time.sleep(0.05)
        self._call("NotifyKeyboardKeysym", GLib.Variant("(ub)", (key, True)))
        time.sleep(0.05)
        self._call("NotifyKeyboardKeysym", GLib.Variant("(ub)", (key, False)))
        time.sleep(0.05)
        for keysym, down in after:
            self._call("NotifyKeyboardKeysym",
                       GLib.Variant("(ub)", (keysym, down)))
            time.sleep(0.05)

    def stop(self) -> None:
        try:
            self._call("Stop")
        except Exception:
            pass


# -------------------------------------------------------------- lingkungan
# Nilai asli disimpan di berkas sebelum diubah. Kalau proses mati di tengah
# jalan (mis. rekaman gagal), latar dan jendela pengguna tetap bisa
# dikembalikan pada jalankan berikutnya.
SAVED = FRAME_ROOT / "island-saved.json"


def gsettings_get(key: str) -> str:
    return subprocess.run(["gsettings", "get", *key.split()],
                          capture_output=True, text=True).stdout.strip()


def gsettings_set(key: str, value: str) -> None:
    subprocess.run(["gsettings", "set", *key.split(), value], check=True)


def set_wallpaper(uri: str) -> dict:
    """Pasang latar sementara; kembalikan nilai lama untuk dipulihkan."""
    old = {key: gsettings_get(key) for key in WALLPAPER_KEYS}
    for key in WALLPAPER_KEYS:
        gsettings_set(key, uri)
    return old


def restore_wallpaper(old: dict) -> None:
    for key, value in old.items():
        gsettings_set(key, value)


def save_state(state_: dict) -> None:
    import json

    SAVED.write_text(json.dumps(state_))


def load_state() -> dict:
    import json

    try:
        return json.loads(SAVED.read_text())
    except (OSError, ValueError):
        return {}


def recover_previous_run(remote) -> None:
    """Kembalikan latar dan workspace kalau jalankan sebelumnya terhenti."""
    saved = load_state()
    if not saved:
        return
    if saved.get("wallpaper"):
        print("memulihkan latar dari jalankan sebelumnya", flush=True)
        restore_wallpaper(saved["wallpaper"])
    for _ in range(int(saved.get("steps") or 0)):
        # Workspace direkam dari kanan, jadi dikembalikan dengan ke kiri.
        print("kembali ke workspace semula", flush=True)
        remote.tap_combo(CTRL, ALT, key=LEFT)
    SAVED.unlink(missing_ok=True)


def close_notifications() -> None:
    """Tutup banner notifikasi yang masih tampil.

    Banner GNOME menutupi area yang direkam (tepi atas tengah layar), dan
    gsettings show-banners tidak selalu dihormati daemon notifikasi Zorin.
    """
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    for nid in range(1, 200):
        try:
            bus.call_sync("org.freedesktop.Notifications",
                          "/org/freedesktop/Notifications",
                          "org.freedesktop.Notifications", "CloseNotification",
                          GLib.Variant("(u)", (nid,)), None,
                          Gio.DBusCallFlags.NONE, 300, None)
        except Exception:
            pass


# ------------------------------------------------------------------ rekaman
def start_cast(bus) -> str:
    result = bus.call_sync(
        BUS, PATH, IFACE, "ScreencastArea",
        GLib.Variant("(iiiisa{sv})", (
            CENTER_X - CAPTURE_W // 2, 0, CAPTURE_W, CAPTURE_H,
            str(CAST)[:-4], {},
        )),
        None, Gio.DBusCallFlags.NONE, 5000, None,
    )
    success, filename = result.unpack()
    if not success:
        raise RuntimeError("GNOME Shell menolak memulai rekaman")
    return filename


def stop_cast(bus) -> None:
    bus.call_sync(BUS, PATH, IFACE, "StopScreencast", None,
                  None, Gio.DBusCallFlags.NONE, 5000, None)


def sample_centre(bus):
    """Warna piksel di tengah layar, untuk memeriksa workspace sudah kosong.

    Titik tengah dipakai karena di sana hanya ada latar: ikon desktop menumpuk
    di kiri dan dock di bawah. Rekaman singkat 32×32 cukup untuk satu piksel,
    dan lebih kecil dari itu encoder Shell menolak.
    """
    probe = FRAME_ROOT / "island-probe"
    for stale in FRAME_ROOT.glob("island-probe*"):
        stale.unlink()

    try:
        result = bus.call_sync(
            BUS, PATH, IFACE, "ScreencastArea",
            GLib.Variant("(iiiisa{sv})", (
                SCREEN_W // 2, SCREEN_H // 2, 32, 32, str(probe), {},
            )),
            None, Gio.DBusCallFlags.NONE, 5000, None,
        )
    except GLib.Error:
        # Area terlalu kecil untuk encoder, atau rekaman lain masih jalan.
        return None
    success, filename = result.unpack()
    if not success:
        return None
    time.sleep(0.6)
    stop_cast(bus)
    time.sleep(0.4)

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", filename, "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True,
    ).stdout
    if len(raw) < 3:
        return None
    return tuple(raw[:3])


def ensure_empty_workspace(bus, remote, expected, wallpaper: dict) -> bool:
    """Pindah ke workspace kosong supaya tidak ada jendela yang ikut terekam.

    Workspace kosong dipakai, bukan `Super+D`: tombol itu bersifat bolak-balik
    dan meninggalkan desktop dalam keadaan "show desktop" kalau ditekan dua
    kali. Perpindahan workspace juga bisa dihitung, jadi `cleanup` tahu persis
    berapa langkah harus kembali.

    Jumlah langkah disimpan di `state["steps"]` dan di berkas, supaya jalankan
    yang mati mendadak tetap bisa dipulihkan.
    """
    for step in range(1, MAX_WORKSPACE_STEPS + 1):
        colour = sample_centre(bus)
        if colour and max(abs(a - b) for a, b in zip(colour, expected)) < 24:
            return True
        print("workspace masih berisi jendela, geser kanan:", colour, flush=True)
        remote.tap_combo(CTRL, ALT, key=RIGHT)
        state["steps"] = step
        save_state({"wallpaper": wallpaper, "steps": step})
        time.sleep(1.2)
    return False


def spawn_player(bus_name: str, titles: str, artist: str, album: str) -> None:
    common = [shutil.which("gjs"), "-m", str(HERE / "mpris_fake.js")]
    state["players"].append(subprocess.Popen(
        common + ["--bus", bus_name, "--titles", titles,
                  "--artist", artist, "--album", album,
                  "--cover", str(COVER), "--interval", "30",
                  "--duration", str(SECONDS + 4)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ))


def frames_from_video() -> int:
    """Pecah rekaman jadi PNG 12 fps (Pillow tidak membaca video).

    Frame pembuka sebelum island muncul dibuang: GIF yang diawali latar kosong
    terlihat seperti gambar rusak di daftar README. Animasi muncul yang masih
    setengah transparan juga dilewati, supaya GIF mulai dari kartu yang utuh.
    """
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(CAST),
         "-vf", "fps=12", str(OUT / "frame_%04d.png")],
        check=True,
    )
    paths = sorted(OUT.glob("frame_*.png"))
    if not paths:
        return 0

    from PIL import Image, ImageChops

    first = Image.open(paths[0]).convert("RGB")
    total = first.width * first.height

    def changed(path) -> int:
        diff = ImageChops.difference(first, Image.open(path).convert("RGB"))
        return sum(1 for value in diff.convert("L").getdata() if value > 24)

    def delta(a, b) -> int:
        diff = ImageChops.difference(
            Image.open(a).convert("RGB"), Image.open(b).convert("RGB"))
        return sum(1 for value in diff.convert("L").getdata() if value > 24)

    # Kartu island mengubah puluhan persen piksel, sedangkan pointer yang
    # bergerak hanya <1%. Ambang 3% melewati frame latar kosong.
    start = 0
    for index, path in enumerate(paths):
        if changed(path) > total * 0.03:
            start = index
            break

    # Lewati frame animasi muncul yang masih berubah banyak ke frame
    # berikutnya (kartu masih tembus pandang).
    while start + 1 < len(paths) and delta(paths[start], paths[start + 1]) > total * 0.03:
        start += 1

    for path in paths[:start]:
        path.unlink()
    return len(paths) - start


def main() -> int:
    make_cover(COVER)
    backdrop, backdrop_colour = make_backdrop()

    loop = GLib.MainLoop()
    remote = Remote()
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    # Jalankan sebelumnya mungkin terhenti sebelum sempat memulihkan apa pun.
    recover_previous_run(remote)

    old_wallpaper = set_wallpaper(f"file://{backdrop}")
    save_state({"wallpaper": old_wallpaper, "steps": 0})
    state["done"] = False

    def cleanup() -> None:
        """Pulihkan desktop: kembali ke workspace semula, latar asli terpasang."""
        if state["done"]:
            return
        state["done"] = True
        for _ in range(int(state.get("steps") or 0)):
            remote.tap_combo(CTRL, ALT, key=LEFT)
            time.sleep(0.4)
        restore_wallpaper(old_wallpaper)
        remote.stop()
        SAVED.unlink(missing_ok=True)

    def encode() -> bool:
        count = frames_from_video()
        print("frame:", count, flush=True)
        gif = encode_gif(OUT, GIF, fps=10, width=CAPTURE_W, colors=128)
        print("gif:", gif, gif.stat().st_size // 1024, "KB", flush=True)
        loop.quit()
        return False

    def click_chip() -> bool:
        remote.move_to(*CHIP_SPOTIFY)
        GLib.timeout_add(260, remote.click)
        # Pointer harus keluar dari island: selama di dalam, hitungan tutup
        # ditahan dan island tidak pernah menutup sendiri.
        GLib.timeout_add(700, lambda: remote.move_to(*AWAY))
        return False

    def hover() -> bool:
        remote.move_to(*HOT_SPOT)
        return False

    def finish() -> bool:
        stop_cast(bus)
        for process in state["players"]:
            process.terminate()
        cleanup()
        # Beri Shell waktu menutup berkas sebelum dipecah.
        GLib.timeout_add(1200, encode)
        return False

    def begin() -> bool:
        if not ensure_empty_workspace(bus, remote, backdrop_colour, old_wallpaper):
            print("workspace kosong tidak ditemukan; rekaman dibatalkan", flush=True)
            cleanup()
            loop.quit()
            return False
        close_notifications()
        remote.move_to(*AWAY)
        try:
            print("merekam:", start_cast(bus), flush=True)
        except Exception as exc:
            print("rekaman gagal:", exc, flush=True)
            cleanup()
            loop.quit()
            return False

        # Pemutar pertama: island muncul sendiri karena media berubah.
        GLib.timeout_add(500, lambda: spawn_player(
            "spotify", "Lampu Kota|Peron Kosong", "Senja Kecil", "Peron"))
        # Pemutar kedua: island pindah ke VLC dan chip pemilih pemutar muncul.
        GLib.timeout_add(2800, lambda: spawn_player(
            "vlc", "Rekaman Konser|Panggung Kecil", "Paduan Suara", "Rekaman"))
        # Klik chip Spotify: island kembali ke lagu pertama, jadi pergantian
        # pemutar terlihat jelas (judul, artis, dan sampul ikut berubah).
        GLib.timeout_add(6400, click_chip)
        # Popup habis 5 dtk setelah klik chip (~11,4 dtk), lalu island menutup.
        # Pointer baru masuk zona hover sesudah itu — kalau lebih awal, hitungan
        # tutup ditahan dan island tak pernah menutup sendiri.
        GLib.timeout_add(11800, hover)
        GLib.timeout_add(int(SECONDS * 1000), finish)
        return False

    try:
        # Pindah ke workspace kosong dulu: jendela lain tidak ikut terekam.
        # Kalau workspace sekarang sudah kosong, `ensure_empty_workspace`
        # tahu dan tidak berpindah sama sekali.
        GLib.timeout_add(800, begin)
        loop.run()
    finally:
        cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
