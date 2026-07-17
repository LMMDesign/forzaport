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


def detect_game_key(*paths: str | None) -> str:
    """Classify a path as ``fh5`` / ``fh6`` / ``fm`` / ``unknown`` from folder names."""
    text = " ".join(
        (p or "").replace("/", "\\").lower() for p in paths if p
    )
    if not text:
        return "unknown"
    # Longer / more specific tokens first.
    if any(
        tok in text
        for tok in (
            "forza horizon 6",
            "horizon 6",
            "horizon6",
            "\\fh6\\",
            "\\fh6_",
            "/fh6/",
            "fh6\\",
        )
    ):
        return "fh6"
    if any(
        tok in text
        for tok in (
            "forza horizon 5",
            "horizon 5",
            "horizon5",
            "\\fh5\\",
            "\\fh5_",
            "/fh5/",
            "fh5\\",
        )
    ):
        return "fh5"
    if "motorsport" in text and "horizon" not in text:
        return "fm"
    # Compact rip layouts: ...\FH6\MER_... or ...\FH5\...
    for part in text.replace("/", "\\").split("\\"):
        if part == "fh6" or part.startswith("fh6_"):
            return "fh6"
        if part == "fh5" or part.startswith("fh5_"):
            return "fh5"
        if part in ("fm8", "fm2023", "forzamotorsport"):
            return "fm"
    return "unknown"


def detect_game_key_from_car_media(car_root: str | None) -> str:
    """Infer game from on-disk Autovista media beside the car (Mojo vs GR2)."""
    if not car_root or not os.path.isdir(car_root):
        return "unknown"
    # FH6 Mojo
    for rel in (
        ("Scene", "animations", "Mojo"),
        ("scene", "animations", "Mojo"),
        ("Scene", "Animations", "Mojo"),
    ):
        if os.path.isdir(os.path.join(car_root, *rel)):
            return "fh6"
    # FH5 Granny
    for name in ("Animations", "animations"):
        anim = os.path.join(car_root, name)
        if not os.path.isdir(anim):
            continue
        try:
            if any(f.lower().endswith(".gr2") for f in os.listdir(anim)):
                return "fh5"
        except OSError:
            pass
    return "unknown"


def resolve_import_game_key(
    *,
    filepath: str | None = None,
    game_path: str | None = None,
    car_root: str | None = None,
) -> str:
    """Best-effort game id for tire/material library selection."""
    key = detect_game_key(filepath, game_path, car_root)
    if key != "unknown":
        return key
    return detect_game_key_from_car_media(car_root)


def _tires_dir_has_zips(tires_dir: str) -> bool:
    try:
        for entry in os.listdir(tires_dir):
            low = entry.lower()
            if low.endswith(".zip") and (low.startswith("tire_") or low.startswith("tirer_")):
                return True
    except OSError:
        return False
    return False


def find_tires_dir(*roots: str | None) -> str | None:
    """Locate ``.../cars/_library/scene/tires`` (extracted folders or tire_*.zip)."""
    seen: set[str] = set()
    for root in roots:
        if not root:
            continue
        root = os.path.abspath(root)
        media = find_media_root(root)
        candidates = [
            root if os.path.basename(root).lower() == "tires" else None,
            os.path.join(root, "tires"),
            os.path.join(root, "cars", "_library", "scene", "tires"),
            os.path.join(root, "Media", "Cars", "_library", "scene", "tires"),
            os.path.join(root, "media", "cars", "_library", "scene", "tires"),
            os.path.join(root, "Content", "media", "cars", "_library", "scene", "tires"),
            os.path.join(root, "Content", "Media", "Cars", "_library", "scene", "tires"),
        ]
        if media:
            candidates.extend(
                [
                    os.path.join(media, "cars", "_library", "scene", "tires"),
                    os.path.join(media, "Cars", "_library", "scene", "tires"),
                ]
            )
        for cand in candidates:
            if not cand:
                continue
            key = os.path.normcase(os.path.abspath(cand))
            if key in seen:
                continue
            seen.add(key)
            if not os.path.isdir(cand):
                continue
            # Prefer a folder that actually contains tire compounds.
            try:
                names = os.listdir(cand)
            except OSError:
                continue
            if any(
                n.lower().startswith("tire")
                for n in names
            ):
                return cand
    return None


class GamePathResolver:
    def __init__(self, root, cars_dir_override=None, tires_dir_override=None,
                 materials_dir_override=None, car_zip_path=None):
        self.root = root
        self.cars_dir_override = cars_dir_override
        self.tires_dir_override = tires_dir_override
        self.materials_dir_override = materials_dir_override
        self._zipfs = None
        media = find_media_root(root)
        cars_dir = None
        if media:
            for name in ("cars", "Cars"):
                cand = os.path.join(media, name)
                if os.path.isdir(cand):
                    cars_dir = cand
                    break
        has_car_zips = False
        if cars_dir:
            try:
                has_car_zips = any(
                    n.lower().endswith(".zip") for n in os.listdir(cars_dir)
                )
            except OSError:
                has_car_zips = False
        want_zipfs = bool(
            car_zip_path
            or (
                media
                and (
                    (
                        cars_dir
                        and os.path.isfile(
                            os.path.join(cars_dir, "_library", "Materials.zip")
                        )
                    )
                    or has_car_zips
                )
            )
            or (
                tires_dir_override
                and os.path.isdir(tires_dir_override)
                and _tires_dir_has_zips(tires_dir_override)
            )
        )
        if want_zipfs:
            # Prefer a real Media root; for a lone car zip use the zip's parent as
            # a stand-in so ZipAssetStore can still cache extracts.
            zip_media = media or (
                os.path.dirname(os.path.abspath(car_zip_path))
                if car_zip_path
                else root
            )
            self._zipfs = ZipAssetStore(zip_media)
            if car_zip_path:
                stem = os.path.splitext(os.path.basename(car_zip_path))[0]
                self._zipfs.register_car_zip(car_zip_path, stem)
            if tires_dir_override and os.path.isdir(tires_dir_override):
                self._zipfs.register_tires_dir(tires_dir_override)

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
    media = find_media_root(game_path)
    candidates = []
    if tires_dir_override:
        candidates.append(tires_dir_override)
    found = find_tires_dir(game_path, media)
    if found:
        candidates.append(found)
    candidates.append(os.path.join(game_path or "", "tires"))
    for tires in candidates:
        if not tires or not os.path.isdir(tires):
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
