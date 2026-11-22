"""Jembatan ke portal desktop & GNOME Shell (warna + screenshot)."""

from __future__ import annotations

import os
import time

from gi.repository import Gio, GLib

from .dbusutil import session_bus

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_IFACE = "org.freedesktop.portal.Screenshot"
REQUEST_IFACE = "org.freedesktop.portal.Request"

SHELL_BUS = "org.gnome.Shell.Screenshot"
SHELL_PATH = "/org/gnome/Shell/Screenshot"
SHELL_IFACE = "org.gnome.Shell.Screenshot"


def _sender_token(bus: Gio.DBusConnection) -> str:
    name = bus.get_unique_name() or ":0.0"
    return name[1:].replace(".", "_")


def _rgb_from_variant(value: GLib.Variant | None) -> tuple[int, int, int] | None:
    if value is None:
        return None
    try:
        if isinstance(value, (tuple, list)) and len(value) == 3:
            r, g, b = value
        elif value.get_type_string() == "(ddd)":
            r, g, b = value.unpack()
        elif isinstance(value, GLib.Variant):
            unpacked = value.unpack()
            if not isinstance(unpacked, tuple) or len(unpacked) != 3:
                return None
            r, g, b = unpacked
        else:
            return None
    except (TypeError, ValueError):
        return None
    components = []
    for component in (r, g, b):
        try:
            number = float(component)
        except (TypeError, ValueError):
            return None
        if number <= 1.0:
            number *= 255.0
        components.append(max(0, min(255, int(round(number)))))
    return components[0], components[1], components[2]


def _call_portal(
    method: str,
    options: dict[str, GLib.Variant],
    on_result,
    on_error,
    timeout_seconds: int = 300,
) -> None:
    bus = session_bus()
    if bus is None:
        on_error()
        return

    token = f"powertools_{os.getpid()}_{int(time.time() * 1000)}"
    predicted = f"{PORTAL_PATH}/request/{_sender_token(bus)}/{token}"
    options = dict(options)
    options["handle_token"] = GLib.Variant("s", token)
    state: dict[str, object] = {"sub": None, "done": False, "timeout": None}

    def finish(payload, error: bool = False) -> None:
        if state["done"]:
            return
        state["done"] = True
        subscription = state.get("sub")
        if subscription:
            try:
                bus.signal_unsubscribe(int(subscription))
            except Exception:
                pass
        timeout_id = state.get("timeout")
        if timeout_id:
            try:
                GLib.source_remove(int(timeout_id))
            except Exception:
                pass
        if error:
            on_error()
        else:
            on_result(payload)

    def on_response(conn, sender, path, iface, signal, params) -> None:
        try:
            response, results = params.unpack()
        except (TypeError, ValueError):
            finish(None, error=True)
            return
        if response == 1:  # user cancelled
            finish(None)
        elif response != 0:
            finish(None, error=True)
        else:
            finish(results)

    state["sub"] = bus.signal_subscribe(
        PORTAL_BUS,
        REQUEST_IFACE,
        "Response",
        predicted,
        None,
        Gio.DBusSignalFlags.NONE,
        on_response,
    )

    def on_timeout() -> bool:
        state["timeout"] = None
        finish(None, error=True)
        return False

    state["timeout"] = GLib.timeout_add_seconds(timeout_seconds, on_timeout)

    def subscribe(handle: str) -> None:
        subscription = state.get("sub")
        if subscription:
            try:
                bus.signal_unsubscribe(int(subscription))
            except Exception:
                pass
        state["sub"] = bus.signal_subscribe(
            PORTAL_BUS,
            REQUEST_IFACE,
            "Response",
            handle,
            None,
            Gio.DBusSignalFlags.NONE,
            on_response,
        )

    def on_call(conn, result) -> None:
        try:
            reply = conn.call_finish(result)
            handle = str(reply.unpack()[0])
        except GLib.Error:
            finish(None, error=True)
            return
        if handle != predicted:
            subscribe(handle)

    try:
        bus.call(
            PORTAL_BUS,
            PORTAL_PATH,
            PORTAL_IFACE,
            method,
            GLib.Variant("(sa{sv})", ("", options)),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            on_call,
        )
    except GLib.Error:
        finish(None, error=True)


def pick_color_portal(on_result, on_error) -> None:
    """Buka color picker native GNOME lewat portal (tanpa screenshot sendiri)."""

    def handle(results) -> None:
        if not isinstance(results, dict):
            on_result(None)
            return
        rgb = _rgb_from_variant(results.get("color"))
        on_result(rgb)

    _call_portal("PickColor", {}, handle, on_error)


def pick_color_shell(on_result, on_error) -> None:
    """GNOME Shell 46+ punya PickColor native (ada loupe + hex)."""
    bus = session_bus()
    if bus is None:
        on_error()
        return

    def on_call(conn, result) -> None:
        try:
            reply = conn.call_finish(result)
            results = reply.unpack()[0]
        except GLib.Error:
            on_error()
            return
        if not isinstance(results, dict):
            on_result(None)
            return
        rgb = _rgb_from_variant(results.get("color"))
        on_result(rgb)

    try:
        bus.call(
            SHELL_BUS,
            SHELL_PATH,
            SHELL_IFACE,
            "PickColor",
            None,
            None,
            Gio.DBusCallFlags.NONE,
            600_000,
            None,
            on_call,
        )
    except GLib.Error:
        on_error()


def screenshot_portal(on_result, on_error) -> None:
    """Ambil screenshot non-interaktif lewat portal. on_result(path|None)."""

    options = {
        "interactive": GLib.Variant("b", False),
        "modal": GLib.Variant("b", False),
    }

    def handle(results) -> None:
        if not isinstance(results, dict):
            on_result(None)
            return
        uri = results.get("uri")
        if not uri:
            on_result(None)
            return
        try:
            path = Gio.File.new_for_uri(str(uri.unpack() if hasattr(uri, "unpack") else uri)).get_path()
        except Exception:
            path = None
        on_result(path)

    _call_portal("Screenshot", options, handle, on_error, timeout_seconds=90)