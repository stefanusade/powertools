# Perekam GIF untuk README

Skrip di sini membuat ulang animasi di `docs/gifs/`. Tiap skrip membuka jendela
PowerTools yang sungguhan (bukan tiruan), menjalankan aksi pada widget-nya, dan
merender tiap frame lewat `Gsk.CairoRenderer` — jadi hasilnya sama dengan
tangkapan layar tanpa perlu screencast.

## Menjalankan

```bash
python3 -m pip install --user Pillow      # satu-satunya dependensi tambahan
./tools/gifs/render-all.sh                # semua GIF, ~5-10 menit
python3 tools/gifs/gif_lyrics.py          # satu GIF saja
```

Butuh sesi grafis (Wayland atau X11) karena jendela GTK tetap harus dipetakan.
Konfigurasi, riwayat clipboard, dan cache ditulis ke direktori sementara
(`setup_env`), jadi data pengguna tidak tersentuh dan tiap rekaman mulai dari
keadaan bersih.

## Isi

| Skrip | GIF |
| --- | --- |
| `gif_clipboard.py` | `clipboard.gif` — cari, pilih, pin, cari lagi |
| `gif_launcher.py` | `launcher.gif` — kalkulator, cari aplikasi, aksi cepat |
| `gif_color_result.py` | `color-picker.gif` — ganti warna, salin HSL/CMYK |
| `gif_color_overlay.py` | `color-picker-overlay.gif` — loupe fallback + klik |
| `gif_lyrics.py` | `lyrics.gif` — gulir otomatis, hover, klik dua kali |
| `gif_settings.py` | `settings.gif` — pindah tab, ubah kepekatan, pin |
| `gif_discord.py` | `discord.gif` — pilih preset per sumber |

`gifkit.py` berisi bagian yang dipakai bersama: `Scene` (perekam waktu maya),
`encode_gif` (Pillow, palet global, tanpa dithering), `type_text`, dan
`pin_size`.

## Catatan teknis

- **Waktu maya.** `Scene` memajukan `1/fps` detik per frame, bukan mengikuti jam
  dinding. Tanpa ini, jendela besar seperti overlay color picker (1000×640)
  kehilangan frame dan gerakannya patah saat diputar.
- **Render dipisah dari aksi.** Snapshot tepat setelah widget diubah
  menghasilkan node kosong (GTK menandai perlu layout ulang), jadi `Scene`
  menerapkan aksi di satu putaran loop lalu merender di putaran berikutnya.
  Kalau snapshot tetap kosong, frame dicoba ulang beberapa kali.
- **Palet global tanpa dithering.** UI ini berisi bidang warna rata dan teks
  kecil; dithering justru mengotori teks dan membuat berkas 4-6x lebih besar.
- **Kanvas disamakan.** Jendela yang tingginya berubah (daftar hasil launcher)
  dipad ke kanvas terbesar, diisi warna sudut kiri atas frame itu.
- **Renderer harus di-`unrealize()`** sebelum proses keluar; kalau tidak,
  `gsk_renderer_dispose` gagal dan proses berhenti dengan SIGABRT.

## Yang tidak bisa direkam di sini

- **Dynamic island media** hidup di dalam GNOME Shell (`media.js`, St/Clutter),
  bukan jendela GTK, jadi tidak bisa dirender lewat `Gtk.WidgetPaintable`.
- Overlay color picker **bawaan GNOME Shell** (jalur pertama) juga milik Shell.
  Yang direkam adalah fallback GTK-nya, yang tampilannya sama-sama memakai
  loupe 8× dan kartu warna.

Lirik pada `gif_lyrics.py` adalah teks fiktif ("Lampu Kota" / "Senja Kecil"),
bukan lirik lagu sungguhan.
