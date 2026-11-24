"""Entry point CLI.

CLI sengaja tidak meng-import GTK supaya aktivasi terasa instan:
kalau aplikasi sudah jalan, cukup kirim D-Bus ActivateAction.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from gi.repository import Gio, GLib

from . import config
from .config import parse_config_value
from .dbusutil import name_has_owner, session_bus

UI_ACTIONS = ("clipboard", "launcher", "color", "lyrics", "settings")
EXT_UUID = "powertools@local"
BRIDGE_NAME = "org.gnome.Shell.Extensions.PowerTools"
HELP = f"""{config.APP_NAME} {config.VERSION} — clipboard history, launcher, color picker, lirik, listening to Discord.

Pemakaian:
  powertools                  Buka launcher (Alt+Space)
  powertools clipboard        Buka riwayat clipboard (Win+V)
  powertools color            Ambil warna dari layar
  powertools lyrics           Buka lirik mengambang
  powertools settings         Buka pengaturan (GUI)
  powertools daemon           Jalankan di background tanpa membuka window
  powertools quit             Hentikan proses background
  powertools bridge           Perbaiki/aktifkan jembatan ekstensi GNOME
  powertools config show      Tampilkan konfigurasi
  powertools config get KEY   Baca satu nilai (contoh: clipboard.max_items)
  powertools config set KEY V Ubah satu nilai
  powertools doctor           Diagnosa instalasi
  powertools version          Cetak versi

Shortcut default (lewat ekstensi GNOME):
  <Super>V         riwayat clipboard
  <Alt>Space       launcher
  <Super><Shift>C  color picker
  <Super><Shift>L  lirik mengambang
"""


def _call_action(action: str) -> bool:
    """Kirim ActivateAction ke instance yang sudah berjalan."""
    try:
        bus = session_bus()
        if bus is None:
            return False
        if not name_has_owner(config.APP_ID):
            return False

        platform: dict[str, GLib.Variant] = {}
        token = os.environ.get("XDG_ACTIVATION_TOKEN") or os.environ.get("DESKTOP_STARTUP_ID")
        if token:
            platform["desktop-startup-id"] = GLib.Variant("s", token)

        bus.call_sync(
            config.APP_ID,
            "/" + config.APP_ID.replace(".", "/"),
            "org.freedesktop.Application",
            "ActivateAction",
            GLib.Variant("(sava{sv})", (action, [], platform)),
            None,
            Gio.DBusCallFlags.NONE,
            3000,
            None,
        )
        return True
    except GLib.Error:
        return False


def _spawn(action: str) -> None:
    """Jalankan aplikasi secara langsung di proses ini (tanpa fork tambahan)."""
    from .service import PowerToolsApp

    app = PowerToolsApp(initial_action=action)
    app.run([sys.argv[0]])


def _run_action(action: str) -> int:
    if _call_action(action):
        return 0
    if action == "quit":
        return 0
    _spawn(action)
    return 0


def _run_daemon() -> int:
    """Tetap hidup di background tanpa membuka window apa pun."""
    if name_has_owner(config.APP_ID):
        return 0
    _spawn("daemon")
    return 0


# --------------------------------------------------------------------- config
def _print_config() -> int:
    cfg = config.Config()
    cfg.ensure_exists()
    print(f"# {cfg.path}")
    import json

    print(json.dumps(cfg.data, indent=2, ensure_ascii=False))
    return 0


def _mirror_module_setting(key: str, value) -> None:
    """Samakan saklar lama di gsettings saat modul media diubah dari CLI.

    GUI melakukan hal yang sama; tanpa ini `config set modules.media true`
    bisa tetap tersembunyi karena key gsettings lama masih `false`.
    """
    if key != "modules.media" or not isinstance(value, bool):
        return
    if not shutil.which("gsettings"):
        return
    try:
        subprocess.run(
            ["gsettings", "set", "org.gnome.shell.extensions.powertools",
             "media-island-enabled", "true" if value else "false"],
            capture_output=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _config_command(args: list[str]) -> int:
    if not args or args[0] == "show":
        return _print_config()
    sub = args[0]
    if sub == "get":
        if len(args) < 2:
            print("Butuh KEY, contoh: powertools config get clipboard.max_items", file=sys.stderr)
            return 2
        cfg = config.Config()
        value = cfg.get(args[1], "<tidak ada>")
        print(value if not isinstance(value, (dict, list)) else __import__("json").dumps(value))
        return 0
    if sub == "set":
        if len(args) < 3:
            print("Butuh KEY dan VALUE", file=sys.stderr)
            return 2
        cfg = config.Config()
        cfg.set(args[1], parse_config_value(" ".join(args[2:])))
        cfg.save()
        _mirror_module_setting(args[1], cfg.get(args[1]))
        print(f"{args[1]} = {cfg.get(args[1])}")
        print("Perubahan modul/opsi biasanya langsung berlaku; kalau belum, "
              "jalankan `powertools quit` lalu buka lagi.")
        return 0
    if sub == "edit":
        cfg = config.Config()
        cfg.ensure_exists()
        try:
            Gio.AppInfo.launch_default_for_uri(config.CONFIG_PATH.as_uri(), None)
        except GLib.Error as exc:
            print(exc, file=sys.stderr)
            return 1
        return 0
    print(f"Subperintah config tidak dikenal: {sub}", file=sys.stderr)
    return 2


# --------------------------------------------------------------------- doctor
def _name_owned(name: str) -> bool:
    return name_has_owner(name)


def _run_capture(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=5, check=False
        )
        return (result.stdout or result.stderr).strip()
    except (OSError, subprocess.TimeoutExpired):
        return "?"


def _detected_source() -> tuple[str, str, str] | None:
    """Sumber MPRIS yang sedang aktif + judul lagu + URL covernya.

    Dipakai `powertools doctor` supaya pengguna tahu kunci apa yang harus
    dipetakan ke Application ID Discord (mis. ``spotify``).
    """
    try:
        from .dbusutil import session_bus
        from .mpris import BUS_PREFIX, OBJECT_PATH
        from .sources import detect_source
    except Exception:
        return None

    bus = session_bus()
    if bus is None:
        return None

    try:
        reply = bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "ListNames",
            None,
            GLib.VariantType.new("(as)"),
            Gio.DBusCallFlags.NONE,
            1500,
            None,
        )
        names = reply.unpack()[0]
    except Exception:
        return None

    for name in names or ():
        owner = str(name)
        if not owner.startswith(BUS_PREFIX + ".") or "playerctld" in owner:
            continue
        try:
            reply = bus.call_sync(
                owner,
                OBJECT_PATH,
                "org.freedesktop.DBus.Properties",
                "Get",
                GLib.Variant(
                    "(ss)", ("org.mpris.MediaPlayer2.Player", "Metadata")
                ),
                GLib.VariantType.new("(v)"),
                Gio.DBusCallFlags.NONE,
                1500,
                None,
            )
            metadata = reply.unpack()[0]
        except Exception:
            continue
        if isinstance(metadata, GLib.Variant):
            try:
                metadata = metadata.recursiveUnpack()
            except Exception:
                continue
        if not isinstance(metadata, dict):
            continue
        title = str(metadata.get("xesam:title") or "").strip()
        if not title:
            continue
        album = str(metadata.get("xesam:album") or "").strip()
        url = str(metadata.get("xesam:url") or "").strip()
        art = str(metadata.get("mpris:artUrl") or "").strip()
        source = detect_source(owner, title, album, url=url)
        return source, title, art
    return None


def _doctor() -> int:
    from .store import ClipStore

    print(f"{config.APP_NAME} {config.VERSION}")
    print(f"Python           : {sys.version.split()[0]} ({sys.executable})")
    print(f"Sesi             : {os.environ.get('XDG_SESSION_TYPE', '?')} / "
          f"{os.environ.get('XDG_CURRENT_DESKTOP', '?')}")

    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        print(f"GTK              : {Gtk.MAJOR_VERSION}.{Gtk.MINOR_VERSION}.{Gtk.MICRO_VERSION}")
    except Exception as exc:  # pragma: no cover - tergantung sistem
        print(f"GTK              : TIDAK ADA ({exc})")

    print(f"GNOME Shell      : {_run_capture(['gnome-shell', '--version']) or '?'}")
    print(f"Ekstensi terpasang: {'ya' if _extension_installed() else 'TIDAK'}")
    bridge_ok = _name_owned(BRIDGE_NAME)
    print(f"Jembatan aktif   : {'ya' if bridge_ok else 'tidak'}")
    if bridge_ok:
        print(f"Deteksi PWA      : {_windows_bridge_status()}")
    print(f"Aplikasi berjalan: {'ya' if _name_owned(config.APP_ID) else 'tidak'}")
    autostart, has_autostart = _autostart_status()
    print(f"Autostart        : {autostart}")
    if not has_autostart:
        print("Catatan          : autostart belum terpasang. Jalankan ./install.sh lagi.")
    if not bridge_ok:
        print(f"Penyebab         : {_bridge_reason()}")
        print("Solusi           : powertools bridge")

    if shutil.which("gsettings"):
        schema = "org.gnome.shell.extensions.powertools"
        keys = ("toggle-clipboard", "toggle-launcher", "pick-color", "toggle-lyrics")
        values = []
        for key in keys:
            out = _run_capture(["gsettings", "get", schema, key])
            values.append(f"{key}={out}")
        print("Shortcut         : " + ", ".join(values))

    cfg = config.Config()
    try:
        max_age = float(cfg.get("clipboard.max_age_minutes", 0) or 0)
        max_items = int(cfg.get("clipboard.max_items", 300))
        max_images = int(cfg.get("clipboard.max_image_items", 60))
    except (TypeError, ValueError):
        max_age, max_items, max_images = 0.0, 300, 60
    store = ClipStore(max_items=max_items, max_age_seconds=max_age * 60)
    stats = store.stats()
    print(
        f"Data             : {config.DATA_DIR} "
        f"({stats['items']} item, {stats['images']} gambar, {stats['bytes']} byte)"
    )
    age_label = "tidak pernah" if max_age <= 0 else f"{max_age:g} menit"
    auto_label = "ya" if cfg.get("clipboard.auto_paste", True) else "tidak"
    print(
        "Clipboard        : "
        f"maks {max_items} teks, {max_images} gambar, "
        f"kedaluwarsa {age_label}, auto-paste {auto_label}"
    )
    print(f"Config           : {config.CONFIG_PATH}")
    modules = ", ".join(
        f"{name}={'aktif' if cfg.module_enabled(name) else 'mati'}"
        for name in config.MODULES
    )
    print(f"Modul            : {modules}")
    hide_taskbar = cfg.get("lyrics.hide_from_taskbar", True)
    if not isinstance(hide_taskbar, bool):
        hide_taskbar = True
    print(
        "Lirik taskbar    : "
        + (
            "disembunyikan (ikon panel tetap bisa membuka)"
            if hide_taskbar
            else "tampil seperti jendela biasa"
        )
    )

    if cfg.module_enabled("discord"):
        app_id = str(cfg.get("discord.app_id", "") or "").strip()
        from .discord import socket_candidates, valid_app_id
        from .sources import (
            APP_PRESETS,
            BUILTIN_APP_IDS,
            preset_for_app_id,
            resolve_app_id,
            source_label,
        )

        mapping = cfg.get("discord.app_ids", {})
        if not isinstance(mapping, dict):
            mapping = {}
        valid_map = {
            str(key).strip().lower(): str(value).strip()
            for key, value in mapping.items()
            if valid_app_id(value)
        }
        use_builtin = cfg.get("discord.use_builtin_ids", True)
        if not isinstance(use_builtin, bool):
            use_builtin = True

        try:
            found = any(path.exists() for path in socket_candidates())
        except OSError:
            found = False
        print(
            "Discord          : aktif "
            f"(socket {'ditemukan' if found else 'belum ada'})"
        )
        if valid_map:
            pairs = []
            for key, value in sorted(valid_map.items()):
                preset = preset_for_app_id(value)
                name = preset["label"] if preset else "ID lama"
                pairs.append(f"{source_label(key)}={name}")
            print(f"Discord sumber   : {', '.join(pairs)}")
        # Preset per sumber selalu berlaku; saklar hanya mengatur cadangan.
        pairs = ", ".join(
            f"{preset['label']}={preset['app_id']}"
            for preset in APP_PRESETS
        )
        print(f"Discord preset   : {pairs}")
        if not use_builtin:
            print("Discord cadangan : tidak dikirim untuk sumber tanpa preset")

        detected = _detected_source()
        if detected is not None:
            source, title, art = detected
            app_for_source = resolve_app_id(
                source,
                app_ids=valid_map,
                app_id=app_id,
                use_builtin=use_builtin,
            )
            if source in valid_map:
                origin = "pilihan di Pengaturan"
            elif source in BUILTIN_APP_IDS:
                origin = "preset sumber"
            elif valid_app_id(app_id):
                origin = "ID lama"
            else:
                origin = "preset default"
            preset = preset_for_app_id(app_for_source)
            preset_name = f" — {preset['label']}" if preset else ""
            print(
                f"Sumber terdeteksi: {source_label(source)} ({source}) "
                f"\"{title}\" — {origin}{preset_name}, "
                f"app {app_for_source or 'tidak ada'}"
            )
            fallback = str(cfg.get("discord.fallback_asset", "") or "").strip()
            if not bool(cfg.get("discord.show_album_art", True)):
                print("Cover            : dimatikan (show_album_art=false)")
            elif art.startswith(("http://", "https://")):
                print("Cover            : http(s) — langsung tampil di Discord")
            elif fallback:
                print(f"Cover            : file lokal → pakai asset \"{fallback}\"")
            else:
                print(
                    "Cover            : file lokal (tidak bisa diambil Discord) — "
                    "unggah Art Asset lalu set discord.fallback_asset"
                )
    else:
        print("Discord          : mati")

    tools = [name for name in ("wl-paste", "xclip", "grim", "gnome-screenshot", "import") if shutil.which(name)]
    print(f"Tool screenshot  : {', '.join(tools) if tools else '-'}")
    print(f"Portal           : {'ya' if _name_owned('org.freedesktop.portal.Desktop') else 'tidak'}")
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" and not _name_owned(
        BRIDGE_NAME
    ):
        print("Catatan          : di Wayland, clipboard otomatis butuh ekstensi GNOME aktif.")
    return 0


def _windows_bridge_status() -> str:
    """Apakah ekstensi yang **berjalan** sudah bisa memberi daftar jendela.

    Kode ekstensi hanya dimuat saat login, jadi setelah memperbarui PowerTools
    metode ini belum ada sampai pengguna logout/login sekali.
    """
    try:
        from .dbusutil import bridge_windows_available
    except Exception:
        return "tidak diketahui"
    try:
        if bridge_windows_available():
            return "ya (jendela + aplikasi web)"
    except Exception:
        pass
    return ("belum — jalankan ./install.sh lalu logout/login sekali "
            "(kode ekstensi lama masih berjalan)")


def _extension_installed() -> bool:
    candidates = [
        Path.home() / ".local/share/gnome-shell/extensions/powertools@local",
        Path("/usr/share/gnome-shell/extensions/powertools@local"),
    ]
    return any(path.exists() for path in candidates)


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")


def _autostart_status() -> tuple[str, bool]:
    """Ringkas semua lapisan autostart yang terpasang."""
    config_home = _config_home()
    data_home = _data_home()
    layers: list[str] = []

    unit = config_home / "systemd/user/powertools.service"
    wants = config_home / "systemd/user/graphical-session.target.wants/powertools.service"
    if unit.exists():
        layers.append("systemd" + (" (enabled)" if wants.exists() else " (belum enabled)"))

    autostart = config_home / "autostart/powertools.desktop"
    if autostart.exists():
        text = ""
        try:
            text = autostart.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        disabled = "Hidden=true" in text or "X-GNOME-Autostart-enabled=false" in text
        layers.append("GNOME" + (" (dimatikan)" if disabled else ""))

    if (data_home / "dbus-1/services" / f"{config.APP_ID}.service").exists():
        layers.append("D-Bus")

    return (", ".join(layers) if layers else "TIDAK ADA"), bool(layers)


def _gsettings_get_list(key: str) -> list[str] | None:
    """Baca key bertipe string-array dari org.gnome.shell."""
    try:
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.shell", key],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        value = ast.literal_eval(result.stdout.strip())
    except (ValueError, SyntaxError):
        return None
    if not isinstance(value, (list, tuple)):
        return None
    return [str(item) for item in value]


def _gvariant_list(items: list[str]) -> str:
    """Format daftar string sebagai literal GVariant: ['a', 'b']."""
    return "[" + ", ".join("'" + str(item).replace("'", "") + "'" for item in items) + "]"


def _gsettings_set_list(key: str, items: list[str]) -> bool:
    body = _gvariant_list(items)
    try:
        result = subprocess.run(
            ["gsettings", "set", "org.gnome.shell", key, body],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _bridge_reason() -> str:
    """Tebak kenapa jembatan ekstensi tidak aktif."""
    if not _extension_installed():
        return "ekstensi powertools@local belum terpasang"
    if not shutil.which("gsettings"):
        return "gsettings tidak tersedia, status ekstensi tidak bisa dibaca"

    if _run_capture(["gsettings", "get", "org.gnome.shell", "disable-user-extensions"]) == "true":
        return "semua ekstensi pengguna dimatikan (disable-user-extensions)"

    disabled = _gsettings_get_list("disabled-extensions") or []
    if EXT_UUID in disabled:
        return "ekstensi diblokir di daftar disabled-extensions"

    enabled = _gsettings_get_list("enabled-extensions") or []
    if EXT_UUID not in enabled:
        return "ekstensi belum terdaftar di enabled-extensions"

    return "shell belum memuat ekstensi ini (perlu logout/login sekali)"


def _bridge(wait: bool = True) -> int:
    """Nyalakan jembatan ekstensi tanpa perlu logout kalau memungkinkan."""
    print(f"{config.APP_NAME} {config.VERSION} — perbaikan jembatan GNOME")
    if _name_owned(BRIDGE_NAME):
        print("Jembatan aktif   : ya (tidak ada yang perlu diperbaiki)")
        return 0

    if not _extension_installed():
        print("Ekstensi terpasang: TIDAK", file=sys.stderr)
        print("Jalankan ./install.sh lebih dulu untuk memasang ekstensinya.", file=sys.stderr)
        return 1

    print(f"Penyebab         : {_bridge_reason()}")

    if _run_capture(["gsettings", "get", "org.gnome.shell", "disable-user-extensions"]) == "true":
        _run_capture(["gsettings", "set", "org.gnome.shell", "disable-user-extensions", "false"])
        print("Memperbaiki     : disable-user-extensions -> false")

    disabled = _gsettings_get_list("disabled-extensions")
    if disabled is not None and EXT_UUID in disabled:
        if _gsettings_set_list("disabled-extensions", [x for x in disabled if x != EXT_UUID]):
            print("Memperbaiki     : melepas blokir disabled-extensions")
        else:
            print("Gagal mengubah disabled-extensions.", file=sys.stderr)
            return 1

    enabled = _gsettings_get_list("enabled-extensions")
    if enabled is not None and EXT_UUID not in enabled:
        if _gsettings_set_list("enabled-extensions", enabled + [EXT_UUID]):
            print("Memperbaiki     : menambah ke enabled-extensions")
        else:
            print("Gagal mengubah enabled-extensions.", file=sys.stderr)
            return 1

    # Minta shell mengaktifkan sekarang juga (tidak perlu logout kalau
    # ekstensi sudah dikenal oleh shell).
    out = _run_capture(["gnome-extensions", "enable", EXT_UUID])
    if out and out != "?":
        print(f"gnome-extensions: {out}")

    for _ in range(24 if wait else 3):
        if _name_owned(BRIDGE_NAME):
            break
        time.sleep(0.25)

    if _name_owned(BRIDGE_NAME):
        print("Jembatan aktif   : ya")
        return 0

    print("Jembatan aktif   : tidak", file=sys.stderr)
    print(
        "Shell belum memuat ekstensi baru. Logout lalu login sekali, "
        "kemudian jalankan `powertools bridge` lagi kalau masih perlu.",
        file=sys.stderr,
    )
    return 1


# ----------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return _run_action("launcher")

    command = args[0]
    if command in ("-h", "--help", "help"):
        print(HELP)
        return 0
    if command in ("-v", "--version", "version"):
        print(f"{config.APP_NAME} {config.VERSION}")
        return 0
    if command in UI_ACTIONS:
        return _run_action(command)
    if command == "daemon":
        return _run_daemon()
    if command == "quit":
        running = _name_owned(config.APP_ID)
        if not running:
            print("PowerTools tidak sedang berjalan.")
            return 0
        _run_action("quit")
        return 0
    if command == "config":
        return _config_command(args[1:])
    if command == "doctor":
        return _doctor()
    if command == "bridge":
        return _bridge(wait="--no-wait" not in args[1:])
    if command.startswith("--show="):
        action = command.split("=", 1)[1]
        if action in UI_ACTIONS:
            return _run_action(action)

    print(f"Perintah tidak dikenal: {command}\n", file=sys.stderr)
    print(HELP)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())