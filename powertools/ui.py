"""Utilitas UI bersama: tema, ikon, clipboard, format teks."""

from __future__ import annotations

import os
import json
import time
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gdk, Gio, GLib, Gtk

from . import config
from .colorspace import (
    color_palette,
    color_shades,
    contrast_color,
    format_color,
    hex_to_rgb,
    rgb_to_cmyk,
    rgb_to_hex,
    rgb_to_hsl,
    shade_color,
)

# Jembatan clipboard milik shell. Dipakai lebih dulu karena di Wayland hanya
# pemilik selection (shell) yang permintaannya selalu diterima, apa pun jendela
# yang sedang fokus.
from .dbusutil import set_clipboard_image as _remote_image
from .dbusutil import set_clipboard_text as _remote_text

_CSS_PROVIDER: Gtk.CssProvider | None = None

# Ruang kosong di sekeliling kartu di dalam jendela. Dipakai agar bayangan
# punya tempat menyebar; kalau terlalu kecil, bayangan terpotong lurus di
# tepi jendela dan terlihat kaku/kotak.
SURFACE_MARGIN = 28


def is_dark_theme() -> bool:
    override = os.environ.get("POWERTOOLS_THEME", "").lower()
    if override in ("dark", "light"):
        return override == "dark"
    try:
        settings = Gio.Settings.new("org.gnome.desktop.interface")
        scheme = settings.get_string("color-scheme")
        if scheme == "prefer-dark":
            return True
        if scheme == "prefer-light":
            return False
        return "dark" in settings.get_string("gtk-theme").lower()
    except Exception:
        return True


_DARK = {
    "surface": "#1e1e23",
    "border": "rgba(255,255,255,0.10)",
    "text": "#ececf0",
    "sub": "rgba(236,236,240,0.55)",
    "faint": "rgba(236,236,240,0.35)",
    "hover": "rgba(255,255,255,0.06)",
    "selected": "rgba(122,162,255,0.24)",
    "accent": "#8fb1ff",
    "entry": "rgba(255,255,255,0.07)",
    "entry_border": "rgba(255,255,255,0.10)",
    "chip": "rgba(255,255,255,0.08)",
}

_LIGHT = {
    "surface": "#f7f7fa",
    "border": "rgba(0,0,0,0.10)",
    "text": "#1c1c1f",
    "sub": "rgba(28,28,31,0.58)",
    "faint": "rgba(28,28,31,0.38)",
    "hover": "rgba(0,0,0,0.05)",
    "selected": "rgba(26,95,208,0.16)",
    "accent": "#1a5fd0",
    "entry": "rgba(0,0,0,0.05)",
    "entry_border": "rgba(0,0,0,0.10)",
    "chip": "rgba(0,0,0,0.07)",
}


def _css_text() -> str:
    p = _DARK if is_dark_theme() else _LIGHT
    return f"""
    window.pt-window {{
        background-color: transparent;
    }}
    .pt-surface {{
        background-color: {p["surface"]};
        border: 1px solid {p["border"]};
        border-radius: 16px;
        color: {p["text"]};
        /* Dua lapis: kontak tipis + sebaran lebar. Lapisan terjauh menjulur
           8+18=26px ke bawah, jadi SURFACE_MARGIN (28px) harus lebih besar
           dari itu. Kalau tidak, GTK memotong shadow di tepi jendela dan
           tepinya terlihat sebagai garis/kotak kaku. */
        box-shadow:
            0 2px 6px rgba(0,0,0,0.12),
            0 8px 18px rgba(0,0,0,0.20);
    }}
    .pt-header {{
        padding: 14px 16px 6px 16px;
    }}
    .pt-title {{
        font-size: 10.5pt;
        font-weight: 700;
    }}
    .pt-sub {{
        font-size: 8.8pt;
        color: {p["sub"]};
    }}
    .pt-faint {{
        font-size: 8.4pt;
        color: {p["faint"]};
    }}
    .pt-warn {{
        font-size: 8.4pt;
        color: {p["accent"]};
    }}
    .pt-preview {{
        font-size: 10pt;
        color: {p["text"]};
    }}
    .pt-thumb {{
        border-radius: 8px;
        background-color: {p["chip"]};
    }}
    entry.pt-search {{
        background-color: {p["entry"]};
        border: 1px solid {p["entry_border"]};
        border-radius: 10px;
        padding: 8px 12px;
        min-height: 22px;
        font-size: 11pt;
        color: {p["text"]};
        caret-color: {p["accent"]};
    }}
    entry.pt-search:focus,
    entry.pt-search:focus-within {{
        border-color: {p["accent"]};
        outline: none;
        box-shadow: none;
    }}
    entry.pt-search text selection {{
        background-color: {p["selected"]};
        color: {p["text"]};
    }}
    scrolledwindow.pt-scroll,
    scrolledwindow.pt-scroll > viewport,
    list.pt-list,
    list.pt-list > row {{
        background-color: transparent;
        background-image: none;
    }}
    .pt-row {{
        padding: 8px 10px;
        border-radius: 10px;
        margin: 1px 6px;
    }}
    .pt-row:hover {{
        background-color: {p["hover"]};
    }}
    .pt-row:selected {{
        background-color: {p["selected"]};
    }}
    .pt-row-title {{
        font-size: 10pt;
        font-weight: 600;
        color: {p["text"]};
    }}
    .pt-row-sub {{
        font-size: 8.6pt;
        color: {p["sub"]};
    }}
    .pt-chip {{
        background-color: {p["chip"]};
        border-radius: 6px;
        padding: 2px 7px;
        font-size: 8pt;
        color: {p["sub"]};
    }}
    .pt-footer {{
        padding: 6px 16px 12px 16px;
        border-top: 1px solid {p["border"]};
    }}
    .pt-footer label {{
        font-size: 8.2pt;
        color: {p["faint"]};
    }}
    .pt-badge {{
        background-color: {p["selected"]};
        color: {p["accent"]};
        border-radius: 6px;
        padding: 1px 6px;
        font-size: 7.8pt;
        font-weight: 700;
    }}
    /* ------------------------------------------------------------ pengaturan */
    .pt-card {{
        background-color: {p["entry"]};
        border: 1px solid {p["border"]};
        border-radius: 12px;
        padding: 9px 12px;
    }}
    .pt-section {{
        font-size: 7.8pt;
        font-weight: 700;
        color: {p["faint"]};
        margin: 8px 2px 0 2px;
    }}
    /* ------------------------------------------- tab vertikal di pengaturan */
    .pt-nav {{
        background-color: transparent;
    }}
    list.pt-nav-list,
    list.pt-nav-list > row {{
        background-color: transparent;
        background-image: none;
    }}
    list.pt-nav-list > row.pt-nav-row {{
        padding: 7px 10px;
        border-radius: 9px;
        margin: 1px 0;
    }}
    list.pt-nav-list > row.pt-nav-row:hover {{
        background-color: {p["hover"]};
    }}
    list.pt-nav-list > row.pt-nav-row:selected {{
        background-color: {p["selected"]};
    }}
    .pt-nav-label {{
        font-size: 9.4pt;
        font-weight: 600;
        color: {p["sub"]};
    }}
    list.pt-nav-list > row.pt-nav-row:hover .pt-nav-label {{
        color: {p["text"]};
    }}
    list.pt-nav-list > row.pt-nav-row:selected .pt-nav-label {{
        color: {p["accent"]};
    }}
    .pt-nav-dot {{
        min-width: 7px;
        min-height: 7px;
        border-radius: 999px;
        background-color: {p["accent"]};
    }}
    .pt-nav-dot-off {{
        background-color: {p["faint"]};
    }}
    .pt-nav-sep {{
        min-width: 1px;
        background-color: {p["border"]};
    }}
    .pt-nav-version {{
        font-size: 8pt;
        color: {p["faint"]};
        padding: 0 4px;
    }}
    .pt-banner {{
        background-color: {p["selected"]};
        border-radius: 8px;
        padding: 6px 10px;
        font-size: 8.6pt;
        color: {p["accent"]};
    }}
    .pt-optrow {{
        padding: 5px 2px;
        min-height: 26px;
    }}
    scale.pt-scale {{
        min-height: 24px;
    }}
    scale.pt-scale trough {{
        min-height: 6px;
        border-radius: 999px;
        background-color: {p["entry_border"]};
    }}
    scale.pt-scale highlight {{
        border-radius: 999px;
        background-color: {p["accent"]};
    }}
    entry.pt-field,
    spinbutton.pt-spin,
    dropdown.pt-drop {{
        background-color: {p["entry"]};
        border: 1px solid {p["entry_border"]};
        border-radius: 8px;
        color: {p["text"]};
        min-height: 24px;
        padding: 2px 4px;
        box-shadow: none;
    }}
    entry.pt-field:focus,
    entry.pt-field:focus-within,
    spinbutton.pt-spin:focus,
    spinbutton.pt-spin:focus-within {{
        border-color: {p["accent"]};
        outline: none;
        box-shadow: none;
    }}
    spinbutton.pt-spin entry {{
        background-color: transparent;
        border: none;
        color: {p["text"]};
        box-shadow: none;
    }}
    spinbutton.pt-spin button {{
        background-color: transparent;
        border: none;
        box-shadow: none;
        color: {p["sub"]};
        min-width: 26px;
    }}
    spinbutton.pt-spin button:hover {{
        background-color: {p["hover"]};
        color: {p["text"]};
    }}
    dropdown.pt-drop button {{
        background-color: transparent;
        border: none;
        box-shadow: none;
        color: {p["text"]};
        padding: 2px 8px;
    }}
    dropdown.pt-drop button:hover {{
        background-color: {p["hover"]};
    }}
    .pt-empty {{
        font-size: 10pt;
        color: {p["sub"]};
        padding: 36px 20px;
    }}
    .pt-magcard {{
        background-color: #111114;
        border: 1px solid rgba(255,255,255,0.28);
        border-radius: 12px;
        padding: 8px;
        box-shadow:
            0 2px 6px rgba(0,0,0,0.30),
            0 8px 22px rgba(0,0,0,0.45);
    }}
    .pt-magcard label {{
        color: #f2f2f6;
        font-size: 9pt;
    }}
    .pt-hex {{
        font-size: 12pt;
        font-weight: 700;
        color: #ffffff;
    }}
    .pt-rgb {{
        font-size: 8.6pt;
        color: rgba(255,255,255,0.66);
    }}
    .pt-center-pixel {{
        border: 1px solid rgba(255,80,110,0.95);
    }}
    /* ---------------------------------------------------- hasil color picker */
    .pt-color-preview {{
        border-radius: 10px;
        border: 1px solid {p["border"]};
        min-width: 36px;
        min-height: 36px;
    }}
    .pt-color-section {{
        font-size: 8pt;
        font-weight: 700;
        color: {p["faint"]};
    }}
    button.pt-shade {{
        border: none;
        border-radius: 10px;
        padding: 9px 4px;
        background-image: none;
        box-shadow: none;
        min-height: 58px;
    }}
    button.pt-shade:hover {{
        box-shadow: inset 0 0 0 2px rgba(255,255,255,0.55);
    }}
    button.pt-palette {{
        border: none;
        border-radius: 9px;
        padding: 5px 8px;
        background-color: transparent;
        background-image: none;
        box-shadow: none;
    }}
    button.pt-palette:hover {{
        background-color: {p["hover"]};
    }}
    button.pt-iconbtn {{
        background-color: transparent;
        background-image: none;
        border: none;
        border-radius: 8px;
        padding: 6px;
        box-shadow: none;
    }}
    button.pt-iconbtn:hover {{
        background-color: {p["hover"]};
    }}
    .pt-dot {{
        border-radius: 8px;
        border: 1px solid {p["border"]};
        min-width: 26px;
        min-height: 26px;
    }}
    .pt-palette-name {{
        font-size: 9pt;
        font-weight: 600;
        color: {p["text"]};
    }}
    .pt-palette-hex {{
        font-family: "JetBrains Mono", "Fira Code", "DejaVu Sans Mono", monospace;
        font-size: 8pt;
        color: {p["sub"]};
    }}
    .pt-format-row {{
        background-color: {p["entry"]};
        border: 1px solid {p["entry_border"]};
        border-radius: 10px;
        padding: 8px 10px;
    }}
    .pt-format-tag {{
        font-size: 7.6pt;
        font-weight: 700;
        color: {p["faint"]};
    }}
    .pt-format-value {{
        font-family: "JetBrains Mono", "Fira Code", "DejaVu Sans Mono", monospace;
        font-size: 10pt;
        color: {p["text"]};
    }}
    button.pt-copy {{
        background-color: {p["chip"]};
        border: none;
        border-radius: 8px;
        padding: 5px 10px;
        font-size: 8.5pt;
        color: {p["text"]};
        box-shadow: none;
        background-image: none;
    }}
    button.pt-copy:hover {{
        background-color: {p["selected"]};
        color: {p["accent"]};
    }}
    .pt-shadow {{
        font-size: 7.4pt;
        font-weight: 600;
    }}
    .pt-hint {{
        background-color: rgba(17,17,20,0.82);
        border-radius: 999px;
        padding: 7px 16px;
        color: #f2f2f6;
        font-size: 9pt;
    }}
    .pt-scrim {{
        background-color: rgba(0,0,0,0.35);
    }}
    """


def install_css() -> None:
    global _CSS_PROVIDER
    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    try:
        provider.load_from_data(_css_text().encode("utf-8"))
    except GLib.Error:
        return
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _CSS_PROVIDER = provider


def flush_clipboard() -> None:
    """Kirim permintaan clipboard ke compositor sekarang juga.

    Di Wayland, GDK baru menulis permintaan ``set_selection`` ke soket pada
    putaran main loop berikutnya. Mutter menolak permintaan itu kalau klien
    yang mengirim tidak sedang memegang fokus keyboard, jadi permintaan harus
    didorong keluar selagi popup masih fokus (sebelum jendela disembunyikan).
    """
    display = Gdk.Display.get_default()
    if display is None:
        return
    try:
        display.flush()
    except Exception:
        pass


def set_clipboard(text: str) -> bool:
    display = Gdk.Display.get_default()
    if display is None:
        return False
    try:
        clipboard = display.get_clipboard()
        # Jangan bungkus dengan GLib.Variant: GDK akan mengiklankan tipe
        # "GVariant", bukan text/plain, sehingga paste di aplikasi lain gagal.
        clipboard.set_content(Gdk.ContentProvider.new_for_value(text))
    except Exception:
        return False
    flush_clipboard()
    return True


def copy_to_clipboard(text: str) -> bool:
    """Taruh teks di clipboard, lewat shell kalau jembatannya tersedia.

    Shell adalah pemilik selection di Wayland, jadi permintaannya selalu
    diterima. Clipboard GDK lokal hanya cadangan (mis. saat ekstensi belum
    dimuat atau sesi X11 tanpa jembatan).
    """
    if _remote_text(text):
        return True
    return set_clipboard(text)


# ------------------------------------------------------------------- gambar
THUMB_W = 192
THUMB_H = 144


def format_bytes(value: int) -> str:
    value = max(0, int(value))
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.0f} KB"
    return f"{value / (1024 * 1024):.1f} MB".replace(".", ",")


def mime_label(mime: str) -> str:
    mime = (mime or "").lower()
    if "jpeg" in mime or "jpg" in mime:
        return "JPEG"
    if "webp" in mime:
        return "WebP"
    if "bmp" in mime:
        return "BMP"
    if "svg" in mime:
        return "SVG"
    return "PNG"


def _pixbuf_module():
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    return GdkPixbuf


def _write_thumb_meta(name: str, width: int, height: int) -> None:
    try:
        config.THUMBS_DIR.mkdir(parents=True, exist_ok=True)
        (config.THUMBS_DIR / f"{name}.json").write_text(
            json.dumps({"w": int(width), "h": int(height)}), encoding="utf-8"
        )
    except OSError:
        pass


def image_size(clip) -> tuple[int, int]:
    """Ukuran asli gambar, dibaca dari cache metadata thumbnail."""
    info = config.THUMBS_DIR / f"{clip.image}.json"
    try:
        data = json.loads(info.read_text(encoding="utf-8"))
        width, height = int(data.get("w") or 0), int(data.get("h") or 0)
        if width and height:
            return width, height
    except (OSError, ValueError, TypeError):
        pass

    # Metadata hilang (mis. cache dibersihkan): baca header saja, tanpa
    # mendekode seluruh gambar.
    try:
        GdkPixbuf = _pixbuf_module()
        _fmt, width, height = GdkPixbuf.Pixbuf.get_file_info(
            str(config.IMAGES_DIR / clip.image)
        )
        if width and height:
            _write_thumb_meta(clip.image, width, height)
            return int(width), int(height)
    except Exception:
        pass
    return 0, 0


def image_thumb(clip) -> tuple[Path | None, int, int]:
    """Buat/ambil thumbnail cache. Return (path, lebar asli, tinggi asli)."""
    src = config.IMAGES_DIR / clip.image
    cache = config.THUMBS_DIR / f"{clip.image}.png"
    if cache.exists():
        width, height = image_size(clip)
        return cache, width, height

    try:
        GdkPixbuf = _pixbuf_module()
        _fmt, width, height = GdkPixbuf.Pixbuf.get_file_info(str(src))
        # `new_from_file_at_scale` membuat decoder langsung mengecilkan
        # (libjpeg bisa melakukannya saat decode) jadi popup tidak perlu
        # memuat foto 20 MP ke memori hanya untuk thumbnail.
        thumb = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            str(src), THUMB_W, THUMB_H, True
        )
    except Exception:
        return None, 0, 0

    if not (width and height):
        width, height = thumb.get_width(), thumb.get_height()
    try:
        config.THUMBS_DIR.mkdir(parents=True, exist_ok=True)
        thumb.savev(str(cache), "png", [], [])
        _write_thumb_meta(clip.image, width, height)
    except Exception:
        return None, width, height
    return cache, width, height


def set_clipboard_image(path: Path, mime: str = "image/png") -> bool:
    display = Gdk.Display.get_default()
    if display is None:
        return False
    try:
        data = Path(path).read_bytes()
    except OSError:
        return False
    if not data:
        return False
    try:
        clipboard = display.get_clipboard()
        # Format asli dipertahankan: GDK bisa menyerahkan tekstur dari
        # PNG/JPEG/WebP/BMP, sedangkan mengubah foto besar ke PNG bisa
        # memakan beberapa detik dan puluhan MB.
        clipboard.set_content(
            Gdk.ContentProvider.new_for_bytes(mime or "image/png", GLib.Bytes.new(data))
        )
    except Exception:
        return False
    flush_clipboard()
    return True


def copy_image_to_clipboard(path: Path, mime: str = "image/png") -> bool:
    """Taruh gambar di clipboard, lewat shell kalau jembatannya tersedia."""
    if _remote_image(str(path), mime):
        return True
    return set_clipboard_image(path, mime)


def get_clipboard_text_async(callback) -> None:
    display = Gdk.Display.get_default()
    if display is None:
        callback(None)
        return
    try:
        display.get_clipboard().read_text_async(None, _on_text, callback)
    except Exception:
        callback(None)


def _on_text(clipboard, result, callback) -> None:
    try:
        text = clipboard.read_text_finish(result)
    except GLib.Error:
        text = None
    callback(text)


def notify(app: Gtk.Application, title: str, body: str | None = None) -> None:
    try:
        notification = Gio.Notification.new(title)
        if body:
            notification.set_body(body)
        notification.set_icon(Gio.ThemedIcon.new("io.github.powertools.App"))
        app.send_notification("powertools", notification)
    except Exception:
        pass


def make_icon(spec, size: int = 24) -> Gtk.Image:
    """spec: nama ikon (str) atau Gio.Icon. Selalu mengembalikan Gtk.Image."""
    if isinstance(spec, Gio.Icon):
        image = Gtk.Image.new_from_gicon(spec)
    else:
        image = Gtk.Image.new_from_icon_name(str(spec or "application-x-executable-symbolic"))
    image.set_pixel_size(size)
    return image


def format_age(ts: float) -> str:
    now = time.time()
    delta = max(0.0, now - ts)
    if delta < 10:
        return "baru saja"
    if delta < 60:
        return f"{int(delta)} detik lalu"
    if delta < 3600:
        return f"{int(delta // 60)} menit lalu"
    if delta < 86400:
        return f"{int(delta // 3600)} jam lalu"
    if delta < 2 * 86400:
        return "kemarin"
    if delta < 7 * 86400:
        return f"{int(delta // 86400)} hari lalu"
    try:
        return datetime.fromtimestamp(ts).strftime("%d %b")
    except (OverflowError, ValueError, OSError):
        return ""


def format_number_id(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def preview_text(text: str, limit: int = 400) -> str:
    text = text.replace("\r\n", "\n").replace("\t", "    ").strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def scroll_row_into_view(scroller: Gtk.ScrolledWindow, row: Gtk.Widget) -> None:
    try:
        ok, rect = row.compute_bounds(scroller)
        if not ok:
            return
        adjustment = scroller.get_vadjustment()
        if adjustment is None:
            return
        top = rect.get_y()
        bottom = top + rect.get_height()
        value = adjustment.get_value()
        page = adjustment.get_page_size()
        if top < value:
            adjustment.set_value(top)
        elif bottom > value + page:
            adjustment.set_value(bottom - page)
    except Exception:
        pass


def hint_bar(left_text: str, right_text: str = "") -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    box.add_css_class("pt-footer")
    left = Gtk.Label(label=left_text, xalign=0)
    left.set_hexpand(True)
    box.append(left)
    if right_text:
        box.append(Gtk.Label(label=right_text, xalign=1))
    return box


# Helper warna tinggal di colorspace.py (tanpa GTK) dan diimpor ulang di sini
# agar pemanggil lama seperti `ui.format_color()` tetap bekerja.