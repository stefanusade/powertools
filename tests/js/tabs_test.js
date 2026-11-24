// SPDX-License-Identifier: MIT
//
// Uji logika tab dynamic island tanpa GNOME Shell: `gjs -m tests/js/tabs_test.js`.
// Dipanggil dari tests/test_media_tabs.py supaya ikut suite Python.

import {
    buildTabs,
    isWebAppClass,
    pidFromBusName,
    resolveTab,
    siteFromTitle,
    stripBrowserSuffix,
    titlesMatch,
    windowTitle,
} from '../../gnome-extension/powertools@local/tabs.js';

let failures = 0;

function check(name, condition, detail = '') {
    if (condition)
        return;
    failures++;
    printerr(`GAGAL ${name}${detail ? `: ${detail}` : ''}`);
}

function equal(name, actual, expected) {
    check(name, JSON.stringify(actual) === JSON.stringify(expected),
        `${JSON.stringify(actual)} != ${JSON.stringify(expected)}`);
}

// ------------------------------------------------------------------- dasar
equal('pid dari nama bus', pidFromBusName(
    'org.mpris.MediaPlayer2.brave.instance111726'), 111726);
equal('pid tanpa instance', pidFromBusName('org.mpris.MediaPlayer2.spotify'), 0);

equal('akhiran browser dibuang',
    stripBrowserSuffix('Red Alert 2 | Yuri - Brave'), 'Red Alert 2 | Yuri');
equal('judul tanpa akhiran tetap',
    stripBrowserSuffix('Lampu Kota'), 'Lampu Kota');

check('kelas PWA Brave', isWebAppClass(
    'brave-cinhimbnkkaeohfgghhklpknlkffjgod-Default'));
check('kelas PWA profil', isWebAppClass('brave-cinhimbnkkaeohfgghhklpknlkffjgod-Profile 2'));
check('kelas PWA crx', isWebAppClass('crx_cinhimbnkkaeohfgghhklpknlkffjgod'));
check('kelas PWA Firefox', isWebAppClass('FFPWA-01JC5Z9Q4Q8K3M2N7P6R5T4V3W'));
check('kelas tab browser bukan PWA', !isWebAppClass('brave-browser'));
check('kelas kosong', !isWebAppClass(''));

equal('situs dari judul PWA', siteFromTitle('YouTube Music'), 'youtube music');
equal('situs dari tab YouTube',
    siteFromTitle("Red Alert 2 | Yuri's Revenge - YouTube"), 'youtube');
equal('situs dari tab Netflix', siteFromTitle('Stranger Things - Netflix'), 'netflix');
equal('situs tak dikenal', siteFromTitle('Dokumen tanpa judul'), '');

check('judul lagu cocok dengan judul jendela', titlesMatch(
    'Stand by Me', "Stand by Me - YouTube - Brave"));
// Judul pendek hanya cocok kalau memang sama persis, bukan sebagai potongan.
check('judul pendek tidak dicocokkan sebagai potongan',
    !titlesMatch('Lagu', 'Lagu Baru - YouTube'));
check('judul pendek yang sama persis tetap cocok',
    titlesMatch('Lagu', 'Lagu - YouTube'));
check('judul berbeda tidak cocok',
    !titlesMatch('Stand by Me', 'Lampu Kota - YouTube'));

// Judul tab Chromium dipotong dengan "…": judul lagu panjang tidak akan
// pernah termuat utuh di judul jendela.
check('judul lagu panjang cocok dengan judul tab terpotong', titlesMatch(
    "Red Alert 2 | Yuri's Revenge | 7 vs 1 | Extra hard AI | Random",
    '(529) Red Alert 2 | … - YouTube - Brave'));
check('judul lagu berbeda tetap ditolak', !titlesMatch(
    'Lampu Kota Senja Kecil', '(529) Red Alert 2 | … - YouTube - Brave'));

// --------------------------------------------------------- dua jendela Brave
const youtubePlayer = {
    owner: 'org.mpris.MediaPlayer2.brave.instance111726',
    label: 'Brave',
    title: "Red Alert 2 | Yuri's Revenge | 7 vs 1",
    artist: 'Red Alert 2',
    playing: true,
    lastChangeAt: 100,
};

const braveWindows = [
    {
        pid: 111726,
        wmClass: 'brave-cinhimbnkkaeohfgghhklpknlkffjgod-Default',
        title: 'YouTube Music',
    },
    {
        pid: 111726,
        wmClass: 'brave-browser',
        title: "Red Alert 2 | Yuri's Revenge | 7 vs 1 - YouTube - Brave",
    },
];

const appNames = new Map([
    ['brave-cinhimbnkkaeohfgghhklpknlkffjgod-Default', 'YouTube Music'],
]);
const appNameFor = wmClass => appNames.get(wmClass) ?? '';

const tabs = buildTabs({
    players: [youtubePlayer],
    windows: braveWindows,
    appNameFor,
});

equal('dua tab: pemutar + jendela lain', tabs.length, 2);
equal('tab pertama pemutar', tabs[0].kind, 'player');
equal('label tab pemutar dari situs jendela', tabs[0].label, 'YouTube');
check('tab pemutar sedang berbunyi', tabs[0].playing);
equal('tab kedua jendela PWA', tabs[1].kind, 'window');
equal('label tab jendela dari aplikasi web', tabs[1].label, 'YouTube Music');
check('tab jendela tidak diklaim berbunyi', !tabs[1].playing);
equal('judul tab jendela tanpa akhiran situs', tabs[1].title, 'YouTube Music');
check('tab jendela menyebut tanpa kontrol',
    tabs[1].detail.includes('tanpa kontrol'));

equal('judul jendela: akhiran situs dibuang', windowTitle(
    "Red Alert 2 | Yuri's Revenge - YouTube"), "Red Alert 2 | Yuri's Revenge");
equal('judul jendela: akhiran browser dan situs dibuang', windowTitle(
    "Red Alert 2 - YouTube - Brave"), 'Red Alert 2');
equal('judul jendela tanpa situs tetap', windowTitle('Dokumen rapat'), 'Dokumen rapat');

// Kunci tab jendela tidak boleh bergantung pada urutan jendela dari shell:
// urutan itu mengikuti tumpukan dan berubah setiap kali fokus berpindah.
const reordered = buildTabs({
    players: [youtubePlayer],
    windows: [...braveWindows].reverse(),
    appNameFor,
});
equal('urutan jendela tidak mengubah kunci tab',
    reordered.map(tab => tab.key), tabs.map(tab => tab.key));

// Jendela yang tidak menyangkut media tidak ikut jadi tab.
const withNoise = buildTabs({
    players: [youtubePlayer],
    windows: [...braveWindows, {
        pid: 111726,
        wmClass: 'brave-browser',
        title: 'Dokumen rapat - Brave',
    }],
    appNameFor,
});
equal('jendela non-media diabaikan', withNoise.length, 2);

// Berkas .desktop browser biasa juga punya nama ("Brave Web Browser"); nama
// itu tidak boleh dipakai sebagai label tab.
const browserNames = new Map([
    ...appNames,
    ['brave-browser', 'Brave Web Browser'],
]);
const withBrowserName = buildTabs({
    players: [youtubePlayer],
    windows: braveWindows,
    appNameFor: wmClass => browserNames.get(wmClass) ?? '',
});
equal('tab pemutar memakai situs, bukan nama browser',
    withBrowserName[0].label, 'YouTube');

// ------------------------------------------------------- hanya satu jendela
const single = buildTabs({
    players: [{
        owner: 'org.mpris.MediaPlayer2.brave.instance111726',
        label: 'Brave',
        title: 'Peron',
        artist: 'Senja Kecil',
        playing: true,
        lastChangeAt: 5,
    }],
    windows: [{
        pid: 111726,
        wmClass: 'brave-cinhimbnkkaeohfgghhklpknlkffjgod-Default',
        title: 'YouTube Music',
    }],
    appNameFor,
});
equal('satu jendela: tetap satu tab', single.length, 1);
equal('label dari aplikasi web walau judul beda', single[0].label, 'YouTube Music');

// Pemutar tanpa jendela (Spotify) tetap dapat tab sendiri.
const spotify = buildTabs({
    players: [{
        owner: 'org.mpris.MediaPlayer2.spotify',
        label: 'Spotify',
        title: 'Lampu Kota',
        artist: 'Senja Kecil',
        playing: true,
        lastChangeAt: 9,
    }],
    windows: braveWindows,
});
equal('pemutar non-browser tanpa tab jendela', spotify.length, 1);
equal('label pemutar non-browser', spotify[0].label, 'Spotify');

// ------------------------------------------------------------ pilih tab
const twoPlayers = buildTabs({
    players: [
        {owner: 'a', label: 'Spotify', title: 'Lampu Kota', playing: true, lastChangeAt: 3},
        {owner: 'b', label: 'VLC', title: 'Peron', playing: true, lastChangeAt: 9},
    ],
    windows: [],
});
equal('tanpa pilihan: yang terakhir berbunyi', resolveTab(twoPlayers, '').key, 'p:b');
equal('pilihan menempel', resolveTab(twoPlayers, 'p:a').key, 'p:a');
equal('pilihan hilang jatuh ke yang berbunyi',
    resolveTab(twoPlayers, 'p:hilang').key, 'p:b');
equal('tanpa tab', resolveTab([], 'p:a'), null);

const paused = buildTabs({
    players: [
        {owner: 'a', label: 'Spotify', title: 'Lampu Kota', playing: false, lastChangeAt: 1},
        {owner: 'b', label: 'VLC', title: 'Peron', playing: false, lastChangeAt: 2},
    ],
    windows: [],
});
equal('semua jeda: tab pertama', resolveTab(paused, '').key, 'p:a');

if (failures > 0) {
    printerr(`${failures} pemeriksaan gagal`);
    imports.system.exit(1);
}
print('tabs.js: semua pemeriksaan lolos');
