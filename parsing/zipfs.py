"""Zip-backed GAME:\\ path resolution for Xbox / MS Store Media installs (FH5, FH6, …).

Per-car archives and shared Materials/Textures/tire/shader zips live under media/cars/.
This module maps a GAME: path to an on-disk file or a cached extract from the owning zip,
so BinaryStream.from_path call sites keep working for both zip installs and extracted trees.
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
        self._built = False

    def close(self):
        for z in self._zips.values():
            try:
                z.close()
            except OSError:
                pass
        self._zips.clear()

    def _open_zip(self, zip_path: str) -> zipfile.ZipFile | None:
        zip_path = os.path.abspath(zip_path)
        z = self._zips.get(zip_path)
        if z is not None:
            return z
        if not os.path.isfile(zip_path):
            return None
        z = zipfile.ZipFile(zip_path, "r")
        self._zips[zip_path] = z
        return z

    def _add_members(self, zip_path: str, prefixes: tuple[str, ...]):
        z = self._open_zip(zip_path)
        if z is None:
            return
        for name in z.namelist():
            if name.endswith("/"):
                continue
            n = name.replace("\\", "/")
            for prefix in prefixes:
                key = _norm(prefix + n)
                if key not in self._index:
                    self._index[key] = (zip_path, name)

    def build(self):
        if self._built:
            return
        self._built = True
        media = self.media_root
        cars = os.path.join(media, "cars")
        if not os.path.isdir(cars):
            return

        # Per-car zips: media/cars/FER_F80_25.zip -> media\cars\fer_f80_25\<member>
        try:
            for entry in os.listdir(cars):
                if not entry.lower().endswith(".zip"):
                    continue
                stem = entry[:-4]
                zip_path = os.path.join(cars, entry)
                self._add_members(zip_path, (f"media\\cars\\{stem}\\",))
        except OSError:
            pass

        lib = os.path.join(cars, "_library")
        if os.path.isdir(lib):
            # Materials.zip (+ FH5 Materials_pri_*.zip) and Textures counterparts
            try:
                for entry in os.listdir(lib):
                    low = entry.lower()
                    if not low.endswith(".zip"):
                        continue
                    zip_path = os.path.join(lib, entry)
                    if low == "materials.zip" or low.startswith("materials_"):
                        self._add_members(
                            zip_path,
                            (
                                "media\\cars\\_library\\materials\\",
                                "media\\_library\\materials\\",
                            ),
                        )
                    elif low == "textures.zip" or low.startswith("textures_"):
                        self._add_members(
                            zip_path,
                            (
                                "media\\cars\\_library\\textures\\",
                                "media\\_library\\textures\\",
                            ),
                        )
            except OSError:
                pass
            # shaders as per-shader zips under cars/_library/shaders/
            shaders = os.path.join(lib, "shaders")
            if os.path.isdir(shaders):
                try:
                    for entry in os.listdir(shaders):
                        if not entry.lower().endswith(".zip"):
                            continue
                        stem = entry[:-4]
                        zip_path = os.path.join(shaders, entry)
                        self._add_members(
                            zip_path,
                            (f"media\\cars\\_library\\shaders\\{stem}\\",),
                        )
                except OSError:
                    pass
            # tire compound zips
            tires = os.path.join(lib, "scene", "tires")
            if os.path.isdir(tires):
                try:
                    for entry in os.listdir(tires):
                        if not entry.lower().endswith(".zip"):
                            continue
                        stem = entry[:-4]
                        zip_path = os.path.join(tires, entry)
                        self._add_members(
                            zip_path,
                            (f"media\\cars\\_library\\scene\\tires\\{stem}\\",),
                        )
                except OSError:
                    pass

    def resolve_to_cache(self, game_or_rest: str) -> str | None:
        """Return a real filesystem path for the asset, extracting from zip if needed."""
        self.build()
        rest = _game_rest(game_or_rest)
        if rest is None:
            rest = game_or_rest.lstrip("\\/")
        key = _norm(rest)
        # Allow callers that already stripped leading media\
        hit = self._index.get(key)
        if hit is None and key.startswith("media\\"):
            hit = self._index.get(key)
        if hit is None and not key.startswith("media\\"):
            hit = self._index.get("media\\" + key)
        if hit is None:
            return None
        zip_path, member = hit
        # Cache layout mirrors GAME rest
        out = os.path.join(self.cache_dir, key.replace("\\", os.sep))
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            touch_file(out)
            return out
        os.makedirs(os.path.dirname(out), exist_ok=True)
        z = self._open_zip(zip_path)
        if z is None:
            return None
        with z.open(member) as src, open(out, "wb") as dst:
            dst.write(src.read())
        trim_dir(self.cache_dir, ZIPFS_MAX_BYTES, protect={out})
        return out
