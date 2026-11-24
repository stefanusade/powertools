"""Tes deteksi PWA dari jendela pemutar MPRIS (tanpa D-Bus/shell)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powertools import webapps
from powertools.webapps import (
    app_id_from_class,
    app_name_for_owner,
    clear_cache,
    desktop_name_for_app_id,
    pid_from_owner,
    pwa_id_from_windows,
    windows_for_owner,
)

PWA_ID = "cinhimbnkkaeohfgghhklpknlkffjgod"


class PidTests(unittest.TestCase):
    def test_pid_dari_nama_bus(self):
        self.assertEqual(
            pid_from_owner("org.mpris.MediaPlayer2.brave.instance10804"),
            10804,
        )
        self.assertEqual(
            pid_from_owner("org.mpris.MediaPlayer2.chrome.instance42"), 42)

    def test_nama_tanpa_instance(self):
        for owner in ("org.mpris.MediaPlayer2.spotify", "", None):
            self.assertEqual(pid_from_owner(owner), 0)


class ClassTests(unittest.TestCase):
    def test_identitas_pwa_chromium(self):
        for wm_class in (f"crx_{PWA_ID}", f"_crx_{PWA_ID}"):
            self.assertEqual(app_id_from_class(wm_class), PWA_ID, wm_class)

    def test_identitas_pwa_firefox(self):
        self.assertEqual(
            app_id_from_class("FFPWA-01JCZ9W0K3"), "FFPWA-01JCZ9W0K3")

    def test_id_tanpa_awalan(self):
        self.assertEqual(app_id_from_class(PWA_ID), PWA_ID)

    def test_jendela_browser_biasa(self):
        for wm_class in ("brave-browser", "Brave-browser", "firefox",
                         "Navigator", "org.gnome.Nautilus", "", None):
            self.assertEqual(app_id_from_class(wm_class), "", wm_class)


class DesktopTests(unittest.TestCase):
    def _dir(self, name: str, body: str) -> Path:
        root = Path(tempfile.mkdtemp(prefix="powertools-apps-"))
        (root / name).write_text(body, encoding="utf-8")
        return root

    def test_nama_dari_berkas_pwa(self):
        root = self._dir(
            "brave-abc123-Default.desktop",
            "[Desktop Entry]\nType=Application\nName=YouTube Music\n"
            "Exec=/usr/bin/brave --app-id=abc123\n",
        )
        self.assertEqual(
            desktop_name_for_app_id("abc123", [root]), "YouTube Music")

    def test_nama_dari_exec_saja(self):
        root = self._dir(
            "webapp.desktop",
            "[Desktop Entry]\nName=Netflix\n"
            "Exec=/usr/bin/chromium --app-id=xyz789\n",
        )
        self.assertEqual(desktop_name_for_app_id("xyz789", [root]), "Netflix")

    def test_nama_dari_startup_wm_class(self):
        # Bentuk nyata berkas PWA Brave: id hanya ada di Exec dan
        # StartupWMClass, nama berkas memuat id, tetapi id bisa juga hanya
        # dikenali dari kelas jendelanya.
        root = self._dir(
            "aplikasi.desktop",
            "[Desktop Entry]\nName=YouTube Music\n"
            f"StartupWMClass=crx_{PWA_ID}\n",
        )
        self.assertEqual(
            desktop_name_for_app_id(PWA_ID, [root]), "YouTube Music")

    def test_app_id_tidak_dikenal(self):
        root = self._dir(
            "brave-abc123-Default.desktop",
            "[Desktop Entry]\nName=YouTube Music\n",
        )
        self.assertEqual(desktop_name_for_app_id("lain", [root]), "")
        self.assertEqual(desktop_name_for_app_id("", [root]), "")


class WindowMatchTests(unittest.TestCase):
    """Inti perbaikan: satu proses browser, banyak jendela."""

    PWA = f"crx_{PWA_ID}"
    BROWSER = "brave-browser"

    def test_tab_biasa_yang_memutar_bukan_pwa(self):
        # Kasus nyata: YouTube di tab, YouTube Music sebagai PWA (dijeda).
        windows = [
            (self.PWA, "YouTube Music"),
            (self.BROWSER,
             "Saya kira HP ini HOAX doang... Ternyata beneran - Brave"),
        ]
        self.assertEqual(
            pwa_id_from_windows(
                "Saya kira HP ini HOAX doang... Ternyata beneran ada",
                windows,
            ),
            "",
        )

    def test_pwa_yang_memutar_dikenali(self):
        windows = [
            (self.BROWSER, "Beranda - Brave"),
            (self.PWA, "ANTIFRAGILE - LE SSERAFIM - YouTube Music"),
        ]
        self.assertEqual(pwa_id_from_windows("ANTIFRAGILE", windows), PWA_ID)

    def test_judul_pendek_tidak_dicocokkan(self):
        windows = [
            (self.PWA, "Lagu - YouTube Music"),
            (self.BROWSER, "Beranda - Brave"),
        ]
        self.assertEqual(pwa_id_from_windows("Lagu", windows), "")

    def test_semua_jendela_pwa_yang_sama(self):
        windows = [(self.PWA, "YouTube Music"), (self.PWA, "Antrian")]
        self.assertEqual(
            pwa_id_from_windows("Tanpa judul cocok", windows), PWA_ID)

    def test_pwa_dan_tab_tanpa_cocok_judul(self):
        windows = [(self.PWA, "YouTube Music"), (self.BROWSER, "Beranda")]
        self.assertEqual(pwa_id_from_windows("Judul lain sekali", windows), "")

    def test_tanpa_jendela(self):
        self.assertEqual(pwa_id_from_windows("Judul apa saja", []), "")
        self.assertEqual(pwa_id_from_windows("Judul apa saja", None), "")


class OwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()
        self.addCleanup(clear_cache)
        self.apps = Path(tempfile.mkdtemp(prefix="powertools-apps-"))
        (self.apps / f"brave-{PWA_ID}-Default.desktop").write_text(
            "[Desktop Entry]\nName=YouTube Music\n", encoding="utf-8")
        self.owner = "org.mpris.MediaPlayer2.brave.instance4242"

    def test_pwa_terbukti(self):
        self.assertEqual(
            app_name_for_owner(
                self.owner,
                "ANTIFRAGILE",
                dirs=[self.apps],
                windows=[(f"crx_{PWA_ID}", "ANTIFRAGILE - YouTube Music")],
            ),
            "YouTube Music",
        )

    def test_tab_biasa_tidak_punya_nama_aplikasi(self):
        self.assertEqual(
            app_name_for_owner(
                self.owner,
                "Video panjang sekali",
                dirs=[self.apps],
                windows=[("brave-browser", "Video panjang sekali - Brave")],
            ),
            "",
        )

    def test_tanpa_jendela_tidak_menebak(self):
        # Ekstensi lama / shell tidak tersedia: jangan mengarang PWA.
        self.assertEqual(
            app_name_for_owner(self.owner, "Video", dirs=[self.apps], windows=[]),
            "",
        )


class BridgeTests(unittest.TestCase):
    """Jembatan yang tidak mendukung daftar jendela hanya dicoba sekali."""

    def setUp(self) -> None:
        clear_cache()
        self.addCleanup(clear_cache)
        self.calls = 0
        self.original = webapps.bridge_windows

        def fake(pid, timeout_ms=400):
            self.calls += 1
            return None

        webapps.bridge_windows = fake
        self.addCleanup(setattr, webapps, "bridge_windows", self.original)

    def test_ekstensi_lama_diingat(self):
        owner = "org.mpris.MediaPlayer2.brave.instance77"
        self.assertEqual(windows_for_owner(owner), [])
        self.assertEqual(windows_for_owner(owner), [])
        self.assertEqual(self.calls, 1)

    def test_muat_ulang_mencoba_lagi(self):
        owner = "org.mpris.MediaPlayer2.brave.instance77"
        windows_for_owner(owner)
        clear_cache()
        windows_for_owner(owner)
        self.assertEqual(self.calls, 2)

    def test_tanpa_pid_tidak_memanggil(self):
        self.assertEqual(windows_for_owner("org.mpris.MediaPlayer2.spotify"), [])
        self.assertEqual(self.calls, 0)


if __name__ == "__main__":
    unittest.main()
