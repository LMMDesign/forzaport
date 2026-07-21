"""Addon preferences + the shared prefs accessor.

Car libraries / game installations / GameDB and default import options for
File > Import > Forza Car. All paths are empty by default so any machine works;
set them in Edit > Preferences > Add-ons > Import Forza Car.

Paths are also mirrored to a JSON file under Blender's user config so they
survive disable/enable and script reload (AddonPreferences alone resets then).
"""

import json
import os

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import AddonPreferences, Operator, PropertyGroup

from .options import LOD_ITEMS, DRAW_ITEMS, SUSP_ITEMS
from .parsing.disk_cache import (
    ZIPFS_MAX_BYTES,
    cache_summary,
    clear_all_caches,
    format_bytes,
)
from .parsing.paths import (
    detect_game_key,
    find_media_root,
    media_root_from_tires_dir,
)

# Module id for AddonPreferences — must match the installed package name
# (legacy folder install or bl_ext.*.io_import_forza_carbin for Extensions).
ADDON_ID = __package__

GAME_ITEMS = (
    ("fh6", "Forza Horizon 6", "Forza Horizon 6 install folder (the one that contains Content)"),
    ("fh5", "Forza Horizon 5", "Forza Horizon 5 install folder (the one that contains Content)"),
    ("fm", "Forza Motorsport", "Forza Motorsport install folder (the one that contains Content)"),
    ("other", "Other / Fallback", "Used when the source game cannot be detected"),
)

# Keys mirrored to disk (survive addon reload).
# Legacy path keys remain readable only so older settings can migrate to game_roots.
# Unknown keys in older user_settings.json (e.g. lslib_path) are ignored on load.
_PERSIST_STRINGS = (
    "gamedb_dir",
    "db_path",
)


def get_prefs():
    addon = bpy.context.preferences.addons.get(__package__)
    if addon is None:
        # Fallbacks for mixed legacy / extension installs.
        addon = bpy.context.preferences.addons.get("io_import_forza_carbin")
    if addon is None:
        for key in bpy.context.preferences.addons.keys():
            if key.endswith(".io_import_forza_carbin") or key.endswith(
                "io_import_forza_carbin"
            ):
                addon = bpy.context.preferences.addons.get(key)
                break
    return addon.preferences if addon else None


def _settings_path() -> str:
    root = bpy.utils.user_resource("CONFIG", path="forza_import", create=True)
    return os.path.join(root, "user_settings.json")


def _gather_settings(prefs) -> dict:
    return {
        "library_roots": [item.path for item in prefs.library_roots if item.path],
        "game_roots": [
            {"game": item.game, "path": item.path}
            for item in prefs.game_roots
            if item.path
        ],
        **{key: getattr(prefs, key, "") or "" for key in _PERSIST_STRINGS},
    }


def _add_game_root(prefs, game: str, path: str) -> bool:
    """Seed one entry from a legacy tires path (stores resolved Media for migration only).

    Manual preference picks keep the raw install/Content/Media path the user chose;
    Media is resolved at validation and import time via find_media_root().
    """
    media = find_media_root(path) or media_root_from_tires_dir(path)
    if not media:
        return False
    media = os.path.normpath(media)
    key = game if game in {g[0] for g in GAME_ITEMS} else detect_game_key(media)
    if key == "unknown":
        key = "other"
    for item in prefs.game_roots:
        if item.game == key:
            return False
        existing = bpy.path.abspath(item.path) if item.path else ""
        existing_media = find_media_root(existing) if existing else None
        if existing_media and os.path.normcase(existing_media) == os.path.normcase(media):
            return False
        if existing and os.path.normcase(existing) == os.path.normcase(media):
            return False
    item = prefs.game_roots.add()
    item.game = key
    item.path = media
    return True


def _migrate_legacy_game_roots(prefs, data=None) -> bool:
    """Migrate old tire-folder settings to explicit game install roots once."""
    if any(item.path for item in prefs.game_roots):
        return False
    changed = False
    data = data if isinstance(data, dict) else {}
    entries = data.get("tires_libraries")
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            if isinstance(path, str) and path.strip():
                changed |= _add_game_root(prefs, entry.get("game", "other"), path)
    legacy = data.get("tires_dir") or getattr(prefs, "tires_dir", "")
    if isinstance(legacy, str) and legacy.strip():
        key = detect_game_key(legacy)
        changed |= _add_game_root(
            prefs, key if key != "unknown" else "other", legacy
        )
    return changed


def save_user_settings(prefs=None) -> None:
    """Write path preferences to the user config JSON."""
    prefs = prefs or get_prefs()
    if prefs is None:
        return
    path = _settings_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_gather_settings(prefs), f, indent=2)
    except OSError as exc:
        print(f"Forza: could not save user settings ({exc})")


def load_user_settings(prefs=None) -> bool:
    """Restore path preferences from JSON. Returns True if a file was applied."""
    prefs = prefs or get_prefs()
    if prefs is None:
        return False
    path = _settings_path()
    if not os.path.isfile(path):
        # First run after upgrade: seed JSON from whatever Blender still holds.
        _migrate_legacy_game_roots(prefs)
        if (
            any(item.path for item in prefs.library_roots)
            or any(item.path for item in prefs.game_roots)
            or any(getattr(prefs, key, "") for key in _PERSIST_STRINGS)
        ):
            save_user_settings(prefs)
        return False
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Forza: could not load user settings ({exc})")
        return False
    if not isinstance(data, dict):
        return False

    roots = data.get("library_roots") or []
    if isinstance(roots, list):
        prefs.library_roots.clear()
        for entry in roots:
            if isinstance(entry, str) and entry.strip():
                item = prefs.library_roots.add()
                # Assign without relying on update mid-load batch
                item.path = entry

    game_roots = data.get("game_roots")
    prefs.game_roots.clear()
    if isinstance(game_roots, list):
        valid_games = {g[0] for g in GAME_ITEMS}
        for entry in game_roots:
            if not isinstance(entry, dict):
                continue
            path_val = entry.get("path")
            if not isinstance(path_val, str) or not path_val.strip():
                continue
            item = prefs.game_roots.add()
            game = entry.get("game")
            item.game = game if game in valid_games else "other"
            item.path = path_val

    for key in _PERSIST_STRINGS:
        val = data.get(key)
        if isinstance(val, str):
            setattr(prefs, key, val)

    if _migrate_legacy_game_roots(prefs, data):
        save_user_settings(prefs)
    return True


def _on_path_updated(self, context):
    # ``self`` may be AddonPreferences or one of the path PropertyGroups.
    save_user_settings(get_prefs())


class ForzaLibraryItem(PropertyGroup):
    path: StringProperty(
        name="Folder",
        description="Your folder of cars copied from the game: .zip files and/or extracted "
                    "car folders. Also accepts Content\\media or a folder containing media\\cars",
        subtype="DIR_PATH",
        update=_on_path_updated,
    )


class ForzaGameRootItem(PropertyGroup):
    game: EnumProperty(
        name="Game",
        description="Which game's imports should use this installation",
        items=GAME_ITEMS,
        default="fh6",
        update=_on_path_updated,
    )
    path: StringProperty(
        name="Game Install Folder",
        description="Game install folder (the one that contains Content). Also accepts "
                    "Content or Content\\media. The path is stored as selected; Media is "
                    "resolved automatically for materials, textures, shaders and tires",
        subtype="DIR_PATH",
        update=_on_path_updated,
    )


class IMPORT_OT_forza_lib_add(Operator):
    bl_idname = "import_scene.forza_lib_add"
    bl_label = "Add Library Folder"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        get_prefs().library_roots.add()
        save_user_settings()
        return {"FINISHED"}


class IMPORT_OT_forza_lib_remove(Operator):
    bl_idname = "import_scene.forza_lib_remove"
    bl_label = "Remove Library Folder"
    bl_options = {"INTERNAL"}

    index: IntProperty(default=-1)

    def execute(self, context):
        prefs = get_prefs()
        if 0 <= self.index < len(prefs.library_roots):
            prefs.library_roots.remove(self.index)
            save_user_settings(prefs)
        return {"FINISHED"}


class IMPORT_OT_forza_game_root_add(Operator):
    bl_idname = "import_scene.forza_game_root_add"
    bl_label = "Add Game Installation"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        get_prefs().game_roots.add()
        save_user_settings()
        return {"FINISHED"}


class IMPORT_OT_forza_game_root_remove(Operator):
    bl_idname = "import_scene.forza_game_root_remove"
    bl_label = "Remove Game Installation"
    bl_options = {"INTERNAL"}

    index: IntProperty(default=-1)

    def execute(self, context):
        prefs = get_prefs()
        if 0 <= self.index < len(prefs.game_roots):
            prefs.game_roots.remove(self.index)
            save_user_settings(prefs)
        return {"FINISHED"}


class IMPORT_OT_forza_clear_cache(Operator):
    bl_idname = "import_scene.forza_clear_cache"
    bl_label = "Clear Cache"
    bl_description = "Delete zip extracts and DDS staging files (safe; recreated on next import)"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        freed = clear_all_caches()
        self.report({"INFO"}, f"Cleared Forza import cache ({format_bytes(freed)} freed)")
        return {"FINISHED"}


class ForzaCarbinPreferences(AddonPreferences):
    bl_idname = ADDON_ID

    library_roots: CollectionProperty(type=ForzaLibraryItem)
    game_roots: CollectionProperty(type=ForzaGameRootItem)
    # Legacy RNA: tires_dir is still read by migration into game_roots.
    tires_dir: StringProperty(default="", options={"HIDDEN"})
    gamedb_dir: StringProperty(
        name="GameDB Folder",
        description="Folder of decrypted GameDB .slt files (searched in addition to folders near the car). "
                    "Leave empty if you set GameDB Path per import or disable Use GameDB",
        subtype="DIR_PATH",
        default="",
        update=_on_path_updated,
    )
    import_animations: BoolProperty(
        name="Import Animations",
        description="When importing from the File > Import > Forza Car list, also build the rig and "
                    "bake part animations. FH5: gr2dump matrix pipeline (.gr2). "
                    "FH6: Mojo pipeline (.clipd). Separate systems — needs .NET 8 for FH5.",
        default=False,
    )

    level_of_detail: EnumProperty(name="Level of Detail", items=LOD_ITEMS, default="1")
    draw_group: EnumProperty(name="Draw Group", items=DRAW_ITEMS, default="1")
    suspension_transform_type: EnumProperty(name="Wheel Positioning", items=SUSP_ITEMS, default="2")
    use_db: BoolProperty(name="Use GameDB", default=True)
    db_path: StringProperty(
        name="GameDB Path (optional)",
        subtype="FILE_PATH",
        default="",
        update=_on_path_updated,
    )
    use_materials: BoolProperty(name="Import Materials", default=True)
    create_placeholder_materials: BoolProperty(
        name="Placeholder Materials",
        description="When a material cannot be built, assign a magenta error material so the "
                    "mesh still has a named slot. Off by default (fail closed — leave slot empty)",
        default=False,
    )
    quadrangulate_mesh: BoolProperty(name="Quadrangulate", default=False)
    hide_decal_transparent_pass: BoolProperty(name="Hide Transparent Decals", default=False)

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="Car Library Folders")
        box.label(text="1. Copy car .zip files from the game's Content\\media\\cars\\", icon="INFO")
        box.label(text="2. Put them in a folder you own (leave zipped, or extract)")
        box.label(text="3. Add that folder here — then use File > Import > Forza Car")
        for i, item in enumerate(self.library_roots):
            row = box.row(align=True)
            row.prop(item, "path", text="")
            row.operator("import_scene.forza_lib_remove", text="", icon="X").index = i
        box.operator("import_scene.forza_lib_add", text="Add Folder", icon="ADD")
        box.label(
            text=f"Paths auto-save to: {_settings_path()}",
            icon="INFO",
        )

        box = layout.box()
        box.label(text="GameDB (decrypted .slt)")
        box.prop(self, "gamedb_dir")
        box.label(
            text="Encrypted on-disk gamedbRC.slt will not work — use a decrypted / runtime dump.",
            icon="ERROR",
        )

        box = layout.box()
        box.label(text="Game Installations")
        box.label(
            text="Pick each game's install folder (the folder that contains Content).",
            icon="INFO",
        )
        box.label(
            text="Content or Content\\media also work. Shared materials/textures/shaders/tires come from Media.",
            icon="INFO",
        )
        for i, item in enumerate(self.game_roots):
            row = box.row(align=True)
            row.prop(item, "game", text="")
            row.prop(item, "path", text="")
            row.operator("import_scene.forza_game_root_remove", text="", icon="X").index = i
            media = find_media_root(bpy.path.abspath(item.path)) if item.path else None
            if item.path and media is None:
                warn = box.row()
                warn.alert = True
                warn.label(
                    text="Could not find Content\\media\\cars under this folder. "
                         "Choose the game install folder (the one that contains Content).",
                    icon="ERROR",
                )
            elif media:
                box.label(text=f"Using Media: {media}", icon="CHECKMARK")
        box.operator("import_scene.forza_game_root_add", text="Add Game Install Folder", icon="ADD")

        box = layout.box()
        box.label(text="Animations")
        box.label(
            text="FH5: gr2dump matrices (.NET 8). FH6: Mojo .clipd — separate pipelines.",
            icon="INFO",
        )
        box.prop(self, "import_animations")

        box = layout.box()
        box.label(text="Import Cache")
        zip_path, zip_bytes, dds_path, dds_bytes = cache_summary()
        box.label(text=f"Zip extracts: {format_bytes(zip_bytes)}  (auto-trim at {format_bytes(ZIPFS_MAX_BYTES)})")
        box.label(text=zip_path)
        box.label(text=f"DDS staging: {format_bytes(dds_bytes)}")
        box.label(text=dds_path)
        box.operator("import_scene.forza_clear_cache", icon="TRASH")

        box = layout.box()
        box.label(text="Default Import Options (File > Import > Forza Car list)")
        box.prop(self, "level_of_detail")
        box.prop(self, "draw_group")
        box.prop(self, "suspension_transform_type")
        row = box.row()
        row.prop(self, "use_db")
        sub = box.column()
        sub.enabled = self.use_db
        sub.prop(self, "db_path")
        box.prop(self, "use_materials")
        box.prop(self, "create_placeholder_materials")
        box.prop(self, "quadrangulate_mesh")
        box.prop(self, "hide_decal_transparent_pass")


classes = (
    ForzaLibraryItem,
    ForzaGameRootItem,
    IMPORT_OT_forza_lib_add,
    IMPORT_OT_forza_lib_remove,
    IMPORT_OT_forza_game_root_add,
    IMPORT_OT_forza_game_root_remove,
    IMPORT_OT_forza_clear_cache,
    ForzaCarbinPreferences,
)
