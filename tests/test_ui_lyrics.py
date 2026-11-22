"""Uji jendela lirik yang butuh GTK (dilewati kalau GTK tidak tersedia).

Yang diuji di sini adalah perilaku yang tidak bisa diperiksa lewat modul
``lyrics`` saja: tombol romaji harus langsung mengubah lagu yang sedang
diputar, dan posisi gulir tidak boleh melompat.
"""

from __future__ import annotations

import unittest

try:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from powertools import config, lyrics
    from powertools.ui_lyrics import LyricsWindow

    GTK_READY = True
except Exception:  # pragma: no cover - lingkungan tanpa GTK/display
    GTK_READY = False


def _make_window(testcase, romanize: bool = False):
    cfg = config.Config()
    cfg.set("lyrics.romanize", romanize)
    window = LyricsWindow(None, cfg)

    class FakeApp:
        """Meniru service: simpan config lalu panggil window.set_romanize()."""

        def __init__(self, target):
            self.target = target
            self.saved: list[bool] = []

        def set_lyrics_romanize(self, enabled):
            self.saved.append(bool(enabled))
            cfg.set("lyrics.romanize", bool(enabled))
            self.target.set_romanize(bool(enabled))

    window._app = FakeApp(window)
    testcase.addCleanup(window.destroy)
    return window


@unittest.skipUnless(GTK_READY, "GTK 4 tidak tersedia")
class RomajiToggleTests(unittest.TestCase):
    def _lyrics(self) -> lyrics.Lyrics:
        return lyrics.Lyrics(
            track="Ditto",
            artist="NewJeans",
            synced=[
                lyrics.LyricLine(1.0, "너의 이름을"),
                lyrics.LyricLine(4.0, "부르는 밤"),
            ],
            romaji=[
                lyrics.LyricLine(1.0, "neoui ireumeul"),
                lyrics.LyricLine(4.0, "bureuneun bam"),
            ],
            romaji_source="transliterasi lokal",
        )

    def test_klik_langsung_mengubah_lagu_sekarang(self):
        """Regresi: tombol sempat hanya berpengaruh pada lagu berikutnya."""
        window = _make_window(self)
        window._lyrics = self._lyrics()
        window._render_lyrics(window._lyrics)
        self.assertEqual(window._labels[0].get_label(), "너의 이름을")

        window._romaji_button.set_active(True)
        self.assertEqual(window._labels[0].get_label(), "neoui ireumeul")
        self.assertTrue(window._romanize)

        window._romaji_button.set_active(False)
        self.assertEqual(window._labels[0].get_label(), "너의 이름을")

    def test_pilihan_tersimpan_ke_config(self):
        window = _make_window(self)
        window._lyrics = self._lyrics()
        window._render_lyrics(window._lyrics)
        window._romaji_button.set_active(True)
        self.assertEqual(window._app.saved, [True])
        self.assertTrue(window._cfg.get("lyrics.romanize"))

    def test_timestamp_tidak_berubah_saat_berganti(self):
        window = _make_window(self)
        window._lyrics = self._lyrics()
        window._render_lyrics(window._lyrics)
        sebelum = list(window._times)
        window._romaji_button.set_active(True)
        self.assertEqual(window._times, sebelum)

    def test_toggle_tidak_membangun_ulang_baris(self):
        """Jumlah baris sama -> teks diganti di tempat, tanpa widget baru."""
        window = _make_window(self)
        window._lyrics = self._lyrics()
        window._render_lyrics(window._lyrics)
        baris_awal = list(window._rows)
        window._romaji_button.set_active(True)
        self.assertEqual(len(window._rows), len(baris_awal))
        for sebelum, sesudah in zip(baris_awal, window._rows):
            self.assertIs(sebelum, sesudah)

    def test_status_menyebut_sumber_romaji(self):
        window = _make_window(self)
        window._lyrics = self._lyrics()
        window._render_lyrics(window._lyrics)
        window._romaji_button.set_active(True)
        self.assertIn("romaji", window._status.get_label())
        self.assertIn("transliterasi lokal", window._status.get_label())

    def test_tombol_tersembunyi_tanpa_romaji(self):
        window = _make_window(self)
        window._lyrics = lyrics.Lyrics(
            synced=[lyrics.LyricLine(1.0, "それは予想通り")]
        )
        window._render_lyrics(window._lyrics)
        self.assertFalse(window._romaji_button.get_visible())

    def test_romaji_aktif_sejak_awal(self):
        window = _make_window(self, romanize=True)
        window._lyrics = self._lyrics()
        window._render_lyrics(window._lyrics)
        self.assertEqual(window._labels[0].get_label(), "neoui ireumeul")
        self.assertTrue(window._romaji_button.get_active())

    def test_lirik_plain_ikut_berganti(self):
        window = _make_window(self)
        window._lyrics = lyrics.Lyrics(
            plain=["첫 번째 줄", "", "세 번째 줄"],
            romaji_plain=["cheot beonjjae jul", "", "se beonjjae jul"],
            romaji_source="transliterasi lokal",
        )
        window._render_lyrics(window._lyrics)
        self.assertEqual(window._plain_rows[0].get_label(), "첫 번째 줄")
        window._romaji_button.set_active(True)
        self.assertEqual(window._plain_rows[0].get_label(), "cheot beonjjae jul")
        self.assertIsNone(window._plain_rows[1])  # spasi antar-bait tetap


@unittest.skipUnless(GTK_READY, "GTK 4 tidak tersedia")
class OpacityTests(unittest.TestCase):
    """Kepekatan latar: hanya kartu, bukan teks, yang dibuat tembus pandang."""

    def test_nilai_awal_dari_config(self):
        cfg = config.Config()
        cfg.set("lyrics.opacity", 55)
        window = LyricsWindow(None, cfg)
        self.addCleanup(window.destroy)
        self.assertEqual(window._opacity, 55)

    def test_nilai_rusak_dijepit_ke_batas_aman(self):
        cfg = config.Config()
        cfg.set("lyrics.opacity", 1)
        window = LyricsWindow(None, cfg)
        self.addCleanup(window.destroy)
        self.assertEqual(window._opacity, 30)

        cfg.set("lyrics.opacity", "bukan angka")
        window.apply_config()
        self.assertEqual(window._opacity, 94)

    def test_perubahan_config_langsung_berlaku(self):
        cfg = config.Config()
        cfg.set("lyrics.opacity", 94)
        window = LyricsWindow(None, cfg)
        self.addCleanup(window.destroy)

        cfg.set("lyrics.opacity", 35)
        window.apply_config()
        self.assertEqual(window._opacity, 35)

    def test_css_memakai_alpha_kartu(self):
        from powertools.ui_lyrics import _css_text

        self.assertIn("rgba(14, 14, 18, 0.35)", _css_text(35))
        self.assertIn("rgba(14, 14, 18, 1.00)", _css_text(100))


@unittest.skipUnless(GTK_READY, "GTK 4 tidak tersedia")
class TaskbarTests(unittest.TestCase):
    """Jendela lirik bisa disembunyikan dari taskbar lewat ekstensi."""

    def _window(self, hide: bool):
        cfg = config.Config()
        cfg.set("lyrics.hide_from_taskbar", hide)
        window = LyricsWindow(None, cfg)
        self.addCleanup(window.destroy)
        window.get_visible = lambda: True
        return cfg, window

    def test_pilihan_awal_dibaca_dari_config(self):
        _, window = self._window(False)
        self.assertFalse(window._hide_from_taskbar)

        _, window = self._window(True)
        self.assertTrue(window._hide_from_taskbar)

    def test_mendorong_nilai_ke_shell(self):
        from unittest import mock

        cfg, window = self._window(False)
        with mock.patch(
            "powertools.ui_lyrics.dbusutil.set_lyrics_skip_taskbar",
            return_value=True,
        ) as call:
            window._push_taskbar()
        call.assert_called_once_with(False)

        cfg.set("lyrics.hide_from_taskbar", True)
        window.apply_config()
        with mock.patch(
            "powertools.ui_lyrics.dbusutil.set_lyrics_skip_taskbar",
            return_value=True,
        ) as call:
            window._push_taskbar()
        call.assert_called_once_with(True)

    def test_gagal_diulang_beberapa_kali(self):
        from unittest import mock

        _, window = self._window(True)
        with mock.patch(
            "powertools.ui_lyrics.dbusutil.set_lyrics_skip_taskbar",
            return_value=False,
        ) as call:
            window._push_taskbar()
            self.assertEqual(call.call_count, 1)
        # Retry dijadwalkan lewat GLib, bukan rekursi langsung.
        self.assertFalse(window._destroyed)


@unittest.skipUnless(GTK_READY, "GTK 4 tidak tersedia")
class SeekTests(unittest.TestCase):
    """Klik dua kali pada baris lirik melompat ke detik baris tersebut."""

    def _window(self, offset_ms: int = 0, accepted: bool = True):
        cfg = config.Config()
        cfg.set("lyrics.offset_ms", offset_ms)
        window = LyricsWindow(None, cfg)

        class FakeApp:
            def __init__(self):
                self.calls: list[float] = []

            def seek_lyrics(self, seconds):
                self.calls.append(seconds)
                return accepted

        window._app = FakeApp()
        self.addCleanup(window.destroy)
        window._lyrics = lyrics.Lyrics(
            track="Lagu",
            artist="Artis",
            synced=[
                lyrics.LyricLine(1.0, "baris pertama"),
                lyrics.LyricLine(4.0, "baris kedua"),
                lyrics.LyricLine(9.0, "baris ketiga"),
            ],
        )
        window._render_lyrics(window._lyrics)
        return window

    def test_klik_dua_kali_melompat(self):
        window = self._window()
        window._on_row_pressed(None, 2, 0, 0, 1)
        self.assertEqual(window._app.calls, [4.0])
        # Baris yang diklik langsung jadi baris aktif.
        self.assertEqual(window._current, 1)
        self.assertIn("pt-lyrics-current",
                      window._rows[1].get_css_classes())

    def test_klik_sekali_tidak_melompat(self):
        window = self._window()
        window._on_row_pressed(None, 1, 0, 0, 1)
        self.assertEqual(window._app.calls, [])

    def test_klik_ketiga_tidak_mengulang(self):
        window = self._window()
        window._on_row_pressed(None, 3, 0, 0, 1)
        self.assertEqual(window._app.calls, [])

    def test_offset_lirik_ikut_dihitung(self):
        window = self._window(offset_ms=500)
        window._on_row_pressed(None, 2, 0, 0, 1)
        # Baris 4.0 s tampil 0.5 s lebih cepat, jadi pemutar dilompat ke 3.5 s.
        self.assertEqual(window._app.calls, [3.5])
        self.assertEqual(window._position, 4.0)

    def test_indeks_di_luar_batas_diabaikan(self):
        window = self._window()
        window._on_row_pressed(None, 2, 0, 0, 99)
        window._on_row_pressed(None, 2, 0, 0, -1)
        self.assertEqual(window._app.calls, [])

    def test_penolakan_pemutar_ditampilkan(self):
        window = self._window(accepted=False)
        status_awal = window._status.get_label()
        window._on_row_pressed(None, 2, 0, 0, 2)
        self.assertIn("tidak mendukung", window._status.get_label())
        self.assertEqual(window._current, -1)
        # Pesan hanya sementara; teks asli disimpan untuk dipulihkan.
        self.assertEqual(window._status_saved, status_awal)

    def test_baris_punya_petunjuk_klik(self):
        window = self._window()
        tooltip = window._rows[0].get_tooltip_text()
        self.assertIn("Klik dua kali", tooltip)

    def test_lirik_tanpa_timestamp_tidak_bisa_diklik(self):
        cfg = config.Config()
        window = LyricsWindow(None, cfg)
        self.addCleanup(window.destroy)
        window._app = None
        window._lyrics = lyrics.Lyrics(plain=["baris", "tanpa waktu"])
        window._render_lyrics(window._lyrics)
        self.assertEqual(window._times, [])
        self.assertEqual(window._rows, [])
        self.assertIsNone(window._plain_rows[0].get_tooltip_text())


if __name__ == "__main__":
    unittest.main()
