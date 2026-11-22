"""Helper D-Bus kecil (PyGObject tidak punya `Gio.DBus.session`)."""

from __future__ import annotations

from gi.repository import Gio, GLib

BRIDGE_NAME = "org.gnome.Shell.Extensions.PowerTools"
BRIDGE_PATH = "/org/gnome/Shell/Extensions/PowerTools"
BRIDGE_IFACE = "org.gnome.Shell.Extensions.PowerTools.Clipboard"


def session_bus() -> Gio.DBusConnection | None:
    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        return None


def name_has_owner(name: str, timeout_ms: int = 500) -> bool:
    bus = session_bus()
    if bus is None:
        return False
    try:
        reply = bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            GLib.Variant("(s)", (name,)),
            None,
            Gio.DBusCallFlags.NONE,
            timeout_ms,
            None,
        )
        return bool(reply.unpack()[0])
    except GLib.Error:
        return False


def _call_bridge(method: str, args: GLib.Variant | None, timeout_ms: int) -> bool:
    bus = session_bus()
    if bus is None:
        return False
    try:
        reply = bus.call_sync(
            BRIDGE_NAME,
            BRIDGE_PATH,
            BRIDGE_IFACE,
            method,
            args,
            None,
            Gio.DBusCallFlags.NONE,
            timeout_ms,
            None,
        )
        return bool(reply.unpack()[0])
    except GLib.Error:
        return False

def paste_previous(timeout_ms: int = 2500) -> bool:
    """Minta ekstensi menekan Ctrl+V di jendela yang fokus sebelum popup.

    Clipboard tidak diubah, jadi Ctrl+V manual berikutnya tetap menempelkan
    item yang sama. Return False kalau jembatan tidak tersedia atau target
    sudah tidak bisa difokuskan.
    """
    return _call_bridge("Paste", None, timeout_ms)

def set_clipboard_text(text: str, timeout_ms: int = 2500) -> bool:
    """Titipkan teks ke clipboard milik shell.

    Shell adalah pemilik selection di Wayland, jadi permintaan ini selalu
    diterima. Kalau aplikasi Python yang menaruh clipboard sendiri, Mutter
    menolaknya ketika popup sudah kehilangan fokus dan Ctrl+V menempelkan
    item lama. Return False kalau jembatan tidak tersedia.
    """
    return _call_bridge("SetClipboardText", GLib.Variant("(s)", (text,)), timeout_ms)

def set_clipboard_image(
    path: str, mime: str = "image/png", timeout_ms: int = 2500
) -> bool:
    """Titipkan gambar (dibaca dari path oleh shell) ke clipboard milik shell."""
    return _call_bridge(
        "SetClipboardImage",
        GLib.Variant("(ss)", (str(path), mime or "image/png")),
        timeout_ms,
    )


def set_lyrics_above(above: bool, timeout_ms: int = 1500) -> bool:
    """Sematkan/lepas jendela lirik di atas jendela lain lewat shell.

    GNOME adalah satu-satunya pihak yang bisa mengubah lapisan jendela di
    Wayland. Kalau ekstensi tidak aktif, jendela tetap jendela biasa.
    """
    return _call_bridge(
        "SetLyricsAbove", GLib.Variant("(b)", (bool(above),)), timeout_ms
    )


def place_lyrics_window(timeout_ms: int = 1500) -> bool:
    """Minta shell menaruh jendela lirik di kanan bawah (sekali per jendela)."""
    return _call_bridge("PlaceLyricsWindow", None, timeout_ms)


def set_lyrics_skip_taskbar(skip: bool, timeout_ms: int = 1500) -> bool:
    """Sembunyikan/tampilkan jendela lirik di taskbar lewat shell.

    ``skip`` bernilai True berarti jendela tidak muncul di taskbar maupun
    Alt-Tab. Tanpa ekstensi GNOME, jendela tetap tampil seperti biasa.
    """
    return _call_bridge(
        "SetLyricsSkipTaskbar", GLib.Variant("(b)", (bool(skip),)), timeout_ms
    )
