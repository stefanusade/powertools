"""Tes penamaan sumber MPRIS (tanpa D-Bus/GTK)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powertools.discord import valid_app_id
from powertools.sources import BUILTIN_APP_IDS, resolve_app_id, source_key


class SourceKeyTests(unittest.TestCase):
    def test_pemutar_biasa(self):
        self.assertEqual(
            source_key("org.mpris.MediaPlayer2.spotify"), "spotify")
        self.assertEqual(source_key("org.mpris.MediaPlayer2.vlc"), "vlc")
        self.assertEqual(
            source_key("org.mpris.MediaPlayer2.mpv.instance42"), "mpv")

    def test_instance_dibuang(self):
        self.assertEqual(
            source_key("org.mpris.MediaPlayer2.brave.instance282140"), "brave")

    def test_browser_tanpa_petunjuk_situs(self):
        self.assertEqual(source_key("org.mpris.MediaPlayer2.brave",
                                    "Crescendo", "Pretty Please"), "brave")

    def test_browser_dengan_petunjuk_youtube_music(self):
        self.assertEqual(
            source_key(
                "org.mpris.MediaPlayer2.brave.instance1",
                "Lagu - YouTube Music",
                "",
            ),
            "youtube-music",
        )
        self.assertEqual(
            source_key(
                "org.mpris.MediaPlayer2.firefox",
                "Lagu",
                "music.youtube.com",
            ),
            "youtube-music",
        )

    def test_browser_dengan_petunjuk_youtube_biasa(self):
        self.assertEqual(
            source_key("org.mpris.MediaPlayer2.chrome", "Video - YouTube"),
            "youtube",
        )

    def test_browser_dengan_petunjuk_spotify(self):
        self.assertEqual(
            source_key("org.mpris.MediaPlayer2.chromium", "Spotify - Web Player"),
            "spotify",
        )

    def test_owner_kosong(self):
        self.assertEqual(source_key(""), "")
        self.assertEqual(source_key(None), "")  # type: ignore[arg-type]

    def test_nama_aneh_dinormalkan(self):
        self.assertEqual(
            source_key("org.mpris.MediaPlayer2.My_Player (Beta)"), "my-player-beta")


class BuiltinAppIdTests(unittest.TestCase):
    def test_semua_id_bawaan_valid(self):
        for key, value in BUILTIN_APP_IDS.items():
            self.assertTrue(valid_app_id(value), f"{key} bukan snowflake")

    def test_ada_cadangan_default(self):
        self.assertIn("default", BUILTIN_APP_IDS)

    def test_sumber_tanpa_pemetaan_memakai_bawaan(self):
        self.assertEqual(
            resolve_app_id("vlc"), BUILTIN_APP_IDS["default"])

    def test_youtube_music_punya_id_sendiri(self):
        self.assertNotEqual(
            BUILTIN_APP_IDS["youtube-music"], BUILTIN_APP_IDS["default"])
        self.assertEqual(
            resolve_app_id("youtube-music"), BUILTIN_APP_IDS["youtube-music"])

    def test_pemetaan_pengguna_menang(self):
        self.assertEqual(
            resolve_app_id("spotify", app_ids={"spotify": "111111111111111111"}),
            "111111111111111111",
        )

    def test_pemetaan_pengguna_menang_atas_bawaan_per_sumber(self):
        self.assertEqual(
            resolve_app_id(
                "youtube-music",
                app_ids={"youtube-music": "222222222222222222"},
            ),
            "222222222222222222",
        )

    def test_id_utama_tetap_dipakai_untuk_sumber_lain(self):
        self.assertEqual(
            resolve_app_id("mpv", app_id="333333333333333333"),
            "333333333333333333",
        )

    def test_bawaan_per_sumber_mengalahkan_id_utama(self):
        self.assertEqual(
            resolve_app_id("youtube-music", app_id="333333333333333333"),
            BUILTIN_APP_IDS["youtube-music"],
        )

    def test_cadangan_default_pengguna_mengalahkan_id_utama(self):
        self.assertEqual(
            resolve_app_id(
                "mpv",
                app_ids={"default": "444444444444444444"},
                app_id="333333333333333333",
            ),
            "444444444444444444",
        )

    def test_saklar_mati_mematikan_bawaan(self):
        self.assertEqual(
            resolve_app_id("youtube-music", use_builtin=False), "")
        self.assertEqual(
            resolve_app_id(
                "youtube-music",
                app_id="333333333333333333",
                use_builtin=False,
            ),
            "333333333333333333",
        )

    def test_id_tidak_valid_diabaikan(self):
        self.assertEqual(
            resolve_app_id(
                "spotify",
                app_ids={"spotify": "bukan-angka"},
                app_id="333333333333333333",
            ),
            "333333333333333333",
        )

    def test_kunci_sumber_tidak_peka_huruf(self):
        self.assertEqual(
            resolve_app_id("YouTube-Music"), BUILTIN_APP_IDS["youtube-music"])

    def test_tanpa_sumber_memakai_cadangan(self):
        self.assertEqual(resolve_app_id(), BUILTIN_APP_IDS["default"])
        self.assertEqual(resolve_app_id("", use_builtin=False), "")


if __name__ == "__main__":
    unittest.main()
