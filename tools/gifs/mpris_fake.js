// Pemutar MPRIS palsu untuk merekam dynamic island.
//
//     gjs -m mpris_fake.js --bus spotify --titles "A|B" --interval 7 \
//         --duration 14 [--cover /tmp/cover.png]
//
// Satu proses = satu pemutar. Jalankan dua proses dengan --bus berbeda untuk
// memunculkan chip pemilih pemutar di island. Semua judul dan sampulnya tiruan,
// jadi rekaman tidak bergantung pada isi sesi pengguna.

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';

const PATH = '/org/mpris/MediaPlayer2';

const ROOT_XML = `
<node>
  <interface name="org.mpris.MediaPlayer2">
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
</node>`;

const PLAYER_XML = `
<node>
  <interface name="org.mpris.MediaPlayer2.Player">
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="LoopStatus" type="s" access="readwrite"/>
    <property name="Rate" type="d" access="readwrite"/>
    <property name="Shuffle" type="b" access="readwrite"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
    <method name="Play"/>
    <method name="Pause"/>
    <method name="PlayPause"/>
    <method name="Stop"/>
    <method name="Next"/>
    <method name="Previous"/>
    <method name="Seek">
      <arg name="Offset" type="x" direction="in"/>
    </method>
    <method name="SetPosition">
      <arg name="TrackId" type="o" direction="in"/>
      <arg name="Position" type="x" direction="in"/>
    </method>
    <method name="OpenUri">
      <arg name="Uri" type="s" direction="in"/>
    </method>
  </interface>
</node>`;

// ---------------------------------------------------------------- argumen
const options = {
    bus: 'spotify',
    titles: 'Lampu Kota|Peron Kosong',
    artist: 'Senja Kecil',
    album: 'Peron',
    interval: 7,
    duration: 14,
    cover: '',
};

for (let i = 0; i < ARGV.length; i += 2) {
    const key = ARGV[i].replace(/^--/, '');
    if (key in options)
        options[key] = ARGV[i + 1];
}

const titles = String(options.titles).split('|');
const interval = Number(options.interval);
const duration = Number(options.duration);
const cover = String(options.cover);
const busName = `org.mpris.MediaPlayer2.${options.bus}`;

let index = 0;
let startedAt = GLib.get_monotonic_time();
let playing = true;
let pausedAt = 0;
let player = null;

function lengthUs() {
    return 200 * 1e6;
}

function positionUs() {
    const now = GLib.get_monotonic_time();
    const elapsed = (playing ? now : pausedAt) - startedAt;
    return Math.max(0, Math.min(lengthUs(), elapsed));
}

function metadata() {
    const data = {
        'mpris:trackid': new GLib.Variant('o',
            `/org/mpris/MediaPlayer2/TrackList/${index + 1}`),
        'mpris:length': new GLib.Variant('x', Math.round(lengthUs())),
        'xesam:title': new GLib.Variant('s', titles[index % titles.length]),
        'xesam:artist': new GLib.Variant('as', [String(options.artist)]),
        'xesam:album': new GLib.Variant('s', String(options.album)),
    };
    if (cover)
        data['mpris:artUrl'] = new GLib.Variant('s', `file://${cover}`);
    return data;
}

const rootIface = {
    Identity: options.bus === 'spotify' ? 'Spotify' : 'VLC',
    DesktopEntry: options.bus === 'spotify' ? 'spotify' : 'vlc',
    CanQuit: false,
    CanRaise: false,
    HasTrackList: false,
    SupportedUriSchemes: [''],
    SupportedMimeTypes: [''],
};

const playerIface = {
    PlaybackStatus: 'Playing',
    LoopStatus: 'None',
    Rate: 1.0,
    Shuffle: false,
    Metadata: metadata(),
    Volume: 0.8,
    Position: 0,
    MinimumRate: 1.0,
    MaximumRate: 1.0,
    CanGoNext: true,
    CanGoPrevious: true,
    CanPlay: true,
    CanPause: true,
    CanSeek: true,
    CanControl: true,
    Play() {
        if (playing)
            return;
        playing = true;
        startedAt = GLib.get_monotonic_time() - positionUs();
        playerIface.PlaybackStatus = 'Playing';
        player.emit_property_changed('PlaybackStatus',
            new GLib.Variant('s', 'Playing'));
    },
    Pause() {
        if (!playing)
            return;
        pausedAt = GLib.get_monotonic_time();
        playing = false;
        playerIface.PlaybackStatus = 'Paused';
        player.emit_property_changed('PlaybackStatus',
            new GLib.Variant('s', 'Paused'));
    },
    PlayPause() {
        playing ? playerIface.Pause() : playerIface.Play();
    },
    Stop() {
        playing = false;
        playerIface.PlaybackStatus = 'Stopped';
        player.emit_property_changed('PlaybackStatus',
            new GLib.Variant('s', 'Stopped'));
    },
    Next() { nextTrack(); },
    Previous() { nextTrack(); },
    Seek(offset) { startedAt -= Number(offset); },
    SetPosition(_id, pos) {
        startedAt = GLib.get_monotonic_time() - Number(pos);
    },
    OpenUri() {},
};

function nextTrack() {
    index = (index + 1) % titles.length;
    startedAt = GLib.get_monotonic_time();
    playerIface.Metadata = metadata();
    player.emit_property_changed('Metadata',
        new GLib.Variant('a{sv}', playerIface.Metadata));
}

const loop = new GLib.MainLoop(null, false);
const connection = Gio.bus_get_sync(Gio.BusType.SESSION, null);

// Satu objek hanya membawa satu antarmuka, jadi root dan Player diekspor
// terpisah pada path yang sama.
const root = Gio.DBusExportedObject.wrapJSObject(ROOT_XML, rootIface);
player = Gio.DBusExportedObject.wrapJSObject(PLAYER_XML, playerIface);

Gio.bus_own_name_on_connection(
    connection, busName, Gio.BusNameOwnerFlags.NONE, null, null);
root.export(connection, PATH);
player.export(connection, PATH);

// Position harus terlihat berjalan, jadi diumumkan setengah detik sekali.
GLib.timeout_add(GLib.PRIORITY_DEFAULT, 500, () => {
    playerIface.Position = positionUs();
    player.emit_property_changed('Position',
        new GLib.Variant('x', playerIface.Position));
    return GLib.SOURCE_CONTINUE;
});

GLib.timeout_add(GLib.PRIORITY_DEFAULT, Math.round(interval * 1000), () => {
    nextTrack();
    return GLib.SOURCE_CONTINUE;
});

GLib.timeout_add(GLib.PRIORITY_DEFAULT, Math.round(duration * 1000), () => {
    loop.quit();
    return GLib.SOURCE_REMOVE;
});

print(`mpris palsu: ${busName}, ${duration}s`);
loop.run();
