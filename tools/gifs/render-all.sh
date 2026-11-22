#!/bin/sh
# Rekam ulang semua GIF di docs/gifs/.
#
# Butuh sesi grafis (GTK4 + PyGObject) dan Pillow. Tiap skrip membuka jendela
# PowerTools sungguhan di direktori config sementara, merender frame, lalu
# menulis GIF-nya. gif_island.py butuh GNOME Shell + gjs + ffmpeg karena
# merekam area layar (island hidup di dalam Shell, bukan jendela GTK).
set -e
cd "$(dirname "$0")"

for script in gif_*.py; do
    printf '\n== %s\n' "$script"
    python3 "$script"
done
