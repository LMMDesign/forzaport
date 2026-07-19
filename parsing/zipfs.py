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

    def _add_members(
        self,
        zip_path: str,
        prefixes: tuple[str, ...],
        strip_stem: str | None = None,
    ):
        """Index zip members under each GAME: prefix.

        Xbox car zips are usually flat (``Scene/...`` and ``Name.carbin`` at root).
        Some archives wrap contents in a folder named after the car
        (``Name/Scene/...``). Only that matching stem is stripped — never
        arbitrary folders like ``Scene``, which would break modelbin lookups.
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
            # Drop leading Name/ only when it matches the archive stem.
            if (
                stem_low
                and len(parts) >= 2
                and parts[0].lower() == stem_low
            ):
                rel = "/".join(parts[1:])
            rel_os = rel.replace("/", "\\")
            for prefix in prefixes:
                key = _norm(prefix + rel_os)
                if key not in self._index:
                    self._index[key] = (zip_path, name)

    def _index_shaderbin_aliases(self, zip_path: str) -> None:
        """Index ``*.shaderbin`` members under ``media\\…\\shaders\\{stem}\\…``.

        FH5 packs many shared shaders into car-named zips with members like
        ``carpaint_standard/carpaint_standard.shaderbin``. Alias those under the
        shader stem so ``GAME:\\Media\\_library\\shaders\\…`` resolves.
        """
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
                if key not in self._index:
                    self._index[key] = (zip_path, name)

    def register_car_zip(self, zip_path: str, media_name: str | None = None) -> bool:
        """Index one car archive so GAME:\\Media\\Cars\\Name\\... resolves from it.

        Needed when the user picks a loose ``Name.zip`` outside ``media/cars/``,
        or when ``build()`` has not yet seen that archive.
        """
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
        # Accept Media/cars or Media/Cars
        cars = None
        for name in ("cars", "Cars"):
            cand = os.path.join(media, name)
            if os.path.isdir(cand):
                cars = cand
                break
        if cars is None:
            return

        # Per-car zips: media/cars/FER_F80_25.zip -> media\cars\fer_f80_25\<member>
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
            # shaders: FH6 per-shader zips, FH5 car-bundled shader zips, Shaders.zip
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
                        # FH5: car-named archives hold shared shader folders
                        # (carpaint_standard/carpaint_standard.shaderbin). Alias them
                        # under the shader stem so GAME:\Media\_library\shaders\... works.
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
            # tire compound zips
            tires = os.path.join(lib, "scene", "tires")
            if os.path.isdir(tires):
                self.register_tires_dir(tires)

    def register_tires_dir(self, tires_dir: str) -> int:
        """Index ``tire_*.zip`` compounds from a shared library tires folder.

        Used for Xbox ``media/cars/_library/scene/tires`` and for preference /
        override folders when the car rip itself has no ``_library``.
        """
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
            # Left/stock compounds are tire_*.zip; tireR_* is the mirrored side.
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
