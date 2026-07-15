"""Resolves internal "GAME:\\..." asset paths to real on-disk paths.

Pure module: no bpy. Supports extracted Media trees and Xbox/MS Store zip packaging
(FH5, FH6, …) via ZipAssetStore.
"""

import os

from .zipfs import ZipAssetStore


def find_media_root(root: str) -> str | None:
    """Locate the Media folder containing `cars` under a game root or Content root."""
    if not root:
        return None
    candidates = (
        root,
        os.path.join(root, "media"),
        os.path.join(root, "Media"),
        os.path.join(root, "Content", "media"),
        os.path.join(root, "Content", "Media"),
    )
    for cand in candidates:
        if os.path.isdir(os.path.join(cand, "cars")) or os.path.isdir(os.path.join(cand, "Cars")):
            return cand
    return None


class GamePathResolver:
    def __init__(self, root, cars_dir_override=None, tires_dir_override=None,
                 materials_dir_override=None):
        self.root = root
        self.cars_dir_override = cars_dir_override
        self.tires_dir_override = tires_dir_override
        self.materials_dir_override = materials_dir_override
        self._zipfs = None
        media = find_media_root(root)
        if media and (
            os.path.isfile(os.path.join(media, "cars", "_library", "Materials.zip"))
            or any(
                n.lower().endswith(".zip")
                for n in (os.listdir(os.path.join(media, "cars")) if os.path.isdir(os.path.join(media, "cars")) else [])
            )
        ):
            self._zipfs = ZipAssetStore(media)

    def _materials_root(self):
        if self.materials_dir_override:
            return self.materials_dir_override
        for cand in (
            os.path.join(self.root, "_library", "materials"),
            os.path.join(self.root, "Media", "_library", "materials"),
            os.path.join(self.root, "media", "_library", "materials"),
            os.path.join(self.root, "media", "cars", "_library", "materials"),
            os.path.join(self.root, "Media", "cars", "_library", "materials"),
            os.path.join(self.root, "materials"),
        ):
            if os.path.isdir(cand):
                return cand
        return os.path.join(self.root, "_library", "materials")

    def _zip_resolve(self, path: str) -> str | None:
        if self._zipfs is None:
            return None
        return self._zipfs.resolve_to_cache(path)

    def resolve(self, path):
        if path[:5].lower() != "game:":
            if not path.startswith(self.root):
                print('Warning: Internal path doesn\'t start with "GAME:".')
            if os.path.isfile(path):
                return path
            z = self._zip_resolve(path)
            return z or path

        rest = path[5:]
        low = rest.lower().replace("/", "\\")

        if self.tires_dir_override:
            tmarker = "\\_library\\scene\\tires"
            tidx = low.find(tmarker)
            if tidx != -1:
                suffix = rest[tidx + len(tmarker):].replace("/", "\\")
                disk = self.tires_dir_override + suffix
                if os.path.isfile(disk):
                    return disk
                # GameDB names like Semi_Slick map to tire_semi_slick.zip members.
                low_disk = self.tires_dir_override + suffix.lower()
                if low_disk != disk and os.path.isfile(low_disk):
                    return low_disk
                z = self._zip_resolve(path)
                if z:
                    return z
                z = self._zip_resolve(r"GAME:\Media\Cars\_library\scene\tires" + suffix.lower())
                if z:
                    return z

        # Material parents may be rooted at GAME:\Media\_library\materials or nested under
        # GAME:\Media\cars\_library\materials — always map to the game library (disk or zip).
        mmarker = "\\_library\\materials"
        midx = low.find(mmarker)
        if midx != -1:
            suffix = rest[midx + len(mmarker):].replace("/", "\\")
            disk = self._materials_root() + suffix
            if os.path.isfile(disk):
                return disk
            z = self._zip_resolve(path)
            if z:
                return z
            # Try media\cars\_library\materials\<suffix> via zip index
            z = self._zip_resolve(r"GAME:\Media\cars\_library\materials" + suffix)
            if z:
                return z
            return disk

        # Shared library textures
        tlib = "\\_library\\textures"
        tidx = low.find(tlib)
        if tidx != -1:
            suffix = rest[tidx + len(tlib):].replace("/", "\\")
            for base in (
                os.path.join(self.root, "_library", "textures"),
                os.path.join(self.root, "Media", "_library", "textures"),
                os.path.join(self.root, "media", "_library", "textures"),
                os.path.join(self.root, "Media", "cars", "_library", "textures"),
                os.path.join(self.root, "media", "cars", "_library", "textures"),
            ):
                cand = base + suffix
                if os.path.isfile(cand):
                    return cand
            z = self._zip_resolve(path)
            if z:
                return z
            z = self._zip_resolve(r"GAME:\Media\cars\_library\textures" + suffix)
            if z:
                return z
            return os.path.join(self.root, "Media", "_library", "textures") + suffix

        if self.cars_dir_override:
            marker = "\\media\\cars"
            idx = low.find(marker)
            if idx != -1:
                suffix = rest[idx + len(marker):].replace("/", "\\")
                if suffix.lower().startswith("\\_library\\"):
                    lib_suffix = suffix[len("\\_library"):]
                    for base in (
                        os.path.join(self.root, "_library"),
                        os.path.join(self.root, "Media", "cars", "_library"),
                        os.path.join(self.root, "media", "cars", "_library"),
                    ):
                        cand = base + lib_suffix
                        if os.path.isfile(cand):
                            return cand
                    z = self._zip_resolve(path)
                    if z:
                        return z
                    return os.path.join(self.root, "_library") + lib_suffix
                parts = [p for p in suffix.split("\\") if p]
                if parts:
                    ov_base = os.path.basename(self.cars_dir_override.rstrip("\\/"))
                    if parts[0].lower() == ov_base.lower():
                        suffix = "\\" + "\\".join(parts[1:])
                disk = self.cars_dir_override + suffix
                if os.path.isfile(disk):
                    return disk
                z = self._zip_resolve(path)
                if z:
                    return z
                return disk

        resolved = self.root + rest
        if os.path.isfile(resolved):
            return resolved
        if not os.path.isfile(resolved):
            alt = resolved.replace("\\Media\\_library\\materials", "\\_library\\materials")
            if alt != resolved and os.path.isfile(alt):
                return alt
            # Content/media root layouts: root already IS media
            media = find_media_root(self.root)
            if media:
                # rest looks like \Media\Cars\... or \cars\...
                stripped = rest
                for prefix in ("\\Media", "\\media"):
                    if stripped.startswith(prefix):
                        stripped = stripped[len(prefix):]
                        break
                cand = media + stripped
                if os.path.isfile(cand):
                    return cand
        z = self._zip_resolve(path)
        if z:
            return z
        return resolved

    def test(self, path):
        return path.startswith(self.root)


_TIRE_COMPOUND_FALLBACKS = ("c", "b", "a", "semi_slick", "street", "sport")


def normalize_tire_model_name(name: str) -> str:
    """GameDB TireModelName (e.g. Semi_Slick) -> on-disk / zip folder suffix (semi_slick)."""
    if not name:
        return ""
    return name.strip().replace(" ", "_").lower()


def resolve_tire_model_name(game_path, tires_dir_override=None, current_name=""):
    """Return tire compound suffix (e.g. 'c' -> tire_c/tireL_c) when GameDB did not supply one."""
    if current_name:
        return normalize_tire_model_name(current_name)
    tires_root = tires_dir_override or os.path.join(game_path, "tires")
    media = find_media_root(game_path)
    candidates = [tires_root]
    if media:
        candidates.append(os.path.join(media, "cars", "_library", "scene", "tires"))
    for tires in candidates:
        if not os.path.isdir(tires):
            continue
        for suffix in _TIRE_COMPOUND_FALLBACKS:
            modelbin = os.path.join(tires, f"tire_{suffix}", f"tireL_{suffix}.modelbin")
            if os.path.isfile(modelbin):
                return suffix
            # Zip tire compounds (Xbox Media layout)
            if os.path.isfile(os.path.join(tires, f"tire_{suffix}.zip")):
                return suffix
        for entry in sorted(os.listdir(tires)):
            low = entry.lower()
            if low.startswith("tire_") and low.endswith(".zip"):
                return entry[5:-4]
            if not low.startswith("tire_"):
                continue
            suffix = entry[5:]
            modelbin = os.path.join(tires, entry, f"tireL_{suffix}.modelbin")
            if os.path.isfile(modelbin):
                return suffix
    return ""


def tire_modelbin_game_path(tire_model_name: str) -> str:
    """GAME: path to the left-side stock tire modelbin for a compound name."""
    name = normalize_tire_model_name(tire_model_name)
    return fr"GAME:\Media\Cars\_library\scene\tires\tire_{name}\tireL_{name}.modelbin"
