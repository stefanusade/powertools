"""Uji logika tab dynamic island (JavaScript) dari suite Python.

Logika penyusunan tab tinggal di ekstensi GNOME (`tabs.js`) karena hanya shell
yang bisa melihat daftar jendela. Modul itu sengaja tidak mengimpor ``gi://``
supaya bisa dijalankan langsung dengan ``gjs -m`` di luar shell.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "js" / "tabs_test.js"


class TabsJsTests(unittest.TestCase):
    def test_logika_tab(self):
        gjs = shutil.which("gjs")
        if gjs is None:
            self.skipTest("gjs tidak terpasang")
        result = subprocess.run(
            [gjs, "-m", str(SCRIPT)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(
            result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
