#!/usr/bin/env bash
# Pasang PowerTools untuk user saat ini (tanpa sudo).
#
#   ./install.sh                 pasang semuanya + autostart
#   ./install.sh --no-autostart  jangan jalankan otomatis saat login
#   ./install.sh --no-extension  lewati ekstensi GNOME (mis. untuk X11)
#
# Semua berkas dipasang di $XDG_DATA_HOME (~/.local/share) milik user.

set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
APP_ROOT="$DATA_HOME/powertools"
APP_DIR="$APP_ROOT/app"
BIN_DIR="$APP_ROOT/bin"
EXT_ROOT="$DATA_HOME/gnome-shell/extensions"
EXT_DIR="$EXT_ROOT/powertools@local"
SCHEMA_DIR="$DATA_HOME/glib-2.0/schemas"
DESKTOP_DIR="$DATA_HOME/applications"
ICON_DIR="$DATA_HOME/icons/hicolor/scalable/apps"
AUTOSTART_DIR="$CONFIG_HOME/autostart"
SYSTEMD_USER_DIR="$CONFIG_HOME/systemd/user"
DBUS_SERVICE_DIR="$DATA_HOME/dbus-1/services"
DESKTOP_ID="io.github.powertools.App"

WITH_AUTOSTART=1
WITH_EXTENSION=1

usage() {
    cat <<'USAGE'
Pasang PowerTools untuk user saat ini (tanpa sudo).

  ./install.sh                 pasang semuanya + autostart
  ./install.sh --no-autostart  jangan jalankan otomatis saat login
  ./install.sh --no-extension  lewati ekstensi GNOME (mis. untuk X11)
USAGE
}

for arg in "$@"; do
    case "$arg" in
        --no-autostart) WITH_AUTOSTART=0 ;;
        --no-extension) WITH_EXTENSION=0 ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Pilihan tidak dikenal: $arg" >&2
            exit 2
            ;;
    esac
done

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m  ! \033[0m%s\n' "$*"; }
ok()    { printf '\033[1;32m  ✓ \033[0m%s\n' "$*"; }

# ------------------------------------------------------------------ prasyarat
info "Memeriksa prasyarat"
if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 tidak ditemukan. Pasang dengan: sudo apt install python3" >&2
    exit 1
fi

PYTHON_BIN="$(command -v python3)"
if ! "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gio, GLib, Gdk
PY
then
    cat >&2 <<'MSG'
PyGObject/GTK4 belum tersedia. Pasang dulu:

  sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 libglib2.0-bin

MSG
    exit 1
fi
ok "Python $("$PYTHON_BIN" -c 'import sys; print(sys.version.split()[0])') + GTK4"

if ! command -v glib-compile-schemas >/dev/null 2>&1; then
    warn "glib-compile-schemas tidak ada; shortcut default mungkin tidak terbaca."
    warn "Pasang dengan: sudo apt install libglib2.0-bin"
fi

# ---------------------------------------------------------------- aplikasi
info "Memasang aplikasi ke $APP_DIR"
mkdir -p "$APP_ROOT" "$BIN_DIR"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
cp -r "$SRC/powertools" "$APP_DIR/powertools"
find "$APP_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
"$PYTHON_BIN" -m py_compile "$APP_DIR"/powertools/*.py
find "$APP_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

cat > "$BIN_DIR/powertools" <<EOF
#!/usr/bin/env bash
# Pembungkus PowerTools (dibuat otomatis oleh install.sh).
set -euo pipefail
BASE="\$(cd "\$(dirname "\$(readlink -f "\${BASH_SOURCE[0]}")")/.." && pwd)"
export PYTHONPATH="\$BASE/app\${PYTHONPATH:+:\$PYTHONPATH}"
# Renderer cairo jauh lebih hemat RAM untuk UI statis seperti ini.
# Hapus/ubah variabel ini kalau ingin akselerasi GPU penuh.
: "\${GSK_RENDERER:=cairo}"
export GSK_RENDERER
PYTHON="\${POWERTOOLS_PYTHON:-/usr/bin/python3}"
[ -x "\$PYTHON" ] || PYTHON="\$(command -v python3)"
exec "\$PYTHON" -m powertools "\$@"
EOF
chmod 755 "$BIN_DIR/powertools"
ok "Perintah: $BIN_DIR/powertools"

# Penjaga autostart: dipakai XDG autostart. Kalau systemd sudah mengurus
# layanan, skrip ini tidak melakukan apa-apa (tidak ada proses ganda).
cat > "$BIN_DIR/powertools-autostart" <<'EOF'
#!/usr/bin/env bash
# Dibuat otomatis oleh install.sh — dipakai entri autostart GNOME.
# Tujuan: memastikan tepat satu layanan PowerTools hidup saat login.
set -u
BASE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
BIN="$BASE/bin/powertools"

if command -v systemctl >/dev/null 2>&1 &&
   systemctl --user is-enabled --quiet powertools.service 2>/dev/null; then
    # systemd yang bertanggung jawab; tunggu sebentar sampai ia jalan.
    for _ in $(seq 1 20); do
        systemctl --user is-active --quiet powertools.service 2>/dev/null && exit 0
        sleep 0.25
    done
    # systemd gagal/tidak tersedia -> jalur cadangan di bawah.
fi

exec "$BIN" daemon
EOF
chmod 755 "$BIN_DIR/powertools-autostart"

# ------------------------------------------------------- ikon + launcher
info "Memasang ikon dan entri menu"
mkdir -p "$DESKTOP_DIR" "$ICON_DIR"
install -m 644 "$SRC/assets/$DESKTOP_ID.svg" "$ICON_DIR/$DESKTOP_ID.svg"

cat > "$DESKTOP_DIR/$DESKTOP_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=PowerTools
GenericName=Clipboard History & Launcher
Comment=Riwayat clipboard (Win+V), launcher (Alt+Space), color picker, dan lirik
Exec=$BIN_DIR/powertools launcher
Icon=$DESKTOP_ID
Terminal=false
Categories=Utility;
Keywords=clipboard;launcher;color;picker;lirik;lyrics;riwayat;warna;discord;
StartupNotify=false
StartupWMClass=powertools
Actions=clipboard;color;lyrics;settings;

[Desktop Action clipboard]
Name=Buka Riwayat Clipboard
Exec=$BIN_DIR/powertools clipboard

[Desktop Action color]
Name=Ambil Warna dari Layar
Exec=$BIN_DIR/powertools color

[Desktop Action lyrics]
Name=Buka Lirik Mengambang
Exec=$BIN_DIR/powertools lyrics

[Desktop Action settings]
Name=Pengaturan PowerTools
Exec=$BIN_DIR/powertools settings
EOF

command -v update-desktop-database >/dev/null 2>&1 &&
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 &&
    gtk-update-icon-cache -f -t "$DATA_HOME/icons/hicolor" >/dev/null 2>&1 || true
ok "Menu aplikasi: $DESKTOP_DIR/$DESKTOP_ID.desktop"

# -------------------------------------------------------------- ekstensi
if [ "$WITH_EXTENSION" -eq 1 ]; then
    info "Memasang ekstensi GNOME Shell (jembatan clipboard + hotkey)"
    mkdir -p "$EXT_ROOT"
    rm -rf "$EXT_DIR"
    cp -r "$SRC/gnome-extension/powertools@local" "$EXT_DIR"

    if command -v glib-compile-schemas >/dev/null 2>&1; then
        glib-compile-schemas "$EXT_DIR/schemas"
        mkdir -p "$SCHEMA_DIR"
        install -m 644 \
            "$EXT_DIR/schemas/org.gnome.shell.extensions.powertools.gschema.xml" \
            "$SCHEMA_DIR/" 2>/dev/null || true
        glib-compile-schemas "$SCHEMA_DIR" >/dev/null 2>&1 || \
            warn "Schema global gagal dikompilasi (shortcut tetap jalan lewat folder ekstensi)."
    fi
    ok "Ekstensi: $EXT_DIR"

    # Alt+Space dipakai GNOME untuk menu jendela; lepaskan supaya launcher jalan.
    if command -v gsettings >/dev/null 2>&1; then
        current="$(gsettings get org.gnome.desktop.wm.keybindings activate-window-menu 2>/dev/null || echo "[]")"
        if printf '%s' "$current" | grep -q "'<Alt>space'"; then
            updated="$(printf '%s' "$current" \
                | sed "s/'<Alt>space', //; s/, '<Alt>space'//; s/'<Alt>space'//")"
            gsettings set org.gnome.desktop.wm.keybindings activate-window-menu "$updated"
            ok "Alt+Space GNOME dilepas (dipakai PowerTools)"
        fi
    fi

    # Lepas blokir yang mungkin tertinggal dan daftarkan ulang. Ini yang
    # membuat `Jembatan aktif: tidak` bisa pulih tanpa logout.
    "$BIN_DIR/powertools" bridge --no-wait >/dev/null 2>&1 || true

    if command -v gnome-extensions >/dev/null 2>&1; then
        state=""
        for _ in 1 2 3 4; do
            gnome-extensions enable powertools@local >/dev/null 2>&1 || true
            sleep 1
            state="$(gnome-extensions info powertools@local 2>/dev/null \
                | awk -F': *' '/State/ {print $2; exit}' || true)"
            [ "${state:-}" = "ACTIVE" ] && break
        done
        if [ "${state:-}" = "ACTIVE" ]; then
            ok "Ekstensi aktif"
        else
            warn "Ekstensi terpasang tapi belum aktif (status: ${state:-tidak diketahui})."
            warn "Logout lalu login lagi, kemudian jalankan: gnome-extensions enable powertools@local"
        fi
    else
        warn "gnome-extensions tidak ditemukan; aktifkan manual dari GNOME Extensions."
    fi
else
    info "Melewati ekstensi GNOME (--no-extension)"
fi

# ------------------------------------------------------------- autostart
# Autostart dibuat berlapis supaya tidak mungkin perlu dijalankan manual:
#   1. systemd user service  -> dijalankan saat login, restart otomatis.
#   2. XDG autostart GNOME   -> cadangan kalau systemd user tidak dipakai.
#   3. aktivasi D-Bus        -> app hidup sendiri begitu fitur dipakai.
#   4. ekstensi GNOME        -> menyalakan layanan saat shell dimuat.
if [ "$WITH_AUTOSTART" -eq 1 ]; then
    info "Mengaktifkan autostart (systemd + GNOME + D-Bus)"

    start_daemon() {
        if command -v setsid >/dev/null 2>&1; then
            setsid "$BIN_DIR/powertools-autostart" >/dev/null 2>&1 &
        else
            "$BIN_DIR/powertools-autostart" >/dev/null 2>&1 &
        fi
        disown 2>/dev/null || true
    }

    SYSTEMD_OK=0
    mkdir -p "$SYSTEMD_USER_DIR"
    cat > "$SYSTEMD_USER_DIR/powertools.service" <<EOF
[Unit]
Description=PowerTools (riwayat clipboard, launcher, color picker, lirik)
After=graphical-session.target
BindsTo=graphical-session.target
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStart="$BIN_DIR/powertools" daemon
Restart=on-failure
RestartSec=2
# Beri kesempatan display/wayland siap sebelum menyerah.
TimeoutStartSec=30

[Install]
WantedBy=graphical-session.target
EOF
    ok "systemd user service: $SYSTEMD_USER_DIR/powertools.service"

    if command -v systemctl >/dev/null 2>&1 &&
       systemctl --user show-environment >/dev/null 2>&1; then
        systemctl --user daemon-reload >/dev/null 2>&1 || true
        if systemctl --user enable powertools.service >/dev/null 2>&1; then
            SYSTEMD_OK=1
            ok "systemd user service: enabled + restart otomatis"
        else
            warn "systemd user service gagal di-enable; memakai jalur cadangan."
        fi
    else
        warn "systemd user tidak tersedia sekarang; unit dipasang untuk login berikutnya."
    fi

    # Hentikan instance lama (kalau ada) supaya tidak bentrok dengan systemd.
    "$BIN_DIR/powertools" quit >/dev/null 2>&1 || true
    sleep 0.3

    if [ "$SYSTEMD_OK" -eq 1 ]; then
        if systemctl --user restart powertools.service >/dev/null 2>&1; then
            ok "Layanan dijalankan sekarang lewat systemd"
        else
            warn "systemd belum bisa menjalankan layanan; akan dicoba lagi saat login."
            start_daemon
        fi
    else
        start_daemon
        ok "Layanan dijalankan sekarang (jalur cadangan)"
    fi

    # Lapis 2: autostart XDG (GNOME). Guard memakai systemd bila tersedia.
    mkdir -p "$AUTOSTART_DIR"
    cat > "$AUTOSTART_DIR/powertools.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=PowerTools (layanan)
Comment=Menjaga popup clipboard, launcher, dan color picker tetap instan
Exec=$BIN_DIR/powertools-autostart
Icon=$DESKTOP_ID
Terminal=false
NoDisplay=true
Hidden=false
X-GNOME-Autostart-enabled=true
EOF
    ok "Autostart GNOME: $AUTOSTART_DIR/powertools.desktop"

    # Lapis 3: aktivasi D-Bus (siapa pun yang memanggil app akan memulai layanan).
    mkdir -p "$DBUS_SERVICE_DIR"
    cat > "$DBUS_SERVICE_DIR/$DESKTOP_ID.service" <<EOF
[D-BUS Service]
Name=$DESKTOP_ID
Exec=$BIN_DIR/powertools daemon
EOF
    ok "Aktivasi D-Bus: $DBUS_SERVICE_DIR/$DESKTOP_ID.service"

    # Bus sesi yang sudah berjalan menyimpan daftar service saat start;
    # minta reload supaya aktivasi D-Bus langsung berlaku tanpa logout.
    if command -v dbus-send >/dev/null 2>&1; then
        dbus-send --session --dest=org.freedesktop.DBus --type=method_call / \
            org.freedesktop.DBus.ReloadConfig >/dev/null 2>&1 || true
    fi

    # Verifikasi nyata: layanan harus benar-benar hidup sekarang.
    for _ in $(seq 1 20); do
        if "$BIN_DIR/powertools" doctor 2>/dev/null | grep -q "Aplikasi berjalan: ya"; then
            ok "Terverifikasi: layanan berjalan dan siap menerima pintasan"
            break
        fi
        sleep 0.5
    done
    if ! "$BIN_DIR/powertools" doctor 2>/dev/null | grep -q "Aplikasi berjalan: ya"; then
        warn "Layanan belum merespons saat instalasi; akan otomatis jalan saat login."
    fi
else
    info "Melewati autostart (--no-autostart)"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user disable --now powertools.service >/dev/null 2>&1 || true
    fi
    rm -f "$SYSTEMD_USER_DIR/powertools.service" \
          "$AUTOSTART_DIR/powertools.desktop" \
          "$DBUS_SERVICE_DIR/$DESKTOP_ID.service"
    command -v systemctl >/dev/null 2>&1 && systemctl --user daemon-reload >/dev/null 2>&1 || true
fi

cat <<MSG

Selesai! Autostart sudah aktif berlapis:
  • systemd user service (mulai otomatis saat login, restart kalau mati)
  • autostart GNOME (cadangan)
  • aktivasi D-Bus (layanan hidup sendiri saat fitur dipakai)

Tidak perlu menjalankan apa pun secara manual setiap login.

Pintasan default:
  Super+V          riwayat clipboard
  Alt+Space        launcher (PowerToys Run)
  Super+Shift+C    color picker
  Super+Shift+L    lirik mengambang

Kalau ekstensi baru saja dipasang, logout lalu login sekali agar GNOME
memuatnya. Setelah itu cek dengan:

  $BIN_DIR/powertools doctor
MSG