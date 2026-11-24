"""Uji jendela pengaturan: tab vertikal per modul dan penyimpanan nilainya.

Yang diperiksa hanya perilaku yang tidak terlihat dari config.json: setiap
modul punya tab sendiri, saklar modul menyamakan isi tab/banner, dan slider
kepekatan lirik menulis nilai yang sama dengan yang dibaca jendela lirik.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

try:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from powertools import config
    from powertools.sources import APP_PRESETS, PRESETS
    from powertools.ui_settings import SettingsWindow

    GTK_READY = True
except Exception:  # pragma: no cover - lingkungan tanpa GTK/display
    GTK_READY = False


class _FakeApp:
    def __init__(self) -> None:
        self.reloads = 0

    def reload_config(self) -> None:
        self.reloads += 1

    def window_hidden(self, _window) -> None:
        pass


def _make_window(testcase, **values):
    tmp = tempfile.mkdtemp(prefix="powertools-settings-")
    path = Path(tmp) / "config.json"
    cfg = config.Config(path)
    for dotted, value in values.items():
        cfg.set(dotted.replace("__", "."), value)
    cfg.save()

    app = _FakeApp()
    window = SettingsWindow(None, cfg)
    window._app = app
    # Jangan memindai D-Bus saat tes: hasilnya tidak deterministik dan bisa
    # menahan suite. Tes yang butuh daftar sumber mengisinya sendiri.
    window._discord_detected_loaded = True
    testcase.addCleanup(window.destroy)
    return window, cfg, path


@unittest.skipUnless(GTK_READY, "GTK 4 tidak tersedia")
class NavTests(unittest.TestCase):
    def test_setiap_modul_punya_tab(self):
        window, _cfg, _path = _make_window(self)
        for name in ("general",) + config.MODULES:
            self.assertIn(name, window._nav_rows)
            self.assertIsNotNone(window._stack.get_child_by_name(name))

    def test_tab_umum_aktif_saat_dibuka(self):
        window, _cfg, _path = _make_window(self)
        self.assertEqual(window._stack.get_visible_child_name(), "general")

    def test_pilih_baris_mengganti_halaman(self):
        window, _cfg, _path = _make_window(self)
        for name in config.MODULES:
            window._nav_list.select_row(window._nav_rows[name])
            self.assertEqual(window._stack.get_visible_child_name(), name)


@unittest.skipUnless(GTK_READY, "GTK 4 tidak tersedia")
class ModuleToggleTests(unittest.TestCase):
    def test_saklar_mati_menonaktifkan_isi_tab(self):
        window, cfg, _path = _make_window(self)
        window._module_switches["launcher"].set_active(False)
        self.assertFalse(cfg.get("modules.launcher"))
        self.assertFalse(window._module_bodies["launcher"].get_sensitive())
        self.assertTrue(window._module_banners["launcher"].get_visible())
        self.assertTrue(window._nav_dots["launcher"].get_visible())

    def test_saklar_hidup_mengembalikan_isi_tab(self):
        window, _cfg, _path = _make_window(self, modules__launcher=False)
        window._module_switches["launcher"].set_active(True)
        self.assertTrue(window._module_bodies["launcher"].get_sensitive())
        self.assertFalse(window._module_banners["launcher"].get_visible())
        self.assertFalse(window._nav_dots["launcher"].get_visible())

    def test_discord_tetap_bisa_diisi_saat_mati(self):
        """Application ID dibutuhkan justru untuk menyalakan modulnya."""
        window, _cfg, _path = _make_window(self)
        self.assertFalse(window._module_switches["discord"].get_active())
        self.assertTrue(window._module_bodies["discord"].get_sensitive())


@unittest.skipUnless(GTK_READY, "GTK 4 tidak tersedia")
class LyricsOpacityTests(unittest.TestCase):
    def test_slider_menulis_ke_config(self):
        window, cfg, path = _make_window(self)
        window._scale_lyrics_opacity.set_value(45)
        self.assertEqual(cfg.get("lyrics.opacity"), 45)
        window._flush()
        tersimpan = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(tersimpan["lyrics"]["opacity"], 45)

    def test_nilai_awal_dibaca_dari_config(self):
        window, _cfg, _path = _make_window(self, lyrics__opacity=70)
        self.assertEqual(int(window._scale_lyrics_opacity.get_value()), 70)

    def test_perubahan_diteruskan_ke_aplikasi(self):
        window, _cfg, _path = _make_window(self)
        window._scale_lyrics_opacity.set_value(60)
        window._flush()
        self.assertEqual(window._app.reloads, 1)


class _Source:
    """Pengganti mpris.SourceInfo supaya tes tidak butuh D-Bus."""

    def __init__(self, source, title="", artist="", playing=False):
        self.source = source
        self.title = title
        self.artist = artist
        self.playing = playing
        self.owner = f"org.mpris.MediaPlayer2.{source}"


@unittest.skipUnless(GTK_READY, "GTK 4 tidak tersedia")
class DiscordPresetTests(unittest.TestCase):
    """Application ID tidak diketik: pengguna memilih sumber dan preset paten."""

    def _window(self, **values):
        window, cfg, path = _make_window(self, **values)
        window._discord_detected = [
            _Source("brave", "Lagu", "Penyanyi", playing=True)]
        window._rebuild_discord_sources()
        return window, cfg, path

    def test_sumber_terbuka_punya_baris_sendiri(self):
        window, _cfg, _path = self._window()
        self.assertIn("brave", window._discord_dropdowns)

    def test_daftar_hanya_berisi_sumber_nyata(self):
        """Tidak ada daftar pemutar bawaan: hanya MPRIS + pemetaan tersimpan."""
        window, _cfg, _path = self._window(
            discord__app_ids={"vlc": PRESETS["default"]["app_id"]})
        self.assertEqual(
            set(window._discord_dropdowns), {"brave", "vlc", "default"})
        self.assertNotIn("spotify", window._discord_dropdowns)
        self.assertNotIn("netflix", window._discord_dropdowns)

    def test_pilihan_preset_menulis_id_paten(self):
        window, cfg, path = self._window()
        dropdown = window._discord_dropdowns["brave"]
        dropdown.set_selected(2)  # YouTube Music
        window._flush()

        self.assertEqual(
            cfg.get("discord.app_ids")["brave"],
            PRESETS["youtube-music"]["app_id"],
        )
        tersimpan = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            tersimpan["discord"]["app_ids"]["brave"],
            PRESETS["youtube-music"]["app_id"],
        )

    def test_dropdown_baris_memilih_preset_tersimpan(self):
        window, _cfg, _path = self._window(
            discord__app_ids={"brave": PRESETS["youtube-music"]["app_id"]})
        dropdown = window._discord_dropdowns["brave"]
        model = dropdown.get_model()
        selected = model.get_string(int(dropdown.get_selected()))
        self.assertEqual(selected, PRESETS["youtube-music"]["label"])

    def test_ganti_preset_lewat_dropdown_baris(self):
        window, cfg, _path = self._window(
            discord__app_ids={"brave": PRESETS["youtube-music"]["app_id"]})
        dropdown = window._discord_dropdowns["brave"]
        # ["Otomatis", "PowerTools (umum)", "YouTube Music"]
        dropdown.set_selected(1)
        self.assertEqual(
            cfg.get("discord.app_ids")["brave"],
            PRESETS["default"]["app_id"],
        )

    def test_otomatis_menghapus_pemetaan(self):
        window, cfg, _path = self._window(
            discord__app_ids={"brave": PRESETS["youtube-music"]["app_id"]})
        window._discord_dropdowns["brave"].set_selected(0)
        self.assertNotIn("brave", cfg.get("discord.app_ids"))

    def test_otomatis_tetap_bernama_otomatis(self):
        window, _cfg, _path = self._window(
            discord__use_builtin_ids=False,
            discord__app_ids={"brave": PRESETS["default"]["app_id"]})
        dropdown = window._discord_dropdowns["brave"]
        self.assertEqual(dropdown.get_model().get_string(0), "Otomatis")

    def test_hapus_baris_menghapus_pemetaan(self):
        window, cfg, _path = self._window(
            discord__app_ids={"brave": PRESETS["default"]["app_id"]})
        window._on_discord_source_remove(None, "brave")
        self.assertNotIn("brave", cfg.get("discord.app_ids"))

    def test_pengaturan_bertahan_setelah_dibuka_ulang(self):
        window, cfg, path = self._window()
        window._discord_dropdowns["brave"].set_selected(2)  # YouTube Music
        window._flush()
        window.destroy()

        # Simulasi restart: jendela baru membaca config yang sama.
        cfg2 = config.Config(path)
        app = _FakeApp()
        window2 = SettingsWindow(None, cfg2)
        window2._app = app
        window2._discord_detected_loaded = True
        self.addCleanup(window2.destroy)

        dropdown = window2._discord_dropdowns["brave"]
        model = dropdown.get_model()
        self.assertEqual(
            model.get_string(int(dropdown.get_selected())),
            PRESETS["youtube-music"]["label"],
        )

    def test_id_lama_masih_dihormati_dan_bisa_dihapus(self):
        window, cfg, _path = self._window(
            discord__app_id="123456789012345678")
        self.assertEqual(
            cfg.get("discord.app_id"), "123456789012345678")
        window._on_discord_legacy_clear(None)
        self.assertEqual(cfg.get("discord.app_id"), "")

    def test_bawaan_mati_menawarkan_tidak_dikirim(self):
        window, cfg, _path = self._window(
            discord__use_builtin_ids=False)
        dropdown = window._discord_dropdowns["default"]
        model = dropdown.get_model()
        self.assertEqual(
            model.get_string(int(dropdown.get_selected())), "Tidak dikirim")
        # Pilih "Otomatis (bawaan)" untuk menyalakan preset lagi.
        dropdown.set_selected(0)
        self.assertTrue(cfg.get("discord.use_builtin_ids"))

    def test_tidak_dikirim_mematikan_preset(self):
        window, cfg, _path = self._window()
        window._discord_dropdowns["default"].set_selected(1)
        self.assertFalse(cfg.get("discord.use_builtin_ids"))

    def test_id_asing_ditandai_sebagai_id_lama(self):
        window, _cfg, _path = self._window(
            discord__app_ids={"brave": "123456789012345678"})
        dropdown = window._discord_dropdowns["brave"]
        model = dropdown.get_model()
        self.assertEqual(
            model.get_string(int(dropdown.get_selected())),
            "Application ID lama",
        )

    def test_cadangan_default_bukan_baris_sumber(self):
        window, _cfg, _path = self._window(
            discord__app_ids={"default": PRESETS["youtube-music"]["app_id"]})
        self.assertNotIn("Default", window._discord_dropdowns)
        self.assertIn("default", window._discord_dropdowns)
        dropdown = window._discord_dropdowns["default"]
        model = dropdown.get_model()
        self.assertEqual(
            model.get_string(int(dropdown.get_selected())),
            PRESETS["youtube-music"]["label"],
        )

    def test_cadangan_default_bisa_diubah(self):
        window, cfg, _path = self._window(
            discord__app_ids={"default": PRESETS["youtube-music"]["app_id"]})
        # ["Otomatis (bawaan)", "Tidak dikirim", "PowerTools (umum)", ...]
        window._discord_dropdowns["default"].set_selected(2)
        self.assertEqual(
            cfg.get("discord.app_ids")["default"],
            PRESETS["default"]["app_id"],
        )
    def test_cadangan_default_otomatis_menghapus(self):
        window, cfg, _path = self._window(
            discord__app_ids={"default": PRESETS["youtube-music"]["app_id"]})
        window._discord_dropdowns["default"].set_selected(0)
        self.assertNotIn("default", cfg.get("discord.app_ids"))


if __name__ == "__main__":
    unittest.main()
