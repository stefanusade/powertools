"""Uji lompat waktu (seek) ke pemutar MPRIS.

Pemutar sungguhan tidak tersedia saat uji, jadi proxy D-Bus-nya diganti objek
palsu yang mencatat panggilan. Yang diperiksa: pemilihan ``SetPosition`` vs
``Seek``, penghormatan ``CanSeek``, penjepitan ke panjang lagu, dan posisi
perkiraan yang langsung ikut bergeser.
"""

from __future__ import annotations

import unittest

try:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import GLib

    from powertools import mpris

    GI_READY = True
except Exception:  # pragma: no cover - lingkungan tanpa PyGObject
    GI_READY = False


class FakeProxy:
    """Tiruan Gio.DBusProxy: properti cache + metode pemutar."""

    def __init__(self, metadata, can_seek=True, fail=()):
        self.metadata = metadata
        self.can_seek = can_seek
        self.fail = set(fail)
        self.calls: list[tuple[str, str, tuple]] = []

    def get_cached_property(self, name):
        if name == "Metadata":
            return GLib.Variant(
                "a{sv}",
                {key: GLib.Variant(*spec) for key, spec in self.metadata.items()},
            )
        if name == "CanSeek":
            return GLib.Variant("b", self.can_seek)
        return None

    def call_sync(self, method, params, *_args):
        # Gio.DBusProxy sudah terikat ke satu interface: (metode, parameter).
        self.calls.append((mpris.PLAYER_IFACE, method, params.unpack()))
        if method in self.fail:
            raise GLib.Error("pemutar menolak")
        return None


def _metadata(track_id="/org/spotify/track/1", length_us=200_000_000):
    return {
        "xesam:title": ("s", "Lagu Uji"),
        "xesam:artist": ("as", ["Artis"]),
        "mpris:length": ("x", length_us),
        "mpris:trackid": ("o", track_id),
    }


@unittest.skipUnless(GI_READY, "PyGObject tidak tersedia")
class SeekTests(unittest.TestCase):
    def _watcher(self, metadata=None, can_seek=True, fail=()):
        watcher = mpris.MprisWatcher(lambda _track: None, lambda _s, _p: None)
        player = mpris._Player("org.mpris.MediaPlayer2.uji")
        player.proxy = FakeProxy(metadata or _metadata(), can_seek, fail)
        watcher._active = player
        return watcher, player

    def test_set_position_dipakai_kalau_track_id_ada(self):
        watcher, player = self._watcher()
        self.assertTrue(watcher.seek(42.5))

        iface, method, args = player.proxy.calls[0]
        self.assertEqual(iface, mpris.PLAYER_IFACE)
        self.assertEqual(method, "SetPosition")
        self.assertEqual(args, ("/org/spotify/track/1", 42_500_000))
        self.assertEqual(player.position_us, 42_500_000)

    def test_tanpa_track_id_memakai_seek_relatif(self):
        watcher, player = self._watcher(_metadata(track_id="/org/mpris/MediaPlayer2/Track/NoTrack"))
        player.position_us = 10_000_000
        player.position_at = GLib.get_monotonic_time()
        self.assertTrue(watcher.seek(30))

        _iface, method, args = player.proxy.calls[0]
        self.assertEqual(method, "Seek")
        self.assertEqual(args, (20_000_000,))
        self.assertEqual(player.position_us, 30_000_000)

    def test_set_position_gagal_jatuh_ke_seek(self):
        watcher, player = self._watcher(fail={"SetPosition"})
        self.assertTrue(watcher.seek(15))
        self.assertEqual(
            [call[1] for call in player.proxy.calls], ["SetPosition", "Seek"]
        )

    def test_can_seek_false_tidak_memanggil_apa_pun(self):
        watcher, player = self._watcher(can_seek=False)
        self.assertFalse(watcher.seek(15))
        self.assertEqual(player.proxy.calls, [])

    def test_target_dijepit_ke_panjang_lagu(self):
        watcher, player = self._watcher(_metadata(length_us=100_000_000))
        self.assertTrue(watcher.seek(500))
        self.assertEqual(player.proxy.calls[0][2][1], 99_000_000)

    def test_posisi_negatif_menjadi_nol(self):
        watcher, player = self._watcher()
        self.assertTrue(watcher.seek(-5))
        self.assertEqual(player.proxy.calls[0][2][1], 0)

    def test_tanpa_pemutar_aktif(self):
        watcher = mpris.MprisWatcher(lambda _track: None, lambda _s, _p: None)
        self.assertFalse(watcher.seek(10))

    def test_poll_lama_tidak_menimpa_posisi_baru(self):
        watcher, player = self._watcher()
        before = player.position_token
        self.assertTrue(watcher.seek(60))
        self.assertGreater(player.position_token, before)


if __name__ == "__main__":
    unittest.main()
