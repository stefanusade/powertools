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


if __name__ == "__main__":
    unittest.main()
