#!/bin/sh
# Rekam ulang semua GIF di docs/gifs/.
#
# Butuh sesi grafis (GTK4 + PyGObject) dan Pillow. Tiap skrip membuka jendela
# PowerTools sungguhan di direktori config sementara, merender frame dengan
# Gsk.CairoRenderer, lalu menulis GIF-nya. Lama total sekitar 5-10 menit.
set -e
cd "$(dirname "$0")"

for script in gif_*.py; do
    printf '\n== %s\n' "$script"
    python3 "$script"
done
