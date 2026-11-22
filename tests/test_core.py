"""Tes inti tanpa GTK: parsing angka, fuzzy match, dan penyimpanan clipboard."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powertools.matcher import calculate, evaluate, fuzzy_score, match_tokens, percent_of
from powertools.config import Config, parse_config_value
from powertools.colorspace import (
    color_palette,
    color_shades,
    contrast_color,
    format_color,
    hex_to_rgb,
    hsl_to_rgb,
    rgb_to_cmyk,
    rgb_to_hex,
    rgb_to_hsl,
    shade_color,
)
from powertools.store import ClipStore, clip_key


class MatcherTests(unittest.TestCase):
    def test_fuzzy_ranking(self):
        self.assertGreater(fuzzy_score("fire", "Firefox"), fuzzy_score("fire", "File Roller"))
        self.assertGreater(fuzzy_score("fire", "Firefox"), 0)
        self.assertGreater(fuzzy_score("ffx", "Firefox"), 0)
        self.assertEqual(fuzzy_score("zzz", "Firefox"), -1)

    def test_tokens(self):
        self.assertTrue(match_tokens("git push", "git push origin main"))
        self.assertFalse(match_tokens("git pull", "git push origin main"))
        self.assertTrue(match_tokens("", "apa saja"))

    def test_arithmetic(self):
        self.assertEqual(calculate("2+3*4"), "14")
        self.assertEqual(calculate("(2+3)*4"), "20")
        self.assertEqual(calculate("10/4"), "2.5")
        self.assertEqual(calculate("2^10"), "1024")
        self.assertEqual(calculate("sqrt(2)"), "1.414213562")
        self.assertEqual(calculate("pi*2"), "6.283185307")
        self.assertEqual(calculate("50% of 240"), "120")
        self.assertEqual(calculate("15% dari 200"), "30")
        self.assertEqual(calculate("12 * 8 ="), "96")

    def test_rejects_non_math(self):
        self.assertIsNone(calculate("halo dunia"))
        self.assertIsNone(calculate("import os"))
        self.assertIsNone(calculate("__import__('os').system('ls')"))
        self.assertIsNone(calculate("2+"))
        self.assertIsNone(calculate("1/0"))
        self.assertIsNone(calculate("9^999999"))

    def test_percent(self):
        self.assertEqual(percent_of("10% of 50"), "5")
        self.assertIsNone(percent_of("10 + 5"))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.spool = self.root / "clipboard.jsonl"
        self.state = self.root / "state.json"
        self.store = ClipStore(self.spool, self.state, max_items=3, max_chars=500)

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_and_order(self):
        now = time.time()
        self.store.append("satu", now - 30)
        self.store.append("dua", now - 20)
        self.store.append("tiga", now - 10)
        clips = self.store.load()
        self.assertEqual([c.text for c in clips], ["tiga", "dua", "satu"])

    def test_dedupe_keeps_newest(self):
        now = time.time()
        self.store.append("sama", now - 10)
        self.store.append("lain", now - 5)
        self.store.append("sama", now)
        clips = self.store.load()
        self.assertEqual(len(clips), 2)
        self.assertEqual(clips[0].text, "sama")

    def test_limit(self):
        now = time.time()
        for index in range(6):
            self.store.append(f"item-{index}", now + index)
        self.assertEqual(len(self.store.load()), 3)
        self.assertEqual(self.store.load()[0].text, "item-5")

    def test_pin_survives_limit(self):
        now = time.time()
        self.store.append("pinned", now - 100)
        self.store.toggle_pin(self.store.load()[0])
        for index in range(6):
            self.store.append(f"baru-{index}", now + index)
        clips = self.store.load()
        self.assertTrue(clips[0].pinned)
        self.assertEqual(clips[0].text, "pinned")
        self.assertLessEqual(len(clips), 5)  # 3 biasa + pinned tetap disimpan

    def test_delete_then_recopy(self):
        now = time.time()
        self.store.append("hapus aku", now - 10)
        clip = self.store.load()[0]
        self.store.delete(clip)
        self.assertEqual(self.store.load(), [])
        self.store.append("hapus aku", now + 5)
        self.assertEqual([c.text for c in self.store.load()], ["hapus aku"])

    def test_clear(self):
        self.store.append("a")
        self.store.append("b")
        self.store.toggle_pin(self.store.load()[0])
        self.store.clear()
        self.assertEqual(self.store.load(), [])
        state = json.loads(self.state.read_text())
        self.assertEqual(state, {"pins": {}, "deleted": {}})

    def test_compact(self):
        for index in range(200):
            self.store.append(f"baris {index}")
        self.store.compact_if_needed()
        self.assertLessEqual(len(self.store.load()), 3)

    def test_corrupt_lines_ignored(self):
        self.spool.write_text('bukan json\n{"t": "valid", "ts": 1}\n\n')
        clips = self.store.load()
        self.assertEqual([c.text for c in clips], ["valid"])

    def test_key_stable(self):
        self.assertEqual(clip_key("abc"), clip_key("abc"))
        self.assertNotEqual(clip_key("abc"), clip_key("abd"))


class BridgeHelperTests(unittest.TestCase):
    def test_gvariant_list(self):
        from powertools.cli import _gvariant_list

        self.assertEqual(_gvariant_list([]), "[]")
        self.assertEqual(_gvariant_list(["a@b"]), "['a@b']")
        self.assertEqual(_gvariant_list(["a", "b@c"]), "['a', 'b@c']")
        # Kutip tunggal tidak boleh merusak literal GVariant.
        self.assertEqual(_gvariant_list(["o'neil"]), "['oneil']")


class ExpiryTests(unittest.TestCase):
    """Batas usia clipboard (clipboard.max_age_minutes)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.images = base / "images"
        self.thumbs = base / "thumbs"
        self.images.mkdir()
        self.thumbs.mkdir()
        self.spool = base / "clipboard.jsonl"
        self.state = base / "state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _store(self, max_age_seconds: float) -> ClipStore:
        return ClipStore(
            spool_path=self.spool,
            state_path=self.state,
            images_dir=self.images,
            thumbs_dir=self.thumbs,
            max_items=10,
            max_age_seconds=max_age_seconds,
        )

    def _write(self, *records: dict) -> None:
        self.spool.write_text(
            "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
        )

    def _old_image(self, name: str, size: int = 64) -> Path:
        path = self.images / name
        path.write_bytes(b"x" * size)
        old = time.time() - 3600
        os.utime(path, (old, old))
        return path

    def test_teks_kedaluwarsa_tidak_muncul(self):
        now = time.time()
        self._write(
            {"t": "lama", "ts": now - 120},
            {"t": "baru", "ts": now - 5},
        )
        clips = self._store(60).load()
        self.assertEqual([c.text for c in clips], ["baru"])

    def test_item_dipin_tetap_ada_walau_tua(self):
        now = time.time()
        self._write({"t": "penting", "ts": now - 9999})
        base = self._store(0)
        clip = base.load()[0]
        base.toggle_pin(clip)

        clips = self._store(60).load()
        self.assertEqual([c.text for c in clips], ["penting"])
        self.assertTrue(clips[0].pinned)

    def test_gambar_kedaluwarsa_dihapus_saat_prune(self):
        now = time.time()
        path = self._old_image("lama")
        self._write({"i": "lama", "m": "image/png", "n": 64, "ts": now - 3600})

        store = self._store(60)
        self.assertEqual(store.load(), [])
        store.prune()

        self.assertFalse(path.exists())
        self.assertEqual(self.spool.read_text(encoding="utf-8").strip(), "")

    def test_prune_mempertahankan_yang_masih_berlaku(self):
        now = time.time()
        self._write(
            {"t": "lama", "ts": now - 3600},
            {"t": "baru", "ts": now - 5},
        )
        store = self._store(60)
        store.prune()
        lines = [
            json.loads(line)
            for line in self.spool.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual([r["t"] for r in lines], ["baru"])

    def test_nol_berarti_tidak_pernah_kedaluwarsa(self):
        self._write({"t": "sangat lama", "ts": 1})
        self.assertEqual(len(self._store(0).load()), 1)


class ConfigDefaultsTests(unittest.TestCase):
    def test_default_clipboard_baru(self):
        from powertools import config

        self.assertEqual(config.DEFAULTS["clipboard"]["max_items"], 300)
        self.assertEqual(config.DEFAULTS["clipboard"]["max_age_minutes"], 0)
        self.assertTrue(config.DEFAULTS["clipboard"]["auto_paste"])

    def test_default_lirik(self):
        from powertools import config

        self.assertIn("lyrics", config.MODULES)
        self.assertFalse(config.DEFAULTS["lyrics"]["auto_show"])
        self.assertFalse(config.DEFAULTS["lyrics"]["pinned"])
        self.assertEqual(config.DEFAULTS["lyrics"]["offset_ms"], 0)
        self.assertEqual(config.DEFAULTS["lyrics"]["font_size"], 15)

    def test_default_discord_opt_in(self):
        from powertools import config

        self.assertIn("discord", config.MODULES)
        self.assertFalse(config.DEFAULTS["modules"]["discord"])
        self.assertEqual(config.DEFAULTS["discord"]["app_id"], "")
        self.assertEqual(config.DEFAULTS["discord"]["app_ids"], {})
        self.assertTrue(config.DEFAULTS["discord"]["use_builtin_ids"])
        self.assertEqual(config.DEFAULTS["discord"]["fallback_asset"], "")
        self.assertFalse(config.DEFAULTS["discord"]["show_paused"])
        self.assertTrue(config.DEFAULTS["discord"]["show_album_art"])

    def test_config_lama_tidak_otomatis_menyalakan_discord(self):
        from powertools import config

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps({"modules": {"clipboard": True, "lyrics": True}}),
                encoding="utf-8",
            )
            cfg = config.Config(path)
            self.assertFalse(cfg.module_enabled("discord"))
            self.assertTrue(cfg.module_enabled("lyrics"))

    def test_override_json_digabung_dengan_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {"clipboard": {"max_items": 5, "max_age_minutes": 90}}
                ),
                encoding="utf-8",
            )
            cfg = Config(path)
            self.assertEqual(cfg.get("clipboard.max_items"), 5)
            self.assertEqual(cfg.get("clipboard.max_age_minutes"), 90)
            self.assertEqual(cfg.get("clipboard.max_chars"), 100_000)

    def test_parse_nilai_cli(self):
        self.assertIs(parse_config_value("true"), True)
        self.assertIs(parse_config_value("off"), False)
        self.assertEqual(parse_config_value("1440"), 1440)
        self.assertEqual(parse_config_value("1"), 1)
        self.assertEqual(parse_config_value("0"), 0)
        self.assertEqual(parse_config_value("0.5"), 0.5)
        self.assertEqual(parse_config_value("dark"), "dark")


class ImageStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.images = base / "images"
        self.thumbs = base / "thumbs"
        self.images.mkdir()
        self.thumbs.mkdir()
        self.spool = base / "clipboard.jsonl"
        self.state = base / "state.json"
        self.store = ClipStore(
            spool_path=self.spool,
            state_path=self.state,
            images_dir=self.images,
            thumbs_dir=self.thumbs,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _image(self, name: str, size: int = 64) -> None:
        (self.images / name).write_bytes(b"x" * size)

    def test_image_record_dibaca(self):
        self._image("abc123", 128)
        self.spool.write_text('{"i": "abc123", "m": "image/png", "n": 128, "ts": 5}\n')
        clips = self.store.load()
        self.assertEqual(len(clips), 1)
        clip = clips[0]
        self.assertTrue(clip.is_image)
        self.assertEqual(clip.key, "img:abc123")
        self.assertEqual(clip.size, 128)
        self.assertEqual(clip.mime, "image/png")

    def test_gambar_hilang_dilewati(self):
        self.spool.write_text('{"i": "hilang", "m": "image/png", "n": 10, "ts": 1}\n')
        self.assertEqual(self.store.load(), [])

    def test_nama_berkas_tidak_boleh_keluar_folder(self):
        self.spool.write_text('{"i": "../evil", "ts": 1}\n')
        self.assertEqual(self.store.load(), [])

    def test_dedup_gambar_berdasarkan_hash(self):
        self._image("dup", 8)
        self.spool.write_text(
            '{"i": "dup", "m": "image/png", "n": 8, "ts": 1}\n'
            '{"i": "dup", "m": "image/png", "n": 8, "ts": 9}\n'
        )
        clips = self.store.load()
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0].ts, 9)

    def test_kuota_gambar_terpisah_dari_teks(self):
        for index in range(5):
            self.store.append(f"teks {index}", ts=index)
        for index in range(5):
            self._image(f"img{index}")
            self.store.append_image(f"img{index}", "image/png", 64, ts=100 + index)
        store = ClipStore(
            spool_path=self.spool,
            state_path=self.state,
            images_dir=self.images,
            thumbs_dir=self.thumbs,
            max_items=3,
            max_image_items=2,
        )
        clips = store.load()
        self.assertEqual(sum(1 for c in clips if c.is_image), 2)
        self.assertEqual(sum(1 for c in clips if not c.is_image), 3)

    def test_hapus_gambar_ikut_menghapus_berkas(self):
        self._image("hapus")
        self.spool.write_text('{"i": "hapus", "m": "image/png", "n": 64, "ts": 1}\n')
        clip = self.store.load()[0]
        self.store.delete(clip)
        self.assertFalse((self.images / "hapus").exists())
        self.assertEqual(self.store.load(), [])

    def test_mime_dideteksi_dari_isi_berkas(self):
        (self.images / "foto").write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 60)
        self.spool.write_text(
            '{"i": "foto", "m": "image/png", "n": 64, "ts": 1}\n'
        )
        clip = self.store.load()[0]
        self.assertEqual(clip.mime, "image/jpeg")

    def test_mime_aneh_diganti_default(self):
        self._image("aman")
        self.spool.write_text(
            '{"i": "aman", "m": "text/html", "n": 64, "ts": 1}\n'
        )
        clip = self.store.load()[0]
        self.assertEqual(clip.mime, "image/png")

    def test_prune_membuang_gambar_yatim(self):
        self._image("dipakai")
        self._image("yatim")
        self.spool.write_text(
            '{"i": "dipakai", "m": "image/png", "n": 64, "ts": 1}\n'
        )
        old = time.time() - 120
        os.utime(self.images / "yatim", (old, old))
        self.store.prune()
        self.assertTrue((self.images / "dipakai").exists())
        self.assertFalse((self.images / "yatim").exists())

    def test_rewrite_mempertahankan_gambar(self):
        self._image("tetap")
        self.store.append_image("tetap", "image/png", 64, ts=2)
        self.store.compact_if_needed()
        self.store._rewrite(self.store.load())
        clips = self.store.load()
        self.assertEqual(len(clips), 1)
        self.assertTrue(clips[0].is_image)


class ColorTests(unittest.TestCase):
    def test_hex_bolak_balik(self):
        self.assertEqual(hex_to_rgb("#FF8000"), (255, 128, 0))
        self.assertEqual(hex_to_rgb("#f80"), (255, 136, 0))
        self.assertEqual(rgb_to_hex((255, 128, 0)), "#FF8000")
        self.assertEqual(rgb_to_hex((300, -5, 12.4)), "#FF000C")
        with self.assertRaises(ValueError):
            hex_to_rgb("#12345")

    def test_hsl_nilai_dasar(self):
        self.assertEqual(rgb_to_hsl(255, 0, 0), (0.0, 100.0, 50.0))
        self.assertEqual(rgb_to_hsl(0, 255, 0), (120.0, 100.0, 50.0))
        self.assertEqual(rgb_to_hsl(0, 0, 255), (240.0, 100.0, 50.0))
        self.assertEqual(rgb_to_hsl(255, 255, 255), (0.0, 0.0, 100.0))
        self.assertEqual(rgb_to_hsl(0, 0, 0), (0.0, 0.0, 0.0))
        self.assertEqual(rgb_to_hsl(128, 128, 128), (0.0, 0.0, 50.19607843137255))

    def test_hsl_bolak_balik(self):
        for color in ((255, 0, 0), (60, 120, 200), (12, 200, 99), (250, 250, 250)):
            back = hsl_to_rgb(*rgb_to_hsl(*color))
            for asli, hasil in zip(color, back):
                self.assertLessEqual(abs(asli - hasil), 1, (color, back))

    def test_cmyk(self):
        self.assertEqual(rgb_to_cmyk(255, 0, 0), (0, 100, 100, 0))
        self.assertEqual(rgb_to_cmyk(255, 255, 255), (0, 0, 0, 0))
        self.assertEqual(rgb_to_cmyk(0, 0, 0), (0, 0, 0, 100))
        self.assertEqual(rgb_to_cmyk(128, 128, 128), (0, 0, 0, 50))

    def test_format_css(self):
        merah = (255, 0, 0)
        self.assertEqual(format_color(merah, "hex"), "#FF0000")
        self.assertEqual(format_color(merah, "rgb"), "rgb(255, 0, 0)")
        self.assertEqual(format_color(merah, "hsl"), "hsl(0, 100%, 50%)")
        self.assertEqual(
            format_color(merah, "cmyk"), "device-cmyk(0% 100% 100% 0%)"
        )
        self.assertEqual(format_color(merah, "tidak-dikenal"), "#FF0000")

    def test_shade_terang_gelap(self):
        self.assertEqual(shade_color((0, 0, 0), 0.2), (51, 51, 51))
        self.assertEqual(shade_color((255, 255, 255), -0.2), (204, 204, 204))
        # Tidak pernah melewati batas meski diminta 100%.
        self.assertEqual(shade_color((255, 255, 255), 1.0), (255, 255, 255))
        self.assertEqual(shade_color((0, 0, 0), -1.0), (0, 0, 0))

    def test_deret_shade(self):
        shades = color_shades((100, 150, 200))
        self.assertEqual(len(shades), 7)
        self.assertEqual(shades[3][0], "Asli")
        self.assertEqual(shades[3][1], (100, 150, 200))
        self.assertLess(sum(shades[0][1]), sum(shades[3][1]))
        self.assertGreater(sum(shades[-1][1]), sum(shades[3][1]))
        self.assertEqual(shades[0][0], "\u221230%")
        self.assertEqual(shades[-1][0], "+30%")

    def test_palet_rekomendasi(self):
        palette = color_palette((255, 0, 0))
        self.assertEqual(len(palette), 9)
        self.assertEqual(palette[0], ("Asli", (255, 0, 0)))
        self.assertEqual(palette[1][0], "Komplementer")
        self.assertEqual(palette[1][1], (0, 255, 255))
        self.assertEqual(len({warna for _nama, warna in palette}), 9)

    def test_kontras_teks(self):
        self.assertEqual(contrast_color((255, 255, 255)), "#111111")
        self.assertEqual(contrast_color((0, 0, 0)), "#ffffff")
        self.assertEqual(contrast_color((255, 255, 0)), "#111111")


class LyricsTests(unittest.TestCase):
    """Parsing LRC dan pemilihan rekaman LRCLIB (tanpa jaringan/GTK)."""

    def test_parse_lrc_urut_dan_multi_timestamp(self):
        from powertools.lyrics import parse_lrc

        text = (
            "[ti:Uji]\n[ar:Artis]\n"
            "[00:12.50]Baris satu\n"
            "[00:20.00][01:00.00]Baris dua\n"
            "[00:05.5]Pembuka\n"
        )
        lines = parse_lrc(text)
        self.assertEqual(
            [(line.time, line.text) for line in lines],
            [
                (5.5, "Pembuka"),
                (12.5, "Baris satu"),
                (20.0, "Baris dua"),
                (60.0, "Baris dua"),
            ],
        )

    def test_parse_lrc_offset_dan_baris_kosong(self):
        from powertools.lyrics import parse_lrc

        text = "[offset:500]\n[00:10.00]Halo\n[00:15.00]\n[00:99.00]Rusak\n"
        lines = parse_lrc(text)
        self.assertEqual([(line.time, line.text) for line in lines], [(10.5, "Halo")])

    def test_parse_lrc_offset_tambahan(self):
        from powertools.lyrics import parse_lrc

        lines = parse_lrc("[00:10.00]Halo\n", offset_ms=-1500)
        self.assertEqual(lines[0].time, 8.5)

    def test_parse_plain_memisahkan_bait(self):
        from powertools.lyrics import parse_plain

        text = "\n\nBait satu\nbaris dua\n\n\nBait dua\n\n"
        self.assertEqual(
            parse_plain(text), ["Bait satu", "baris dua", "", "Bait dua"]
        )

    def test_parse_kosong(self):
        from powertools.lyrics import parse_lrc, parse_plain

        self.assertEqual(parse_lrc(None), [])
        self.assertEqual(parse_lrc(""), [])
        self.assertEqual(parse_plain(None), [])

    def test_clean_title(self):
        from powertools.lyrics import clean_title

        self.assertEqual(clean_title("Super Shy (Official Video)"), "Super Shy")
        self.assertEqual(clean_title("Lagu (feat. Artis Lain)"), "Lagu")
        self.assertEqual(clean_title("Song - Live"), "Song")
        self.assertEqual(clean_title("  Halo   Dunia  "), "Halo Dunia")
        # Judul tanpa hiasan tidak boleh berubah.
        self.assertEqual(clean_title("Bintang Di Surga"), "Bintang Di Surga")

    def test_cache_key_stabil(self):
        from powertools.lyrics import cache_key

        self.assertEqual(
            cache_key("Lagu", "Artis", "Album", 200.2),
            cache_key("  lagu ", "ARTIS", "album", 200.4),
        )
        self.assertNotEqual(
            cache_key("Lagu", "Artis"), cache_key("Lagu Lain", "Artis")
        )

    def test_pilih_rekaman_sinkron(self):
        from powertools.lyrics import _pick_record

        records = [
            {
                "trackName": "Lagu",
                "artistName": "Artis",
                "duration": 200,
                "plainLyrics": "tanpa timestamp",
            },
            {
                "trackName": "Lagu",
                "artistName": "Artis",
                "duration": 201,
                "syncedLyrics": "[00:01.00]dengan timestamp",
                "plainLyrics": "dengan timestamp",
            },
        ]
        picked = _pick_record(records, "Lagu", "Artis", 200)
        self.assertIsNotNone(picked)
        self.assertTrue(picked.get("syncedLyrics"))

    def test_rekaman_tanpa_lirik_diabaikan(self):
        from powertools.lyrics import _pick_record

        self.assertIsNone(
            _pick_record([{"trackName": "X", "artistName": "Y"}], "X", "Y", 0)
        )

    def test_rekaman_ke_lirik(self):
        from powertools.lyrics import _record_to_lyrics

        result = _record_to_lyrics(
            {
                "id": 7,
                "trackName": "Lagu",
                "artistName": "Artis",
                "albumName": "Album",
                "duration": 210.5,
                "syncedLyrics": "[00:01.00]Halo\n[00:02.00]Dunia\n",
                "plainLyrics": "Halo\nDunia\n",
            }
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.has_synced)
        self.assertTrue(result.has_any)
        self.assertEqual(result.record_id, 7)
        self.assertEqual([line.text for line in result.synced], ["Halo", "Dunia"])

    def test_instrumental(self):
        from powertools.lyrics import _record_to_lyrics

        result = _record_to_lyrics({"trackName": "X", "instrumental": True})
        self.assertIsNotNone(result)
        self.assertTrue(result.instrumental)
        self.assertTrue(result.has_any)

    def test_retry_after_dibatasi(self):
        import urllib.error

        from powertools.lyrics import _retry_after_seconds

        err = urllib.error.HTTPError(
            "https://lrclib.net", 429, "Too Many Requests",
            {"Retry-After": "120"}, None,
        )
        self.assertEqual(_retry_after_seconds(err), 120.0)
        tanpa_header = urllib.error.HTTPError(
            "https://lrclib.net", 429, "Too Many Requests", {}, None
        )
        self.assertEqual(_retry_after_seconds(tanpa_header), 30.0)
        aneh = urllib.error.HTTPError(
            "https://lrclib.net", 429, "Too Many Requests",
            {"Retry-After": "nanti"}, None,
        )
        self.assertEqual(_retry_after_seconds(aneh), 30.0)


class RomanizeTests(unittest.TestCase):
    """Transliterasi Hangul dan pemilihan rekaman romaji (tanpa jaringan)."""

    ROMAJI = (
        "Yume naraba dore hodo yokatta deshou\n"
        "Imada ni anata no koto wo yume ni miru\n"
        "Wasureta mono wo tori ni kaeru you ni\n"
        "Furubita omoide no hokori wo harau\n"
    )
    INGGRIS = (
        "The club isn't the best place to find a lover\n"
        "So the bar is where I go\n"
        "Me and my friends at the table doing shots\n"
        "Drinking fast and then we talk slow\n"
    )
    JEPANG = "夢ならばどれほどよかったでしょう\n未だにあなたのことを夢にみる\n"

    def test_transliterasi_hangul_dasar(self):
        from powertools.lyrics import romanize_hangul

        self.assertEqual(romanize_hangul("밤"), "bam")
        self.assertEqual(romanize_hangul("한국어"), "hangugeo")
        self.assertEqual(romanize_hangul("좋아"), "joa")
        self.assertEqual(romanize_hangul("빛이 나는 솔로"), "bichi naneun sollo")
        self.assertEqual(
            romanize_hangul("너의 이름을 부르는 밤"),
            "neoui ireumeul bureuneun bam",
        )

    def test_transliterasi_membiarkan_teks_lain(self):
        from powertools.lyrics import romanize_hangul

        self.assertEqual(romanize_hangul(""), "")
        self.assertEqual(romanize_hangul("BTS - Dynamite"), "BTS - Dynamite")
        self.assertEqual(romanize_hangul("사랑해 love"), "saranghae love")

    def test_deteksi_kebutuhan_romaji(self):
        from powertools.lyrics import Lyrics, needs_romanization

        self.assertTrue(needs_romanization(Lyrics(plain=["너의 이름을 부르는 밤"])))
        self.assertTrue(needs_romanization(Lyrics(plain=["夢ならば"])))
        self.assertFalse(needs_romanization(Lyrics(plain=["Just a normal song"])))
        self.assertFalse(needs_romanization(Lyrics()))

    def test_tebakan_romaji_membedakan_terjemahan(self):
        from powertools.lyrics import looks_romanized

        self.assertTrue(looks_romanized(self.ROMAJI))
        self.assertFalse(looks_romanized(self.INGGRIS))
        self.assertFalse(looks_romanized(self.JEPANG))

    def test_pilih_rekaman_romaji(self):
        from powertools.lyrics import Lyrics, LyricLine, _pick_romaji_record

        base = Lyrics(
            duration=256.0,
            record_id=5394410,
            synced=[LyricLine(1.0, "夢ならば")],
            plain=["夢ならば", "未だにあなたのことを夢にみる"],
        )
        records = [
            # Rekaman asli (kanji) — tidak boleh terpilih.
            {
                "id": 5394410,
                "duration": 256,
                "syncedLyrics": "[00:01.00]夢ならば\n",
                "plainLyrics": self.JEPANG,
            },
            # Terjemahan bahasa Inggris — proporsi vokalnya terlalu rendah.
            {
                "id": 2,
                "duration": 256,
                "syncedLyrics": "[00:01.00]If only it were a dream\n",
                "plainLyrics": self.INGGRIS,
            },
            # Durasi jauh berbeda — sorotan akan meleset.
            {
                "id": 3,
                "duration": 140,
                "syncedLyrics": "[00:01.00]Yume naraba\n",
                "plainLyrics": self.ROMAJI,
            },
            # Kandidat yang benar.
            {
                "id": 4,
                "duration": 256,
                "syncedLyrics": "[00:00.92]Yume naraba dore hodo yokatta deshou\n",
                "plainLyrics": self.ROMAJI,
            },
        ]
        picked = _pick_romaji_record(records, base)
        self.assertIsNotNone(picked)
        self.assertEqual(picked["id"], 4)

    def test_romaji_butuh_timestamp_jika_aslinya_sinkron(self):
        from powertools.lyrics import Lyrics, LyricLine, _pick_romaji_record

        base = Lyrics(
            duration=200.0,
            synced=[LyricLine(1.0, "너의 이름을")],
        )
        records = [{
            "id": 9,
            "duration": 200,
            "plainLyrics": "neoui ireumeul bureuneun bam",
        }]
        self.assertIsNone(_pick_romaji_record(records, base))

    def test_transliterasi_lokal_korea(self):
        from powertools.lyrics import Lyrics, LyricLine, _apply_transliteration

        result = Lyrics(
            synced=[LyricLine(1.0, "너의 이름을"), LyricLine(4.0, "부르는 밤")],
            plain=["너의 이름을", "", "부르는 밤"],
        )
        self.assertTrue(_apply_transliteration(result))
        self.assertEqual(
            [(line.time, line.text) for line in result.romaji],
            [(1.0, "neoui ireumeul"), (4.0, "bureuneun bam")],
        )
        self.assertEqual(result.romaji_plain, ["neoui ireumeul", "", "bureuneun bam"])
        self.assertEqual(result.romaji_source, "transliterasi lokal")
        self.assertTrue(result.has_romaji)

    def test_jepang_tanpa_unggahan_romaji_dibiarkan(self):
        from powertools.lyrics import Lyrics, _apply_transliteration

        result = Lyrics(plain=["夢ならばどれほどよかったでしょう"])
        self.assertFalse(_apply_transliteration(result))
        self.assertFalse(result.has_romaji)

    def test_baris_judul_romaji_dibuang(self):
        from powertools.lyrics import Lyrics, _apply_romaji

        base = Lyrics(track="Lemon", artist="Kenshi Yonezu")
        record = {
            "syncedLyrics": (
                "[00:00.00] Lemon - Kenshi Yonezu\n"
                "[00:00.92] Yume naraba dore hodo yokatta deshou\n"
            ),
            "plainLyrics": (
                "Lemon - Kenshi Yonezu\nYume naraba dore hodo yokatta deshou\n"
            ),
        }
        self.assertTrue(_apply_romaji(base, record))
        self.assertEqual(
            [line.text for line in base.romaji],
            ["Yume naraba dore hodo yokatta deshou"],
        )
        self.assertEqual(
            base.romaji_plain, ["Yume naraba dore hodo yokatta deshou"]
        )

    def test_baris_lirik_di_detik_nol_tidak_dibuang(self):
        from powertools.lyrics import Lyrics, _apply_romaji

        base = Lyrics(track="Lemon", artist="Kenshi Yonezu")
        record = {
            "syncedLyrics": "[00:00.00] Yume naraba dore hodo\n",
            "plainLyrics": "Yume naraba dore hodo\n",
        }
        self.assertTrue(_apply_romaji(base, record))
        self.assertEqual([line.text for line in base.romaji], ["Yume naraba dore hodo"])

    def test_display_text_tolak_romaji_tanpa_timestamp(self):
        from powertools.lyrics import Lyrics, LyricLine, display_text

        synced_only = Lyrics(
            synced=[LyricLine(1.0, "夢ならば")],
            romaji_plain=["Yume naraba"],
            romaji_source="lrclib.net",
        )
        lines, _plain, source = display_text(synced_only, True)
        self.assertEqual([line.text for line in lines], ["夢ならば"])
        self.assertEqual(source, "lrclib.net")

    def test_display_text_bolak_balik(self):
        from powertools.lyrics import Lyrics, LyricLine, display_text

        result = Lyrics(
            synced=[LyricLine(1.0, "너의 이름을")],
            romaji=[LyricLine(1.0, "neoui ireumeul")],
            romaji_source="transliterasi lokal",
        )
        asli, _p, source_asli = display_text(result, False)
        romaji, _p2, source_romaji = display_text(result, True)
        self.assertEqual([l.text for l in asli], ["너의 이름을"])
        self.assertEqual([l.text for l in romaji], ["neoui ireumeul"])
        self.assertEqual(source_asli, "lrclib.net")
        self.assertEqual(source_romaji, "romaji (transliterasi lokal)")
        # Timestamp harus identik supaya sorotan tidak bergeser.
        self.assertEqual(asli[0].time, romaji[0].time)

    def test_display_text_tanpa_romaji(self):
        from powertools.lyrics import Lyrics, LyricLine, display_text

        result = Lyrics(synced=[LyricLine(1.0, "それは予想通り")])
        lines, _plain, _source = display_text(result, True)
        self.assertEqual([line.text for line in lines], ["それは予想通り"])

    def test_display_text_lirik_plain(self):
        from powertools.lyrics import Lyrics, display_text

        result = Lyrics(
            plain=["너의 이름을"],
            romaji_plain=["neoui ireumeul"],
            romaji_source="transliterasi lokal",
        )
        lines, plain, _source = display_text(result, True)
        self.assertEqual(lines, [])
        self.assertEqual(plain, ["neoui ireumeul"])

    def test_cache_lama_tanpa_romaji_dianggap_kedaluwarsa(self):
        import time

        from powertools.lyrics import (
            Lyrics,
            LyricLine,
            _lyrics_to_cache,
            _romaji_missing,
        )

        korea = Lyrics(synced=[LyricLine(1.0, "너의 이름을")])
        inggris = Lyrics(synced=[LyricLine(1.0, "just a normal song")])

        # Cache lama: tidak punya penanda romaji_checked.
        lama = {"lyrics": {"synced": [[1.0, "너의 이름을"]]}}
        self.assertTrue(_romaji_missing(lama, korea))
        # Lagu Latin tidak perlu diambil ulang.
        self.assertFalse(_romaji_missing(lama, inggris))
        # Cache baru: sudah ditandai, tidak perlu diambil ulang lagi.
        baru = {"saved": time.time(), "lyrics": _lyrics_to_cache(korea)}
        self.assertFalse(_romaji_missing(baru, korea))

    def test_cache_jepang_tanpa_romaji_tetap_ditandai(self):
        from powertools.lyrics import Lyrics, _lyrics_to_cache

        # Lagu Jepang yang tidak punya unggahan romaji: penanda tetap ditulis
        # supaya tidak diambil ulang setiap kali diputar.
        jepang = Lyrics(plain=["夢ならばどれほどよかったでしょう"])
        data = _lyrics_to_cache(jepang)
        self.assertTrue(data.get("romaji_checked"))
        self.assertEqual(data.get("romaji"), [])

    def test_cache_menyimpan_romaji(self):
        from powertools.lyrics import (
            Lyrics,
            LyricLine,
            _lyrics_from_cache,
            _lyrics_to_cache,
        )

        asli = Lyrics(
            track="Lagu",
            duration=200.0,
            synced=[LyricLine(1.0, "너의 이름을")],
            romaji=[LyricLine(1.0, "neoui ireumeul")],
            romaji_plain=["neoui ireumeul"],
            romaji_source="lrclib.net",
        )
        hasil = _lyrics_from_cache(_lyrics_to_cache(asli))
        self.assertIsNotNone(hasil)
        self.assertEqual([l.text for l in hasil.romaji], ["neoui ireumeul"])
        self.assertEqual(hasil.romaji_plain, ["neoui ireumeul"])
        self.assertEqual(hasil.romaji_source, "lrclib.net")
        self.assertTrue(hasil.has_romaji)


if __name__ == "__main__":
    unittest.main()