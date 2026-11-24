// SPDX-License-Identifier: MIT
//
// Penyusunan tab "dynamic island": satu tab per pemutar MPRIS, ditambah satu
// tab per jendela browser yang memutar atau membuka media.
//
// Kenapa tab jendela perlu ada: Chromium/Brave hanya mengekspos SATU pemutar
// MPRIS per proses, jadi tab YouTube dan aplikasi web YouTube Music di peramban
// yang sama berbagi satu pemutar — metadata yang terbaca hanya milik sesi yang
// dianggap aktif. Satu-satunya pembeda yang tersedia adalah jendela: judul
// jendela bisa dibaca dari shell, dan aplikasi web punya kelas jendela sendiri.
//
// Tab jendela hanya membawa judul. MPRIS tidak melaporkan posisi atau kontrol
// untuk sesi yang tidak dieksposnya, dan menebaknya akan menyesatkan.
//
// Modul ini murni (tanpa gi://) supaya bisa diuji dengan `gjs -m` di luar shell.

//: Akhiran nama bus MPRIS: `…brave.instance10804` → PID 10804.
const INSTANCE = /\.instance(\d+)$/i;

//: Kelas jendela aplikasi web. Id ekstensi Chromium selalu 32 huruf a–p.
const WEB_APP_CLASSES = [
    /^_?crx_[a-p]{32}$/i,
    // brave-cinhimbnkkaeohfgghhklpknlkffjgod-Default
    /^[A-Za-z0-9-]+?-[a-p]{32}(?:-[A-Za-z0-9 _.-]+)?$/i,
    /^[a-p]{32}$/i,
    // Firefox PWA (proyek firefoxpwa).
    /^FFPWA-[A-Za-z0-9]+$/i,
];

//: Situs media yang layak muncul sebagai tab jendela, dari yang paling
//: spesifik: "youtube music" harus diperiksa sebelum "youtube".
export const SITE_HINTS = [
    'youtube music',
    'music.youtube',
    'youtube',
    'netflix',
    'spotify',
    'soundcloud',
    'twitch',
    'deezer',
    'tidal',
    'bandcamp',
    'vimeo',
];

//: Nama tampilan untuk situs di atas. Kunci situs tetap huruf kecil supaya
//: pencocokan judul tidak bergantung pada kapitalisasi.
const SITE_LABELS = {
    'youtube music': 'YouTube Music',
    'music.youtube': 'YouTube Music',
    'youtube': 'YouTube',
    'netflix': 'Netflix',
    'spotify': 'Spotify',
    'soundcloud': 'SoundCloud',
    'twitch': 'Twitch',
    'deezer': 'Deezer',
    'tidal': 'Tidal',
    'bandcamp': 'Bandcamp',
    'vimeo': 'Vimeo',
};

//: Akhiran yang ditambahkan Chromium/Firefox ke judul jendela.
const BROWSER_SUFFIX =
    /\s[-–—]\s*(?:brave|chromium|chrome|google chrome|firefox|mozilla firefox|vivaldi|opera|microsoft edge|edge)\s*$/i;

//: Judul lebih pendek dari ini tidak dipakai untuk mencocokkan jendela dengan
//: lagu: "Lagu" bisa ada di jendela mana saja.
const MIN_MATCH = 8;

//: Batas tab jendela supaya island tidak melebar tanpa kendali.
const MAX_WINDOW_TABS = 4;

export function pidFromBusName(name) {
    const match = INSTANCE.exec(String(name ?? '').trim());
    if (!match)
        return 0;
    const pid = Number(match[1]);
    return Number.isFinite(pid) ? pid : 0;
}

export function stripBrowserSuffix(title) {
    return String(title ?? '').replace(BROWSER_SUFFIX, '').trim();
}

//: Judul tab jendela: akhiran browser dan akhiran nama situs dibuang karena
//: tabnya sudah menyebut situs itu ("YouTube Music", "YouTube").
export function windowTitle(title) {
    const text = stripBrowserSuffix(title);
    const site = siteLabel(siteFromTitle(text));
    if (!site)
        return text;
    const suffix = ` - ${site}`;
    if (text.toLowerCase().endsWith(suffix.toLowerCase()))
        return text.slice(0, text.length - suffix.length).trim();
    return text;
}

//: Teks yang dipakai untuk mencocokkan jendela dengan lagu: akhiran browser
//: dan situs dibuang, begitu juga penanda yang ditambahkan browser seperti
//: jumlah notifikasi "(529) " dan elipsis potongan "…" di ujung judul.
function coreTitle(title) {
    return normalizeText(windowTitle(title))
        .replace(/^\(\d+\)\s*/, '')
        .replace(/[\s|·•….,-]+$/, '')
        .trim();
}

export function normalizeText(text) {
    return stripBrowserSuffix(text).toLowerCase().replace(/\s+/g, ' ').trim();
}

export function isWebAppClass(wmClass) {
    const name = String(wmClass ?? '').trim();
    if (!name)
        return false;
    return WEB_APP_CLASSES.some(pattern => pattern.test(name));
}

export function siteFromTitle(title) {
    const text = normalizeText(title);
    if (!text)
        return '';
    for (const hint of SITE_HINTS) {
        if (text === hint || text.endsWith(` - ${hint}`) ||
            text.startsWith(`${hint} - `))
            return hint;
    }
    return '';
}

export function titlesMatch(left, right) {
    const a = coreTitle(left);
    const b = coreTitle(right);
    if (!a || !b)
        return false;
    if (a === b)
        return true;
    const shorter = a.length <= b.length ? a : b;
    const longer = shorter === a ? b : a;
    if (shorter.length < MIN_MATCH)
        return false;
    if (longer.includes(shorter))
        return true;

    // Judul tab Chromium dipotong dengan "…" ("(529) Red Alert 2 | …"), jadi
    // pencocokan harus tahan terhadap potongan: sebagian besar kata judul
    // pendek harus muncul di judul panjang.
    const words = shorter.split(' ').filter(word => word.length > 1);
    if (words.length < 2)
        return false;
    const hits = words.filter(word => longer.includes(word)).length;
    return hits / words.length >= 0.7;
}

export function siteLabel(site) {
    return SITE_LABELS[site] ?? '';
}

function isMediaWindow(window, playingTitles) {
    if (!window || !window.pid)
        return false;
    if (!String(window.title ?? '').trim())
        return false;
    if (isWebAppClass(window.wmClass))
        return true;
    if (siteFromTitle(window.title))
        return true;
    return playingTitles.some(title => titlesMatch(window.title, title));
}

function appLabel(window, appNameFor) {
    if (!window)
        return '';
    // Nama dari berkas .desktop hanya dipakai untuk aplikasi web. Berkas
    // .desktop browser biasa (`brave-browser.desktop`) juga punya nama
    // ("Brave Web Browser"), dan nama itu menyesatkan sebagai label tab.
    if (!isWebAppClass(window.wmClass))
        return siteLabel(siteFromTitle(window.title));
    let name = '';
    try {
        name = String(appNameFor?.(window.wmClass) ?? '').trim();
    } catch (e) {
        name = '';
    }
    return name || siteLabel(siteFromTitle(window.title));
}

/**
 * Susun daftar tab dari pemutar MPRIS dan jendela shell.
 *
 * @param {object} input
 * @param {Array} input.players pemutar dengan `owner`, `title`, `artist`,
 *     `playing`, `label` (nama pemutar), `lastChangeAt`, dan `pid` opsional.
 * @param {Array} input.windows jendela dengan `pid`, `wmClass`, `title`.
 * @param {Function} [input.appNameFor] kelas jendela → nama aplikasi web.
 * @returns {Array} tab berurutan: pemutar lebih dulu, lalu jendela.
 */
export function buildTabs({players = [], windows = [], appNameFor = null} = {}) {
    const byPid = new Map();
    for (const window of windows) {
        if (!window || !window.pid)
            continue;
        if (!byPid.has(window.pid))
            byPid.set(window.pid, []);
        byPid.get(window.pid).push(window);
    }

    // Urutan jendela dari shell mengikuti tumpukan (stacking), jadi berubah
    // setiap kali fokus berpindah. Kunci tab jendela memakai nomor urut, dan
    // urutan yang berubah berarti tab yang dipilih bisa menunjuk jendela lain.
    // Karena itu urutannya dipatok: kelas jendela lalu judul.
    for (const list of byPid.values()) {
        list.sort((a, b) => {
            const left = `${a.wmClass}\u0001${a.title}`;
            const right = `${b.wmClass}\u0001${b.title}`;
            return left < right ? -1 : (left > right ? 1 : 0);
        });
    }

    const playingTitles = players
        .filter(player => player.playing && player.title)
        .map(player => player.title);

    const tabs = [];
    const used = new Set();

    for (const player of players) {
        const pid = player.pid || pidFromBusName(player.owner);
        const mediaWindows = (byPid.get(pid) ?? [])
            .filter(window => isMediaWindow(window, playingTitles));

        // Jendela yang judulnya memuat judul lagu adalah jendela yang sedang
        // memutar. Kalau tidak ada yang cocok dan hanya ada satu jendela media,
        // jendela itu tetap dipakai sebagai sumber label: judul aplikasi web
        // sering hanya nama aplikasinya, jadi tidak akan pernah cocok.
        let matched = mediaWindows
            .find(window => titlesMatch(window.title, player.title)) ?? null;
        let labelWindow = matched;
        if (!matched && mediaWindows.length === 1)
            labelWindow = mediaWindows[0];
        if (labelWindow)
            used.add(labelWindow);

        tabs.push({
            key: `p:${player.owner}`,
            kind: 'player',
            player,
            window: matched,
            label: appLabel(labelWindow, appNameFor) ||
                String(player.label ?? '').trim() || 'Media',
            playing: Boolean(player.playing),
            title: player.title ?? '',
            lastChangeAt: player.lastChangeAt ?? 0,
        });
    }

    let extra = 0;
    const scanned = new Set();
    for (const player of players) {
        const pid = player.pid || pidFromBusName(player.owner);
        // Dua pemutar dari proses yang sama (jarang) tidak boleh membuat tab
        // jendela ganda.
        if (scanned.has(pid))
            continue;
        scanned.add(pid);
        const mediaWindows = (byPid.get(pid) ?? [])
            .filter(window => isMediaWindow(window, playingTitles));
        for (let index = 0; index < mediaWindows.length; index++) {
            const window = mediaWindows[index];
            if (used.has(window))
                continue;
            if (extra >= MAX_WINDOW_TABS)
                break;
            extra++;
            tabs.push({
                key: `w:${pid}:${index}`,
                kind: 'window',
                player: null,
                window,
                label: appLabel(window, appNameFor) || 'Jendela',
                playing: false,
                title: windowTitle(window.title) || window.title,
                // MPRIS tidak punya posisi/kontrol untuk sesi ini, jadi
                // keterangan itu ditulis apa adanya, bukan ditebak.
                detail: isWebAppClass(window.wmClass)
                    ? 'Aplikasi web · tanpa kontrol'
                    : 'Jendela browser · tanpa kontrol',
                lastChangeAt: 0,
            });
        }
    }

    return tabs;
}

/**
 * Pilih tab yang ditampilkan.
 *
 * Tab yang pernah diklik menempel: island tidak berpindah sendiri walau tab
 * lain mulai berbunyi. Kalau tab itu hilang (pemutar keluar, jendela ditutup),
 * pilihan jatuh ke tab yang sedang berbunyi, lalu tab pertama.
 */
export function resolveTab(tabs, selectedKey) {
    if (!Array.isArray(tabs) || tabs.length === 0)
        return null;
    if (selectedKey) {
        const pinned = tabs.find(tab => tab.key === selectedKey);
        if (pinned)
            return pinned;
    }
    const playing = tabs.filter(tab => tab.playing);
    if (playing.length > 0) {
        let best = playing[0];
        for (const tab of playing) {
            if ((tab.lastChangeAt ?? 0) > (best.lastChangeAt ?? 0))
                best = tab;
        }
        return best;
    }
    return tabs[0];
}
