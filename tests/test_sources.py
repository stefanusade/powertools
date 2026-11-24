"""Tes penamaan sumber MPRIS (tanpa D-Bus/GTK)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powertools.discord import (
    ACTIVITY_TYPE_LISTENING,
    ACTIVITY_TYPE_WATCHING,
    valid_app_id,
)
from powertools.sources import (
    APP_PRESETS,
    BUILTIN_APP_IDS,
    PRESETS,
    activity_type_for,
    preset_for_app_id,
    resolve_app_id,
    source_key,
    source_label,
)


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
                "vlc",
                app_ids={"vlc": "bukan-angka"},
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


class PresetTests(unittest.TestCase):
    """Preset Application ID paten: GUI hanya memilih, tidak mengetik ID."""

    def test_setiap_preset_punya_bentuk_lengkap(self):
        for preset in APP_PRESETS:
            self.assertEqual(
                set(preset), {"key", "label", "app_id"}, preset)
            self.assertTrue(preset["key"])
            self.assertTrue(preset["label"])
            self.assertTrue(valid_app_id(preset["app_id"]), preset)

    def test_kunci_preset_unik(self):
        keys = [preset["key"] for preset in APP_PRESETS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_peta_bawaan_dibentuk_dari_preset(self):
        self.assertEqual(
            BUILTIN_APP_IDS,
            {preset["key"]: preset["app_id"] for preset in APP_PRESETS},
        )

    def test_id_bisa_dilacak_balik_ke_preset(self):
        for preset in APP_PRESETS:
            found = preset_for_app_id(preset["app_id"])
            self.assertIsNotNone(found)
            self.assertEqual(found["key"], preset["key"])

    def test_id_asing_tidak_dianggap_preset(self):
        self.assertIsNone(preset_for_app_id("123456789012345678"))
        self.assertIsNone(preset_for_app_id(""))
        self.assertIsNone(preset_for_app_id(None))

    def test_preset_default_tersedia_untuk_gui(self):
        self.assertIn("default", PRESETS)
        self.assertEqual(PRESETS["default"]["label"], "PowerTools (umum)")

    def test_preset_platform_baru(self):
        self.assertEqual(
            BUILTIN_APP_IDS["spotify"], "1574690633262567494")
        self.assertEqual(
            BUILTIN_APP_IDS["netflix"], "1574693531161731112")
        self.assertEqual(
            BUILTIN_APP_IDS["youtube"], "1574695288088559706")
        self.assertEqual(
            resolve_app_id("spotify"), BUILTIN_APP_IDS["spotify"])
        self.assertEqual(
            resolve_app_id("netflix"), BUILTIN_APP_IDS["netflix"])
        self.assertEqual(
            resolve_app_id("youtube"), BUILTIN_APP_IDS["youtube"])

    def test_preset_platform_baru_bisa_dilacak_balik(self):
        for key in ("spotify", "netflix", "youtube"):
            found = preset_for_app_id(BUILTIN_APP_IDS[key])
            self.assertIsNotNone(found)
            self.assertEqual(found["key"], key)

    def test_label_sumber_dikenal(self):
        self.assertEqual(source_label("youtube-music"), "YouTube Music")
        self.assertEqual(source_label("spotify"), "Spotify")

    def test_label_sumber_asing_dibentuk_dari_kunci(self):
        self.assertEqual(source_label("my-player"), "My Player")
        self.assertEqual(source_label(""), "Tidak dikenal")


class ActivityTypeTests(unittest.TestCase):
    """YouTube/Netflix memakai Watching, sumber musik tetap Listening to."""

    def test_video_memakai_watching(self):
        self.assertEqual(
            activity_type_for("youtube"), ACTIVITY_TYPE_WATCHING)
        self.assertEqual(
            activity_type_for("netflix"), ACTIVITY_TYPE_WATCHING)
        self.assertEqual(
            activity_type_for(" Netflix "), ACTIVITY_TYPE_WATCHING)

    def test_musik_memakai_listening(self):
        for source in ("spotify", "youtube-music", "vlc", "mpv", "brave"):
            self.assertEqual(
                activity_type_for(source), ACTIVITY_TYPE_LISTENING, source)

    def test_sumber_kosong_atau_asing_memakai_listening(self):
        for source in ("", None, "pemutar-aneh"):
            self.assertEqual(
                activity_type_for(source), ACTIVITY_TYPE_LISTENING)

    def test_preset_platform_menentukan_kata_kerja(self):
        # Browser yang dipetakan ke preset YouTube harus "Watching" walau
        # sumbernya bukan "youtube".
        self.assertEqual(
            activity_type_for("brave", BUILTIN_APP_IDS["youtube"]),
            ACTIVITY_TYPE_WATCHING,
        )
        self.assertEqual(
            activity_type_for("brave", BUILTIN_APP_IDS["netflix"]),
            ACTIVITY_TYPE_WATCHING,
        )
        self.assertEqual(
            activity_type_for("youtube", BUILTIN_APP_IDS["spotify"]),
            ACTIVITY_TYPE_LISTENING,
        )

    def test_preset_umum_mengikuti_jenis_sumber(self):
        self.assertEqual(
            activity_type_for("youtube", BUILTIN_APP_IDS["default"]),
            ACTIVITY_TYPE_WATCHING,
        )
        self.assertEqual(
            activity_type_for("spotify", BUILTIN_APP_IDS["default"]),
            ACTIVITY_TYPE_LISTENING,
        )
        self.assertEqual(
            activity_type_for("netflix", "123456789012345678"),
            ACTIVITY_TYPE_WATCHING,
        )


if __name__ == "__main__":
    unittest.main()
