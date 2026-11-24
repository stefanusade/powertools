// SPDX-License-Identifier: MIT
//
// "Dynamic island" untuk semua pemutar MPRIS yang sedang aktif.
//
// Kenapa di ekstensi GNOME, bukan di aplikasi GTK?
//  - Di Wayland aplikasi biasa tidak bisa menaruh jendela di atas semua
//    jendela (butuh gtk4-layer-shell yang tidak selalu tersedia), dan
//  - mendeteksi pointer berhenti di tepi atas layar jauh lebih andal dari
//    dalam shell, termasuk saat ada jendela fullscreen.
//
// Semua data media diambil dari D-Bus standar org.mpris.MediaPlayer2, jadi
// Spotify, VLC, mpv, Firefox, Chromium/YouTube, dan YouTube Music langsung
// ikut tanpa integrasi khusus.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import Pango from 'gi://Pango';
import Soup from 'gi://Soup?version=3.0';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {buildTabs, pidFromBusName, resolveTab} from './tabs.js';

const BUS_PREFIX = 'org.mpris.MediaPlayer2';
const PLAYER_IFACE = 'org.mpris.MediaPlayer2.Player';
const ROOT_IFACE = 'org.mpris.MediaPlayer2';
const OBJECT_PATH = '/org/mpris/MediaPlayer2';

// playerctld adalah proksi yang meneruskan pemutar lain; kalau ikut dibaca
// setiap lagu akan muncul dua kali.
const EXCLUDED_PLAYERS = new Set(['org.mpris.MediaPlayer2.playerctld']);

const TICK_MS = 150;
const POSITION_POLL_MS = 500;
//: Seberapa sering daftar jendela dibaca ulang saat island tampil. Jendela
//: tidak punya sinyal seperti MPRIS, jadi aplikasi web yang baru dibuka hanya
//: ketahuan lewat pemeriksaan berkala.
const WINDOW_SCAN_MS = 2000;
const HIDE_GRACE_MS = 700;
const MAX_ART_BYTES = 6 * 1024 * 1024;
const ART_MAX_AGE_DAYS = 30;

const ISLAND_WIDTH = 384;
const ISLAND_MIN_WIDTH = 260;
const ISLAND_MARGIN_TOP = 0;
const HOT_HALF_WIDTH = 170;
const HOT_MIN_HEIGHT = 6;
// Kalau panel GNOME tidak di atas (mis. Zorin memindahkannya ke bawah),
// island menempel langsung ke tepi atas layar. Zona hover diambil dari
// tinggi panel; kalau panel di bawah, pakai pita tetap di tepi atas.
const HOT_TOP_STRIP = 36;
const HOVER_POLL_MS = 100;
const HOVER_MOVE_TOLERANCE = 6;

const DEBUG = GLib.getenv('POWERTOOLS_MEDIA_DEBUG') === '1';

function debug(...args) {
    if (DEBUG)
        console.log('[powertools-media]', ...args);
}

function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
}

function nowMs() {
    return GLib.get_monotonic_time() / 1000;
}

function unpack(variant, fallback = null) {
    if (variant === null || variant === undefined)
        return fallback;
    try {
        if (typeof variant.recursiveUnpack === 'function')
            return variant.recursiveUnpack();
        if (typeof variant.deepUnpack === 'function')
            return variant.deepUnpack();
        return variant;
    } catch (e) {
        return fallback;
    }
}

function asNumber(value) {
    if (typeof value === 'number')
        return Number.isFinite(value) ? value : 0;
    if (typeof value === 'bigint')
        return Number(value);
    if (value && typeof value.deepUnpack === 'function')
        return asNumber(unpack(value, 0));
    const number = Number(value);
    return Number.isFinite(number) ? number : 0;
}

function formatTime(microseconds) {
    const total = Math.max(0, Math.floor(asNumber(microseconds) / 1_000_000));
    const seconds = total % 60;
    const minutes = Math.floor(total / 60) % 60;
    const hours = Math.floor(total / 3600);
    if (hours > 0) {
        return `${hours}:${String(minutes).padStart(2, '0')}:` +
            String(seconds).padStart(2, '0');
    }
    return `${Math.floor(total / 60)}:${String(seconds).padStart(2, '0')}`;
}

function prettyBusName(busName) {
    let name = busName.startsWith(`${BUS_PREFIX}.`)
        ? busName.slice(BUS_PREFIX.length + 1)
        : busName;
    name = name.replace(/\.instance\d+$/i, '');
    name = name.replace(/[._-]+/g, ' ').trim();
    if (!name)
        return 'Media';
    return name.charAt(0).toUpperCase() + name.slice(1);
}

function readMetadata(proxy) {
    if (!proxy)
        return null;

    let data = null;
    try {
        data = unpack(proxy.get_cached_property('Metadata'), null);
    } catch (e) {
        data = null;
    }
    if (!data || typeof data !== 'object')
        return null;

    const title = String(data['xesam:title'] ?? '').trim();
    let artist = data['xesam:artist'] ?? data['xesam:albumArtist'] ?? '';
    if (Array.isArray(artist))
        artist = artist.filter(Boolean).join(', ');
    artist = String(artist ?? '').trim();

    return {
        title,
        artist,
        album: String(data['xesam:album'] ?? '').trim(),
        artUrl: String(data['mpris:artUrl'] ?? '').trim(),
        length: Math.max(0, asNumber(data['mpris:length'])),
        trackId: String(data['mpris:trackid'] ?? ''),
    };
}

function trackKey(metadata) {
    if (!metadata)
        return '';
    const id = metadata.trackId &&
        metadata.trackId !== '/org/mpris/MediaPlayer2/TrackList/NoTrack'
        ? metadata.trackId
        : '';
    return [id, metadata.title, metadata.artist, metadata.length].join('|');
}

function makeBox(vertical, styleClass = null, params = {}) {
    const layout = new Clutter.BoxLayout({
        orientation: vertical
            ? Clutter.Orientation.VERTICAL
            : Clutter.Orientation.HORIZONTAL,
    });
    return new St.Widget({
        layout_manager: layout,
        style_class: styleClass,
        ...params,
    });
}

class Player {
    constructor(owner) {
        this.owner = owner;
        this.proxy = null;
        this.root = null;
        this.identity = '';
        this.desktopEntry = '';
        this.initialized = false;
        this.propsChangedId = 0;
        this.signalId = 0;
        this.lastStatus = 'Stopped';
        this.lastChangeAt = 0;
        this.trackKey = '';
        this.positionUs = 0;
        this.positionAt = GLib.get_monotonic_time();
        this.positionPending = false;
        this.positionToken = 0;
    }

    cached(name, fallback) {
        try {
            return unpack(this.proxy?.get_cached_property(name), fallback);
        } catch (e) {
            return fallback;
        }
    }

    metadata() {
        return readMetadata(this.proxy);
    }

    playbackStatus() {
        return this.cached('PlaybackStatus', 'Stopped');
    }

    isPlaying() {
        return this.playbackStatus() === 'Playing';
    }

    canSeek() {
        return this.cached('CanSeek', false) === true;
    }

    canGoNext() {
        return this.cached('CanGoNext', false) === true;
    }

    canGoPrevious() {
        return this.cached('CanGoPrevious', false) === true;
    }

    displayName() {
        const identity = this.identity.trim();
        return identity || prettyBusName(this.owner);
    }

    estimatedPosition() {
        let position = Math.max(0, asNumber(this.positionUs));
        if (this.isPlaying())
            position += GLib.get_monotonic_time() - this.positionAt;
        return position;
    }
}

export class MediaIsland {
    constructor(settings, dir) {
        this._settings = settings;
        this._dir = dir;
        this._destroyed = false;
        this._players = new Map();
        this._active = null;

        this._shown = false;
        this._autoHideAt = 0;
        this._leftAt = 0;
        this._lastPositionPoll = 0;

        this._hoverWatch = 0;
        this._hoverStart = 0;
        this._hoverX = -1;
        this._hoverY = -1;
        this._uiTimer = 0;
        this._pruneId = 0;
        this._geoKey = '';

        this._dragging = false;
        this._seekHover = false;
        this._seekFraction = 0;

        this._artUrl = '';
        this._artToken = 0;
        this._soup = null;

        //: Tab yang tampil sekarang (pemutar MPRIS + jendela browser).
        this._tabs = [];
        this._activeTab = null;
        //: Tab yang diklik pengguna. Selama tabnya masih ada, island tidak
        //: berpindah sendiri walau tab lain mulai berbunyi.
        this._selectedKey = '';
        this._lastTabsAt = 0;
        this._tabsSignature = '';
        //: Kelas jendela → nama aplikasi web, dibaca sekali per kelas.
        this._appNames = new Map();

        this._signalIds = [];
        this._settingsIds = [];

        this._artDir = GLib.build_filenamev([
            GLib.get_user_cache_dir(), 'powertools', 'media-art',
        ]);

        this._loadStylesheet();
        this._buildUi();
        this._connectShell();
        this._connectBus();
        this._scheduleArtPrune();
        this._startHoverWatch();

        debug('siap');
    }

    // ------------------------------------------------------------- pengaturan
    get enabled() {
        try {
            return this._settings
                ? this._settings.get_boolean('media-island-enabled')
                : true;
        } catch (e) {
            return true;
        }
    }

    get hoverDelayMs() {
        try {
            return clamp(this._settings.get_int('media-island-hover-delay'),
                0, 10000);
        } catch (e) {
            return 1000;
        }
    }

    get popupMs() {
        try {
            return clamp(this._settings.get_int('media-island-popup-time'),
                0, 30000);
        } catch (e) {
            return 5000;
        }
    }

    _loadStylesheet() {
        try {
            this._stylesheet = this._dir.get_child('stylesheet.css');
            if (!this._stylesheet.query_exists(null))
                return;
            this._theme = St.ThemeContext.get_for_stage(global.stage).get_theme();
            this._theme.load_stylesheet(this._stylesheet);
        } catch (e) {
            this._theme = null;
            this._stylesheet = null;
            logError(e, 'PowerTools: gagal memuat stylesheet media');
        }
    }

    // ------------------------------------------------------------------- UI
    _buildUi() {
        this._island = makeBox(true, 'pt-media-island', {
            reactive: true,
            track_hover: true,
            visible: false,
            opacity: 0,
        });
        this._island.set_pivot_point(0.5, 0);

        const row = makeBox(false, 'pt-media-row', {
            x_expand: true,
        });
        row.layout_manager.spacing = 10;

        this._cover = new St.Bin({
            style_class: 'pt-media-cover',
            width: 54,
            height: 54,
            x_align: Clutter.ActorAlign.START,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._coverIcon = new St.Icon({
            icon_name: 'audio-x-generic-symbolic',
            icon_size: 24,
            style_class: 'pt-media-cover-icon',
        });
        this._cover.set_child(this._coverIcon);
        row.add_child(this._cover);

        const info = makeBox(true, null, {
            x_expand: true,
            y_align: Clutter.ActorAlign.CENTER,
        });
        info.layout_manager.spacing = 1;

        this._title = new St.Label({
            style_class: 'pt-media-title',
            text: '',
            x_expand: true,
            x_align: Clutter.ActorAlign.FILL,
        });
        this._title.clutter_text.ellipsize = Pango.EllipsizeMode.END;
        this._title.clutter_text.line_wrap = false;
        info.add_child(this._title);

        this._artist = new St.Label({
            style_class: 'pt-media-sub',
            text: '',
            x_expand: true,
            x_align: Clutter.ActorAlign.FILL,
        });
        this._artist.clutter_text.ellipsize = Pango.EllipsizeMode.END;
        this._artist.clutter_text.line_wrap = false;
        info.add_child(this._artist);

        row.add_child(info);

        const controls = makeBox(false, null, {
            x_align: Clutter.ActorAlign.END,
            y_align: Clutter.ActorAlign.CENTER,
        });
        controls.layout_manager.spacing = 4;

        this._prevIcon = new St.Icon({
            icon_name: 'media-skip-backward-symbolic',
            icon_size: 16,
        });
        this._prev = this._makeButton('pt-media-button', this._prevIcon,
            () => this._previous());
        controls.add_child(this._prev);

        this._playIcon = new St.Icon({
            icon_name: 'media-playback-start-symbolic',
            icon_size: 18,
        });
        this._play = this._makeButton('pt-media-play', this._playIcon,
            () => this._togglePlay());
        controls.add_child(this._play);

        this._nextIcon = new St.Icon({
            icon_name: 'media-skip-forward-symbolic',
            icon_size: 16,
        });
        this._next = this._makeButton('pt-media-button', this._nextIcon,
            () => this._nextTrack());
        controls.add_child(this._next);

        row.add_child(controls);
        this._island.add_child(row);

        const progressRow = makeBox(false, 'pt-media-progress', {
            x_expand: true,
        });
        progressRow.layout_manager.spacing = 10;

        this._seek = new St.DrawingArea({
            style_class: 'pt-media-seek',
            reactive: true,
            track_hover: true,
            x_expand: true,
            height: 18,
        });
        this._seek.connect('repaint', area => this._drawSeek(area));
        this._seek.connect('enter-event', () => {
            this._seekHover = true;
            this._seek.queue_repaint();
            return Clutter.EVENT_PROPAGATE;
        });
        this._seek.connect('leave-event', () => {
            this._seekHover = false;
            this._seek.queue_repaint();
            return Clutter.EVENT_PROPAGATE;
        });
        this._seek.connect('button-press-event', (_area, event) => {
            if (!this._canSeek())
                return Clutter.EVENT_STOP;
            this._dragging = true;
            this._seekFromEvent(event);
            return Clutter.EVENT_STOP;
        });
        this._seek.connect('motion-event', (_area, event) => {
            if (!this._dragging)
                return Clutter.EVENT_PROPAGATE;
            this._seekFromEvent(event);
            return Clutter.EVENT_STOP;
        });
        this._seek.connect('button-release-event', (_area, event) => {
            if (!this._dragging)
                return Clutter.EVENT_PROPAGATE;
            this._seekFromEvent(event);
            this._dragging = false;
            this._commitSeek(this._seekFraction);
            return Clutter.EVENT_STOP;
        });
        progressRow.add_child(this._seek);

        this._time = new St.Label({
            style_class: 'pt-media-time',
            text: '',
            y_align: Clutter.ActorAlign.CENTER,
        });
        progressRow.add_child(this._time);
        this._island.add_child(progressRow);
        this._progressRow = progressRow;

        // Tab ada di atas: satu tab per pemutar MPRIS, ditambah tab jendela
        // browser (aplikasi web) yang tidak diekspos MPRIS. Dengan tab, dua
        // sumber yang berbunyi bersamaan terlihat sekaligus.
        // Baris tab disusun manual (bukan FlowLayout): FlowLayout melaporkan
        // tinggi dua baris walau semua tab muat satu baris, sehingga muncul
        // celah kosong di atas judul lagu.
        this._tabBar = makeBox(true, 'pt-media-tabs', {
            visible: false,
            x_expand: true,
        });
        this._tabBar.layout_manager.spacing = 6;
        this._island.insert_child_at_index(this._tabBar, 0);

        Main.layoutManager.addChrome(this._island, {
            affectsInputRegion: true,
            // Island harus tetap terlihat di atas video/fullscreen.
            trackFullscreen: false,
        });
        this._positionIsland();
    }

    _makeButton(styleClass, icon, handler) {
        const button = new St.Button({
            style_class: styleClass,
            can_focus: false,
            reactive: true,
            track_hover: true,
            child: icon,
        });
        button.connect('clicked', handler);
        return button;
    }

    // -------------------------------------------------------------- interaksi
    _connectShell() {
        this._signalIds.push([
            Main.layoutManager,
            Main.layoutManager.connect('monitors-changed',
                () => this._positionIsland()),
        ]);

        this._signalIds.push([
            Main.overview,
            Main.overview.connect('showing', () => this._hide()),
        ]);

        this._signalIds.push([
            Main.sessionMode,
            Main.sessionMode.connect('updated', () => {
                if (Main.sessionMode.isLocked)
                    this._hide();
            }),
        ]);

        if (this._settings) {
            this._settingsIds.push(this._settings.connect(
                'changed::media-island-enabled',
                () => this._onSettingsChanged()));
            this._settingsIds.push(this._settings.connect(
                'changed::media-island-hover-delay',
                () => this._cancelHover()));
        }
    }

    _onSettingsChanged() {
        if (!this.enabled) {
            this._cancelHover();
            this._hide();
        }
    }

    // Deteksi hover lewat polling, bukan hanya event motion: pointer yang
    // sudah berhenti di tepi atas (atau event yang tertelan panel) tetap
    // terdeteksi. Biaya 5 kali cek per detik sangat kecil.
    _startHoverWatch() {
        if (this._hoverWatch || this._destroyed)
            return;
        this._hoverWatch = GLib.timeout_add(GLib.PRIORITY_DEFAULT,
            HOVER_POLL_MS, () => this._onHoverTick());
    }

    _onHoverTick() {
        this._hoverWatch = 0;
        if (this._destroyed)
            return GLib.SOURCE_REMOVE;

        const now = nowMs();
        // Posisi pointer ikut dicatat saat log detail aktif: di shell bersarang
        // gerakan relatif tidak dijepit ke tepi layar, jadi skrip perekaman GIF
        // perlu membaca posisi sebenarnya sebelum menggeser pointer.
        if (DEBUG) {
            const [px, py] = global.get_pointer();
            debug('pointer', px, py);
        }
        if (this.enabled && !this._shown && !Main.sessionMode.isLocked &&
            !Main.overview.visible) {
            const [x, y, mods] = global.get_pointer();
            const dragging = !!(mods & Clutter.ModifierType.BUTTON1_MASK);
            if (!dragging && this._inHotZone(x, y) && this._hasPlaying()) {
                const moved = this._hoverStart > 0 &&
                    (Math.abs(x - this._hoverX) > HOVER_MOVE_TOLERANCE ||
                     Math.abs(y - this._hoverY) > HOVER_MOVE_TOLERANCE);
                if (this._hoverStart === 0 || moved) {
                    this._hoverStart = now;
                    this._hoverX = x;
                    this._hoverY = y;
                }
                if (now - this._hoverStart >= this.hoverDelayMs) {
                    this._hoverStart = 0;
                    this._hoverX = -1;
                    this._hoverY = -1;
                    console.log('[powertools-media] hover terpenuhi di',
                        Math.round(x), Math.round(y));
                    this._show('hover');
                }
            } else {
                this._hoverStart = 0;
            }
        } else {
            this._hoverStart = 0;
        }

        this._hoverWatch = GLib.timeout_add(GLib.PRIORITY_DEFAULT,
            HOVER_POLL_MS, () => this._onHoverTick());
        return GLib.SOURCE_REMOVE;
    }

    _cancelHover() {
        this._hoverStart = 0;
        this._hoverX = -1;
        this._hoverY = -1;
    }

    _panelAtTop() {
        const panel = Main.panel;
        const monitor = Main.layoutManager.primaryMonitor;
        if (!panel || !monitor || !panel.visible || panel.height <= 0)
            return false;
        const [, py] = panel.get_transformed_position();
        // Saat shell baru mulai panel belum di-layout (NaN). Asumsikan
        // panel standar di atas; posisi sebenarnya dibaca lagi saat show.
        if (isNaN(py))
            return true;
        return py <= monitor.y + 2;
    }

    _inHotZone(x, y) {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return false;
        const center = monitor.x + monitor.width / 2;
        // Kalau panel di atas, seluruh tinggi panel ikut dihitung: orang
        // berhenti di atas jam, bukan di piksel paling atas. Kalau panel
        // dipindah ke bawah (Zorin), pakai pita tipis di tepi atas layar.
        const panelHeight = Main.panel?.height ?? 0;
        const height = this._panelAtTop()
            ? Math.max(panelHeight + 4, HOT_MIN_HEIGHT)
            : HOT_TOP_STRIP;
        return y <= monitor.y + height &&
            Math.abs(x - center) <= HOT_HALF_WIDTH;
    }

    _pointerInIsland() {
        if (!this._shown || !this._island)
            return false;
        const [x, y] = global.get_pointer();
        const [ix, iy] = this._island.get_transformed_position();
        return x >= ix && x <= ix + this._island.width &&
            y >= iy && y <= iy + this._island.height;
    }

    _positionIsland() {
        if (!this._island)
            return;
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;

        const width = Math.round(Math.min(ISLAND_WIDTH,
            Math.max(ISLAND_MIN_WIDTH, monitor.width - 40)));
        if (this._island.width !== width)
            this._island.set_width(width);

        const panelHeight = Main.panel?.height ?? 0;
        const panelAtTop = this._panelAtTop();
        const x = monitor.x + Math.round((monitor.width - width) / 2);
        // Panel di atas: menempel di bawah panel. Panel di bawah (Zorin):
        // menempel langsung ke tepi atas layar seperti tetesan air.
        const y = panelAtTop
            ? monitor.y + panelHeight + ISLAND_MARGIN_TOP
            : monitor.y + ISLAND_MARGIN_TOP;
        this._island.set_position(x, y);
        const geoKey = `${x},${y},${panelAtTop},${panelHeight}`;
        if (this._geoKey !== geoKey) {
            this._geoKey = geoKey;
            const [ppx, ppy] = Main.panel
                ? Main.panel.get_transformed_position() : [0, 0];
            console.log('[powertools-media] jangkar', x, y,
                'panel', panelAtTop ? 'atas' : 'bawah',
                `(${panelHeight}px @ ${Math.round(ppx)},${Math.round(ppy)})`,
                'layar', monitor.width, 'x', monitor.height);
        }
    }

    // ------------------------------------------------------------- tampil/hide
    _show(reason) {
        if (this._destroyed || !this.enabled)
            return;
        if (Main.sessionMode.isLocked || Main.overview.visible)
            return;
        if (reason === 'hover' && !this._hasPlaying())
            return;
        if (!this._hasVisiblePlayer())
            return;

        const now = nowMs();
        this._refresh(true);

        if (this._shown) {
            if (reason === 'change')
                this._autoHideAt = now + this.popupMs;
            return;
        }

        this._shown = true;
        this._autoHideAt = reason === 'change' ? now + this.popupMs : 0;
        this._leftAt = 0;
        this._island.remove_all_transitions();
        // Tampil dengan opasitas penuh sejak frame pertama; hanya skala yang
        // dianimasikan. Animasi opacity dari 0 sempat membuat kedipan satu
        // frame pada St/Clutter.
        this._island.opacity = 255;
        this._island.set_scale(0.9, 0.7);
        this._positionIsland();
        this._island.visible = true;
        // Tetesan air: tumbuh ke bawah tanpa overshoot.
        this._island.ease({
            scale_x: 1,
            scale_y: 1,
            duration: 220,
            mode: Clutter.AnimationMode.EASE_OUT_CUBIC,
        });
        this._startUiTimer();
        console.log('[powertools-media] tampil:', reason,
            this._activeTab?.label ?? '-');
    }

    _hide() {
        if (!this._shown || !this._island)
            return;
        this._shown = false;
        this._autoHideAt = 0;
        this._leftAt = 0;
        this._dragging = false;
        this._stopUiTimer();
        this._cancelHover();
        this._island.remove_all_transitions();
        this._island.ease({
            opacity: 0,
            scale_x: 0.9,
            scale_y: 0.72,
            duration: 170,
            mode: Clutter.AnimationMode.EASE_IN_QUAD,
            onComplete: () => {
                if (this._shown || !this._island)
                    return;
                this._island.visible = false;
                this._island.set_scale(1, 1);
                this._island.set_translation(0, 0, 0);
            },
        });
        console.log('[powertools-media] sembunyi');
    }

    _startUiTimer() {
        if (this._uiTimer)
            return;
        this._uiTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, TICK_MS,
            () => this._tick());
    }

    _stopUiTimer() {
        if (!this._uiTimer)
            return;
        GLib.source_remove(this._uiTimer);
        this._uiTimer = 0;
    }

    _tick() {
        this._uiTimer = 0;
        if (!this._shown)
            return GLib.SOURCE_REMOVE;

        if (!this.enabled || Main.sessionMode.isLocked ||
            Main.overview.visible) {
            this._hide();
            return GLib.SOURCE_REMOVE;
        }

        this._updateProgress();
        this._pollPosition();

        const now = nowMs();
        // Jendela tidak memberi sinyal saat dibuka/ditutup, jadi daftar tab
        // diperiksa berkala: aplikasi web yang baru dibuka ikut muncul.
        if (now - this._lastTabsAt >= WINDOW_SCAN_MS)
            this._refresh();

        const [x, y] = global.get_pointer();
        const keep = this._dragging || this._pointerInIsland() ||
            this._inHotZone(x, y);
        if (keep) {
            this._leftAt = 0;
            if (this._autoHideAt)
                this._autoHideAt = Math.max(this._autoHideAt, now + 1200);
        } else {
            if (!this._leftAt)
                this._leftAt = now;
            const deadlineReached = !this._autoHideAt || now >= this._autoHideAt;
            if (now - this._leftAt >= HIDE_GRACE_MS && deadlineReached)
                this._hide();
        }

        this._uiTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, TICK_MS,
            () => this._tick());
        return GLib.SOURCE_REMOVE;
    }

    // ---------------------------------------------------------------- render
    _visiblePlayers() {
        const players = [];
        for (const player of this._players.values()) {
            if (!player.proxy)
                continue;
            const metadata = player.metadata();
            if (metadata && metadata.title)
                players.push(player);
        }
        return players;
    }

    _hasVisiblePlayer() {
        return this._visiblePlayers().length > 0;
    }

    _hasPlaying() {
        return this._visiblePlayers().some(player => player.isPlaying());
    }

    // Jendela hanya bisa dibaca dari dalam shell. Dipanggil berkala saat island
    // tampil karena jendela tidak punya sinyal seperti MPRIS.
    _collectWindows() {
        const windows = [];
        let actors = [];
        try {
            actors = global.get_window_actors();
        } catch (e) {
            return windows;
        }
        for (const actor of actors) {
            const window = actor?.meta_window;
            if (!window)
                continue;
            let pid = 0;
            let wmClass = '';
            let title = '';
            try {
                pid = window.get_pid();
                wmClass = window.get_wm_class() ?? '';
                title = window.get_title() ?? '';
            } catch (e) {
                continue;
            }
            if (!pid || !title)
                continue;
            windows.push({pid, wmClass, title});
        }
        return windows;
    }

    // Nama aplikasi web dibaca dari berkas .desktop yang dibuat browser saat
    // aplikasi dipasang: namanya sama dengan kelas jendela PWA. Hasilnya
    // diingat per kelas supaya tidak membaca berkas berulang kali.
    _appNameFor(wmClass) {
        const key = String(wmClass ?? '').trim();
        if (!key)
            return '';
        if (this._appNames.has(key))
            return this._appNames.get(key);

        let name = '';
        const dirs = [
            GLib.build_filenamev([GLib.get_user_data_dir(), 'applications']),
            '/usr/local/share/applications',
            '/usr/share/applications',
            GLib.build_filenamev([GLib.get_home_dir(),
                '.local/share/flatpak/exports/share/applications']),
            '/var/lib/flatpak/exports/share/applications',
        ];
        for (const dir of dirs) {
            const path = GLib.build_filenamev([dir, `${key}.desktop`]);
            if (!GLib.file_test(path, GLib.FileTest.EXISTS))
                continue;
            try {
                const keyFile = new GLib.KeyFile();
                keyFile.load_from_file(path, GLib.KeyFileFlags.NONE);
                name = keyFile.get_string('Desktop Entry', 'Name').trim();
            } catch (e) {
                name = '';
            }
            if (name)
                break;
        }
        this._appNames.set(key, name);
        return name;
    }

    _buildTabs() {
        const players = this._visiblePlayers().map(player => {
            const metadata = player.metadata() ?? {};
            return {
                owner: player.owner,
                pid: pidFromBusName(player.owner),
                label: player.displayName(),
                title: metadata.title ?? '',
                artist: metadata.artist ?? '',
                playing: player.isPlaying(),
                lastChangeAt: player.lastChangeAt,
            };
        });
        // Hanya jendela milik proses pemutar yang dibaca: judul jendela lain
        // tidak ada urusannya dengan island.
        const pids = new Set(players.map(player => player.pid).filter(Boolean));
        const windows = this._collectWindows()
            .filter(window => pids.has(window.pid));
        return buildTabs({
            players,
            windows,
            appNameFor: wmClass => this._appNameFor(wmClass),
        });
    }

    _refresh(force = false) {
        const tabs = this._buildTabs();
        // buildTabs hanya bekerja dengan data biasa; kembalikan objek pemutar
        // aslinya supaya kontrol tetap bisa memanggil D-Bus.
        for (const tab of tabs) {
            if (tab.kind === 'player')
                tab.player = this._players.get(tab.key.slice(2)) ?? null;
        }
        this._tabs = tabs;
        this._lastTabsAt = nowMs();

        // Pemeriksaan jendela berkala tidak perlu membangun ulang tombol tab
        // kalau isinya sama; membangun ulang terus-menerus membuat tab
        // berkedip dan menghapus status hover.
        const signature = tabs
            .map(tab => [tab.key, tab.label, tab.playing, tab.title].join('\u0001'))
            .join('\u0002');
        const changed = signature !== this._tabsSignature;
        this._tabsSignature = signature;

        if (this._selectedKey &&
            !tabs.some(tab => tab.key === this._selectedKey))
            this._selectedKey = '';

        const tab = resolveTab(tabs, this._selectedKey);
        if (!tab) {
            this._activeTab = null;
            this._active = null;
            if (this._shown)
                this._hide();
            return;
        }

        this._activeTab = tab;
        this._active = tab.kind === 'player' ? tab.player : null;
        if (force || (this._shown && changed))
            this._render(tabs, tab);
    }

    _selectTab(key) {
        this._selectedKey = key;
        this._autoHideAt = nowMs() + this.popupMs;
        // Jangan hancurkan tombol yang sedang mengirim sinyal "clicked"; render
        // ulang setelah sinyal selesai.
        GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            if (!this._destroyed && this._shown)
                this._refresh(true);
            return GLib.SOURCE_REMOVE;
        });
    }

    _render(tabs, tab) {
        this._renderTabs(tabs, tab);
        if (tab.kind === 'window') {
            this._renderWindow(tab);
            return;
        }

        const player = tab.player;
        const metadata = player?.metadata();
        if (!metadata)
            return;

        this._title.text = metadata.title || 'Tanpa judul';
        const source = tab.label || player.displayName();
        this._artist.text = metadata.artist
            ? (tabs.length > 1 ? metadata.artist : `${metadata.artist} · ${source}`)
            : source;

        const playing = player.isPlaying();
        this._playIcon.icon_name = playing
            ? 'media-playback-pause-symbolic'
            : 'media-playback-start-symbolic';
        this._play.opacity = 255;
        this._prev.opacity = player.canGoPrevious() ? 255 : 90;
        this._next.opacity = player.canGoNext() ? 255 : 90;
        this._progressRow.visible = true;

        this._setCover(metadata.artUrl);
        this._updateProgress();
    }

    // Tab jendela: MPRIS tidak melaporkan posisi, durasi, atau kontrol untuk
    // sesi yang tidak dieksposnya, jadi hanya judul yang ditampilkan. Kontrol
    // diredupkan, bukan disembunyikan, supaya tata letak tab tidak melompat.
    _renderWindow(tab) {
        this._title.text = tab.title || tab.label || 'Jendela browser';
        this._artist.text = tab.detail ?? tab.label;
        this._playIcon.icon_name = 'media-playback-start-symbolic';
        this._play.opacity = 90;
        this._prev.opacity = 90;
        this._next.opacity = 90;
        this._progressRow.visible = false;
        this._time.text = '';
        this._setCover('');
    }

    // Lebar tab diperkirakan dari panjang label, bukan diukur: label selalu
    // bisa dipotong dengan elipsis, dan lebar tetap membuat baris tab tidak
    // melompat saat judul lagu berganti.
    _tabWidth(text) {
        return clamp(26 + String(text).length * 7, 62, 150);
    }

    _renderTabs(tabs, active) {
        this._tabBar.destroy_all_children();
        if (tabs.length <= 1) {
            this._tabBar.visible = false;
            return;
        }

        const totals = {};
        for (const tab of tabs)
            totals[tab.label] = (totals[tab.label] ?? 0) + 1;
        const seen = {};

        const available = Math.max(120, (this._island.width || ISLAND_WIDTH) - 32);
        const spacing = 6;
        let row = makeBox(false, null, {x_expand: true});
        row.layout_manager.spacing = spacing;
        this._tabBar.add_child(row);
        let used = 0;

        for (const tab of tabs) {
            let text = tab.label;
            if (totals[tab.label] > 1) {
                seen[tab.label] = (seen[tab.label] ?? 0) + 1;
                text = `${text} ${seen[tab.label]}`;
            }

            const width = this._tabWidth(text);
            if (used > 0 && used + width > available) {
                row = makeBox(false, null, {x_expand: true});
                row.layout_manager.spacing = spacing;
                this._tabBar.add_child(row);
                used = 0;
            }
            used += width + spacing;

            const content = makeBox(false, null, {
                y_align: Clutter.ActorAlign.CENTER,
            });
            content.layout_manager.spacing = 5;

            if (tab.playing) {
                content.add_child(new St.Widget({
                    style_class: 'pt-media-tab-dot',
                    y_align: Clutter.ActorAlign.CENTER,
                }));
            }

            const label = new St.Label({
                text,
                style_class: 'pt-media-tab-label',
            });
            label.clutter_text.ellipsize = Pango.EllipsizeMode.END;
            label.clutter_text.line_wrap = false;
            label.set_width(width - 20);
            content.add_child(label);

            const button = new St.Button({
                style_class: 'pt-media-tab',
                can_focus: false,
                reactive: true,
                track_hover: true,
                child: content,
            });
            if (tab.kind === 'window')
                button.add_style_class_name('pt-media-tab-window');
            if (tab.key === active.key)
                button.add_style_class_name('pt-media-tab-active');
            button.connect('clicked', () => this._selectTab(tab.key));
            row.add_child(button);
        }
        this._tabBar.visible = true;
    }

    _updateProgress() {
        const player = this._active;
        if (!player) {
            this._time.text = '';
            return;
        }
        const metadata = player.metadata();
        if (!metadata)
            return;

        const length = metadata.length;
        const position = clamp(player.estimatedPosition(), 0, length || Infinity);
        if (!this._dragging) {
            this._seekFraction = length > 0 ? position / length : 0;
            this._seek.queue_repaint();
        }
        this._time.text = length > 0
            ? `${formatTime(position)} / ${formatTime(length)}`
            : formatTime(position);
    }

    _drawSeek(area) {
        const cr = area.get_context();
        const width = area.width;
        const height = area.height;
        if (width <= 0 || height <= 0)
            return;

        const barHeight = 4;
        const y = Math.round((height - barHeight) / 2);
        const radius = barHeight / 2;
        const fraction = clamp(this._seekFraction, 0, 1);

        cr.newPath();
        cr.setSourceRGBA(1, 1, 1, 0.18);
        this._roundedRect(cr, 0, y, width, barHeight, radius);
        cr.fill();

        if (fraction > 0) {
            const fillWidth = Math.max(barHeight, Math.round(width * fraction));
            cr.newPath();
            cr.setSourceRGBA(1, 1, 1, 0.92);
            this._roundedRect(cr, 0, y, fillWidth, barHeight, radius);
            cr.fill();
        }

        if (this._seekHover || this._dragging) {
            const x = clamp(Math.round(width * fraction), 5, width - 5);
            cr.newPath();
            cr.setSourceRGBA(1, 1, 1, 1);
            cr.arc(x, height / 2, 5, 0, Math.PI * 2);
            cr.fill();
        }
    }

    _roundedRect(cr, x, y, width, height, radius) {
        const r = Math.min(radius, width / 2, height / 2);
        cr.moveTo(x + r, y);
        cr.lineTo(x + width - r, y);
        cr.arc(x + width - r, y + r, r, -Math.PI / 2, 0);
        cr.lineTo(x + width, y + height - r);
        cr.arc(x + width - r, y + height - r, r, 0, Math.PI / 2);
        cr.lineTo(x + r, y + height);
        cr.arc(x + r, y + height - r, r, Math.PI / 2, Math.PI);
        cr.lineTo(x, y + r);
        cr.arc(x + r, y + r, r, Math.PI, Math.PI * 1.5);
        cr.closePath();
    }

    _canSeek() {
        const metadata = this._active?.metadata();
        return Boolean(metadata && metadata.length > 0 && this._active.canSeek());
    }

    _seekFromEvent(event) {
        const [stageX] = event.get_coords();
        const [areaX] = this._seek.get_transformed_position();
        const width = Math.max(1, this._seek.width);
        this._seekFraction = clamp((stageX - areaX) / width, 0, 1);

        const length = this._active?.metadata()?.length ?? 0;
        if (length > 0) {
            this._time.text = `${formatTime(this._seekFraction * length)} / ` +
                formatTime(length);
        }
        this._seek.queue_repaint();
    }

    _commitSeek(fraction) {
        const player = this._active;
        const metadata = player?.metadata();
        if (!player || !metadata || metadata.length <= 0 || !player.canSeek())
            return;

        const target = Math.round(clamp(fraction, 0, 1) * metadata.length);
        const current = player.estimatedPosition();
        player.positionUs = target;
        player.positionAt = GLib.get_monotonic_time();
        player.positionToken++;
        this._updateProgress();

        const fallback = () => {
            this._call(player, 'Seek',
                new GLib.Variant('(x)', Math.round(target - current)));
        };
        if (metadata.trackId.startsWith('/')) {
            try {
                this._call(player, 'SetPosition',
                    new GLib.Variant('(ox)', [metadata.trackId, target]), fallback);
                return;
            } catch (e) {
                debug('SetPosition tidak valid:', e.message);
            }
        }
        fallback();
    }

    // --------------------------------------------------------------- MPRIS
    _connectBus() {
        if (!Gio.DBus.session) {
            debug('tanpa session bus');
            return;
        }

        this._nameOwnerId = Gio.DBus.session.signal_subscribe(
            'org.freedesktop.DBus',
            'org.freedesktop.DBus',
            'NameOwnerChanged',
            '/org/freedesktop/DBus',
            null,
            Gio.DBusSignalFlags.NONE,
            (_connection, _sender, _path, _iface, _signal, params) => {
                const [name, oldOwner, newOwner] = unpack(params, []);
                if (!name || !name.startsWith(`${BUS_PREFIX}.`))
                    return;
                if (name.includes('playerctld'))
                    return;
                if (newOwner && newOwner !== '')
                    this._addPlayer(name, true);
                else
                    this._removePlayer(name);
            });

        Gio.DBus.session.call(
            'org.freedesktop.DBus',
            '/org/freedesktop/DBus',
            'org.freedesktop.DBus',
            'ListNames',
            null,
            null,
            Gio.DBusCallFlags.NONE,
            1500,
            null,
            (bus, result) => {
                let names = [];
                try {
                    names = unpack(bus.call_finish(result), [[]])[0] ?? [];
                } catch (e) {
                    debug('ListNames gagal:', e.message);
                    return;
                }
                for (const name of names) {
                    if (!name.startsWith(`${BUS_PREFIX}.`))
                        continue;
                    if (name.includes('playerctld'))
                        continue;
                    this._addPlayer(name, false);
                }
            });
    }

    _addPlayer(owner, announce) {
        if (this._destroyed || EXCLUDED_PLAYERS.has(owner) ||
            this._players.has(owner))
            return;

        const player = new Player(owner);
        this._players.set(owner, player);
        debug('pemutar baru:', owner, announce ? '(baru)' : '(awal)');

        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            null,
            owner,
            OBJECT_PATH,
            PLAYER_IFACE,
            null,
            (_source, result) => {
                if (this._destroyed || !this._players.has(owner))
                    return;

                let proxy = null;
                try {
                    proxy = Gio.DBusProxy.new_for_bus_finish(result);
                } catch (e) {
                    debug('proxy gagal:', owner, e.message);
                    this._players.delete(owner);
                    return;
                }

                player.proxy = proxy;
                player.initialized = true;
                player.lastStatus = player.playbackStatus();
                player.trackKey = trackKey(player.metadata());

                player.propsChangedId = proxy.connect('g-properties-changed',
                    (_proxy, changed, invalidated) =>
                        this._onPropertiesChanged(player, changed, invalidated));
                player.signalId = proxy.connect('g-signal',
                    (_proxy, _sender, signal, params) =>
                        this._onPlayerSignal(player, signal, params));

                this._loadIdentity(player);
                this._refresh();

                if (announce && player.isPlaying() &&
                    player.metadata()?.title) {
                    player.lastChangeAt = nowMs();
                    this._refresh();
                    this._show('change');
                }
            });
    }

    _loadIdentity(player) {
        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            null,
            player.owner,
            OBJECT_PATH,
            ROOT_IFACE,
            null,
            (_source, result) => {
                if (this._destroyed || !this._players.has(player.owner))
                    return;
                let root = null;
                try {
                    root = Gio.DBusProxy.new_for_bus_finish(result);
                } catch (e) {
                    return;
                }
                player.root = root;
                player.identity = String(
                    unpack(root.get_cached_property('Identity'), '') ?? '');
                player.desktopEntry = String(
                    unpack(root.get_cached_property('DesktopEntry'), '') ?? '');
                this._refresh();
            });
    }

    _removePlayer(owner) {
        const player = this._players.get(owner);
        if (!player)
            return;
        this._players.delete(owner);
        if (player.proxy) {
            if (player.propsChangedId)
                player.proxy.disconnect(player.propsChangedId);
            if (player.signalId)
                player.proxy.disconnect(player.signalId);
        }
        debug('pemutar hilang:', owner);

        if (this._active?.owner === owner)
            this._active = null;
        if (this._selectedKey === `p:${owner}`)
            this._selectedKey = '';
        this._refresh();
        if (!this._hasVisiblePlayer())
            this._hide();
    }

    _onPropertiesChanged(player, changed, invalidated) {
        if (this._destroyed)
            return;

        let keys = [];
        try {
            keys = Object.keys(unpack(changed, {}) ?? {});
        } catch (e) {
            keys = [];
        }
        if (Array.isArray(invalidated))
            keys.push(...invalidated);

        if (keys.includes('Metadata')) {
            const metadata = player.metadata();
            const key = trackKey(metadata);
            const trackChanged = key && key !== player.trackKey;
            player.trackKey = key;

            if (trackChanged) {
                player.lastChangeAt = nowMs();
                player.positionUs = 0;
                player.positionAt = GLib.get_monotonic_time();
                player.positionToken++;
                this._refresh();
                if (metadata?.title)
                    this._show('change');
            } else {
                this._refresh();
            }
        }

        if (keys.includes('PlaybackStatus')) {
            const status = player.playbackStatus();
            const wasPlaying = player.lastStatus === 'Playing';
            const nowUs = GLib.get_monotonic_time();
            if (wasPlaying && status !== 'Playing') {
                // Bekukan posisi tepat saat jeda, bukan saat polling terakhir.
                player.positionUs = Math.max(0, player.positionUs) +
                    (nowUs - player.positionAt);
            }
            player.positionAt = nowUs;
            player.positionToken++;
            player.lastStatus = status;
            if (status === 'Playing' && !wasPlaying) {
                player.lastChangeAt = nowMs();
                this._refresh();
                this._show('change');
            } else {
                this._refresh();
            }
        }

        if (keys.includes('CanSeek') || keys.includes('CanGoNext') ||
            keys.includes('CanGoPrevious'))
            this._refresh();
    }

    _onPlayerSignal(player, signal, params) {
        if (signal !== 'Seeked')
            return;
        const [position] = unpack(params, [0]);
        player.positionUs = asNumber(position);
        player.positionAt = GLib.get_monotonic_time();
        player.positionToken++;
        if (this._shown)
            this._updateProgress();
    }

    _togglePlay() {
        const player = this._active;
        if (!player)
            return;
        this._call(player, 'PlayPause', null);
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, 200, () => {
            this._refresh();
            return GLib.SOURCE_REMOVE;
        });
    }

    _previous() {
        const player = this._active;
        if (!player || !player.canGoPrevious())
            return;
        this._call(player, 'Previous', null);
    }

    _nextTrack() {
        const player = this._active;
        if (!player || !player.canGoNext())
            return;
        this._call(player, 'Next', null);
    }

    _call(player, method, params, onError = null) {
        if (!player?.proxy)
            return;
        try {
            player.proxy.call(method, params, Gio.DBusCallFlags.NONE, 1200,
                null, (proxy, result) => {
                    try {
                        proxy.call_finish(result);
                    } catch (e) {
                        debug(`${method} gagal:`, e.message);
                        if (onError)
                            onError(e);
                    }
                });
        } catch (e) {
            debug(`${method} gagal:`, e.message);
            if (onError)
                onError(e);
        }
    }

    _pollPosition() {
        const player = this._active;
        if (!player?.proxy || player.positionPending)
            return;
        const now = nowMs();
        if (now - this._lastPositionPoll < POSITION_POLL_MS)
            return;
        this._lastPositionPoll = now;
        player.positionPending = true;
        const token = player.positionToken;

        try {
            player.proxy.call(
                'org.freedesktop.DBus.Properties.Get',
                new GLib.Variant('(ss)', [PLAYER_IFACE, 'Position']),
                Gio.DBusCallFlags.NONE,
                800,
                null,
                (proxy, result) => {
                    player.positionPending = false;
                    try {
                        const value = unpack(proxy.call_finish(result), [0]);
                        if (player.positionToken === token) {
                            player.positionUs = asNumber(value[0]);
                            player.positionAt = GLib.get_monotonic_time();
                        }
                    } catch (e) {
                        // Beberapa pemutar tidak mengimplementasikan Position.
                    }
                });
        } catch (e) {
            player.positionPending = false;
        }
    }

    // ----------------------------------------------------------- album art
    _setCover(url) {
        if (url === this._artUrl)
            return;
        this._artUrl = url;
        this._artToken++;
        const token = this._artToken;

        this._coverIcon.gicon = null;
        this._coverIcon.icon_name = 'audio-x-generic-symbolic';
        this._coverIcon.icon_size = 24;

        if (!url)
            return;

        if (url.startsWith('data:')) {
            this._artFromData(url, token);
            return;
        }
        if (url.startsWith('file:')) {
            const file = Gio.File.new_for_uri(url);
            if (file.query_exists(null))
                this._applyArt(file, token);
            return;
        }
        if (url.startsWith('http://') || url.startsWith('https://'))
            this._artFromNetwork(url, token);
    }

    _applyArt(file, token) {
        if (token !== this._artToken || !this._coverIcon)
            return;
        this._coverIcon.icon_name = null;
        this._coverIcon.icon_size = 52;
        this._coverIcon.gicon = new Gio.FileIcon({file});
    }

    _artFromData(url, token) {
        const cached = this._cacheFileFor(url);
        if (cached.query_exists(null)) {
            this._applyArt(cached, token);
            return;
        }

        const comma = url.indexOf(',');
        if (comma < 0)
            return;
        const header = url.slice(5, comma);
        if (!header.includes(';base64'))
            return;

        try {
            const data = GLib.base64_decode(url.slice(comma + 1));
            if (!data || data.length === 0 || data.length > MAX_ART_BYTES)
                return;
            this._writeCache(cached, data);
            this._applyArt(cached, token);
        } catch (e) {
            debug('art data gagal:', e.message);
        }
    }

    _artFromNetwork(url, token) {
        const cached = this._cacheFileFor(url);
        if (cached.query_exists(null)) {
            this._applyArt(cached, token);
            return;
        }

        let session = this._soup;
        if (!session) {
            try {
                session = new Soup.Session({
                    timeout: 10,
                    user_agent: 'PowerTools/1.0 (GNOME Shell)',
                });
            } catch (e) {
                debug('Soup tidak tersedia:', e.message);
                return;
            }
            this._soup = session;
        }

        let message = null;
        try {
            message = Soup.Message.new('GET', url);
        } catch (e) {
            return;
        }
        if (!message)
            return;

        session.send_and_read_async(message, GLib.PRIORITY_DEFAULT, null,
            (soup, result) => {
                if (token !== this._artToken)
                    return;
                let bytes = null;
                try {
                    bytes = soup.send_and_read_finish(result);
                } catch (e) {
                    debug('unduh art gagal:', e.message);
                    return;
                }
                const status = message.get_status();
                if (status < 200 || status >= 300)
                    return;
                if (!bytes || bytes.get_size() === 0 ||
                    bytes.get_size() > MAX_ART_BYTES)
                    return;
                try {
                    this._writeCache(cached, bytes.get_data());
                } catch (e) {
                    debug('simpan art gagal:', e.message);
                }
                this._applyArt(cached, token);
            });
    }

    _cacheFileFor(url) {
        let hash = '';
        try {
            hash = GLib.compute_checksum_for_string(
                GLib.ChecksumType.SHA1, url, -1) ?? '';
        } catch (e) {
            hash = '';
        }
        if (!hash)
            hash = String(Date.now());
        return Gio.File.new_for_path(
            GLib.build_filenamev([this._artDir, hash]));
    }

    _writeCache(file, data) {
        try {
            GLib.mkdir_with_parents(this._artDir, 0o700);
        } catch (e) {
            // Direktori sudah ada atau tidak bisa dibuat; biarkan gagal di bawah.
        }
        file.replace_contents(
            data,
            null,
            false,
            Gio.FileCreateFlags.REPLACE_DESTINATION |
                Gio.FileCreateFlags.PRIVATE,
            null);
    }

    _scheduleArtPrune() {
        this._pruneId = GLib.idle_add(GLib.PRIORITY_LOW, () => {
            this._pruneId = 0;
            this._pruneArtCache();
            return GLib.SOURCE_REMOVE;
        });
    }

    _pruneArtCache() {
        const dir = Gio.File.new_for_path(this._artDir);
        let enumerator = null;
        try {
            enumerator = dir.enumerate_children(
                'standard::name,time::modified',
                Gio.FileQueryInfoFlags.NONE,
                null);
        } catch (e) {
            return;
        }

        const cutoff = Date.now() / 1000 - ART_MAX_AGE_DAYS * 86400;
        try {
            let info;
            while ((info = enumerator.next_file(null)) !== null) {
                if (info.get_attribute_uint64('time::modified') > cutoff)
                    continue;
                try {
                    dir.get_child(info.get_name()).delete(null);
                } catch (e) {
                    // Berkas mungkin sedang dipakai; coba lagi lain waktu.
                }
            }
        } catch (e) {
            // Enumerasi gagal di tengah jalan: tidak fatal.
        }
        try {
            enumerator.close(null);
        } catch (e) {
            // Abaikan.
        }
    }

    // --------------------------------------------------------------- bersihkan
    destroy() {
        this._destroyed = true;

        this._cancelHover();
        if (this._hoverWatch) {
            GLib.source_remove(this._hoverWatch);
            this._hoverWatch = 0;
        }
        this._stopUiTimer();
        if (this._pruneId) {
            GLib.source_remove(this._pruneId);
            this._pruneId = 0;
        }

        for (const [object, id] of this._signalIds) {
            try {
                object.disconnect(id);
            } catch (e) {
                // Objek sudah hilang.
            }
        }
        this._signalIds = [];

        for (const id of this._settingsIds) {
            try {
                this._settings.disconnect(id);
            } catch (e) {
                // Abaikan.
            }
        }
        this._settingsIds = [];

        if (this._nameOwnerId && Gio.DBus.session) {
            try {
                Gio.DBus.session.signal_unsubscribe(this._nameOwnerId);
            } catch (e) {
                // Abaikan.
            }
            this._nameOwnerId = 0;
        }

        for (const player of this._players.values()) {
            if (player.proxy) {
                if (player.propsChangedId)
                    player.proxy.disconnect(player.propsChangedId);
                if (player.signalId)
                    player.proxy.disconnect(player.signalId);
            }
        }
        this._players.clear();
        this._active = null;

        if (this._soup) {
            try {
                this._soup.abort();
            } catch (e) {
                // Abaikan.
            }
            this._soup = null;
        }

        if (this._island) {
            try {
                Main.layoutManager.removeChrome(this._island);
            } catch (e) {
                // Abaikan.
            }
            this._island.destroy();
            this._island = null;
        }

        if (this._theme && this._stylesheet) {
            try {
                this._theme.unload_stylesheet(this._stylesheet);
            } catch (e) {
                // Abaikan.
            }
        }
        this._theme = null;
        this._stylesheet = null;

        debug('dibersihkan');
    }
}