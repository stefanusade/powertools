"""Tes deteksi PWA dari proses pemutar MPRIS (tanpa D-Bus)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powertools import webapps
from powertools.webapps import (
    app_id_from_cmdline,
    app_name_for_owner,
    clear_cache,
    desktop_name_for_app_id,
    pid_from_owner,
    read_cmdline,
)


class PidTests(unittest.TestCase):
    def test_pid_dari_nama_bus(self):
        self.assertEqual(
            pid_from_owner("org.mpris.MediaPlayer2.brave.instance10804"),
            10804,
        )
        self.assertEqual(
            pid_from_owner("org.mpris.MediaPlayer2.chrome.instance42"),
            42,
        )

    def test_nama_tanpa_instance(self):
        for owner in ("org.mpris.MediaPlayer2.spotify", "", None):
            self.assertEqual(pid_from_owner(owner), 0)


class CmdlineTests(unittest.TestCase):
    def test_ambil_app_id(self):
        line = (
            "/opt/brave.com/brave/brave --profile-directory=Default "
            "--app-id=cinhimbnkkaeohfgghhklpknlkffjgod"
        )
        self.assertEqual(
            app_id_from_cmdline(line), "cinhimbnkkaeohfgghhklpknlkffjgod")

    def test_browser_biasa_tanpa_app_id(self):
        self.assertEqual(
            app_id_from_cmdline("/usr/bin/brave --profile-directory=Default"),
            "",
        )
        self.assertEqual(app_id_from_cmdline(""), "")

    def test_ambil_app_id_firefox_pwa(self):
        line = (
            "/usr/lib/firefox/firefox -class FFPWA-01JCZ9W0K3 -name "
            "FFPWA-01JCZ9W0K3"
        )
        self.assertEqual(
            app_id_from_cmdline(line), "FFPWA-01JCZ9W0K3")

    def test_cmdline_dibaca_dari_proc_tiruan(self):
        root = Path(tempfile.mkdtemp(prefix="powertools-proc-"))
        pid = 4321
        (root / str(pid)).mkdir()
        (root / str(pid) / "cmdline").write_bytes(
            b"/usr/bin/brave\0--app-id=abc123\0"
        )
        text = read_cmdline(pid, root)
        self.assertIn("--app-id=abc123", text)
        self.assertEqual(app_id_from_cmdline(text), "abc123")

    def test_proses_hilang(self):
        root = Path(tempfile.mkdtemp(prefix="powertools-proc-"))
        self.assertEqual(read_cmdline(9999, root), "")
        self.assertEqual(read_cmdline(0, root), "")


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
        self.assertEqual(
            desktop_name_for_app_id("xyz789", [root]), "Netflix")

    def test_app_id_tidak_dikenal(self):
        root = self._dir(
            "brave-abc123-Default.desktop",
            "[Desktop Entry]\nName=YouTube Music\n",
        )
        self.assertEqual(desktop_name_for_app_id("lain", [root]), "")
        self.assertEqual(desktop_name_for_app_id("", [root]), "")


class OwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()
        self.addCleanup(clear_cache)
        self.proc = Path(tempfile.mkdtemp(prefix="powertools-proc-"))
        self.apps = Path(tempfile.mkdtemp(prefix="powertools-apps-"))
        (self.apps / "brave-abc123-Default.desktop").write_text(
            "[Desktop Entry]\nName=YouTube Music\n", encoding="utf-8")

    def _owner(self, pid: int, cmdline: str) -> str:
        (self.proc / str(pid)).mkdir()
        (self.proc / str(pid) / "cmdline").write_bytes(cmdline.encode())
        return f"org.mpris.MediaPlayer2.brave.instance{pid}"

    def test_pwa_dikenali(self):
        owner = self._owner(101, "/usr/bin/brave\0--app-id=abc123\0")
        self.assertEqual(
            app_name_for_owner(owner, proc=self.proc, dirs=[self.apps]),
            "YouTube Music",
        )

    def test_browser_biasa_tidak_punya_nama_aplikasi(self):
        owner = self._owner(102, "/usr/bin/brave\0")
        self.assertEqual(
            app_name_for_owner(owner, proc=self.proc, dirs=[self.apps]), "")

    def test_hasil_di_cache(self):
        owner = self._owner(103, "/usr/bin/brave\0--app-id=abc123\0")
        self.assertEqual(
            app_name_for_owner(owner, proc=self.proc, dirs=[self.apps]),
            "YouTube Music",
        )
        # Berkas .desktop dihapus: hasil lama tetap dipakai selama PWA sama.
        for path in self.apps.glob("*.desktop"):
            path.unlink()
        self.assertEqual(
            app_name_for_owner(owner, proc=self.proc, dirs=[self.apps]),
            "YouTube Music",
        )
        self.assertEqual(webapps._CACHE["abc123"], "YouTube Music")

    def test_hasil_kosong_tidak_di_cache(self):
        # PWA bisa dipasang setelah aplikasi berjalan; nama harus dicari ulang.
        owner = self._owner(104, "/usr/bin/brave\0--app-id=baru\0")
        self.assertEqual(
            app_name_for_owner(owner, proc=self.proc, dirs=[self.apps]), "")
        self.assertNotIn("baru", webapps._CACHE)


if __name__ == "__main__":
    unittest.main()
