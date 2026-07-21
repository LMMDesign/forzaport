"""Zip-backed GAME:\\ path resolution for Xbox / MS Store Media installs (FH5, FH6, …).

Per-car archives live under media/cars/. Shared Materials/Textures zips live under
media/_library/ (GAME:\\Media\\_library\\…) and under media/cars/_library/ for
car-pack content (GAME:\\Media\\cars\\_library\\…). Those prefixes must not be
cross-aliased: cars Textures.zip must not answer Media\\_library\\textures lookups.
"""

from __future__ import annotations

import os
import zipfile

from .disk_cache import ZIPFS_MAX_BYTES, touch_file, trim_dir, zipfs_cache_dir


def _norm(p: str) -> str:
    return p.replace("/", "\\").lower()


def _game_rest(path: str) -> str | None:
    if len(path) >= 5 and path[:5].lower() == "game:":
        return path[5:].lstrip("\\/")
    return None


class ZipAssetStore:
    """Index of Forza Media zip archives for lazy resolve+cache."""

    def __init__(self, media_root: str, cache_dir: str | None = None):
        self.media_root = os.path.abspath(media_root)
        self.cache_dir = cache_dir or zipfs_cache_dir()
        self._zips: dict[str, zipfile.ZipFile] = {}
        # lower member path -> (zip_path, actual_member_name)
        self._index: dict[str, tuple[str, str]] = {}
        # Ambiguous canonical keys: first wins; later hits recorded for diagnostics.
        self._ambiguous: dict[str, list[tuple[str, str]]] = {}
        self._built = False
        self.stats: dict[str, int] = {
            "archives_indexed": 0,
            "members_indexed": 0,
            "lookups": 0,
            "hits": 0,
            "misses": 0,
            "extracts": 0,
            "cache_hits": 0,
            "ambiguous_keys": 0,
        }

    def close(self):
        for z in self._zips.values():
            try:
                z.close()
            except OSError:
                pass
        self._zips.clear()

    def cache_stats(self) -> dict[str, int]:
        """Snapshot of index/lookup counters (for diagnostics and tests)."""
        return dict(self.stats)

    def _open_zip(self, zip_path: str) -> zipfile.ZipFile | None:
        zip_path = os.path.abspath(zip_path)
        z = self._zips.get(zip_path)
        if z is not None:
            return z
        if not os.path.isfile(zip_path):
            return None
        try:
            z = zipfile.ZipFile(zip_path, "r")
        except zipfile.BadZipFile as exc:
            raise RuntimeError(f"corrupt or unreadable zip archive: {zip_path}") from exc
        self._zips[zip_path] = z
        self.stats["archives_indexed"] += 1
        return z

    def _add_members(
        self,
        zip_path: str,
        prefixes: tuple[str, ...],
        strip_stem: str | None = None,
    ):
        """Index zip members under each GAME: prefix.

        Xbox car zips are usually flat (``Scene/...`` and ``Name.carbin`` at root).
        Some archives wrap contents in a folder named after the car
        (``Name/Scene/...``). Only that matching stem is stripped -- never
        arbitrary folders like ``Scene``, which would break modelbin lookups.

        Path traversal members (``..``) are skipped. Duplicate canonical keys keep
        the first registration; later sources are recorded as ambiguous.
        """
        z = self._open_zip(zip_path)
        if z is None:
            return
        stem_low = strip_stem.lower() if strip_stem else None
        for name in z.namelist():
            if name.endswith("/"):
                continue
            n = name.replace("\\", "/").lstrip("/")
            rel = n
            low = n.lower()
            for strip in ("content/media/cars/", "media/cars/"):
                if low.startswith(strip):
                    rel = n[len(strip) :]
                    low = rel.lower()
                    break
            parts = rel.split("/")
            if (
                stem_low
                and len(parts) >= 2
                and parts[0].lower() == stem_low
            ):
                rel = "/".join(parts[1:])
                parts = rel.split("/")
            if any(p == ".." for p in parts):
                continue
            rel_os = rel.replace("/", "\\")
            for prefix in prefixes:
                key = _norm(prefix + rel_os)
                entry = (zip_path, name)
                if key not in self._index:
                    self._index[key] = entry
                    self.stats["members_indexed"] += 1
                elif self._index[key] != entry:
                    bucket = self._ambiguous.setdefault(key, [self._index[key]])
                    if entry not in bucket:
                        bucket.append(entry)
                        self.stats["ambiguous_keys"] = len(self._ambiguous)

    def _index_shaderbin_aliases(self, zip_path: str) -> None:
        """Index ``*.shaderbin`` members under ``media\\…\\shaders\\{stem}\\…``."""
        z = self._open_zip(zip_path)
        if z is None:
            return
        for name in z.namelist():
            if name.endswith("/"):
                continue
            base = os.path.basename(name.replace("\\", "/"))
            if not base.lower().endswith(".shaderbin"):
                continue
            stem = base[: -len(".shaderbin")]
            rel = f"{stem}\\{base}"
            for prefix in (
                "media\\cars\\_library\\shaders\\",
                "media\\_library\\shaders\\",
            ):
                key = _norm(prefix + rel)
                entry = (zip_path, name)
                if key not in self._index:
                    self._index[key] = entry
                    self.stats["members_indexed"] += 1

    def _index_shared_library(self, media: str) -> None:
        """Index ``media/_library/{Textures,Materials}.zip`` under Media\\_library\\ only."""
        shared = os.path.join(media, "_library")
        if not os.path.isdir(shared):
            return
        try:
            entries = os.listdir(shared)
        except OSError:
            return
        for entry in entries:
            low = entry.lower()
            if not low.endswith(".zip"):
                continue
            zip_path = os.path.join(shared, entry)
            if low == "textures.zip" or low.startswith("textures_"):
                self._add_members(zip_path, ("media\\_library\\textures\\",))
            elif low == "materials.zip" or low.startswith("materials_"):
                self._add_members(zip_path, ("media\\_library\\materials\\",))

    def register_car_zip(self, zip_path: str, media_name: str | None = None) -> bool:
        """Index one car archive so GAME:\\Media\\Cars\\Name\\... resolves from it."""
        zip_path = os.path.abspath(zip_path)
        if not os.path.isfile(zip_path) or not zip_path.lower().endswith(".zip"):
            return False
        stem = media_name or os.path.splitext(os.path.basename(zip_path))[0]
        self.build()
        self._add_members(zip_path, (f"media\\cars\\{stem}\\",), strip_stem=stem)
        return True

    def build(self):
        if self._built:
            return
        self._built = True
        media = self.media_root
        cars = None
        for name in ("cars", "Cars"):
            cand = os.path.join(media, name)
            if os.path.isdir(cand):
                cars = cand
                break

        # Shared library first: GAME:\Media\_library\textures\… binds to media/_library.
        self._index_shared_library(media)

        if cars is None:
            return

        try:
            for entry in os.listdir(cars):
                if not entry.lower().endswith(".zip"):
                    continue
                stem = entry[:-4]
                zip_path = os.path.join(cars, entry)
                self._add_members(
                    zip_path, (f"media\\cars\\{stem}\\",), strip_stem=stem
                )
        except OSError:
            pass

        lib = os.path.join(cars, "_library")
        if os.path.isdir(lib):
            # Cars-library archives bind ONLY under media\cars\_library\…
            # (never media\_library\… -- that poisoned shared-swatch resolution).
            try:
                for entry in os.listdir(lib):
                    low = entry.lower()
                    if not low.endswith(".zip"):
                        continue
                    zip_path = os.path.join(lib, entry)
                    if low == "materials.zip" or low.startswith("materials_"):
                        self._add_members(
                            zip_path,
                            ("media\\cars\\_library\\materials\\",),
                        )
                    elif low == "textures.zip" or low.startswith("textures_"):
                        self._add_members(
                            zip_path,
                            ("media\\cars\\_library\\textures\\",),
                        )
            except OSError:
                pass
            for shaders_name in ("shaders", "Shaders"):
                shaders = os.path.join(lib, shaders_name)
                if not os.path.isdir(shaders):
                    continue
                try:
                    for entry in os.listdir(shaders):
                        low = entry.lower()
                        if not low.endswith(".zip"):
                            continue
                        zip_path = os.path.join(shaders, entry)
                        if low == "shaders.zip":
                            self._add_members(
                                zip_path,
                                (
                                    "media\\cars\\_library\\shaders\\",
                                    "media\\_library\\shaders\\",
                                ),
                            )
                            self._index_shaderbin_aliases(zip_path)
                            continue
                        stem = entry[:-4]
                        self._add_members(
                            zip_path,
                            (f"media\\cars\\_library\\shaders\\{stem}\\",),
                            strip_stem=stem,
                        )
                        self._index_shaderbin_aliases(zip_path)
                except OSError:
                    pass
            mono = os.path.join(lib, "Shaders.zip")
            if os.path.isfile(mono):
                self._add_members(
                    mono,
                    (
                        "media\\cars\\_library\\shaders\\",
                        "media\\_library\\shaders\\",
                    ),
                )
                self._index_shaderbin_aliases(mono)
            tires = os.path.join(lib, "scene", "tires")
            if os.path.isdir(tires):
                self.register_tires_dir(tires)

    def register_tires_dir(self, tires_dir: str) -> int:
        """Index ``tire_*.zip`` compounds from a shared library tires folder."""
        self.build()
        tires_dir = os.path.abspath(tires_dir)
        if not os.path.isdir(tires_dir):
            return 0
        count = 0
        try:
            entries = os.listdir(tires_dir)
        except OSError:
            return 0
        for entry in entries:
            low = entry.lower()
            if not low.endswith(".zip"):
                continue
            if not (low.startswith("tire_") or low.startswith("tirer_")):
                continue
            stem = entry[:-4]
            zip_path = os.path.join(tires_dir, entry)
            self._add_members(
                zip_path,
                (f"media\\cars\\_library\\scene\\tires\\{stem}\\",),
                strip_stem=stem,
            )
            count += 1
        return count

    def lookup_member(self, game_or_rest: str) -> tuple[str, str] | None:
        """Return (archive_path, member_name) without extracting."""
        self.build()
        rest = _game_rest(game_or_rest)
        if rest is None:
            rest = game_or_rest.lstrip("\\/")
        key = _norm(rest)
        hit = self._index.get(key)
        if hit is None and not key.startswith("media\\"):
            hit = self._index.get("media\\" + key)
        return hit

    def resolve_to_cache(self, game_or_rest: str) -> str | None:
        """Return a real filesystem path for the asset, extracting from zip if needed."""
        self.build()
        self.stats["lookups"] += 1
        rest = _game_rest(game_or_rest)
        if rest is None:
            rest = game_or_rest.lstrip("\\/")
        key = _norm(rest)
        hit = self._index.get(key)
        if hit is None and not key.startswith("media\\"):
            hit = self._index.get("media\\" + key)
        if hit is None:
            self.stats["misses"] += 1
            return None
        self.stats["hits"] += 1
        zip_path, member = hit
        out = os.path.join(self.cache_dir, key.replace("\\", os.sep))
        out_abs = os.path.abspath(out)
        cache_root = os.path.abspath(self.cache_dir)
        if not (
            out_abs == cache_root
            or out_abs.startswith(cache_root + os.sep)
        ):
            self.stats["misses"] += 1
            return None
        if os.path.isfile(out_abs) and os.path.getsize(out_abs) > 0:
            touch_file(out_abs)
            self.stats["cache_hits"] += 1
            return out_abs
        os.makedirs(os.path.dirname(out_abs), exist_ok=True)
        z = self._open_zip(zip_path)
        if z is None:
            self.stats["misses"] += 1
            return None
        tmp = out_abs + ".partial"
        try:
            with z.open(member) as src, open(tmp, "wb") as dst:
                dst.write(src.read())
            os.replace(tmp, out_abs)
        except Exception:
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            raise
        self.stats["extracts"] += 1
        trim_dir(self.cache_dir, ZIPFS_MAX_BYTES, protect={out_abs})
        return out_abs
