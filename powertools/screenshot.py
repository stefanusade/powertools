"""Pengambilan screenshot untuk color picker fallback."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from gi.repository import Gio, GLib

from . import portal
from .dbusutil import session_bus


def _temp_path() -> Path:
    return Path(GLib.get_tmp_dir()) / f"powertools-shot-{os.getpid()}-{int(time.time() * 1000)}.png"


def _valid(path: Path | None) -> bool:
    try:
        return path is not None and path.exists() and path.stat().st_size > 0
    except OSError:
        return False


def _shell_screenshot(path: Path, on_done, on_fail) -> None:
    bus = session_bus()
    if bus is None:
        on_fail()
        return

    def on_call(conn, result) -> None:
        try:
            success, used = conn.call_finish(result).unpack()
        except GLib.Error:
            on_fail()
            return
        candidate = Path(used) if used else path
        if success and _valid(candidate):
            on_done(candidate)
        else:
            on_fail()

    try:
        bus.call(
            "org.gnome.Shell.Screenshot",
            "/org/gnome/Shell/Screenshot",
            "org.gnome.Shell.Screenshot",
            "Screenshot",
            GLib.Variant("(bbs)", (False, False, str(path))),
            None,
            Gio.DBusCallFlags.NONE,
            15_000,
            None,
            on_call,
        )
    except GLib.Error:
        on_fail()


def _portal_screenshot(on_done, on_fail) -> None:
    def handle(path: str | None) -> None:
        if not path:
            on_fail()
            return
        source = Path(path)
        if not _valid(source):
            on_fail()
            return
        target = _temp_path()
        try:
            if source.parent == target.parent:
                os.replace(source, target)
            else:
                shutil.copy2(source, target)
                source.unlink(missing_ok=True)
        except OSError:
            target = source
        on_done(target)

    portal.screenshot_portal(handle, on_fail)


def _command_screenshot(path: Path, on_done, on_fail) -> None:
    candidates = [
        ("grim", ["grim", "-t", "png"]),
        ("gnome-screenshot", ["gnome-screenshot", "-f"]),
        ("spectacle", ["spectacle", "-b", "-n", "-o"]),
        ("scrot", ["scrot", "-o"]),
        ("import", ["import", "-window", "root"]),
    ]

    def worker() -> None:
        result: Path | None = None
        for binary, command in candidates:
            if not shutil.which(binary):
                continue
            try:
                subprocess.run(
                    command + [str(path)],
                    check=False,
                    timeout=15,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if _valid(path):
                result = path
                break
        if result is not None:
            GLib.idle_add(on_done, result)
        else:
            GLib.idle_add(on_fail)

    threading.Thread(target=worker, daemon=True).start()


def capture_screen(callback) -> None:
    """Cari cara tercepat yang tersedia; callback(path | None)."""
    path = _temp_path()
    state = {"done": False}

    def finish(result: Path | None) -> None:
        if state["done"]:
            return
        state["done"] = True
        callback(result)

    _shell_screenshot(
        path,
        lambda p: finish(p),
        lambda: _portal_screenshot(
            lambda p: finish(p),
            lambda: _command_screenshot(path, lambda p: finish(p), lambda: finish(None)),
        ),
    )