"""Color picker: pakai picker native GNOME, fallback ke overlay screenshot."""

from __future__ import annotations

import os
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from . import config, portal, screenshot, ui


class ColorPicker:
    """Koordinator pemilihan warna.

    Urutan: PickColor milik GNOME Shell (paling mulus, ada loupe) ->
    portal PickColor -> overlay screenshot buatan sendiri.
    """

    def __init__(self, app: Gtk.Application) -> None:
        self._app = app
        self._window: ColorOverlay | None = None
        self._busy = False

    def start(self) -> None:
        if self._busy:
            return
        self._busy = True
        portal.pick_color_shell(self._on_picked, self._try_portal)

    def _try_portal(self) -> None:
        portal.pick_color_portal(self._on_picked, self._fallback)

    def _on_picked(self, rgb) -> None:
        self._busy = False
        if rgb is None:
            return
        self._app.apply_color(rgb)  # type: ignore[attr-defined]

    def _fallback(self) -> None:
        screenshot.capture_screen(self._on_screenshot)

    def _on_screenshot(self, path: Path | None) -> None:
        if path is None:
            self._busy = False
            ui.notify(
                self._app,
                "Color picker tidak tersedia",
                "Tidak bisa mengambil screenshot di sesi ini.",
            )
            return
        try:
            self._window = ColorOverlay(self._app, path, self._on_overlay_done)
        except Exception:
            self._busy = False
            try:
                os.unlink(path)
            except OSError:
                pass
            ui.notify(self._app, "Color picker gagal", "Screenshot tidak bisa dibaca.")
            return
        self._window.show_overlay()

    def _on_overlay_done(self, rgb) -> None:
        self._busy = False
        self._window = None
        if rgb is not None:
            self._app.apply_color(rgb)  # type: ignore[attr-defined]


class ColorOverlay(Gtk.Window):
    REGION = 15  # jumlah piksel yang ditampilkan di loupe (ganjil)
    ZOOM = 8

    def __init__(self, app: Gtk.Application, shot_path: Path, on_done) -> None:
        super().__init__(application=app)
        self._on_done = on_done
        self._shot_path = Path(shot_path)
        self._done = False

        self._pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(shot_path))
        self._pixels = self._pixbuf.get_pixels()
        self._channels = self._pixbuf.get_n_channels()
        self._rowstride = self._pixbuf.get_rowstride()
        self._width = self._pixbuf.get_width()
        self._height = self._pixbuf.get_height()

        self._pointer: tuple[int, int] | None = None
        self._pending: tuple[int, int] | None = None
        self._throttle_source = 0
        self._current_rgb: tuple[int, int, int] | None = None
        self._shown_at = 0.0

        self.set_title("PowerTools Color Picker")
        self.set_icon_name(config.APP_ID)
        self.set_decorated(False)
        self.add_css_class("pt-window")
        self.set_cursor_from_name("crosshair")

        self._build()
        self.connect("notify::is-active", self._on_active_changed)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_click)
        self.add_controller(click)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    # ------------------------------------------------------------------- build
    def _build(self) -> None:
        texture = Gdk.Texture.new_for_pixbuf(self._pixbuf)
        self._picture = Gtk.Picture.new_for_paintable(texture)
        self._picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self._picture.set_can_shrink(True)

        overlay = Gtk.Overlay()
        overlay.set_child(self._picture)

        self._area = Gtk.DrawingArea()
        self._area.set_draw_func(self._draw_crosshair)
        self._area.set_can_target(False)
        overlay.add_overlay(self._area)

        self._hint = Gtk.Label(label="Klik untuk memilih warna   ·   Esc untuk batal")
        self._hint.add_css_class("pt-hint")
        self._hint.set_halign(Gtk.Align.CENTER)
        self._hint.set_valign(Gtk.Align.START)
        self._hint.set_margin_top(22)
        overlay.add_overlay(self._hint)

        self._fixed = Gtk.Fixed()
        self._fixed.set_can_target(False)
        self._card = self._make_card()
        self._fixed.put(self._card, 0, 0)
        overlay.add_overlay(self._fixed)

        self.set_child(overlay)

    def _make_card(self) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("pt-magcard")
        card.set_size_request(148, -1)

        self._mag_picture = Gtk.Picture()
        self._mag_picture.set_can_shrink(False)
        self._mag_picture.set_size_request(128, 128)
        self._mag_picture.set_content_fit(Gtk.ContentFit.CONTAIN)

        mag_overlay = Gtk.Overlay()
        mag_overlay.set_child(self._mag_picture)
        self._center_pixel = Gtk.Box()
        self._center_pixel.add_css_class("pt-center-pixel")
        self._center_pixel.set_size_request(self.ZOOM, self.ZOOM)
        self._center_pixel.set_halign(Gtk.Align.CENTER)
        self._center_pixel.set_valign(Gtk.Align.CENTER)
        mag_overlay.add_overlay(self._center_pixel)
        card.append(mag_overlay)

        self._hex_label = Gtk.Label(label="#000000", xalign=0)
        self._hex_label.add_css_class("pt-hex")
        card.append(self._hex_label)

        self._rgb_label = Gtk.Label(label="rgb(0, 0, 0)", xalign=0)
        self._rgb_label.add_css_class("pt-rgb")
        card.append(self._rgb_label)
        return card

    # ------------------------------------------------------------------ public
    def show_overlay(self) -> None:
        self._shown_at = time.monotonic()
        self.fullscreen()
        self.present()
        GLib.idle_add(self._initial_pointer)

    def _initial_pointer(self) -> bool:
        width = max(self.get_width(), 1)
        height = max(self.get_height(), 1)
        self._update(width // 2, height // 2)
        return False

    # ------------------------------------------------------------------ events
    def _on_motion(self, controller, x: float, y: float) -> None:
        self._pending = (int(x), int(y))
        if self._throttle_source:
            return
        self._throttle_source = GLib.timeout_add(14, self._flush_motion)

    def _flush_motion(self) -> bool:
        self._throttle_source = 0
        if self._pending is not None:
            x, y = self._pending
            self._pending = None
            self._update(x, y)
        return False

    def _on_click(self, gesture, n_press, x, y) -> None:
        if n_press == 1:
            self._update(int(x), int(y))
            self._finish(self._current_rgb)

    def _on_key(self, controller, keyval, keycode, state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self._finish(None)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space, Gdk.KEY_ISO_Enter):
            self._finish(self._current_rgb)
            return True
        return False

    def _on_active_changed(self, window, pspec) -> None:
        # Kalau overlay kehilangan fokus (mis. workspace pindah), batalkan.
        if not self.is_active() and not os.environ.get("POWERTOOLS_NO_AUTOHIDE"):
            if self._shown_at and time.monotonic() - self._shown_at > 0.4:
                self._finish(None)

    # ------------------------------------------------------------------ render
    def _update(self, x: int, y: int) -> None:
        self._pointer = (x, y)
        rgb = self._sample(x, y)
        if rgb is None:
            return
        self._current_rgb = rgb
        self._hex_label.set_label(ui.rgb_to_hex(rgb))
        self._rgb_label.set_label(f"rgb({rgb[0]}, {rgb[1]}, {rgb[2]})")
        self._magnify(x, y)
        self._position_card(x, y)
        self._area.queue_draw()

    def _scale_info(self) -> tuple[float, float, int, int]:
        pic_w = self._picture.get_width() or self.get_width() or 1
        pic_h = self._picture.get_height() or self.get_height() or 1
        scale = min(pic_w / self._width, pic_h / self._height)
        draw_w = self._width * scale
        draw_h = self._height * scale
        offset_x = int((pic_w - draw_w) / 2)
        offset_y = int((pic_h - draw_h) / 2)
        return scale, offset_x, offset_y, pic_w

    def _sample(self, x: int, y: int) -> tuple[int, int, int] | None:
        scale, offset_x, offset_y, _ = self._scale_info()
        if scale <= 0:
            return None
        px = int((x - offset_x) / scale)
        py = int((y - offset_y) / scale)
        if px < 0 or py < 0 or px >= self._width or py >= self._height:
            return None
        offset = py * self._rowstride + px * self._channels
        try:
            return (
                self._pixels[offset],
                self._pixels[offset + 1],
                self._pixels[offset + 2],
            )
        except (IndexError, TypeError):
            return None

    def _magnify(self, x: int, y: int) -> None:
        scale, offset_x, offset_y, _ = self._scale_info()
        if scale <= 0:
            return
        px = int((x - offset_x) / scale)
        py = int((y - offset_y) / scale)
        half = self.REGION // 2
        x0 = max(0, px - half)
        y0 = max(0, py - half)
        x1 = min(self._width, px + half + 1)
        y1 = min(self._height, py + half + 1)
        if x1 <= x0 or y1 <= y0:
            return
        try:
            sub = self._pixbuf.new_subpixbuf(x0, y0, x1 - x0, y1 - y0)
            zoom = self.ZOOM
            scaled = sub.scale_simple(
                (x1 - x0) * zoom, (y1 - y0) * zoom, GdkPixbuf.InterpType.NEAREST
            )
            self._mag_picture.set_paintable(Gdk.Texture.new_for_pixbuf(scaled))
        except Exception:
            return

    def _position_card(self, x: int, y: int) -> None:
        width = self.get_width() or 1920
        height = self.get_height() or 1080
        card_w = max(self._card.get_width(), 148)
        card_h = max(self._card.get_height(), 190)
        card_x = x + 24
        card_y = y + 24
        if card_x + card_w > width - 12:
            card_x = x - card_w - 24
        if card_y + card_h > height - 12:
            card_y = y - card_h - 24
        self._fixed.move(self._card, max(12, card_x), max(12, card_y))

    def _draw_crosshair(self, area, cr, width: int, height: int) -> None:
        if self._pointer is None:
            return
        x, y = self._pointer
        x = min(max(0, x), width)
        y = min(max(0, y), height)
        cr.set_line_width(3)
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.55)
        cr.move_to(x + 0.5, 0)
        cr.line_to(x + 0.5, height)
        cr.move_to(0, y + 0.5)
        cr.line_to(width, y + 0.5)
        cr.stroke()
        cr.set_line_width(1)
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.9)
        cr.move_to(x + 0.5, 0)
        cr.line_to(x + 0.5, height)
        cr.move_to(0, y + 0.5)
        cr.line_to(width, y + 0.5)
        cr.stroke()

    # ------------------------------------------------------------------ finish
    def _finish(self, rgb) -> None:
        if self._done:
            return
        self._done = True
        self._on_done(rgb)
        try:
            self._shot_path.unlink(missing_ok=True)
        except OSError:
            pass
        self.destroy()