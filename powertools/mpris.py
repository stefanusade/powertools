"""Pemantau MPRIS ringan untuk jendela lirik.

Tidak ada dependensi tambahan: semua lewat D-Bus sesi standar
``org.mpris.MediaPlayer2``. Pemutar yang sedang aktif dipilih dengan aturan
yang sama seperti dynamic island (yang sedang memutar, lalu yang terakhir
berubah).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from gi.repository import Gio, GLib

from .dbusutil import session_bus
from .sources import source_key

BUS_PREFIX = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
OBJECT_PATH = "/org/mpris/MediaPlayer2"

# playerctld meneruskan pemutar lain; kalau ikut dibaca lagu bisa dobel.
EXCLUDED_PLAYERS = {"org.mpris.MediaPlayer2.playerctld"}


@dataclass(slots=True)
class Track:
    key: str
    title: str
    artist: str
    album: str
    length_us: int
    playing: bool
    owner: str
    track_id: str = ""
    art_url: str = ""
    # Kunci sumber yang sudah dinormalkan, mis. "spotify", "vlc", "brave".
    source: str = ""


def _unpack(value: object, fallback: object = None) -> object:
    if value is None:
        return fallback
    for name in ("recursiveUnpack", "deepUnpack", "unpack"):
        method = getattr(value, name, None)
        if callable(method):
            try:
                return method()
            except Exception:
                pass
    return value


def _as_number(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value) if value == value else 0.0
    if isinstance(value, (tuple, list)) and value:
        return _as_number(value[0])
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _metadata(proxy: Gio.DBusProxy | None) -> dict | None:
    if proxy is None:
        return None
    try:
        data = _unpack(proxy.get_cached_property("Metadata"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _read_metadata(proxy: Gio.DBusProxy | None) -> dict | None:
    data = _metadata(proxy)
    if not data:
        return None

    title = str(data.get("xesam:title") or "").strip()
    artist = data.get("xesam:artist") or data.get("xesam:albumArtist") or ""
    if isinstance(artist, (list, tuple)):
        artist = ", ".join(str(item) for item in artist if item)
    artist = str(artist or "").strip()
    album = str(data.get("xesam:album") or "").strip()
    art_url = str(data.get("mpris:artUrl") or "").strip()
    track_id = str(data.get("mpris:trackid") or "")
    try:
        length_us = max(0, int(_as_number(data.get("mpris:length"))))
    except (TypeError, ValueError):
        length_us = 0

    if not title:
        return None
    return {
        "title": title,
        "artist": artist,
        "album": album,
        "length_us": length_us,
        "track_id": track_id,
        "art_url": art_url,
    }


def _track_key(metadata: dict | None) -> str:
    if not metadata:
        return ""
    track_id = metadata.get("track_id") or ""
    if track_id.endswith("/NoTrack"):
        track_id = ""
    return "|".join(
        (
            str(track_id),
            str(metadata.get("title") or ""),
            str(metadata.get("artist") or ""),
            str(metadata.get("length_us") or 0),
        )
    )


class _Player:
    __slots__ = (
        "owner",
        "proxy",
        "props_id",
        "signal_id",
        "position_us",
        "position_at",
        "position_pending",
        "position_token",
        "last_change",
        "track_key",
        "status",
    )

    def __init__(self, owner: str) -> None:
        self.owner = owner
        self.proxy: Gio.DBusProxy | None = None
        self.props_id = 0
        self.signal_id = 0
        self.position_us = 0
        self.position_at = GLib.get_monotonic_time()
        self.position_pending = False
        self.position_token = 0
        self.last_change = 0.0
        self.track_key = ""
        self.status = "Stopped"

    def cached(self, name: str, fallback: object = None) -> object:
        if self.proxy is None:
            return fallback
        try:
            return _unpack(self.proxy.get_cached_property(name), fallback)
        except Exception:
            return fallback

    def metadata(self) -> dict | None:
        return _read_metadata(self.proxy)

    def playback_status(self) -> str:
        return str(self.cached("PlaybackStatus", "Stopped") or "Stopped")

    def is_playing(self) -> bool:
        return self.playback_status() == "Playing"

    def estimated_position(self) -> float:
        position = max(0.0, float(self.position_us))
        if self.is_playing():
            position += GLib.get_monotonic_time() - self.position_at
        return position


class MprisWatcher:
    """Pantau semua pemutar MPRIS dan laporkan lagu/posisi yang aktif."""

    def __init__(
        self,
        on_track: Callable[[Track | None], None],
        on_position: Callable[[float, bool], None],
    ) -> None:
        self._on_track = on_track
        self._on_position = on_position
        self._bus: Gio.DBusConnection | None = None
        self._players: dict[str, _Player] = {}
        self._active: _Player | None = None
        self._name_id = 0
        self._timer = 0
        self._last_poll = 0.0
        self._started = False
        self._destroyed = False
        self._current_key = ""
        self._current_track: Track | None = None

    @property
    def current_track(self) -> Track | None:
        return self._current_track

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._started or self._destroyed:
            return
        self._started = True
        self._bus = session_bus()
        if self._bus is None:
            return

        try:
            self._name_id = self._bus.signal_subscribe(
                "org.freedesktop.DBus",
                "org.freedesktop.DBus",
                "NameOwnerChanged",
                "/org/freedesktop/DBus",
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_name_owner_changed,
            )
        except Exception:
            self._name_id = 0

        self._list_names()
        self._timer = GLib.timeout_add(250, self._tick)

    def stop(self) -> None:
        self._destroyed = True
        self._started = False

        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        if self._name_id and self._bus is not None:
            try:
                self._bus.signal_unsubscribe(self._name_id)
            except Exception:
                pass
            self._name_id = 0

        for player in self._players.values():
            self._disconnect(player)
        self._players.clear()
        self._active = None
        self._current_key = ""
        self._current_track = None
        self._bus = None

    def _disconnect(self, player: _Player) -> None:
        if player.proxy is None:
            return
        for signal_id in (player.props_id, player.signal_id):
            if signal_id:
                try:
                    player.proxy.disconnect(signal_id)
                except Exception:
                    pass
        player.props_id = 0
        player.signal_id = 0

    # ---------------------------------------------------------------- players
    def _list_names(self) -> None:
        if self._bus is None:
            return
        try:
            self._bus.call(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "ListNames",
                None,
                None,
                Gio.DBusCallFlags.NONE,
                1500,
                None,
                self._on_names,
            )
        except Exception:
            pass

    def _on_names(self, bus: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
        if self._destroyed:
            return
        try:
            value = _unpack(bus.call_finish(result))
            names = value[0] if isinstance(value, (tuple, list)) and value else []
        except Exception:
            return
        for name in names or []:
            if not isinstance(name, str) or not name.startswith(f"{BUS_PREFIX}."):
                continue
            if name in EXCLUDED_PLAYERS or "playerctld" in name:
                continue
            self._add_player(name)

    def _on_name_owner_changed(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _path: str,
        _interface: str,
        _signal: str,
        params: GLib.Variant,
    ) -> None:
        if self._destroyed:
            return
        value = _unpack(params, ("", "", ""))
        if not isinstance(value, (tuple, list)) or len(value) < 3:
            return
        name, _old_owner, new_owner = value[0], value[1], value[2]
        if not isinstance(name, str) or not name.startswith(f"{BUS_PREFIX}."):
            return
        if name in EXCLUDED_PLAYERS or "playerctld" in name:
            return
        if new_owner:
            self._add_player(name)
        else:
            self._remove_player(name)

    def _add_player(self, owner: str) -> None:
        if self._destroyed or self._bus is None or owner in self._players:
            return
        if owner in EXCLUDED_PLAYERS or "playerctld" in owner:
            return

        player = _Player(owner)
        self._players[owner] = player
        try:
            Gio.DBusProxy.new(
                self._bus,
                Gio.DBusProxyFlags.NONE,
                None,
                owner,
                OBJECT_PATH,
                PLAYER_IFACE,
                None,
                self._on_proxy_ready,
                owner,
            )
        except Exception:
            self._players.pop(owner, None)

    def _on_proxy_ready(
        self,
        _source: Gio.DBusProxy,
        result: Gio.AsyncResult,
        owner: str,
    ) -> None:
        if self._destroyed or owner not in self._players:
            return
        player = self._players[owner]
        try:
            proxy = Gio.DBusProxy.new_finish(result)
        except Exception:
            self._players.pop(owner, None)
            return

        player.proxy = proxy
        player.status = player.playback_status()
        player.track_key = _track_key(player.metadata())
        player.last_change = time.monotonic()

        try:
            player.props_id = proxy.connect(
                "g-properties-changed", self._on_properties_changed, player
            )
            player.signal_id = proxy.connect(
                "g-signal", self._on_player_signal, player
            )
        except Exception:
            pass

        self._refresh(force=True)

    def _remove_player(self, owner: str) -> None:
        player = self._players.pop(owner, None)
        if player is None:
            return
        self._disconnect(player)
        if self._active is player:
            self._active = None
        self._refresh(force=True)

    # ---------------------------------------------------------------- refresh
    def _visible_players(self) -> list[_Player]:
        return [
            player
            for player in self._players.values()
            if player.proxy is not None and player.metadata() is not None
        ]

    def _choose_active(self, players: list[_Player]) -> _Player | None:
        if not players:
            return None

        current = self._active
        any_playing = any(player.is_playing() for player in players)
        if current in players and (current.is_playing() or not any_playing):
            return current

        pool = (
            [player for player in players if player.is_playing()]
            if any_playing
            else players
        )
        return max(pool, key=lambda player: player.last_change) if pool else None

    def _refresh(self, force: bool = False) -> None:
        if self._destroyed:
            return
        players = self._visible_players()
        active = self._choose_active(players)
        self._active = active

        if active is None:
            if force or self._current_track is not None:
                self._current_key = ""
                self._current_track = None
                self._on_track(None)
            return

        metadata = active.metadata()
        key = _track_key(metadata)
        playing = active.is_playing()
        identity = f"{key}|{int(playing)}"
        if force or identity != self._current_key:
            self._current_key = identity
            if metadata is not None:
                self._current_track = Track(
                    key=key,
                    title=str(metadata.get("title") or ""),
                    artist=str(metadata.get("artist") or ""),
                    album=str(metadata.get("album") or ""),
                    length_us=int(metadata.get("length_us") or 0),
                    playing=playing,
                    owner=active.owner,
                    track_id=str(metadata.get("track_id") or ""),
                    art_url=str(metadata.get("art_url") or ""),
                    source=source_key(
                        active.owner,
                        str(metadata.get("title") or ""),
                        str(metadata.get("album") or ""),
                    ),
                )
                self._on_track(self._current_track)
            else:
                self._current_track = None
                self._on_track(None)

    # ------------------------------------------------------------- properties
    def _on_properties_changed(
        self,
        _proxy: Gio.DBusProxy,
        changed: GLib.Variant,
        invalidated: list[str],
        player: _Player,
    ) -> None:
        if self._destroyed:
            return

        keys: set[str] = set()
        props = _unpack(changed, {})
        if isinstance(props, dict):
            keys.update(str(key) for key in props.keys())
        if invalidated:
            keys.update(str(key) for key in invalidated)

        if "Metadata" in keys:
            metadata = player.metadata()
            key = _track_key(metadata)
            if key != player.track_key:
                player.track_key = key
                player.last_change = time.monotonic()
                player.position_us = 0
                player.position_at = GLib.get_monotonic_time()
                player.position_token += 1
            self._refresh(force=True)

        if "PlaybackStatus" in keys:
            status = player.playback_status()
            was_playing = player.status == "Playing"
            now = GLib.get_monotonic_time()
            if was_playing and status != "Playing":
                player.position_us = max(
                    0.0, player.position_us + (now - player.position_at)
                )
            player.position_at = now
            player.position_token += 1
            player.status = status
            if status == "Playing" and not was_playing:
                player.last_change = time.monotonic()
            self._refresh(force=True)

        if {"CanSeek", "CanGoNext", "CanGoPrevious"} & keys:
            self._refresh()

    def _on_player_signal(
        self,
        _proxy: Gio.DBusProxy,
        _sender: str,
        signal: str,
        params: GLib.Variant,
        player: _Player,
    ) -> None:
        if self._destroyed or signal != "Seeked":
            return
        value = _unpack(params, (0,))
        position = _as_number(value)
        player.position_us = max(0.0, position)
        player.position_at = GLib.get_monotonic_time()
        player.position_token += 1

    # --------------------------------------------------------------- position
    def _tick(self) -> bool:
        self._timer = 0
        if self._destroyed or not self._started:
            return GLib.SOURCE_REMOVE

        active = self._active
        if active is not None and active.proxy is not None:
            if active.is_playing():
                self._poll_position(active)
            self._on_position(active.estimated_position() / 1_000_000.0,
                              active.is_playing())

        self._timer = GLib.timeout_add(250, self._tick)
        return GLib.SOURCE_REMOVE

    def _poll_position(self, player: _Player) -> None:
        if player.position_pending or player.proxy is None:
            return
        now = time.monotonic()
        if now - self._last_poll < 1.0:
            return
        self._last_poll = now
        player.position_pending = True
        token = player.position_token
        try:
            player.proxy.call(
                "org.freedesktop.DBus.Properties.Get",
                GLib.Variant("(ss)", (PLAYER_IFACE, "Position")),
                Gio.DBusCallFlags.NONE,
                800,
                None,
                self._on_position_reply,
                (player, token),
            )
        except Exception:
            player.position_pending = False

    def _on_position_reply(
        self,
        proxy: Gio.DBusProxy,
        result: Gio.AsyncResult,
        data: tuple[_Player, int],
    ) -> None:
        player, token = data
        player.position_pending = False
        if self._destroyed:
            return
        try:
            value = _unpack(proxy.call_finish(result))
            position = _as_number(value)
        except Exception:
            return
        if player.position_token == token:
            player.position_us = max(0.0, position)
            player.position_at = GLib.get_monotonic_time()
