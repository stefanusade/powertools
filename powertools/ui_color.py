"""Jendela mengambang hasil color picker.

Menampilkan warna yang baru diambil beserta nilai CSS (hex/rgb/hsl/cmyk),
deret versi lebih gelap/terang di atas, dan palet rekomendasi di kiri.
"""

from __future__ import annotations

import itertools

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GLib, Gtk

from . import colorspace, config, ui

FORMATS = (
    ("HEX", "hex"),
    ("RGB", "rgb"),
    ("HSL", "hsl"),
    ("CMYK", "cmyk"),
)

_counter = itertools.count(1)


class ColorResultWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, store, cfg) -> None:
        super().__init__(application=app)
        self._app = app
        self._store = store
        self._cfg = cfg
        self._rgb = (0, 0, 0)
        self._status_source = 0
        self._prefix = f"ptc{next(_counter)}"
        self._shade_labels: list[Gtk.Label] = []
        self._shade_values: list[Gtk.Label] = []
        self._palette_names: list[Gtk.Label] = []
        self._palette_values: list[Gtk.Label] = []
        self._format_values: dict[str, Gtk.Label] = {}

        self.set_title("PowerTools Warna")
        self.set_icon_name(config.APP_ID)
        self.add_css_class("pt-window")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_default_size(840, 600)
        self.set_modal(False)

        self._build()
        self._provider = self._make_dynamic_provider()
        self._layered = self._setup_layer_shell()

        controller = Gtk.EventControllerKey()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(controller)

        self.connect("close-request", self._on_close_request)
        self.connect("destroy", self._on_destroy)

    # ------------------------------------------------------------------- build
    def _build(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for edge in ("top", "bottom", "start", "end"):
            getattr(root, f"set_margin_{edge}")(ui.SURFACE_MARGIN)

        surface = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        surface.add_css_class("pt-surface")
        root.append(surface)
        self.set_child(root)

        # Header: pratinjau + hex + tombol tutup.
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.add_css_class("pt-header")

        preview = Gtk.Box()
        preview.add_css_class("pt-color-preview")
        preview.add_css_class(f"{self._prefix}-preview")
        preview.set_size_request(36, 36)
        preview.set_valign(Gtk.Align.CENTER)
        header.append(preview)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title_box.set_hexpand(True)
        title_box.set_valign(Gtk.Align.CENTER)
        title = Gtk.Label(label="Warna Terpilih", xalign=0)
        title.add_css_class("pt-title")
        title_box.append(title)
        self._hex_label = Gtk.Label(label="—", xalign=0)
        self._hex_label.add_css_class("pt-sub")
        title_box.append(self._hex_label)
        header.append(title_box)

        close = Gtk.Button.new_from_icon_name("window-close-symbolic")
        close.add_css_class("pt-iconbtn")
        close.set_tooltip_text("Tutup (Esc)")
        close.set_valign(Gtk.Align.CENTER)
        close.connect("clicked", lambda *_: self.hide_window())
        header.append(close)
        surface.append(header)

        # Deret 10–30% lebih gelap/terang.
        band = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        band.set_margin_start(16)
        band.set_margin_end(16)
        band.set_margin_bottom(6)
        shades = colorspace.color_shades(self._rgb)
        for index, (label, _rgb) in enumerate(shades):
            button = Gtk.Button()
            button.add_css_class("pt-shade")
            button.add_css_class(f"{self._prefix}-shade-{index}")
            button.set_hexpand(True)
            button.set_tooltip_text(f"Salin warna {label}")

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            box.set_valign(Gtk.Align.CENTER)
            pct = Gtk.Label(label=label, xalign=0.5)
            pct.add_css_class("pt-shadow")
            box.append(pct)
            value = Gtk.Label(label="", xalign=0.5)
            value.add_css_class("pt-shadow")
            box.append(value)
            button.set_child(box)
            button.connect("clicked", self._on_shade_clicked, index)
            band.append(button)
            self._shade_labels.append(pct)
            self._shade_values.append(value)
        surface.append(band)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        body.set_margin_start(16)
        body.set_margin_end(16)
        body.set_margin_bottom(8)
        body.set_vexpand(True)

        # Kolom palet rekomendasi.
        palette = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        palette.set_size_request(232, -1)
        section = Gtk.Label(label="PALET REKOMENDASI", xalign=0)
        section.add_css_class("pt-color-section")
        section.set_margin_bottom(2)
        section.set_margin_start(8)
        palette.append(section)
        for index, (name, _rgb) in enumerate(colorspace.color_palette(self._rgb)):
            button = Gtk.Button()
            button.add_css_class("pt-palette")
            button.set_tooltip_text(f"Salin {name}")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
            dot = Gtk.Box()
            dot.add_css_class("pt-dot")
            dot.add_css_class(f"{self._prefix}-dot-{index}")
            dot.set_size_request(26, 26)
            dot.set_valign(Gtk.Align.CENTER)
            row.append(dot)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            text.set_valign(Gtk.Align.CENTER)
            text.set_hexpand(True)
            name_label = Gtk.Label(label=name, xalign=0)
            name_label.add_css_class("pt-palette-name")
            text.append(name_label)
            hex_label = Gtk.Label(label="", xalign=0)
            hex_label.add_css_class("pt-palette-hex")
            text.append(hex_label)
            row.append(text)
            button.set_child(row)
            button.connect("clicked", self._on_palette_clicked, index)
            palette.append(button)
            self._palette_names.append(name_label)
            self._palette_values.append(hex_label)
        body.append(palette)

        # Kolom nilai CSS.
        values = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        values.set_hexpand(True)
        section = Gtk.Label(label="NILAI WARNA · SIAP TEMPEL SEBAGAI CSS", xalign=0)
        section.add_css_class("pt-color-section")
        section.set_margin_start(2)
        values.append(section)

        for tag, fmt in FORMATS:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.add_css_class("pt-format-row")
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            text.set_hexpand(True)
            text.set_valign(Gtk.Align.CENTER)
            tag_label = Gtk.Label(label=tag, xalign=0)
            tag_label.add_css_class("pt-format-tag")
            text.append(tag_label)
            value = Gtk.Label(label="", xalign=0)
            value.add_css_class("pt-format-value")
            value.set_selectable(True)
            value.set_ellipsize(3)  # Pango.EllipsizeMode.END
            text.append(value)
            row.append(text)

            copy = Gtk.Button()
            copy.add_css_class("pt-copy")
            copy.set_valign(Gtk.Align.CENTER)
            copy.set_tooltip_text(f"Salin {tag}")
            copy_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            copy_row.append(ui.make_icon("edit-copy-symbolic", 14))
            copy_row.append(Gtk.Label(label="Salin"))
            copy.set_child(copy_row)
            copy.connect("clicked", self._on_copy_format, fmt)
            row.append(copy)

            values.append(row)
            self._format_values[fmt] = value

        note = Gtk.Label(
            label="Klik swatch atau tombol Salin untuk menaruh nilainya di clipboard.",
            xalign=0,
        )
        note.add_css_class("pt-faint")
        note.set_wrap(True)
        note.set_margin_start(2)
        values.append(note)
        body.append(values)
        surface.append(body)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.add_css_class("pt-footer")
        hint = Gtk.Label(label="Klik swatch untuk salin   ·   Esc tutup", xalign=0)
        hint.set_hexpand(True)
        footer.append(hint)
        self._status = Gtk.Label(label="", xalign=1)
        footer.append(self._status)
        surface.append(footer)

    # ------------------------------------------------------------------- color
    def show_color(self, rgb, copied: str | None = None, fmt: str = "hex") -> None:
        self._rgb = colorspace.normalize_rgb(rgb)
        self._refresh()
        self.set_visible(True)
        self.present()
        GLib.idle_add(self._present_again)
        if copied is not None:
            self._flash(f"{fmt.upper()} disalin: {copied}")

    def hide_window(self) -> None:
        self.set_visible(False)
        self._app.window_hidden(self)  # type: ignore[attr-defined]

    def _refresh(self) -> None:
        rgb = self._rgb
        hex_value = colorspace.rgb_to_hex(rgb)
        self._hex_label.set_label(f"{hex_value}  ·  klik nilai untuk menyalin")

        rules = [
            f".{self._prefix}-preview {{ background-color: {hex_value}; }}",
        ]

        for index, (label, color) in enumerate(colorspace.color_shades(rgb)):
            value = colorspace.rgb_to_hex(color)
            foreground = colorspace.contrast_color(color)
            self._shade_labels[index].set_label(label)
            self._shade_values[index].set_label(value)
            rules.append(
                f".{self._prefix}-shade-{index} {{ "
                f"background-color: {value}; color: {foreground}; }}"
            )
            rules.append(
                f".{self._prefix}-shade-{index}:hover {{ "
                f"box-shadow: inset 0 0 0 2px {foreground}; }}"
            )

        for index, (name, color) in enumerate(colorspace.color_palette(rgb)):
            value = colorspace.rgb_to_hex(color)
            self._palette_names[index].set_label(name)
            self._palette_values[index].set_label(value)
            rules.append(
                f".{self._prefix}-dot-{index} {{ background-color: {value}; }}"
            )

        for fmt, label in self._format_values.items():
            label.set_label(colorspace.format_color(rgb, fmt))

        try:
            self._provider.load_from_data("\n".join(rules).encode("utf-8"))
        except GLib.Error:
            pass

    # ------------------------------------------------------------------ actions
    def _copy(self, text: str) -> None:
        if ui.copy_to_clipboard(text):
            self._store.touch(text)
            self._flash(f"Disalin: {text}")
        else:
            self._flash("Clipboard tidak tersedia")

    def _on_copy_format(self, _button, fmt: str) -> None:
        self._copy(colorspace.format_color(self._rgb, fmt))

    def _on_shade_clicked(self, _button, index: int) -> None:
        label = self._shade_values[index].get_label()
        if label:
            self._copy(label)

    def _on_palette_clicked(self, _button, index: int) -> None:
        label = self._palette_values[index].get_label()
        if label:
            self._copy(label)

    def _flash(self, message: str) -> None:
        if self._status_source:
            try:
                GLib.source_remove(self._status_source)
            except Exception:
                pass
        self._status.set_label(message)
        self._status_source = GLib.timeout_add(2600, self._clear_status)

    def _clear_status(self) -> bool:
        self._status_source = 0
        self._status.set_label("")
        return False

    # ------------------------------------------------------------------- layer
    def _setup_layer_shell(self) -> bool:
        """Opsional: pakai lapisan OVERLAY kalau gtk4-layer-shell tersedia.

        Tanpa pustaka itu, jendela tetap mengambang sebagai jendela biasa dan
        diangkat ke depan lewat ``present()``.
        """
        try:
            gi.require_version("Gtk4LayerShell", "1.0")
            from gi.repository import Gtk4LayerShell as layer_shell
        except (ValueError, ImportError):
            return False
        try:
            layer_shell.init_for_window(self)
            layer_shell.set_namespace(self, "powertools-color")
            layer_shell.set_layer(self, layer_shell.Layer.OVERLAY)
            layer_shell.set_anchor(self, layer_shell.Edge.TOP, True)
            layer_shell.set_margin(self, layer_shell.Edge.TOP, 72)
            layer_shell.set_keyboard_mode(
                self, layer_shell.KeyboardMode.ON_DEMAND
            )
            return True
        except Exception:
            return False

    def _present_again(self) -> bool:
        if self.get_visible():
            self.present()
        return False

    def _make_dynamic_provider(self) -> Gtk.CssProvider:
        provider = Gtk.CssProvider()
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        return provider

    # ------------------------------------------------------------------- close
    def _on_key_pressed(self, controller, keyval, keycode, state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.hide_window()
            return True
        return False

    def _on_close_request(self, window) -> bool:
        self.hide_window()
        return True

    def _on_destroy(self, window) -> None:
        if self._status_source:
            try:
                GLib.source_remove(self._status_source)
            except Exception:
                pass
            self._status_source = 0
        display = Gdk.Display.get_default()
        if display is not None:
            try:
                Gtk.StyleContext.remove_provider_for_display(display, self._provider)
            except Exception:
                pass