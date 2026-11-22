"""Indeks aplikasi .desktop dan frekuensi pemakaian."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from gi.repository import Gio

from . import config
from .matcher import fuzzy_score


@dataclass(slots=True)
class AppEntry:
    app_id: str
    name: str
    generic: str
    comment: str
    keywords: str
    executable: str
    icon: Gio.Icon | None
    info: Gio.AppInfo
    haystack: str


def _clean(value: str | None) -> str:
    return value.strip() if value else ""


def load_apps() -> list[AppEntry]:
    entries: list[AppEntry] = []
    seen: set[str] = set()
    for info in Gio.AppInfo.get_all():
        try:
            if not isinstance(info, Gio.DesktopAppInfo):
                continue
            if not info.should_show() or info.get_nodisplay() or info.get_is_hidden():
                continue
            app_id = info.get_id() or ""
            if not app_id or app_id in seen:
                continue
        except Exception:
            continue

        seen.add(app_id)
        name = _clean(info.get_display_name()) or _clean(info.get_name())
        if not name:
            continue
        generic = ""
        comment = ""
        keywords = ""
        executable = ""
        try:
            generic = _clean(info.get_generic_name())
        except Exception:
            pass
        try:
            comment = _clean(info.get_description())
        except Exception:
            pass
        try:
            keywords = " ".join(info.get_keywords() or [])
        except Exception:
            pass
        try:
            executable = _clean(info.get_executable())
        except Exception:
            pass

        haystack = " ".join(filter(None, (name, generic, comment, keywords, executable))).lower()
        entries.append(
            AppEntry(
                app_id=app_id,
                name=name,
                generic=generic,
                comment=comment,
                keywords=keywords,
                executable=executable,
                icon=info.get_icon(),
                info=info,
                haystack=haystack,
            )
        )

    # Banyak paket (mis. LibreOffice) punya entri native + Flatpak dengan
    # nama yang sama. Tampilkan satu saja, utamakan yang native.
    deduped: dict[str, AppEntry] = {}
    for entry in entries:
        key = entry.name.lower()
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = entry
            continue
        if _via_flatpak(existing) and not _via_flatpak(entry):
            deduped[key] = entry
    entries = list(deduped.values())
    entries.sort(key=lambda e: e.name.lower())
    return entries


def _via_flatpak(entry: AppEntry) -> bool:
    executable = (entry.executable or "").lower()
    return "flatpak" in executable


class UsageStore:
    """Skor frekuensi + kebaruan ala PowerToys Run (frecency)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else config.USAGE_PATH
        self._data: dict[str, list[float]] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        for key, value in data.items():
            if isinstance(value, list) and len(value) >= 2:
                try:
                    self._data[str(key)] = [float(value[0]), float(value[1])]
                except (TypeError, ValueError):
                    continue

    def bump(self, app_id: str) -> None:
        now = time.time()
        count, _last = self._data.get(app_id, [0.0, 0.0])
        self._data[app_id] = [min(count + 1.0, 999.0), now]
        self._dirty = True
        # Simpan langsung; file-nya kecil dan bump hanya terjadi saat app dibuka.
        self.save()

    def bonus(self, app_id: str) -> float:
        entry = self._data.get(app_id)
        if not entry:
            return 0.0
        count, last = entry
        score = min(count, 20.0) * 0.35
        age = max(0.0, time.time() - last)
        if age < 3600:
            score += 6.0
        elif age < 86400:
            score += 4.0
        elif age < 7 * 86400:
            score += 2.0
        return score

    def recent(self, apps: Iterable[AppEntry], limit: int) -> list[AppEntry]:
        ranked = sorted(apps, key=lambda a: self.bonus(a.app_id), reverse=True)
        return [a for a in ranked if self.bonus(a.app_id) > 0][:limit]

    def save(self) -> bool:
        if not self._dirty:
            return False
        try:
            config.atomic_write_text(
                self.path, json.dumps(self._data, ensure_ascii=False) + "\n"
            )
            self._dirty = False
            return True
        except OSError:
            return False


def search_apps(
    apps: Iterable[AppEntry], query: str, usage: UsageStore, limit: int
) -> list[AppEntry]:
    scored: list[tuple[float, AppEntry]] = []
    for app in apps:
        name_score = fuzzy_score(query, app.name)
        bonus_source = max(
            fuzzy_score(query, app.generic) if app.generic else -1.0,
            fuzzy_score(query, app.keywords) if app.keywords else -1.0,
            fuzzy_score(query, app.executable) if app.executable else -1.0,
        )
        score = max(name_score, bonus_source * 0.6)
        if score < 0:
            continue
        score += usage.bonus(app.app_id)
        scored.append((score, app))
    if not scored:
        return []
    scored.sort(key=lambda item: item[0], reverse=True)
    # Buang hasil yang jauh di bawah kandidat terbaik supaya daftar tidak
    # dipenuhi kecocokan acak (mis. query "fire" memunculkan "Videos").
    cutoff = max(scored[0][0] * 0.45, 8.0)
    filtered = [item for item in scored if item[0] >= cutoff]
    return [app for _score, app in filtered[:limit]]