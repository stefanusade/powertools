# Perekam GIF untuk README

Skrip di sini membuat ulang animasi di `docs/gifs/`. Dua cara perekaman dipakai,
tergantung di mana UI-nya hidup.

## Jendela GTK: render langsung dari widget

Untuk semua jendela PowerTools (clipboard, launcher, hasil color picker,
overlay picker, lirik, Pengaturan, Discord), tiap frame dirender langsung dari
widget dengan `Gsk.CairoRenderer` — sama seperti tangkapan statis di `docs/`,
tanpa perlu screencast.

```bash
python3 -m pip install --user Pillow      # satu-satunya dependensi tambahan
python3 tools/gifs/gif_lyrics.py          # satu GIF
./tools/gifs/render-all.sh                # semua GIF, ~10 menit
```

Butuh sesi grafis (Wayland atau X11) karena jendela GTK tetap harus dipetakan.
Konfigurasi, riwayat clipboard, dan cache ditulis ke direktori sementara
(`setup_env`), jadi data pengguna tidak tersentuh dan tiap rekaman mulai dari
keadaan bersih.

`gif_lyrics_romaji.py` tidak memanggil LRCLIB: lirik "Crescendo" (NMIXX)
disalin apa adanya dari rekaman LRCLIB id 33813966, lalu versi Latinnya dibuat
`lyrics._attach_romaji` dengan daftar hasil pencarian kosong. Jadi rekaman
tetap jalan tanpa internet, dan yang terlihat sama dengan hasil sungguhan —
termasuk jalur transliterasi Hangul lokal, karena lagu ini tidak punya
unggahan romaji.

## Dynamic island: rekam area layar

Island hidup di dalam GNOME Shell (`media.js`, St/Clutter), bukan jendela GTK,
jadi tidak bisa diambil lewat `Gtk.WidgetPaintable`. `gif_island.py` merekam
area tepi atas layar lewat `org.gnome.Shell.Screencast`. Supaya tidak ada
jendela lain yang ikut terekam, skrip pindah ke workspace kosong dan memasang
latar sementara yang netral; workspace dan latar dikembalikan seperti semula
setelah selesai. Butuh `gjs` dan `ffmpeg`, dan hanya jalan di GNOME Shell.

Workspace kosong dipilih, bukan `Super+D` (show desktop): `Super+D` bersifat
bolak-balik, jadi satu tekanan berlebih meninggalkan desktop dalam keadaan
show desktop. Jumlah perpindahan dihitung dan disimpan di
`/tmp/powertools-gifs/island-saved.json`; kalau proses mati di tengah jalan,
jalankan berikutnya mengembalikan workspace dan latar lebih dulu.

Yang dipakai untuk memicu perilaku asli:

- `mpris_fake.js` — pemutar MPRIS palsu (judul dan sampul tiruan). Dua proses
  dengan `--bus` berbeda memunculkan chip pemilih pemutar.
- `org.gnome.Mutter.RemoteDesktop` — menggerakkan pointer, mengklik chip, dan
  berpindah workspace (`Ctrl+Alt+→`), tanpa dialog izin portal.
- Banner notifikasi ditutup lebih dulu; kalau tidak, banner menutupi area
  rekaman.

Ukuran layar dibaca dari GDK, jadi area rekam selalu di tengah tepi atas. Tapi
koordinat klik (`CHIP_SPOTIFY`, `HOT_SPOT`) mengasumsikan panel GNOME **tidak**
di atas layar, seperti Zorin — island menempel langsung ke tepi atas. Kalau
panel ada di atas, island turun sepanjang panel; geser kedua koordinat itu
sebesar tinggi panel.

Frame pembuka sebelum island muncul dibuang otomatis (ambang 3% piksel
berubah), begitu juga frame animasi muncul yang kartunya masih tembus pandang.
GIF yang diawali latar kosong atau kartu buram terlihat seperti rekaman gagal.

Apakah workspace sudah kosong diperiksa dari piksel tengah layar (di sana hanya
ada latar). Kalau setelah beberapa kali geser masih ada jendela, rekaman
dibatalkan dan workspace dikembalikan — lebih baik tidak ada GIF daripada GIF
berisi isi desktop orang.

## Isi

| Skrip | GIF |
| --- | --- |
| `gif_clipboard.py` | `clipboard.gif` — cari, pilih, pin, cari lagi |
| `gif_launcher.py` | `launcher.gif` — kalkulator, cari aplikasi, aksi cepat |
| `gif_color_result.py` | `color-picker.gif` — ganti warna, salin HSL/CMYK |
| `gif_color_overlay.py` | `color-picker-overlay.gif` — loupe fallback + klik |
| `gif_lyrics.py` | `lyrics.gif` — gulir otomatis, hover, klik dua kali |
| `gif_lyrics_romaji.py` | `lyrics-romaji.gif` — tombol Romaji: Latin ↔ Hangul |
| `gif_settings.py` | `settings.gif` — pindah tab, ubah kepekatan, pin |
| `gif_discord.py` | `discord.gif` — pilih preset per sumber |
| `gif_island.py` | `island.gif` — muncul, ganti pemutar, tutup, hover |

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

## Yang tetap tidak bisa direkam

- **Color picker bawaan GNOME Shell** (jalur pertama, dengan loupe milik Shell)
  tidak punya jendela GTK. Yang direkam adalah fallback GTK-nya, yang
  tampilannya sama-sama memakai loupe 8× dan kartu warna.

Lirik pada `gif_lyrics.py` adalah teks fiktif ("Lampu Kota" / "Senja Kecil"),
bukan lirik lagu sungguhan.
