"""Addon preferences + the shared prefs accessor.

Library / GameDB / tires / materials paths and default import options for
File > Import > Forza Car. All paths are empty by default so any machine works;
set them in Edit > Preferences > Add-ons > Import Forza Car.
"""

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

ADDON_ID = __package__  # "io_import_forza_carbin"


def get_prefs():
    addon = bpy.context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon else None


class ForzaLibraryItem(PropertyGroup):
    path: StringProperty(
        name="Folder",
        description="Your folder of cars copied from the game: .zip files and/or extracted "
                    "car folders. Also accepts Content\\media or a folder containing media\\cars",
        subtype="DIR_PATH",
    )


class IMPORT_OT_forza_lib_add(Operator):
    bl_idname = "import_scene.forza_lib_add"
    bl_label = "Add Library Folder"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        get_prefs().library_roots.add()
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
    gamedb_dir: StringProperty(
        name="GameDB Folder",
        description="Folder of decrypted GameDB .slt files (searched in addition to folders near the car). "
                    "Leave empty if you set GameDB Path per import or disable Use GameDB",
        subtype="DIR_PATH",
        default="",
    )
    tires_dir: StringProperty(
        name="Tires Folder (optional)",
        description="Override for tire models: either extracted tire_<name>\\tireL_<name>.modelbin folders "
                    "or a folder of tire_*.zip (Xbox Media). Leave empty to auto-detect under the game Media tree",
        subtype="DIR_PATH",
        default="",
    )
    materials_dir: StringProperty(
        name="Materials Folder (optional)",
        description="Override for shared materials library (on-disk materials tree). Leave empty to "
                    "auto-detect or resolve from Materials.zip / Materials_pri_*.zip via the Media root",
        subtype="DIR_PATH",
        default="",
    )
    lslib_path: StringProperty(
        name="LSLib divine.exe (optional)",
        description="Path to LSLib divine.exe for auto-converting Animations\\*.gr2 to .dae "
                    "(typical for FH5 car zips). Place granny2.dll next to divine.exe. "
                    "FH6 Mojo (.clipd) is not supported. Leave empty to pick .dae files you converted yourself",
        subtype="FILE_PATH",
        default="",
    )
    import_animations: BoolProperty(
        name="Import Animations",
        description="When importing from the File > Import > Forza Car list, also build the rig and "
                    "bake part animations (needs divine.exe and Granny .gr2; not FH6 Mojo)",
        default=False,
    )

    level_of_detail: EnumProperty(name="Level of Detail", items=LOD_ITEMS, default="1")
    draw_group: EnumProperty(name="Draw Group", items=DRAW_ITEMS, default="1")
    suspension_transform_type: EnumProperty(name="Wheel Positioning", items=SUSP_ITEMS, default="2")
    use_db: BoolProperty(name="Use GameDB", default=True)
    db_path: StringProperty(name="GameDB Path (optional)", subtype="FILE_PATH", default="")
    use_materials: BoolProperty(name="Import Materials", default=True)
    create_placeholder_materials: BoolProperty(
        name="Placeholder Materials",
        description="Give every mesh a material slot named after the original Forza material "
                    "(shared across meshes) even when the material can't be built",
        default=True,
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

        box = layout.box()
        box.label(text="GameDB (decrypted .slt)")
        box.prop(self, "gamedb_dir")
        box.label(
            text="Encrypted on-disk gamedbRC.slt will not work — use a decrypted / runtime dump.",
            icon="ERROR",
        )

        box = layout.box()
        box.label(text="Overrides (optional)")
        box.prop(self, "tires_dir")
        box.prop(self, "materials_dir")

        box = layout.box()
        box.label(text="Animations (Granny .gr2 — FH5 and similar)")
        box.prop(self, "lslib_path")
        box.label(text="granny2.dll must sit next to divine.exe", icon="INFO")
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
    IMPORT_OT_forza_lib_add,
    IMPORT_OT_forza_lib_remove,
    IMPORT_OT_forza_clear_cache,
    ForzaCarbinPreferences,
)
