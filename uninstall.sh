#!/usr/bin/env bash
# Copot PowerTools yang dipasang oleh install.sh.
#
#   ./uninstall.sh            hapus aplikasi, ikon, shortcut, autostart
#   ./uninstall.sh --purge    hapus juga config dan seluruh data (riwayat clipboard)
#
# Riwayat clipboard disimpan di ~/.local/share/powertools/clipboard.jsonl.
# Tanpa --purge, berkas tersebut tetap ada beserta config di ~/.config/powertools.

set -euo pipefail

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
APP_ROOT="$DATA_HOME/powertools"
BIN_DIR="$APP_ROOT/bin"
EXT_DIR="$DATA_HOME/gnome-shell/extensions/powertools@local"
SCHEMA_DIR="$DATA_HOME/glib-2.0/schemas"
DESKTOP_DIR="$DATA_HOME/applications"
ICON_DIR="$DATA_HOME/icons/hicolor/scalable/apps"
AUTOSTART_DIR="$CONFIG_HOME/autostart"
SYSTEMD_USER_DIR="$CONFIG_HOME/systemd/user"
DBUS_SERVICE_DIR="$DATA_HOME/dbus-1/services"
DESKTOP_ID="io.github.powertools.App"

PURGE=0
for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        -h|--help)
            sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Pilihan tidak dikenal: $arg" >&2
            exit 2
            ;;
    esac
done

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

if [ -f "$SYSTEMD_USER_DIR/powertools.service" ] && command -v systemctl >/dev/null 2>&1; then
    info "Melepas autostart systemd"
    systemctl --user disable --now powertools.service >/dev/null 2>&1 || true
fi

if [ -x "$BIN_DIR/powertools" ]; then
    info "Menghentikan proses PowerTools"
    "$BIN_DIR/powertools" quit >/dev/null 2>&1 || true
    sleep 0.5
fi

if command -v gnome-extensions >/dev/null 2>&1; then
    info "Menonaktifkan ekstensi GNOME"
    gnome-extensions disable powertools@local >/dev/null 2>&1 || true
fi

info "Menghapus berkas aplikasi"
rm -rf "$EXT_DIR"
rm -f "$DESKTOP_DIR/$DESKTOP_ID.desktop"
rm -f "$ICON_DIR/$DESKTOP_ID.svg"
rm -f "$AUTOSTART_DIR/powertools.desktop"
rm -f "$SYSTEMD_USER_DIR/powertools.service"
rm -f "$DBUS_SERVICE_DIR/$DESKTOP_ID.service"
rm -f "$SCHEMA_DIR/org.gnome.shell.extensions.powertools.gschema.xml"

if command -v dbus-send >/dev/null 2>&1; then
    dbus-send --session --dest=org.freedesktop.DBus --type=method_call / \
        org.freedesktop.DBus.ReloadConfig >/dev/null 2>&1 || true
fi

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload >/dev/null 2>&1 || true
fi

if command -v glib-compile-schemas >/dev/null 2>&1 && [ -d "$SCHEMA_DIR" ]; then
    glib-compile-schemas "$SCHEMA_DIR" >/dev/null 2>&1 || true
fi

if [ "$PURGE" -eq 1 ]; then
    info "Menghapus data dan konfigurasi (--purge)"
    rm -rf "$APP_ROOT" "$CONFIG_HOME/powertools"
else
    # Sisakan riwayat clipboard, hapus hanya kode program.
    rm -rf "$APP_ROOT/app" "$APP_ROOT/bin"
    rmdir "$APP_ROOT" 2>/dev/null || true
fi

# Kembalikan Alt+Space untuk menu jendela GNOME kalau sekarang kosong.
if command -v gsettings >/dev/null 2>&1; then
    current="$(gsettings get org.gnome.desktop.wm.keybindings activate-window-menu 2>/dev/null || echo "[]")"
    if [ "$current" = "[]" ] || [ "$current" = "@as []" ]; then
        gsettings set org.gnome.desktop.wm.keybindings activate-window-menu "['<Alt>space']"
        info "Alt+Space dikembalikan ke menu jendela GNOME"
    fi
fi

cat <<'MSG'

PowerTools sudah dicopot. Logout/login sekali agar GNOME melepas ekstensinya.
Kalau tanpa --purge, riwayat tetap ada di ~/.local/share/powertools/clipboard.jsonl
dan konfigurasi di ~/.config/powertools/config.json.
MSG