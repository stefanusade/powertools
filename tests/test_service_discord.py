"""Uji keputusan service yang tidak butuh jendela GTK.

Yang dijaga di sini adalah regresi yang membuat Discord berhenti mengirim
activity sama sekali: pemantau MPRIS tidak pernah dinyalakan kalau service
mengira tidak ada Application ID yang bisa dipakai.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from powertools.service import PowerToolsApp

    SERVICE_READY = True
except Exception:  # pragma: no cover - lingkungan tanpa GTK
    SERVICE_READY = False

class _Cfg:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def module_enabled(self, name: str) -> bool:
        return self.enabled and name == "discord"

class _FakeApp:
    """Bagian PowerToolsApp yang dipakai ``_discord_active`` saja."""

    def __init__(self, enabled: bool, mapping=None, app_id: str = "") -> None:
        self.cfg = _Cfg(enabled)
        self._mapping = dict(mapping or {})
        self._app_id = app_id

    def _discord_mapping(self):
        return self._mapping

    def _discord_app_id_for(self, _track):
        return self._app_id

    _discord_active = PowerToolsApp._discord_active

@unittest.skipUnless(SERVICE_READY, "GTK 4 tidak tersedia")
class DiscordActiveTests(unittest.TestCase):
    def test_modul_mati(self):
        self.assertFalse(_FakeApp(False)._discord_active())

    def test_preset_tetap_menyalakan_pemantau(self):
        # use_builtin_ids=false + app_ids kosong + app_id kosong: dulu
        # _discord_active() False, sehingga MPRIS tidak pernah dipantau dan
        # tidak ada activity yang terkirim sama sekali.
        self.assertTrue(_FakeApp(True)._discord_active())

    def test_pemetaan_pengguna_juga_menyalakan(self):
        self.assertTrue(
            _FakeApp(True, mapping={"spotify": "111111111111111111"})
            ._discord_active()
        )

class _SyncCfg:
    def get(self, key, default=None):
        data = {
            "discord.show_paused": False,
            "discord.show_album_art": True,
            "discord.fallback_asset": "",
        }
        return data.get(key, default)

class _Recorder:
    def __init__(self, app_id: str) -> None:
        self.app_id = app_id
        self.updates = []
        self.cleared = 0

    def set_app_id(self, app_id: str) -> None:
        self.app_id = app_id

    def update(self, activity) -> None:
        self.updates.append(activity)

    def clear(self) -> None:
        self.cleared += 1

class _FakeSync:
    """Bagian PowerToolsApp yang dipakai ``_discord_sync`` saja."""

    cfg = _SyncCfg()
    _discord = None
    _discord_track = None
    _discord_playing = False
    _discord_identity = None
    _discord_sent_at = 0.0
    _discord_sent_position = 0.0
    _discord_position = 0.0

    def __init__(self, app_id: str = "") -> None:
        self.app_id = app_id
        self._discord = _Recorder(app_id)

    def _discord_app_id_for(self, track):
        return self.app_id

    _discord_sync = PowerToolsApp._discord_sync

def _track(source: str, title: str = "Judul lagu", playing: bool = True):
    return SimpleNamespace(
        key=f"{source}:1",
        title=title,
        artist="Artis",
        album="",
        length_us=240_000_000,
        art_url="",
        source=source,
        playing=playing,
    )

@unittest.skipUnless(SERVICE_READY, "GTK 4 tidak tersedia")
class DiscordSyncTests(unittest.TestCase):
    """Activity yang benar-benar dikirim ke DiscordPresence."""

    def test_youtube_dikirim_sebagai_watching(self):
        app = _FakeSync("1574695288088559706")
        app._discord_track = _track("youtube", "Video panjang")
        app._discord_playing = True
        app._discord_sync(force=True)

        self.assertEqual(len(app._discord.updates), 1)
        activity = app._discord.updates[0]
        self.assertEqual(activity["type"], 3)
        self.assertEqual(activity["details"], "Video panjang")

    def test_spotify_dikirim_sebagai_listening(self):
        app = _FakeSync("1574690633262567494")
        app._discord_track = _track("spotify")
        app._discord_playing = True
        app._discord_sync(force=True)

        self.assertEqual(app._discord.updates[0]["type"], 2)

    def test_sumber_tanpa_id_tidak_dikirim(self):
        app = _FakeSync("")
        app._discord_track = _track("pemutar-langka")
        app._discord_playing = True
        app._discord_sync(force=True)

        self.assertEqual(app._discord.updates, [])

    def test_dijeda_tanpa_show_paused_tidak_dikirim(self):
        app = _FakeSync("1574690633262567494")
        app._discord_track = _track("spotify", playing=False)
        app._discord_playing = False
        app._discord_sync(force=True)

        self.assertEqual(app._discord.updates, [])


if __name__ == "__main__":
    unittest.main()
