"""Tes deteksi PWA dari jendela pemutar MPRIS (tanpa D-Bus/shell)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powertools import webapps
from powertools.sources import detect_source, source_key
from powertools.webapps import (
    BrowserContext,
    app_id_from_class,
    app_name_for_owner,
    clear_cache,
    context_from_windows,
    context_for_owner,
    desktop_name_for_app_id,
    desktop_name_for_class,
    pid_from_owner,
    pwa_id_from_windows,
    windows_for_owner,
)

PWA_ID = "cinhimbnkkaeohfgghhklpknlkffjgod"

#: Bentuk nyata dari sesi pengguna (Brave di Wayland).
PWA_CLASS = f"brave-{PWA_ID}-Default"
BROWSER_CLASS = "brave-browser"
PWA_WINDOW = (PWA_CLASS, "YouTube Music")
VIDEO_TITLE = (
    "(529) Red Alert 2 | Yuri's Revenge | 7 vs 1 | Extra hard AI | Random | "
    "France | Giand Cannon"
)
VIDEO_WINDOW = (BROWSER_CLASS, f"{VIDEO_TITLE} - YouTube - Brave")


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
        for wm_class in (
            f"crx_{PWA_ID}",
            f"_crx_{PWA_ID}",
            PWA_ID,
            PWA_CLASS,
            f"google-chrome-{PWA_ID}-Profile 1",
        ):
            self.assertEqual(app_id_from_class(wm_class), PWA_ID, wm_class)

    def test_identitas_pwa_firefox(self):
        self.assertEqual(
            app_id_from_class("FFPWA-01JCZ9W0K3"), "FFPWA-01JCZ9W0K3")

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
            f"{PWA_CLASS}.desktop",
            "[Desktop Entry]\nType=Application\nName=YouTube Music\n"
            f"Exec=/usr/bin/brave --app-id={PWA_ID}\n",
        )
        self.assertEqual(
            desktop_name_for_app_id(PWA_ID, [root]), "YouTube Music")

    def test_nama_dari_nama_berkas_yang_sama_dengan_kelas(self):
        root = self._dir(
            f"{PWA_CLASS}.desktop",
            "[Desktop Entry]\nName=YouTube Music\n",
        )
        self.assertEqual(
            desktop_name_for_class(PWA_CLASS, [root]), "YouTube Music")

    def test_nama_dari_exec_saja(self):
        root = self._dir(
            "webapp.desktop",
            "[Desktop Entry]\nName=Netflix\n"
            "Exec=/usr/bin/chromium --app-id=xyz789\n",
        )
        self.assertEqual(desktop_name_for_app_id("xyz789", [root]), "Netflix")

    def test_nama_dari_startup_wm_class(self):
        root = self._dir(
            "aplikasi.desktop",
            "[Desktop Entry]\nName=YouTube Music\n"
            f"StartupWMClass=crx_{PWA_ID}\n",
        )
        self.assertEqual(
            desktop_name_for_app_id(PWA_ID, [root]), "YouTube Music")

    def test_app_id_tidak_dikenal(self):
        root = self._dir(
            f"{PWA_CLASS}.desktop",
            "[Desktop Entry]\nName=YouTube Music\n",
        )
        self.assertEqual(desktop_name_for_app_id("lain", [root]), "")
        self.assertEqual(desktop_name_for_app_id("", [root]), "")


class WindowMatchTests(unittest.TestCase):
    """Satu proses browser, banyak jendela: pilih jendela sumbernya."""

    BROWSER = BROWSER_CLASS

    def test_tab_yang_memutar_bukan_pwa(self):
        windows = [PWA_WINDOW, VIDEO_WINDOW]
        self.assertEqual(pwa_id_from_windows(VIDEO_TITLE, windows), "")
        context = context_from_windows(VIDEO_TITLE, windows)
        self.assertFalse(context.pwa)
        self.assertIn("YouTube", context.window_title)

    def test_judul_pendek_di_awalan_jendela(self):
        # Lagu pendek ("REDRED") tetap dikenali dari awalan judul jendela.
        windows = [
            PWA_WINDOW,
            (self.BROWSER, "REDRED - YouTube - Brave"),
        ]
        context = context_from_windows("REDRED", windows)
        self.assertFalse(context.pwa)
        self.assertEqual(context.window_title, "REDRED - YouTube - Brave")

    def test_judul_terpotong_di_titik_berbeda(self):
        # MPRIS dan judul jendela memotong judul panjang di titik berbeda.
        windows = [
            PWA_WINDOW,
            (self.BROWSER,
             "Saya kira HP ini HOAX doang... Ternyata beneran - Brave"),
        ]
        self.assertEqual(
            pwa_id_from_windows(
                "Saya kira HP ini HOAX doang... Ternyata beneran ada", windows),
            "",
        )

    def test_pwa_yang_memutar_dikenali(self):
        windows = [
            (self.BROWSER, "Beranda - Brave"),
            (PWA_CLASS, "ANTIFRAGILE - LE SSERAFIM - YouTube Music"),
        ]
        self.assertEqual(pwa_id_from_windows("ANTIFRAGILE", windows), PWA_ID)

    def test_judul_pwa_generik_tetap_pwa(self):
        # Jendela PWA hanya berjudul nama aplikasinya: judul lagu tidak akan
        # pernah cocok, tetapi satu-satunya PWA di proses itu adalah sumbernya.
        windows = [PWA_WINDOW, (self.BROWSER, "Beranda - Brave")]
        self.assertEqual(
            pwa_id_from_windows("ANTIFRAGILE", windows), PWA_ID)
        self.assertEqual(pwa_id_from_windows("What is Love?", windows), PWA_ID)

    def test_dua_pwa_berbeda_tidak_ditebak(self):
        windows = [
            PWA_WINDOW,
            (f"brave-{'a' * 32}-Default", "Netflix"),
            (self.BROWSER, "Beranda - Brave"),
        ]
        self.assertEqual(pwa_id_from_windows("ANTIFRAGILE", windows), "")

    def test_semua_jendela_pwa_yang_sama(self):
        windows = [(PWA_CLASS, "YouTube Music"), (PWA_CLASS, "Antrian")]
        self.assertEqual(
            pwa_id_from_windows("Tanpa judul cocok", windows), PWA_ID)

    def test_tanpa_jendela(self):
        self.assertEqual(pwa_id_from_windows("Judul apa saja", []), "")
        self.assertEqual(pwa_id_from_windows("Judul apa saja", None), "")
        self.assertEqual(context_from_windows("Judul", []), BrowserContext())


class SourceTests(unittest.TestCase):
    """Judul jendela adalah satu-satunya petunjuk situs untuk tab biasa."""

    def test_tab_youtube_jadi_sumber_youtube(self):
        self.assertEqual(
            source_key(
                "org.mpris.MediaPlayer2.brave.instance1",
                "Video panjang",
                window_title=f"{VIDEO_TITLE} - YouTube - Brave",
            ),
            "youtube",
        )

    def test_pwa_youtube_music_tetap_youtube_music(self):
        self.assertEqual(
            source_key(
                "org.mpris.MediaPlayer2.brave.instance1",
                "ANTIFRAGILE",
                app_name="YouTube Music",
                window_title="YouTube Music",
            ),
            "youtube-music",
        )

    def test_judul_jendela_tidak_mengalahkan_url(self):
        self.assertEqual(
            source_key(
                "org.mpris.MediaPlayer2.firefox.instance1",
                "Film",
                url="https://www.netflix.com/watch/1",
                window_title="Sesuatu - YouTube - Firefox",
            ),
            "netflix",
        )


class OwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()
        self.addCleanup(clear_cache)
        self.apps = Path(tempfile.mkdtemp(prefix="powertools-apps-"))
        (self.apps / f"{PWA_CLASS}.desktop").write_text(
            "[Desktop Entry]\nName=YouTube Music\n", encoding="utf-8")
        self.owner = "org.mpris.MediaPlayer2.brave.instance4242"

    def test_pwa_terbukti(self):
        self.assertEqual(
            app_name_for_owner(
                self.owner,
                "ANTIFRAGILE",
                dirs=[self.apps],
                windows=[PWA_WINDOW],
            ),
            "YouTube Music",
        )

    def test_tab_biasa_tidak_punya_nama_aplikasi(self):
        self.assertEqual(
            app_name_for_owner(
                self.owner,
                VIDEO_TITLE,
                dirs=[self.apps],
                windows=[PWA_WINDOW, VIDEO_WINDOW],
            ),
            "",
        )

    def test_tanpa_jendela_tidak_menebak(self):
        # Ekstensi lama / shell tidak tersedia: jangan mengarang PWA.
        self.assertEqual(
            app_name_for_owner(self.owner, "Video", dirs=[self.apps], windows=[]),
            "",
        )

    def test_konteks_membawa_judul_jendela(self):
        context = context_for_owner(
            self.owner, VIDEO_TITLE, windows=[PWA_WINDOW, VIDEO_WINDOW])
        self.assertEqual(context.app_name, "")
        self.assertTrue(context.window_title.endswith("YouTube - Brave"))
        self.assertTrue(context)

    def test_detect_source_memakai_jendela(self):
        import powertools.webapps as webapps_module

        original = webapps_module.context_for_owner
        webapps_module.context_for_owner = lambda owner, title: BrowserContext(
            app_name="YouTube Music", window_title="YouTube Music", pwa=True)
        self.addCleanup(
            setattr, webapps_module, "context_for_owner", original)
        self.assertEqual(
            detect_source(self.owner, "ANTIFRAGILE"), "youtube-music")


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
