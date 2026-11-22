// SPDX-License-Identifier: MIT
//
// PowerTools GNOME Shell bridge.
//
// Tugas ekstensi ini sengaja dibuat sesempit mungkin:
//  - memantau selection CLIPBOARD dan menuliskannya ke clipboard.jsonl
//    (di Wayland GNOME, aplikasi biasa tidak bisa memantau clipboard),
//  - mendaftarkan hotkey global dan mengaktifkan window PowerTools,
//  - menaruh ikon PowerTools di panel (tray) dan menyembunyikan jendela
//    lirik dari taskbar/Alt-Tab.
//
// Semua UI, pencarian, penyimpanan, dan color picker ada di aplikasi
// Python/GTK4, jadi ekstensi ini tidak menambah beban berarti ke shell.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {MediaIsland} from './media.js';

const DBUS_NAME = 'org.gnome.Shell.Extensions.PowerTools';
const DBUS_PATH = '/org/gnome/Shell/Extensions/PowerTools';
const APP_ID = 'io.github.powertools.App';
const DBUS_XML = `
<node>
  <interface name="org.gnome.Shell.Extensions.PowerTools.Clipboard">
    <method name="GetText">
      <arg type="s" direction="out" name="text"/>
    </method>
    <method name="Paste">
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="SetClipboardText">
      <arg type="s" direction="in" name="text"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="SetClipboardImage">
      <arg type="s" direction="in" name="path"/>
      <arg type="s" direction="in" name="mime"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="SetLyricsAbove">
      <arg type="b" direction="in" name="above"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="PlaceLyricsWindow">
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="SetLyricsSkipTaskbar">
      <arg type="b" direction="in" name="skip"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <signal name="Changed"/>
  </interface>
</node>`;

const TEXT_MIMETYPES = [
    'text/plain;charset=utf-8',
    'UTF8_STRING',
    'text/plain',
    'STRING',
];

// Format gambar yang bisa ditangkap. SVG sengaja dilewati: isinya teks
// dan ukurannya bisa sangat besar tanpa manfaat untuk riwayat.
const IMAGE_MIMETYPES = [
    'image/png',
    'image/jpeg',
    'image/webp',
    'image/bmp',
];

const MAX_TEXT_LENGTH = 500_000;
const MAX_IMAGE_BYTES = 20 * 1024 * 1024;
const SPOOL_MAX_BYTES = 16 * 1024 * 1024;
const SPOOL_KEEP_LINES = 3000;
const FOCUS_POLL_MS = 70;
const FOCUS_POLL_TRIES = 40;
const FOCUS_CONFIRM_TRIES = 5;
const CAPTURE_RETRY_MS = 70;
const CAPTURE_RETRY_TRIES = 3;
// Transfer clipboard bisa menggantung kalau pemilik selection hilang di
// tengah jalan (aplikasi ditutup, klien Wayland tidak menjawab). Tanpa batas
// waktu, satu transfer macet membuat semua copy berikutnya diabaikan sampai
// ekstensi dimuat ulang — jadi selalu ada pengawal waktu di sini.
const CAPTURE_STALL_MS = 2500;
// Perubahan selection yang berasal dari PowerTools sendiri (saat menempel
// atau menyalin item) tidak perlu ditangkap lagi; isinya sudah tercatat.
const SELF_CHANGE_WINDOW_MS = 700;
// Jeda sebelum ekstensi memastikan layanan PowerTools hidup. systemd dan
// autostart GNOME biasanya sudah lebih dulu; ini hanya jaring pengaman.
const ENSURE_APP_DELAY = 8;

// Auto-paste: fokuskan lagi jendela sebelum popup, lalu tekan Ctrl+V.
const PASTE_POLL_MS = 40;
const PASTE_POLL_TRIES = 75;      // ~3 detik menunggu target benar-benar fokus
const PASTE_REACTIVATE_EVERY = 5; // aktivasi ulang tiap ~200 ms
const PASTE_GRACE_MS = 6000;      // hanya izinkan sesaat setelah popup fokus

// Terminal umumnya memakai Ctrl+Shift+V, bukan Ctrl+V.
const TERMINAL_CLASSES = [
    'gnome-terminal', 'org.gnome.terminal', 'org.gnome.console', 'kgx',
    'konsole', 'xterm', 'uxterm', 'urxvt', 'rxvt', 'alacritty', 'kitty',
    'wezterm', 'foot', 'tilix', 'terminator', 'guake', 'yakuake',
    'terminology', 'sakura', 'qterminal', 'lxterminal', 'xfce4-terminal',
    'mate-terminal', 'io.elementary.terminal', 'warp', 'hyper', 'tabby',
    'ghostty', 'blackbox', 'com.raggesilver.blackbox',
];

const KEYBINDINGS = {
    'toggle-clipboard': {action: 'clipboard', module: 'clipboard'},
    'toggle-launcher': {action: 'launcher', module: 'launcher'},
    'pick-color': {action: 'color', module: 'color'},
    'toggle-lyrics': {action: 'lyrics', module: 'lyrics'},
};

const MODULE_KEYS = ['clipboard', 'launcher', 'color', 'media', 'lyrics', 'discord'];
const LYRICS_TITLE = 'PowerTools Lirik';
// Jendela lirik punya padding transparan 24px di setiap sisi untuk shadow
// (SHADOW_PAD di powertools/ui_lyrics.py). Jadi jarak visual kartu ke tepi
// layar = LYRICS_MARGIN + padding tersebut.
const LYRICS_MARGIN = 4;
const CONFIG_WATCH_DEBOUNCE_MS = 120;

export default class PowerToolsExtension extends Extension {
    enable() {
        this._active = true;
        this._selection = null;
        this._ownerChangedId = 0;
        this._busy = false;
        this._busySince = 0;
        this._captureGen = 0;
        this._captureWatchdog = 0;
        this._selfChanges = 0;
        this._selfChangeAt = 0;
        this._pendingCapture = false;
        this._captureTries = 0;
        this._lastText = '';
        this._lastImage = '';
        this._bindings = [];
        this._focusSource = 0;
        this._focusTries = 0;
        this._focusWindowId = 0;
        this._previousFocus = null;
        this._lastAppFocusMs = 0;
        this._appWasFocused = false;
        this._pasteSource = 0;
        this._pasteTarget = null;
        this._pasteTries = 0;
        this._pasteDevice = null;
        this._dbus = null;
        this._nameId = 0;
        this._ensureSource = 0;
        this._media = null;
        this._indicator = null;
        this._windowCreatedId = 0;
        this._hideLyricsTaskbar = true;
        this._modules = {
            clipboard: true, launcher: true, color: true, media: true, lyrics: true,
            discord: false,
        };
        this._configMonitor = null;
        this._configMonitorId = 0;
        this._configSyncSource = 0;
        this._settingsIds = [];

        this._spoolDir = GLib.build_filenamev([GLib.get_user_data_dir(), 'powertools']);
        this._spoolPath = GLib.build_filenamev([this._spoolDir, 'clipboard.jsonl']);
        this._imagesDir = GLib.build_filenamev([this._spoolDir, 'images']);

        try {
            this._settings = this.getSettings('org.gnome.shell.extensions.powertools');
        } catch (e) {
            logError(e, 'PowerTools: schema tidak ditemukan');
            this._settings = null;
        }

        this._exportDbus();
        this._watchConfig();
        this._syncModules();
        this._createIndicator();
        this._watchWindows();
        this._ensureAppRunning();

        if (this._settings) {
            this._settingsIds.push(this._settings.connect(
                'changed::media-island-enabled',
                () => this._syncMediaIsland()));
        }
    }

    disable() {
        this._active = false;

        if (this._windowCreatedId) {
            global.display.disconnect(this._windowCreatedId);
            this._windowCreatedId = 0;
        }
        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }

        if (this._settings) {
            for (const id of this._settingsIds)
                this._settings.disconnect(id);
        }
        this._settingsIds = [];

        if (this._configSyncSource) {
            GLib.source_remove(this._configSyncSource);
            this._configSyncSource = 0;
        }
        if (this._configMonitor && this._configMonitorId) {
            this._configMonitor.disconnect(this._configMonitorId);
            this._configMonitorId = 0;
        }
        this._configMonitor = null;

        if (this._media) {
            try {
                this._media.destroy();
            } catch (e) {
                logError(e, 'PowerTools: gagal mematikan media island');
            }
            this._media = null;
        }

        // Jendela lirik tidak boleh tetap "selalu di atas" saat jembatan mati.
        try {
            const lyrics = this._findLyricsWindow();
            if (lyrics) {
                lyrics.unmake_above();
                // Penanda skip-taskbar milik ekstensi ini juga dilepas,
                // supaya jendela kembali terlihat di taskbar.
                delete lyrics.skip_taskbar;
            }
        } catch (e) {
            // Jendela mungkin sudah hilang; tidak ada yang perlu dibersihkan.
        }
        if (this._ensureSource) {
            GLib.source_remove(this._ensureSource);
            this._ensureSource = 0;
        }

        if (this._selection && this._ownerChangedId)
            this._selection.disconnect(this._ownerChangedId);
        this._selection = null;
        this._ownerChangedId = 0;

        if (this._focusWindowId) {
            global.display.disconnect(this._focusWindowId);
            this._focusWindowId = 0;
        }
        this._previousFocus = null;

        if (this._captureWatchdog) {
            GLib.source_remove(this._captureWatchdog);
            this._captureWatchdog = 0;
        }
        this._busy = false;
        this._pendingCapture = false;

        if (this._pasteSource) {
            GLib.source_remove(this._pasteSource);
            this._pasteSource = 0;
        }
        this._pasteTarget = null;
        this._pasteTries = 0;
        this._pasteDevice = null;

        if (this._focusSource) {
            GLib.source_remove(this._focusSource);
            this._focusSource = 0;
        }

        for (const name of this._bindings) {
            try {
                Main.wm.removeKeybinding(name);
            } catch (e) {
                logError(e, `PowerTools: gagal melepas ${name}`);
            }
        }
        this._bindings = [];

        if (this._nameId) {
            Gio.bus_unown_name(this._nameId);
            this._nameId = 0;
        }
        if (this._dbus) {
            try {
                this._dbus.unexport();
            } catch (e) {
                logError(e);
            }
            this._dbus = null;
        }

        this._settings = null;
    }

    // ------------------------------------------------------------------ modul
    // Modul dibaca dari config.json milik aplikasi. Ekstensi ikut memantau
    // berkasnya, jadi mematikan modul dari GUI langsung terasa tanpa logout:
    // hotkey dilepas, pemantau clipboard berhenti, island dihancurkan.
    _configPath() {
        return GLib.build_filenamev([
            GLib.get_user_config_dir(), 'powertools', 'config.json',
        ]);
    }

    _readConfig() {
        try {
            const [ok, contents] = GLib.file_get_contents(this._configPath());
            if (!ok || !contents)
                return null;
            // GJS baru mengembalikan Uint8Array, versi lama mengembalikan string.
            const text = typeof contents === 'string'
                ? contents
                : new TextDecoder('utf-8').decode(contents);
            const data = JSON.parse(text);
            return data && typeof data === 'object' ? data : null;
        } catch (e) {
            // Berkas belum ada / JSON rusak.
            return null;
        }
    }

    _readModules() {
        const modules = {
            clipboard: true, launcher: true, color: true, media: true, lyrics: true,
            discord: false,
        };
        const data = this._readConfig();
        const raw = data && typeof data.modules === 'object' ? data.modules : {};
        for (const key of MODULE_KEYS) {
            if (typeof raw[key] === 'boolean')
                modules[key] = raw[key];
        }
        return modules;
    }

    _watchConfig() {
        try {
            const dir = GLib.build_filenamev([
                GLib.get_user_config_dir(), 'powertools',
            ]);
            GLib.mkdir_with_parents(dir, 0o700);
            this._configMonitor = Gio.File.new_for_path(dir)
                .monitor_directory(Gio.FileMonitorFlags.NONE, null);
            this._configMonitorId = this._configMonitor.connect(
                'changed', (_monitor, file) => {
                    if (file && file.get_basename() === 'config.json')
                        this._queueConfigSync();
                });
        } catch (e) {
            logError(e, 'PowerTools: gagal memantau config.json');
        }
    }

    _queueConfigSync() {
        if (this._configSyncSource)
            return;
        this._configSyncSource = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT,
            CONFIG_WATCH_DEBOUNCE_MS,
            () => {
                this._configSyncSource = 0;
                this._syncModules();
                return GLib.SOURCE_REMOVE;
            });
    }

    _syncModules() {
        this._modules = this._readModules();
        this._syncKeybindings();
        this._syncClipboardMonitor();
        this._syncMediaIsland();
        this._syncLyricsTaskbar();
        if (this._anyModuleEnabled())
            this._ensureAppRunning();
    }

    _moduleEnabled(name) {
        return this._modules[name] !== false;
    }

    _anyModuleEnabled() {
        return MODULE_KEYS.some(name => this._moduleEnabled(name));
    }

    // ------------------------------------------------------------- keybindings
    _syncKeybindings() {
        if (!this._settings)
            return;

        for (const [name, spec] of Object.entries(KEYBINDINGS)) {
            const wanted = this._moduleEnabled(spec.module);
            const bound = this._bindings.includes(name);

            if (wanted && !bound) {
                try {
                    Main.wm.addKeybinding(
                        name,
                        this._settings,
                        Meta.KeyBindingFlags.IGNORE_AUTOREPEAT,
                        Shell.ActionMode.ALL,
                        () => this._trigger(spec.action));
                    this._bindings.push(name);
                } catch (e) {
                    logError(e, `PowerTools: keybinding ${name} gagal`);
                }
            } else if (!wanted && bound) {
                try {
                    Main.wm.removeKeybinding(name);
                } catch (e) {
                    logError(e, `PowerTools: gagal melepas ${name}`);
                }
                this._bindings = this._bindings.filter(item => item !== name);
            }
        }
    }

    // ------------------------------------------------------- pemantau clipboard
    _syncClipboardMonitor() {
        if (!this._active)
            return;

        if (this._moduleEnabled('clipboard')) {
            if (!this._ownerChangedId) {
                this._selection = global.display.get_selection();
                this._ownerChangedId = this._selection.connect(
                    'owner-changed',
                    (_selection, type) => this._onOwnerChanged(type));
                this._readLastText();
            }
            // Ingat jendela non-PowerTools terakhir supaya auto-paste tahu
            // ke mana Ctrl+V harus dikirim.
            if (!this._focusWindowId) {
                this._focusWindowId = global.display.connect(
                    'notify::focus-window', () => this._onFocusWindowChanged());
                this._onFocusWindowChanged();
            }
            return;
        }

        // Modul mati: lepas sinyal supaya shell tidak bekerja sama sekali
        // setiap kali ada aplikasi menyalin sesuatu.
        if (this._selection && this._ownerChangedId)
            this._selection.disconnect(this._ownerChangedId);
        this._selection = null;
        this._ownerChangedId = 0;
        if (this._focusWindowId) {
            global.display.disconnect(this._focusWindowId);
            this._focusWindowId = 0;
        }
        this._previousFocus = null;
        this._captureEnd();
        this._pendingCapture = false;
        this._captureTries = 0;
        this._selfChanges = 0;
    }

    // "Dynamic island" media (MPRIS). Dibuat sesudah jembatan lain siap
    // supaya kegagalan fitur ini tidak pernah mematikan clipboard/hotkey.
    _syncMediaIsland() {
        if (!this._active)
            return;

        let allowed = this._moduleEnabled('media');
        if (allowed && this._settings) {
            try {
                allowed = this._settings.get_boolean('media-island-enabled');
            } catch (e) {
                allowed = true;
            }
        }
        const wanted = allowed && !Main.sessionMode.isGreeter && !!this._settings;

        if (wanted && !this._media) {
            try {
                this._media = new MediaIsland(this._settings, this.dir);
            } catch (e) {
                this._media = null;
                logError(e, 'PowerTools: media island gagal dimuat');
            }
        } else if (!wanted && this._media) {
            try {
                this._media.destroy();
            } catch (e) {
                logError(e, 'PowerTools: gagal mematikan media island');
            }
            this._media = null;
        }
    }

    _trigger(action, force = false) {
        // Jaga-jaga kalau hotkey masih terdaftar saat modul baru dimatikan.
        // Menu panel memakai force: aplikasi sendiri yang menampilkan
        // pemberitahuan "modul dimatikan" dan membuka Pengaturan.
        if (!force) {
            for (const spec of Object.values(KEYBINDINGS)) {
                if (spec.action === action && !this._moduleEnabled(spec.module))
                    return;
            }
        }

        const command = this._command();
        if (!command) {
            logError(new Error('Perintah PowerTools tidak ditemukan'));
            return;
        }

        try {
            GLib.spawn_command_line_async(`${this._quote(command)} ${action}`);
        } catch (e) {
            logError(e, `PowerTools: gagal menjalankan ${action}`);
            return;
        }

        // `quit` tidak membuka jendela apa pun; tidak perlu mengejar fokus.
        if (action !== 'quit')
            this._focusAppWindow(action);
    }

    _quote(command) {
        try {
            return GLib.shell_quote(command);
        } catch (e) {
            return `'${command.replace(/'/g, `'\\''`)}'`;
        }
    }

    // Jaring pengaman terakhir: kalau karena alasan apa pun layanan belum
    // hidup (systemd mati, autostart dilewati), ekstensi yang menyalakannya.
    // Dengan begitu pengguna tidak pernah perlu menjalankan app manual.
    _ensureAppRunning() {
        if (this._ensureSource)
            return;
        if (!this._anyModuleEnabled())
            return;
        const command = this._command();
        if (!command)
            return;
        this._ensureSource = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT,
            ENSURE_APP_DELAY,
            () => {
                this._ensureSource = 0;
                this._spawnDaemonIfNeeded(command);
                return GLib.SOURCE_REMOVE;
            });
    }

    _spawnDaemonIfNeeded(command) {
        if (!Gio.DBus.session) {
            this._spawnDaemon(command);
            return;
        }
        try {
            Gio.DBus.session.call(
                'org.freedesktop.DBus',
                '/org/freedesktop/DBus',
                'org.freedesktop.DBus',
                'NameHasOwner',
                new GLib.Variant('(s)', [APP_ID]),
                null,
                Gio.DBusCallFlags.NONE,
                1500,
                null,
                (connection, result) => {
                    let owned = false;
                    try {
                        owned = connection.call_finish(result).deepUnpack()[0];
                    } catch (e) {
                        owned = false;
                    }
                    if (!owned && this._active && this._anyModuleEnabled())
                        this._spawnDaemon(command);
                });
        } catch (e) {
            this._spawnDaemon(command);
        }
    }

    _spawnDaemon(command) {
        try {
            GLib.spawn_command_line_async(`${this._quote(command)} daemon`);
        } catch (e) {
            logError(e, 'PowerTools: gagal menyalakan layanan');
        }
    }

    _command() {
        if (this._settings) {
            const configured = this._settings.get_string('app-command');
            if (configured)
                return configured;
        }
        const fallback = GLib.build_filenamev([
            GLib.get_user_data_dir(), 'powertools', 'bin', 'powertools',
        ]);
        return GLib.file_test(fallback, GLib.FileTest.IS_EXECUTABLE) ? fallback : null;
    }

    _focusAppWindow(action = '') {
        if (this._focusSource) {
            GLib.source_remove(this._focusSource);
            this._focusSource = 0;
        }
        this._focusTries = 0;
        this._focusSource = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT,
            FOCUS_POLL_MS,
            () => {
                const window = this._focusTarget(action);
                if (window) {
                    this._activateWindow(window);
                    if (this._windowHasFocus(window)) {
                        this._focusSource = 0;
                        return GLib.SOURCE_REMOVE;
                    }
                    // Window GTK baru bisa mendapat fokus satu frame kemudian;
                    // ulangi sebentar sebelum menyerah.
                    if (++this._focusTries > FOCUS_CONFIRM_TRIES) {
                        this._focusSource = 0;
                        return GLib.SOURCE_REMOVE;
                    }
                    return GLib.SOURCE_CONTINUE;
                }
                if (++this._focusTries > FOCUS_POLL_TRIES) {
                    this._focusSource = 0;
                    return GLib.SOURCE_REMOVE;
                }
                return GLib.SOURCE_CONTINUE;
            });
    }

    _focusTarget(action) {
        if (action === 'lyrics') {
            const window = this._findLyricsWindow();
            if (!window)
                return null;
            try {
                return window.showing_on_its_workspace() ? window : null;
            } catch (e) {
                return null;
            }
        }
        // Jendela lirik tidak boleh "mencuri" fokus dari popup clipboard atau
        // launcher yang baru dibuka; ia hanya difokuskan oleh hotkey-nya sendiri.
        return this._findAppWindow(true);
    }

    _windowHasFocus(window) {
        return global.display.get_focus_window() === window;
    }

    _activateWindow(window) {
        try {
            Main.activateWindow(window);
        } catch (e) {
            try {
                window.activate(global.get_current_time());
            } catch (e2) {
                logError(e2);
            }
        }
    }

    _appHasFocus() {
        const focus = global.display.get_focus_window();
        return focus !== null && this._isAppWindow(focus);
    }

    _isAppWindow(window) {
        const title = window.get_title() ?? '';
        if (title.startsWith('PowerTools'))
            return true;
        let wmClass = '';
        try {
            wmClass = `${window.get_wm_class() ?? ''} ${window.get_wm_class_instance() ?? ''}`;
        } catch (e) {
            wmClass = '';
        }
        return wmClass.toLowerCase().includes('powertools');
    }

    _findAppWindow(skipLyrics = false) {
        for (const actor of global.get_window_actors()) {
            const window = actor.meta_window;
            if (!window)
                continue;
            if (!this._isAppWindow(window))
                continue;
            // Lewati jendela PowerTools yang sedang tersembunyi/minimized.
            if (!window.showing_on_its_workspace())
                continue;
            if (skipLyrics) {
                try {
                    if ((window.get_title() ?? '') === LYRICS_TITLE)
                        continue;
                } catch (e) {
                    // Judul tidak terbaca; anggap jendela biasa.
                }
            }
            return window;
        }
        return null;
    }

    // --------------------------------------------------------------- auto-paste
    _onFocusWindowChanged() {
        const focus = global.display.get_focus_window();
        const now = GLib.get_monotonic_time() / 1000;

        if (focus !== null && this._isAppWindow(focus)) {
            this._lastAppFocusMs = now;
            this._appWasFocused = true;
            return;
        }

        // Popup baru saja kehilangan fokus: mulai jendela waktu auto-paste
        // dari titik ini, bukan dari saat popup pertama kali dibuka.
        if (this._appWasFocused) {
            this._lastAppFocusMs = now;
            this._appWasFocused = false;
        }

        // Simpan jendela luar terakhir; popup PowerTools tidak menimpanya.
        if (focus !== null)
            this._previousFocus = focus;
    }

    // Dipanggil aplikasi Python tepat setelah item clipboard dipilih.
    // Kembalian hanya menandakan permintaan diterima, bukan paste selesai.
    Paste() {
        if (!this._active)
            return false;

        const target = this._previousFocus;
        if (!target)
            return false;
        if (this._isAppWindow(target))
            return false;
        try {
            if (!target.showing_on_its_workspace())
                return false;
        } catch (e) {
            return false;
        }

        // Jangan biarkan aplikasi lain memicu paste kapan saja lewat D-Bus:
        // hanya boleh saat popup masih fokus atau baru saja kehilangan fokus.
        const now = GLib.get_monotonic_time() / 1000;
        const appFocus = this._appHasFocus();
        if (!appFocus && !this._appWasFocused &&
            now - this._lastAppFocusMs > PASTE_GRACE_MS)
            return false;

        this._activateWindow(target);
        this._schedulePaste(target);
        return true;
    }

    _schedulePaste(target) {
        if (this._captureWatchdog) {
            GLib.source_remove(this._captureWatchdog);
            this._captureWatchdog = 0;
        }
        this._busy = false;
        this._pendingCapture = false;

        if (this._pasteSource) {
            GLib.source_remove(this._pasteSource);
            this._pasteSource = 0;
        }
        this._pasteTarget = target;
        this._pasteTries = 0;
        this._pasteSource = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT,
            PASTE_POLL_MS,
            () => {
                const focus = global.display.get_focus_window();
                if (focus !== null && focus === this._pasteTarget) {
                    this._pasteSource = 0;
                    this._pasteTarget = null;
                    this._sendPaste(target);
                    return GLib.SOURCE_REMOVE;
                }
                if (++this._pasteTries > PASTE_POLL_TRIES) {
                    this._pasteSource = 0;
                    this._pasteTarget = null;
                    return GLib.SOURCE_REMOVE;
                }
                if (this._pasteTries % PASTE_REACTIVATE_EVERY === 0) {
                    try {
                        this._activateWindow(target);
                    } catch (e) {
                        logError(e);
                    }
                }
                return GLib.SOURCE_CONTINUE;
            });
    }

    _sendPaste(target) {
        const device = this._virtualKeyboard();
        if (!device)
            return false;
        const shift = this._isTerminalWindow(target);
        // CURRENT_TIME membuat Mutter mengisi stempel waktu mikrodetik sendiri
        // saat event diproses (API virtual input tidak memakai milidetik).
        const time = Clutter.CURRENT_TIME;
        try {
            if (shift)
                device.notify_keyval(time, Clutter.KEY_Shift_L, Clutter.KeyState.PRESSED);
            device.notify_keyval(time, Clutter.KEY_Control_L, Clutter.KeyState.PRESSED);
            device.notify_keyval(time, Clutter.KEY_v, Clutter.KeyState.PRESSED);
            device.notify_keyval(time, Clutter.KEY_v, Clutter.KeyState.RELEASED);
            device.notify_keyval(time, Clutter.KEY_Control_L, Clutter.KeyState.RELEASED);
            if (shift)
                device.notify_keyval(time, Clutter.KEY_Shift_L, Clutter.KeyState.RELEASED);
            return true;
        } catch (e) {
            logError(e, 'PowerTools: gagal mengirim Ctrl+V');
            this._pasteDevice = null;
            return false;
        }
    }

    _virtualKeyboard() {
        if (this._pasteDevice)
            return this._pasteDevice;
        try {
            const backend = Clutter.get_default_backend();
            const seat = backend.get_default_seat();
            this._pasteDevice = seat.create_virtual_device(
                Clutter.InputDeviceType.KEYBOARD_DEVICE);
        } catch (e) {
            logError(e, 'PowerTools: keyboard virtual tidak tersedia');
            this._pasteDevice = null;
        }
        return this._pasteDevice;
    }

    _isTerminalWindow(window) {
        let text = '';
        try {
            text = `${window.get_wm_class() ?? ''} ${window.get_wm_class_instance() ?? ''}`;
        } catch (e) {
            text = '';
        }
        text = text.toLowerCase();
        return TERMINAL_CLASSES.some(name => text.includes(name));
    }

    // -------------------------------------------------------------- clipboard
    // Transfer clipboard bisa menggantung tanpa pernah memanggil callback
    // (pemilik selection hilang, klien Wayland berhenti menjawab, dsb).
    // Setiap transfer karena itu punya pengawal waktu; kalau lewat batas,
    // status dibersihkan supaya copy berikutnya tidak ikut diabaikan.
    _captureBegin() {
        this._busy = true;
        this._busySince = GLib.get_monotonic_time() / 1000;
        this._captureGen += 1;
        const gen = this._captureGen;
        if (this._captureWatchdog)
            GLib.source_remove(this._captureWatchdog);
        this._captureWatchdog = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT,
            CAPTURE_STALL_MS,
            () => {
                this._captureWatchdog = 0;
                if (!this._busy || this._captureGen !== gen)
                    return GLib.SOURCE_REMOVE;
                console.log('[powertools-clipboard] transfer macet, '
                    + 'siap menerima copy berikutnya');
                this._busy = false;
                this._busySince = 0;
                this._captureTries = 0;
                this._capturePending();
                return GLib.SOURCE_REMOVE;
            });
        return gen;
    }

    _captureEnd(gen) {
        // Callback dari transfer lama (sudah dianggap macet) tidak boleh
        // mengganggu transfer yang sedang berjalan sekarang.
        if (gen !== undefined && gen !== this._captureGen)
            return false;
        this._busy = false;
        this._busySince = 0;
        if (this._captureWatchdog) {
            GLib.source_remove(this._captureWatchdog);
            this._captureWatchdog = 0;
        }
        return true;
    }

    // PowerTools sendiri yang menaruh isi clipboard saat menempel/menyalin.
    // Perubahan itu tidak perlu ditangkap ulang (isinya sudah tercatat) dan
    // transfer dari selection milik shell sendiri justru rawan menggantung.
    _markSelfChange() {
        this._selfChanges += 1;
        this._selfChangeAt = GLib.get_monotonic_time() / 1000;
    }

    _consumeSelfChange() {
        if (this._selfChanges <= 0)
            return false;
        const now = GLib.get_monotonic_time() / 1000;
        if (now - this._selfChangeAt > SELF_CHANGE_WINDOW_MS) {
            this._selfChanges = 0;
            return false;
        }
        this._selfChanges -= 1;
        return true;
    }

    _onOwnerChanged(type) {
        if (type !== Meta.SelectionType.SELECTION_CLIPBOARD)
            return;
        if (!this._moduleEnabled('clipboard'))
            return;
        if (this._consumeSelfChange())
            return;
        if (this._busy) {
            const now = GLib.get_monotonic_time() / 1000;
            if (this._busySince && now - this._busySince > CAPTURE_STALL_MS) {
                // Transfer sebelumnya macet: jangan buang copy yang baru.
                this._captureEnd();
            } else {
                this._pendingCapture = true;
                return;
            }
        }
        const gen = this._captureBegin();
        this._captureTries = 0;
        GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            if (this._captureGen === gen && this._busy)
                this._tryCapture(gen);
            return GLib.SOURCE_REMOVE;
        });
    }

    _tryCapture(gen) {
        if (!this._selection) {
            this._captureEnd(gen);
            return;
        }

        let mimetypes = [];
        try {
            mimetypes = this._selection.get_mimetypes(
                Meta.SelectionType.SELECTION_CLIPBOARD) ?? [];
        } catch (e) {
            mimetypes = [];
        }

        const textMime = TEXT_MIMETYPES.find(type => mimetypes.includes(type));
        const imageMime = textMime ? null : IMAGE_MIMETYPES.find(type => mimetypes.includes(type));

        if (!textMime && !imageMime) {
            // Clipboard manager lain kadang belum mendaftarkan isinya saat
            // sinyal owner-changed tiba. Coba lagi beberapa kali (~210 ms)
            // sebelum menyerah, supaya copy tidak hilang.
            if (this._captureTries < CAPTURE_RETRY_TRIES) {
                this._captureTries++;
                GLib.timeout_add(GLib.PRIORITY_DEFAULT, CAPTURE_RETRY_MS, () => {
                    if (!this._busy || !this._selection ||
                        this._captureGen !== gen)
                        return GLib.SOURCE_REMOVE;
                    this._tryCapture(gen);
                    return GLib.SOURCE_REMOVE;
                });
                return;
            }
            this._captureEnd(gen);
            this._capturePending();
            return;
        }

        // Teks diutamakan kalau ada: banyak aplikasi (LibreOffice Calc,
        // browser) mengirim teks + gambar sekaligus untuk satu copy.
        if (imageMime) {
            this._transferImage(imageMime, gen);
            return;
        }
        this._transferText(textMime, gen);
    }

    _transferText(mimetype, gen) {
        const stream = Gio.MemoryOutputStream.new_resizable();
        try {
            this._selection.transfer_async(
                Meta.SelectionType.SELECTION_CLIPBOARD,
                mimetype,
                -1,
                stream,
                null,
                (_selection, result) => {
                    let text = '';
                    try {
                        _selection.transfer_finish(result);
                        const bytes = stream.steal_as_bytes();
                        text = new TextDecoder('utf-8').decode(bytes.get_data());
                    } catch (e) {
                        if (!(e instanceof Gio.IOErrorEnum))
                            logError(e);
                    }
                    if (!this._captureEnd(gen))
                        return;
                    if (text)
                        this._store(text);
                    this._capturePending();
                });
        } catch (e) {
            this._captureEnd(gen);
            logError(e);
            this._capturePending();
        }
    }

    _transferImage(mimetype, gen) {
        const stream = Gio.MemoryOutputStream.new_resizable();
        try {
            this._selection.transfer_async(
                Meta.SelectionType.SELECTION_CLIPBOARD,
                mimetype,
                -1,
                stream,
                null,
                (_selection, result) => {
                    let bytes = null;
                    try {
                        _selection.transfer_finish(result);
                        bytes = stream.steal_as_bytes();
                    } catch (e) {
                        if (!(e instanceof Gio.IOErrorEnum))
                            logError(e);
                    }
                    if (!this._captureEnd(gen))
                        return;
                    if (bytes)
                        this._storeImage(bytes, mimetype);
                    this._capturePending();
                });
        } catch (e) {
            this._captureEnd(gen);
            logError(e);
            this._capturePending();
        }
    }

    _storeImage(bytes, mimetype) {
        const size = bytes.get_size();
        if (size <= 0 || size > MAX_IMAGE_BYTES)
            return;

        let checksum = '';
        try {
            checksum = GLib.compute_checksum_for_bytes(
                GLib.ChecksumType.SHA1, bytes);
        } catch (e) {
            logError(e, 'PowerTools: gagal menghitung hash gambar');
            return;
        }
        if (!checksum || checksum === this._lastImage)
            return;

        try {
            GLib.mkdir_with_parents(this._imagesDir, 0o700);
            const file = Gio.File.new_for_path(
                GLib.build_filenamev([this._imagesDir, checksum]));
            file.replace_contents(
                bytes.get_data(),
                null,
                false,
                Gio.FileCreateFlags.PRIVATE |
                    Gio.FileCreateFlags.REPLACE_DESTINATION,
                null);
        } catch (e) {
            logError(e, 'PowerTools: gagal menyimpan gambar clipboard');
            return;
        }

        this._lastImage = checksum;
        this._appendRecord({
            i: checksum,
            m: mimetype,
            n: size,
            ts: Date.now() / 1000.0,
        });
    }

    _capturePending() {
        if (!this._pendingCapture)
            return;
        this._pendingCapture = false;
        this._onOwnerChanged(Meta.SelectionType.SELECTION_CLIPBOARD);
    }

    _store(text) {
        if (!text || text.length > MAX_TEXT_LENGTH)
            return;
        if (text.trim().length === 0)
            return;
        if (text === this._lastText)
            return;

        this._lastText = text;
        this._appendRecord({t: text, ts: Date.now() / 1000.0});
    }

    _appendRecord(record) {
        try {
            GLib.mkdir_with_parents(this._spoolDir, 0o700);
            const file = Gio.File.new_for_path(this._spoolPath);
            const output = file.append_to(Gio.FileCreateFlags.PRIVATE, null);
            const data = new TextEncoder().encode(`${JSON.stringify(record)}\n`);
            output.write_all(data, null);
            output.close(null);
        } catch (e) {
            logError(e, 'PowerTools: gagal menyimpan clipboard');
            return;
        }

        if (this._dbus) {
            try {
                this._dbus.emit_signal('Changed', null);
            } catch (e) {
                logError(e);
            }
        }
        this._maybeShrinkSpool();
    }

    _maybeShrinkSpool() {
        try {
            const file = Gio.File.new_for_path(this._spoolPath);
            const info = file.query_info(
                'standard::size', Gio.FileQueryInfoFlags.NONE, null);
            if (info.get_size() < SPOOL_MAX_BYTES)
                return;
            const [, contents] = file.load_contents(null);
            const lines = new TextDecoder('utf-8')
                .decode(contents)
                .split('\n')
                .filter(line => line.trim().length > 0);
            GLib.file_set_contents(
                this._spoolPath, `${lines.slice(-SPOOL_KEEP_LINES).join('\n')}\n`);
        } catch (e) {
            logError(e, 'PowerTools: gagal memangkas spool');
        }
    }

    _readLastText() {
        try {
            const file = Gio.File.new_for_path(this._spoolPath);
            const info = file.query_info(
                'standard::size', Gio.FileQueryInfoFlags.NONE, null);
            const size = info.get_size();
            if (size === 0)
                return;
            const stream = file.read(null);
            const chunk = 64 * 1024;
            if (size > chunk)
                stream.seek(size - chunk, GLib.SeekType.SET, null);
            const bytes = stream.read_bytes(chunk, null);
            stream.close(null);
            const lines = new TextDecoder('utf-8')
                .decode(bytes.get_data())
                .split('\n')
                .filter(line => line.trim().length > 0);
            const last = JSON.parse(lines[lines.length - 1]);
            if (last && typeof last.t === 'string')
                this._lastText = last.t;
            else if (last && typeof last.i === 'string')
                this._lastImage = last.i;
        } catch (e) {
            this._lastText = '';
        }
    }

    // ------------------------------------------------- clipboard milik shell
    // Di Wayland, Mutter menolak `set_selection` dari klien yang tidak sedang
    // memegang fokus keyboard. Popup clipboard kehilangan fokus tepat saat
    // ditutup, jadi permintaan clipboard dari aplikasi Python sering ditolak
    // dan Ctrl+V menempelkan item LAMA. Shell adalah pemilik selection itu
    // sendiri, sehingga menaruh clipboard dari sini selalu diterima tanpa
    // bergantung pada jendela mana yang sedang fokus.
    SetClipboardText(text) {
        if (typeof text !== 'string' || text.length === 0)
            return false;
        try {
            St.Clipboard.get_default().set_text(
                St.ClipboardType.CLIPBOARD, text);
        } catch (e) {
            logError(e, 'PowerTools: gagal menaruh teks di clipboard');
            return false;
        }
        // Tandai sudah tercatat supaya pemantau clipboard tidak menambah
        // salinan baru untuk teks yang sama.
        this._lastText = text;
        this._markSelfChange();
        return true;
    }

    SetClipboardImage(path, mime) {
        if (typeof path !== 'string' || path.length === 0)
            return false;
        let bytes = null;
        try {
            const [, contents] = Gio.File.new_for_path(path).load_contents(null);
            bytes = GLib.Bytes.new(contents);
        } catch (e) {
            logError(e, 'PowerTools: gagal membaca gambar clipboard');
            return false;
        }
        try {
            St.Clipboard.get_default().set_content(
                St.ClipboardType.CLIPBOARD, mime || 'image/png', bytes);
        } catch (e) {
            logError(e, 'PowerTools: gagal menaruh gambar di clipboard');
            return false;
        }
        try {
            this._lastImage = GLib.compute_checksum_for_bytes(
                GLib.ChecksumType.SHA1, bytes);
        } catch (e) {
            this._lastImage = '';
        }
        this._markSelfChange();
        return true;
    }

    // ---------------------------------------------------------- jendela lirik
    // Jendela lirik adalah jendela GTK biasa, jadi satu-satunya pihak yang
    // bisa menaikkannya di atas jendela lain (Wayland) adalah shell. Aplikasi
    // memanggil metode ini setiap kali tombol pin ditekan atau jendela dibuka.
    _findLyricsWindow() {
        for (const actor of global.get_window_actors()) {
            const window = actor.meta_window;
            if (window && this._windowTitle(window) === LYRICS_TITLE)
                return window;
        }
        return null;
    }

    _windowTitle(window) {
        try {
            return window.get_title() ?? '';
        } catch (e) {
            return '';
        }
    }

    SetLyricsAbove(above) {
        if (!this._active)
            return false;
        const window = this._findLyricsWindow();
        if (!window)
            return false;
        try {
            if (above)
                window.make_above();
            else
                window.unmake_above();
        } catch (e) {
            logError(e, 'PowerTools: gagal mengubah lapisan jendela lirik');
            return false;
        }
        return true;
    }

    PlaceLyricsWindow() {
        if (!this._active)
            return false;
        const window = this._findLyricsWindow();
        if (!window)
            return false;

        let x = 0;
        let y = 0;
        try {
            const area = window.get_work_area_current_monitor();
            const rect = window.get_frame_rect();
            x = area.x + area.width - rect.width - LYRICS_MARGIN;
            y = area.y + area.height - rect.height - LYRICS_MARGIN;
        } catch (e) {
            return false;
        }

        // Signature move_frame berbeda antar versi GNOME: yang lama memakai
        // `user_op` di argumen pertama. Coba keduanya supaya tidak pecah.
        try {
            window.move_frame(true, x, y);
        } catch (e) {
            try {
                window.move_frame(x, y);
            } catch (e2) {
                logError(e2, 'PowerTools: gagal menaruh jendela lirik');
                return false;
            }
        }
        return true;
    }

    // --------------------------------------------------- lirik & taskbar
    // Di Wayland, `skip-taskbar` milik Meta.Window hanya bisa dibaca: Mutter
    // mengisinya dari hint X11 dan tidak menyediakan setter. Karena taskbar,
    // Alt-Tab, dan overview semuanya membaca properti itu dari JavaScript,
    // nilainya ditimpa pada objek JS jendela lirik. Inti Mutter tetap memakai
    // getter aslinya, jadi jendela tetap bisa difokuskan dan di-pin.
    _readLyricsTaskbarHidden() {
        let hidden = true;
        const data = this._readConfig();
        const lyrics = data && typeof data.lyrics === 'object' ? data.lyrics : {};
        if (typeof lyrics.hide_from_taskbar === 'boolean')
            hidden = lyrics.hide_from_taskbar;
        return hidden;
    }

    _watchWindows() {
        if (this._windowCreatedId)
            return;
        this._windowCreatedId = global.display.connect(
            'window-created',
            (_display, window) => this._onWindowCreated(window));
    }

    _onWindowCreated(window) {
        if (!window)
            return;
        const apply = () => {
            if (!this._active)
                return;
            if (this._windowTitle(window) === LYRICS_TITLE)
                this._applyLyricsTaskbar(window);
        };
        // Judul dikirim beberapa saat setelah jendela dibuat; pasang penanda
        // begitu judulnya cocok supaya ikon tidak sempat muncul di taskbar.
        apply();
        try {
            window.connect('notify::title', apply);
        } catch (e) {
            // Jendela bisa hilang sebelum handler terpasang.
        }
    }

    _syncLyricsTaskbar() {
        this._hideLyricsTaskbar = this._readLyricsTaskbarHidden();
        for (const actor of global.get_window_actors()) {
            const window = actor.meta_window;
            if (window && this._windowTitle(window) === LYRICS_TITLE)
                this._applyLyricsTaskbar(window);
        }
    }

    _applyLyricsTaskbar(window) {
        if (!this._active)
            return;
        if (!this._hideLyricsTaskbar) {
            try {
                delete window.skip_taskbar;
            } catch (e) {
                // Properti bukan milik kita; tidak ada yang perlu dilepas.
            }
            return;
        }
        try {
            Object.defineProperty(window, 'skip_taskbar', {
                configurable: true,
                enumerable: false,
                value: true,
                writable: true,
            });
        } catch (e) {
            logError(e, 'PowerTools: gagal menyembunyikan lirik dari taskbar');
        }
    }

    SetLyricsSkipTaskbar(skip) {
        if (!this._active)
            return false;
        const window = this._findLyricsWindow();
        if (!window)
            return false;
        this._hideLyricsTaskbar = Boolean(skip);
        this._applyLyricsTaskbar(window);
        return true;
    }

    // ------------------------------------------------------------ ikon panel
    // Ikon tray kecil di panel: satu-satunya pintu masuk saat jendela lirik
    // disembunyikan dari taskbar. Menu ini juga berguna kalau taskbar penuh.
    _createIndicator() {
        if (this._indicator || Main.sessionMode.isGreeter)
            return;

        // Kegagalan ikon panel tidak boleh mematikan jembatan clipboard.
        let indicator = null;
        try {
            indicator = new PanelMenu.Button(0.0, 'PowerTools', false);
            indicator.add_child(new St.Icon({
                icon_name: APP_ID,
                fallback_icon_name: 'edit-copy-symbolic',
                style_class: 'system-status-icon',
            }));

            const addItem = (label, action) => {
                const item = new PopupMenu.PopupMenuItem(label);
                item.connect('activate', () => this._trigger(action, true));
                indicator.menu.addMenuItem(item);
                return item;
            };

            addItem('Buka Lirik', 'lyrics');
            addItem('Riwayat Clipboard', 'clipboard');
            addItem('Launcher', 'launcher');
            addItem('Ambil Warna', 'color');
            indicator.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
            addItem('Pengaturan', 'settings');
            indicator.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
            addItem('Keluar PowerTools', 'quit');

            Main.panel.addToStatusArea('powertools', indicator);
            this._indicator = indicator;
        } catch (e) {
            logError(e, 'PowerTools: gagal menaruh ikon di panel');
            try {
                indicator?.destroy();
            } catch (e2) {
                // Indikator mungkin belum sempat terpasang.
            }
            this._indicator = null;
        }
    }

    // ---------------------------------------------------------------- D-Bus
    _exportDbus() {
        try {
            const node = Gio.DBusNodeInfo.new_for_xml(DBUS_XML);
            this._dbus = Gio.DBusExportedObject.wrapJSObject(node.interfaces[0], this);
            this._nameId = Gio.DBus.own_name(
                Gio.BusType.SESSION,
                DBUS_NAME,
                Gio.BusNameOwnerFlags.NONE,
                connection => {
                    try {
                        this._dbus.export(connection, DBUS_PATH);
                    } catch (e) {
                        logError(e);
                    }
                },
                null,
                () => {});
        } catch (e) {
            logError(e);
        }
    }

    GetText() {
        return this._lastText;
    }
}